"""The API's Host check (TrustedHost in api/main.py, API_ALLOWED_HOSTS), against DNS rebinding.

A browser sends the page's own name as Host. A page at evil.example whose name a DNS rebinding points at
127.0.0.1 reaches the published port, but its Host is not one the API answers, so it gets 400.
"""
import contextlib
import socket
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_api import ONE_EXAM, api_module, configure
from tests.test_api_headers import assert_secured
from tests.test_carga import serve

REFUSED = {'detail': 'Cabeçalho Host não permitido: chame a API por 127.0.0.1 ou localhost (no host) ou por api '
                     '(na rede do compose), ou inclua o nome em API_ALLOWED_HOSTS.'}


@pytest.fixture
def default_hosts(tmp_path, monkeypatch):
    """The API with API_ALLOWED_HOSTS unset: its defaults, as in the compose."""
    monkeypatch.delenv('API_ALLOWED_HOSTS')
    with TestClient(configure(tmp_path, monkeypatch)) as client:
        yield client


@pytest.mark.parametrize('host', ['127.0.0.1', '127.0.0.1:8765', '127.0.0.1:9000', 'localhost:8765', 'LOCALHOST',
                                  'api:8000', 'api'])
def test_the_hosts_the_stack_uses_are_answered(default_hosts, host):
    created = default_hosts.post('/appointments', json=ONE_EXAM, headers={'host': host})
    assert created.status_code == 201
    assert default_hosts.get(f"/appointments/{created.json()['id']}", headers={'host': host}).status_code == 200


@pytest.mark.parametrize('host', ['evil.example', 'evil.example:8765', '127.0.0.1.evil.example', 'api.evil.example',
                                  'testserver', '', '127.0.0.1:8765@evil.example', 'api:8000/x', '[::1]:8765',
                                  'localhost:', '127.0.0.1:8765:80'])
def test_any_other_host_is_400_in_the_api_error_style(default_hosts, host):
    refused = default_hosts.post('/appointments', json=ONE_EXAM, headers={'host': host})
    assert_secured(refused, 400, no_store=True)
    assert refused.json() == REFUSED
    assert 'evil' not in refused.text  # the refused value is never echoed
    assert_secured(default_hosts.get('/health', headers={'host': host}), 400, no_store=False)
    with contextlib.closing(sqlite3.connect(default_hosts.app.state.db_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM appointments').fetchone() == (0,)  # nothing was written


def test_api_allowed_hosts_replaces_the_list(tmp_path, monkeypatch):
    monkeypatch.setenv('API_ALLOWED_HOSTS', ' Clinica.Interna , [::1] ')
    with TestClient(configure(tmp_path, monkeypatch)) as client:
        assert client.get('/health', headers={'host': 'clinica.interna:8443'}).status_code == 200
        assert client.get('/health', headers={'host': 'CLINICA.interna'}).status_code == 200
        assert client.get('/health', headers={'host': '[::1]:8765'}).status_code == 200
        assert client.get('/health', headers={'host': '127.0.0.1:8765'}).status_code == 400  # not in the list


@pytest.mark.parametrize('value', ['', ' , '])
def test_an_empty_list_means_the_defaults(tmp_path, monkeypatch, value):
    monkeypatch.setenv('API_ALLOWED_HOSTS', value)
    with TestClient(configure(tmp_path, monkeypatch)) as client:
        assert client.app.state.allowed_hosts == {'127.0.0.1', 'localhost', 'api'}


@pytest.mark.parametrize('value', ['http://api', 'api:8000', '*', 'evil.example/', 'clínica'])
def test_an_invalid_entry_stops_the_start(tmp_path, monkeypatch, value):
    monkeypatch.setenv('API_ALLOWED_HOSTS', f'127.0.0.1,{value}')
    with pytest.raises(api_module().ConfigError) as stop, TestClient(configure(tmp_path, monkeypatch)):
        pass
    assert str(stop.value) == (f'API_ALLOWED_HOSTS inválido: "{value}" não é um nome de host '
                               '(ex.: 127.0.0.1,localhost,api).')


def test_before_the_start_every_request_is_refused(tmp_path, monkeypatch):
    """Fail closed: an app whose lifespan never ran has no list, and answers nothing."""
    app = configure(tmp_path, monkeypatch)
    if hasattr(app.state, 'allowed_hosts'):
        monkeypatch.delattr(app.state, 'allowed_hosts')
    assert TestClient(app).get('/openapi.json').status_code == 400  # no `with`: no lifespan


def test_under_uvicorn_a_rebinding_host_is_refused_and_the_loopback_one_books(tmp_path, monkeypatch):
    """The real server and the real headers: a raw request with the attacker's name as Host."""
    monkeypatch.delenv('API_ALLOWED_HOSTS')
    server, url = serve(configure(tmp_path, monkeypatch))
    try:
        port = int(url.rsplit(':', 1)[1])
        body = b'{"exams": [{"code": "FICT-001"}]}'

        def raw(host):
            with socket.create_connection(('127.0.0.1', port), timeout=10) as connection:
                connection.sendall(b'POST /appointments HTTP/1.1\r\nHost: ' + host + b'\r\nContent-Type: application/json'
                                   b'\r\nContent-Length: ' + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n'
                                   + body)
                return b''.join(iter(lambda: connection.recv(65536), b''))

        assert raw(b'evil.example:' + str(port).encode()).startswith(b'HTTP/1.1 400 ')
        assert raw(f'127.0.0.1:{port}'.encode()).startswith(b'HTTP/1.1 201 ')
        assert httpx.get(f'{url}/health', timeout=10).status_code == 200  # an ordinary client sends 127.0.0.1:port
    finally:
        server.should_exit = True
