"""The transpiler is not tied to one pipeline: servers with any names on allowed hosts, tools checked on
the servers that answer, ADK workflows and App plugins the spec names, a generic agent with no booking
code at all, and, with the booking plugin, the tool roles it declares, a spec that lists exams without
booking and a 3-agent variant with other names and another order. No API key or Gemini."""
import ast
import asyncio
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.agents import LlmAgent, ParallelAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import FunctionTool
from google.genai import types

import cli
import transpiler.live
from runtime.plugin import BookingPlugin
from tests.test_agent_mcp import servers  # noqa: F401  (the real MCP servers, as processes)
from tests.versionados import EXAMPLE_SPECS
from transpiler import TranspileError, load_root_agent, load_spec, parse_spec, render, transpile
from transpiler.live import live_tools

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / 'specs'
SPEC = json.loads((SPECS / 'agent.json').read_text(encoding='utf-8'))
LISTING, VARIANT = SPECS / 'listar-exames.json', SPECS / 'agendar-variante.json'
GENERIC = SPECS / 'exemplo-generico.json'
# The generic example's server is not a compose service: the deployment allows its host.
WITH_DOCS = 'ocr:8001,rag:8002,api:8000,docs:8010'
REAL_TOOLS = {  # what the compose servers answer (input schemas cut to the properties)
    'ocr': {'extract_exam_text': {'properties': {'filename': {}}}},
    'rag': {'search_exams': {'properties': {'query': {}, 'top_k': {}}}},
    'api': {'create_appointment': {}, 'get_appointment': {}, 'health': {}},
}


def spec_with(change, base=SPEC):
    data = copy.deepcopy(base)
    change(data)
    return json.dumps(data)


def problems(text):
    with pytest.raises(TranspileError) as error:
        parse_spec(text)
    return error.value.problems


def booking(spec):
    """The kwargs of a spec's BookingPlugin: its roles and its policy."""
    return spec['plugins'][0]['kwargs']


@pytest.fixture
def servers_answer(monkeypatch):
    """The servers answer by their host, with REAL_TOOLS unless a test changes `answers`."""
    answers = copy.deepcopy(REAL_TOOLS)

    async def by_host(url):
        host = re.match(r'https?://([^:/]+)', url)[1]
        if answers.get(host) is None:
            raise OSError('connection refused')
        return answers[host]
    monkeypatch.setattr(transpiler.live, 'mcp_tools', by_host)
    monkeypatch.setattr(transpiler.live, 'api_operations', by_host)
    return answers


# --- servers: any name, on an allowed host -------------------------------------------------------

def test_servers_take_any_name_and_the_roles_follow_the_spec(tmp_path):
    transpile(VARIANT, tmp_path / 'agent.py')
    app = load_root_agent(tmp_path / 'agent.py', name='app')
    root_agent, callbacks = app.root_agent, BookingPlugin.of(app)
    assert [agent.name for agent in root_agent.sub_agents] == ['ler_e_buscar', 'revisar', 'agendar']
    assert (callbacks.ocr_tool, callbacks.search_tool, callbacks.booking_tool) == (
        'extract_exam_text', 'search_exams', 'create_appointment')
    first, review, book = root_agent.sub_agents
    assert [type(tool).__name__ for tool in first.tools] == ['McpToolset', 'McpToolset'] and review.tools == []
    assert type(book.tools[0]).__name__ == 'LiveOpenAPIToolset'


@pytest.mark.parametrize('allowed, url, ok', [
    (None, 'http://rag:8002/sse', True),  # the default list: the three compose services, each on its port
    (None, 'http://rag:9999/sse', False),  # ... and only on that port
    (None, 'http://localhost:8002/sse', False),  # this machine is not in the default list
    (None, 'http://localhost:2375/sse', False),  # a local service (here, the Docker API)
    (None, 'http://127.0.0.1:6379/sse', False),
    ('', 'http://rag:8002/sse', True),  # empty: the default list
    (' , ', 'http://rag:8002/sse', True),  # only separators: the default list, not an empty one
    (' , ', 'http://localhost:8002/sse', False),
    ('ocr:8001,api:8000,127.0.0.1', 'http://127.0.0.1:6379/sse', True),  # "host" alone: any port, if listed
    ('ocr,api,rag-interno:8002', 'http://rag-interno:8002/sse', True),  # the deployment adds a host and port
    ('ocr,api,rag-interno:8002', 'http://rag-interno:8003/sse', False),  # only that port
    ('ocr,api,rag-interno:8002', 'http://rag:8002/sse', False),  # the list replaces the default one
    (None, 'http://169.254.169.254/sse', False),
    (None, 'http://metadata.google.internal/sse', False),
])
def test_allowed_hosts_come_from_the_deployment_not_from_the_spec(monkeypatch, allowed, url, ok):
    if allowed is None:
        monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    else:
        monkeypatch.setenv('ALLOWED_HOSTS', allowed)
    text = spec_with(lambda s: s['servers']['rag'].update(url=url))
    if ok:
        assert parse_spec(text).servers['rag'].url == url
    else:
        assert any(problem.startswith('servers.rag.url: host ') and 'fora de ALLOWED_HOSTS' in problem
                   for problem in problems(text))


