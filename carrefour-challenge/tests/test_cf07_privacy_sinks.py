"""CF-APP-07 canary oracles. Fixtures are fictional; no cloud or patient data."""
import asyncio
from contextlib import redirect_stdout
import copy
import io
import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import traceback
import unicodedata
from uuid import UUID

import pytest
from clinic_adk.catalog import Catalog
from clinic_adk.compiler import emit, parse_spec
from clinic_adk.errors import SafeError
from clinic_adk.privacy import sanitize_ocr
from clinic_adk import runtime as runtime_module

# Every value is a deliberately fictional sentinel, never an actual credential.
CANARIES = ('Pessoa Canario ZQX', 'Doutora Canario QRS', '123.456.789-00',
            'canario.zqx@example.invalid', '(11) 90000-1234',
            'Rua Canario Ficticia 987', '29/02/2000', 'Ma\u0301rcia Canario WUV')
HEADERS = ('Paciente', 'Médica', 'CPF', 'E-mail', 'Telefone', 'Endereço',
           'Nascimento', 'Nome')
REQUEST_ID = '00000000-0000-4000-8000-000000000707'


def normalized(value):
    import re
    value = re.sub(r'\\u([0-9a-fA-F]{4})', lambda match: chr(int(match[1], 16)), value)
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                   if c.isalnum())


def assert_private(*sinks):
    text = '\n'.join(s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
                     for s in sinks)
    # Decode any nested JSON text before scanning escaped Unicode representations.
    variants = [text]
    try:
        variants.append(json.dumps(json.loads(text), ensure_ascii=False))
    except (ValueError, TypeError):
        pass
    for variant in variants:
        folded = normalized(variant)
        for canary in CANARIES:
            assert canary not in variant, 'literal canary reached observable sink'
            assert normalized(canary) not in folded, 'normalized canary reached observable sink'


def headers_text():
    return '\n'.join(f'{key}: {value}' for key, value in zip(HEADERS, CANARIES))


def fullwidth(text):
    return ''.join(chr(ord(c) + 0xFEE0) if 0x21 <= ord(c) <= 0x7E else c for c in text)


@pytest.mark.parametrize('transform', [lambda s: s, fullwidth,
                                     lambda s: unicodedata.normalize('NFD', s),
                                     lambda s: s.replace(':', '\u00a0:\u00a0')])
def test_ocr_normalizes_personal_headers_before_projection(transform):
    result = sanitize_ocr(transform(headers_text() + '\nExame: Hemograma completo'), Catalog())
    assert result['exam_names'] == ['Hemograma completo']
    assert result['redacted_lines'] == len(CANARIES)
    assert_private(result)


@pytest.mark.parametrize('canary', CANARIES)
def test_ocr_unknown_exam_is_not_silently_discarded(canary):
    with pytest.raises(SafeError) as error:
        sanitize_ocr('Exame: Hemograma completo\nExame: ' + canary, Catalog())
    assert error.value.code == 'OCR_UNRESOLVED_EXAMS'
    assert_private(''.join(traceback.format_exception(error.value)))


@pytest.mark.parametrize('control', ['\u200b', '\u202e', '\x00'])
def test_ocr_invisible_controls_fail_closed(control):
    with pytest.raises(SafeError):
        sanitize_ocr('Paciente: ' + CANARIES[0] + control + '\nExame: Hemograma completo', Catalog())


@pytest.mark.parametrize('field', ['name', 'evidence', 'extra', 'code'])
def test_retrieval_rejects_canary_before_node_event(field, monkeypatch):
    runtime = runtime_module.Runtime('request.png', REQUEST_ID)
    runtime.stages = ['ocr']
    payload = runtime.catalog.retrieve(['Hemograma completo'])
    payload['exams'][0][field] = CANARIES[0]

    async def remote(*args, **kwargs):
        return payload

    monkeypatch.setattr(runtime_module, 'mcp_call', remote)
    outputs = []
    async def run():
        outputs.append(await runtime.step('retrieve', {'names': ['Hemograma completo']}))
    with pytest.raises(SafeError) as error:
        asyncio.run(run())
    assert error.value.code == 'EXAM_EVIDENCE_MISMATCH'
    assert outputs == [] and runtime.stages == ['ocr', 'retrieve']
    assert_private(''.join(traceback.format_exception(error.value)))


