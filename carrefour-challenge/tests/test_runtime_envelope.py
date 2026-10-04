"""UC06-F01: ambiguous MCP envelopes stop before approval or appointment.

Live hostile SSE is an explicitly adversarial fictional fixture. The graph
oracle seeds only the OCR stage; retrieval uses the actual ADK MCP SSE client.
"""
import asyncio
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4

import pytest
import httpx2
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from starlette.responses import JSONResponse
from starlette.routing import Route

from clinic_adk.catalog import Catalog
from clinic_adk.cli import execute, safe_failure_code
from clinic_adk.compiler import emit, parse_spec
from clinic_adk.errors import SafeError
from clinic_adk import runtime as module

PAYLOAD = {'ok': True, 'exams': [{'name': 'Hemograma completo', 'code': 'FICT-001',
                               'evidence': 'fictional evidence'}]}
HOSTILE_MODES = ('duplicate', 'contradictory', 'inconsistent_evidence', 'malformed_text')
# Envelope controls must themselves pass the real catalog input gate. These are
# approved fictional names; the hostile response and every oracle stay unchanged.
MODE_QUERIES = dict(zip(HOSTILE_MODES, [Catalog().by_code[f'FICT-{index:03d}']['name']
                                     for index in range(2, 6)]))
hostile_calls = []


def hostile_text(payload, mode):
    other = copy.deepcopy(payload)
    if mode == 'duplicate':
        return json.dumps(other).replace('"ok": true', '"ok": false, "ok": true')
    if mode == 'contradictory':
        other['ok'] = False
    elif mode == 'inconsistent_evidence':
        other['exams'][0]['evidence'] = 'forged fictional evidence'
    elif mode == 'malformed_text':
        return '{broken'
    return json.dumps(other)


@pytest.mark.parametrize('mode', HOSTILE_MODES)
@pytest.mark.parametrize('key', ['structuredContent', 'structured_content'])
def test_structured_result_cannot_hide_hostile_text(mode, key):
    envelope = {key: copy.deepcopy(PAYLOAD), 'content': [
        {'type': 'text', 'text': hostile_text(PAYLOAD, mode)}]}
    with pytest.raises(SafeError):
        module.decode_tool(envelope)


@pytest.mark.parametrize('text', [
    '{"ok":true,"nested":{"code":"FICT-999","code":"FICT-001"}}',
    '{"ok":true,"number":NaN}', '{"ok":true,"number":1e999}',
    '[' * 1100 + ']' * 1100, 'x' * 16001,
])
def test_structured_result_still_checks_all_text_json(text):
    with pytest.raises(SafeError):
        module.decode_tool({'structuredContent': {'ok': True},
                            'content': [{'type': 'text', 'text': text}]})


@pytest.mark.parametrize('content', [None, 'text', [None],
    [{'type': 'image', 'data': 'fictional'}], [{'type': 'text', 'text': None}],
    [{'type': 'text', 'text': '{"ok":true}'}] * 2])
def test_structured_result_cannot_hide_invalid_content_shape(content):
    with pytest.raises(SafeError):
        module.decode_tool({'structuredContent': {'ok': True}, 'content': content})


@pytest.mark.parametrize('structured', [[], False, 1, 'text'])
def test_text_result_cannot_hide_wrong_structured_type(structured):
    with pytest.raises(SafeError):
        module.decode_tool({'structuredContent': structured,
                            'content': [{'type': 'text', 'text': '{"ok":true}'}]})


def test_structured_aliases_must_agree_with_strict_json_types():
    for other in ({'ok': False}, {'ok': True, 'value': 1}, {'ok': True, 'value': 1.0}):
        with pytest.raises(SafeError):
            module.decode_tool({'structuredContent': {'ok': True, 'value': True},
                                'structured_content': other})


def test_direct_payload_cannot_override_envelope_representations():
    with pytest.raises(SafeError):
        module.decode_tool({'ok': True, 'structuredContent': {'ok': False}})
    with pytest.raises(SafeError):
        module.decode_tool({'ok': True, 'content': [{'type': 'text', 'text': '{broken'}]})


