import os
import re
import secrets
import asyncio
import copy
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()
from .engine import Engine, requested_brand  # noqa: E402
from .files import parse_file
from .model_router import ModelRouter
from .agent import handle_query

app = FastAPI(title='EKT AI Engineer')
engine = Engine()
router = ModelRouter()
sessions: dict[str, dict] = {}
STATIC = Path(__file__).parent.parent / 'static'


class Query(BaseModel):
    text: str
    mode: str = 'best'


class CartRequest(BaseModel):
    product_id: int
    quantity: int = 1
    confirmation_token: str | None = None


class CartLine(BaseModel):
    product_id: int
    quantity: int


class BatchRequest(BaseModel):
    items: list[CartLine]


class BatchConfirm(BaseModel):
    confirmation_token: str


def session(sid: str | None) -> tuple[str, dict]:
    sid = sid if sid and re.fullmatch(r'[a-f0-9]{32}', sid) else secrets.token_hex(16)
    return sid, sessions.setdefault(sid, {'cart': [], 'pending': {}, 'pending_batches': {}})


@app.get('/')
async def index():
    return FileResponse(STATIC / 'index.html')


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
    return {'catalog_items': len(engine.catalog), 'ekt_configured': bool(os.getenv('EKT_PASSWORD')), 'syncing': engine.syncing, 'last_synced': engine.last_synced, 'model_usage': router.ledger}


@app.get('/api/conditions')
async def conditions():
    return {
        'source': 'https://ekt.kz/about/information/',
        'payment': 'Физлица: карта онлайн, наличные при получении, наличные или POS при самовывозе. Юрлица: перечисление по счёту или наличные в торговом зале при самовывозе.',
        'delivery': 'На официальном сайте: согласованная с менеджером доставка по Алматы в течение 48 часов; условия других городов согласовываются с менеджером. Индивидуальный ETA по товару API не даёт.',
        'minimum_order': None,
    }


@app.post('/api/sync')
async def sync():
    return {'catalog_items': await engine.sync()}


@app.on_event('startup')
async def startup():
    if os.getenv('EKT_PASSWORD'):
        async def refresh_loop():
            while True:
                try:
                    await engine.sync()
                except Exception:
                    engine.syncing = False
                await asyncio.sleep(max(1, int(os.getenv('CATALOG_REFRESH_HOURS', '12'))) * 3600)
        app.state.sync_task = asyncio.create_task(refresh_loop())


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
        previous = state.get('last_result') or {}
        chosen_items = previous.get('solution_items')
        if chosen_items:
            lines = [CartLine(product_id=item['chosen']['id'], quantity=item['requirement']['quantity']) for item in chosen_items if item.get('chosen')]
        else:
            selected = previous.get('selected')
            lines = [CartLine(product_id=selected['id'], quantity=1)] if selected and not selected.get('warnings') else []
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
    if state.get('last_query') == text and state.get('last_result') and time.monotonic() - state.get('last_verified_at', 0) < 120 and not state['last_result'].get('solution_items'):
        result = rerank_cached(state['last_result'], mode, requested_brand(text))
    else:
        result = await handle_query(text, mode, engine, router)
    result['session_id'] = sid
    result['user_message'] = original_text
    state['last_query'] = text
    state['last_result'] = copy.deepcopy(result)
    state['last_verified_at'] = time.monotonic()
    return result


@app.post('/api/upload')
async def upload(file: UploadFile = File(...)):
    data = await file.read(10_000_001)
    if len(data) > 10_000_000:
        raise HTTPException(413, 'File exceeds 10 MB')
    try:
        if (file.filename or '').lower().endswith(('.jpg', '.jpeg', '.png')):
            requirements, route = await router.read_image(data, file.content_type or 'image/jpeg')
        else:
            try:
                requirements = parse_file(file.filename or '', data)
                route = 'DIRECT'
            except ValueError as exc:
                if not (file.filename or '').lower().endswith('.pdf') or 'no text layer' not in str(exc):
                    raise
                import pymupdf
                document = pymupdf.open(stream=data, filetype='pdf')
                if not document.page_count:
                    raise ValueError('Empty PDF') from exc
                png = document[0].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).tobytes('png')
                requirements, route = await router.read_image(png, 'image/png')
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
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
    if not 1 <= len(body.items) <= 20 or any(item.quantity < 1 for item in body.items):
        raise HTTPException(400, 'Invalid items')
    requested = {}
    for item in body.items:
        requested[item.product_id] = requested.get(item.product_id, 0) + item.quantity
    details = await asyncio.gather(*(engine.ekt.detail(product_id) for product_id in requested))
    for detail in details:
        already = sum(item['quantity'] for item in state['cart'] if item['id'] == detail['id'])
        if (detail.get('quantity') or 0) < requested[detail['id']] + already:
            raise HTTPException(409, f"Insufficient stock for {detail['id']}")
    token = secrets.token_urlsafe(20)
    state['pending_batches'][token] = requested
    return {'session_id': sid, 'confirmation_token': token, 'message': f'Подтвердите добавление {len(requested)} позиций в локальную корзину.'}


@app.post('/api/cart/confirm-batch')
async def confirm_batch(body: BatchConfirm, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    requested = state['pending_batches'].pop(body.confirmation_token, None)
    if not requested:
        raise HTTPException(400, 'Confirmation missing or expired')
    details = await asyncio.gather(*(engine.ekt.detail(product_id) for product_id in requested))
    for detail in details:
        already = sum(item['quantity'] for item in state['cart'] if item['id'] == detail['id'])
        if (detail.get('quantity') or 0) < requested[detail['id']] + already:
            raise HTTPException(409, f"Stock changed for {detail['id']}")
    state['cart'].extend({'id': detail['id'], 'name': detail.get('name'), 'quantity': requested[detail['id']], 'price': detail.get('price')} for detail in details)
    return {'session_id': sid, 'cart': state['cart'], 'cart_type': 'local_prototype', 'official_cart_url': 'https://ekt.kz/personal/cart/'}


@app.post('/api/cart/prepare')
async def prepare(body: CartRequest, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    if body.quantity < 1:
        raise HTTPException(400, 'Quantity must be positive')
    product = await engine.ekt.detail(body.product_id)
    available = product.get('quantity')
    if not isinstance(available, int) or available < body.quantity:
        raise HTTPException(409, 'Insufficient verified stock')
    token = secrets.token_urlsafe(20)
    state['pending'][token] = {'id': body.product_id, 'quantity': body.quantity, 'name': product.get('name')}
    return {'session_id': sid, 'confirmation_token': token, 'message': f"Подтвердите добавление {body.quantity} × {product.get('name')} в локальную корзину."}


@app.post('/api/cart/confirm')
async def confirm(body: CartRequest, x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    pending = state['pending'].pop(body.confirmation_token, None)
    if not pending or pending['id'] != body.product_id or pending['quantity'] != body.quantity:
        raise HTTPException(400, 'Confirmation missing or expired')
    product = await engine.ekt.detail(body.product_id)
    already = sum(item['quantity'] for item in state['cart'] if item['id'] == body.product_id)
    if (product.get('quantity') or 0) < body.quantity + already:
        raise HTTPException(409, 'Stock changed')
    state['cart'].append({'id': body.product_id, 'name': product.get('name'), 'quantity': body.quantity, 'price': product.get('price')})
    return {'session_id': sid, 'cart': state['cart'], 'cart_type': 'local_prototype', 'official_cart_url': 'https://ekt.kz/personal/cart/'}


@app.get('/api/cart')
async def cart(x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    return {'session_id': sid, 'cart': state['cart'], 'cart_type': 'local_prototype'}

