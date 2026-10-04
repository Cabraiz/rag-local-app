"""Reject malformed tool envelopes before SDK validation can reflect input."""
import json
import asyncio
from mcp.types import CallToolResult, TextContent
from starlette.responses import JSONResponse
from .errors import SafeError
from .security import tool_arguments

# Owner servers retain their original type/length validation unless the explicit
# security adapter enables its process-local policy before serving requests.
PROFILE_TOOLS = frozenset()


def enable_profile_tools():
    global PROFILE_TOOLS
    PROFILE_TOOLS = frozenset(('extract_exams', 'lookup_exams'))

class ToolEnvelopeGuard:
    def __init__(self, tool, argument):
        self.tool, self.argument = tool, argument

    def failure(self):
        return CallToolResult(is_error=True, content=[
            TextContent(type='text', text=json.dumps({'ok':False,'error':'MCP_INVALID_TOOL_ENVELOPE'}))])

    async def __call__(self, ctx, call_next):
        if ctx.method != 'tools/call':
            return await call_next(ctx)
        params = ctx.params
        if (not isinstance(params, dict) or set(params) - {'name','arguments','_meta'}
                or params.get('name') != self.tool):
            return self.failure()
        args = params.get('arguments')
        if not isinstance(args, dict) or set(args) != {self.argument}:
            return self.failure()
        value = args[self.argument]
        if self.argument == 'image_ref':
            valid = isinstance(value, str) and 1 <= len(value) <= 100
        else:
            valid = (isinstance(value, list) and 1 <= len(value) <= 20
                     and all(isinstance(item, str) and 1 <= len(item) <= 120 for item in value))
        if not valid:
            return self.failure()
        if self.tool in PROFILE_TOOLS:
            try:
                tool_arguments(self.tool, args)
            except SafeError:
                return self.failure()
        try:
            return await call_next(ctx)
        except Exception:
            return self.failure()


def unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate field')
        value[key] = item
    return value


def rpc_message(value):
    """Only client operations needed by this fixed SSE workflow are allowed.

    A request id/progress token is echoed by JSON-RPC. Restrict it to a bounded
    integer so it cannot become an exfiltration channel for attacker text.
    """
    if (not isinstance(value, dict) or set(value) - {'jsonrpc', 'id', 'method', 'params'}
            or value.get('jsonrpc') != '2.0'):
        return False
    method, params = value.get('method'), value.get('params', {})
    if not isinstance(params, dict):
        return False
    request = method in ('initialize', 'ping', 'tools/list', 'tools/call')
    if request:
        if type(value.get('id')) is not int or not 0 <= value['id'] <= 2147483647:
            return False
    elif method not in ('notifications/initialized', 'notifications/cancelled') or 'id' in value:
        return False
    meta = params.get('_meta', {})
    if (not isinstance(meta, dict) or set(meta) - {'progressToken'}
            or ('progressToken' in meta and (type(meta['progressToken']) is not int
                or not 0 <= meta['progressToken'] <= 2147483647))):
        return False
    if method == 'tools/call':
        # Argument/tool errors use the non-reflective MCP tool middleware.
        return set(params) <= {'name', 'arguments', '_meta'}
    if method == 'initialize':
        info = params.get('clientInfo')
        return (set(params) - {'_meta'} == {'protocolVersion', 'capabilities', 'clientInfo'}
                and params['protocolVersion'] in ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
                and params['capabilities'] == {}
                and isinstance(info, dict) and set(info) == {'name', 'version'}
                and all(isinstance(info[k], str) and 1 <= len(info[k]) <= 100 for k in info))
    if method == 'notifications/cancelled':
        return (set(params) <= {'requestId', 'reason', '_meta'}
                and type(params.get('requestId')) is int and 0 <= params['requestId'] <= 2147483647
                and ('reason' not in params or isinstance(params['reason'], str)
                     and len(params['reason']) <= 200))
    return set(params) <= {'_meta'}


class MessageEnvelopeGuard:
    """Validate raw JSON before the SDK can coerce or reflect attacker fields."""
    def __init__(self, app, limit=16384):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if (scope['type'] != 'http' or scope['method'] != 'POST'
                or not scope['path'].startswith('/messages/')):
            return await self.app(scope, receive, send)

        async def reject(status, code):
            await JSONResponse({'ok': False, 'error': code}, status_code=status)(scope, receive, send)

        raw = bytearray()
        try:
            async with asyncio.timeout(3):
                while True:
                    event = await receive()
                    if event['type'] == 'http.disconnect':
                        return
                    chunk = event.get('body', b'')
                    if len(raw) + len(chunk) > self.limit:
                        return await reject(413, 'MCP_MESSAGE_SIZE_LIMIT')
                    raw.extend(chunk)
                    if not event.get('more_body', False):
                        break
        except TimeoutError:
            return await reject(408, 'MCP_MESSAGE_TIMEOUT')
        try:
            value = json.loads(raw.decode('utf8'), object_pairs_hook=unique_fields,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('number')))
            if not rpc_message(value):
                raise ValueError('envelope')
        except (ValueError, UnicodeError, RecursionError, TypeError):
            return await reject(400, 'MCP_INVALID_MESSAGE')

        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type': 'http.request', 'body': bytes(raw), 'more_body': False}
            return await receive()

        return await self.app(scope, replay, send)
