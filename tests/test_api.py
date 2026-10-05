import json
import re
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

CATALOG = [{'code': 'FICT-001', 'name': 'Hemograma completo'},
           {'code': 'FICT-002', 'name': 'Glicemia de jejum'}]


def configure(tmp_path, monkeypatch):
    """The settings the API reads when it starts (not on import); returns its app."""
    catalog = tmp_path / 'exams.json'
    catalog.write_text(json.dumps(CATALOG), encoding='utf-8')
    monkeypatch.setenv('EXAMS_PATH', str(catalog))
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'appointments.db'))
    from api.crypto import new_key
    monkeypatch.setenv('DB_ENCRYPTION_KEY', new_key())
    return api_module().app


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(configure(tmp_path, monkeypatch)) as test_client:
        yield test_client


def test_health(client):
    assert client.get('/health').json() == {'status': 'ok', 'catalog_size': 2}


def test_create_then_read_appointment(client):
    created = client.post('/appointments', json={'exams': [{'code': 'FICT-001', 'name': 'hemograma'}]})
    assert created.status_code == 201
    body = created.json()
    assert body['status'] == 'scheduled'
    # The stored name is the catalog's official name, not the text read by OCR.
    assert body['exams'] == [{'code': 'FICT-001', 'name': 'Hemograma completo'}]
    assert client.get(f"/appointments/{body['id']}").json() == body


def test_unknown_code_is_rejected_with_a_clear_message(client):
    response = client.post('/appointments', json={'exams': [{'code': 'FICT-999', 'name': 'Inexistente'}]})
    assert response.status_code == 422
    assert response.json() == {'detail': [{'loc': ['body', 'exams', 0, 'code'], 'type': 'unknown_exam_code',
                                           'msg': 'Código de exame desconhecido no catálogo.'}]}
    assert '999' not in response.text  # the item is located by `loc`, the value is not echoed
    # The 422 documented in /openapi.json describes this exact body.
    schema = client.get('/openapi.json').json()
    for path, method in (('/appointments', 'post'), ('/appointments/{appointment_id}', 'get')):
        ref = schema['paths'][path][method]['responses']['422']['content']['application/json']['schema']['$ref']
        assert ref.endswith('/ValidationErrors')
    item = schema['components']['schemas']['ErrorItem']
    assert set(item['required']) == set(response.json()['detail'][0])


@pytest.mark.parametrize('body', [
    {'exams': []},
    {'exams': [{'code': 'ABC-1', 'name': 'Hemograma completo'}]},
    {'exams': [{'name': 'Hemograma completo'}]},
    {'exams': [{'code': 'FICT-001', 'name': ''}]},
    {'exams': [{'code': 'FICT-001', 'name': 'Hemograma completo', 'cpf': '123.456.789-00'}]},
    {'exams': [{'code': 'FICT-001', 'name': 'Hemograma completo'}], 'patient': 'Maria'},
])
def test_invalid_body_is_rejected(client, body):
    assert client.post('/appointments', json=body).status_code == 422


def test_the_name_is_optional_and_the_contract_says_the_catalog_name_is_stored(client):
    created = client.post('/appointments', json={'exams': [{'code': 'FICT-002'}, {'code': 'FICT-001', 'name': None}]})
    assert created.status_code == 201
    assert created.json()['exams'] == [{'code': 'FICT-002', 'name': 'Glicemia de jejum'},
                                       {'code': 'FICT-001', 'name': 'Hemograma completo'}]
    schema = client.get('/openapi.json').json()
    requested = schema['components']['schemas']['RequestedExam']
    assert requested['required'] == ['code'] and 'nome oficial do catálogo' in requested['properties']['name']['description']
    assert 'o `name` é opcional' in schema['paths']['/appointments']['post']['description']


def test_missing_appointment_is_404(client):
    response = client.get('/appointments/00000000-0000-0000-0000-000000000000')
    assert response.status_code == 404
    assert response.json() == {'detail': 'Agendamento não encontrado.'}


