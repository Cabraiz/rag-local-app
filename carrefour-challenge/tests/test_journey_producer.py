"""Real HTTP + SQLite + MCP SSE proof against the available producer.

These are negative integration oracles, not a substitute for a confirmed live
journey. No fake slots or receipt schemas are installed into the producer.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import httpx2
import pytest

from clinic_adk.errors import SafeError
from clinic_adk.journey import Journey, State, build_agent
from clinic_adk import journey_gateway as gateway_module
from clinic_adk import runtime

KEY = '00000000-0000-4000-8000-000000000003'
TURN = '00000000-0000-4000-8000-000000000031'


@contextmanager
def serve_api(root, db=None):
    """Owned loopback API/ledger fixture; no dependency on another card's tests."""
    db = Path(db) if db is not None else root / 'appointments.sqlite3'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with (root / 'api.log').open('w') as log:
        process = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'clinic_adk.api:app', '--host',
             '127.0.0.1', '--port', str(port), '--no-access-log'],
            env={**os.environ, 'CLINIC_DB': str(db)}, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            stdout=log, stderr=log)
        try:
            with httpx2.Client(base_url=f'http://127.0.0.1:{port}', timeout=8, trust_env=False) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail('OWNED_API_STARTUP_FAILED')
                    try:
                        if client.get('/health').status_code == 200:
                            break
                    except httpx2.HTTPError:
                        time.sleep(.05)
                else:
                    pytest.fail('OWNED_API_READINESS_TIMEOUT')
                yield client, db, port
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@contextmanager
def serve_rag(root):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with (root / 'rag.log').open('w') as log:
        process = subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'clinic_adk.rag_server:app',
             '--host', '127.0.0.1', '--port', str(port), '--no-access-log'],
            shell=False, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        url = f'http://127.0.0.1:{port}'
        try:
            with httpx2.Client(timeout=2, trust_env=False) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail('OWNED_RAG_STARTUP_FAILED')
                    try:
                        if client.get(url + '/health').status_code == 200:
                            break
                    except httpx2.HTTPError:
                        pass
                    time.sleep(.05)
                else:
                    pytest.fail('OWNED_RAG_READINESS_TIMEOUT')
            yield url
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def count_rows(db):
    with sqlite3.connect(db) as connection:
        return connection.execute('SELECT count(*) FROM appointments').fetchone()[0]


def snapshot(root, case, **data):
    # Harness supplies a worktree-private mounted directory, never a shared DB.
    evidence = Path(os.environ.get('CF_JOURNEY_EVIDENCE', str(root)))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / ('producer-' + case + '.json')).write_text(json.dumps(data, sort_keys=True, indent=2))


class ObservedGateway(gateway_module.ClinicGateway):
    def __init__(self):
        self.http = []
    async def _http(self, method, path, **kwargs):
        self.http.append((method, path))
        return await super()._http(method, path, **kwargs)


def test_actual_adk_rag_sse_api_sqlite_stops_at_missing_slots(tmp_path, monkeypatch):
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    with serve_api(tmp_path) as (client, db, port), serve_rag(tmp_path) as rag:
        monkeypatch.setattr(gateway_module, 'API', f'http://127.0.0.1:{port}')
        monkeypatch.setitem(runtime.ENDPOINTS, 'rag', rag + '/sse')
        # The read creates the disposable ledger; no appointment is inserted.
        assert client.get('/appointments/by-request/' + KEY).status_code == 404
        assert count_rows(db) == 0
        schema = client.get('/openapi.json').json()
        assert '/slots' not in schema['paths']
        assert schema['components']['schemas']['AppointmentReceipt']['properties']['status']['const'] == 'REQUESTED'
        assert set(schema['components']['schemas']['AppointmentRequest']['properties']) == {
            'request_id', 'exam_codes', 'catalog_version'}
        gateway = ObservedGateway()
        journey = Journey(gateway, request_id=KEY)
        async def run():
            sessions = InMemorySessionService()
            await sessions.create_session(app_name='clinic_lab', user_id='fictional_demo', session_id=KEY)
            agent = build_agent(journey, {'turn_id': TURN, 'action': 'search', 'exam_name': 'Hemograma completo'})
            runner = Runner(app_name='clinic_lab', node=agent, session_service=sessions)
            outputs = []
            async for event in runner.run_async(user_id='fictional_demo', session_id=KEY,
                new_message=types.Content(role='user', parts=[types.Part(text='Pedido fictício local.')])):
                output = getattr(event, 'output', None)
                if isinstance(output, dict) and 'result' in output:
                    outputs.append(output['result'])
            return outputs[-1]
        result = asyncio.run(run())
        assert result['state'] == State.CLARIFY
        assert result['code'] == 'API_AVAILABILITY_CONTRACT_UNAVAILABLE'
        assert result['exam']['code'] == 'FICT-001'  # From the actual RAG SSE tool.
        assert result['slots'] == [] and result['receipt'] is None
        assert gateway.http == [('GET', '/slots')]
        assert count_rows(db) == 0
        assert client.get('/slots', params={'exam_code': 'FICT-001'}).status_code == 404
        snapshot(tmp_path, 'missing-slots', state=result['state'], tool_http=gateway.http, persisted_rows=0,
                 openapi_sha256=hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
                 blocker='PRODUCER_SLOT_CONSENT_CONTRACT_UNAVAILABLE')


