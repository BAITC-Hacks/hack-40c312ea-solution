import os
import re
import secrets
import asyncio
import copy
import time
import base64
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StrictInt

load_dotenv()
from .engine import Engine, requested_brand  # noqa: E402
from .files import parse_file
from .model_router import ModelRouter
from .agent import handle_query
from .cart_actions import cancel_action, confirm_action, prepare_action, prepare_edit, confirm_edit
from .security import get_session, install_security, audit, redact
from .features import install_features, conversation_intent
from .i18n import language, search_phrase, localize_result, tr
from .upload_guard import validate_file, parse_isolated

app = FastAPI(title='EKT AI Engineer')
engine = Engine()
router = ModelRouter()
sessions: dict[str, dict] = {}
STATIC = Path(__file__).parent.parent / 'static'
UPLOAD_SLOTS = asyncio.Semaphore(2)
install_security(app)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({'detail': 'Проверьте формат и допустимые значения полей.'}, status_code=422)


class Query(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=2000)
    mode: Literal['best', 'cheapest', 'available', 'brand'] = 'best'
    language: Literal['ru', 'kk'] | None = None


class CartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_id: StrictInt = Field(gt=0)
    quantity: StrictInt = Field(default=1, gt=0, le=10000)
    store_id: StrictInt | None = Field(default=None, gt=0)
    confirmation_token: str | None = Field(default=None, max_length=128)


class CartLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_id: StrictInt = Field(gt=0)
    quantity: StrictInt = Field(gt=0, le=10000)
    store_id: StrictInt | None = Field(default=None, gt=0)


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[CartLine] = Field(min_length=1, max_length=20)


class BatchConfirm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation_token: str = Field(min_length=1, max_length=128)


def session(sid: str | None) -> tuple[str, dict]:
    return get_session(sessions, sid)


install_features(app, session, engine, sessions)


@app.get('/')
async def index():
    return FileResponse(STATIC / 'store.html')


@app.get('/engine')
async def original_interface():
    return FileResponse(STATIC / 'index.html')


@app.get('/store.js')
async def store_script():
    return FileResponse(STATIC / 'store.js')


@app.get('/store.css')
async def store_style():
    return FileResponse(STATIC / 'store.css')


@app.get('/style.css')
async def style():
    return FileResponse(STATIC / 'style.css')


@app.get('/app.js')
async def script():
    return FileResponse(STATIC / 'app.js')


@app.get('/upload.js')
async def upload_script():
    return FileResponse(STATIC / 'upload.js')


@app.get('/conditions.js')
async def conditions_script():
    return FileResponse(STATIC / 'conditions.js')


@app.get('/cart.js')
async def cart_script():
    return FileResponse(STATIC / 'cart.js')


@app.get('/api/status')
async def status():
    return {'catalog_items': len(engine.catalog), 'ekt_configured': bool(os.getenv('EKT_PASSWORD')), 'syncing': engine.syncing, 'last_synced': engine.last_synced, 'demo_mode': os.getenv('DEMO_MODE') == '1'}


@app.get('/api/conditions')
async def conditions():
    return {
        'source': 'https://ekt.kz/about/information/',
        'payment': 'Физлица: карта онлайн, наличные при получении, наличные или POS при самовывозе. Юрлица: перечисление по счёту или наличные в торговом зале при самовывозе.',
        'delivery': 'На официальном сайте: согласованная с менеджером доставка по Алматы в течение 48 часов; условия других городов согласовываются с менеджером. Индивидуальный ETA по товару API не даёт.',
        'minimum_order': None,
    }


@app.post('/api/sync')
async def sync(x_admin_token: str | None = Header(default=None)):
    expected = os.getenv('ADMIN_SYNC_TOKEN')
    if not expected or not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(403, 'Обновление каталога доступно администратору.')
    return {'catalog_items': await engine.sync()}