def test_appointments_survive_a_restart(tmp_path, monkeypatch):
    app = configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        created = client.post('/appointments', json={'exams': [{'code': 'FICT-002', 'name': 'Glicemia'}]}).json()
    with TestClient(app) as restarted:
        assert restarted.get(f"/appointments/{created['id']}").json() == created


def test_swagger_describes_every_route_and_field(client):
    schema = client.get('/openapi.json').json()
    for path, operations in schema['paths'].items():
        for method, operation in operations.items():
            assert operation['summary'] and operation['description'], (method, path)
            # Every documented response, errors included, carries a JSON schema.
            for status, response in operation['responses'].items():
                assert response['content']['application/json']['schema'], (method, path, status)
    errors = schema['paths']['/appointments']['post']['responses']
    assert {'413', '422'} <= set(errors)
    assert {'404', '422'} <= set(schema['paths']['/appointments/{appointment_id}']['get']['responses'])
    for model in ('RequestedExam', 'Exam', 'AppointmentRequest', 'Appointment'):
        for name, field in schema['components']['schemas'][model]['properties'].items():
            assert field.get('description'), (model, name)
    assert client.get('/docs').status_code == 200


def examples(schema):
    """{(path, method, status): the `detail` of the example /docs shows} for the errors with one text."""
    return {(path, method, status): response['content']['application/json']['example']['detail']
            for path, operations in schema['paths'].items() for method, operation in operations.items()
            for status, response in operation['responses'].items() if status not in ('200', '201', '422')}


def test_each_documented_error_shows_the_text_the_api_really_returns(tmp_path, monkeypatch):
    # An independent run-through: every Message example in /docs showed the 404's text, also for 409 and 429.
    with limited_client(tmp_path, monkeypatch, '1000') as client:
        shown = examples(client.get('/openapi.json').json())
        created = client.post('/appointments', json=ONE_EXAM, headers={'Idempotency-Key': 'k1'}).json()
        returned = {
            '400': client.get('/health', headers={'Host': 'evil.example'}),
            '404': client.get('/appointments/00000000-0000-0000-0000-000000000000'),
            '409': client.post('/appointments', json={'exams': [{'code': 'FICT-001'}, {'code': 'FICT-002'}]},
                               headers={'Idempotency-Key': 'k1'}),
            '413': client.post('/appointments', content='x' * 20_000, headers={'content-type': 'application/json'}),
        }
        with closing(sqlite3.connect(client.app.state.db_path)) as connection, connection:  # a row edited in the db
            connection.execute("UPDATE appointments SET exams = 'alterado' WHERE id = ?", (created['id'],))
        returned['500'] = client.get(f"/appointments/{created['id']}")
    with limited_client(tmp_path, monkeypatch, '1') as client:
        client.get('/openapi.json')
        returned['429'] = client.get('/openapi.json')
    detail = {status: response.json()['detail'] for status, response in returned.items()
              if response.status_code == int(status)}
    assert set(detail) == {'400', '404', '409', '413', '429', '500'}
    assert {status for _, _, status in shown} == set(detail)
    for (path, method, status), example in shown.items():
        # the 429's wait varies; the text around it does not
        assert re.sub(r'\d+ s\.', 'N s.', example) == re.sub(r'\d+ s\.', 'N s.', detail[status]), (path, method, status)


# --- Security: hostile input never breaks the API, leaks data or reaches SQL unparameterized.

INJECTION = "x'); DROP TABLE appointments; --"
SECRET = 'Maria Sentinela 123.456.789-00'


def test_sql_injection_is_rejected_or_stored_as_plain_data(client):
    kept = client.post('/appointments', json={'exams': [{'code': 'FICT-001', 'name': 'Hemograma'}]}).json()
    assert client.post('/appointments', json={'exams': [{'code': INJECTION, 'name': 'x'}]}).status_code == 422
    created = client.post('/appointments', json={'exams': [{'code': 'FICT-002', 'name': INJECTION}]})
    assert created.status_code == 201 and INJECTION not in created.text
    assert client.get("/appointments/x' OR '1'='1").status_code == 422
    # The table survived and the earlier row is intact.
    assert client.get(f"/appointments/{kept['id']}").json() == kept


