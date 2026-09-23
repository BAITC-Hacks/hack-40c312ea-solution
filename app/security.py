"""Bounded anonymous sessions and defensive request handling for the MVP."""
import os
import re
import secrets
import time
from collections import deque
from urllib.parse import urlsplit

from fastapi import HTTPException
from fastapi.responses import JSONResponse

SESSION_TTL = 1800
MAX_SESSIONS = 1000


def get_session(sessions, sid):
    now = time.monotonic()
    for key, state in list(sessions.items()):
        if now - state.get('_seen', now) > SESSION_TTL:
            del sessions[key]
    if not sid or sid not in sessions:
        if len(sessions) >= MAX_SESSIONS:
            raise HTTPException(503, 'Сервис занят. Попробуйте позже.')
        sid = secrets.token_hex(16)
        sessions[sid] = {'cart': [], 'history': [], 'language': 'ru', 'language_auto': True}
    state = sessions[sid]
    state['_seen'] = now
    return sid, state


def redact(text):
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', '[email]', str(text))
    text = re.sub(r'(?<!\w)(?:\+?7|8)[ ()-]*\d{3}[ ()-]*\d{3}[ ()-]*\d{2}[ ()-]*\d{2}(?!\d)', '[телефон]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{10,}|\d{12,19})\b', '[закрытые данные]', text)
    return text


def audit(state, action, status):
    events = state.setdefault('audit', [])
    events.append({'action': action, 'status': status, 'at': int(time.time())})
    del events[:-50]


def install_security(app):
    buckets = {}

    @app.middleware('http')
    async def boundary(request, call_next):
        now = time.monotonic()
        path = request.url.path
        for key in list(buckets):
            if not buckets[key] or now - buckets[key][-1] > 60:
                del buckets[key]
        if path.startswith('/api/'):
            ip = request.client.host if request.client else 'unknown'
            kind = 'upload' if path == '/api/upload' else 'api'
            bucket = buckets.setdefault((ip, kind), deque())
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            limit = 10 if kind == 'upload' else int(os.getenv('RATE_LIMIT_PER_MINUTE', '180'))
            if len(bucket) >= limit or len(buckets) > 4000:
                return JSONResponse({'detail': 'Слишком много запросов. Подождите минуту.'}, status_code=429, headers={'Retry-After': '60'})
            bucket.append(now)
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                origin = request.headers.get('origin')
                if origin and origin != str(request.base_url).rstrip('/'):
                    return JSONResponse({'detail': 'Недопустимый источник запроса.'}, status_code=403)
                try:
                    size = int(request.headers.get('content-length', '0'))
                except ValueError:
                    return JSONResponse({'detail': 'Некорректный запрос.'}, status_code=400)
                if size > 10_100_000:
                    return JSONResponse({'detail': 'Файл превышает 10 МБ.'}, status_code=413)
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse({'detail': 'Не удалось обработать запрос. Попробуйте ещё раз или свяжитесь с менеджером.'}, status_code=500)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        embedded = path == '/' and request.query_params.get('embed') == '1'
        allowed = []
        for entry in os.getenv('EMBED_ALLOWED_ORIGINS', 'https://ekt.kz,https://www.ekt.kz').split(','):
            parsed = urlsplit(entry.strip())
            if parsed.scheme == 'https' and parsed.netloc and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path in {'', '/'}:
                allowed.append(f'{parsed.scheme}://{parsed.netloc}')
        ancestors = "'self' " + ' '.join(allowed) if embedded else "'none'"
        if not embedded:
            response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https://ekt.kz https://*.ekt.kz blob: data:; connect-src 'self'; object-src 'none'; frame-ancestors " + ancestors + "; base-uri 'self'; form-action 'self'"
        if path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        if request.url.scheme == 'https':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response


def safe_product_url(value):
    try:
        parsed = urlsplit(value or '')
        if parsed.scheme == 'https' and (parsed.hostname == 'ekt.kz' or (parsed.hostname or '').endswith('.ekt.kz')):
            return value
    except ValueError:
        pass
    return None