@pytest.mark.parametrize('envelope', [
    {'ok': True}, {'structuredContent': {'ok': True}},
    {'structuredContent': {'ok': True}, 'content': []},
    {'structuredContent': None, 'content': [{'type': 'text', 'text': '{"ok":true}'}]},
    {'content': [{'type': 'text', 'text': '{"ok":true}'}]},
    {'structuredContent': {'ok': True}, 'structured_content': {'ok': True},
     'content': [{'type': 'text', 'text': ' { "ok": true } '}]},
])
def test_unambiguous_supported_representations_remain_valid(envelope):
    assert module.decode_tool(envelope) == {'ok': True}


def test_equivalent_text_order_and_unicode_escapes_remain_valid():
    payload = {'ok': True, 'name': 'Exame fictício', 'value': [1, 'x']}
    text = json.dumps(dict(reversed(list(payload.items()))), ensure_ascii=True, indent=2)
    assert module.decode_tool({'structuredContent': payload,
                              'content': [{'type': 'text', 'text': text}]}) == payload


hostile_server = MCPServer('fictional-uc08-envelope-regression')


@hostile_server.tool()
def lookup_exams(exam_names: list[str]) -> CallToolResult:
    mode = next((key for key, name in MODE_QUERIES.items() if name == exam_names[0]), 'consistent')
    hostile_calls.append(mode)
    payload = Catalog().retrieve(['Hemograma completo'])
    return CallToolResult(structured_content=payload,
                         content=[TextContent(type='text', text=hostile_text(payload, mode))])


async def health(request):
    return JSONResponse({'ok': True, 'fictional': True, 'tool_calls': len(hostile_calls)})


hostile_app = hostile_server.sse_app(transport_security=TransportSecuritySettings(
    enable_dns_rebinding_protection=True, allowed_hosts=['127.0.0.1:*'], allowed_origins=[]))
hostile_app.routes.append(Route('/health', health))


@pytest.fixture(scope='module')
def hostile_endpoint(tmp_path_factory):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    log = (tmp_path_factory.mktemp('hostile-sse') / 'server.log').open('w')
    process = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'test_runtime_envelope:hostile_app',
         '--app-dir', str(Path(__file__).parent), '--host', '127.0.0.1',
         '--port', str(port), '--no-access-log'], shell=False, stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    url = 'http://127.0.0.1:' + str(port)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                pytest.fail('OWNED_HOSTILE_SSE_EXITED')
            try:
                with opener.open(url + '/health', timeout=1) as response:
                    assert json.load(response)['ok']
                break
            except OSError:
                if time.monotonic() >= deadline:
                    pytest.fail('OWNED_HOSTILE_SSE_READINESS_TIMEOUT')
                time.sleep(.1)
        yield url + '/sse'
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


@pytest.mark.parametrize('mode', HOSTILE_MODES)
def test_actual_adk_sse_rejects_ambiguous_envelope(mode, hostile_endpoint, monkeypatch):
    monkeypatch.setitem(module.ENDPOINTS, 'rag', hostile_endpoint)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def calls():
        with opener.open(hostile_endpoint.removesuffix('/sse') + '/health', timeout=3) as response:
            return json.load(response)['tool_calls']
    before = calls()
    with pytest.raises(SafeError):
        asyncio.run(module.mcp_call('rag', {'exam_names': [MODE_QUERIES[mode]]}))
    assert calls() == before + 1, 'ENVELOPE_GATE_MUST_EXERCISE_REAL_SSE'


