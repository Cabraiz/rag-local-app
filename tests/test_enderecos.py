"""Where the agent connects, checked where it runs: an agent.py whose toolsets name a host outside
ALLOWED_HOSTS does not import, `cli run` runs only the agent.py the spec generates today, every
server has to list its tools before a run, no redirect takes a toolset to another host, and an
allowed name that resolves to a local or metadata address stops the run, whose addresses stay
the ones checked. No API key or Gemini."""
import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams

import cli
import runtime
import transpiler.live
from runtime import rede
from tests.test_spec_generica import REAL_TOOLS, SPEC, VARIANT, FakeStream, servers_answer, spec_with  # noqa: F401
from transpiler import TranspileError, load_root_agent, parse_spec, transpile
from transpiler.live import live_tools

SPEC_FILE = Path(__file__).resolve().parents[1] / 'specs' / 'agent.json'

# --- the runtime checks the hosts again, when agent.py is imported --------------------------------


@pytest.mark.parametrize('allowed, url', [
    (None, 'http://ocr:8001/sse'), (None, 'http://OCR:8001/sse'), (None, 'https://ocr:8001/sse'),
    (None, 'http://ocr:8002/sse'), (None, 'http://ocr/sse'), (None, 'http://ocr.:8001/sse'),
    (None, 'http://169.254.169.254/sse'), (None, 'http://localhost:2375/sse'), (None, 'http://ocr:99999/sse'),
    ('clinica.interna', 'https://clinica.interna:8443/sse'), ('clinica.interna:8443', 'https://clinica.interna/sse'),
    ('clinica.interna:8443, ocr:8001', 'http://ocr:8001/sse'), (',', 'http://rag:8002/sse'),
])
def test_the_runtime_allows_exactly_the_hosts_the_transpiler_allows(monkeypatch, allowed, url):
    if allowed is None:
        monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    else:
        monkeypatch.setenv('ALLOWED_HOSTS', allowed)
    try:
        parse_spec(spec_with(lambda s: s['servers']['ocr'].update(url=url)))
        transpiler_allows = True
    except TranspileError as error:
        transpiler_allows = not any(problem.startswith('servers.ocr.url') for problem in error.problems)
    try:
        runtime.McpToolset(connection_params=SseConnectionParams(url=url), tool_filter=['extract_exam_text'])
        runtime_allows = True
    except ValueError as error:
        assert 'fora de ALLOWED_HOSTS' in str(error)
        runtime_allows = False
    assert runtime_allows == transpiler_allows


@pytest.mark.parametrize('openapi_url, base_url', [
    ('http://169.254.169.254/openapi.json', 'http://169.254.169.254'),
    ('http://api:8000/openapi.json', 'http://evil.example:8000'),  # the contract from the API, the POSTs elsewhere
])
def test_an_openapi_toolset_outside_allowed_hosts_is_refused(monkeypatch, openapi_url, base_url):
    monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    with pytest.raises(ValueError, match='fora de ALLOWED_HOSTS'):
        runtime.LiveOpenAPIToolset(openapi_url=openapi_url, base_url=base_url, tool_filter=['create_appointment'])


def test_an_agent_py_generated_for_another_allowed_hosts_does_not_import(tmp_path, servers_answer, monkeypatch):
    monkeypatch.setenv('ALLOWED_HOSTS', 'ocr:8001,rag:8002,clinica.interna:8443')
    (tmp_path / 'spec.json').write_text(spec_with(
        lambda s: s['servers']['api'].update(openapi_url='https://clinica.interna:8443/openapi.json')), encoding='utf-8')
    transpile(tmp_path / 'spec.json', tmp_path / 'agent.py')
    monkeypatch.delenv('ALLOWED_HOSTS')  # the deployment went back to the default
    with pytest.raises(TranspileError) as error:
        load_root_agent(tmp_path / 'agent.py')
    assert 'host "clinica.interna:8443" fora de ALLOWED_HOSTS (ocr:8001,rag:8002,api:8000)' in error.value.problems[0]


def test_an_agent_py_edited_to_reach_another_host_does_not_import(tmp_path, monkeypatch):
    monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    agent = tmp_path / 'agent.py'
    transpile(SPEC_FILE, agent)
    text = agent.read_text(encoding='utf-8')
    agent.write_text(text.replace("'http://rag:8002/sse'", "'http://169.254.169.254/sse'"), encoding='utf-8')
    with pytest.raises(TranspileError) as error:
        load_root_agent(agent)
    assert 'host "169.254.169.254:80" fora de ALLOWED_HOSTS' in error.value.problems[0]


