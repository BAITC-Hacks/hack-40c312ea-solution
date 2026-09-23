"""Optional storefront features. All writes remain behind explicit confirmation."""
import copy
import re
import secrets
import time
from typing import Literal

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .i18n import language, localize_result, tr
from .security import audit, redact


class LanguageChoice(BaseModel):
    language: Literal['ru', 'kk']


class HandoffDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='', max_length=100)
    contact: str = Field(min_length=3, max_length=150)
    channel: Literal['phone', 'email', 'whatsapp'] = 'phone'
    reason: str = Field(default='', max_length=500)
    consent: StrictBool = False
    share_history: StrictBool = False
    share_products: StrictBool = False


class Confirmation(BaseModel):
    token: str = Field(min_length=10, max_length=128)
    consent: StrictBool = False


class RecommendationPreference(BaseModel):
    enabled: StrictBool


def category(product):
    name = product.get('name', '').lower()
    for group, terms in [('enclosure', ('щит', 'шкаф', 'қалқан')), ('rail', ('din', 'дин-рейк')),
                         ('busbar', ('шина ', 'шинк', 'n/pe')), ('gland', ('кабельный ввод', 'сальник')),
                         ('label', ('маркиров', 'маркер')), ('cable', ('кабель ', 'провод ')),
                         ('terminal', ('клемм', 'наконечник')), ('breaker', ('автомат', 'ав drx', 'диф.авт'))]:
        if any(term in name for term in terms):
            return group
    return None


async def recommendations(engine, product_id, state, lang):
    if state.get('recommendations_enabled') is False or state.get('purchase_declined'):
        return []
    source = await engine.ekt.detail(product_id)
    rules = {'enclosure': {'rail', 'busbar', 'gland', 'label'}, 'cable': {'terminal', 'label', 'gland'},
             'breaker': {'enclosure', 'label', 'rail'}}
    allowed = rules.get(category(source), set())
    if not allowed:
        return []
    recommended = (source.get('properties') or {}).get('RECOMMEND') or []
    if not isinstance(recommended, list):
        recommended = [recommended]
    ids = [int(x) for x in recommended if str(x).isdigit()][:10]
    ids += [p['id'] for p in engine.catalog if category(p) in allowed][:20]
    excluded = {product_id} | {p['id'] for p in state['cart']}
    output = []
    for pid in dict.fromkeys(ids):
        if pid in excluded:
            continue
        try:
            result = await engine.solution(str(pid))
            p = next((p for p in result['products'] if p['id'] == pid), None)
            if not p or category(p) not in allowed or not p.get('quantity') or p.get('warnings'):
                continue
            p['reason'] = ('Дополнение по назначению. Проверьте размеры и состав комплекта; совместимость не подтверждена.'
                           if lang == 'ru' else 'Мақсаты бойынша қосымша бұйым. Өлшемі мен жинақтамасын тексеріңіз; үйлесімділік расталмаған.')
            p['recommendation_source'] = 'catalog' if str(pid) in [str(x) for x in recommended] else 'category_rule'
            output.append(p)
            if len(output) == 3:
                break
        except Exception:
            continue
    return output


def conversation_intent(text, state, lang):
    lower = text.lower().strip()
    if any(word in lower for word in ('не буду покупать', 'не хочу покупать', 'не надо', 'не нужно', 'отказываюсь', 'сатып алмаймын', 'керек емес')):
        state['purchase_declined'] = True
        state['recommendations_enabled'] = False
        state.pop('pending_chat_cart', None)
        for action in state.get('cart_actions', {}).values():
            if action.get('status') == 'pending':
                action['status'] = 'cancelled'
        return {'action': 'declined', 'message': tr('declined', lang)}
    if any(word in lower for word in ('менеджер', 'оператор', 'человек', 'маман')):
        return {'action': 'manager', 'message': tr('manager', lang), 'handoff_available': True}
    if any(word in lower for word in ('api ключ', 'api-ключ', 'пароль', 'данные клиентов', 'секрет', 'номер карты', 'құпия', 'cvv')):
        return {'action': 'private', 'message': tr('private', lang), 'handoff_available': True}
    if any(word in lower for word in ('скидк', 'оптовая цена', 'жеке жеңілдік')):
        return {'action': 'manager', 'message': tr('manager', lang), 'handoff_available': True}
    if any(word in lower for word in ('доставк', 'жеткізу')):
        return {'action': 'delivery', 'message': tr('delivery', lang), 'handoff_available': True}
    if any(word in lower for word in ('оплат', 'минимальная партия', 'төлем')):
        return {'action': 'payment', 'message': tr('payment', lang)}
    if any(word in lower for word in ('сомневаюсь', 'не уверен', 'подумаю', 'күмән', 'ойланам')):
        if state.get('purchase_declined') or state.get('doubt_helped'):
            return {'action': 'pause', 'message': tr('pause', lang)}
        state['doubt_helped'] = True
        return {'action': 'doubt', 'message': tr('doubt', lang)}
    if any(word in lower for word in ('подойдет ли', 'совместим', 'үйлесім')) and state.get('last_result'):
        return {'action': 'compatibility', 'message': tr('compatibility', lang), 'handoff_available': True}
    return None