def test_oversized_body_is_413(client):
    body = json.dumps({'exams': [{'code': 'FICT-001', 'name': 'x' * 20_000}]})
    response = client.post('/appointments', content=body, headers={'content-type': 'application/json'})
    assert response.status_code == 413


def test_oversized_chunked_body_without_content_length_is_413(client):
    def chunks():  # sent with Transfer-Encoding: chunked, so there is no Content-Length to trust
        for _ in range(32):
            yield b'x' * 1024

    response = client.post('/appointments', content=chunks(), headers={'content-type': 'application/json'})
    assert response.status_code == 413


def test_fifty_parallel_posts_are_all_created(client):
    from concurrent.futures import ThreadPoolExecutor
    body = {'exams': [{'code': 'FICT-001', 'name': 'Hemograma'}]}
    with ThreadPoolExecutor(max_workers=50) as pool:
        statuses = list(pool.map(lambda _: client.post('/appointments', json=body).status_code, range(50)))
    assert statuses == [201] * 50


@pytest.mark.parametrize('code', ['FICT-００１', 'FICT-٠٠١'])
def test_only_ascii_digits_are_accepted_in_codes(client, code):
    response = client.post('/appointments', json={'exams': [{'code': code, 'name': 'x'}]})
    assert response.status_code == 422 and code not in response.text


@pytest.mark.parametrize('body', [
    {'exams': [{'code': 1, 'name': 'Hemograma'}]},
    {'exams': [{'code': 'FICT-001', 'name': ['Hemograma']}]},
    {'exams': {'code': 'FICT-001', 'name': 'Hemograma'}},
    {'exams': None},
    [],
    {'exams': [{'code': 'FICT-001', 'name': 'a'}, {'code': 'FICT-001', 'name': 'b'}]},
    {'exams': [{'code': 'FICT-001', 'name': 'a'}] * 21},
])
def test_wrong_types_duplicates_and_too_many_are_422(client, body):
    response = client.post('/appointments', json=body)
    assert response.status_code == 422
    assert all({'loc', 'msg', 'type'} == set(item) for item in response.json()['detail'])


SENTINEL = 'Sentinela-987'  # a value that must never come back in a message
EXAM = {'code': 'FICT-001', 'name': 'Hemograma'}


@pytest.mark.parametrize('body, loc, kind, msg', [
    ({'exams': []}, ['body', 'exams'], 'too_short', 'Deve ter pelo menos 1 item.'),
    ({'exams': [EXAM] * 21}, ['body', 'exams'], 'too_long', 'Deve ter no máximo 20 itens.'),
    ({'exams': [{**EXAM, 'cpf': SENTINEL}]}, ['body', 'exams', 0, 'cpf'], 'extra_forbidden',
     'Campo não permitido: envie só os campos documentados.'),
    ({'exams': [EXAM], 'paciente': SENTINEL}, ['body', 'paciente'], 'extra_forbidden',
     'Campo não permitido: envie só os campos documentados.'),
    ({'exams': [{'name': 'Hemograma'}]}, ['body', 'exams', 0, 'code'], 'missing', 'Campo obrigatório ausente.'),
    ({}, ['body', 'exams'], 'missing', 'Campo obrigatório ausente.'),
    ({'exams': [{'code': SENTINEL, 'name': 'a'}]}, ['body', 'exams', 0, 'code'], 'string_pattern_mismatch',
     'Código fora do formato: use FICT- e 3 dígitos, como FICT-001.'),
    ({'exams': [{'code': 987, 'name': 'a'}]}, ['body', 'exams', 0, 'code'], 'string_type', 'Deve ser um texto.'),
    ({'exams': [{'code': 'FICT-001', 'name': ''}]}, ['body', 'exams', 0, 'name'], 'string_too_short',
     'Deve ter pelo menos 1 caractere.'),
    ({'exams': [{'code': 'FICT-001', 'name': SENTINEL * 20}]}, ['body', 'exams', 0, 'name'], 'string_too_long',
     'Deve ter no máximo 120 caracteres.'),
    ({'exams': EXAM}, ['body', 'exams'], 'list_type', 'Deve ser uma lista.'),
    ([SENTINEL], ['body'], 'model_attributes_type', 'Deve ser um objeto JSON, como {"exams": [...]}.'),
    ({'exams': [EXAM, EXAM]}, ['body', 'exams'], 'value_error', 'Cada código de exame deve aparecer uma única vez.'),
])
def test_validation_errors_are_in_portuguese_and_never_echo_the_value(client, body, loc, kind, msg):
    response = client.post('/appointments', json=body)
    assert response.status_code == 422
    assert response.json() == {'detail': [{'loc': loc, 'type': kind, 'msg': msg}]}
    assert SENTINEL not in response.text and '987' not in response.text


