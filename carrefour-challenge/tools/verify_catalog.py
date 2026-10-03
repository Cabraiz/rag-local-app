"""Lightweight CF05 real SSE gate in a disposable Docker container.

Compose must map rag to 127.0.0.1 and disable external networking. This gate
starts the real RAG ASGI app, calls it via the real MCP clients, then stops only
its own subprocess. It never launches OCR/API or writes persistent volumes.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request


def main():
    if socket.gethostbyname('rag') != '127.0.0.1':
        raise RuntimeError('RAG_LOOPBACK_ALIAS_REQUIRED')
    evidence = Path('/evidence')
    evidence.mkdir(exist_ok=True)
    options = {'stdout': subprocess.PIPE, 'stderr': subprocess.STDOUT, 'shell': False}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    with (evidence / 'rag-server.log').open('wb') as log:
        options['stdout'] = log
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'clinic_adk.rag_server:app',
            '--host', '127.0.0.1', '--port', '8082', '--no-access-log'], **options)
        try:
            for _ in range(150):
                if server.poll() is not None:
                    raise RuntimeError('RAG_STARTUP_FAILED')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8082/health', timeout=0.2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError('RAG_STARTUP_TIMEOUT')
            tests = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                'tests/test_catalog_rag_card.py', 'tests/test_unit.py', 'tests/test_pii_guardrails.py',
                '--junitxml=/evidence/results.xml'], timeout=90, shell=False,
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
            return tests.returncode
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
