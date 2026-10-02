"""Second discovery batch: explore adjacent cases after the first findings."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import pytest
import httpx2
from clinic_adk.errors import SafeError
from clinic_adk.runtime import Runtime

@pytest.mark.parametrize('status', [408, 500, 502, 503, 504])
def test_uncertain_http_outcome_is_not_declared_rejected(status, monkeypatch):
    runtime = Runtime('request.png')
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **kw): return httpx2.Response(status, content=b'{}')
    monkeypatch.setattr(httpx2, 'AsyncClient', lambda **kw: Client())
    body = {'request_id': runtime.request_id, 'exam_codes': ['FICT-001'], 'catalog_version': runtime.catalog.version}
    with pytest.raises(SafeError) as failure:
        asyncio.run(runtime.book(body))
    assert failure.value.code == 'APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY'

def test_ambiguous_api_receipt_is_rejected(monkeypatch):
    runtime = Runtime('request.png')
    body = {'request_id': runtime.request_id, 'exam_codes': ['FICT-001'], 'catalog_version': runtime.catalog.version}
    receipt = {**body, 'appointment_id': str(uuid4()), 'status': 'REQUESTED'}
    raw = json.dumps(receipt).replace('"status": "REQUESTED"', '"status":"REJECTED","status":"REQUESTED"').encode()
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **kw): return httpx2.Response(201, content=raw)
    monkeypatch.setattr(httpx2, 'AsyncClient', lambda **kw: Client())
    with pytest.raises(SafeError):
        asyncio.run(runtime.book(body))

def test_transpile_fifo_output_is_bounded():
    path = Path('/artifacts')/('discovery-'+str(uuid4())+'.py')
    os.mkfifo(path)
    try:
        try:
            result = subprocess.run([sys.executable, '-m', 'clinic_adk.cli', 'transpile', '--output', path.name], capture_output=True, timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail('TRANSPILE_FIFO_BLOCKED_WITHOUT_TIMEOUT')
        assert result.returncode in (0, 2)
        if result.returncode == 0:
            assert path.is_file() and json.loads(result.stdout)['ok'] is True
    finally:
        path.unlink(missing_ok=True)

@pytest.mark.parametrize('slot', ['code', 'name', 'evidence'])
@pytest.mark.parametrize('value', [[], {}, None, False, 5])
def test_evidence_types_fail_with_typed_error(slot, value):
    agent = Runtime('request.png')
    agent.stages = ['ocr', 'retrieve']
    row = {key: agent.catalog.by_code['FICT-001'][key] for key in ('code', 'name', 'evidence')}
    row[slot] = value
    with pytest.raises(SafeError) as failure:
        asyncio.run(agent.step('validate', {'names': ['Hemograma completo'], 'exams': [row]}))
    assert failure.value.code == 'EXAM_EVIDENCE_MISMATCH'

def test_file_reader_rejects_symlinks_and_devices(tmp_path):
    from clinic_adk.file_input import bounded_file
    path = tmp_path/'escape.json'
    path.symlink_to('/app/examples/agent.json')
    for target in (path, Path('/dev/zero'), tmp_path):
        with pytest.raises(SafeError): bounded_file(target, 16384, 'FILE_DENIED')

def test_oversize_spec_is_bounded_on_opened_descriptor(tmp_path):
    from clinic_adk.file_input import bounded_file
    path = tmp_path/'oversized'
    with path.open('wb') as stream: stream.truncate(10_000_000)
    with pytest.raises(SafeError): bounded_file(path, 16384, 'FILE_DENIED')
