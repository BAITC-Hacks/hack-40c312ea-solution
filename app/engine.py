import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from .ekt import EKTClient

CACHE = Path(__file__).parent.parent / 'catalog_cache.json'


def tokens(text: str) -> str:
    return re.sub(r'[^\w]+', ' ', text.casefold()).strip()


def requested_electrical(text: str) -> tuple[str | None, str | None]:
    amps = re.search(r'\b(\d{1,4})\s*[aа](?!\w)', text, re.I)
    poles = re.search(r'\b([1-4])\s*(?:p|п|ф|пол)', text, re.I)
    return (amps.group(1) if amps else None, poles.group(1) if poles else None)


def requested_brand(text: str) -> str | None:
    lower = text.casefold()
    return next((brand for brand in ('schneider', 'legrand', 'abb', 'iek', 'chint', 'dekraft', 'ekt') if re.search(r'\b' + brand + r'\b', lower)), None)


def requested_cable_size(text: str) -> tuple[str, str] | None:
    match = re.search(r'\b(\d+)\s*[xх×]\s*(\d+(?:[,.]\d+)?)\b', text, re.I)
    return (match.group(1), match.group(2).replace(',', '.')) if match else None


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


def certificate_info(product: dict) -> tuple[str | None, str]:
    for key, value in (product.get('properties') or {}).items():
        if any(marker in key.casefold() for marker in ('sertif', 'certif', 'сертифик')):
            values = value if isinstance(value, list) else [value]
            for candidate in values:
                if isinstance(candidate, str) and candidate.startswith('https://'):
                    return candidate, 'Ссылка на сертификат указана в API'
            return None, 'Поле сертификата есть, но ссылка не предоставлена'
    return None, 'Сертификат не указан в API'


