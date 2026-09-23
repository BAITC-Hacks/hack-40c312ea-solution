import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from .ekt import EKTClient

CACHE = Path(__file__).parent.parent / 'catalog_cache.json'


def tokens(text: str) -> str:
    return re.sub(r'[^\w]+', ' ', text.casefold()).strip()


def attributes(product: dict) -> dict:
    p = product.get('properties') or {}
    return {
        'brand': p.get('TORGOVAYA_MARKA'),
        'poles': p.get('KOLICHESTVO_POLYUSOV'),
        'current': p.get('NOMINALNYY_TOK'),
        'voltage': p.get('NOMINALNOE_NAPRYAZHENIE'),
        'breaking_capacity': p.get('NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST'),
    }


def conflict_warnings(product: dict) -> list[str]:
    name = product.get('name', '')
    current = attributes(product).get('current') or ''
    named = re.search(r'(\d+)\s*[аa](?![\w])', name, re.I)
    field = re.search(r'(\d+)\s*[аa](?![\w])', current, re.I)
    if named and field and named.group(1) != field.group(1):
        return [f'Источник противоречив: название {named.group(1)} А, свойство {field.group(1)} А. Требуется проверка менеджером.']
    return []


class Engine:
    def __init__(self):
        self.ekt = EKTClient()
        self.catalog: list[dict[str, Any]] = []
        if CACHE.exists():
            try:
                self.catalog = json.loads(CACHE.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass

    async def sync(self) -> int:
        pages = max(1, min(int(os.getenv('CATALOG_PAGES', '25')), 250))
        items = await self.ekt.pages(pages)
        if items:
            self.catalog = items
            CACHE.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
        return len(self.catalog)

    async def search(self, query: str, limit: int = 6) -> list[dict]:
        match = re.search(r'\b(?:id[=:\s]*)?(\d{5,7})\b', query, re.I)
        if match:
            try:
                detail = await self.ekt.detail(int(match.group(1)))
                if detail.get('id'):
                    return [detail]
            except Exception:
                pass
        q = tokens(query)
        words = [w for w in q.split() if len(w) > 1]
        ranked = []
        for item in self.catalog:
            hay = tokens(f"{item.get('name','')} {item.get('article','')} {item.get('url','')}")
            score = fuzz.WRatio(q, hay)
            score += 12 * sum(w in hay for w in words)
            if score > 30:
                ranked.append((score, item))
        ranked.sort(key=lambda x: x[0], reverse=True)
        picked = [item for _, item in ranked[:limit]]
        details = await asyncio.gather(*(self.ekt.detail(int(p['id'])) for p in picked), return_exceptions=True)
        return [d for d in details if isinstance(d, dict) and d.get('id')]

    async def solution(self, query: str, mode: str = 'best') -> dict:
        products = await self.search(query)
        if mode == 'cheapest':
            products.sort(key=lambda p: (p.get('price') is None, p.get('price') or 10**12))
        elif mode == 'available':
            products.sort(key=lambda p: -(p.get('quantity') or 0))
        cards = []
        for p in products:
            cards.append({
                'id': p['id'], 'name': p.get('name'), 'article': p.get('article'),
                'price': p.get('price'), 'quantity': p.get('quantity'),
                'url': p.get('url'), 'image': p.get('image'),
                'description': p.get('description'), 'attributes': attributes(p),
                'warnings': conflict_warnings(p), 'stores': [s for s in p.get('stores', []) if s.get('quantity', 0) > 0],
                'certificate': None,
            })
        return {'query': query, 'mode': mode, 'products': cards, 'route': 'DIRECT', 'events': ['Requirements extracted', 'Catalog searched', f'{len(cards)} live details verified', 'Solution generated'], 'warnings': ['Срок доставки API не предоставляет.']}