def install_features(app, get_session, engine, sessions):
    @app.get('/api/session')
    async def read_session(x_session_id: str | None = Header(default=None)):
        sid, state = get_session(x_session_id)
        return {'session_id': sid, 'language': state.get('language', 'ru'), 'history': state.get('history', []),
                'recommendations_enabled': state.get('recommendations_enabled', True), 'expires_in_seconds': 1800}

    @app.post('/api/session/language')
    async def set_language(body: LanguageChoice, x_session_id: str | None = Header(default=None)):
        sid, state = get_session(x_session_id)
        return {'session_id': sid, 'language': language(state, explicit=body.language)}

    @app.post('/api/session/recommendations')
    async def preferences(body: RecommendationPreference, x_session_id: str | None = Header(default=None)):
        sid, state = get_session(x_session_id)
        state['recommendations_enabled'] = body.enabled
        if body.enabled:
            state['purchase_declined'] = False
        return {'session_id': sid, 'enabled': body.enabled}

    @app.get('/api/recommendations/{product_id}')
    async def get_recommendations(product_id: int, x_session_id: str | None = Header(default=None)):
        sid, state = get_session(x_session_id)
        lang = state.get('language', 'ru')
        products = await recommendations(engine, product_id, state, lang)
        return {'session_id': sid, 'products': products, 'message': tr('cross' if products else 'no_cross', lang)}

    @app.post('/api/handoff/prepare')
    async def prepare_handoff(body: HandoffDraft, x_session_id: str | None = Header(default=None)):
        if not body.consent:
            raise HTTPException(403, 'Нужно согласие на обработку контакта и заявки.')
        if body.channel == 'email':
            valid = re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', body.contact)
        else:
            valid = re.fullmatch(r'\+?[\d ()-]{7,25}', body.contact)
        if not valid:
            raise HTTPException(422, 'Проверьте контакт для выбранного способа связи.')
        sid, state = get_session(x_session_id)
        payload = body.model_dump(exclude={'consent'})
        payload['reason'] = redact(payload['reason'])
        payload['history'] = copy.deepcopy(state.get('history', [])[-12:]) if body.share_history else []
        payload['products'] = [{'id': p['id'], 'name': p['name']} for p in (state.get('last_result') or {}).get('products', [])[:3]] if body.share_products else []
        token = secrets.token_urlsafe(32)
        state['handoff_pending'] = {'token': token, 'payload': payload, 'expires': time.monotonic() + 120}
        audit(state, 'handoff_prepare', 'awaiting_confirmation')
        return {'session_id': sid, 'token': token, 'preview': payload, 'mode': 'demo', 'expires_in_seconds': 120}

    @app.post('/api/handoff/confirm')
    async def confirm_handoff(body: Confirmation, x_session_id: str | None = Header(default=None)):
        sid, state = get_session(x_session_id)
        pending = state.get('handoff_pending')
        if not body.consent or not pending or pending['token'] != body.token or time.monotonic() >= pending['expires']:
            raise HTTPException(403, 'Нет действующего согласия на передачу.')
        if 'request_id' not in pending:
            request_id = secrets.token_hex(8)
            pending['request_id'] = request_id
            state['handoff'] = {'id': request_id, 'status': 'demo_saved', 'mode': 'demo', **pending['payload']}
            audit(state, 'handoff_confirm', 'demo_saved')
        return {'session_id': sid, 'id': pending['request_id'], 'status': 'demo_saved', 'mode': 'demo',
                'message': 'Демонстрационная заявка сохранена в этой сессии. Менеджеру она ещё не отправлена.' if state.get('language') != 'kk' else 'Демонстрациялық өтінім осы сессияда сақталды. Менеджерге әлі жіберілген жоқ.'}

    @app.get('/api/handoff')
    async def handoff_status(x_session_id: str | None = Header(default=None)):
        sid, state = get_session(x_session_id)
        item = state.get('handoff')
        return {'session_id': sid, 'request': {k: item[k] for k in ('id', 'status', 'mode')} if item else None}

    @app.post('/api/handoff/cancel')
    async def cancel_handoff(x_session_id: str | None = Header(default=None)):
        sid, state = get_session(x_session_id)
        state.pop('handoff_pending', None)
        state.pop('handoff', None)
        audit(state, 'handoff_cancel', 'deleted')
        return {'session_id': sid, 'cancelled': True}

    @app.get('/api/privacy')
    async def privacy():
        return {'retention_seconds': 1800, 'raw_files_stored': False, 'handoff_mode': 'demo',
                'external_processing': 'Текст запросов может отправляться настроенному ИИ-провайдеру. Фото и сканы — только с отдельного согласия.',
                'notice_kk': 'Сұрау мәтіні бапталған AI провайдеріне жіберілуі мүмкін. Фото мен скандар үшін бөлек келісім қажет.'}

    @app.post('/api/session/clear')
    async def clear_session(x_session_id: str | None = Header(default=None)):
        sid, state = get_session(x_session_id)
        async with state.setdefault('cart_lock', __import__('asyncio').Lock()):
            sessions.pop(sid, None)
            sid, _ = get_session(None)
        return {'session_id': sid, 'cleared': True}

