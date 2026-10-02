"""New failure oracles, written before fixes; no Gemini, production data or writes.

Storage failures must be distinguishable from a missing/successful booking.
Untrusted tool JSON must be unambiguous. File input must be bounded/nonblocking.
Two fresh seeds vary cases; these same-author checks are not an independent audit.
"""
import asyncio
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from clinic_adk.errors import SafeError
from clinic_adk.runtime import decode_tool

@pytest.mark.parametrize('route', ['by-request', 'appointment', 'create'])
def test_storage_unavailable_is_safe_503(route, monkeypatch):
    from clinic_adk import api
    key = str(uuid4())
    def unavailable():
        raise sqlite3.OperationalError('PRIVATE_DISCOVERY_SENTINEL')
    monkeypatch.setattr(api, 'connect', unavailable)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        if route == 'create':
            response = client.post('/appointments', json={
                'request_id': key, 'exam_codes': ['FICT-001'], 'catalog_version': api.catalog.version})
        else:
            path = '/appointments/by-request/' if route == 'by-request' else '/appointments/'
            response = client.get(path + key)
        assert response.status_code == 503
        assert response.json() == {'detail': 'LEDGER_UNAVAILABLE_RETRY_SAME_KEY'}
        assert 'PRIVATE_DISCOVERY_SENTINEL' not in response.text

def test_partial_connection_initialization_closes_handle(monkeypatch):
    from clinic_adk import api
    connection = Mock()
    connection.execute.side_effect = sqlite3.OperationalError('locked')
    monkeypatch.setattr(api.sqlite3, 'connect', lambda *a, **kw: connection)
    with pytest.raises(sqlite3.Error):
        api.connect()
    connection.close.assert_called_once()

@pytest.mark.parametrize('route', ['by-request', 'appointment', 'create'])
@pytest.mark.parametrize('damage', ['missing', 'wrong-request', 'invalid-code'])
def test_corrupted_saved_receipt_never_claims_success(route, damage, tmp_path, monkeypatch):
    from clinic_adk import api
    monkeypatch.setattr(api, 'DB', str(tmp_path/'corrupt.sqlite3'))
    key, appointment = str(uuid4()), str(uuid4())
    body = {'request_id': key, 'exam_codes': ['FICT-001'], 'catalog_version': api.catalog.version}
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post('/appointments', json=body)
        assert response.status_code == 201
        receipt = response.json()
        appointment = receipt['appointment_id']
        if damage == 'missing': receipt.pop('status')
        elif damage == 'wrong-request': receipt['request_id'] = str(uuid4())
        else: receipt['exam_codes'] = ['FICT-999']
        with sqlite3.connect(api.DB) as connection:
            connection.execute('UPDATE appointments SET result=? WHERE request_id=?', (json.dumps(receipt), key))
        if route == 'create': response = client.post('/appointments', json=body)
        else: response = client.get('/appointments/by-request/'+key if route == 'by-request' else '/appointments/'+appointment)
        assert response.status_code == 503
        assert response.json() == {'detail': 'LEDGER_UNAVAILABLE_RETRY_SAME_KEY'}

@pytest.mark.parametrize('text', [
    '{"ok":false,"ok":true}', '{"ok":true,"unresolved_count":1,"unresolved_count":0}',
    '{"ok":true,"nested":{"code":"FICT-999","code":"FICT-001"}}',
    '{"ok":true,"number":NaN}', '{"ok":true,"number":1e999}',
    '['*1100+']'*1100,
])
def test_tool_json_is_unambiguous_and_safe(text):
    with pytest.raises(SafeError):
        decode_tool({'content': [{'type': 'text', 'text': text}]})

def test_invalid_spec_keeps_valid_request_correlation(monkeypatch, capsys):
    from clinic_adk import cli
    key = str(uuid4())
    monkeypatch.setattr(sys, 'argv', ['clinic', 'run', '--request-id', key])
    def reject(path): raise SafeError('SPEC_INVALID_JSON')
    monkeypatch.setattr(cli, 'read_spec', reject)
    assert cli.main() == 2
    assert json.loads(capsys.readouterr().err)['request_id'] == key

def test_spec_reader_does_not_use_unbounded_read(monkeypatch):
    from clinic_adk.cli import read_spec
    original = Path.read_bytes
    path = Path('/app/examples/agent.json')
    def forbidden(self):
        if self == path: raise AssertionError('UNBOUNDED_SPEC_READ')
        return original(self)
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    assert read_spec(str(path)).schema_version == 1

def test_fifo_artifact_cannot_block_before_workflow_timeout(tmp_path):
    path = tmp_path/'blocked.py'
    os.mkfifo(path)
    code = '''import asyncio,sys
from pathlib import Path
from uuid import uuid4
from clinic_adk.cli import execute
from clinic_adk.compiler import parse_spec
from clinic_adk.errors import SafeError
spec=parse_spec(Path('/app/examples/agent.json').read_bytes())
try: asyncio.run(execute(spec,Path(sys.argv[1]),'request.png',str(uuid4())))
except SafeError: sys.exit(0)
sys.exit(1)
'''
    try:
        result = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        pytest.fail('FIFO_BLOCKED_OUTSIDE_WORKFLOW_BUDGET')
    assert result.returncode == 0

def test_new_seeded_mutations_never_call_network(monkeypatch):
    from clinic_adk.compiler import parse_spec, emit
    from clinic_adk import runtime
    rng = random.Random(int(os.environ.get('CF_SEED', '99173')))
    base = json.loads(Path('/app/examples/agent.json').read_text())
    names = ['start', 'workflow', 'root_agent', 'match', 'case', 'type', 'runtime', 'class', 'x\u200b', 'x\n']
    rng.shuffle(names)
    for name in names:
        spec = json.loads(json.dumps(base))
        spec['stages'][0]['name'] = name
        try:
            parsed = parse_spec(json.dumps(spec).encode())
        except SafeError:
            continue
        module = {}
        exec(compile(emit(parsed), 'seeded_discovery', 'exec'), module)
        assert module['root_agent'].name == base['name']
    async def forbidden(*a, **kw): raise AssertionError('NETWORK_AFTER_BAD_EVIDENCE')
    monkeypatch.setattr(runtime, 'mcp_call', forbidden)
    for code in rng.sample(['FICT-999', None, [], {}, True, 42], 6):
        agent = runtime.Runtime('request.png')
        agent.stages = ['ocr', 'retrieve']
        row = {'code': code, 'name': 'Hemograma completo', 'evidence': 'invented'}
        with pytest.raises(SafeError):
            asyncio.run(agent.step('validate', {'names': ['Hemograma completo'], 'exams': [row]}))