@pytest.mark.parametrize('allowed', ['ocr:abc', 'ocr,rag:', 'http://ocr:8001', 'ocr/sse'])
def test_a_malformed_allowed_hosts_entry_is_an_error_not_ignored(monkeypatch, allowed):
    monkeypatch.setenv('ALLOWED_HOSTS', allowed)
    found = problems(spec_with(lambda s: None))
    assert found and all(problem.startswith('ALLOWED_HOSTS: "') and 'não é host nem host:porta' in problem
                         for problem in found), found


def test_the_default_allowed_hosts_are_named_in_the_message(monkeypatch):
    monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    found = problems(spec_with(lambda s: s['servers']['rag'].update(url='http://localhost:2375/sse')))
    assert 'servers.rag.url: host "localhost:2375" fora de ALLOWED_HOSTS (ocr:8001, rag:8002, api:8000); '            'quem implanta pode incluí-lo em ALLOWED_HOSTS' in found


@pytest.mark.parametrize('change, expected', [
    (lambda s: s['servers']['rag'].update(openapi_url='http://rag:8002/openapi.json'),
     'servers.rag: declare url e tools (servidor MCP) ou openapi_url e operations (API OpenAPI), um dos dois'),
    (lambda s: s['servers']['rag'].pop('tools'), 'servers.rag: declare url e tools'),
    (lambda s: s['servers'].update(OCR=s['servers'].pop('ocr')), 'servers: "OCR": use minúsculas'),
    (lambda s: s['servers']['rag'].update(tools=['search-exams']), 'servers.rag.tools: "search-exams": use minúsculas'),
    (lambda s: s['servers']['rag'].update(tools=['search_exams', 'search_exams']), 'servers.rag.tools: nome repetido'),
    (lambda s: s['servers']['ocr'].update(tools=['extract_exam_text', 'search_exams']),
     'servers: "search_exams" está em ocr e rag; um nome de ferramenta, um servidor'),
    (lambda s: s.update(servers={}), 'servers: lista vazia'),
    (lambda s: s.update(servers=[]), 'servers: deve ser um objeto JSON'),
    (lambda s: s['servers']['rag'].update(url='http://rag:99999/sse'),
     'servers.rag.url: porta inválida (99999); use de 1 a 65535'),
    (lambda s: s['servers']['rag'].update(url='http://ocr:0/sse'),  # not the default port 80
     'servers.rag.url: porta inválida (0); use de 1 a 65535'),
    (lambda s: s['servers']['api'].update(openapi_url='http://api:00/openapi.json'),
     'servers.api.openapi_url: porta inválida (00); use de 1 a 65535'),
])
def test_each_server_is_one_kind_with_valid_names(change, expected):
    found = problems(spec_with(change))
    assert any(problem.startswith(expected) for problem in found), found


# --- the booking plugin's roles: which tool reads, searches and books ------------------------------

def with_roles(roles):
    def change(spec):
        for role in ('read', 'search', 'book'):
            booking(spec).pop(role, None)
        booking(spec).update(roles)
    return change


@pytest.mark.parametrize('roles, expected', [
    ({'read': 'ocr.extract_exam_text', 'search': 'rag.search_exams', 'book': 'api.get_appointment'},
     'plugins.0.kwargs.book: "api.get_appointment" não está nas tools de nenhum agente'),
    ({'read': 'ocr.extract_exam_text', 'search': 'api.create_appointment', 'book': 'rag.search_exams'},
     'plugins.0.kwargs.search: "api.create_appointment" precisa ser uma ferramenta de um servidor MCP'),
    ({'read': 'ocr.extract_exam_text', 'search': 'api.create_appointment', 'book': 'rag.search_exams'},
     'plugins.0.kwargs.book: "rag.search_exams" precisa ser uma operação de uma API OpenAPI'),
    ({'read': 'ocr.extract_exam_text', 'book': 'api.create_appointment'},
     'plugins.0.kwargs.book: agendar pede read e search'),
    ({'search': 'rag.search_exams'}, 'plugins.0.kwargs.search: a busca é medida contra o pedido lido'),
    ({'read': 'ocr.extract_exam_text', 'search': 'ocr.extract_exam_text'}, 'plugins.0.kwargs: cada papel usa uma ferramenta diferente'),
    ({'read': 'ocr', 'search': 'rag.search_exams'}, 'plugins.0.kwargs.read: "ocr" não está nas tools de nenhum agente'),
    ({'read': 'ocr.extract_exam_text', 'search': 'rag.search_exams', 'pay': 'api.create_appointment'},
     'plugins.0.kwargs.pay: campo não permitido'),
])
def test_roles_are_checked_against_the_agents_and_the_kind_of_server(roles, expected):
    found = problems(spec_with(with_roles(roles)))
    assert any(problem.startswith(expected) for problem in found), found


