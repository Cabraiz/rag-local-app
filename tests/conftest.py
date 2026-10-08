"""Shared by every test module."""
import pytest

from runtime import rede


@pytest.fixture
def new_process(monkeypatch):
    """No name pinned, as in a new `adk run` / `adk web` process (runtime/rede.py, check_urls); the pins the
    test makes are dropped after it."""
    monkeypatch.setattr(rede, 'PINS', None)
    monkeypatch.setattr(rede, 'SCOPE', None)


@pytest.fixture(autouse=True)
def api_accepts_the_test_client_host(monkeypatch):
    """Starlette's TestClient sends `Host: testserver`; the API's host check (API_ALLOWED_HOSTS,
    api/main.py) accepts it in tests, besides its own defaults. Tests of the check set their own list."""
    monkeypatch.setenv('API_ALLOWED_HOSTS', '127.0.0.1,localhost,api,testserver')