class Engine:
    def __init__(self):
        self.ekt = EKTClient()
        self.catalog: list[dict[str, Any]] = []
        self.syncing = False
        self.last_synced = None
        if CACHE.exists():
            try:
                self.catalog = json.loads(CACHE.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass

    async def sync(self) -> int:
        if self.syncing:
            return len(self.catalog)
        self.syncing = True
        try:
            pages = max(1, min(int(os.getenv('CATALOG_PAGES', '800')), 1000))
            first_id = None
            seen = {}
            for start in range(1, pages + 1, 10):
                batch = await asyncio.gather(*(self.ekt.page(i) for i in range(start, min(pages + 1, start + 10))), return_exceptions=True)
                finished = False
                for page in batch:
                    if not isinstance(page, dict):
                        continue
                    items = page.get('items') or []
                    if not items:
                        finished = True
                        continue
                    if first_id is None:
                        first_id = items[0].get('id')
                    elif items[0].get('id') == first_id:
                        finished = True
                        continue
                    seen.update({item['id']: item for item in items})
                if seen:
                    self.catalog = list(seen.values())
                if finished:
                    break
            if self.catalog:
                CACHE.write_text(json.dumps(self.catalog, ensure_ascii=False), encoding='utf-8')
                self.last_synced = datetime.now(timezone.utc).isoformat()
            return len(self.catalog)
        finally:
            self.syncing = False

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
        requested_amps, requested_poles = requested_electrical(query)
        brand = requested_brand(query)
        cable_size = requested_cable_size(query)
        ranked = []
        for item in self.catalog:
            hay = tokens(f"{item.get('name','')} {item.get('article','')} {item.get('url','')}")
            score = fuzz.WRatio(q, hay)
            score += 12 * sum(w in hay for w in words)
            item_amps, item_poles = requested_electrical(item.get('name', ''))
            if requested_amps and item_amps:
                score += 80 if requested_amps == item_amps else -100
            if requested_poles and item_poles:
                score += 50 if requested_poles == item_poles else -70
            if brand:
                score += 150 if brand in hay else -80
            if cable_size:
                item_size = requested_cable_size(item.get('name', ''))
                if item_size:
                    score += 100 if item_size == cable_size else -130
            if score > 30:
                ranked.append((score, item))
        ranked.sort(key=lambda x: x[0], reverse=True)
        picked = ranked[:limit]
        details = await asyncio.gather(*(self.ekt.detail(int(p['id'])) for _, p in picked), return_exceptions=True)
        result = []
        for (score, _), detail in zip(picked, details):
            if isinstance(detail, dict) and detail.get('id'):
                detail['_search_score'] = score
                result.append(detail)
        return result

    async def solution(self, query: str, mode: str = 'best') -> dict:
        products = await self.search(query)
        requested_amps, requested_poles = requested_electrical(query)
        brand = requested_brand(query)
        cable_size = requested_cable_size(query)
        for p in products:
            p['_match_warnings'] = []
            found_amps, found_poles = requested_electrical(p.get('name', ''))
            if requested_amps and found_amps and requested_amps != found_amps:
                p['_match_warnings'].append(f'Ток отличается: требуется {requested_amps} А, товар {found_amps} А.')
            elif requested_amps and not found_amps and not attributes(p).get('current'):
                p['_match_warnings'].append('Номинальный ток товара не указан; соответствие не подтверждено.')
            if requested_poles and found_poles and requested_poles != found_poles:
                p['_match_warnings'].append(f'Полюса отличаются: требуется {requested_poles}, товар {found_poles}.')
            elif requested_poles and not found_poles and not attributes(p).get('poles'):
                p['_match_warnings'].append('Число полюсов товара не указано; соответствие не подтверждено.')
            found_size = requested_cable_size(p.get('name', ''))
            if cable_size and found_size and cable_size != found_size:
                p['_match_warnings'].append(f'Сечение/число жил отличаются: требуется {cable_size[0]}×{cable_size[1]}, товар {found_size[0]}×{found_size[1]}.')
        if mode == 'cheapest':
            products.sort(key=lambda p: (bool(p['_match_warnings']), not bool(p.get('quantity')), p.get('price') is None, p.get('price') or 10**12))
        elif mode == 'available':
            products.sort(key=lambda p: (bool(p['_match_warnings']), -(p.get('quantity') or 0)))
        elif mode == 'brand':
            products.sort(key=lambda p: (bool(p['_match_warnings']), brand not in p.get('name', '').casefold() if brand else False, -(p.get('quantity') or 0), -p.get('_search_score', 0)))
        else:
            products.sort(key=lambda p: (bool(p['_match_warnings']), not bool(p.get('quantity')), -p.get('_search_score', 0)))
        cards = []
        for p in products:
            certificate, certificate_status = certificate_info(p)
            attrs = attributes(p)
            missing_fields = []
            if requested_amps and not attrs['current']:
                missing_fields.append('nominal_current')
            if requested_poles and not attrs['poles']:
                missing_fields.append('poles')
            cards.append({
                'id': p['id'], 'name': p.get('name'), 'article': p.get('article'),
                'match_score': p.get('_search_score'),
                'price': p.get('price'), 'quantity': p.get('quantity'),
                'url': p.get('url'), 'image': p.get('image'),
                'description': p.get('description'), 'attributes': attrs,
                'warnings': conflict_warnings(p) + p['_match_warnings'], 'stores': [s for s in p.get('stores', []) if s.get('quantity', 0) > 0],
                'compatibility': 'uncertain' if conflict_warnings(p) else ('incompatible' if p['_match_warnings'] else 'uncertain'),
                'missing_fields': missing_fields,
                'reason': 'Совпадение по названию; полная совместимость не подтверждена.' if not p['_match_warnings'] else 'Есть расхождение с запросом; используйте как аналог только после проверки.',
                'certificate': certificate, 'certificate_status': certificate_status,
            })
        return {'query': query, 'mode': mode, 'products': cards, 'selected': cards[0] if cards else None, 'total': cards[0]['price'] if cards else None, 'route': 'DIRECT', 'events': ['Requirements extracted', 'Catalog searched', f'{len(cards)} live details verified', 'Solution generated'], 'warnings': ['Индивидуальный срок доставки API не предоставляет.']}