def test_booking_needs_the_reading_and_the_search_in_earlier_agents():
    def book_first(spec):
        spec['agents'][2]['instruction'] = 'Agende os exames do pedido chamando a ferramenta de agendamento.'
        spec['agents'].insert(0, spec['agents'].pop(2))
    found = problems(spec_with(book_first, json.loads(VARIANT.read_text(encoding='utf-8'))))
    assert found == ['agents.0.tools: clinica.create_appointment só agenda códigos achados no catálogo: antes dele, '
                     'agentes anteriores precisam usar leitor.extract_exam_text e catalogo.search_exams']



# --- an API operation reaches an agent only as the book role: fail closed -------------------------

def renamed_api(spec):  # the API server under another name, and the plugin's book left on the old one
    spec['servers']['clinica'] = spec['servers'].pop('api')
    spec['agents'][2]['tools'] = ['clinica.create_appointment']


def another_operation(index):
    def change(spec):  # book is declared, and an agent also gets a second API operation
        spec['servers']['api']['operations'].append('get_appointment')
        spec['agents'][index]['tools'].append('api.get_appointment')
    return change


def listing_reaches_the_api(spec):
    spec['servers']['api'] = {'openapi_url': 'http://api:8000/openapi.json', 'operations': ['create_appointment']}
    spec['agents'][1]['tools'].append('api.create_appointment')


@pytest.mark.parametrize('change, base, tool', [
    (renamed_api, SPEC, 'agents.2.tools: "clinica.create_appointment"'),
    (another_operation(1), SPEC, 'agents.1.tools: "api.get_appointment"'),
    (another_operation(2), SPEC, 'agents.2.tools: "api.get_appointment"'),
    (listing_reaches_the_api, json.loads(LISTING.read_text(encoding='utf-8')), 'agents.1.tools: "api.create_appointment"'),
])
def test_an_api_operation_outside_the_book_role_never_reaches_an_agent(change, base, tool):
    # Only the book role's call is checked by the runtime; any other operation would go out unchecked,
    # so the spec is refused.
    found = problems(spec_with(change, base))
    assert f'{tool} é uma operação de API e só a de plugins.0.kwargs.book chega a um agente, porque só ela passa pela '            'checagem dos códigos antes da chamada; declare-a em plugins.0.kwargs.book ou tire-a do agente' in found, found



def roles_without_book(spec):  # the plugin with no book, and the API operation still given to an agent
    booking(spec).pop('book')


def second_api_server(spec):  # another OpenAPI server that writes, next to the book role's
    spec['servers']['clinica2'] = {'openapi_url': 'http://api:8000/openapi.json', 'operations': ['create_appointment_v2']}
    spec['agents'][2]['tools'].append('clinica2.create_appointment_v2')


@pytest.mark.parametrize('change, tool', [
    (roles_without_book, 'agents.2.tools: "api.create_appointment"'),
    (second_api_server, 'agents.2.tools: "clinica2.create_appointment_v2"'),
])
def test_an_api_operation_without_the_book_role_is_refused(change, tool):
    found = problems(spec_with(change))
    assert any(problem.startswith(f'{tool} é uma operação de API e só a de plugins.0.kwargs.book') for problem in found), found


def test_a_tool_without_a_role_is_refused():
    def extra_mcp_tool(spec):  # an MCP tool of the OCR server that takes a free file name, outside the roles
        spec['servers']['ocr']['tools'].append('read_any_file')
        spec['agents'][0]['tools'].append('ocr.read_any_file')
    assert problems(spec_with(extra_mcp_tool)) == [
        'agents.0.tools: "ocr.read_any_file" não tem papel em plugins.0.kwargs (read, search ou book); o runtime só deixa '
        'passar as ferramentas dos papéis, cada uma conferida: declare o papel ou tire-a do agente']


def booking_contract(change=None):
    """The real API's description of create_appointment (its /openapi.json), changed by a test."""
    from api.main import app
    from transpiler.live import operations
    document = copy.deepcopy(app.openapi())
    if change:
        change(document)
    return operations(document)


def no_key(document):
    document['paths']['/appointments']['post']['parameters'] = []


def body_with_notes(document):
    request = document['components']['schemas']['AppointmentRequest']
    request['properties']['notas'] = {'type': 'string'}
    request.pop('additionalProperties')


def body_open(document):
    document['components']['schemas']['AppointmentRequest'].pop('additionalProperties')


def body_without_codes(document):
    document['components']['schemas']['RequestedExam']['properties'].pop('code')


