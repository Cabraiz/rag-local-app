"""Safe local infrastructure/OCR failure probes, never an attack on third parties."""
import os
from pathlib import Path
import socket
import subprocess
import threading
from uuid import uuid4
import pytest
from PIL import Image
import httpx2
import yaml
from clinic_adk.errors import SafeError

def test_developer_compose_security_contract():
    value = yaml.safe_load(Path('/app/docker-compose.yml').read_text())
    assert set(value['services']) == {'api', 'ocr', 'rag', 'runner', 'tests', 'browser'}
    for name, service in value['services'].items():
        assert service['user'] == '10001:10001' and service['read_only'] is True
        assert service['cap_drop'] == ['ALL']
        assert 'no-new-privileges:true' in service['security_opt']
        assert service['mem_limit'] and service['tmpfs']
        assert not any('key' in key.lower() or 'token' in key.lower() for key in service['environment'])
        if name != 'api': assert 'ports' not in service
    assert value['services']['api']['ports'][0].startswith('127.0.0.1:')
    assert value['networks']['clinic']['internal'] is True

def test_attacker_runner_has_no_privileges_source_write_or_egress():
    assert os.geteuid() == 10001
    with pytest.raises(OSError): Path('/app/attack-probe').touch()
    with socket.socket() as client:
        client.settimeout(0.3)
        # Documentation-only reserved address; no live external service is contacted.
        with pytest.raises(OSError): client.connect(('203.0.113.1', 443))

@pytest.mark.parametrize('failure', ['timeout', 'engine', 'invalid-utf8'])
def test_developer_ocr_failure_releases_bounded_slot(failure, tmp_path, monkeypatch):
    from clinic_adk import ocr_server as module
    Image.new('RGB', (100, 100), 'white').save(tmp_path/'sample.png')
    monkeypatch.setattr(module, 'SAMPLES', tmp_path)
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(module, 'slots', slots)
    def broken(*a, **kw):
        if failure == 'timeout': raise subprocess.TimeoutExpired('tesseract', 8)
        return subprocess.CompletedProcess('tesseract', 1 if failure == 'engine' else 0, b'\xff')
    monkeypatch.setattr(module.subprocess, 'run', broken)
    with pytest.raises(SafeError): module.extract('sample.png')
    assert slots.acquire(blocking=False)
    slots.release()

@pytest.mark.parametrize('server,port', [('ocr', 8081), ('rag', 8082)])
def test_attacker_mcp_rebinding_origin_and_body_limits(server, port):
    url = f'http://{server}:{port}'
    # A rejected Host may close keepalive intentionally; each probe has its own connection.
    with httpx2.Client(timeout=5, trust_env=False) as client:
        result = client.get(url+'/sse', headers={'Host': 'attacker.invalid', 'Accept': 'text/event-stream'})
        assert result.status_code == 421
    with httpx2.Client(timeout=5, trust_env=False) as client:
        result = client.get(url+'/sse', headers={'Origin': 'https://attacker.invalid', 'Accept': 'text/event-stream'})
        assert result.status_code == 403
    with httpx2.Client(timeout=5, trust_env=False) as client:
        result = client.post(url+'/messages/?session_id='+str(uuid4()), content=b'x'*16385,
                             headers={'Content-Type': 'application/json'})
        assert result.status_code == 413
