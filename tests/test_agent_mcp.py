"""The agent's own MCP path without Gemini: the servers started as the Dockerfile starts them
(`python -m mcp_servers.ocr` / `.rag`), reached through the generated `mcp_tool` (McpToolset +
SseConnectionParams), called, left idle for 8 s and called again on the same sessions.

The idle wait also holds a raw HTTP/1.1 connection to each server: it must still answer after
8 s. With uvicorn's default 5 s keep-alive, equal to the clients' pool expiry, a POST sent as
the server closed the connection was lost and the agent's tool call waited for its read timeout
(python-sdk #906); the servers now keep idle connections for 75 s.
"""
import asyncio
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams

from runtime import mcp_payload
from tests.portas import require_free

ROOT = Path(__file__).resolve().parents[1]
SERVERS = {'ocr': 8001, 'rag': 8002}
IDLE_SECONDS = 8

pytestmark = [
    pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image'),
    # The spec's own ports, also used by the other module: with pytest-xdist (-n) both run on one
    # worker, one after the other.
    pytest.mark.xdist_group('spec-ports'),
    # ADK's notices for McpToolset used outside a Runner; the agent itself is not affected.
    pytest.mark.filterwarnings(r'ignore:\[EXPERIMENTAL\]:UserWarning'),
    pytest.mark.filterwarnings('ignore:MCPTool class is deprecated:DeprecationWarning'),
]


def generated_agent():
    """docs/exemplo-agent.py, the transpiler's output for specs/agent.json (importing it calls no model)."""
    spec = importlib.util.spec_from_file_location('exemplo_agent', ROOT / 'docs' / 'exemplo-agent.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def servers():
    """Each MCP server as its own process, on its real port, with the run code of its __main__."""
    for port in SERVERS.values():
        require_free(port)
    env = {**os.environ, 'SAMPLES_DIR': str(ROOT / 'samples')}
    processes = [subprocess.Popen([sys.executable, '-m', f'mcp_servers.{name}'], cwd=ROOT, env=env, shell=False,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                 for name in SERVERS]
    try:
        for name, port in SERVERS.items():
            deadline = time.monotonic() + 60  # generous: a busy machine starts the servers slowly
            while True:
                try:
                    if httpx.get(f'http://127.0.0.1:{port}/health', timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline, f'{name} server did not start'
                time.sleep(0.2)
        yield {name: f'http://127.0.0.1:{port}/sse' for name, port in SERVERS.items()}
    finally:
        for process in processes:
            process.terminate()
            process.wait(timeout=10)


async def health(reader, writer, port):
    """GET /health on an open HTTP/1.1 connection; the status line, or None if the server closed it."""
    writer.write(f'GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n'.encode())
    try:
        await writer.drain()
        head = await reader.readuntil(b'\r\n\r\n')
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    length = next(int(line.split(b':')[1]) for line in head.split(b'\r\n') if line.lower().startswith(b'content-length'))
    await reader.readexactly(length)
    return head.split(b'\r\n', 1)[0].decode()


def test_agent_tools_answer_again_after_the_sessions_sit_idle(servers):
    generated_agent()  # the generated code imports with no model and no network
    # The tool runs outside a Runner: no model, no auth or debug settings, so the context is a stand-in.
    context = MagicMock()

    async def run():
        # the same toolsets the generated agent declares, pointed at the local servers
        toolsets = {name: McpToolset(connection_params=SseConnectionParams(url=servers[name]), tool_filter=[tool])
                    for name, tool in (('ocr', 'extract_exam_text'), ('rag', 'search_exams'))}
        arguments = {'ocr': {'filename': 'pedido.png'}, 'rag': {'query': 'Glicose', 'top_k': 1}}
        try:
            tools = {name: await toolset.get_tools() for name, toolset in toolsets.items()}
            assert {name: [tool.name for tool in found] for name, found in tools.items()} == \
                {'ocr': ['extract_exam_text'], 'rag': ['search_exams']}

            async def call_both():
                replies = {name: await tools[name][0].run_async(args=arguments[name], tool_context=context)
                           for name in tools}
                return {name: mcp_payload(reply) for name, reply in replies.items()}

            first = await call_both()
            assert 'Exame: Creatinina' in first['ocr']['lines']
            assert first['rag'] == [{'code': 'FICT-002', 'name': 'Glicemia de jejum', 'score': 1.0,
                                     'term': 'Glicose'}]

            raw = {name: await asyncio.open_connection('127.0.0.1', port) for name, port in SERVERS.items()}
            assert {name: await health(*raw[name], SERVERS[name]) for name in raw} == \
                {name: 'HTTP/1.1 200 OK' for name in raw}
            await asyncio.sleep(IDLE_SECONDS)
            still_open = {name: await health(*raw[name], SERVERS[name]) for name in raw}
            for _, writer in raw.values():
                writer.close()

            assert await call_both() == first  # same sessions, same answers after the idle wait
            assert still_open == {name: 'HTTP/1.1 200 OK' for name in raw}, \
                'a server closed an idle connection before the clients drop it (5 s): keep KEEP_ALIVE_SECONDS'
        finally:
            for toolset in toolsets.values():
                await toolset.close()

    asyncio.run(asyncio.wait_for(run(), timeout=90))  # a lost POST fails here instead of hanging the suite
