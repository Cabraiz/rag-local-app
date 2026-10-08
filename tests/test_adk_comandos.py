"""The `adk run` and `adk web` command lines of docs/como-rodar.md, run as written by the real `adk` script.

The other ADK tests call ADK's click entry point or its FastAPI app with arguments of their own, so a
documented flag the installed ADK refuses (ADK 2.10 refuses --no_use_local_storage together with
--session_service_uri) would pass them. Here each documented line, from `adk` on, runs in its own
process on the folder `cli transpile` writes, offline, with no model call: it must start, serve (or
open its prompt), keep nothing in generated/.adk, and stop.
"""
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from transpiler import transpile

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / 'docs' / 'como-rodar.md'
ADK = shutil.which('adk')
pytestmark = [pytest.mark.skipif(ADK is None, reason='the adk script is installed in the Docker image'),
              pytest.mark.filterwarnings('ignore::UserWarning')]


def documented(command):
    """The arguments after `adk <command>` in the guide's `docker compose run ... agent adk <command>` line."""
    [line] = re.findall(rf'^docker compose run --rm (?:-p \S+ )?agent adk {command} ([^#\n]+)', GUIDE.read_text('utf-8'),
                        re.M)
    return shlex.split(line)


@pytest.fixture
def workdir(tmp_path):
    """The agent container's /app, as far as adk sees it: generated/ written by transpile, the project importable."""
    transpile(ROOT / 'specs' / 'agent.json', tmp_path / 'generated' / 'agent.py')
    return tmp_path


def environment():
    return {**os.environ, 'PYTHONPATH': str(ROOT), 'ADK_DISABLE_LOAD_DOTENV': '1',
            'GOOGLE_API_USE_CLIENT_CERTIFICATE': 'false', 'GOOGLE_API_KEY': ''}


def test_the_documented_lines_are_the_ones_this_test_runs():
    assert documented('run') == ['--in_memory', 'generated']
    assert documented('web') == ['--host', '0.0.0.0', '--port', '8000', '--no-reload', '--no_use_local_storage',
                                 'generated']


def test_the_documented_adk_run_starts_and_exits(workdir):
    done = subprocess.run([ADK, 'run', *documented('run')], cwd=workdir, env=environment(), input='exit\n',
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'Running agent clinic_scheduler, type exit to exit.' in done.stdout
    assert not (workdir / 'generated' / '.adk').exists()


def test_the_documented_adk_web_serves_the_agent_in_memory(workdir):
    with socket.socket() as probe:  # the documented 8000 may be taken on this machine: only the port changes
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    arguments = documented('web')
    arguments[arguments.index('--port') + 1] = str(port)
    server = subprocess.Popen([ADK, 'web', *arguments], cwd=workdir, env=environment(), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True)
    url = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 90
        while True:
            assert server.poll() is None, server.communicate()[0]  # it exited: a refused flag, for one
            try:
                apps = urllib.request.urlopen(f'{url}/list-apps', timeout=2).read()
                break
            except OSError:
                assert time.monotonic() < deadline, 'adk web did not answer'
                time.sleep(0.5)
        assert apps == b'["generated"]'
        create = urllib.request.Request(f'{url}/apps/generated/users/u/sessions', data=b'{}', method='POST',
                                        headers={'content-type': 'application/json'})
        assert urllib.request.urlopen(create, timeout=30).status == 200
        assert not (workdir / 'generated' / '.adk').exists()  # the session lives in memory only
    finally:
        server.terminate()
        output = server.communicate(timeout=30)[0]
    assert 'using in-memory session service' in output and 'Traceback' not in output, output


def test_the_old_line_is_refused_by_this_adk(workdir):
    """The flags the guide used to document, as a guard that the check above can fail."""
    done = subprocess.run([ADK, 'web', '--session_service_uri', 'memory://', '--no_use_local_storage', 'generated'],
                          cwd=workdir, env=environment(), capture_output=True, text=True, timeout=120)
    assert done.returncode != 0 and 'cannot be used with --session_service_uri' in done.stderr


