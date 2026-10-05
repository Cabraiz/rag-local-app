"""Shared by every test module."""
import pytest


@pytest.fixture(autouse=True)
def api_accepts_the_test_client_host(monkeypatch):
    """Starlette's TestClient sends `Host: testserver`; the API's host check (API_ALLOWED_HOSTS,
    api/main.py) accepts it in tests, besides its own defaults. Tests of the check set their own list."""
    monkeypatch.setenv('API_ALLOWED_HOSTS', '127.0.0.1,localhost,api,testserver')
