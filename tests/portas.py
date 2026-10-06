"""The spec's own ports (8000-8002), shared by the test modules that serve on them."""
import os
import socket
import time

import pytest

WAIT_SECONDS = 15


def wait_until_free(port, seconds=WAIT_SECONDS):
    """True once nothing listens on the port. The previous module's server on the same port may
    still be closing (its SSE streams included), above all when the modules run back to back."""
    deadline = time.monotonic() + seconds
    while True:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) != 0:
                return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)


def require_free(port):
    """Wait for the port; if it never frees, fail in CI and skip elsewhere.

    In CI nothing else uses these ports, so a port still taken is a bug, and a skip would pass
    green while hiding these tests. On a developer's machine another program may well listen on
    8000, and failing the whole suite for that would be wrong: there the module skips, saying why.
    """
    if wait_until_free(port):
        return
    message = f'port {port} is still in use after {WAIT_SECONDS} s'
    if os.environ.get('CI'):
        pytest.fail(f'{message} (CI: a skip here would hide these tests)')
    pytest.skip(message)
