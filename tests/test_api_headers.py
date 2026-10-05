"""The security headers of the API (SecurityHeaders in api/main.py) on every kind of response."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.test_api import ONE_EXAM, api_module, client, configure, limited_client  # noqa: F401 (fixture)

EVERY_RESPONSE = {'x-content-type-options': 'nosniff', 'x-frame-options': 'DENY', 'referrer-policy': 'no-referrer'}
CSP = "default-src 'none'; frame-ancestors 'none'"


def assert_secured(response, status, no_store=True, csp=True):
    assert response.status_code == status, response.text
    for name, value in EVERY_RESPONSE.items():
        assert response.headers.get_list(name) == [value], (name, status)
    assert response.headers.get_list('content-security-policy') == ([CSP] if csp else [])
    assert response.headers.get_list('cache-control') == (['no-store'] if no_store else [])
    assert response.headers['x-request-id']  # the request log's header is still there


def test_created_read_missing_and_invalid_appointments(client):
    created = client.post('/appointments', json=ONE_EXAM)
    assert_secured(created, 201)
    assert_secured(client.get(f"/appointments/{created.json()['id']}"), 200)
    assert_secured(client.get('/appointments/00000000-0000-0000-0000-000000000000'), 404)
    assert_secured(client.get('/appointments/nao-e-uuid'), 422)
    assert_secured(client.post('/appointments', json={'exams': []}), 422)
    assert_secured(client.post('/appointments', content='{"exams": [', headers={'content-type': 'application/json'}), 422)


def test_routes_without_exams_are_not_marked_no_store(client):
    assert_secured(client.get('/health'), 200, no_store=False)
    assert_secured(client.get('/openapi.json'), 200, no_store=False)
    assert_secured(client.get('/nao-existe'), 404, no_store=False)
    assert_secured(client.get('/appointmentsX'), 404, no_store=False)  # only /appointments and below


def test_the_413_of_the_body_limit(client):
    body = json.dumps({'exams': [{'code': 'FICT-001', 'name': 'x' * 20_000}]})
    assert_secured(client.post('/appointments', content=body, headers={'content-type': 'application/json'}), 413)


def test_the_429_of_the_rate_limit(tmp_path, monkeypatch):
    with limited_client(tmp_path, monkeypatch, '1') as client:
        assert client.post('/appointments', json=ONE_EXAM).status_code == 201
        refused = client.post('/appointments', json=ONE_EXAM)
        assert_secured(refused, 429)
        assert refused.headers['retry-after']


def test_an_unhandled_error_is_a_500_with_the_headers_and_the_same_body(client, monkeypatch):
    monkeypatch.setattr(api_module(), 'connect', lambda _: (_ for _ in ()).throw(sqlite3.OperationalError('disk I/O')))
    broken = TestClient(client.app, raise_server_exceptions=False)  # no `with`: the started app is reused
    response = broken.post('/appointments', json=ONE_EXAM)
    assert_secured(response, 500)
    assert response.json() == {'detail': 'Erro interno ao processar a requisição.'}
    assert 'Traceback' not in response.text and 'disk I/O' not in response.text


def test_the_unhandled_error_still_reaches_the_server(client, monkeypatch):
    """The 500 is sent once, and the exception still goes up (uvicorn logs it, a TestClient raises it)."""
    monkeypatch.setattr(api_module(), 'connect', lambda _: (_ for _ in ()).throw(sqlite3.OperationalError('disk I/O')))
    with pytest.raises(sqlite3.OperationalError):
        TestClient(client.app).post('/appointments', json=ONE_EXAM)


def test_an_unreadable_record_is_a_500_with_the_headers(tmp_path, monkeypatch):
    with TestClient(configure(tmp_path, monkeypatch)) as client:
        created = client.post('/appointments', json=ONE_EXAM).json()
        with sqlite3.connect(client.app.state.db_path) as connection:
            connection.execute("UPDATE appointments SET created_at = '2020-01-01T00:00:00+00:00'")
        assert_secured(client.get(f"/appointments/{created['id']}"), 500)


@pytest.mark.parametrize('page, asset', [
    ('/docs', 'https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js'),
    ('/redoc', 'https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js'),
])
def test_the_docs_pages_still_load_their_cdn_assets(client, page, asset):
    """No CSP on the docs pages: their HTML loads scripts from a CDN and runs an inline one."""
    response = client.get(page)
    assert_secured(response, 200, no_store=False, csp=False)
    assert response.headers['content-type'].startswith('text/html')
    assert asset in response.text
    if page == '/docs':
        assert '<script>' in response.text  # the inline SwaggerUIBundle(...) call