@app.on_event('startup')
async def startup():
    if os.getenv('DEMO_MODE') == '1':
        from .demo import enable_demo
        await enable_demo(engine)
    async def expire_sessions():
        while True:
            now = time.monotonic()
            for sid, state in list(sessions.items()):
                if now - state.get('_seen', now) > 1800:
                    sessions.pop(sid, None)
                elif state.get('handoff_pending', {}).get('expires', now + 1) <= now:
                    state.pop('handoff_pending', None)
            await asyncio.sleep(60)
    app.state.expiry_task = asyncio.create_task(expire_sessions())
    if os.getenv('EKT_PASSWORD') and os.getenv('DEMO_MODE') != '1':
        async def refresh_loop():
            while True:
                try:
                    await engine.sync()
                except Exception:
                    engine.syncing = False
                await asyncio.sleep(max(1, int(os.getenv('CATALOG_REFRESH_HOURS', '12'))) * 3600)
        app.state.sync_task = asyncio.create_task(refresh_loop())


@app.on_event('shutdown')
async def shutdown():
    for name in ('expiry_task', 'sync_task'):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()
    await engine.ekt.close()


@app.get('/api/catalog')
async def catalog(q: str = '', limit: int = 12):
    if len(q) > 200:
        raise HTTPException(422, 'Слишком длинный запрос.')
    if q:
        result = await engine.solution(q)
        return {'products': result['products'][:max(1, min(limit, 24))], 'verified': True}
    return {'products': engine.catalog[:max(1, min(limit, 24))], 'verified': False}


@app.get('/api/products/{product_id}')
async def product(product_id: int):
    result = await engine.solution(str(product_id))
    found = next((p for p in result['products'] if p['id'] == product_id), None)
    if not found:
        raise HTTPException(404, 'Товар не найден.')
    return found


def rerank_cached(result: dict, mode: str, brand: str | None) -> dict:
    updated = copy.deepcopy(result)
    cards = updated.get('products', [])
    if mode == 'cheapest':
        cards.sort(key=lambda p: (bool(p['warnings']), not bool(p['quantity']), p['price'] is None, p['price'] or 10**12))
    elif mode == 'available':
        cards.sort(key=lambda p: (bool(p['warnings']), -(p['quantity'] or 0)))
    elif mode == 'brand' and brand:
        cards.sort(key=lambda p: (bool(p['warnings']), brand not in p['name'].casefold(), not bool(p['quantity']), p['price'] or 10**12))
    else:
        cards.sort(key=lambda p: (bool(p['warnings']), not bool(p['quantity']), -(p.get('match_score') or 0)))
    updated['mode'] = mode
    updated['selected'] = cards[0] if cards else None
    updated['total'] = cards[0]['price'] if cards else None
    updated['route'] = 'DIRECT · verified shortlist cache'
    updated['events'] = updated.get('events', []) + ['Solution re-ranked from verified candidates']
    return updated


