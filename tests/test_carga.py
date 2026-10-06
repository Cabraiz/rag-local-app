"""Teste de carga de dados sensíveis, pequeno, para a CI: 20 pedidos fictícios pelo OCR via MCP/SSE.

O mesmo código de tests/load/carga.py (a carga de 500 roda com o compose), com os
servidores OCR, RAG e a API em processo, como em test_mcp_sse.py. Falha se vazar um valor,
se o SQLite tiver algo em claro ou se um agendamento não voltar igual ao enviado.
"""
import asyncio
import importlib
import json
import random
import shutil
import socket
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn

from mcp_servers import ocr, rag
from tests.load import carga, pedidos


def serve(app, path=''):
    """Run an ASGI app with uvicorn in a background thread; return (server, URL + path).

    An MCP server module (ocr, rag) is served as its SSE app at /sse, as test_manuscritos
    and test_robustez call it: serve(ocr).
    """
    if hasattr(app, 'server') and hasattr(app, 'SECURITY'):
        app, path = app.server.sse_app(transport_security=app.SECURITY, host='127.0.0.1'), '/sse'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    # Keep-alive as in production (75 s): with uvicorn's 5 s, a POST can race the client's 5 s pool expiry.
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning',
                                           timeout_keep_alive=ocr.KEEP_ALIVE_SECONDS))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 60  # generous: a busy machine starts uvicorn slowly
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    return server, f'http://127.0.0.1:{port}{path}'


def test_generated_cpfs_are_never_valid_and_every_value_would_be_caught(tmp_path):
    manifesto = pedidos.gerar(tmp_path, 30)
    for item in manifesto.values():
        assert not pedidos.cpf_valido(item['sensiveis']['cpf'])
        # Without the mask every sensitive field is found: the leak check is not empty.
        rng = random.Random(0)
        raw = '\n'.join(pedidos.linhas(rng, item['sensiveis'], [e['name'] for e in item['exames']], item['layout']))
        assert {field for field, _ in carga.vazamentos(raw, item['sensiveis'])} == set(item['sensiveis'])


@pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image')
def test_twenty_orders_through_ocr_leak_nothing(tmp_path, monkeypatch):
    manifesto = pedidos.gerar(tmp_path / 'pedidos', 20)
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', tmp_path / 'pedidos')
    db = tmp_path / 'appointments.db'
    monkeypatch.setenv('DB_PATH', str(db))
    from api.crypto import new_key
    monkeypatch.setenv('DB_ENCRYPTION_KEY', new_key())
    import api.main
    servers = [serve(ocr), serve(rag), serve(importlib.reload(api.main).app)]
    try:
        urls = [url for _, url in servers]
        results, seconds, sessions = asyncio.run(carga.rodar(manifesto, *urls, concorrencia=4))
        diferentes = asyncio.run(carga.conferir_api(urls[2], results))
    finally:
        for server, _ in servers:
            server.should_exit = True
    table, problems = carga.resumo(manifesto, results, seconds, carga.banco_em_claro(db, manifesto, results), diferentes)
    assert problems == 0 and all(found['agendado'] for found in results.values()), table
    assert sessions == {'nao_fecharam': 0, 'quebraram': 0}, table
    exams = sum(len(item['exames']) for item in manifesto.values())
    assert sum(len(found['nao_lidos']) for found in results.values()) <= exams * 0.05, table


def test_values_stored_in_clear_are_found_in_the_database_bytes(tmp_path):
    # The same check on a database written without encryption: it must not pass.
    manifesto = pedidos.gerar(tmp_path / 'pedidos', 1)
    (filename, item), = manifesto.items()
    db = tmp_path / 'clear.db'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE appointments (id TEXT, exams TEXT, note TEXT)')
        connection.execute('INSERT INTO appointments VALUES (?, ?, ?)', (
            'b1a41468-7e29-40b1-ba66-1b478d6a3568', json.dumps(item['exames'], ensure_ascii=False),
            f'{item["sensiveis"]["paciente"]} {item["sensiveis"]["cpf"]}'))
    found = carga.banco_em_claro(db, manifesto, {filename: {'agendado': 'b1a41468-7e29-40b1-ba66-1b478d6a3568'}})
    kinds = {what for what, _, _ in found}
    assert {'código FICT', 'paciente', 'cpf'} <= kinds and any(what.startswith('exame ') for what in kinds)


class Session:
    """A fake MCP session: the OCR reads one order, the RAG fails."""

    def __init__(self, lines=(), error=None):
        self.lines, self.error = list(lines), error

    async def call_tool(self, tool, arguments):
        if self.error:
            raise self.error
        text = json.dumps({'lines': self.lines, 'pii_masked': {}})
        return SimpleNamespace(is_error=False, content=[SimpleNamespace(text=text)], structured_content=None)