def test_invalid_json_and_a_bad_id_are_in_portuguese_too(client):
    broken = client.post('/appointments', content='{"exams": [', headers={'content-type': 'application/json'})
    assert broken.json()['detail'][0]['msg'] == 'JSON inválido: confira aspas, vírgulas e chaves.'
    for wrong in ('nao-e-uuid', '987'):  # pydantic's own text quotes a character of the value
        [item] = client.get(f'/appointments/{wrong}').json()['detail']
        assert item == {'loc': ['path', 'appointment_id'], 'type': 'uuid_parsing',
                        'msg': 'Deve ser um UUID, como 3fa85f64-5717-4562-b3fc-2c963f66afa6.'}


def test_an_unmapped_error_type_keeps_the_original_message():
    from api.main import portuguese
    assert portuguese({'type': 'novo_tipo', 'loc': ('body', 'x'), 'msg': 'Original message'}) == 'Original message'


def test_errors_never_echo_the_submitted_values(client):
    response = client.post('/appointments', json={'exams': [{'code': 'FICT-001', 'name': 'a', 'cpf': SECRET}]})
    assert response.status_code == 422 and 'Sentinela' not in response.text and '123.456' not in response.text
    created = client.post('/appointments', json={'exams': [{'code': 'FICT-001', 'name': SECRET}]})
    assert created.status_code == 201 and 'Sentinela' not in created.text
    stored = client.app.state.db_path.read_bytes()
    assert b'Sentinela' not in stored and b'123.456' not in stored


def test_invalid_json_and_internal_errors_have_no_stack_trace(client, monkeypatch):
    response = client.post('/appointments', content='{"exams": [', headers={'content-type': 'application/json'})
    assert response.status_code == 422 and 'Traceback' not in response.text
    monkeypatch.setattr(api_module(), 'connect', lambda _: (_ for _ in ()).throw(sqlite3.OperationalError('disk I/O')))
    broken = TestClient(client.app, raise_server_exceptions=False)  # no `with`: the started app is reused
    response = broken.post('/appointments', json={'exams': [{'code': 'FICT-001', 'name': 'a'}]})
    assert response.status_code == 500
    assert response.json() == {'detail': 'Erro interno ao processar a requisição.'}


# --- Rate limit per client: 429 with Retry-After over the limit; /health exempt; 0 turns it off.

ONE_EXAM = {'exams': [{'code': 'FICT-001', 'name': 'Hemograma'}]}


def limited_client(tmp_path, monkeypatch, per_minute):
    monkeypatch.setenv('API_RATE_LIMIT_PER_MINUTE', per_minute)
    return TestClient(configure(tmp_path, monkeypatch))


