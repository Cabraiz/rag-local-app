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
from fastapi.testclient import TestClient

from tests.test_api import api_module, client, configure  # noqa: F401 (fixture)
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
BOTH = {'exams': [{'code': 'FICT-001'}, {'code': 'FICT-002'}]}


def rows(table):
    with contextlib.closing(sqlite3.connect(api_module().app.state.db_path)) as connection:
        return connection.execute(f'SELECT * FROM {table}').fetchall()


def test_same_key_and_body_returns_the_same_appointment(client):
    first = client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'pedido-42'})
    again = client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'pedido-42'})
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json()
    assert len(rows('appointments')) == 1


def test_same_key_with_an_exam_already_booked_in_another_list_is_409(client):
    assert client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'pedido-42'}).status_code == 201
    response = client.post('/appointments', json=BOTH, headers={'Idempotency-Key': 'pedido-42'})
    assert response.status_code == 409
    assert response.json() == {'detail': 'Esta Idempotency-Key já agendou um destes exames, com outra lista. '
                                         'Para um novo agendamento, use uma chave nova.'}
    assert len(rows('appointments')) == 1


def test_without_a_key_every_post_creates_an_appointment(client):
    ids = {client.post('/appointments', json=BODY).json()['id'] for _ in range(2)}
    assert len(ids) == 2 and rows('idempotency_keys') == []


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


def test_the_key_and_the_request_are_stored_only_as_keyed_hashes(client):
    client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'pedido-Maria-123.456.789-00'})
    [(key_hash, request_hash, _, created_at)] = rows('idempotency_keys')  # one row per exam
    for stored in (key_hash, request_hash):
        assert re.fullmatch(r'[0-9a-f]{64}', stored)
    plain_key = hashlib.sha256(b'pedido-Maria-123.456.789-00').hexdigest()
    plain_codes = hashlib.sha256(json.dumps(['FICT-001']).encode('utf-8')).hexdigest()
    assert key_hash != plain_key and request_hash != plain_codes  # HMACs with the database key, not guessable hashes
    assert isinstance(created_at, int)
    assert not any(b'Maria' in path.read_bytes() for path in client.app.state.db_path.parent.glob('appointments.db*'))


def test_a_reworded_name_or_another_order_with_the_same_codes_is_a_replay(client):
    both = {'exams': [{'code': 'FICT-001', 'name': 'Hemograma'}, {'code': 'FICT-002', 'name': 'Glicemia'}]}
    reworded = {'exams': [{'code': 'FICT-002', 'name': 'glicose em jejum'}, {'code': 'FICT-001'}]}
    first = client.post('/appointments', json=both, headers={'Idempotency-Key': 'pedido-7'})
    again = client.post('/appointments', json=reworded, headers={'Idempotency-Key': 'pedido-7'})
    assert first.status_code == again.status_code == 201 and again.json() == first.json()
    assert [exam['name'] for exam in again.json()['exams']] == ['Hemograma completo', 'Glicemia de jejum']
    assert len(rows('appointments')) == 1


def test_a_key_books_each_exam_once_whatever_the_list_around_it(client):
    # A retry of the same order (the same key) never books an exam twice: a list with an exam the key already
    # booked, in another list, is 409, even with the same names; an exam it never booked is booked once.
    def post(body):
        return client.post('/appointments', json=body, headers={'Idempotency-Key': 'pedido-8'})

    first = post(BODY).json()
    assert post({'exams': [{'code': 'FICT-001', 'name': 'Hemograma'}, {'code': 'FICT-002'}]}).status_code == 409
    second = post(OTHER)  # FICT-002, never booked with this key
    assert second.status_code == 201 and [exam['code'] for exam in second.json()['exams']] == ['FICT-002']
    assert post(BOTH).status_code == 409  # both are booked, each in its own list
    assert post(BODY).json() == first and post(OTHER).json() == second.json()  # each list replays
    assert len(rows('appointments')) == 2 and len(rows('idempotency_keys')) == 2


def test_a_key_expires_after_the_ttl_and_can_be_used_again(client, monkeypatch):
    clock = [1_800_000_000]
    monkeypatch.setattr(api_module(), 'now', lambda: clock[0])
    first = client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'pedido-9'}).json()
    clock[0] += 24 * 3600 - 1  # still within the default 24 h: the same appointment
    assert client.post('/appointments', json=BOTH, headers={'Idempotency-Key': 'pedido-9'}).status_code == 409
    assert client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'pedido-9'}).json() == first
    clock[0] += 1  # 24 h later: forgotten, so the key books anew (here other exams)
    renewed = client.post('/appointments', json=OTHER, headers={'Idempotency-Key': 'pedido-9'})
    assert renewed.status_code == 201 and renewed.json()['id'] != first['id']
    assert [row[2] for row in rows('idempotency_keys')] == [renewed.json()['id']]  # the expired row is gone
    assert client.get(f"/appointments/{first['id']}").json() == first  # the appointment itself stays


def test_the_ttl_is_configurable_in_hours(tmp_path, monkeypatch):
    monkeypatch.setenv('API_IDEMPOTENCY_TTL_HOURS', '1')
    clock = [1_800_000_000]
    monkeypatch.setattr(api_module(), 'now', lambda: clock[0])
    with TestClient(configure(tmp_path, monkeypatch)) as client:
        first = client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'k'}).json()
        clock[0] += 3599
        assert client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'k'}).json() == first
        clock[0] += 1
        assert client.post('/appointments', json=BODY, headers={'Idempotency-Key': 'k'}).json()['id'] != first['id']