def under_a_path_parameter(document):  # /clinics/{clinic_id}/appointments: a value the runtime never sends
    paths = document['paths']
    paths['/clinics/{clinic_id}/appointments'] = paths.pop('/appointments')
    paths['/clinics/{clinic_id}/appointments']['post']['parameters'].append(
        {'name': 'clinic_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}})


@pytest.mark.parametrize('change, expected', [
    (None, []),
    (no_key, ['plugins.0.kwargs.book: "api.create_appointment" não recebe Idempotency-Key, que o runtime envia para que um POST '
              'repetido não agende duas vezes']),
    (body_with_notes, ['plugins.0.kwargs.book: "api.create_appointment" aceita no corpo outros campos além de exams; o runtime só '
                       'confere os exames, então a API precisa recusar o resto (additionalProperties: false)']),
    (body_open, ['plugins.0.kwargs.book: "api.create_appointment" aceita no corpo outros campos além de exams; o runtime só '
                 'confere os exames, então a API precisa recusar o resto (additionalProperties: false)']),
    (body_without_codes, ['plugins.0.kwargs.book: "api.create_appointment" não recebe um corpo com exams[].code, que o runtime '
                          'confere']),
    (under_a_path_parameter, ['plugins.0.kwargs.book: "api.create_appointment" pede clinic_id (no caminho ou obrigatório), que o '
                              'runtime não envia: ele manda só exams e a Idempotency-Key']),
])
def test_the_booking_operation_takes_only_what_the_runtime_sends(tmp_path, servers_answer, change, expected):
    # The live check reads the API's own description of the booking operation: our key, and a body of
    # exams with their codes and nothing else, or the transpile stops.
    servers_answer['api'] = booking_contract(change)
    (tmp_path / 'spec.json').write_text(json.dumps(SPEC), encoding='utf-8')
    if not expected:
        transpile(tmp_path / 'spec.json', tmp_path / 'agent.py')
        return
    with pytest.raises(TranspileError) as error:
        transpile(tmp_path / 'spec.json', tmp_path / 'agent.py')
    assert error.value.problems == expected


@pytest.mark.parametrize('name, reply, called', [
    ('create_appointment', {'id': 'a1'}, True),
    ('get_appointment', {'id': 'a1'}, True),  # a tool without a role that answered: it may have written
    ('get_appointment', {'blocked': 'sem papel'}, False),  # refused by the runtime: nothing was sent
    ('search_exams', {'result': []}, False),
    ('extract_exam_text', {'lines': []}, False),
])
def test_any_tool_that_may_have_written_is_never_called_nothing_written(name, reply, called):
    found = cli.new_found()
    cli.note_reply(found, parse_spec(json.dumps(SPEC)), SimpleNamespace(name=name, response=reply))
    assert found['api_called'] is called

@pytest.mark.parametrize('spec_file', [SPECS / name for name in EXAMPLE_SPECS], ids=lambda path: path.name)
def test_generated_code_fits_120_columns_imports_only_what_it_uses_and_keeps_each_instruction(spec_file, monkeypatch):
    monkeypatch.setenv('ALLOWED_HOSTS', WITH_DOCS)
    spec = load_spec(spec_file)
    source = render(spec, spec_file)
    assert max(map(len, source.splitlines())) <= 120
    tree = ast.parse(source)
    imported = {alias.asname or alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                for alias in node.names}
    assert imported <= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    # A long instruction is written as adjacent literals: the same text as in the spec.
    written = [ast.literal_eval(node.args[0]) for node in ast.walk(tree)
               if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'guarded']
    assert written == [agent.instruction for agent in spec.agents]

# --- tools checked on the servers that answer -----------------------------------------------------

def test_transpile_checks_the_tools_on_the_servers_that_answer(tmp_path, servers_answer, capsys):
    assert cli.main(['transpile', str(SPECS / 'agent.json'), '--output', str(tmp_path / 'agent.py')]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == 'Ferramentas conferidas nos servidores: ocr, rag, api'


@pytest.mark.parametrize('change, answers, expected', [
    (lambda s: s['servers']['rag'].update(tools=['search_exams', 'nao_existe']), {},
     'servers.rag.tools: "nao_existe" não existe neste servidor (use search_exams)'),
    (lambda s: s['servers']['api'].update(operations=['create_appointment', 'delete_everything']), {},
     'servers.api.operations: "delete_everything" não existe neste servidor (use create_appointment, get_appointment, health)'),
    (lambda s: None, {'rag': {'search_exams': {'properties': {'query': {}}}}},
     'plugins.0.kwargs.search: "rag.search_exams" não recebe top_k, que o runtime envia'),
    (lambda s: None, {'ocr': {'extract_exam_text': {'properties': {'path': {}}}}},
     'plugins.0.kwargs.read: "ocr.extract_exam_text" não recebe filename, que o runtime envia'),
])
def test_a_tool_the_server_does_not_have_fails_in_transpile(tmp_path, servers_answer, change, answers, expected):
    servers_answer.update(answers)
    (tmp_path / 'spec.json').write_text(spec_with(change), encoding='utf-8')
    with pytest.raises(TranspileError) as error:
        transpile(tmp_path / 'spec.json', tmp_path / 'agent.py')
    assert error.value.problems == [expected]


def test_a_server_that_does_not_answer_keeps_the_declared_list(tmp_path, servers_answer, capsys):
    servers_answer['api'] = None
    (tmp_path / 'spec.json').write_text(spec_with(
        lambda s: s['servers']['api'].update(operations=['create_appointment', 'nao_existe'])), encoding='utf-8')
    assert cli.main(['transpile', str(tmp_path / 'spec.json'), '--output', str(tmp_path / 'agent.py')]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == 'Ferramentas conferidas nos servidores: ocr, rag'


def test_cli_run_asks_the_servers_again_before_calling_gemini(tmp_path, servers_answer, monkeypatch, capsys):
    # Transpiled offline with a typo; at run time the server answers and the run stops before Gemini.
    servers_answer['rag'] = None
    (tmp_path / 'spec.json').write_text(spec_with(
        lambda s: s['servers']['rag'].update(tools=['search_exams', 'search_exam'])), encoding='utf-8')
    transpile(tmp_path / 'spec.json', tmp_path / 'agent.py')
    servers_answer['rag'] = REAL_TOOLS['rag']

    def must_not_run(*args, **kwargs):
        raise AssertionError('Gemini called with a tool the server does not have')

    monkeypatch.setenv('GOOGLE_API_KEY', 'not-used')
    monkeypatch.setattr(cli.httpx, 'stream', lambda *args, **kwargs: FakeStream())
    monkeypatch.setattr(cli, 'run_agent', must_not_run)
    argv = ['run', '--image', 'pedido.png', '--spec', str(tmp_path / 'spec.json'), '--agent', str(tmp_path / 'agent.py')]
    assert cli.main(argv) == 2
    assert capsys.readouterr().err == \
        'Erro: servers.rag.tools: "search_exam" não existe neste servidor (use search_exams)\n'


class FakeStream:
    """httpx.stream of a healthy service."""
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None


# The real servers on the spec's ports (test_agent_mcp's fixture): same pytest-xdist worker as that module.
@pytest.mark.xdist_group('spec-ports')
def test_the_real_mcp_servers_answer_list_tools_with_what_the_roles_need(servers, monkeypatch):  # noqa: F811
    monkeypatch.setenv('ALLOWED_HOSTS', '127.0.0.1')

    def on_this_machine(spec):
        for name, url in servers.items():
            spec['servers'][name]['url'] = url
        spec['servers']['api']['openapi_url'] = 'http://127.0.0.1:9/openapi.json'  # nothing listens there
    spec = parse_spec(spec_with(on_this_machine))
    live = live_tools(spec)
    assert set(live['ocr']['extract_exam_text']['properties']) >= {'filename'}
    assert set(live['rag']['search_exams']['properties']) >= {'query', 'top_k'}
    assert live['api'] is None  # did not answer: the declared list stands
    assert transpiler.live.check_live(spec, live) == []


# --- a spec that lists exams, and a variant with three agents, run end to end ---------------------

ORDER = ['Pedido de exames', '- GA', '- Creatinina', '- TSH']
HITS = {'IgA': {'code': 'FICT-079', 'name': 'IgA', 'score': 1.0},
        'Creatinina': {'code': 'FICT-005', 'name': 'Creatinina', 'score': 1.0},
        'TSH': {'code': 'FICT-024', 'name': 'TSH', 'score': 1.0}}
CALLS: dict[str, list] = {'extract_exam_text': [], 'search_exams': [], 'create_appointment': []}


def extract_exam_text(filename: str) -> dict:
    """Reads the order (stand-in for the OCR server)."""
    CALLS['extract_exam_text'].append(filename)
    return {'structuredContent': {'lines': ORDER, 'line_confidence': [96.0] * len(ORDER),
                                  'line_intent': ['request'] * len(ORDER), 'contested_exams': [], 'page_clean': True, 'pii_masked': {}}}


def search_exams(query: str, top_k: int = 3) -> dict:
    """Searches the catalog (stand-in for the RAG server)."""
    CALLS['search_exams'].append((query, top_k))
    return {'structuredContent': {'result': [HITS[query]]}}


def create_appointment(exams: list[dict]) -> dict:
    """Books the exams (stand-in for POST /appointments)."""
    CALLS['create_appointment'].append([exam['code'] for exam in exams])
    return {'id': 'a1', 'status': 'scheduled', 'exams': exams}


STAND_INS = {tool.__name__: tool for tool in (extract_exam_text, search_exams, create_appointment)}


class Scripted(BaseLlm):
    """Answers each model call of its agent with the next turn: tool calls, or the final text."""
    model: str = 'scripted'
    turns: list = []
    step: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        turn = self.turns[min(self.step, len(self.turns) - 1)]
        self.step += 1
        if isinstance(turn, str):
            parts = [types.Part(text=turn)]
        else:
            parts = [types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in turn]
        yield LlmResponse(content=types.Content(role='model', parts=parts))


@pytest.fixture(autouse=True)
def fresh_calls():
    for calls in CALLS.values():
        calls.clear()


def scripted(spec_file, tmp_path, turns):
    """The generated app, each model replaced by its turns and each tool by its stand-in."""
    transpile(spec_file, tmp_path / 'agent.py')
    app = load_root_agent(tmp_path / 'agent.py', name='app')
    for agent in app.root_agent.sub_agents:
        agent.model = Scripted(turns=turns[agent.name])
        agent.tools = [FunctionTool(STAND_INS[name]) for tool in agent.tools
                       for name in getattr(tool, 'operations', None) or tool.tool_filter]
    BookingPlugin.of(app).can_ask = lambda: False
    return app


READ = [('extract_exam_text', {'filename': 'pedido-1.png'})]
SEARCH = [('search_exams', {'query': query}) for query in ('IgA', 'Creatinina', 'TSH')]
LISTED = json.dumps([{'code': 'FICT-079', 'name': 'IgA'}, {'code': 'FICT-005', 'name': 'Creatinina'},
                     {'code': 'FICT-999', 'name': 'Inventado'}])  # TSH left out, one code invented
LISTING_TURNS = {'ler': [READ, 'IgA\nCreatinina\nTSH'], 'listar': [SEARCH, f'```json\n{LISTED}\n```']}


def run(app, spec_file):
    found = cli.new_found()
    asyncio.run(cli.run_agent(app, 'pedido.png', load_spec(spec_file), found))
    return found


def test_the_listing_spec_lists_what_the_policy_accepts_and_books_nothing(tmp_path):
    found = run(scripted(LISTING, tmp_path, LISTING_TURNS), LISTING)
    assert CALLS['create_appointment'] == [] and found['appointment'] is None
    assert [(item['code'], item['confidence'], item['check']) for item in found['listing']] == [
        ('FICT-005', 1.0, False), ('FICT-079', 0.8, True)]  # "- GA" read as IgA: to check, as it would be asked
    assert [(item['code'], item['reason']) for item in found['low_confidence']] == [('FICT-024', 'omitted')]
    assert found['invented'] == ['FICT-999']


def test_cli_run_prints_the_list_of_a_spec_that_does_not_book(tmp_path, monkeypatch, capsys):
    app = scripted(LISTING, tmp_path, LISTING_TURNS)
    monkeypatch.setenv('GOOGLE_API_KEY', 'not-used')
    monkeypatch.setattr(cli, 'check_services', lambda spec: None)
    monkeypatch.setattr(cli, 'load_root_agent', lambda path, name: app)
    argv = ['run', '--image', 'pedido.png', '--spec', str(LISTING), '--agent', str(tmp_path / 'agent.py')]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "não incluído pelo agente: '- TSH' → TSH FICT-024 (confiança 1,00); confira o pedido" in out
    assert 'ignorado: FICT-999 não veio de nenhuma busca no catálogo' in out
    assert '| Creatinina | FICT-005 | 1,00         |\n| IgA        | FICT-079 | 0,80 confira |' in out
    assert '2 exame(s) listado(s); nada foi agendado (o BookingPlugin da spec não declara book)' in out
    assert 'Agendamento' not in out


def test_the_three_agent_variant_reads_and_searches_in_one_agent_and_books(tmp_path):
    reviewed = json.dumps([{'code': 'FICT-005', 'name': 'Creatinina'}, {'code': 'FICT-024', 'name': 'TSH'}])
    turns = {'ler_e_buscar': [READ, SEARCH, reviewed], 'revisar': [reviewed],
             'agendar': [[('create_appointment', {'exams': json.loads(reviewed)})], 'agendado: a1']}
    found = run(scripted(VARIANT, tmp_path, turns), VARIANT)
    assert CALLS['extract_exam_text'] == ['pedido.png']  # the token became the real file, as in agent.json
    assert CALLS['search_exams'] == [('IgA', 3), ('Creatinina', 3), ('TSH', 3)]
    assert CALLS['create_appointment'] == [['FICT-005', 'FICT-024']]
    assert found['appointment']['id'] == 'a1'


def test_cli_run_refuses_a_spec_that_does_not_read_and_search(tmp_path, monkeypatch, capsys):
    def only_reads(spec):
        spec.update(agents=spec['agents'][:1])
        spec['servers'] = {'ocr': spec['servers']['ocr']}
        spec['plugins'][0]['kwargs'] = {'read': 'ocr.extract_exam_text'}
    (tmp_path / 'spec.json').write_text(spec_with(only_reads), encoding='utf-8')
    transpile(tmp_path / 'spec.json', tmp_path / 'agent.py')
    monkeypatch.setenv('GOOGLE_API_KEY', 'not-used')
    argv = ['run', '--image', 'pedido.png', '--spec', str(tmp_path / 'spec.json'), '--agent', str(tmp_path / 'agent.py')]
    assert cli.main(argv) == 2
    assert ('não tem o BookingPlugin com read e search; ela pode ser transpilada e rodada com `adk run` ou '
            '`adk web`, não pela CLI') in capsys.readouterr().err


@pytest.mark.parametrize('called, last_line', [
    (False, '1 exame(s) listado(s); nada foi agendado (o BookingPlugin da spec não declara book)'),
    (True, '1 exame(s) listado(s); o BookingPlugin da spec não declara book, mas uma ferramenta fora dos papéis '
           'respondeu e pode ter gravado: confira'),
])
def test_a_listing_never_says_nothing_was_written_when_a_tool_may_have_written(monkeypatch, capsys, called, last_line):
    monkeypatch.setattr(cli, 'print_reading', lambda found: None)
    monkeypatch.setattr(cli, 'ocr_problem', lambda found, spec: None)
    found = cli.new_found() | {'api_called': called, 'invented': [],
                               'listing': [{'name': 'IgA', 'code': 'FICT-079', 'confidence': 1.0, 'check': False}]}
    cli.show_listing(found, parse_spec(LISTING.read_text(encoding='utf-8')))
    assert capsys.readouterr().out.strip().splitlines()[-1] == last_line


# --- a generic agent: ADK workflows and plugins, no booking code ----------------------------------

BOOKING_WORDS = re.compile(r'book|ocr|exam|appointment|pedido|agend|confian', re.IGNORECASE)


def test_the_generic_example_is_one_llm_agent_with_one_mcp_toolset_and_no_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv('ALLOWED_HOSTS', WITH_DOCS)
    transpile(GENERIC, tmp_path / 'agent.py')
    app = load_root_agent(tmp_path / 'agent.py', name='app')
    assert type(app.root_agent) is LlmAgent and app.root_agent.name == 'answer' and app.plugins == []
    [toolset] = app.root_agent.tools
    assert type(toolset).__name__ == 'McpToolset' and toolset.tool_filter == ['search_docs']
    assert app.root_agent.output_key == 'answer' and app.name == 'docs_assistant'
    assert app.root_agent.instruction.startswith('Regra fixa:')  # the fixed rule is not a booking rule
    # Nothing of the booking domain is written in it: no word, no callback, no plugin.
    source = (tmp_path / 'agent.py').read_text(encoding='utf-8')
    assert not BOOKING_WORDS.findall(source), BOOKING_WORDS.findall(source)


def test_the_generic_example_imports_without_the_runtimes_booking_code(tmp_path, monkeypatch):
    # In a fresh interpreter: importing the generated file loads the toolsets and the model, never the
    # booking policy (callbacks, confidence, the order's record, the plugin) nor the catalog.
    monkeypatch.setenv('ALLOWED_HOSTS', WITH_DOCS)
    transpile(GENERIC, tmp_path / 'agent.py')
    check = ('import importlib.util, sys; sys.path.insert(0, ".");'
             f'spec = importlib.util.spec_from_file_location("agent", {str(tmp_path / "agent.py")!r});'
             'module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module);'
             'print(module.app.root_agent.name, sorted(name for name in sys.modules'
             ' if name.split(".")[0] in ("runtime", "catalogo", "transpiler", "cli")))')
    done = subprocess.run([sys.executable, '-c', check], cwd=ROOT, capture_output=True, text=True, timeout=120,
                          env=os.environ | {'PYTHONWARNINGS': 'ignore'})
    assert done.returncode == 0, done.stderr
    assert done.stdout.split(' ', 1) == ['answer', "['runtime', 'runtime.adk', 'runtime.rede']\n"]


def two_agents(workflow, **more):
    """The generic example with a second agent, run by `workflow`."""
    def change(spec):
        spec['agents'].append({'name': 'review', 'instruction': 'Revise a resposta anterior: {answer}',
                               'output_key': 'reviewed'})
        spec.update(workflow=workflow, **more)
    return spec_with(change, json.loads(GENERIC.read_text(encoding='utf-8')))


@pytest.mark.parametrize('workflow, more, kind', [
    ('SequentialAgent', {}, 'SequentialAgent'),
    ('LoopAgent', {'max_iterations': 2}, 'LoopAgent'),
])
@pytest.mark.filterwarnings('ignore:(Loop|Parallel)Agent is deprecated:DeprecationWarning')  # as SequentialAgent
def test_the_spec_names_the_adk_workflow_that_runs_its_agents(tmp_path, monkeypatch, workflow, more, kind):
    monkeypatch.setenv('ALLOWED_HOSTS', WITH_DOCS)
    (tmp_path / 'spec.json').write_text(two_agents(workflow, **more), encoding='utf-8')
    root_agent = transpile(tmp_path / 'spec.json', tmp_path / 'agent.py')
    assert type(root_agent).__name__ == kind and [agent.name for agent in root_agent.sub_agents] == ['answer', 'review']
    assert getattr(root_agent, 'max_iterations', None) == more.get('max_iterations')


@pytest.mark.filterwarnings('ignore:(Loop|Parallel)Agent is deprecated:DeprecationWarning')  # as SequentialAgent
def test_a_parallel_workflow_runs_its_agents_at_once_so_none_reads_another(tmp_path, monkeypatch):
    monkeypatch.setenv('ALLOWED_HOSTS', WITH_DOCS)
    assert problems(two_agents('ParallelAgent')) == [
        'agents.1.instruction: {answer} não é saída de um agente anterior']
    text = two_agents('ParallelAgent').replace('Revise a resposta anterior: {answer}', 'Resuma a pergunta do usuário.')
    (tmp_path / 'spec.json').write_text(text, encoding='utf-8')
    assert isinstance(transpile(tmp_path / 'spec.json', tmp_path / 'agent.py'), ParallelAgent)


@pytest.mark.parametrize('workflow, more, expected', [
    (None, {}, 'workflow: null (sem workflow) pede um só agente, que é a raiz'),
    ('LoopAgent', {}, 'max_iterations: obrigatório com LoopAgent, e só com ele'),
    ('SequentialAgent', {'max_iterations': 2}, 'max_iterations: obrigatório com LoopAgent, e só com ele'),
    ('LoopAgent', {'max_iterations': 0}, 'max_iterations: deve ser no mínimo 1'),
])
def test_the_workflow_fits_the_agents(monkeypatch, workflow, more, expected):
    monkeypatch.setenv('ALLOWED_HOSTS', WITH_DOCS)
    assert expected in problems(two_agents(workflow, **more))


def with_plugin(path, **kwargs):
    return spec_with(lambda spec: spec.update(plugins=[{'path': path, 'kwargs': kwargs}]),
                     json.loads(GENERIC.read_text(encoding='utf-8')))


def test_any_adk_plugin_of_the_allowed_packages_is_an_app_plugin(tmp_path, monkeypatch):
    # The transpiler knows no plugin: ADK's own LoggingPlugin takes its kwargs as the booking one does,
    # and its text stays a string constant.
    monkeypatch.setenv('ALLOWED_HOSTS', WITH_DOCS)
    hostile = 'log""" + __import__("os").system("x") + """'
    (tmp_path / 'spec.json').write_text(with_plugin('google.adk.plugins.logging_plugin.LoggingPlugin', name=hostile),
                                        encoding='utf-8')
    transpile(tmp_path / 'spec.json', tmp_path / 'agent.py')
    app = load_root_agent(tmp_path / 'agent.py', name='app')
    [plugin] = app.plugins
    assert type(plugin).__name__ == 'LoggingPlugin' and plugin.name == hostile
    source = (tmp_path / 'agent.py').read_text(encoding='utf-8')
    assert 'from google.adk.plugins.logging_plugin import LoggingPlugin' in source and 'servers=' not in source


@pytest.mark.parametrize('path, kwargs, expected', [
    ('os.path.Join', {}, 'plugins.0.path: "os.path.Join" fora dos pacotes de plugins (runtime, google.adk.plugins)'),
    ('runtimes.plugin.BookingPlugin', {}, 'plugins.0.path: "runtimes.plugin.BookingPlugin" fora dos pacotes'),
    ('runtime.plugin.Nope', {}, 'plugins.0.path: "runtime.plugin.Nope" não é uma classe de plugin do ADK'),
    ('runtime.adk.McpToolset', {}, 'plugins.0.path: "runtime.adk.McpToolset" não é uma classe de plugin do ADK'),
    ('runtime.plugin', {}, 'plugins.0.path: formato inválido: esperado modulo.Classe'),
    ('runtime.plugin.BookingPlugin', {'class': 1}, 'plugins.0.kwargs: "class": use minúsculas'),
    ('runtime.plugin.BookingPlugin', {'servers': {}}, 'plugins.0.kwargs: "servers": o transpile passa os servidores'),
    ('runtime.plugin.BookingPlugin', {'top_k': 'três'}, 'plugins.0.kwargs.top_k: deve ser um número inteiro'),
    ('runtime.plugin.BookingPlugin', {'search': 'docs.search_docs'},  # the booking plugin's own rules
     'plugins.0.kwargs.search: a busca é medida contra o pedido lido: declare também read'),
])
def test_a_plugin_is_checked_before_any_code_is_written(monkeypatch, path, kwargs, expected):
    monkeypatch.setenv('ALLOWED_HOSTS', WITH_DOCS)
    found = problems(with_plugin(path, **kwargs))
    assert any(problem.startswith(expected) for problem in found), found
