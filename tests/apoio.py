"""Helpers the test modules share, each written once: a server in a thread, stand-ins for the services
and for the Gemini API, a resolver for the spec's hosts, and the spec edits the transpiler tests make."""

import asyncio
import copy
import json
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'specs' / 'agent.json').read_text(encoding='utf-8'))

# The modules that serve OCR, RAG and the API on the spec's own ports (8000-8002): with pytest-xdist (-n) they
# run on one worker, one after the other (tests/portas.py waits for each port to be free).
SPEC_PORTS_MARKS = [
    pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image'),
    pytest.mark.xdist_group('spec-ports'),
    pytest.mark.filterwarnings(r'ignore:\[EXPERIMENTAL\]:UserWarning'),
    pytest.mark.filterwarnings('ignore::DeprecationWarning'),
]


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def serve(app, path='', port=None):
    """Run an ASGI app with uvicorn in a background thread; return (server, URL + path).

    An MCP server module (ocr, rag) is served as its SSE app at /sse: serve(ocr). Without `port`, a free one.
    """
    import uvicorn

    from mcp_servers.arguments import KEEP_ALIVE_SECONDS

    if hasattr(app, 'server') and hasattr(app, 'SECURITY'):
        app, path = app.server.sse_app(transport_security=app.SECURITY, host='127.0.0.1'), '/sse'
    port = port or free_port()
    # Keep-alive as in production (75 s): with uvicorn's 5 s, a POST can race the client's 5 s pool expiry.
    server = uvicorn.Server(
        uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', timeout_keep_alive=KEEP_ALIVE_SECONDS)
    )
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 60  # generous: a busy machine (CI, parallel builds) starts uvicorn slowly
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, f'port {port} did not start'
    return server, f'http://127.0.0.1:{port}{path}'


def read_through_servers(monkeypatch, folder, itens):
    """OCR and RAG in this process read `itens` from `folder`, as tests/load/manuscritos.py does with the
    compose: (its table, its problem count, the result of each image)."""
    from mcp_servers import ocr, rag
    from tests.load import manuscritos

    monkeypatch.setattr(ocr, 'SAMPLES_DIR', folder)
    servers = [serve(ocr), serve(rag)]
    try:
        results = asyncio.run(manuscritos.rodar(itens, servers[0][1], servers[1][1], concorrencia=2))
    finally:
        for server, _ in servers:
            server.should_exit = True
    table, problems = manuscritos.resumo(itens, results)
    return table, problems, results


def forbidden(why):
    """A stand-in that fails the test, saying why, when anything calls it."""

    def call(*args, **kwargs):
        raise AssertionError(why)

    return call


def loopback(names):
    """getaddrinfo where `names` (the spec's hosts: ocr, rag, api) are this machine; the rest as usual."""
    real = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host  # anyio passes it IDNA-encoded
        return real('127.0.0.1' if name in names else host, *args, **kwargs)

    return getaddrinfo


# --- the spec, edited --------------------------------------------------------------------------------


def spec_with(change, base=SPEC):
    """A spec (specs/agent.json by default) as JSON text after change(spec_dict) edits a copy of it."""
    data = copy.deepcopy(base)
    change(data)
    return json.dumps(data)


def problems(text):
    """What the transpiler says is wrong with a spec."""
    from transpiler import TranspileError, parse_spec

    with pytest.raises(TranspileError) as error:
        parse_spec(text)
    return error.value.problems


def booking(spec):
    """The kwargs of a spec's BookingPlugin: its roles and its policy."""
    return spec['plugins'][0]['kwargs']


# --- stand-ins ---------------------------------------------------------------------------------------


def stand_ins(order, hits):
    """The OCR, RAG and API tools as plain functions over one order: (calls by tool, the tools by name)."""
    calls = {'extract_exam_text': [], 'search_exams': [], 'create_appointment': []}

    def extract_exam_text(filename: str) -> dict:
        """Reads the order (stand-in for the OCR server)."""
        calls['extract_exam_text'].append(filename)
        return {
            'structuredContent': {
                'version': 1,
                'lines': order,
                'line_confidence': [96.0] * len(order),
                'line_intent': ['request'] * len(order),
                'contested_exams': [],
                'page_clean': True,
                'pii_masked': {},
            }
        }

    def search_exams(query: str, top_k: int = 3) -> dict:
        """Searches the catalog (stand-in for the RAG server)."""
        calls['search_exams'].append((query, top_k))
        return {'structuredContent': {'result': [hits[query]]}}

    def create_appointment(exams: list[dict]) -> dict:
        """Books the exams (stand-in for POST /appointments)."""
        calls['create_appointment'].append([exam['code'] for exam in exams])
        return {'id': 'a1', 'status': 'scheduled', 'exams': exams}

    return calls, {tool.__name__: tool for tool in (extract_exam_text, search_exams, create_appointment)}


class Quiet(BaseHTTPRequestHandler):
    """http.server's handler without its log line per request."""

    def log_message(self, *args):
        pass


class OfflineGemini(Quiet):
    """The Gemini API, offline: every model in `refused` answers `status`, any other 404 (not retried); the
    models called are kept in `calls`."""

    status, refused, calls = 503, frozenset(), []

    def do_POST(self):  # noqa: N802 (http.server's name)
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        model = self.path.split('/models/')[-1].split(':')[0]
        type(self).calls.append(model)
        code = self.status if model in self.refused else 404
        body = json.dumps(
            {
                'error': {
                    'code': code,
                    'message': 'This model is currently experiencing high demand',
                    'status': 'UNAVAILABLE',
                }
            }
        ).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def offline_gemini(monkeypatch, refused, status=503):
    """Point the Gemini client at an OfflineGemini on this machine; returns its server (shut it down after)."""
    server = ThreadingHTTPServer(('127.0.0.1', 0), OfflineGemini)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(OfflineGemini, 'status', status)
    monkeypatch.setattr(OfflineGemini, 'refused', frozenset(refused))
    monkeypatch.setattr(OfflineGemini, 'calls', [])
    monkeypatch.setenv('GOOGLE_GEMINI_BASE_URL', f'http://127.0.0.1:{server.server_port}')
    return server