# --- cli run runs the agent.py its spec generates -------------------------------------------------


class ServicesChecked(cli.RunError):
    """Raised in place of the service check: the run got past check_agent."""


def services_reached(spec):
    raise ServicesChecked('serviços conferidos')


@pytest.fixture
def run_argv(tmp_path, monkeypatch):
    """A transpiled agent.py, a key, and a service check that only says it was reached."""
    monkeypatch.setenv('GOOGLE_API_KEY', 'not-used')
    monkeypatch.setattr(cli, 'check_services', services_reached)
    agent = tmp_path / 'agent.py'
    transpile(SPEC_FILE, agent)
    return agent, ['run', '--image', 'pedido.png', '--agent', str(agent)]


def test_cli_run_goes_on_with_the_agent_py_its_spec_generates(run_argv, capsys):
    agent, argv = run_argv
    assert cli.main(argv) == 2
    assert capsys.readouterr().err == 'Erro: serviços conferidos\n'
    agent.write_bytes(agent.read_bytes().replace(b'\n', b'\r\n'))  # a Windows checkout
    assert cli.main(argv) == 2
    assert capsys.readouterr().err == 'Erro: serviços conferidos\n'


@pytest.mark.parametrize('edit', [
    lambda text: text.replace('min_confidence=0.9', 'min_confidence=0.1'),
    lambda text: text.replace("'http://rag:8002/sse'", "'http://rag:8002/sse?x'"),
    lambda text: text + '\n# edited\n',
    None,  # another spec's agent.py
])
def test_cli_run_refuses_an_agent_py_its_spec_does_not_generate(run_argv, tmp_path, capsys, edit):
    agent, argv = run_argv
    if edit is None:
        transpile(VARIANT, agent)
    else:
        text = agent.read_text(encoding='utf-8')
        assert edit(text) != text
        agent.write_text(edit(text), encoding='utf-8')
    assert cli.main(argv) == 2
    assert capsys.readouterr().err == (
        f'Erro: {agent} não é o que specs/agent.json gera hoje (gerado de outra spec, antes de uma mudança ou '
        f'editado à mão); gere de novo: docker compose run --rm agent python -m cli transpile specs/agent.json '
        f'--output {agent}\n')


def test_cli_run_imports_the_agent_py_it_compared_even_if_the_file_changes_after(run_argv, monkeypatch, capsys):
    agent, argv = run_argv
    imported = []

    def swap_the_file(spec):  # between the check and the import
        agent.write_text(agent.read_text(encoding='utf-8') + "\nraise RuntimeError('trocado depois da checagem')\n",
                         encoding='utf-8')

    async def run_agent(root_agent, image, spec, found):
        imported.append(root_agent.name)
    monkeypatch.setattr(cli, 'check_services', swap_the_file)
    monkeypatch.setattr(cli, 'run_agent', run_agent)
    cli.main(argv)
    assert imported == ['clinic_scheduler'] and 'trocado' not in capsys.readouterr().err


# --- every server lists its tools before a run ----------------------------------------------------


@pytest.mark.parametrize('server, expected', [
    ('ocr', 'servers.ocr: http://ocr:8001/sse respondeu, mas não listou as ferramentas (é um servidor MCP?)'),
    ('rag', 'servers.rag: http://rag:8002/sse respondeu, mas não listou as ferramentas (é um servidor MCP?)'),
    ('api', 'servers.api: http://api:8000/openapi.json respondeu, mas não listou as operações (é um /openapi.json?)'),
])
def test_cli_run_stops_when_a_server_answers_but_lists_nothing(tmp_path, servers_answer, monkeypatch, capsys,
                                                              server, expected):
    agent = tmp_path / 'agent.py'
    transpile(SPEC_FILE, agent)
    servers_answer[server] = None  # the GET answers; list_tools (or /openapi.json) does not

    def must_not_run(*args, **kwargs):
        raise AssertionError('Gemini called without the tools checked')

    monkeypatch.setattr(socket, 'getaddrinfo', resolver(COMPOSE_DNS))
    monkeypatch.setenv('GOOGLE_API_KEY', 'not-used')
    monkeypatch.setattr(cli.httpx, 'stream', lambda *args, **kwargs: FakeStream())
    monkeypatch.setattr(cli, 'run_agent', must_not_run)
    assert cli.main(['run', '--image', 'pedido.png', '--agent', str(agent)]) == 2
    assert capsys.readouterr().err == f'Erro: {expected}\n'


def test_transpile_still_keeps_the_declared_list_of_a_server_that_does_not_answer(tmp_path, servers_answer):
    servers_answer['rag'] = None
    assert transpile(SPEC_FILE, tmp_path / 'agent.py').name == 'clinic_scheduler'


