import asyncio
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from rapidfuzz import fuzz

from .ekt import EKTClient
from .query_text import normalize_catalog_terms
from .grounding import CatalogUnavailable

CACHE = Path(__file__).parent.parent / 'catalog_cache.json'


def tokens(text: str) -> str:
    return re.sub(r'[^\w]+', ' ', text.casefold()).strip()


def product_kind(text: str) -> str | None:
    value = normalize_catalog_terms(text).casefold()
    if re.search(r'ламп\w* приобретаются отдельно|гирлянд|\bспот\b', value):
        return 'luminaire'
    for kind, pattern in [
        ('rcd', r'\bузо\b|\bавдт|\bдиф'),
        ('breaker', r'\bавтомат|\bав\b|\bавт\.\s*выкл'),
        ('contactor', r'\bконтактор|\bпускатель|\bкми[-\s]|\bкмэ[-\s]'),
        ('relay', r'\bреле'), ('holder', r'\bпатрон'),
        ('gland', r'кабельн\w* ввод|\bсальник'),
        ('luminaire', r'\bсветильник|\bпрожектор|\bлюстр|\b(?:дво|дпо|лпо|лво|спо|дсп|дпп)\b'),
        ('enclosure', r'\bщит(?:ок|а|ы|ов|ки)?\b|\bшкаф|\bщр[внсп]|\bвру[-\s]|\bщо[-\s]'),
        ('terminal', r'\bклемм|\bнаконечник|\bгильза'),
        ('cable', r'\bкабел[ьи]\b|\bпровод(?:а|ов)?\b|\bа?ввг\w*|\b(?:пвс|nym|nyy|кг|шввп|пугв)\b'),
        ('strip', r'\bлент'),
        ('signal', r'ламп\w*\s+(?:коммутац|сигн)|сиг(?:нальн\w*|\.)?\s*ламп'),
        ('industrial_lamp', r'\b(?:дрв|дри|днат|дрл)\b'),
        ('lamp', r'\bламп(?:а|ы|у|е|ой|ам|ами|ах)?\b|\bled\s+(?:[acgp]\d{2,3}|mr\d{2}|gu\d{2})\b|\bшам(?:дар|ы)?\b'),
        ('data_socket', r'(?:розетк.*(?:\brj[- ]?\d|\btel\b|\btv\b|keystone|информацион)|(?:телефон|компьютерн).*розетк)'),
        ('socket', r'\bрозетк'), ('switch', r'\bвыключател'), ('rail', r'\bdin\b|дин.рейк')]:
        if re.search(pattern, value):
            return kind
    return None


def catalog_kind(product: dict) -> str | None:
    """Use the item's own purpose and source category, not incidental mention of a device."""
    name = product.get('name', '').casefold()
    if re.search(r'\b(?:фильтр|решетка|решётка|катушка|бирка|рамка|накладка|крышка|заглушка|дверь|дверца|крепление|кронштейн|драйвер|замок|подставка)\b|цоколь для щ|панель для счетчика|корпус\s+(?:настенн\w*\s+)?розет|кабель[- ]канал|установка пожаротушения', name):
        return 'accessory'
    kind = product_kind(name)
    # The noun before "for" describes the item; an incidental compatible device
    # must not turn a key, adapter or spare part into that device.
    if ' для ' in name and product_kind(name.split(' для ', 1)[0]) is None:
        return 'accessory'
    directory = urlsplit(product.get('url') or '').path.rsplit('/', 2)[0]
    if re.search(r'/[^/]*(?:aksessuar|zamki_dlya|komplektuyushch)[^/]*/', directory + '/'):
        return kind if kind in {'terminal', 'gland', 'rail', 'holder'} else 'accessory'
    if '/kabel_provod/' in directory and kind in {None, 'cable'}:
        return 'cable'
    if '/shkafy_shchity/' in directory and kind in {None, 'enclosure', 'contactor'}:
        return 'enclosure'
    return kind


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
    name = product.get('name', '')
    def named(pattern):
        m = re.search(pattern, name, re.I)
        return m.group(0) if m else None
    return {
        'brand': p.get('TORGOVAYA_MARKA'),
        'poles': p.get('KOLICHESTVO_POLYUSOV'),
        'current': p.get('NOMINALNYY_TOK'),
        'voltage': p.get('NOMINALNOE_NAPRYAZHENIE'),
        'breaking_capacity': p.get('NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST'),
        'base': p.get('TIP_TSOKOLYA') or named(r'\b(?:[eеg]\s*\d{1,2}|gx53|gu10)\b'),
        'power': p.get('MOSHCHNOST_W') or named(r'\b\d+(?:[.,]\d+)?\s*(?:вт|w)\b'),
        'color_temperature': p.get('TSVETOVAYA_TEMPERATURA') or named(r'\b\d{4}\s*[kк]\b'),
        'luminous_flux': p.get('SVETOVOY_POTOK_LM') or named(r'\b\d+\s*(?:lm|лм)\b'),
        'mounting': p.get('SPOSOB_MONTAZHA'),
        'application': p.get('OBLAST_PRIMENENIYA'),
    }