@app.post('/api/query')
async def query(body: Query, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    lang = language(state, body.text, body.language)
    history = state.setdefault('history', [])
    history.append({'role': 'user', 'text': redact(body.text)[:1000]})
    intent = conversation_intent(body.text, state, lang)
    if intent:
        result = {'session_id': sid, 'products': [], 'events': [], 'warnings': [], 'route': 'DIRECT', **intent}
    else:
        phrase = search_phrase(body.text) if lang == 'kk' else body.text
        if lang == 'kk':
            if re.fullmatch(r'\s*(иә|иә[, ]+қос|растау)\s*[.!]?\s*', body.text.lower()):
                phrase = 'да, добавь'
            elif 'себет' in body.text.lower() and 'қос' in body.text.lower():
                count = re.search(r'\b\d+\b', body.text)
                phrase = 'добавь ' + (count.group() + ' ' if count else '') + 'в корзину'
        if any(word in body.text.lower() for word in ('арзан', 'дорого', 'цена высокая')) and state.get('last_query'):
            phrase = 'дешевле'
        if re.search(r'сколько осталось|остаток|қанша қалды|қорда бар', body.text.lower()) and state.get('last_result', {}).get('selected'):
            phrase = str(state['last_result']['selected']['id'])
        if body.text.lower().strip() in {'отмена', 'нет', 'бас тарту', 'жоқ'}:
            for item in state.get('cart_actions', {}).values():
                if item.get('status') == 'pending':
                    item['status'] = 'cancelled'
            state.pop('pending_chat_cart', None)
            result = {'session_id': sid, 'action': 'cancelled', 'message': tr('cancelled', lang), 'products': [], 'events': [], 'warnings': []}
        else:
            result = await query_core(Query(text=phrase, mode=body.mode), sid)
        if not result.get('action'):
            cards = result.get('products', [])
            result['message'] = tr('found', lang, n=len(cards)) if cards else tr('empty', lang)
            result['handoff_available'] = not cards or any(p.get('warnings') for p in cards)
            if any(p.get('warnings') for p in cards):
                result['message'] += ' ' + tr('uncertain', lang)
    if lang == 'kk' and result.get('action') == 'cart_confirmation':
        token = state.get('pending_chat_cart', {}).get('token')
        pending = state.get('cart_actions', {}).get(token, {})
        rows = pending.get('snapshot', [])
        total = sum(p['line_total'] for p in rows)
        result['message'] = 'Себетке қосуды растаңыз:\n' + '\n'.join(f"{p['quantity']} × {p['name']} — {p['line_total']:,.2f} ₸" for p in rows) + f'\nБарлығы: {total:,.2f} ₸. «Иә, қос» деп жауап беріңіз.'
    result = localize_result(result, lang)
    history.append({'role': 'assistant', 'text': result.get('message', '')[:1500]})
    del history[:-30]
    return result


async def query_core(body: Query, x_session_id: str | None = None):
    if not body.text.strip():
        raise HTTPException(400, 'Enter a query')
    sid, state = session(x_session_id)
    original_text = body.text.strip()[:500]
    text = original_text
    mode = body.mode
    lower = text.casefold()
    if re.fullmatch(r'(?:да|подтверждаю|да[,! ]+добавь|добавляй)[.! ]*', lower):
        pending = state.pop('pending_chat_cart', None)
        if pending and time.monotonic() - pending['at'] < 120:
            try:
                confirmed = await confirm_batch(BatchConfirm(confirmation_token=pending['token']), sid)
            except HTTPException as exc:
                return {'session_id': sid, 'action': 'cart_error', 'message': str(exc.detail), 'products': [], 'events': ['Stock recheck failed'], 'warnings': [], 'route': 'DIRECT'}
            return {'session_id': sid, 'action': 'cart_added', 'message': 'Позиции добавлены в локальную корзину. Официальную корзину EKT потребуется заполнить на сайте.', 'cart': confirmed['cart'], 'official_cart_url': confirmed['official_cart_url'], 'products': [], 'events': ['Live stock rechecked', 'Local cart updated'], 'warnings': [], 'route': 'DIRECT'}
    if re.search(r'\b(?:добавь|добавить|положи)\b.*\bкорзин', lower):
        state.pop('pending_chat_cart', None)
        requested_quantity = None
        quantity_match = re.search(r'\b(?:добавь|добавить|положи)\s+(-?\d+(?:[.,]\d+)?)\b', lower)
        if quantity_match:
            quantity_text = quantity_match.group(1)
            if not quantity_text.isdigit() or not 1 <= int(quantity_text) <= 10000:
                return {'session_id': sid, 'action': 'cart_error', 'message': 'Укажите целое количество от 1 до 10000.',
                        'products': [], 'events': [], 'warnings': [], 'route': 'DIRECT'}
            requested_quantity = int(quantity_text)
        previous = state.get('last_result') or {}
        chosen_items = previous.get('solution_items')
        if chosen_items:
            lines = [CartLine(product_id=item['chosen']['id'], quantity=item['requirement']['quantity']) for item in chosen_items if item.get('chosen')]
            if requested_quantity is not None:
                if len(lines) != 1:
                    return {'session_id': sid, 'action': 'cart_error', 'message': 'Уточните количество для каждого товара комплекта.',
                            'products': [], 'events': [], 'warnings': [], 'route': 'DIRECT'}
                lines[0].quantity = requested_quantity
        else:
            selected = previous.get('selected')
            lines = [CartLine(product_id=selected['id'], quantity=requested_quantity or 1)] if selected and not selected.get('warnings') else []
        if not lines:
            return {'session_id': sid, 'action': 'cart_unavailable', 'message': 'Пока нет проверенного комплекта для корзины. Уточните характеристики и выберите подходящие товары.', 'products': [], 'events': ['Cart request checked'], 'warnings': [], 'route': 'DIRECT'}
        prepared = await prepare_batch(BatchRequest(items=lines), sid)
        state['pending_chat_cart'] = {'token': prepared['confirmation_token'], 'at': time.monotonic()}
        return {'session_id': sid, 'action': 'cart_confirmation', 'message': prepared['message'] + ' Ответьте «да, добавь» в следующем сообщении.', 'products': [], 'events': ['Live stock checked', 'Awaiting explicit confirmation'], 'warnings': [], 'route': 'DIRECT'}
    state.pop('pending_chat_cart', None)
    if state.get('last_query'):
        if 'сделай дешевле' in lower or lower in {'дешевле', 'подешевле'}:
            text, mode = state['last_query'], 'cheapest'
        elif lower in {'в наличии', 'только в наличии', 'быстрее'}:
            text, mode = state['last_query'], 'available'
        elif lower.startswith('оставь ') or lower.startswith('предпочтительный бренд '):
            brand = text.split()[-1]
            text = re.sub(r'\b(?:schneider|legrand|abb|iek|chint|dekraft|ekt)\b', '', state['last_query'], flags=re.I).strip() + ' ' + brand
            mode = 'brand'
        else:
            replacement = re.search(r'замени\s+(\w+)\s+на\s+(\w+)', text, re.I)
            if replacement:
                text = re.sub(re.escape(replacement.group(1)), replacement.group(2), state['last_query'], flags=re.I)
                mode = 'brand'
    if not engine.catalog and hasattr(app.state, 'sync_task') and not re.fullmatch(r'\s*(?:id\s*[=:]?\s*)?\d{5,7}\s*', text, re.I):
        import asyncio
        for _ in range(100):
            if engine.catalog or app.state.sync_task.done():
                break
            await asyncio.sleep(0.1)
    result = await handle_query(text, mode, engine, router)
    result['session_id'] = sid
    result['user_message'] = original_text
    state['last_query'] = text
    state['last_result'] = copy.deepcopy(result)
    state['last_verified_at'] = time.monotonic()
    return result


@app.post('/api/upload')
async def upload(file: UploadFile = File(...), allow_external: bool = Form(default=False),
                 preview_only: bool = Form(default=False), x_session_id: str | None = Header(default=None)):
    data = await file.read(10_000_001)
    await file.close()
    ext = validate_file(file.filename or '', data)
    if ext in {'.jpg', '.jpeg', '.png'} and not allow_external:
        raise HTTPException(403, 'Для фото нужно согласие на передачу изображения настроенному ИИ-провайдеру.')
    async with UPLOAD_SLOTS:
        parsed = await parse_isolated(file.filename or '', data)
    route = 'DIRECT'
    if parsed.get('image'):
        if not allow_external:
            raise HTTPException(403, 'Это скан. Нужно согласие на передачу изображения настроенному ИИ-провайдеру.')
        try:
            requirements, route = await router.read_image(base64.b64decode(parsed['image']), parsed['mime'])
        except ValueError:
            raise HTTPException(422, 'Распознавание недоступно. Настройте VISION_MODEL и ключ на сервере.') from None
    else:
        requirements = parsed['requirements']
    if not requirements:
        raise HTTPException(422, 'Товарные позиции в файле не найдены.')
    sid, state = session(x_session_id)
    if preview_only:
        token = secrets.token_urlsafe(32)
        state['upload_pending'] = {'token': token, 'expires': time.monotonic() + 120, 'route': route}
        return {'session_id': sid, 'preview': requirements[:12], 'token': token, 'first_page_only': parsed.get('first_page_only', False), 'total_rows': len(requirements)}
    return {'session_id': sid, **await solve_requirements(requirements, route)}


class SpecificationLine(BaseModel):
    description: str = Field(min_length=1, max_length=400)
    quantity: StrictInt = Field(gt=0, le=10000)


class SpecificationConfirm(BaseModel):
    token: str = Field(max_length=128)
    lines: list[SpecificationLine] = Field(min_length=1, max_length=12)


@app.post('/api/upload/confirm')
async def confirm_upload(body: SpecificationConfirm, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    pending = state.pop('upload_pending', None)
    if not pending or pending['token'] != body.token or time.monotonic() >= pending['expires']:
        raise HTTPException(400, 'Предпросмотр истёк. Загрузите файл ещё раз.')
    return {'session_id': sid, **await solve_requirements([line.model_dump() for line in body.lines], pending['route'])}


async def solve_requirements(requirements, route):
    results = []
    total = 0
    complete_total = True
    for req in requirements[:12]:
        solution = await engine.solution(req['description'])
        products = solution['products']
        chosen = next((p for p in products if not p['warnings'] and (p['quantity'] or 0) >= req['quantity']), None)
        if chosen:
            exact = str(chosen['id']) in req['description'] or bool(chosen['article'] and chosen['article'].casefold() in req['description'].casefold())
            line_status = 'exact' if exact else 'alternative'
            if chosen['price'] is None:
                complete_total = False
            else:
                total += chosen['price'] * req['quantity']
        else:
            line_status = 'clarification' if products else 'unavailable'
            complete_total = False
        results.append({'requirement': req, 'status': line_status, 'chosen': chosen, 'products': products[:3]})
    return {'requirements': len(requirements), 'results': results, 'processed': len(results), 'route': route, 'total': total if complete_total else None}


@app.post('/api/cart/prepare-batch')
async def prepare_batch(body: BatchRequest, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    result = await prepare_action(state, [item.model_dump() for item in body.items], engine.ekt.detail, kind='batch')
    return {'session_id': sid, **result}


@app.post('/api/cart/confirm-batch')
async def confirm_batch(body: BatchConfirm, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    result = await confirm_action(state, body.confirmation_token, engine.ekt.detail, kind='batch')
    return {'session_id': sid, **result}


@app.post('/api/cart/prepare')
async def prepare(body: CartRequest, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    line = body.model_dump(exclude={'confirmation_token'})
    result = await prepare_action(state, [line], engine.ekt.detail, kind='single')
    return {'session_id': sid, **result}


@app.post('/api/cart/confirm')
async def confirm(body: CartRequest, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    result = await confirm_action(state, body.confirmation_token, engine.ekt.detail,
                                  kind='single', expected=[body.model_dump(exclude={'confirmation_token'})])
    return {'session_id': sid, **result}


@app.post('/api/cart/cancel')
async def cancel(body: BatchConfirm, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    result = await cancel_action(state, body.confirmation_token)
    return {'session_id': sid, **result}


@app.get('/api/cart')
async def cart(x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    return {'session_id': sid, 'cart': state['cart'], 'cart_type': 'local_prototype'}


class CartEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    index: StrictInt = Field(ge=0)
    quantity: StrictInt = Field(ge=0, le=10000)


@app.post('/api/cart/prepare-edit')
async def cart_edit(body: CartEdit, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    return {'session_id': sid, **await prepare_edit(state, body.index, body.quantity, engine.ekt.detail)}


@app.post('/api/cart/confirm-edit')
async def cart_edit_confirm(body: BatchConfirm, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    result = await confirm_edit(state, body.confirmation_token, engine.ekt.detail)
    audit(state, 'cart_edit', 'confirmed')
    return {'session_id': sid, **result}