@pytest.mark.parametrize('mode', HOSTILE_MODES)
def test_generated_adk_graph_stops_before_validation_and_booking(mode, hostile_endpoint, monkeypatch, tmp_path):
    monkeypatch.setitem(module.ENDPOINTS, 'rag', hostile_endpoint)
    original_call, original_step = module.mcp_call, module.Runtime.step
    stages, posts = [], []
    async def tools(provider, arguments, timeout=12):
        if provider == 'ocr':
            return {'ok': True, 'pii_masked': True, 'unresolved_count': 0,
                    'exam_names': ['Hemograma completo']}
        return await original_call(provider, {'exam_names': [MODE_QUERIES[mode]]}, timeout)
    async def step(self, kind, node_input):
        stages.append(kind)
        return await original_step(self, kind, node_input)
    async def forbidden(self, body):
        posts.append(body)
        raise AssertionError('BOOKING_AFTER_INVALID_ENVELOPE')
    monkeypatch.setattr(module, 'mcp_call', tools)
    monkeypatch.setattr(module.Runtime, 'step', step)
    monkeypatch.setattr(module.Runtime, 'book', forbidden)
    spec = parse_spec(Path('/app/examples/agent.json').read_bytes())
    artifact = tmp_path / 'generated.py'
    artifact.write_text(emit(spec))
    with pytest.raises(Exception) as failure:
        asyncio.run(execute(spec, artifact, 'fictional.png', str(uuid4())))
    assert safe_failure_code(failure.value) in ('MCP_INVALID_RESULT', 'MCP_TOOL_FAILED')
    assert stages == ['ocr', 'retrieve']
    assert posts == []


def test_consistent_actual_adk_sse_result_still_validates(hostile_endpoint, monkeypatch):
    monkeypatch.setitem(module.ENDPOINTS, 'rag', hostile_endpoint)
    async def run():
        runtime = module.Runtime('fictional.png')
        runtime.stages = ['ocr']
        # This fixture mode returns identical structured and text catalog evidence.
        original = module.mcp_call
        async def call(provider, arguments, timeout=12):
            return await original(provider, {'exam_names': ['Hemograma completo']}, timeout)
        monkeypatch.setattr(module, 'mcp_call', call)
        retrieved = await runtime.step('retrieve', {'names': ['Hemograma completo']})
        return await runtime.step('validate', retrieved)
    assert asyncio.run(run())['validated'] is True


@pytest.mark.parametrize('request_id', ['', False, 0, [], {}, 7, b'fictional',
    'not-a-uuid', 'AABBCCDD-1122-3344-5566-778899001122'])
def test_r2_explicit_invalid_identity_never_allocates_replacement(request_id):
    with pytest.raises(SafeError) as failure:
        module.Runtime('fictional.png', request_id)
    assert failure.value.code == 'INVALID_REQUEST_ID'


def test_r2_omitted_and_canonical_identity_remain_valid():
    key = str(uuid4())
    assert module.Runtime(request_id=key).request_id == key
    assert str(__import__('uuid').UUID(module.Runtime().request_id))


def test_r2_actual_adk_graph_cancellation_during_close_stops_before_booking(
        hostile_endpoint, monkeypatch, tmp_path):
    from google.adk.tools.mcp_tool import McpToolset
    original_close, original_call, original_step = McpToolset.close, module.mcp_call, module.Runtime.step
    stages, posts = [], []
    monkeypatch.setitem(module.ENDPOINTS, 'rag', hostile_endpoint)
    async def run():
        closing = asyncio.Event()
        async def close(self):
            await original_close(self)
            closing.set()
            await asyncio.sleep(60)
        async def call(provider, arguments, timeout=12):
            if provider == 'ocr':
                return {'ok': True, 'pii_masked': True, 'unresolved_count': 0,
                        'exam_names': ['Hemograma completo']}
            return await original_call(provider, {'exam_names': ['Hemograma completo']}, timeout)
        async def step(self, kind, value):
            stages.append(kind)
            return await original_step(self, kind, value)
        async def forbidden(self, body):
            posts.append(body)
            raise AssertionError('BOOKING_AFTER_CANCEL')
        monkeypatch.setattr(McpToolset, 'close', close)
        monkeypatch.setattr(module, 'mcp_call', call)
        monkeypatch.setattr(module.Runtime, 'step', step)
        monkeypatch.setattr(module.Runtime, 'book', forbidden)
        spec = parse_spec(Path('/app/examples/agent.json').read_bytes())
        source = tmp_path / 'generated.py'
        source.write_text(emit(spec))
        task = asyncio.create_task(execute(spec, source, 'fictional.png', str(uuid4())))
        try:
            await asyncio.wait_for(closing.wait(), 15)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
    asyncio.run(run())
    assert stages == ['ocr', 'retrieve'] and posts == []