def test_the_servers_are_asked_also_from_inside_an_event_loop(servers_answer):
    spec = parse_spec(json.dumps(SPEC))

    async def inside_a_loop():
        return live_tools(spec)
    assert asyncio.run(inside_a_loop()) == REAL_TOOLS


# --- no redirect leaves the allowed host ----------------------------------------------------------


class Recorder(BaseHTTPRequestHandler):
    """GET /sse and /openapi.json redirect to `target`; any other path is recorded and answers 404."""
    target = ''
    hits: list[str] = []

    def do_GET(self):  # noqa: N802 (http.server's name)
        if self.path in ('/sse', '/openapi.json'):
            self.send_response(307)
            self.send_header('Location', self.target + self.path.replace('.json', '2.json').replace('/sse', '/sse2'))
            self.end_headers()
            return
        type(self).hits.append(self.path)
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def two_origins(monkeypatch):
    """(redirecting server URL, hits on it, hits on the other origin): the first redirects to `to`."""
    monkeypatch.setenv('ALLOWED_HOSTS', '127.0.0.1')
    servers = []
    for name in ('Here', 'Other'):
        handler = type(name, (Recorder,), {'hits': []})
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    here, other = servers

    def redirect(to_other):
        here.RequestHandlerClass.target = f'http://127.0.0.1:{(other if to_other else here).server_port}'
        return f'http://127.0.0.1:{here.server_port}', here.RequestHandlerClass.hits, other.RequestHandlerClass.hits
    yield redirect
    for server in servers:
        server.shutdown()
        server.server_close()


def test_the_mcp_client_follows_a_redirect_only_within_the_same_origin(two_origins):
    url, here, other = two_origins(to_other=False)
    with pytest.raises(Exception):  # noqa: B017 (the SDK wraps the 404 in its own error group)
        asyncio.run(transpiler.live.mcp_tools(url + '/sse'))
    assert here == ['/sse2']  # a redirect on the same host and port is followed
    url, here, other = two_origins(to_other=True)
    with pytest.raises(Exception):  # noqa: B017
        asyncio.run(transpiler.live.mcp_tools(url + '/sse'))
    assert other == []  # to another port, it is not: nothing reached the other origin


def test_the_openapi_toolset_follows_no_redirect(two_origins):
    url, here, other = two_origins(to_other=True)
    toolset = runtime.LiveOpenAPIToolset(openapi_url=url + '/openapi.json', base_url=url, tool_filter=['x'])
    with pytest.raises(httpx.HTTPStatusError, match='307'):
        asyncio.run(toolset.get_tools())
    assert other == []


# --- a name that resolves to a local or metadata address ------------------------------------------

COMPOSE_DNS = {'ocr': ['172.18.0.3'], 'rag': ['10.0.0.6'], 'api': ['192.168.1.10']}  # what Docker's DNS gives


def resolver(answers):
    """getaddrinfo where each name in `answers` resolves to its next address; the rest as usual."""
    real = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        if host not in answers:
            return real(host, port, *args, **kwargs)
        address = answers[host].pop(0) if len(answers[host]) > 1 else answers[host][0]
        if address is None:
            raise socket.gaierror(socket.EAI_NONAME, 'Name or service not known')
        family, sockaddr = (socket.AF_INET6, (address, port, 0, 0)) if ':' in address else (socket.AF_INET, (address, port))
        return [(family, socket.SOCK_STREAM, 6, '', sockaddr)]
    return getaddrinfo


@pytest.mark.parametrize('address', ['127.0.0.1', '127.1.2.3', '169.254.169.254', '0.0.0.0', '::1', '::ffff:127.0.0.1',
                                     'fe80::1', 'fd00:ec2::254', 'fc00::1', '100.100.100.200', '100.64.0.1', '168.63.129.16'])
