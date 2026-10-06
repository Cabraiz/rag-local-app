"""A port of the spec that never frees: the test modules fail in CI instead of skipping silently."""
import ast
import socket
from pathlib import Path

import pytest

from tests import portas


def test_a_port_still_taken_fails_in_ci(monkeypatch):
    monkeypatch.setattr(portas, 'wait_until_free', lambda port: False)
    monkeypatch.setenv('CI', 'true')
    with pytest.raises(pytest.fail.Exception, match='port 8001 is still in use after 15 s .CI: a skip here'):
        portas.require_free(8001)


def test_a_port_still_taken_skips_elsewhere_and_says_why(monkeypatch):
    monkeypatch.setattr(portas, 'wait_until_free', lambda port: False)
    monkeypatch.delenv('CI', raising=False)
    with pytest.raises(pytest.skip.Exception, match='port 8001 is still in use after 15 s'):
        portas.require_free(8001)


def test_a_free_port_is_free_at_once():
    with socket.socket() as probe:  # a port the OS just handed out and nobody listens on
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    assert portas.wait_until_free(port, seconds=0)


def test_every_test_on_the_spec_ports_runs_in_the_spec_ports_group():
    # With -n, a test that serves on 8000-8002 outside the xdist_group runs beside the others and
    # finds the port taken (in CI it fails; elsewhere it would skip and hide the test).
    missing = []
    for path in sorted(Path(__file__).parent.glob('test_*.py')):
        text = path.read_text(encoding='utf-8')
        module_group = "xdist_group('spec-ports')" in text.split('\ndef ', 1)[0]  # in the module's pytestmark
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith('test'):
                continue
            uses_ports = {arg.arg for arg in node.args.args} & {'servers', 'services'}
            grouped = any("xdist_group('spec-ports')" in ast.unparse(decorator) for decorator in node.decorator_list)
            if uses_ports and not (module_group or grouped):
                missing.append(f'{path.name}::{node.name}')
    assert not missing, f"add @pytest.mark.xdist_group('spec-ports') to: {missing}"