class ObservedStream(httpx2.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.reads, self.closed = chunks, [], False
    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            self.reads.append(index)
            yield chunk
    async def aclose(self):
        self.closed = True


def appointment_input():
    runtime = module.Runtime('fictional.png')
    return runtime, {'request_id': runtime.request_id, 'exam_codes': ['FICT-001'],
                     'catalog_version': runtime.catalog.version}


@pytest.mark.parametrize('status', [202, 204, 206, 299, 301, 302, 307, 308, 408, 500, 503, 599])
def test_r2_uncertain_status_never_reads_body_or_reports_rejection(status, monkeypatch):
    stream = ObservedStream([b'private-unbounded-error-body' * 10000])
    calls = []
    async def send(self, request):
        calls.append(request)
        return httpx2.Response(status, stream=stream)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, 'handle_async_request', send)
    runtime, body = appointment_input()
    with pytest.raises(SafeError) as failure:
        asyncio.run(runtime.book(body))
    assert failure.value.code == 'APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY'
    assert len(calls) == 1 and stream.reads == [] and stream.closed


@pytest.mark.parametrize('status', [400, 401, 403, 404, 409, 422, 429, 499])
def test_r2_known_rejection_closes_without_draining(status, monkeypatch):
    stream = ObservedStream([b'private-error-body' * 10000])
    async def send(self, request):
        return httpx2.Response(status, stream=stream)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, 'handle_async_request', send)
    runtime, body = appointment_input()
    with pytest.raises(SafeError) as failure:
        asyncio.run(runtime.book(body))
    assert failure.value.code == 'APPOINTMENT_REJECTED'
    assert stream.reads == [] and stream.closed


def test_r2_success_body_overflow_stops_before_next_chunk(monkeypatch):
    stream = ObservedStream([b'x' * 4096, b'x', b'x' * 65536])
    async def send(self, request):
        return httpx2.Response(201, stream=stream)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, 'handle_async_request', send)
    runtime, body = appointment_input()
    with pytest.raises(SafeError) as failure:
        asyncio.run(runtime.book(body))
    assert failure.value.code == 'APPOINTMENT_INVALID_RECEIPT'
    assert stream.reads == [0, 1] and stream.closed


@pytest.mark.parametrize('status', [200, 201])
def test_r2_exact_body_bound_preserves_valid_receipt(status, monkeypatch):
    runtime, body = appointment_input()
    receipt = {**body, 'appointment_id': str(uuid4()), 'status': 'REQUESTED'}
    content = json.dumps(receipt).encode().ljust(4096, b' ')
    stream = ObservedStream([content[:250], content[250:]])
    async def send(self, request):
        return httpx2.Response(status, stream=stream)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, 'handle_async_request', send)
    assert asyncio.run(runtime.book(body)) == receipt
    assert stream.reads == [0, 1] and stream.closed


def test_r2_compressed_receipt_rejected_before_decompression(monkeypatch):
    import gzip
    stream = ObservedStream([gzip.compress(b'x' * 1000000)])
    async def send(self, request):
        return httpx2.Response(201, headers={'Content-Encoding': 'gzip'}, stream=stream)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, 'handle_async_request', send)
    runtime, body = appointment_input()
    with pytest.raises(SafeError) as failure:
        asyncio.run(runtime.book(body))
    assert failure.value.code == 'APPOINTMENT_INVALID_RECEIPT'
    assert stream.reads == [] and stream.closed
