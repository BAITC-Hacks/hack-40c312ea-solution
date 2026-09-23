import asyncio
import os
from typing import Any

import httpx


BASE = 'https://ekt.kz/api/products'


class EKTClient:
    def __init__(self):
        self.user = os.getenv('EKT_USER', 'apiuser')
        self.password = os.getenv('EKT_PASSWORD', '')
        self.client = httpx.AsyncClient(auth=(self.user, self.password), timeout=12, follow_redirects=True)

    async def page(self, number: int) -> dict[str, Any]:
        response = await self.client.get(BASE, params={'page': number})
        response.raise_for_status()
        return response.json()

    async def detail(self, product_id: int) -> dict[str, Any]:
        response = await self.client.get(BASE + '/detail', params={'id': product_id})
        response.raise_for_status()
        return response.json()

    async def pages(self, count: int) -> list[dict[str, Any]]:
        result = []
        for start in range(1, count + 1, 5):
            batch = await asyncio.gather(*(self.page(i) for i in range(start, min(count + 1, start + 5))), return_exceptions=True)
            for page in batch:
                if isinstance(page, dict):
                    result.extend(page.get('items', []))
            if any(isinstance(page, dict) and not page.get('items') for page in batch):
                break
        return result

    async def close(self):
        await self.client.aclose()
