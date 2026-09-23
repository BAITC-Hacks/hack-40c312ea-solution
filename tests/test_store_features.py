import asyncio
import copy
import io
import os
import unittest
import zipfile
from unittest.mock import patch

import httpx

os.environ['EKT_PASSWORD'] = ''
os.environ['RATE_LIMIT_PER_MINUTE'] = '10000'
from app import main
from app.demo import PRODUCTS, DemoClient
from app.upload_guard import validate_file
from app.security import redact


class StoreFeatures(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.sessions.clear()
        self.original = main.engine.ekt, main.engine.catalog
        main.engine.ekt = DemoClient()
        main.engine.catalog = copy.deepcopy(PRODUCTS)
        self.config = patch.object(main.router, 'config', return_value=None)
        self.config.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test')
        self.sid = (await self.client.get('/api/session')).json()['session_id']
        self.headers = {'X-Session-Id': self.sid}

    async def asyncTearDown(self):
        await self.client.aclose()
        main.engine.ekt, main.engine.catalog = self.original
        self.config.stop()

    async def post(self, path, data, headers=None):
        return await self.client.post('/api/' + path, json=data, headers=headers or self.headers)

    async def test_handoff_requires_both_consents(self):
        body = {'contact': 'test@example.com', 'channel': 'email'}
        self.assertEqual((await self.post('handoff/prepare', body)).status_code, 403)
        draft = (await self.post('handoff/prepare', {**body, 'consent': True})).json()
        self.assertEqual(draft['preview']['history'], [])
        self.assertEqual((await self.post('handoff/confirm', {'token': draft['token']})).status_code, 403)
        confirmed = await self.post('handoff/confirm', {'token': draft['token'], 'consent': True})
        self.assertEqual(confirmed.json()['status'], 'demo_saved')
        self.assertEqual((await self.post('handoff/confirm', {'token': draft['token'], 'consent': True})).json()['id'], confirmed.json()['id'])

    async def test_handoff_session_isolation_expiry_cancel(self):
        draft = (await self.post('handoff/prepare', {'contact': 'test@example.com', 'channel': 'email', 'consent': True})).json()
        self.assertEqual((await self.post('handoff/confirm', {'token': draft['token'], 'consent': True}, {'X-Session-Id': 'unknown'})).status_code, 403)
        main.sessions[self.sid]['handoff_pending']['expires'] = 0
        self.assertEqual((await self.post('handoff/confirm', {'token': draft['token'], 'consent': True})).status_code, 403)
        await self.post('handoff/cancel', {})
        self.assertNotIn('handoff_pending', main.sessions[self.sid])

    async def test_kazakh_facts_and_refusal(self):
        data = (await self.post('query', {'text': 'маған 900005 керек', 'language': 'kk'})).json()
        self.assertEqual(data['language'], 'kk')
        self.assertTrue(data['products'])
        self.assertIn('DEMO', data['products'][0]['name'])
        self.assertEqual(data['products'][0]['price'], 2400)
        answer = (await self.post('query', {'text': 'сатып алмаймын', 'language': 'kk'})).json()
        self.assertEqual(answer['action'], 'declined')
        rec = await self.client.get('/api/recommendations/900001', headers=self.headers)
        self.assertEqual(rec.json()['products'], [])

    async def test_recommendations_do_not_change_cart(self):
        rec = (await self.client.get('/api/recommendations/900001', headers=self.headers)).json()
        self.assertTrue(1 <= len(rec['products']) <= 3)
        self.assertNotIn(900001, [p['id'] for p in rec['products']])
        self.assertEqual(main.sessions[self.sid]['cart'], [])

    async def test_clear_rotates_session(self):
        main.sessions[self.sid]['history'] = [{'role': 'user', 'content': 'private'}]
        new = (await self.post('session/clear', {})).json()['session_id']
        self.assertNotEqual(new, self.sid)
        self.assertNotIn(self.sid, main.sessions)

    async def test_cart_edit_requires_exact_confirmation(self):
        line = {'product_id': 900005, 'quantity': 2}
        token = (await self.post('cart/prepare', line)).json()['confirmation_token']
        self.assertEqual((await self.post('cart/confirm', {**line, 'confirmation_token': token})).status_code, 200)
        edit = (await self.post('cart/prepare-edit', {'index': 0, 'quantity': 3})).json()
        self.assertEqual(main.sessions[self.sid]['cart'][0]['quantity'], 2)
        self.assertEqual((await self.post('cart/confirm-edit', {'confirmation_token': edit['confirmation_token']})).status_code, 200)
        self.assertEqual(main.sessions[self.sid]['cart'][0]['quantity'], 3)
        self.assertEqual((await self.post('cart/confirm-edit', {'confirmation_token': edit['confirmation_token']})).status_code, 400)
        removal = (await self.post('cart/prepare-edit', {'index': 0, 'quantity': 0})).json()
        await self.post('cart/confirm-edit', {'confirmation_token': removal['confirmation_token']})
        self.assertEqual(main.sessions[self.sid]['cart'], [])

    async def test_csv_preview_and_selection(self):
        response = await self.client.post('/api/upload', headers=self.headers, files={'file': ('list.csv', 'товар,количество\n900005,2'.encode(), 'text/csv')}, data={'preview_only': 'true'})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['preview'][0]['quantity'], 2)
        solved = await self.post('upload/confirm', {'token': data['token'], 'lines': data['preview']})
        self.assertEqual(solved.status_code, 200, solved.text)
        self.assertEqual(main.sessions[self.sid]['cart'], [])
        self.assertEqual((await self.post('upload/confirm', {'token': data['token'], 'lines': data['preview']})).status_code, 400)

    async def test_image_requires_consent_and_valid_bytes(self):
        from PIL import Image
        b = io.BytesIO()
        Image.new('RGB', (10, 10)).save(b, format='JPEG')
        r = await self.client.post('/api/upload', headers=self.headers, files={'file': ('test.jpg', b.getvalue(), 'image/jpeg')})
        self.assertEqual(r.status_code, 403)
        r = await self.client.post('/api/upload', headers=self.headers, files={'file': ('test.jpg', b'bad', 'image/jpeg')}, data={'allow_external': 'true'})
        self.assertEqual(r.status_code, 422)

    async def test_security_headers_and_origin(self):
        r = await self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn("script-src 'self'", r.headers['content-security-policy'])
        self.assertEqual((await self.post('session/clear', {}, {'Origin': 'https://evil.invalid'})).status_code, 403)
        self.assertEqual((await self.post('sync', {})).status_code, 403)

    def test_upload_validation_and_redaction(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            validate_file('bad.exe', b'data')
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('[Content_Types].xml', '')
            z.writestr('word/document.xml', '')
            z.writestr('../evil', '')
        with self.assertRaises(HTTPException):
            validate_file('test.docx', b.getvalue())
        self.assertNotIn('test@example.com', redact('test@example.com +77011234567'))


if __name__ == '__main__':
    unittest.main()