@pytest.mark.parametrize('value', ['0', '-1', '1.5', 'um dia'])
def test_an_invalid_ttl_stops_the_start(tmp_path, monkeypatch, value):
    monkeypatch.setenv('API_IDEMPOTENCY_TTL_HOURS', value)
    with pytest.raises(api_module().ConfigError) as stop, TestClient(configure(tmp_path, monkeypatch)):
        pass
    assert str(stop.value) == 'API_IDEMPOTENCY_TTL_HOURS inválido: use um número inteiro de horas, a partir de 1.'


def test_the_clear_keys_of_an_earlier_version_are_erased_at_startup(tmp_path, monkeypatch):
    app = configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        booked = client.post('/appointments', json=BODY).json()
    with contextlib.closing(sqlite3.connect(app.state.db_path)) as connection, connection:
        connection.execute('CREATE TABLE idempotency (key TEXT PRIMARY KEY, body_hash TEXT NOT NULL, '
                           'appointment_id TEXT NOT NULL)')
        connection.execute('INSERT INTO idempotency VALUES (?, ?, ?)', ('pedido-Maria-antigo', 'f' * 64, booked['id']))
    with TestClient(app) as client:
        with contextlib.closing(sqlite3.connect(app.state.db_path)) as connection:
            assert connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'idempotency'").fetchone() is None
        assert client.get(f"/appointments/{booked['id']}").json() == booked  # the appointment stays
    files = [path.read_bytes() for path in app.state.db_path.parent.glob('appointments.db*')]
    assert files and not any(b'pedido-Maria-antigo' in data for data in files)  # secure_delete


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
        assert args['exams'] == [{'code': 'FICT-001'}]  # the API names it from its catalog
        keys.append(args['idempotency_key'])
    assert keys[0] == keys[1] and re.fullmatch(r'[0-9a-f]{32}', keys[0])
    other = FakeContext()  # another run, another key
    agent.CALLBACKS.after_tool(FakeTool('extract_exam_text'), {}, other, ocr_reply('1. Hemograma completo'))
    search(agent, other, 'Hemograma', ('FICT-001', 'Hemograma completo', 1.0))
    args = {'exams': [{'code': 'FICT-001', 'name': 'Hemograma'}]}
    agent.CALLBACKS.before_tool(FakeTool('create_appointment'), args, other)
    assert args['idempotency_key'] != keys[0]


def test_the_booking_body_has_only_the_codes_whatever_the_model_adds(client, tmp_path):
    # Fields the API forbids (extra='forbid'), also nested in an exam, would fail the whole order with a 422:
    # the runtime sends {code} alone, and the API answers with the catalog's names.
    agent, context = generated_module(tmp_path), FakeContext()
    agent.CALLBACKS.after_tool(FakeTool('extract_exam_text'), {}, context, ocr_reply('1. Hemograma completo'))
    search(agent, context, 'Hemograma', ('FICT-001', 'Hemograma completo', 1.0))
    proposed = [{'code': 'FICT-001', 'name': 'Maria ' * 40, 'notes': {'paciente': 'Maria'}, 'extra': [1]}]
    assert client.post('/appointments', json={'exams': proposed}).status_code == 422  # what the model's body would get
    args = {'exams': proposed}
    assert agent.CALLBACKS.before_tool(FakeTool('create_appointment'), args, context) is None
    assert args['exams'] == [{'code': 'FICT-001'}]
    created = client.post('/appointments', json={'exams': args['exams']}, headers={'Idempotency-Key': args['idempotency_key']})
    assert created.status_code == 201 and created.json()['exams'] == [{'code': 'FICT-001', 'name': 'Hemograma completo'}]


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
                                                                  'X-Request-ID': 'pedido-Maria-123'})
    client.get(f"/appointments/{created.json()['id']}")
    client.get('/nao/existe/Maria')
    client.get('/openapi.json')
    client.post('/appointments', content=b'x' * 20_000, headers={'content-type': 'application/json'})
    lines = access_log()
    assert re.fullmatch(r'[0-9a-f]{32}', created.headers['x-request-id'])  # ours, never the client's
    assert [(line['method'], line['route'], line['status']) for line in lines] == [
        ('POST', '/appointments', 201), ('GET', '/appointments/{appointment_id}', 200),
        ('GET', '(sem rota)', 404), ('GET', '/openapi.json', 200), ('POST', '(sem rota)', 413)]
    assert all(set(line) == {'request_id', 'method', 'route', 'status', 'duration_ms'} for line in lines)
    assert lines[0]['request_id'] == created.headers['x-request-id'] and len({line['request_id'] for line in lines}) == 5
    text = json.dumps(lines)
    assert 'Sentinela' not in text and 'chave-secreta' not in text and 'Maria' not in text


def test_a_client_request_id_or_method_never_reaches_the_log(client, access_log):
    sent = [client.get('/health', headers={'X-Request-ID': given}).headers['x-request-id']
            for given in ('x", "status": 200', 'a' * 65, 'Maria-Silva', 'abc-123')]
    refused = client.request('MARIASILVA', '/health')
    lines = access_log()
    assert [line['request_id'] for line in lines] == [*sent, refused.headers['x-request-id']]
    assert all(re.fullmatch(r'[0-9a-f]{32}', request_id) for request_id in sent)  # new uuid4().hex values
    assert lines[-1]['method'] == '(outro)' and 'Maria' not in json.dumps(lines) and 'abc-123' not in json.dumps(lines)
