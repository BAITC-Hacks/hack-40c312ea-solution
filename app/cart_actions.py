"""Server-owned, expiring confirmations for the session-local demo cart."""

import asyncio
import copy
import secrets
from decimal import Decimal, InvalidOperation
from time import monotonic

from fastapi import HTTPException
from .cart_provider import cart_provider

TTL_SECONDS = 120
MAX_ACTIONS = 32
MAX_QUANTITY = 10000


def _number(value, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise HTTPException(409, f'{label}: API не предоставил корректное значение.') from None
    if not number.is_finite() or number < 0:
        raise HTTPException(409, f'{label}: API не предоставил корректное значение.')
    return number


def _lock(state: dict) -> asyncio.Lock:
    return state.setdefault('cart_lock', asyncio.Lock())


def _actions(state: dict) -> dict:
    actions = state.setdefault('cart_actions', {})
    now = monotonic()
    for token, action in list(actions.items()):
        if now >= action['expires_at']:
            del actions[token]
    return actions


def _normalize(lines: list[dict]) -> list[dict]:
    if not 1 <= len(lines) <= 20:
        raise HTTPException(400, 'Нужно выбрать от 1 до 20 позиций.')
    grouped = {}
    for line in lines:
        pid, quantity, store = line['product_id'], line['quantity'], line.get('store_id')
        if type(pid) is not int or pid < 1 or type(quantity) is not int or quantity < 1:
            raise HTTPException(400, 'Некорректный товар или количество.')
        if store is not None and (type(store) is not int or store < 1):
            raise HTTPException(400, 'Некорректный склад.')
        key = (pid, store)
        grouped[key] = grouped.get(key, 0) + quantity
        if grouped[key] > MAX_QUANTITY:
            raise HTTPException(400, 'Слишком большое количество товара.')
    return [{'product_id': pid, 'store_id': store, 'quantity': qty}
            for (pid, store), qty in grouped.items()]


async def _snapshot(lines: list[dict], state: dict, detail) -> list[dict]:
    ids = list(dict.fromkeys(line['product_id'] for line in lines))
    try:
        records = await asyncio.gather(*(detail(pid) for pid in ids))
    except Exception:
        raise HTTPException(502, 'Не удалось проверить каталог. Попробуйте ещё раз.') from None
    products = {}
    for pid, record in zip(ids, records):
        if not isinstance(record, dict) or record.get('id') != pid:
            raise HTTPException(502, 'Каталог вернул данные другого товара.')
        products[pid] = record
    result = []
    for line in lines:
        pid, qty, store_id = line['product_id'], line['quantity'], line['store_id']
        product = products[pid]
        from .engine import conflict_warnings
        if conflict_warnings(product):
            raise HTTPException(409, 'Характеристики противоречат друг другу. Обратитесь к менеджеру.')
        price = _number(product.get('price'), 'Цена')
        available = _number(product.get('quantity'), 'Остаток')
        already = sum(item['quantity'] for item in state['cart'] if item['id'] == pid)
        requested = sum(item['quantity'] for item in lines if item['product_id'] == pid)
        if already + requested > available:
            raise HTTPException(409, f'Недостаточно товара {pid} с учётом уже добавленного в корзину.')
        properties = product.get('properties') or {}
        step = _number(properties.get('KRATNOST_MIN', 1), 'Кратность покупки')
        if step <= 0 or Decimal(qty) % step:
            raise HTTPException(409, f'Количество товара {pid} должно быть кратно {step}.')
        store_name = 'Суммарный остаток по складам; склад не выбран'
        if store_id is not None:
            store = next((s for s in product.get('stores', []) if s.get('id') == store_id), None)
            if not store:
                raise HTTPException(409, f'Склад для товара {pid} не найден.')
            store_stock = _number(store.get('quantity'), 'Остаток на складе')
            store_already = sum(item['quantity'] for item in state['cart']
                                if item['id'] == pid and item.get('store_id') == store_id)
            if store_already + qty > store_stock:
                raise HTTPException(409, f'Недостаточно товара {pid} на выбранном складе.')
            store_name = str(store.get('name') or store_id)
        result.append({'id': pid, 'name': str(product.get('name') or pid),
                       'quantity': qty, 'price': float(price),
                       'line_total': float(price * qty), 'store_id': store_id,
                       'store_name': store_name, 'purchase_multiple': str(step)})
    return result


def _receipt(state: dict) -> dict:
    return cart_provider.receipt(state)


async def prepare_action(state: dict, lines: list[dict], detail, *, kind: str) -> dict:
    lines = _normalize(lines)
    async with _lock(state):
        actions = _actions(state)
        if len(actions) >= MAX_ACTIONS:
            raise HTTPException(429, 'Слишком много подтверждений. Отмените старые или подождите две минуты.')
        snapshot = await _snapshot(lines, state, detail)
        token = secrets.token_urlsafe(32)
        total = sum(Decimal(str(item['price'])) * item['quantity'] for item in snapshot)
        actions[token] = {'kind': kind, 'lines': lines, 'snapshot': snapshot,
                          'expires_at': monotonic() + TTL_SECONDS, 'status': 'pending'}
        summary = '\n'.join(f"{item['quantity']} × {item['name']} — {item['price']:,.2f} ₸/шт.; "
                            f"{item['line_total']:,.2f} ₸. {item['store_name']}" for item in snapshot)
        return {'confirmation_token': token, 'items': copy.deepcopy(snapshot),
                'total': float(total), 'expires_in_seconds': TTL_SECONDS,
                'message': f'Добавить в локальную корзину?\n{summary}\nИтого: {total:,.2f} ₸. '
                           'Подтверждение действует 2 минуты. Товар не резервируется.'}


async def confirm_action(state: dict, token: str | None, detail, *, kind: str,
                         expected: list[dict] | None = None) -> dict:
    async with _lock(state):
        action = _actions(state).get(token)
        if not action or action['kind'] != kind:
            raise HTTPException(400, 'Подтверждение отсутствует или истекло.')
        if expected is not None and action['lines'] != _normalize(expected):
            raise HTTPException(400, 'Товар, количество или склад не совпадают с подтверждением.')
        if action['status'] == 'completed':
            raise HTTPException(400, 'Подтверждение уже использовано. Корзина не изменена.')
        if action['status'] != 'pending':
            raise HTTPException(409, 'Действие отменено. Подготовьте новое подтверждение.')
        snapshot = await _snapshot(action['lines'], state, detail)
        if monotonic() >= action['expires_at']:
            action['status'] = 'expired'
            raise HTTPException(409, 'Подтверждение истекло во время проверки. Подготовьте новое.')
        if snapshot != action['snapshot']:
            action['status'] = 'invalidated'
            raise HTTPException(409, 'Цена или условия товара изменились. Проверьте новые данные и подтвердите заново.')
        cart_provider.add(state, snapshot)
        action['status'] = 'completed'
        return _receipt(state)


async def cancel_action(state: dict, token: str) -> dict:
    async with _lock(state):
        action = _actions(state).get(token)
        if not action:
            raise HTTPException(400, 'Подтверждение отсутствует или истекло.')
        if action['status'] == 'completed':
            raise HTTPException(409, 'Действие уже выполнено; отмена не удаляет товар из корзины.')
        action['status'] = 'cancelled'
        return {'cancelled': True}


async def prepare_edit(state, index, quantity, detail):
    async with _lock(state):
        if index < 0 or index >= len(state['cart']):
            raise HTTPException(404, 'Позиция не найдена.')
        actions = _actions(state)
        if len(actions) >= MAX_ACTIONS:
            raise HTTPException(429, 'Слишком много подтверждений.')
        before = copy.deepcopy(state['cart'])
        old = before[index]
        other = {'cart': before[:index] + before[index + 1:]}
        lines = _normalize([{'product_id': old['id'], 'quantity': quantity, 'store_id': old.get('store_id')}]) if quantity else []
        snapshot = await _snapshot(lines, other, detail) if lines else []
        token = secrets.token_urlsafe(32)
        actions[token] = {'kind': 'edit', 'status': 'pending', 'expires_at': monotonic() + TTL_SECONDS,
                          'before': before, 'index': index, 'lines': lines, 'snapshot': snapshot}
        return {'confirmation_token': token, 'items': snapshot, 'previous': old, 'remove': not quantity,
                'expires_in_seconds': TTL_SECONDS, 'total': snapshot[0]['line_total'] if snapshot else 0}


async def confirm_edit(state, token, detail):
    async with _lock(state):
        action = _actions(state).get(token)
        if not action or action['kind'] != 'edit' or action['status'] != 'pending':
            raise HTTPException(400, 'Подтверждение недействительно.')
        if state['cart'] != action['before']:
            raise HTTPException(409, 'Корзина изменилась. Подтвердите заново.')
        i = action['index']
        other = {'cart': state['cart'][:i] + state['cart'][i + 1:]}
        snapshot = await _snapshot(action['lines'], other, detail) if action['lines'] else []
        if snapshot != action['snapshot'] or monotonic() >= action['expires_at']:
            raise HTTPException(409, 'Условия изменились или подтверждение истекло.')
        cart_provider.replace(state, i, snapshot)
        action['status'] = 'completed'
        return _receipt(state)