def test_retrieval_drops_remote_metadata_and_copies_canonical_rows(monkeypatch):
    runtime = runtime_module.Runtime('request.png', REQUEST_ID)
    runtime.stages = ['ocr']
    payload = runtime.catalog.retrieve(['Hemograma completo'])
    payload['patient'] = CANARIES[0]
    async def remote(*args, **kwargs):
        return payload
    monkeypatch.setattr(runtime_module, 'mcp_call', remote)
    output = asyncio.run(runtime.step('retrieve', {'names': ['Hemograma completo']}))
    payload['exams'][0]['evidence'] = CANARIES[1]
    assert_private(output)
    assert output['exams'][0] == {key: runtime.catalog.by_code['FICT-001'][key]
                                  for key in ('name', 'code', 'evidence')}


@pytest.mark.parametrize('failure', [RuntimeError, TimeoutError])
def test_node_exception_does_not_publish_remote_message(failure, monkeypatch):
    async def broken(*args, **kwargs):
        raise failure(CANARIES[0])
    monkeypatch.setattr(runtime_module, 'mcp_call', broken)
    with pytest.raises(SafeError) as error:
        asyncio.run(runtime_module.Runtime('request.png', REQUEST_ID).step('ocr', None))
    assert error.value.code == 'WORKFLOW_FAILED_SAFE'
    assert_private(str(error.value), ''.join(traceback.format_exception(error.value)))


@pytest.mark.parametrize('mode', ['success', 'forged_rag', 'sdk_error', 'sdk_timeout'])
def test_adk_session_and_outputs_are_private_before_return(mode, monkeypatch, tmp_path):
    from google.adk.sessions import InMemorySessionService
    from clinic_adk.cli import execute, safe_failure_code
    captured, calls = [], []
    class CapturingSessions(InMemorySessionService):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured.append(self)
    monkeypatch.setattr('google.adk.sessions.InMemorySessionService', CapturingSessions)
    catalog = Catalog()
    async def tools(provider, arguments, **kwargs):
        calls.append((provider, copy.deepcopy(arguments)))
        if provider == 'ocr':
            if mode == 'sdk_error':
                raise RuntimeError(CANARIES[0])
            if mode == 'sdk_timeout':
                raise TimeoutError(CANARIES[0])
            return sanitize_ocr(headers_text() + '\nExame: Hemograma completo', catalog)
        result = catalog.retrieve(arguments['exam_names'])
        if mode == 'forged_rag':
            result['exams'][0]['evidence'] = CANARIES[0]
        return result
    posts = []
    async def book(self, body):
        posts.append(body)
        return {**body, 'appointment_id': '00000000-0000-4000-8000-000000000708', 'status': 'REQUESTED'}
    monkeypatch.setattr(runtime_module, 'mcp_call', tools)
    monkeypatch.setattr(runtime_module.Runtime, 'book', book)
    spec = parse_spec(Path('/app/examples/agent.json').read_bytes())
    source = tmp_path / 'agent.py'
    source.write_text(emit(spec))
    async def run():
        try:
            result = await execute(spec, source, 'request.png', REQUEST_ID)
        except Exception as error:
            result = {'code': safe_failure_code(error), 'trace': ''.join(traceback.format_exception(error))}
        session = await captured[0].get_session(app_name='clinic_lab', user_id='fictional_demo', session_id=REQUEST_ID)
        return result, session.model_dump(mode='json')
    result, session = asyncio.run(run())
    assert_private(result, session, calls, posts)
    assert len(posts) == (1 if mode == 'success' else 0)
    if mode == 'success':
        assert posts[0]['request_id'] == REQUEST_ID
        assert posts[0]['exam_codes'] == ['FICT-001']
    else:
        assert result['code'] in ('EXAM_EVIDENCE_MISMATCH', 'WORKFLOW_FAILED_SAFE')


