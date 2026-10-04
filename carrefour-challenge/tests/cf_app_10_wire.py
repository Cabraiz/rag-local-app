"""Dedicated CF10 wire fixture; no dependency on removed owner test modules."""
import asyncio
from contextlib import asynccontextmanager
import json
import httpx2


class Wire:
    def __init__(self, client, response, base):
        self.client, self.response, self.base = client, response, base
        self.lines = response.aiter_lines()
        self.endpoint = None

    async def frame(self):
        event, data = None, []
        async for line in self.lines:
            if not line:
                if event or data:
                    return event, '\n'.join(data)
            elif line.startswith('event:'):
                event = line[6:].strip()
            elif line.startswith('data:'):
                data.append(line[5:].lstrip())
        raise EOFError('SSE_CLOSED')

    async def post(self, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        return await self.client.post(self.base + self.endpoint, content=raw,
            headers={'Content-Type': 'application/json'})

    async def result(self, request_id):
        async with asyncio.timeout(12):
            while True:
                event, data = await self.frame()
                if event == 'message':
                    value = json.loads(data)
                    if value.get('id') == request_id:
                        return value


def message(request_id, method, params):
    return {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}


@asynccontextmanager
async def wire(base):
    async with httpx2.AsyncClient(timeout=15, trust_env=False) as client:
        async with client.stream('GET', base + '/sse', headers={'Accept': 'text/event-stream'}) as response:
            assert response.status_code == 200
            session = Wire(client, response, base)
            event, endpoint = await session.frame()
            assert event == 'endpoint' and endpoint.startswith('/messages/?session_id=')
            session.endpoint = endpoint
            initialized = await session.post(message(1, 'initialize', {
                'protocolVersion': '2024-11-05', 'capabilities': {},
                'clientInfo': {'name': 'fictional-cf10-proof', 'version': '1.0'}}))
            assert initialized.status_code == 202
            assert 'result' in await session.result(1)
            assert (await session.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'})).status_code == 202
            yield session


def failed(value):
    assert 'PRIVATE_' not in json.dumps(value)
    if 'error' in value:
        return
    result = value.get('result')
    assert isinstance(result, dict)
    if result.get('isError'):
        return
    structured = result.get('structuredContent')
    if structured is None:
        structured = json.loads(result['content'][0]['text'])
    assert structured.get('ok') is False
