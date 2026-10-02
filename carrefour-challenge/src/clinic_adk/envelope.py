"""Bound the API envelope before FastAPI parses or reflects any submitted JSON."""
import asyncio
import json
from starlette.responses import JSONResponse

class BoundedJSON:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope['path'] != '/appointments':
            return await self.app(scope, receive, send)
        async def reject(status, code):
            await JSONResponse({'code': code}, status_code=status)(scope, receive, send)
        raw = bytearray()
        try:
            async with asyncio.timeout(3):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    raw.extend(message.get('body', b''))
                    if len(raw) > 4096:
                        return await reject(413, 'REQUEST_SIZE_LIMIT')
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            return await reject(408, 'REQUEST_BODY_TIMEOUT')
        headers = dict(scope.get('headers', []))
        media_type=headers.get(b'content-type', b'').split(b';',1)[0].strip().lower()
        if media_type != b'application/json':
            return await reject(415, 'JSON_REQUIRED')
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('duplicate')
                value[key] = item
            return value
        try:
            json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('number')))
        except (ValueError, UnicodeError, RecursionError):
            return await reject(400, 'INVALID_JSON_ENVELOPE')
        consumed = False
        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type':'http.request', 'body':bytes(raw), 'more_body':False}
            return await receive()
        await self.app(scope, replay, send)
