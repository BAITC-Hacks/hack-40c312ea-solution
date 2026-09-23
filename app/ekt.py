import asyncio
import os
from typing import Any

import httpx


BASE = 'https://ekt.kz/api/products'


class EKTClient:
    def __init__(self):
        self.user = os.getenv('EKT_USER', 'apiuser')
        self.password = os.getenv('EKT_PASSWORD', '')
        self.client = httpx.AsyncClient(auth=(self.user, self.password), timeout=20, follow_redirects=True)

    async def _get(self, url: str, params: dict) -> dict[str, Any]:
        for attempt in range(3):
            try:
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.4 * (2 ** attempt))
        raise RuntimeError('EKT request failed')

    async def page(self, number: int) -> dict[str, Any]:
        return await self._get(BASE, {'page': number})

    async def detail(self, product_id: int) -> dict[str, Any]:
        return await self._get(BASE + '/detail', {'id': product_id})

    async def pages(self, count: int) -> list[dict[str, Any]]:
        result = []
        first_id = None
        for start in range(1, count + 1, 5):
            batch = await asyncio.gather(*(self.page(i) for i in range(start, min(count + 1, start + 5))), return_exceptions=True)
            for page in batch:
                if isinstance(page, dict):
                    items = page.get('items', [])
                    if items and first_id is None:
                        first_id = items[0].get('id')
                    if result and items and items[0].get('id') == first_id:
                        return list({item['id']: item for item in result}.values())
                    result.extend(items)
            if any(isinstance(page, dict) and not page.get('items') for page in batch):
                break
        return list({item['id']: item for item in result}.values())

    async def close(self):
        await self.client.aclose()