def test_cli_run_stops_when_an_allowed_name_resolves_to_a_local_address(tmp_path, servers_answer, monkeypatch, capsys,
                                                                       address):
    monkeypatch.setenv('ALLOWED_HOSTS', 'ocr:8001,rag:8002,clinica.exemplo:8443')
    (tmp_path / 'spec.json').write_text(spec_with(
        lambda s: s['servers']['api'].update(openapi_url='https://clinica.exemplo:8443/openapi.json')), encoding='utf-8')
    transpile(tmp_path / 'spec.json', tmp_path / 'agent.py')

    def must_not_run(*args, **kwargs):
        raise AssertionError('a request was sent before the addresses were checked')

    monkeypatch.setattr(socket, 'getaddrinfo', resolver({'clinica.exemplo': [address], 'ocr': ['10.0.0.5'], 'rag': ['10.0.0.6']}))
    monkeypatch.setenv('GOOGLE_API_KEY', 'not-used')
    monkeypatch.setattr(cli.httpx, 'stream', must_not_run)
    monkeypatch.setattr(cli, 'run_agent', must_not_run)
    argv = ['run', '--image', 'pedido.png', '--spec', str(tmp_path / 'spec.json'), '--agent', str(tmp_path / 'agent.py')]
    assert cli.main(argv) == 2
    assert capsys.readouterr().err == (
        f'Erro: servers.api.openapi_url: "clinica.exemplo" resolve para {address}, um endereço local ou de metadados '
        'de nuvem (ex.: 127.0.0.1, 169.254.169.254, fd00:ec2::254); só um host escrito como esse endereço (IP ou '
        'localhost) e listado em ALLOWED_HOSTS pode apontar para ele\n')


def test_the_private_addresses_of_the_compose_network_are_allowed(monkeypatch):
    monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    monkeypatch.setattr(socket, 'getaddrinfo', resolver(COMPOSE_DNS))
    with rede.pinned_names() as pins:
        assert transpiler.live.check_addresses(parse_spec(json.dumps(SPEC))) == []
    assert pins == COMPOSE_DNS


def test_a_name_that_does_not_resolve_keeps_no_address_for_the_whole_run(monkeypatch):
    # A DNS that fails during the check and answers 127.0.0.1 later must not reach loopback.
    monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    monkeypatch.setattr(socket, 'getaddrinfo', resolver(COMPOSE_DNS | {'rag': [None, '127.0.0.1']}))
    with rede.pinned_names() as pins:
        assert transpiler.live.check_addresses(parse_spec(json.dumps(SPEC))) == []
        assert pins['rag'] == []
        with pytest.raises(socket.gaierror, match='"rag" não resolveu no início da execução'):
            socket.getaddrinfo('rag', 8002, type=socket.SOCK_STREAM)  # the DNS now answers 127.0.0.1
        with pytest.raises(httpx.ConnectError):  # what the GET of check_services sees: "fora do ar"
            httpx.get('http://rag:8002/sse', timeout=1)


def test_a_host_written_as_the_address_is_not_resolved(monkeypatch):
    monkeypatch.setenv('ALLOWED_HOSTS', '127.0.0.1,localhost')

    def on_this_machine(spec):
        spec['servers']['ocr']['url'] = 'http://127.0.0.1:8001/sse'
        spec['servers']['rag']['url'] = 'http://localhost:8002/sse'
        spec['servers']['api']['openapi_url'] = 'http://127.0.0.1:8000/openapi.json'

    def must_not_resolve(*args, **kwargs):
        raise AssertionError('resolved a host that ALLOWED_HOSTS lists by its address')
    spec = parse_spec(spec_with(on_this_machine))
    monkeypatch.setattr(socket, 'getaddrinfo', must_not_resolve)
    assert transpiler.live.check_addresses(spec) == []


def test_the_run_keeps_the_address_it_checked_when_the_dns_answer_changes(monkeypatch):
    monkeypatch.setenv('ALLOWED_HOSTS', 'ocr:8001,rag:8002,clinica.exemplo:8443')
    spec = parse_spec(spec_with(lambda s: s['servers']['api'].update(openapi_url='https://clinica.exemplo:8443/openapi.json')))
    rebinding = resolver({'clinica.exemplo': ['10.0.0.7', '127.0.0.1'], 'ocr': ['10.0.0.5'], 'rag': ['10.0.0.6']})
    monkeypatch.setattr(socket, 'getaddrinfo', rebinding)
    with rede.pinned_names() as pins:
        assert transpiler.live.check_addresses(spec) == []
        assert pins == {'ocr': ['10.0.0.5'], 'rag': ['10.0.0.6'], 'clinica.exemplo': ['10.0.0.7']}
        # The DNS now answers 127.0.0.1; every client of the run still gets the address checked.
        assert [info[4] for info in socket.getaddrinfo('CLINICA.exemplo', 443, type=socket.SOCK_STREAM)] == [('10.0.0.7', 443)]
        assert [info[4][0] for info in socket.getaddrinfo(b'clinica.exemplo', 8443, type=socket.SOCK_STREAM)] == ['10.0.0.7']
    assert socket.getaddrinfo is rebinding  # restored after the run
    assert [info[4][0] for info in socket.getaddrinfo('clinica.exemplo', 443)] == ['127.0.0.1']