def test_a_failure_in_the_rag_counts_and_keeps_the_ocr_check(tmp_path):
    manifesto = pedidos.gerar(tmp_path, 1)
    (filename, item), = manifesto.items()
    ocr_session = Session([f'Paciente: {item["sensiveis"]["paciente"]}', '- Hemograma completo'])
    found = asyncio.run(carga.um_pedido(ocr_session, Session(error=RuntimeError('RAG fora')), None, filename, item))
    assert found['erro'] == 'RuntimeError: RAG fora' and found['vazamentos']  # the leak is still seen


def test_no_session_means_failed_orders_not_a_hang(tmp_path):
    # Nothing listens on this port: every order is a counted failure, and rodar returns.
    manifesto = pedidos.gerar(tmp_path, 3)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    results, _, sessions = asyncio.run(carga.rodar(manifesto, f'http://127.0.0.1:{port}/sse', concorrencia=2))
    assert set(results) == set(manifesto) and all(found['erro'] for found in results.values())
    assert sessions['quebraram'] == 2


def test_a_dead_session_is_retried_once_on_a_new_one(tmp_path):
    # A lost reply becomes MCPError after CALL_SECONDS (the keep-alive race): the order is
    # marked for a new session, not hung and not posted twice.
    from mcp.shared.exceptions import MCPError
    manifesto = pedidos.gerar(tmp_path, 1)
    (filename, item), = manifesto.items()
    found = asyncio.run(carga.um_pedido(Session(error=MCPError(-32001, 'Timed out')), None, None, filename, item))
    assert found['sessao'] and found['erro'].startswith('MCPError') and not found['agendado']


def test_an_order_whose_session_dies_is_not_orphaned_when_the_reopen_fails(tmp_path, monkeypatch):
    # The "sem sessão MCP" of a loaded run: the reply to the last order is lost (MCPError after
    # CALL_SECONDS), the order goes back to the queue, and its worker cannot open a new session
    # (503). The other worker must take the order instead of having left on an empty-looking queue.
    # (Whether the 503 is reached depends on who is faster; the old code always reached it and lost
    # the order, the new one may finish the order on the other worker before the reopen.)
    from starlette.responses import PlainTextResponse
    manifesto = pedidos.gerar(tmp_path, 4)
    last = list(manifesto)[-1]
    monkeypatch.setattr(ocr, 'SAMPLES_DIR', tmp_path)
    monkeypatch.setattr(carga, 'CALL_SECONDS', 1.0)
    stuck = threading.Event()

    def read_lines(path):  # no Tesseract: the sessions matter here, not the text
        if path.name == last and not stuck.is_set():
            stuck.set()
            time.sleep(3)  # longer than CALL_SECONDS: the reply is lost
        return ['- Hemograma completo']

    monkeypatch.setattr(ocr, 'read_lines', read_lines)
    app, opened = ocr.server.sse_app(transport_security=ocr.SECURITY, host='127.0.0.1'), []

    async def first_reopen_fails(scope, receive, send):
        if scope['type'] == 'http' and scope['path'] == '/sse':
            opened.append(scope['path'])
            if len(opened) == 3:  # the 2 sessions open; the 1st reopen gets a 503
                return await PlainTextResponse('indisponível', status_code=503)(scope, receive, send)
        await app(scope, receive, send)

    server, url = serve(first_reopen_fails, '/sse')
    try:
        results, _, sessions = asyncio.run(carga.rodar(manifesto, url, concorrencia=2))
    finally:
        server.should_exit = True
    assert stuck.is_set()  # the reply to the last order was lost
    assert not [found['erro'] for found in results.values() if found['erro']]
    assert sessions == {'nao_fecharam': 0, 'quebraram': 0}


def test_a_long_list_by_type_wraps_between_items_in_the_value_column():
    value = ' · '.join(f'{kind} {n}' for kind, n in [('CID', 176), ('CLINICO', 67), ('CONVENIO', 200), ('CPF', 200),
                                                     ('CRM', 200), ('DATA', 400), ('EMAIL', 200), ('ENDERECO', 266),
                                                     ('NOME', 400), ('RG', 200), ('TELEFONE', 200)])
    parts = carga.quebrar(value)
    assert len(parts) > 1 and all(len(part) <= 64 for part in parts)
    assert ' · '.join(parts) == value  # every item kept whole, in order
    single = '0 em claro (código FICT, exame ou valor sensível)'
    assert carga.quebrar(single) == [single]
