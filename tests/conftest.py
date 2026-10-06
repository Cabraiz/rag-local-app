"""Shared by every test module."""
import pytest

from runtime import rede


@pytest.fixture(autouse=True)
def no_process_pin():
    """An agent run outside `cli run` (adk run, or a test driving the runner directly) pins the servers'
    names for the process (runtime/rede.py, check_urls); each test starts and ends without that pin."""
    rede.unpin_process()
    yield
    rede.unpin_process()


@pytest.fixture(autouse=True)
def api_accepts_the_test_client_host(monkeypatch):
    """Starlette's TestClient sends `Host: testserver`; the API's host check (API_ALLOWED_HOSTS,
    api/main.py) accepts it in tests, besides its own defaults. Tests of the check set their own list."""
    monkeypatch.setenv('API_ALLOWED_HOSTS', '127.0.0.1,localhost,api,testserver')