def conflict_warnings(product: dict) -> list[str]:
    name = product.get('name', '')
    current = str(attributes(product).get('current') or '')
    named = re.search(r'(\d+)\s*[аa](?![\w])', name, re.I)
    field = re.search(r'(\d+)\s*[аa](?![\w])', current, re.I)
    if named and field and named.group(1) != field.group(1):
        return [f'Источник противоречив: название {named.group(1)} А, свойство {field.group(1)} А. Требуется проверка менеджером.']
    return []


def certificate_info(product: dict) -> tuple[str | None, str]:
    from urllib.parse import urljoin, urlsplit
    def urls(value):
        if isinstance(value, str):
            candidate = urljoin('https://ekt.kz/', value.strip())
            if value.strip().startswith(('https://', '/')) and urlsplit(candidate).scheme == 'https':
                yield candidate
        elif isinstance(value, list):
            for item in value:
                yield from urls(item)
        elif isinstance(value, dict):
            for key in ('url', 'href', 'src', 'file', 'value', 'VALUE', 'SRC'):
                if key in value:
                    yield from urls(value[key])
    present = False
    for container in (product, product.get('properties') or {}):
        for key, value in container.items():
            if any(marker in str(key).casefold() for marker in ('sertif', 'certif', 'сертифик')):
                present = True
                found = next(urls(value), None)
                if found:
                    return found, 'Ссылка на сертификат указана в API'
    return None, ('Поле сертификата есть, но ссылка не предоставлена' if present else 'Сертификат не указан в API')


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
                if seen and not self.catalog:
                    self.catalog = list(seen.values())
                if finished:
                    break
            if seen:
                self.catalog = list(seen.values())
                CACHE.write_text(json.dumps(self.catalog, ensure_ascii=False), encoding='utf-8')
                self.last_synced = datetime.now(timezone.utc).isoformat()
            return len(self.catalog)
        finally:
            self.syncing = False

    async def search(self, query: str, limit: int = 6) -> list[dict]:
        match = re.fullmatch(r'\s*(?:id[=:\s]*)?(\d{5,7})\s*', query, re.I)
        if match:
            try:
                detail = await self.ekt.detail(int(match.group(1)))
                if detail.get('id') == int(match.group(1)):
                    if detail.get('quantity') == 0 and detail.get('name') and detail['name'] != query:
                        alternatives = await self.search(detail['name'], limit=max(limit, 18))
                        amps, poles = requested_electrical(detail['name'])
                        relevant = [p for p in alternatives if p['id'] != detail['id'] and (p.get('quantity') or 0) > 0
                                    and (amps or poles) and requested_electrical(p.get('name', '')) == (amps, poles)
                                    and catalog_kind(p) == catalog_kind(detail)
                                    and not conflict_warnings(p)]
                        for p in relevant:
                            p['_alternative_for'] = detail['id']
                            p['_alternative_reason'] = f"Аналог отсутствующего товара {detail['id']}: совпадают " + ', '.join(x for x in [f'ток {amps} А' if amps else '', f'полюса {poles}' if poles else ''] if x) + '. Остальные параметры требуют проверки.'
                        return [detail] + relevant[:3]
                    return [detail]
                return []
            except CatalogUnavailable:
                return [detail] if detail.get('id') == int(match.group(1)) else []
            except Exception as exc:
                raise CatalogUnavailable() from exc
        exact = next((p for p in self.catalog if str(p.get('article', '')).casefold() == query.strip().casefold()), None)
        if exact:
            return await self.search(str(exact['id']), limit)
        query = normalize_catalog_terms(query)
        q = tokens(query)
        ignored = {'меня', 'чтобы', 'ты', 'подобрал', 'подобрать', 'помоги', 'мне', 'нужен', 'нужна', 'нужны', 'нужно', 'для', 'или', 'это', 'есть', 'какой', 'какая', 'найди', 'подбери', 'хочу', 'пожалуйста', 'товар', 'нужное', 'the', 'for', 'могу', 'купить'}
        words = [w for w in q.split() if len(w) > 1 and w not in ignored]
        kind = product_kind(query)
        requested_amps, requested_poles = requested_electrical(query)
        brand = requested_brand(query)
        cable_size = requested_cable_size(query)
        cable_family = re.search(r'\b(а?ввг\w*|пвс|nym|nyy|шввп|пугв)\b', query, re.I) if kind == 'cable' else None
        ranked = []
        for item in self.catalog:
            hay = tokens(f"{item.get('name','')} {item.get('article','')} {item.get('url','')}")
            if kind and catalog_kind(item) != kind:
                continue
            if cable_family and not re.search(r'\b' + re.escape(cable_family.group(1)), item.get('name', ''), re.I):
                continue
            if not kind and not any(w in hay for w in words):
                continue
            score = fuzz.WRatio(q, hay)
            if kind:
                score += 45
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
        for (score, source), detail in zip(picked, details):
            if isinstance(detail, dict) and detail.get('id') == source['id']:
                if kind and catalog_kind(detail) != kind:
                    continue
                detail['_search_score'] = score
                result.append(detail)
        if picked and not result and any(isinstance(d, Exception) or (isinstance(d, dict) and d.get('id') != p['id']) for (_, p), d in zip(picked, details)):
            raise CatalogUnavailable()
        return result

    async def solution(self, query: str, mode: str = 'best', *, supplied_products=None) -> dict:
        products = supplied_products if supplied_products is not None else await self.search(query, limit=24 if mode in {'cheapest', 'expensive', 'available'} else 6)
        requested_amps, requested_poles = requested_electrical(query)
        brand = requested_brand(query)
        cable_size = requested_cable_size(query)
        for p in products:
            p['_match_warnings'] = []
            requested_base = re.search(r'\b([eеg]\s*\d{1,2})\b', query, re.I)
            if requested_base:
                normalize_base = lambda value: re.sub(r'\s+', '', str(value).lower().replace('е', 'e'))
                named_base = re.search(r'\b([eеg]\s*\d{1,2})\b', p.get('name', ''), re.I)
                actual_base = attributes(p).get('base') or (named_base.group(1) if named_base else None)
                if not actual_base or normalize_base(actual_base) != normalize_base(requested_base.group(1)):
                    p['_match_warnings'].append('Цоколь отличается от запроса или не подтверждён каталогом.')
            kind = product_kind(query)
            if kind and catalog_kind(p) != kind:
                p['_match_warnings'].append('Назначение товара отличается от запроса.')
            found_amps, found_poles = requested_electrical(p.get('name', ''))
            for wanted, actual, label in [(requested_amps, attributes(p).get('current'), 'Ток'), (requested_poles, attributes(p).get('poles'), 'Полюса')]:
                measured = re.search(r'\d+(?:[.,]\d+)?', str(actual or ''))
                if wanted and measured and float(measured.group().replace(',', '.')) != float(wanted):
                    p['_match_warnings'].append(f'{label}: свойство API {actual}, требуется {wanted}.')
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
            elif cable_size and not found_size:
                p['_match_warnings'].append('Сечение/число жил не подтверждены каталогом.')
            if cable_size and '+' in p.get('name', '') and '+' not in query:
                p['_match_warnings'].append('Есть дополнительная группа жил; состав кабеля отличается от указанного запроса.')
        if mode == 'cheapest':
            products.sort(key=lambda p: (bool(p['_match_warnings']), not bool(p.get('quantity')), p.get('price') is None, p.get('price') or 10**12))
        elif mode == 'expensive':
            products.sort(key=lambda p: (bool(p['_match_warnings']), not bool(p.get('quantity')), p.get('price') is None, -(p.get('price') or 0)))
        elif mode == 'available':
            products.sort(key=lambda p: (bool(p['_match_warnings']), -(p.get('quantity') or 0)))
        elif mode == 'brand':
            products.sort(key=lambda p: (bool(p['_match_warnings']), brand not in p.get('name', '').casefold() if brand else False, -(p.get('quantity') or 0), -p.get('_search_score', 0)))
        else:
            products.sort(key=lambda p: (bool(p['_match_warnings']), not bool(p.get('quantity')), -p.get('_search_score', 0)))
        cards = []
        for p in (products[:6] if supplied_products is None else products):
            certificate, certificate_status = certificate_info(p)
            attrs = attributes(p)
            missing_fields = []
            for field in ('price', 'quantity'):
                if p.get(field) is None:
                    p['_match_warnings'].append('Цена не указана в API.' if field == 'price' else 'Остаток не указан в API.')
                    missing_fields.append(field)
            if requested_amps and not attrs['current']:
                missing_fields.append('nominal_current')
            if requested_poles and not attrs['poles']:
                missing_fields.append('poles')
            cards.append({
                'id': p['id'], 'name': html.unescape(p.get('name') or ''), 'article': p.get('article'),
                'match_score': p.get('_search_score'),
                'price': p.get('price'), 'quantity': p.get('quantity'),
                'url': p.get('url'), 'image': p.get('image'),
                'description': p.get('description'), 'attributes': attrs,
                'warnings': conflict_warnings(p) + p['_match_warnings'], 'stores': [s for s in p.get('stores', []) if s.get('quantity', 0) > 0],
                'compatibility': 'uncertain' if conflict_warnings(p) else ('incompatible' if p['_match_warnings'] else 'uncertain'),
                'missing_fields': missing_fields,
                'reason': p.get('_alternative_reason') or ('Совпадение по названию; полная совместимость не подтверждена.' if not p['_match_warnings'] else 'Есть расхождение с запросом; используйте как аналог только после проверки.'),
                'alternative_for': p.get('_alternative_for'),
                'certificate': certificate, 'certificate_status': certificate_status,
                'source': f"https://ekt.kz/api/products/detail?id={p['id']}",
                'verified_at': datetime.now(timezone.utc).isoformat(),
            })
        return {'query': query, 'mode': mode, 'products': cards, 'selected': cards[0] if cards else None, 'total': cards[0]['price'] if cards else None, 'route': 'DIRECT', 'events': ['Requirements extracted', 'Catalog searched', f'{len(cards)} live details verified', 'Solution generated'], 'warnings': ['Индивидуальный срок доставки API не предоставляет.']}
