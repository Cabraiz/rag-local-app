"""MCP over SSE without Gemini: start both servers and call their tools with mcp.client.sse.

This is the same transport the ADK agent uses (SseConnectionParams), so it proves the
servers, tool names and response formats without an API key.
"""
import asyncio
import json
import re
import shutil
import socket
import threading
import time
from pathlib import Path

import httpx
import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client

from mcp_servers import ocr, rag

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'samples'


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@pytest.fixture(scope='module')
def urls():
    """Run each server's SSE app with uvicorn in a background thread; yield their /sse URLs."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ocr, 'SAMPLES_DIR', SAMPLES)
        servers, found = [], {}
        for name, module in (('ocr', ocr), ('rag', rag)):
            port = free_port()
            app = module.server.sse_app(transport_security=module.SECURITY, host='127.0.0.1')
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning',
                                                   timeout_keep_alive=module.KEEP_ALIVE_SECONDS))
            threading.Thread(target=server.run, daemon=True).start()
            deadline = time.monotonic() + 60  # generous: a busy machine starts uvicorn slowly
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started, f'{name} server did not start'
            servers.append(server)
            found[name] = f'http://127.0.0.1:{port}/sse'
        yield found
        for server in servers:
            server.should_exit = True


def test_servers_keep_idle_connections_longer_than_the_client_pool():
    """With both at 5 s, a POST sent as the server closed the connection was lost and its call
    never returned (python-sdk #906): OCR, RAG and the API must outlast the clients' pools."""
    client_expiry = max(httpx.Limits().keepalive_expiry, httpx2.Limits().keepalive_expiry)
    api = re.search(r'"--timeout-keep-alive", "(\d+)"', (ROOT / 'Dockerfile').read_text(encoding='utf-8'))
    assert api and int(api[1]) == ocr.KEEP_ALIVE_SECONDS == rag.KEEP_ALIVE_SECONDS
    assert ocr.KEEP_ALIVE_SECONDS > 2 * client_expiry


def call(url, tool, arguments):
    """Open an SSE session, list the tools and call one; return (tool names, result)."""
    async def run():
        async with sse_client(url) as streams, ClientSession(*streams) as session:
            await session.initialize()
            names = [listed.name for listed in (await session.list_tools()).tools]
            return names, await session.call_tool(tool, arguments)
    return asyncio.run(run())


def test_rag_tool_over_sse(urls):
    names, result = call(urls['rag'], 'search_exams', {'query': 'Glicose', 'top_k': 2})
    assert names == ['search_exams'] and not result.is_error
    assert result.structured_content['result'][0] == {'code': 'FICT-002', 'name': 'Glicemia de jejum', 'score': 1.0,
                                                       'term': 'Glicose'}


@pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image')
def test_ocr_tool_over_sse_returns_masked_lines(urls):
    names, result = call(urls['ocr'], 'extract_exam_text', {'filename': 'pedido.png'})
    assert names == ['extract_exam_text'] and not result.is_error
    payload = json.loads(result.content[0].text)
    assert 'Exame: Creatinina' in payload['lines'] and payload['pii_masked']['CPF'] == 1
    assert 'sentinela' not in json.dumps(payload, ensure_ascii=False).lower()
    # Contract for the agent: one 0-100 confidence per returned line, in the same order.
    confidence = payload['line_confidence']
    assert len(confidence) == len(payload['lines'])
    assert all(isinstance(value, (int, float)) and 0 <= value <= 100 for value in confidence)
    assert confidence[payload['lines'].index('Exame: Creatinina')] >= 80  # a clean printed line reads sure


@pytest.mark.parametrize('server, tool, arguments, message', [
    ('ocr', 'extract_exam_text', {'filename': '../etc/passwd'}, 'sem pastas'),
    ('rag', 'search_exams', {'query': ''}, 'query deve ser um texto'),
    # Wrong type: one sentence, never the SDK's pydantic dump.
    *[('ocr', 'extract_exam_text', arguments, 'filename deve ser o nome de um arquivo, ex.: pedido.png')
      for arguments in ({'filename': None}, {'filename': 123}, {'filename': ['a.png']}, {'filename': {'nome': 'a.png'}},
                        {'filename': True})],
    *[('rag', 'search_exams', arguments, 'query deve ser um texto com o nome de um exame')
      for arguments in ({'query': 123}, {'query': None}, {'query': ['tsh']})],
    *[('rag', 'search_exams', {'query': 'tsh', 'top_k': top_k}, 'top_k deve ser um inteiro entre 1 e 10')
      for top_k in (0, 1000, 'x', None, 2.5, [3])],
])
def test_tool_errors_reach_the_client_with_a_clear_message(urls, server, tool, arguments, message):
    _, result = call(urls[server], tool, arguments)
    text = result.content[0].text
    assert result.is_error and message in text
    assert 'validation error' not in text and 'pydantic' not in text


@pytest.mark.parametrize('server, tool', [('ocr', 'extract_exam_text'), ('rag', 'search_exams')])
def test_a_missing_required_argument_is_refused_by_the_sdk(urls, server, tool):
    # The arguments stay required in the schema the model reads, so a missing one is
    # refused by the SDK's own validation before the tool runs (rare: the model always sends them).
    _, result = call(urls[server], tool, {})
    assert result.is_error and 'Field required' in result.content[0].text


def test_published_schemas_keep_their_types_and_required_arguments(urls):
    """The ADK agent builds its function declarations from these schemas."""
    async def schemas(url):
        async with sse_client(url) as streams, ClientSession(*streams) as session:
            await session.initialize()
            return {tool.name: tool.input_schema for tool in (await session.list_tools()).tools}
    ocr_schema = asyncio.run(schemas(urls['ocr']))['extract_exam_text']
    assert ocr_schema['properties']['filename']['type'] == 'string' and ocr_schema['required'] == ['filename']
    rag_schema = asyncio.run(schemas(urls['rag']))['search_exams']
    assert (rag_schema['properties']['query']['type'], rag_schema['properties']['top_k']['type']) == ('string', 'integer')
    assert rag_schema['required'] == ['query']


def test_valid_arguments_in_other_json_shapes_still_work(urls):
    # A top_k sent as 2.0 (Gemini's numbers) or "2" is still a whole number.
    for top_k in (2.0, '2'):
        _, result = call(urls['rag'], 'search_exams', {'query': 'Glicose', 'top_k': top_k})
        assert not result.is_error and len(result.structured_content['result']) == 2
