import os
import re
import secrets
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()
from .engine import Engine  # noqa: E402
from .files import parse_file
from .model_router import ModelRouter

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
    return {'catalog_items': len(engine.catalog), 'ekt_configured': bool(os.getenv('EKT_PASSWORD'))}


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
    if os.getenv('EKT_PASSWORD') and not engine.catalog:
        import asyncio
        app.state.sync_task = asyncio.create_task(engine.sync())


@app.post('/api/query')
async def query(body: Query):
    if not body.text.strip():
        raise HTTPException(400, 'Enter a query')
    if not engine.catalog and hasattr(app.state, 'sync_task') and not re.fullmatch(r'\s*(?:id\s*[=:]?\s*)?\d{5,7}\s*', body.text, re.I):
        import asyncio
        for _ in range(100):
            if engine.catalog or app.state.sync_task.done():
                break
            await asyncio.sleep(0.1)
    search_text, route = await router.normalize(body.text.strip()[:500])
    result = await engine.solution(search_text, body.mode)
    result['query'] = body.text.strip()[:500]
    result['route'] = route
    return result


@app.post('/api/upload')
async def upload(file: UploadFile = File(...)):
    data = await file.read(10_000_001)
    if len(data) > 10_000_000:
        raise HTTPException(413, 'File exceeds 10 MB')
    try:
        if (file.filename or '').lower().endswith(('.jpg', '.jpeg', '.png')):
            lines, route = await router.read_image(data, file.content_type or 'image/jpeg')
            requirements = [{'description': line, 'quantity': 1} for line in lines]
        else:
            requirements = parse_file(file.filename or '', data)
            route = 'DIRECT'
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
