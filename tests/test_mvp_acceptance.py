"""Offline acceptance checks for the integrated, embeddable bilingual MVP."""
import copy
import io
import os
import unittest
from unittest.mock import patch

import httpx
os.environ['RATE_LIMIT_PER_MINUTE'] = '10000'
from app import main
from app.demo import PRODUCTS, DemoClient


class Acceptance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.sessions.clear()
        self.original = main.engine.ekt, main.engine.catalog
        main.engine.ekt, main.engine.catalog = DemoClient(), copy.deepcopy(PRODUCTS)
        self.config = patch.object(main.router, 'config', return_value=None)
        self.config.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test')
        self.sid = (await self.client.get('/api/session')).json()['session_id']
        self.headers = {'X-Session-Id': self.sid}

    async def asyncTearDown(self):
        await self.client.aclose()
        main.engine.ekt, main.engine.catalog = self.original
        self.config.stop()

    async def post(self, path, body):
        return await self.client.post('/api/' + path, json=body, headers=self.headers)

    async def test_catalog_shows_verified_stock_and_pagination(self):
        d = (await self.client.get('/api/catalog?limit=2')).json()
        self.assertEqual(len(d['products']), 2)
        self.assertTrue(d['verified'] and d['has_more'])
        self.assertTrue(all(p['quantity'] == 20 for p in d['products']))
        second = (await self.client.get('/api/catalog?limit=2&page=2')).json()
        self.assertNotEqual(d['products'][0]['id'], second['products'][0]['id'])
        filtered = (await self.client.get('/api/catalog?category=breaker')).json()
        self.assertEqual([p['id'] for p in filtered['products']], [900005])

    async def test_local_cart_link_has_current_session_contents(self):
        line = {'product_id': 900005, 'quantity': 2}
        draft = (await self.post('cart/prepare', line)).json()
        self.assertEqual((await self.client.get('/api/cart', headers=self.headers)).json()['cart'], [])
        receipt = (await self.post('cart/confirm', {**line, 'confirmation_token': draft['confirmation_token']})).json()
        self.assertEqual(receipt['cart_url'], '/cart')
        self.assertEqual((await self.client.get(receipt['cart_url'])).status_code, 200)
        current = (await self.client.get('/api/cart', headers=self.headers)).json()
        self.assertEqual(current['cart'], receipt['cart'])
        self.assertEqual(current['cart'][0]['quantity'], 2)

    async def test_manager_channels_are_real_links_not_fake_delivery(self):
        d = (await self.client.get('/api/manager?language=kk')).json()
        self.assertEqual(d['mode'], 'direct_contact')
        self.assertEqual(d['phone_url'], 'tel:+77273468888')
        self.assertEqual(d['whatsapp_url'], 'https://wa.me/77782768888/')
        self.assertIn('ekt.kz/about/contacts', d['source'])
        self.assertIn('Қоңырау', d['message'])

    async def test_widget_embedding_is_limited_to_configured_origins(self):
        normal = await self.client.get('/')
        embedded = await self.client.get('/?embed=1')
        self.assertEqual(normal.headers['x-frame-options'], 'DENY')
        self.assertNotIn('x-frame-options', embedded.headers)
        self.assertIn("frame-ancestors 'self' https://ekt.kz", embedded.headers['content-security-policy'])
        self.assertNotIn('*', embedded.headers['content-security-policy'].split('frame-ancestors')[1])
        self.assertEqual((await self.client.get('/widget.js')).status_code, 200)

    async def test_kazakh_cart_quantity_and_confirmation(self):
        await self.post('query', {'text': 'Маған 900005 керек', 'language': 'kk'})
        draft = await self.post('query', {'text': 'себетке 2 қос', 'language': 'kk'})
        self.assertEqual(draft.json()['action'], 'cart_confirmation')
        self.assertEqual(main.sessions[self.sid]['cart'], [])
        result = (await self.post('query', {'text': 'иә, қос', 'language': 'kk'})).json()
        self.assertEqual(result['action'], 'cart_added')
        self.assertEqual(result['cart'][0]['quantity'], 2)
        self.assertIn('Тауарлар', result['message'])

    async def test_unrelated_manager_turn_invalidates_chat_confirmation(self):
        await self.post('query', {'text': '900005'})
        await self.post('query', {'text': 'добавь 2 в корзину'})
        await self.post('query', {'text': 'Позови менеджера'})
        await self.post('query', {'text': 'да, добавь'})
        self.assertEqual(main.sessions[self.sid]['cart'], [])

    async def test_text_pdf_works_in_parser_process(self):
        import pymupdf
        doc = pymupdf.open(); page = doc.new_page(); page.insert_text((72, 72), '900005 2')
        data = doc.tobytes(); doc.close()
        r = await self.client.post('/api/upload', headers=self.headers, files={'file': ('spec.pdf', data, 'application/pdf')}, data={'preview_only': 'true'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('900005', r.json()['preview'][0]['description'])

    async def test_valid_image_requires_consent_then_calls_vision(self):
        from PIL import Image
        buf = io.BytesIO(); Image.new('RGB', (80, 40), 'white').save(buf, 'PNG')
        file = {'file': ('image.png', buf.getvalue(), 'image/png')}
        self.assertEqual((await self.client.post('/api/upload', files=file, headers=self.headers)).status_code, 403)
        async def vision(data, mime):
            return [{'description': '900005', 'quantity': 2}], 'VISION test'
        with patch.object(main.router, 'read_image', side_effect=vision):
            r = await self.client.post('/api/upload', files=file, headers=self.headers, data={'allow_external': 'true', 'preview_only': 'true'})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['preview'][0]['quantity'], 2)

    def test_kazakh_file_quantities_and_invalid_quantities(self):
        from app.files import parse_file
        rows = parse_file('spec.csv', 'Тауар,Саны\n900005,2'.encode())
        self.assertEqual(rows, [{'description': '900005', 'quantity': 2}])
        with self.assertRaises(ValueError):
            parse_file('spec.csv', b'description,quantity\n900005,-2')


if __name__ == '__main__':
    unittest.main()