def test_over_the_limit_is_429_with_retry_after(tmp_path, monkeypatch):
    with limited_client(tmp_path, monkeypatch, '3') as client:
        created = client.post('/appointments', json=ONE_EXAM)
        assert created.status_code == 201
        assert client.get(f"/appointments/{created.json()['id']}").status_code == 200
        assert client.post('/appointments', json=ONE_EXAM).status_code == 201
        refused = client.post('/appointments', json=ONE_EXAM)
        assert refused.status_code == 429
        assert int(refused.headers['Retry-After']) >= 1
        assert refused.json() == {'detail': f"Muitas requisições deste cliente: tente de novo em {refused.headers['Retry-After']} s."}
        assert client.get(f"/appointments/{created.json()['id']}").status_code == 429  # GET counts too
        rows = sqlite3.connect(client.app.state.db_path).execute('SELECT COUNT(*) FROM appointments').fetchone()
        assert rows == (2,)  # the refused POST wrote nothing


def test_health_is_exempt_and_zero_turns_the_limit_off(tmp_path, monkeypatch):
    with limited_client(tmp_path, monkeypatch, '1') as client:
        assert client.post('/appointments', json=ONE_EXAM).status_code == 201
        assert client.post('/appointments', json=ONE_EXAM).status_code == 429
        assert [client.get('/health').status_code for _ in range(20)] == [200] * 20
    with limited_client(tmp_path, monkeypatch, '0') as client:
        assert {client.post('/appointments', json=ONE_EXAM).status_code for _ in range(30)} == {201}


def test_the_bucket_refills_with_time_and_is_per_client():
    now = [0.0]
    limiter = api_module().RateLimiter(60, clock=lambda: now[0])  # one request a second
    assert [limiter.take('a') for _ in range(60)] == [0] * 60
    assert limiter.take('a') == 1
    assert limiter.take('b') == 0  # another client has its own bucket
    now[0] += 1.0
    assert limiter.take('a') == 0
    assert limiter.take('a') == 1


def test_the_clients_kept_are_capped_and_the_least_recently_seen_goes_first(monkeypatch):
    module = api_module()
    monkeypatch.setattr(module.RateLimiter, 'MAX_CLIENTS', 3)
    limiter = module.RateLimiter(60, clock=lambda: 0.0)
    for client in ('a', 'b', 'c'):
        limiter.take(client)
    limiter.take('a')  # 'a' is now the most recent, 'b' the oldest
    for index in range(100):  # many new clients never grow the table past the cap
        limiter.take(f'x{index}')
        assert len(limiter.buckets) <= 3
    limiter.take('d')
    assert list(limiter.buckets)[-1] == 'd' and 'b' not in limiter.buckets
    # A client still in the table keeps what it spent: 'd' used 1 of its 60 tokens.
    assert limiter.buckets['d'][0] == 59


def test_an_invalid_limit_stops_the_start_with_one_clear_line(tmp_path, monkeypatch):
    module = api_module()
    with pytest.raises(module.ConfigError) as stop, limited_client(tmp_path, monkeypatch, 'muitas'):
        pass
    assert str(stop.value).startswith('API_RATE_LIMIT_PER_MINUTE inválido')
    assert not list(tmp_path.glob('appointments.db*'))


def test_429_is_in_the_openapi_contract_and_the_ip_is_not_logged(tmp_path, monkeypatch):
    import logging
    records = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record.getMessage())
    access = logging.getLogger('api.access')
    access.addHandler(handler)
    # cli.main() turns logging off for its whole process, and other tests call it before this one;
    # the API runs in a process of its own (as in test_idempotencia.py's access_log).
    disabled = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    try:
        with limited_client(tmp_path, monkeypatch, '2') as client:
            schema = client.get('/openapi.json').json()  # counts too: only /health is exempt
            assert client.post('/appointments', json=ONE_EXAM).status_code == 201
            assert client.post('/appointments', json=ONE_EXAM).status_code == 429
    finally:
        access.removeHandler(handler)
        logging.disable(disabled)
    for path, method in (('/appointments', 'post'), ('/appointments/{appointment_id}', 'get')):
        response = schema['paths'][path][method]['responses']['429']
        assert response['content']['application/json']['schema']['$ref'].endswith('/Message')
        assert 'Retry-After' in response['headers']
    assert any('"status": 429' in line for line in records)
    assert not any('testclient' in line for line in records)  # the TestClient's client host


def api_module():
    import api.main
    return api.main
