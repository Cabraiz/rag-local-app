"""Pedidos manuscritos simulados: uma fração das 120 imagens e as duas amostras soltas em samples/.

O mesmo código de tests/load/manuscritos.py (as 120 rodam com o compose da carga), com OCR e
RAG em processo, como em test_carga.py. Nada fora do pedido pode ser agendado e nenhuma PII
do gabarito pode sobrar no texto do OCR.
"""
import asyncio
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from mcp_servers import ocr, rag
from tests.load import manuscritos
from tests.load.carga import vazamentos
from tests.test_carga import serve
from tests.test_transpiler import FakeContext, FakeTool, book, generated_module, ocr_reply, search

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'samples' / 'manuscritos'
ITENS = manuscritos.gabarito(FOLDER)
CI_FRACTION = sorted(ITENS)[::12]  # 10 das 120, dos dois estilos e de vários níveis de degradação
# As duas soltas em samples/ (para `cli run --image`) são cópias em PNG destas do conjunto.
LOOSE = {'pedido-manuscrito.png': 'comum-016.jpg', 'pedido-manuscrito-dificil.png': 'medico-005.jpg'}
needs_tesseract = pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image')


def test_the_set_is_complete_small_and_fictional():
    assert len(ITENS) == 120 and sum(item['estilo'] == 'medico' for item in ITENS.values()) == 50
    assert all((FOLDER / name).stat().st_size <= 60_000 for name in ITENS)
    assert sum((FOLDER / name).stat().st_size for name in ITENS) <= 7_000_000
    assert sum(item['degradacao'] != 'scan' for item in ITENS.values()) >= 0.7 * len(ITENS)
    for loose, source in LOOSE.items():
        with Image.open(ROOT / 'samples' / loose) as copy, Image.open(FOLDER / source) as original:
            assert copy.format == 'PNG'
            assert ImageChops.difference(copy.convert('RGB'), original.convert('RGB')).getbbox() is None


@pytest.mark.parametrize('line, query', [
    ('1. Complemento C4', 'Complemento C4'),
    ('2) Vitamina B12', 'Vitamina B12'),
    ('- T4 livre', 'T4 livre'),
    ('• CA 19-9', 'CA 19-9'),
    ('3. 25-OH Vitamina D', '25-OH Vitamina D'),
    ('25-OH Vitamina D', '25-OH Vitamina D'),
    ('  12 . Ferritina.', 'Ferritina.'),
])
def test_only_the_leading_marker_is_removed_from_the_query(line, query):
    # Digits at the end or inside an exam name are part of it ("Vitamina B12" is not "Vitamina B").
    assert manuscritos.consulta(line) == query


@needs_tesseract
# Fora desta fração, as 120 ainda têm trocas de exame pelo OCR ("Vitamina D" lida "Vitamina 2"), que
# viram pergunta e nunca são agendadas sozinhas; 0 PII sobrando. tests/load/manuscritos.py as mede.
def test_a_fraction_of_the_handwritten_orders_books_nothing_wrong_and_leaks_nothing(monkeypatch):
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


@needs_tesseract
@pytest.mark.parametrize('sample', LOOSE)
def test_loose_samples_through_the_agent_confidence_rule(tmp_path, sample):
    # OCR (with the mask), a real catalog search per line and the generated agent's callback,
    # as test_transpiler does for the other shipped samples.
    item = ITENS[LOOSE[sample]]
    lines = ocr.mask_lines(ocr.read_lines(ROOT / 'samples' / sample))['lines']
    assert vazamentos('\n'.join(lines), item['sensiveis']) == []
    agent, context = generated_module(tmp_path), FakeContext()
    agent.CALLBACKS.after_tool(FakeTool('extract_exam_text'), {}, context, ocr_reply(*lines))
    for line in lines:
        query = manuscritos.consulta(line)
        if len(query) >= 2:
            for hit in rag.search(query, 1):
                search(agent, context, query, (hit['code'], hit['name'], hit['score']))
    candidates = context.state.get('candidates', {})
    reply, args = book(agent, context, *candidates)
    booked = set() if reply else {exam['code'] for exam in args['exams']}
    expected = {exam['code'] for exam in item['exames']}
    assert booked <= expected  # nothing outside the order is booked
    if sample == 'pedido-manuscrito.png':
        assert len(booked) * 2 > len(expected)  # most of the order is booked
    else:
        assert context.state['low_confidence']  # the doubtful exams are reported, not booked silently
