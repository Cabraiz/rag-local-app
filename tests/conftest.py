"""Shared by every test module."""
import sys

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


@pytest.fixture
def no_terminal(monkeypatch):
    """docker compose run -T, a pipe or CI: nobody answers the [s/N] question, and `cli run` without --yes
    stops before the model (cli.check_someone_answers)."""
    from runtime import confirmacao
    monkeypatch.setattr(confirmacao, 'can_ask', lambda: False)


@pytest.fixture(autouse=True)
def cli_run_goes_past_its_terminal_check(request, monkeypatch):
    """Under pytest stdout is never a terminal, so `cli run` without --yes would stop at its start; the tests of a
    run say themselves whether the person answers (confirmacao.ask_person). Tests with `no_terminal` keep the stop."""
    cli = sys.modules.get('cli')  # imported by the test modules that run it
    if cli is not None and 'no_terminal' not in request.fixturenames:
        monkeypatch.setattr(cli, 'check_someone_answers', lambda args, spec: None)
