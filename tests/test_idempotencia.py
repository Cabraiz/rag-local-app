"""Idempotency-Key no POST /appointments e o log JSON por requisição da API."""
import asyncio
import contextlib
import hashlib
import json
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import cli
from tests.test_api import api_module, client  # noqa: F401 (fixture)
from tests.test_transpiler import (  # noqa: F401 (ready_run is a fixture)
    SPEC_FILE,
    UNAVAILABLE,
    FakeContext,
    FakeTool,
    generated_module,
    ocr_reply,
    ready_run,
    search,
)
from transpiler import transpile

BODY = {'exams': [{'code': 'FICT-001', 'name': 'Hemograma'}]}
OTHER = {'exams': [{'code': 'FICT-002', 'name': 'Glicemia'}]}


def rows(table):
    with contextlib.closing(sqlite3.connect(api_module().app.state.db_path)) as connection:
        return connection.execute(f'SELECT * FROM {table}').fetchall()


def test_same_key_and_body_returns_the_same_appointment(client):
    first = client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'pedido-42'})
    again = client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'pedido-42'})
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert len(rows('appointments')) == 1


def test_same_key_with_another_body_is_409(client):
    assert client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'pedido-42'}).status_code == 201
    response = client.post('/appointments', json=OTHER, headers={'Idempotency-Key': 'pedido-42'})
    assert response.status_code == 409
    assert response.json() == {'detail': 'Esta Idempotency-Key já foi usada com outro corpo. '
                                         'Para um novo agendamento, use uma chave nova.'}
    assert len(rows('appointments')) == 1


def test_without_a_key_every_post_creates_an_appointment(client):
    ids = {client.post('/appointments', json=BODY).json()['id'] for _ in range(2)}
    assert len(ids) == 2 and rows('idempotency') == []


@pytest.mark.parametrize('serialized', [False, True])
def test_twenty_simultaneous_posts_with_one_key_create_one_appointment(client, monkeypatch, serialized):
    # serialized=True drops the in-process lock: SQLite's write transaction alone must hold,
    # as it would across API processes.
    if serialized:
        monkeypatch.setattr(api_module(), 'WRITE_LOCK', contextlib.nullcontext())
    def post(_):
        return client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'retry'})

    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(post, range(20)))
    assert [r.status_code for r in responses] == [201] * 20
    assert len({r.json()['id'] for r in responses}) == 1
    assert len(rows('appointments')) == 1


def test_the_body_is_stored_only_as_a_keyed_hash(client):
    client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'pedido-42'})
    [(key, body_hash, _)] = rows('idempotency')
    assert key == 'pedido-42' and 'FICT' not in body_hash and 'Hemograma' not in body_hash
    plain = hashlib.sha256(json.dumps(BODY, sort_keys=True).encode('utf-8')).hexdigest()
    assert body_hash != plain  # an HMAC with the database key, not a guessable hash


@pytest.mark.parametrize('key', ['', 'com espaço', 'x' * 129, 'acentuação'])
def test_invalid_keys_are_422_in_portuguese_without_the_value(client, key):
    response = client.post('/appointments', json=BODY, headers={'Idempotency-Key': key.encode('utf-8')})
    assert response.status_code == 422 and rows('appointments') == []
    [item] = response.json()['detail']
    assert item['loc'] == ['header', 'Idempotency-Key'] and set(item) == {'loc', 'msg', 'type'}
    assert item['msg'] == 'Idempotency-Key inválida: use de 1 a 128 caracteres ASCII visíveis, sem espaço nem acento.'
    sent = key.encode('utf-8').decode('latin-1')  # how the header arrives
    assert not key or (key not in response.text and sent not in response.text)


def test_the_key_is_documented_and_the_agent_still_reads_the_contract(client, tmp_path, monkeypatch):
    schema = client.get('/openapi.json').json()
    post = schema['paths']['/appointments']['post']
    [header] = post['parameters']
    assert header['name'] == 'Idempotency-Key' and header['in'] == 'header' and not header.get('required')
    assert header['description'] and '409' in post['responses']
    # The generated agent's OpenAPIToolset, fed this API's real /openapi.json.
    real_client = httpx.AsyncClient
    def serve(request):
        return httpx.Response(200, json=schema)

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(serve), **kw))
    toolset = transpile(SPEC_FILE, tmp_path / 'agent.py').sub_agents[2].tools[0]
    [tool] = asyncio.run(toolset.get_tools())
    parameters = tool._get_declaration().parameters_json_schema
    assert tool.name == 'create_appointment' and parameters['required'] == ['exams']
    assert set(parameters['properties']) == {'exams', 'idempotency_key'}


