"""Bounded OAuth requests, resource binding, safe failures and response headers."""
from collections import defaultdict, deque
import time
from urllib.parse import parse_qsl

from starlette.requests import Request
from starlette.responses import JSONResponse

PATHS = {"/authorize", "/token", "/register", "/revoke", "/oauth/start", "/oauth/consent",
         "/.well-known/oauth-authorization-server"}


class OAuthBoundary:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.requests = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] not in PATHS:
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        async def secured(message):
            if message['type'] == 'http.response.start':
                blocked = {b'cache-control', b'pragma', b'referrer-policy', b'x-content-type-options'}
                message['headers'] = [(k, v) for k, v in message.get('headers', []) if k.lower() not in blocked] + [
                    (b'cache-control', b'no-store'), (b'pragma', b'no-cache'),
                    (b'referrer-policy', b'no-referrer'), (b'x-content-type-options', b'nosniff')]
            await send(message)
        async def fail(status, error):
            await JSONResponse({'error': error}, status_code=status)(scope, receive, secured)
        # Each bounded instance has a conservative global limiter. It never stores IPs.
        if request.method != 'OPTIONS':
            queue = self.requests[scope['path']]
            now = time.monotonic()
            while queue and queue[0] < now - 60:
                queue.popleft()
            if len(queue) >= (20 if scope['path'] == '/register' else 120):
                return await fail(429, 'temporarily_unavailable')
            queue.append(now)
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 32_768:
                    return await fail(413, 'invalid_request')
            params = list(request.query_params.multi_items())
            if request.method == 'POST' and scope['path'] in {'/authorize', '/token', '/revoke'}:
                if request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded':
                    return await fail(400, 'invalid_request')
                params += parse_qsl(body.decode(), keep_blank_values=True)
            names = [key for key, _ in params]
            if len(names) != len(set(names)):
                return await fail(400, 'invalid_request')
            if dict(params).get('resource', self.settings.public_url) != self.settings.public_url:
                return await fail(400, 'invalid_target')
            if len(request.headers.getlist('authorization')) > 1:
                return await fail(400, 'invalid_request')
        except (ValueError, UnicodeError):
            return await fail(400, 'invalid_request')
        received = False
        async def replay():
            nonlocal received
            if not received:
                received = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        started = False
        async def safe_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await secured(message)
        try:
            await self.app(scope, replay, safe_send)
        except Exception:
            if started:
                raise
            # Never emit credentials/provider or storage exception bodies.
            await fail(503, 'temporarily_unavailable')
