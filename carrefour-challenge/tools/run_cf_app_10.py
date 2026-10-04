"""Offline image gate, dependency verification and separate test processes."""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from contextlib import contextmanager
import httpx2


@contextmanager
def regression_services():
    """Owned processes inside this one network-none container, never host ports."""
    processes = []
    environment = {**os.environ, 'CLINIC_DB': '/tmp/cf-app10-regressions.sqlite3'}
    try:
        for app, port in (('clinic_adk.api:app', 8080),
                          ('clinic_adk.security_profile:ocr_app', 8081),
                          ('clinic_adk.security_profile:rag_app', 8082)):
            command = [sys.executable, '-m', 'uvicorn', app, '--host', '127.0.0.1',
                       '--port', str(port), '--no-access-log', '--log-level', 'error']
            process = subprocess.Popen(command, env=environment, shell=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            processes.append(process)
            ready = False
            with httpx2.Client(timeout=1, trust_env=False) as client:
                for _ in range(300):
                    if process.poll() is not None:
                        raise RuntimeError('REGRESSION_SERVICE_EXITED')
                    try:
                        ready = client.get(f'http://127.0.0.1:{port}/health').status_code == 200
                    except httpx2.TransportError:
                        pass
                    if ready: break
                    time.sleep(0.1)
            if not ready: raise RuntimeError('REGRESSION_SERVICE_NOT_READY')
        yield
    finally:
        failed_cleanup = False
        for process in processes:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=8)
                failed_cleanup = True
        if failed_cleanup: raise RuntimeError('REGRESSION_SERVICE_TEARDOWN_FAILED')


def suite_run(command):
    result = subprocess.run(command, timeout=600, shell=False,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    print(result.stdout.decode('utf8', errors='replace'), flush=True)
    return result.returncode


def main():
    versions = {}
    for line in Path('/app/requirements.lock').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, expected = line.split('==')
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError('DEPENDENCY_VERSION_MISMATCH:' + name)
        versions[name] = actual
    print(json.dumps({'locked_dependencies_checked': len(versions), 'seed': os.environ['CF_SEED']}), flush=True)
    suites = [
        ['tests/test_cf_app_10_security.py', 'tests/test_cf_app_10_compatibility.py'],
        ['tests/test_unit.py', 'tests/test_adversarial.py',
         'tests/test_discovery_boundaries.py', 'tests/test_discovery_outcomes.py',
         'tests/test_media_type.py', 'tests/test_compiler_sandbox.py', 'tests/test_catalog_rag_card.py',
         'tests/test_pii_guardrails.py', 'tests/test_cf07_privacy_adapters.py', 'tests/test_cf07_mcp_egress.py'],
    ]
    for index, suite in enumerate(suites, 1):
        command = [sys.executable, '-m', 'pytest', '-q', '-s', '-p', 'no:cacheprovider', *suite]
        print(json.dumps({'suite': index, 'command': command}), flush=True)
        if index == 2:
            with regression_services():
                status = suite_run(command)
        else:
            status = suite_run(command)
        if status:
            return status
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
