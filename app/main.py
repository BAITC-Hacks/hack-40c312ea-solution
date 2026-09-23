import os
import re
import secrets
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()
from .engine import Engine  # noqa: E402
from .files import parse_file

app = FastAPI(title='EKT AI Engineer')
engine = Engine()
sessions: dict[str, dict] = {}
STATIC = Path(__file__).parent.parent / 'static'


class Query(BaseModel):
    text: str
    mode: str = 'best'


class CartRequest(BaseModel):
    product_id: int
    quantity: int = 1
    confirmation_token: str | None = None


def session(sid: str | None) -> tuple[str, dict]:
    sid = sid if sid and re.fullmatch(r'[a-f0-9]{32}', sid) else secrets.token_hex(16)
    return sid, sessions.setdefault(sid, {'cart': [], 'pending': {}})


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


@app.get('/api/status')
async def status():
    return {'catalog_items': len(engine.catalog), 'ekt_configured': bool(os.getenv('EKT_PASSWORD'))}


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
    if not engine.catalog and hasattr(app.state, 'sync_task'):
        await app.state.sync_task
    return await engine.solution(body.text.strip()[:500], body.mode)


@app.post('/api/upload')
async def upload(file: UploadFile = File(...)):
    data = await file.read(10_000_001)
    if len(data) > 10_000_000:
        raise HTTPException(413, 'File exceeds 10 MB')
    try:
        requirements = parse_file(file.filename or '', data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    results = []
    for req in requirements[:12]:
        solution = await engine.solution(req['description'])
        results.append({'requirement': req, 'status': 'candidate' if solution['products'] else 'unavailable', 'products': solution['products'][:3]})
    return {'requirements': len(requirements), 'results': results, 'processed': len(results)}


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
    if (product.get('quantity') or 0) < body.quantity:
        raise HTTPException(409, 'Stock changed')
    state['cart'].append({'id': body.product_id, 'name': product.get('name'), 'quantity': body.quantity, 'price': product.get('price')})
    return {'session_id': sid, 'cart': state['cart'], 'cart_type': 'local_prototype', 'official_cart_url': 'https://ekt.kz/personal/cart/'}


@app.get('/api/cart')
async def cart(x_session_id: str | None = Header(default=None)):
    sid, state = session(x_session_id)
    return {'session_id': sid, 'cart': state['cart'], 'cart_type': 'local_prototype'}
