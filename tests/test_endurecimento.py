"""Hardening that crosses the services: the MCP servers' logs keep no value a peer sent, a hostile image ends in
one clear sentence, and the Docker base image is pinned by digest."""
import asyncio
import logging
import re
import struct
import zlib
from pathlib import Path

import pytest
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
    [called] = re.findall(r"quiet_logs\(([^)]*)\)", source.split("if __name__ == '__main__':")[1])
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
    monkeypatch.setattr(ocr, 'sobre_branco', hostile)
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', ROOT / 'samples')
    with pytest.raises(ToolError) as refused:
        ocr.read_lines(ocr.resolve_sample('pedido.png'))
    assert str(refused.value) == 'Imagem corrompida ou incompleta.' and refused.value.__suppress_context__


def test_the_base_image_is_pinned_by_digest():
    sources = re.findall(r'^FROM (\S+) AS (\S+)', (ROOT / 'Dockerfile').read_text(encoding='utf-8'), re.M)
    stages = {name for _, name in sources}
    external = [image for image, _ in sources if image not in stages]
    assert external and all(re.fullmatch(r'[\w./:-]+@sha256:[0-9a-f]{64}', image) for image in external)