@pytest.mark.parametrize('field', ['request_id', 'exam_codes', 'catalog_version', 'extra_key'])
def test_api_validation_never_reflects_input_or_persists_it(field, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from clinic_adk import api
    monkeypatch.setattr(api, 'DB', str(tmp_path / 'ledger.sqlite3'))
    body = {'request_id': REQUEST_ID, 'exam_codes': ['FICT-001'], 'catalog_version': api.catalog.version}
    if field == 'extra_key':
        body[CANARIES[0]] = dict(zip(HEADERS, CANARIES))
    else:
        body[field] = list(CANARIES) if field == 'exam_codes' else CANARIES[0]
    response = TestClient(api.app, raise_server_exceptions=False).post('/appointments', json=body)
    assert response.status_code == 422
    assert_private(response.json())
    assert not Path(api.DB).exists()


def test_api_ledger_error_and_minimal_success_are_private(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from clinic_adk import api
    monkeypatch.setattr(api, 'DB', str(tmp_path / 'ledger.sqlite3'))
    body = {'request_id': REQUEST_ID, 'exam_codes': ['FICT-001'], 'catalog_version': api.catalog.version}
    client = TestClient(api.app, raise_server_exceptions=False)
    receipt = client.post('/appointments', json=body)
    replay = client.post('/appointments', json=body)
    assert receipt.status_code == 201 and replay.status_code == 200
    assert receipt.json() == replay.json()
    assert receipt.json()['request_id'] == REQUEST_ID
    assert receipt.json()['exam_codes'] == body['exam_codes']
    with sqlite3.connect(api.DB) as connection:
        rows = connection.execute('select request_id,digest,result from appointments').fetchall()
    assert len(rows) == 1
    assert_private(receipt.json(), rows)
    def unavailable():
        raise sqlite3.OperationalError(CANARIES[0])
    monkeypatch.setattr(api, 'connect', unavailable)
    response = client.post('/appointments', json=body)
    assert response.status_code == 503
    assert_private(response.json())


def test_api_partial_body_timeout_does_not_echo_canary():
    from clinic_adk.envelope import BoundedJSON
    responses, calls = [], []
    async def inner(*args):
        calls.append(True)
    index = 0
    async def receive():
        nonlocal index
        index += 1
        if index == 1:
            return {'type': 'http.request', 'body': CANARIES[0].encode(), 'more_body': True}
        raise TimeoutError(CANARIES[1])
    async def send(message):
        responses.append(message)
    scope = {'type': 'http', 'method': 'POST', 'path': '/appointments', 'headers': []}
    asyncio.run(BoundedJSON(inner)(scope, receive, send))
    assert responses[0]['status'] == 408 and calls == []
    assert_private(responses[-1]['body'].decode())


def test_diagnostics_do_not_format_any_record_field():
    from clinic_adk.safe_logging import SafeHandler
    record = logging.LogRecord('untrusted', logging.ERROR, CANARIES[0], 1,
                               '%s', (CANARIES[1],), (RuntimeError, RuntimeError(CANARIES[2]), None))
    record.stack_info = CANARIES[3]
    record.patient = CANARIES[4]
    output = io.StringIO()
    with redirect_stdout(output):
        SafeHandler().emit(record)
    assert json.loads(output.getvalue()) == {'event': 'sdk_diagnostic', 'level': 'WARNING'}
    assert_private(output.getvalue())


def test_cli_invalid_arguments_are_not_reflected():
    result = subprocess.run(['python', '-m', 'clinic_adk.cli', '--' + CANARIES[0]],
                            capture_output=True, timeout=15, check=False)
    assert result.returncode == 2
    assert_private(result.stdout.decode(), result.stderr.decode())


def test_cli_transpile_does_not_reflect_sensitive_filename(monkeypatch, tmp_path, capsys):
    from clinic_adk import cli
    spec = parse_spec(Path('/app/examples/agent.json').read_bytes())
    target = tmp_path / (CANARIES[0] + '.py')
    monkeypatch.setattr(cli, 'read_spec', lambda _: spec)
    monkeypatch.setattr(cli, 'artifact_path', lambda _: target)
    monkeypatch.setattr('sys.argv', ['clinic', 'transpile', '--output', target.name])
    assert cli.main() == 0 and target.read_text() == emit(spec)
    output = capsys.readouterr()
    assert_private(output.out, output.err)
    assert json.loads(output.out)['generated'] is True
