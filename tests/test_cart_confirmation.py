"""Offline API checks: no EKT credentials, model calls, or real basket writes."""

import asyncio
import copy
import os
import unittest
from unittest.mock import patch

import httpx

os.environ['EKT_PASSWORD'] = ''
os.environ['RATE_LIMIT_PER_MINUTE'] = '10000'
from app import main
from app import cart_actions


class CartConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.sessions.clear()
        self.product = {
            'id': 101, 'name': 'Тестовый автомат', 'price': 100,
            'quantity': 10, 'properties': {'KRATNOST_MIN': '1'},
            'stores': [{'id': 13, 'name': 'Алматы', 'quantity': 3}],
        }
        self.time = 1000.0
        self.clock = patch.object(cart_actions, 'monotonic', lambda: self.time)
        self.clock.start()

        async def detail(pid):
            await asyncio.sleep(0)
            value = copy.deepcopy(self.product)
            value['id'] = pid
            return value

        self.detail = patch.object(main.engine.ekt, 'detail', side_effect=detail)
        self.mock_detail = self.detail.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test')
        self.headers = {'X-Session-Id': (await self.client.get('/api/session')).json()['session_id']}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.detail.stop()
        self.clock.stop()
        main.sessions.clear()

    async def post(self, path, data, headers=None):
        return await self.client.post('/api/cart/' + path, json=data, headers=headers or self.headers)

    async def prepare(self, quantity=2, **kwargs):
        response = await self.post('prepare', {'product_id': 101, 'quantity': quantity, **kwargs})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def confirm(self, prepared, quantity=2, **kwargs):
        return await self.post('confirm', {'product_id': 101, 'quantity': quantity,
                                          'confirmation_token': prepared['confirmation_token'], **kwargs})

    async def cart(self):
        response = await self.client.get('/api/cart', headers=self.headers)
        return response.json()['cart']

    async def test_prepare_is_read_only_and_discloses_price_total_and_scope(self):
        prepared = await self.prepare()
        self.assertEqual(await self.cart(), [])
        self.assertEqual(prepared['total'], 200)
        self.assertEqual(prepared['items'][0]['price'], 100)
        self.assertIn('Тестовый автомат', prepared['message'])
        self.assertIn('200.00', prepared['message'])
        self.assertIn('склад не выбран', prepared['message'])
        self.assertEqual(prepared['expires_in_seconds'], 120)

    async def test_confirm_without_token_cannot_mutate(self):
        response = await self.post('confirm', {'product_id': 101, 'quantity': 2})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(await self.cart(), [])

    async def test_price_change_requires_new_consent(self):
        prepared = await self.prepare()
        self.product['price'] = 130
        response = await self.confirm(prepared)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(await self.cart(), [])
        self.product['price'] = 100
        self.assertEqual((await self.confirm(prepared)).status_code, 409)
        fresh = await self.prepare()
        self.assertEqual((await self.confirm(fresh)).status_code, 200)

    async def test_expired_action_is_rejected(self):
        prepared = await self.prepare()
        self.time += 120
        self.assertEqual((await self.confirm(prepared)).status_code, 400)
        self.assertEqual(await self.cart(), [])

    async def test_expiration_during_live_check_is_rejected(self):
        prepared = await self.prepare()

        async def slow_detail(pid):
            self.time += 121
            return copy.deepcopy(self.product)

        self.mock_detail.side_effect = slow_detail
        self.assertEqual((await self.confirm(prepared)).status_code, 409)
        self.assertEqual(await self.cart(), [])

    async def test_repeat_confirmation_is_idempotent(self):
        prepared = await self.prepare()
        first = await self.confirm(prepared)
        second = await self.confirm(prepared)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(sum(item['quantity'] for item in await self.cart()), 2)

    async def test_other_session_cannot_confirm(self):
        prepared = await self.prepare()
        response = await self.post('confirm', {'product_id': 101, 'quantity': 2,
                                  'confirmation_token': prepared['confirmation_token']},
                                  headers={'X-Session-Id': 'b' * 32})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(await self.cart(), [])

    async def test_quantity_or_store_cannot_be_changed_after_consent(self):
        prepared = await self.prepare()
        self.assertEqual((await self.confirm(prepared, quantity=3)).status_code, 400)
        self.assertEqual((await self.confirm(prepared, store_id=13)).status_code, 400)
        self.assertEqual(await self.cart(), [])
        self.assertEqual((await self.confirm(prepared)).status_code, 200)

    async def test_cancel_revokes_token(self):
        prepared = await self.prepare()
        cancelled = await self.post('cancel', {'confirmation_token': prepared['confirmation_token']})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual((await self.confirm(prepared)).status_code, 409)
        self.assertEqual(await self.cart(), [])

    async def test_stock_drop_prevents_addition(self):
        prepared = await self.prepare()
        self.product['quantity'] = 1
        self.assertEqual((await self.confirm(prepared)).status_code, 409)
        self.assertEqual(await self.cart(), [])

    async def test_stock_in_existing_cart_is_counted_at_prepare(self):
        first = await self.prepare(quantity=8)
        self.assertEqual((await self.confirm(first, quantity=8)).status_code, 200)
        response = await self.post('prepare', {'product_id': 101, 'quantity': 3})
        self.assertEqual(response.status_code, 409)

    async def test_parallel_single_and_batch_cannot_exceed_stock(self):
        one = await self.prepare(quantity=6)
        batch = await self.post('prepare-batch', {'items': [{'product_id': 101, 'quantity': 6}]})
        self.assertEqual(batch.status_code, 200)
        results = await asyncio.gather(self.confirm(one, quantity=6),
                                      self.post('confirm-batch', {'confirmation_token': batch.json()['confirmation_token']}))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        self.assertEqual(sum(item['quantity'] for item in await self.cart()), 6)

    async def test_batch_is_all_or_nothing_when_one_price_changes(self):
        prepared = await self.post('prepare-batch', {'items': [{'product_id': 101, 'quantity': 1},
                                                              {'product_id': 102, 'quantity': 1}]})
        self.assertEqual(prepared.status_code, 200)

        async def changed_detail(pid):
            product = copy.deepcopy(self.product)
            product.update(id=pid, price=100 if pid == 101 else 200)
            return product

        self.mock_detail.side_effect = changed_detail
        response = await self.post('confirm-batch', {'confirmation_token': prepared.json()['confirmation_token']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(await self.cart(), [])

    async def test_batch_duplicate_rows_are_aggregated(self):
        response = await self.post('prepare-batch', {'items': [{'product_id': 101, 'quantity': 2},
                                                             {'product_id': 101, 'quantity': 3}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['items'][0]['quantity'], 5)
        self.assertEqual(response.json()['total'], 500)

    async def test_selected_warehouse_stock_is_enforced(self):
        response = await self.post('prepare', {'product_id': 101, 'quantity': 4, 'store_id': 13})
        self.assertEqual(response.status_code, 409)
        prepared = await self.prepare(store_id=13)
        self.assertEqual(prepared['items'][0]['store_name'], 'Алматы')
        self.product['stores'][0]['quantity'] = 1
        self.assertEqual((await self.confirm(prepared, store_id=13)).status_code, 409)

    async def test_invalid_numbers_and_price_override_are_rejected(self):
        for body in [{'product_id': 101, 'quantity': True}, {'product_id': 101, 'quantity': 1.5},
                     {'product_id': 101, 'quantity': -1}, {'product_id': 101, 'quantity': 10001},
                     {'product_id': 101, 'quantity': 1, 'price': 0}]:
            with self.subTest(body=body):
                self.assertEqual((await self.post('prepare', body)).status_code, 422)
        self.assertEqual(await self.cart(), [])

    async def test_unknown_or_nonfinite_price_is_not_added(self):
        for price in [None, 'NaN', 'Infinity', -1, True]:
            self.product['price'] = price
            response = await self.post('prepare', {'product_id': 101, 'quantity': 1})
            self.assertEqual(response.status_code, 409, str(price))

    async def test_minimum_multiple_is_enforced(self):
        self.product['properties']['KRATNOST_MIN'] = '2'
        response = await self.post('prepare', {'product_id': 101, 'quantity': 3})
        self.assertEqual(response.status_code, 409)
        prepared = await self.prepare(quantity=4)
        self.assertEqual((await self.confirm(prepared, quantity=4)).status_code, 200)

    async def test_catalog_error_does_not_leak_internal_details(self):
        self.mock_detail.side_effect = RuntimeError('private-key-value')
        response = await self.post('prepare', {'product_id': 101, 'quantity': 1})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('private-key-value', response.text)

    async def test_existing_chat_confirmation_flow_still_works(self):
        main.sessions[self.headers['X-Session-Id']] = {'cart': [], 'last_result': {'selected': {'id': 101, 'warnings': []}}}
        prepared = await self.client.post('/api/query', headers=self.headers,
                                          json={'text': 'добавь в корзину'})
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(prepared.json()['action'], 'cart_confirmation')
        self.assertEqual(await self.cart(), [])
        confirmed = await self.client.post('/api/query', headers=self.headers, json={'text': 'да, добавь'})
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()['action'], 'cart_added')
        self.assertEqual(sum(item['quantity'] for item in await self.cart()), 1)

    async def test_chat_preserves_requested_quantity(self):
        main.sessions[self.headers['X-Session-Id']] = {'cart': [], 'last_result': {'selected': {'id': 101, 'warnings': []}}}
        prepared = await self.client.post('/api/query', headers=self.headers,
                                          json={'text': 'добавь 2 в корзину'})
        self.assertEqual(prepared.json()['action'], 'cart_confirmation')
        self.assertIn('2 ×', prepared.json()['message'])
        await self.client.post('/api/query', headers=self.headers, json={'text': 'да, добавь'})
        self.assertEqual(sum(item['quantity'] for item in await self.cart()), 2)

    async def test_chat_rejects_invalid_quantity(self):
        main.sessions[self.headers['X-Session-Id']] = {'cart': [], 'last_result': {'selected': {'id': 101, 'warnings': []}}}
        for text in ['добавь -2 в корзину', 'добавь 0 в корзину', 'добавь 1.5 в корзину']:
            response = await self.client.post('/api/query', headers=self.headers, json={'text': text})
            self.assertEqual(response.json()['action'], 'cart_error')
        self.assertEqual(await self.cart(), [])


if __name__ == '__main__':
    unittest.main()

