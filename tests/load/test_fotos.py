"""Fotos de celular de pedidos impressos: 5 das 30 de samples/fotos-celular na CI, e a solta em samples/.

O mesmo código de tests/load/manuscritos.py (as 30 rodam com o compose da carga), com OCR e
RAG em processo, como em test_manuscritos.py. Nada fora do pedido pode ser agendado sem
perguntar e nenhuma PII do gabarito pode sobrar no texto do OCR.
"""
import asyncio
import re
import shutil
from pathlib import Path

import pytest
from PIL import Image

from mcp_servers import ocr, rag
from tests.load import manuscritos
from tests.load.pedidos import cpf_valido
from tests.test_carga import serve

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'samples' / 'fotos-celular'
ITENS = manuscritos.gabarito(FOLDER)
CI_FRACTION = sorted(ITENS)[::7]  # 5 das 30: os três layouts e os três níveis (leve, média, forte)
LOOSE = ROOT / 'samples' / 'pedido-foto-celular.jpg'  # para `cli run --image`: cópia exata de uma do conjunto
LOOSE_SOURCE = 'foto-14.jpg'
needs_tesseract = pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image')


def test_the_photo_set_is_complete_small_and_fictional():
    assert len(ITENS) == 30 and {item['degradacao'] for item in ITENS.values()} == {'leve', 'media', 'forte'}
    assert sum(path.stat().st_size for path in FOLDER.iterdir()) + LOOSE.stat().st_size <= 3_000_000
    for name, item in ITENS.items():
        with Image.open(FOLDER / name) as photo:
            assert photo.format == 'JPEG' and 1200 <= max(photo.size) <= 2000 and list(photo.size) == item['tamanho']
        assert not cpf_valido(item['sensiveis']['cpf'])  # the check digit is wrong on purpose
        assert re.search(r'@exemplo\.invalid$', item['sensiveis']['email'])
    assert LOOSE.read_bytes() == (FOLDER / LOOSE_SOURCE).read_bytes()


@needs_tesseract
# As 30 com o compose da carga (tests/load/manuscritos.py --origem samples/fotos-celular) também
# medem os exames que o agente deixa de fora; aqui, só o que não pode acontecer.
def test_five_phone_photos_book_nothing_wrong_and_leak_nothing(monkeypatch):
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', FOLDER)
    servers = [serve(ocr), serve(rag)]
    try:
        itens = {name: ITENS[name] for name in CI_FRACTION}
        results = asyncio.run(manuscritos.rodar(itens, servers[0][1], servers[1][1], concorrencia=2))
    finally:
        for server, _ in servers:
            server.should_exit = True
    table, problems = manuscritos.resumo(itens, results)
    assert problems == 0, table
    assert not any(found['erro'] for found in results.values()), table