def test_the_agent_sends_one_key_of_its_own_per_run_never_the_models(tmp_path):
    # The model's key could repeat across orders or carry text from the order; the run's own key
    # (a random uuid, the same for every call of the run) makes a repeated POST come back as the same
    # appointment from the API.
    agent, context = generated_module(tmp_path), FakeContext()
    agent.CALLBACKS.after_tool(FakeTool('extract_exam_text'), {}, context, ocr_reply('1. Hemograma completo'))
    search(agent, context, 'Hemograma', ('FICT-001', 'Hemograma completo', 1.0))
    keys = []
    for _ in range(2):
        args = {'exams': [{'code': 'FICT-001', 'name': 'Hemograma'}], 'idempotency_key': 'pedido-Maria'}
        assert agent.CALLBACKS.before_tool(FakeTool('create_appointment'), args, context) is None
        assert args['exams'] == [{'code': 'FICT-001', 'name': 'Hemograma'}]
        keys.append(args['idempotency_key'])
    assert keys[0] == keys[1] and re.fullmatch(r'[0-9a-f]{32}', keys[0])
    other = FakeContext()  # another run, another key
    agent.CALLBACKS.after_tool(FakeTool('extract_exam_text'), {}, other, ocr_reply('1. Hemograma completo'))
    search(agent, other, 'Hemograma', ('FICT-001', 'Hemograma completo', 1.0))
    args = {'exams': [{'code': 'FICT-001', 'name': 'Hemograma'}]}
    agent.CALLBACKS.before_tool(FakeTool('create_appointment'), args, other)
    assert args['idempotency_key'] != keys[0]


@pytest.mark.parametrize('second_body', ['the same', 'another'])
def test_after_the_first_appointment_a_new_call_never_reaches_the_api(tmp_path, second_body):
    agent, context = generated_module(tmp_path), FakeContext()
    agent.CALLBACKS.after_tool(FakeTool('extract_exam_text'), {}, context, ocr_reply('- Hemograma completo', '- Creatinina'))
    search(agent, context, 'Hemograma', ('FICT-001', 'Hemograma completo', 1.0))
    search(agent, context, 'Creatinina', ('FICT-005', 'Creatinina', 1.0))
    first = {'exams': [{'code': 'FICT-001', 'name': 'Hemograma'}]}
    assert agent.CALLBACKS.before_tool(FakeTool('create_appointment'), first, context) is None
    created = {'id': 'a1', 'status': 'scheduled', 'exams': first['exams']}
    agent.CALLBACKS.after_tool(FakeTool('create_appointment'), first, context, created)  # the API's 201
    codes = ['FICT-001'] if second_body == 'the same' else ['FICT-001', 'FICT-005']
    second = {'exams': [{'code': code, 'name': code} for code in codes]}
    # the same appointment comes back as the tool's reply, and nothing is sent
    assert agent.CALLBACKS.before_tool(FakeTool('create_appointment'), second, context) == created


@pytest.fixture
def access_log(caplog):
    # cli.main() turns logging off for its whole process; the API runs in a process of its own.
    disabled = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    logger = logging.getLogger('api.access')
    logger.addHandler(caplog.handler)
    yield lambda: [json.loads(record.getMessage()) for record in caplog.records if record.name == 'api.access']
    logger.removeHandler(caplog.handler)
    logging.disable(disabled)


def test_one_json_line_per_request_without_body_or_key(client, access_log):
    secret = {'exams': [{'code': 'FICT-001', 'name': 'Maria Sentinela 123.456.789-00'}]}
    created = client.post('/appointments', json=secret, headers={'Idempotency-Key': 'chave-secreta',
                                                                  'X-Request-ID': 'abc-123'})
    client.get(f"/appointments/{created.json()['id']}")
    client.get('/nao/existe/Maria')
    client.get('/openapi.json')
    client.post('/appointments', content=b'x' * 20_000, headers={'content-type': 'application/json'})
    lines = access_log()
    assert created.headers['x-request-id'] == 'abc-123'
    assert [(line['method'], line['route'], line['status']) for line in lines] == [
        ('POST', '/appointments', 201), ('GET', '/appointments/{appointment_id}', 200),
        ('GET', '(sem rota)', 404), ('GET', '/openapi.json', 200), ('POST', '(sem rota)', 413)]
    assert all(set(line) == {'request_id', 'method', 'route', 'status', 'duration_ms'} for line in lines)
    assert lines[0]['request_id'] == 'abc-123' and len({line['request_id'] for line in lines}) == 5
    text = json.dumps(lines)
    assert 'Sentinela' not in text and 'chave-secreta' not in text and 'Maria' not in text


def test_a_forged_request_id_is_replaced(client, access_log):
    sent = [client.get('/health', headers={'X-Request-ID': forged}).headers['x-request-id']
            for forged in ('x", "status": 200', 'a' * 65)]
    assert [line['request_id'] for line in access_log()] == sent
    assert all(len(request_id) == 32 for request_id in sent)  # new uuid4().hex values


def test_a_409_on_the_fallback_run_warns_that_the_first_post_may_have_booked(ready_run, monkeypatch, capsys):
    # The first run's POST went out but its reply was lost (Gemini failed); the fallback model proposes
    # other exams, so the run's key comes back from the API as a 409.
    monkeypatch.setenv('GEMINI_MODEL', 'gemini-test-main')  # the fallback changes it; restored after
    runs = []

    async def lost_reply_then_409(root_agent, image, spec, found):
        runs.append(dict(found))
        if len(runs) == 1:
            found['idempotency_key'] = 'k' * 32
            raise RuntimeError('agent failed') from UNAVAILABLE
        found['api_called'], found['api_error'] = True, 'HTTP 409: {"detail": "Esta Idempotency-Key já foi usada"}'
        found['candidates'] = {'FICT-001': {}}

    monkeypatch.setattr(cli, 'run_agent', lost_reply_then_409)
    assert cli.main(ready_run) == 2
    assert runs[1]['idempotency_key'] == 'k' * 32  # the fallback run kept the first run's key
    assert 'um agendamento da 1ª tentativa pode já ter sido criado, confira antes de repetir' in capsys.readouterr().err
