"""Hardening that crosses the services: the MCP servers' logs keep no value a peer sent, a hostile image ends in
one clear sentence, and the Docker base image is pinned by digest."""
import asyncio
import logging
import re
import shutil
import struct
import subprocess
import threading
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from mcp.server.mcpserver.exceptions import ToolError

from mcp_servers import ocr, rag
from mcp_servers.arguments import quiet_logs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def quiet(caplog):
    """quiet_logs as the servers install it, undone afterwards; the records reach caplog."""
    factory, sse, disabled = logging.getLogRecordFactory(), logging.getLogger('mcp.server.sse').level, \
        logging.root.manager.disable
    logging.disable(logging.NOTSET)  # cli.main() turns logging off for its whole process
    logger = logging.getLogger('teste.servidor')
    logger.addHandler(caplog.handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # each record once, through caplog's handler
    quiet_logs('extract_exam_text')
    yield logger
    logger.removeHandler(caplog.handler)
    logging.setLogRecordFactory(factory)
    logging.getLogger('mcp.server.sse').setLevel(sse)
    logging.disable(disabled)


def test_the_mcp_logs_keep_no_file_name_peer_id_or_traceback(quiet, caplog):
    quiet.info('Tool %r failed: %r', 'extract_exam_text', 'Arquivo "Maria-Silva.png" não encontrado em /data/samples.')
    try:
        raise FileNotFoundError('/data/samples/Maria-Silva.png')
    except FileNotFoundError:
        quiet.exception('Tool %r raised an unexpected exception', 'extract_exam_text')
    quiet.info('%s - "%s %s HTTP/%s" %d', '172.18.0.5:43210', 'POST', '/messages/?session_id=Maria', '1.1', 202)
    quiet.error(OSError('Maria-Silva.png'))
    quiet.info('Started server process [%d]', 7)
    assert [record.getMessage() for record in caplog.records] == [
        "Tool 'extract_exam_text' failed: '…'",
        "Tool 'extract_exam_text' raised an unexpected exception (FileNotFoundError)",
        '… - "POST … HTTP/1.1" 202', 'OSError', 'Started server process [7]']
    assert 'Maria' not in caplog.text and 'Traceback' not in caplog.text
    assert logging.getLogger('mcp.server.sse').getEffectiveLevel() == logging.ERROR  # its warnings quote the peer


@pytest.mark.parametrize('module', [ocr, rag])
def test_each_server_quiets_its_logs_naming_its_own_tools(module):
    source = Path(module.__file__).read_text(encoding='utf-8')
    [called] = re.findall(r"serve\(server, SECURITY, \d+, ([^)]*)\)", source.split("if __name__ == '__main__':")[1])
    tools = [tool.name for tool in asyncio.run(module.server.list_tools())]
    assert sorted(re.findall(r"'([^']+)'", called)) == sorted(tools)


def png(width, height):
    """A PNG header that claims width x height, with no pixels behind it."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IEND', b'')


def test_a_decompression_bomb_is_refused_from_its_header(tmp_path, monkeypatch):
    (tmp_path / 'bomba.png').write_bytes(png(100_000, 100_000))
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', tmp_path)
    for check in (ocr.read_lines, lambda path: ocr.checked_image(path, lambda image: image.size)):
        with pytest.raises(ToolError, match='^Imagem com resolução grande demais.$'):
            check(ocr.resolve_sample('bomba.png'))


@pytest.mark.parametrize('error', [ValueError('tile cannot extend outside image'), SyntaxError('broken PNG file'),
                                   EOFError(), OSError('image file is truncated')])
def test_what_pillow_raises_on_a_hostile_image_ends_in_one_sentence(monkeypatch, error):
    def hostile(image):
        raise error
    monkeypatch.setattr(ocr, 'on_white', hostile)
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', ROOT / 'samples')
    with pytest.raises(ToolError) as refused:
        ocr.read_lines(ocr.resolve_sample('pedido.png'))
    assert str(refused.value) == 'Imagem corrompida ou incompleta.' and refused.value.__suppress_context__


def test_the_base_image_is_pinned_by_digest():
    sources = re.findall(r'^FROM (\S+) AS (\S+)', (ROOT / 'Dockerfile').read_text(encoding='utf-8'), re.M)
    stages = {name for _, name in sources}
    external = [image for image, _ in sources if image not in stages]
    assert external and all(re.fullmatch(r'[\w./:-]+@sha256:[0-9a-f]{64}', image) for image in external)


# --- The OCR reads a few images at a time --------------------------------------------------------------------

@pytest.fixture
def slots(monkeypatch):
    """Fresh OCR slots, a short wait for one, and a reading that holds its slot until `release` is set."""
    monkeypatch.setattr(ocr, 'SLOTS', threading.BoundedSemaphore(3))
    monkeypatch.setattr(ocr, 'SLOT_WAIT_SECONDS', 0.2)
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', ROOT / 'samples')
    state = SimpleNamespace(release=threading.Event(), running=0, most=0, lock=threading.Lock())

    def reading(path):
        with state.lock:
            state.running += 1
            state.most = max(state.most, state.running)
        state.release.wait(10)
        with state.lock:
            state.running -= 1
        return []
    monkeypatch.setattr(ocr, 'read_lines', reading)
    yield state
    state.release.set()


def test_requests_past_the_ocr_slots_wait_and_then_get_a_clear_refusal(slots):
    async def burst():
        calls = [asyncio.create_task(ocr.extract_exam_text('pedido.png')) for _ in range(5)]
        done = await asyncio.gather(*calls, return_exceptions=True)
        return [str(item) if isinstance(item, Exception) else 'ok' for item in done]
    threading.Timer(1.0, slots.release.set).start()  # after the 0,2 s wait of the 2 extra requests
    assert sorted(asyncio.run(burst())) == ['OCR ocupado com outras imagens; tente de novo em instantes.'] * 2 + ['ok'] * 3
    assert slots.most == 3


def test_a_request_that_waits_less_than_the_limit_gets_its_reading(slots, monkeypatch):
    monkeypatch.setattr(ocr, 'SLOT_WAIT_SECONDS', 5)
    threading.Timer(0.3, slots.release.set).start()

    async def burst():
        return await asyncio.gather(*(ocr.extract_exam_text('pedido.png') for _ in range(8)))
    assert len(asyncio.run(burst())) == 8 and slots.most == 3


def test_a_cancelled_request_keeps_its_slot_until_its_reading_ends(slots):
    """A peer that cancels its calls cannot start more readings than the slots: the thread holds the slot."""
    async def cancel_three():
        calls = [asyncio.create_task(ocr.extract_exam_text('pedido.png')) for _ in range(3)]
        while slots.running < 3:
            await asyncio.sleep(0.01)
        for call in calls:
            call.cancel()
        await asyncio.gather(*calls, return_exceptions=True)
        with pytest.raises(ToolError, match='^OCR ocupado'):
            await ocr.check_image('pedido.png')  # the image check shares the slots
        slots.release.set()
    asyncio.run(cancel_three())  # returns once the readings ended
    assert slots.running == 0 and ocr.SLOTS.acquire(blocking=False)


# --- What reaches the images: the build context ------------------------------------------------------------

def docker_rules(path):
    """(negated, regex) of each .dockerignore line, as Docker reads them (`**` any folders, `*` within one)."""
    rules = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        negated, pattern = line.startswith('!'), line.lstrip('!').strip('/')
        regex = ''.join({'**/': '(?:.*/)?', '**': '.*', '*': '[^/]*', '?': '[^/]'}.get(part, re.escape(part))
                        for part in re.split(r'(\*\*/|\*\*|\*|\?)', pattern))
        rules.append((negated, re.compile(regex)))
    return rules


def in_the_context(path):
    """Docker's rule: the last pattern that matches the path, or one of its folders, decides."""
    parts, kept = path.split('/'), True
    for negated, regex in docker_rules(ROOT / '.dockerignore'):
        if any(regex.fullmatch('/'.join(parts[:end])) for end in range(1, len(parts) + 1)):
            kept = negated
    return kept


def gitignore_examples():
    """A path each .gitignore pattern ignores; a pattern with no folder ignores it in any folder."""
    for line in (ROOT / '.gitignore').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith(('#', '!')):
            continue
        example = line.strip().replace('*', 'x').rstrip('/') + ('/f' if line.strip().endswith('/') else '')
        yield example
        if '/' not in line.strip().rstrip('/'):
            yield f'tests/{example}'


@pytest.mark.parametrize('path', [*gitignore_examples(), '.env.local', 'api/.env.production', 'chave.pem',
                                  'tests/servico.key', 'gcp-credentials.json', '.local/notas.txt', 'secrets/db.txt',
                                  'docs/secrets/x', '.venv/lib/x.py', 'runtime/venv/x.py', '.mypy_cache/x',
                                  'runtime/.ruff_cache/x', 'minha-pasta/qualquer.txt', '.git/config'])
def test_secrets_and_local_files_stay_out_of_the_images(path):
    assert not in_the_context(path)


def test_what_the_repository_versions_reaches_the_test_image():
    """Where git runs: every tracked file but the CI's and generated/'s; in the image, the example env file."""
    assert in_the_context('.env.example') and in_the_context('tests/test_api.py')
    if shutil.which('git') is None or (done := subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True,
                                                               text=True)).returncode != 0:
        return
    tracked = [path for path in done.stdout.splitlines() if not path.startswith(('.github/', 'generated/'))]
    assert len(tracked) > 100 and [path for path in tracked if not in_the_context(path)] == []


def test_every_file_the_dockerfile_copies_reaches_the_build_context():
    """Without git too: a new top-level module the images COPY must be allowed by .dockerignore, or the build fails."""
    copied = [source for line in (ROOT / 'Dockerfile').read_text(encoding='utf-8').splitlines()
              if line.strip().startswith('COPY') and '--from' not in line
              for source in [word for word in line.split()[1:] if not word.startswith('--')][:-1]]
    assert 'catalogo.py' in copied and [source for source in copied if source != '.' and not in_the_context(source)] == []


def test_every_service_has_a_memory_and_process_cap_and_the_servers_a_cpu_cap():
    """A burst of large images restarts the OCR container at worst (or gets "OCR ocupado"), never fills the host."""
    services = yaml.safe_load((ROOT / 'docker-compose.yml').read_text(encoding='utf-8'))['services']
    assert {'ocr', 'rag', 'api', 'agent', 'tests', 'tests-e2e'} <= set(services)
    for name, service in services.items():
        assert service.get('mem_limit') and 0 < service.get('pids_limit', 0) <= 1024, name
        assert name.startswith('tests') or 0 < service.get('cpus', 0) <= 2, name  # no more than a small host has
    assert services['ocr']['environment']['MALLOC_ARENA_MAX'] == '2'
