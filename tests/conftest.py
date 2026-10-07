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
