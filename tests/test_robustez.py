"""Robustez, pequeno, para a CI: um caso de cada categoria pelo caminho real, em processo.

O mesmo código de tests/load/robustez.py (a suíte completa roda com o compose), com OCR e
RAG em processo via MCP/SSE (como em test_carga.py) e a API pelo ASGI do FastAPI. Falha se
algum caso der erro 500, traceback, mensagem pouco clara, PII sobrando ou exame agendado
que não está na imagem.
"""
import asyncio
import importlib
import shutil

import httpx
import pytest

from mcp_servers import ocr, rag
from tests.load import robustez
from tests.test_carga import serve


def test_clear_message_keeps_user_sentences_and_rejects_traces():
    assert robustez.mensagem_clara('Error executing tool extract_exam_text: Imagem corrompida ou incompleta.') == \
        (True, 'Imagem corrompida ou incompleta.')
    for texto in ('Traceback (most recent call last):', "KeyError: 'lines'", 'erro', '',
                  '1 validation error for search_examsArguments\nquery\n  Input should be a valid string '
                  '[type=string_type, input_value=123, input_type=int]',
                  '<object at 0x7f00>', 'x' * 301):
        assert not robustez.mensagem_clara(texto)[0], texto


def test_queries_drop_labels_and_list_markers():
    assert robustez.consulta('Exames solicitados: TSH') == 'TSH'
    assert robustez.consulta('- Hemograma completo') == 'Hemograma completo'
    assert robustez.consulta('2) Ferritina') == 'Ferritina'
    assert robustez.consulta('Exames:') == ''


def test_cases_are_the_same_on_every_run(tmp_path):
    first = robustez.montar(1, tmp_path / 'a')
    second = robustez.montar(1, tmp_path / 'b')
    assert [case[:3] for case in first] == [case[:3] for case in second]
    assert sorted(p.read_bytes() for p in (tmp_path / 'a').iterdir()) == \
        sorted(p.read_bytes() for p in (tmp_path / 'b').iterdir())


@pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image')
def test_one_case_per_category_never_breaks(tmp_path, monkeypatch):
    from api.crypto import new_key
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'appointments.db'))
    monkeypatch.setenv('DB_ENCRYPTION_KEY', new_key())
    import api.main
    api_module = importlib.reload(api.main)
    app = api_module.app
    samples = tmp_path / 'samples'
    samples.mkdir()
    # 25 milhões de pixels levam segundos no Tesseract: ficam para a suíte completa.
    casos = [case for case in robustez.montar(1, samples) if case[1] != 'enorme (perto do limite)']
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', samples)
    servers = [serve(module.server.sse_app(transport_security=module.SECURITY, host='127.0.0.1'), '/sse')
               for module in (ocr, rag)]
    async def run():
        async with api_module.lifespan(app):  # httpx.ASGITransport does not start the app itself
            return await robustez.rodar(casos, servers[0][1], servers[1][1], 'http://api', concorrencia=4,
                                        posts_simultaneos=10, transporte=httpx.ASGITransport(app=app))

    try:
        resultados, segundos = asyncio.run(run())
    finally:
        for server, _ in servers:
            server.should_exit = True
    # A missing argument ({}) gets the SDK's "Field required", counted as clear in robustez.recusa_mcp.
    tabela, falhas = robustez.resumo(resultados, segundos)
    assert falhas == 0, tabela
    assert len(resultados) == len(casos) + 10, tabela


class _Reply:
    """An MCP tool error as the client receives it (one text item)."""
    def __init__(self, text):
        self.content = [type('Item', (), {'text': text})()]


def test_only_a_missing_argument_gets_the_sdk_refusal_as_clear():
    missing = ('Error executing tool extract_exam_text: 1 validation error for extract_exam_textArguments\n'
               'filename\n  Field required [type=missing, input_value={}, input_type=dict]')
    assert robustez.recusa_mcp(_Reply(missing), 'OCR')[0] == 'clara'
    wrong = ('Error executing tool search_exams: 1 validation error for search_examsArguments\n'
             'query\n  Input should be a valid string [type=string_type, input_value=123, input_type=int]')
    assert robustez.recusa_mcp(_Reply(wrong), 'RAG')[0] == 'falhou'
    assert robustez.recusa_mcp(_Reply('Error executing tool search_exams: Informe o nome de um exame para buscar.'),
                               'RAG') == ('clara', 'Informe o nome de um exame para buscar.')