def test_consent_payload_rejected_by_actual_api_and_adapter_sends_no_post(tmp_path, monkeypatch):
    with serve_api(tmp_path) as (client, db, port):
        monkeypatch.setattr(gateway_module, 'API', f'http://127.0.0.1:{port}')
        assert client.get('/appointments/by-request/' + KEY).status_code == 404
        schema = client.get('/openapi.json').json()
        body = {**schema['paths']['/appointments']['post']['requestBody']['content']['application/json']['example'],
                'request_id': KEY, 'exam_codes': ['FICT-001'],
                'slot_id': '00000000-0000-5000-8000-000000000001', 'confirmed': True}
        response = client.post('/appointments', json=body)
        assert response.status_code == 422 and count_rows(db) == 0
        # The public diagnostics contract masks unknown keys before a sink.
        extra = {'field': 'body.[extra]', 'type': 'extra_forbidden'}
        assert response.json()['errors'] == [extra, extra]
        base = {k: v for k, v in body.items() if k not in ('slot_id', 'confirmed')}
        for field in ('slot_id', 'confirmed'):
            rejected = client.post('/appointments', json={**base, field: body[field]})
            assert rejected.status_code == 422
            assert rejected.json()['errors'] == [extra]
        assert count_rows(db) == 0
        gateway = ObservedGateway()
        with pytest.raises(SafeError, match='API_CONFIRMATION_CONTRACT_UNAVAILABLE'):
            asyncio.run(gateway.reserve({**body, 'patient_ref': 'FICT-PAT-0001'}))
        assert gateway.http == [] and count_rows(db) == 0
        snapshot(tmp_path, 'consent-rejected', consent_post_status=422, adapter_http=gateway.http, persisted_rows=0,
                 blocker='PRODUCER_SLOT_CONSENT_CONTRACT_UNAVAILABLE')


def test_persisted_requested_receipt_is_never_confirmed_or_cancelled(tmp_path, monkeypatch):
    with serve_api(tmp_path) as (client, db, port):
        monkeypatch.setattr(gateway_module, 'API', f'http://127.0.0.1:{port}')
        schema = client.get('/openapi.json').json()
        body = {**schema['paths']['/appointments']['post']['requestBody']['content']['application/json']['example'],
                'request_id': KEY, 'exam_codes': ['FICT-001']}
        response = client.post('/appointments', json=body)
        assert response.status_code == 201 and response.json()['status'] == 'REQUESTED'
        assert client.post('/appointments', json=body).json() == response.json()
        assert count_rows(db) == 1
        gateway = ObservedGateway()
        with pytest.raises(SafeError, match='API_UNCONFIRMED_RECEIPT'):
            asyncio.run(gateway.reconcile(KEY))
        with pytest.raises(SafeError, match='API_CANCELLATION_CONTRACT_UNAVAILABLE'):
            asyncio.run(gateway.cancel(response.json()['appointment_id'], KEY, KEY))
        assert gateway.http == [('GET', '/appointments/by-request/' + KEY)]
        assert count_rows(db) == 1
        with sqlite3.connect(db) as connection:
            receipt = json.loads(connection.execute('SELECT result FROM appointments').fetchone()[0])
        assert receipt['status'] == 'REQUESTED' and receipt == response.json()
        snapshot(tmp_path, 'requested-receipt', persisted_rows=1, persisted_status='REQUESTED', adapter_http=gateway.http,
                 blocker='PRODUCER_CONFIRMED_CANCELLATION_CONTRACT_UNAVAILABLE')
