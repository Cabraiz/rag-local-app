"""The generated agent under ADK's own tooling, as `adk run` runs it: no CLI and no state injected.

The command is ADK's own `adk run` (the click entry point the `adk` script calls), in --in_memory
mode, on the folder `cli transpile` writes (agent.py and __init__.py). The person types only the
order's file name, as in the console; `cli.py` is never imported here. Every step's model is a
scripted stand-in (no Gemini); the OCR and catalog MCP servers and the API are the real ones, served
in this process at the spec's own URLs (ocr:8001, rag:8002, api:8000, resolved to this machine), so
the generated code runs unchanged. What counts is what the API stored, read back through its GET.
"""
import asyncio
import base64
import json
import logging
import re
import shutil
import socket
import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from google.adk.cli import api_server as adk_api_server
from google.adk.cli import cli as adk_cli
from google.adk.cli.cli_tools_click import main as adk_main
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.tools.tool_confirmation import ToolConfirmation
from google.genai import types

from runtime import BookingCallbacks, confirmacao, rede
from runtime.confirmacao import answers_given
from runtime.entrada import image_names
from tests.test_alucinacao import IMAGE, NAMED, PORTS, ROOT, appointment, services, stored_ids  # noqa: F401
from transpiler import transpile

CHECK_URLS = rede.check_urls  # the real address rule; the agent_folder fixture replaces it
SEEN = []  # every request the scripted models got: what reached "the LLM"
READ = ['Hemograma completo', 'Glicemia de jejum', 'Creatinina']  # the exams of pedido.png
CODES = {'Hemograma completo': 'FICT-001', 'Glicemia de jejum': 'FICT-002', 'Creatinina': 'FICT-005'}
BOOK = {'exams': [{'code': CODES[name], 'name': name} for name in READ]}  # what the scripted schedule step books

pytestmark = [
    pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image'),
    # The spec's own ports, also used by test_alucinacao and test_agent_mcp: with pytest-xdist (-n) the
    # three modules run on one worker, one after the other.
    pytest.mark.xdist_group('spec-ports'),
    pytest.mark.filterwarnings(r'ignore:\[EXPERIMENTAL\]:UserWarning'),
    pytest.mark.filterwarnings('ignore::DeprecationWarning'),
]


def file_named_in(request):
    """The order's file as the model was told it, the way a real model reads its prompt."""
    told = re.search(r'Arquivo do pedido: ([\w.-]+)', request.model_dump_json())
    return told[1] if told else ''


def replies(request):
    last = request.contents[-1] if request.contents else None
    return [part.function_response.response for part in (last.parts if last else None) or [] if part.function_response]


class Scripted(BaseLlm):
    """Each step does its task honestly: read the file it was told, search each exam read, book BOOK."""
    model: str = 'scripted'

    async def generate_content_async(self, llm_request, stream=False):
        SEEN.append(llm_request.model_dump_json())
        tool, answered = next(iter(llm_request.tools_dict or {}), None), replies(llm_request)
        if tool == 'extract_exam_text' and not answered:
            calls = [(tool, {'filename': file_named_in(llm_request)})]
        elif tool == 'search_exams' and not answered:
            calls = [(tool, {'query': name}) for name in READ]
        elif tool == 'search_exams':
            best = [(reply.get('structuredContent') or {}).get('result', [{}])[0] for reply in answered]
            calls = json.dumps([{'code': hit.get('code'), 'name': hit.get('name')} for hit in best], ensure_ascii=False)
        elif tool == 'create_appointment' and not answered:
            calls = [(tool, BOOK)]
        else:
            calls = '\n'.join(READ) if tool == 'extract_exam_text' else 'Pronto.'
        parts = ([types.Part(text=calls)] if isinstance(calls, str) else
                 [types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in calls])
        yield LlmResponse(content=types.Content(role='model', parts=parts))


class ScriptedRunner(Runner):
    """ADK's Runner, as `adk run` builds it, with each step's model replaced by the script."""

    def __init__(self, *args, app=None, **kwargs):
        steps = [app.root_agent]
        while steps:
            step = steps.pop()
            steps.extend(getattr(step, 'sub_agents', []))
            if hasattr(step, 'model'):
                step.model = Scripted()
        super().__init__(*args, app=app, **kwargs)


@pytest.fixture
def agent_folder(services, tmp_path, monkeypatch):  # noqa: F811
    """The folder `cli transpile` writes (agent.py, __init__.py), with the spec's hosts on this machine.
    Yields (the folder, the URLs each order checked); afterwards, as for a new `adk` process, the
    folder's modules, sys.path and the log handler `adk run` adds are gone."""
    real = socket.getaddrinfo

    def local(host, *args, **kwargs):  # the spec's hosts (ocr, rag, api) are this machine
        name = host.decode() if isinstance(host, bytes) else host
        return real('127.0.0.1' if name in PORTS else host, *args, **kwargs)

    checked = []

    def compose_names_on_loopback(urls):  # on purpose here: the rule itself is tested at the end of this file
        checked.append(list(urls))
        return []

    monkeypatch.setattr(socket, 'getaddrinfo', local)
    monkeypatch.setattr(rede, 'check_urls', compose_names_on_loopback)
    monkeypatch.setenv('ADK_DISABLE_LOAD_DOTENV', '1')
    monkeypatch.delenv('GEMINI_MODEL', raising=False)
    transpile(ROOT / 'specs' / 'agent.json', tmp_path / 'generated' / 'agent.py')
    root, handlers, path = logging.getLogger(), list(logging.getLogger().handlers), list(sys.path)
    SEEN.clear()
    yield tmp_path / 'generated', checked
    for handler in [handler for handler in root.handlers if handler not in handlers]:  # adk run logs to /tmp
        root.removeHandler(handler)
        handler.close()
    sys.path[:] = path
    for name in [name for name in sys.modules if name == 'generated' or name.startswith('generated.')]:
        del sys.modules[name]


@pytest.fixture
def adk_run(agent_folder, services, monkeypatch):  # noqa: F811
    """adk_run(*lines) -> (what the console showed, the appointments this session stored, the URLs checked):
    `adk run --in_memory generated`, with the person typing each line, then exit."""
    folder, checked = agent_folder
    monkeypatch.setattr(adk_cli, 'Runner', ScriptedRunner)

    def go(*lines):
        before = stored_ids(services)
        typed = ''.join(f'{line}\n' for line in [*lines, 'exit'])
        done = CliRunner().invoke(adk_main, ['run', '--in_memory', str(folder)], input=typed)
        assert done.exception is None, done.output
        return done.output, [appointment(id_) for id_ in stored_ids(services) if id_ not in before], checked
    return go


def all_three():
    return [[(CODES[name], name) for name in READ]]


@pytest.mark.parametrize('typed', [NAMED, f'Por favor, agende o pedido "{NAMED}".'])
def test_adk_run_books_the_order_from_only_its_file_name(adk_run, typed):
    out, new, checked = adk_run(typed)
    assert new == all_three(), out  # read by the real OCR, searched in the real RAG, stored by the real API
    stored_id = re.search(r'Agendamento confirmado pela API: id (\S+), status scheduled', out)
    assert stored_id and appointment(stored_id[1].rstrip(',')) == all_three()[0], out
    assert 'PII mascarada pelo OCR: NOME x2, CPF x1, EMAIL x1, TELEFONE x1' in out
    # The file name carries a (fictional) patient's name: the person typed it, no model ever saw it.
    assert SEEN and not [request for request in SEEN if 'joao' in request.lower() or 'silva' in request.lower()]
    assert all('Arquivo do pedido: pedido-1.png' in request for request in SEEN)
    # The order checks every server before the first model turn; each toolset, its URLs before it connects.
    assert checked[0] == ['http://ocr:8001/sse', 'http://rag:8002/sse', 'http://api:8000/openapi.json']
    assert {url for urls in checked[1:] for url in urls} == {
        'http://ocr:8001/sse', 'http://rag:8002/sse', 'http://api:8000/openapi.json', 'http://api:8000'}


@pytest.mark.parametrize('answer, stored, line', [
    ('yes', ['FICT-001', 'FICT-002', 'FICT-067'], "incluído com a sua confirmação: 'Exame: Creatinina' → Creatinoquinase FICT-067"),
    ('no', ['FICT-001', 'FICT-002'], "não incluído (você respondeu não): 'Exame: Creatinina' → Creatinoquinase FICT-067"),
])
def test_the_question_is_answered_in_adk_runs_console_and_resumes_the_same_call(adk_run, monkeypatch, answer, stored,
                                                                                 line):
    # A weaker candidate of the Creatinina search (Creatinoquinase, in the question band) proposed with
    # the two exams read clearly: ADK's console shows the question, the answer applies to it, one POST.
    monkeypatch.setattr(confirmacao, 'can_ask', lambda: True)  # the console is a terminal
    monkeypatch.setitem(BOOK, 'exams', [{'code': 'FICT-001', 'name': 'Hemograma completo'},
                                        {'code': 'FICT-002', 'name': 'Glicemia de jejum'},
                                        {'code': 'FICT-067', 'name': 'Creatinoquinase'}])
    out, new, _ = adk_run(IMAGE, answer)
    assert '[HITL confirm] Confirme os exames lidos com confiança média: ' in out
    assert "'Exame: Creatinina' → Creatinoquinase FICT-067 (confiança 0," in out
    assert [[code for code, _ in exams] for exams in new] == [stored], out
    assert line in out and 'Agendamento confirmado pela API' in out


@pytest.mark.parametrize('typed, told', [
    ('oi, quero agendar meus exames', 'Informe só o nome de um arquivo de pedido em samples/'),
    ('samples/pedido.png', 'Informe só o nome de um arquivo de pedido em samples/'),  # no folders
    ('pedido.png e pedido-variacao.png', 'Informe só o nome de um arquivo de pedido em samples/'),  # one order
    ('pedido-joao-silva-nao-existe.png', 'OCR recusou a imagem: Arquivo "pedido-1.png" não encontrado'),
])
def test_without_one_readable_image_nothing_reaches_a_model(adk_run, typed, told):
    out, new, _ = adk_run(typed)
    assert told in out and 'Nada foi lido nem agendado' in out, out
    assert SEEN == [] and new == []  # stopped before the first model turn
    assert 'joao' not in out  # the OCR's refusal names the token, not the file


def test_an_exam_the_order_says_not_to_do_is_not_booked_under_adk_run(adk_run, monkeypatch):
    # The order of a blind review: the model proposes Ferritina too. Tesseract's reading is replaced by
    # these lines; the rest is the real OCR server (injection guard, line_intent, PII mask), the real
    # search and the real API, under `adk run`.
    from mcp_servers import ocr
    from mcp_servers.preprocessamento import Linha
    order = ['Hemograma completo', 'TSH', 'Obs: NAO realizar Ferritina']
    monkeypatch.setattr(ocr, 'read_lines', lambda path: [Linha(line, 95) for line in order])
    monkeypatch.setattr(sys.modules[__name__], 'READ', ['Hemograma completo', 'TSH', 'Ferritina'])
    monkeypatch.setitem(BOOK, 'exams', [{'code': 'FICT-001', 'name': 'Hemograma completo'},
                                        {'code': 'FICT-024', 'name': 'TSH'}, {'code': 'FICT-018', 'name': 'Ferritina'}])
    out, new, _ = adk_run(IMAGE)
    assert new == [[('FICT-001', 'Hemograma completo'), ('FICT-024', 'TSH')]], out
    report = out.split('[clinic_scheduler]: ', 1)[1]  # the message written in code, not the model's
    assert "não agendado: 'Obs: NAO realizar Ferritina' → Ferritina FICT-018; o pedido diz para não realizar" in report
    assert 'Agendamento confirmado pela API' in report and 'ATENÇÃO' not in report


def test_one_order_per_session(adk_run):
    out, new, _ = adk_run(IMAGE, IMAGE)
    assert new == all_three(), out  # one appointment; the 2nd message is told it exists, not to repeat it
    stored_id = re.search(r'Agendamento confirmado pela API: id (\S+), status scheduled', out)[1]
    assert f'Esta sessão já tratou um pedido, e o agendamento {stored_id} já foi criado: não repita' in out


# --- The same rules, without servers ------------------------------------------------------------------

@pytest.mark.parametrize('text, names', [
    ('pedido.png', ['pedido.png']),
    ('Arquivo: "Pedido-2.JPG".', ['Pedido-2.JPG']),
    ('../samples/pedido.png', ['../samples/pedido.png']),  # kept whole, then refused for its folder
    ('sem arquivo', []),
])
def test_the_image_name_is_taken_whole_from_the_message(text, names):
    assert image_names(text) == names


@pytest.mark.parametrize('confirmation, answers', [
    (None, {}),
    (ToolConfirmation(confirmed=True), {'FICT-079': True, 'FICT-005': True}),  # adk run's console, adk web
    (ToolConfirmation(confirmed=False), {'FICT-079': False, 'FICT-005': False}),
    (ToolConfirmation(confirmed=True, payload={'respostas': {'FICT-079': True}}), {'FICT-079': True}),  # cli run
    (ToolConfirmation(confirmed=True, payload={'respostas': {}}), {}),  # cli run with nobody to answer
    (ToolConfirmation(confirmed=True, payload={'respostas': {'FICT-999': True}}), {}),  # not asked by this call
])
def test_one_answer_from_adk_tooling_applies_to_every_exam_the_call_asked(confirmation, answers):
    assert answers_given({}, confirmation, ['FICT-079', 'FICT-005']) == answers


def resolver(answers):
    """getaddrinfo where each name in `answers` resolves to its next address; the rest are unknown."""
    def getaddrinfo(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if rede.is_address(name):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (name, port))]
        if name not in answers:
            raise socket.gaierror(socket.EAI_NONAME, 'Name or service not known')
        address = answers[name].pop(0) if len(answers[name]) > 1 else answers[name][0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]
    return getaddrinfo


def order(callbacks, text, state=None):
    context = SimpleNamespace(state=state if state is not None else {}, invocation_id='i1',
                              user_content=types.Content(role='user', parts=[types.Part(text=text)]))
    return asyncio.run(callbacks.start_order(context)), context.state


@pytest.mark.parametrize('address', ['127.0.0.1', '169.254.169.254', '100.100.100.200'])
def test_outside_cli_run_a_name_on_a_local_address_stops_the_order_before_any_model_turn(monkeypatch, address):
    monkeypatch.setenv('ALLOWED_HOSTS', 'ocr:8001,rag:8002,clinica.exemplo:8443')
    monkeypatch.setattr(socket, 'getaddrinfo', resolver({'clinica.exemplo': [address], 'ocr': ['10.0.0.5'],
                                                         'rag': ['10.0.0.6']}))
    callbacks = BookingCallbacks(ocr_tool='extract_exam_text', servers=[
        'http://ocr:8001/sse', 'http://rag:8002/sse', 'https://clinica.exemplo:8443/openapi.json'])
    said, state = order(callbacks, 'pedido.png')
    assert said.parts[0].text == (
        f'Endereço recusado: https://clinica.exemplo:8443/openapi.json: "clinica.exemplo" resolve para {address}, '
        f'{rede.REFUSED}. Nada foi lido nem agendado.')
    assert state == {}  # no image taken, no order started
    with pytest.raises(socket.gaierror):  # and the name reaches no address in this process
        socket.getaddrinfo('clinica.exemplo', 8443)


def test_outside_cli_run_the_addresses_checked_are_kept_for_the_process(monkeypatch):
    monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    rebinding = resolver({'ocr': ['10.0.0.5', '127.0.0.1'], 'rag': ['10.0.0.6'], 'api': ['10.0.0.7']})
    monkeypatch.setattr(socket, 'getaddrinfo', rebinding)
    callbacks = BookingCallbacks(servers=['http://ocr:8001/sse', 'http://rag:8002/sse', 'http://api:8000/openapi.json'])
    said, state = order(callbacks, 'qualquer texto')  # a spec with no reading role takes no image
    assert said is None and state == {'order_invocation': 'i1'}
    # The DNS now answers 127.0.0.1 for ocr: every client of the process still gets the address checked.
    assert [info[4][0] for info in socket.getaddrinfo('OCR', 8001)] == ['10.0.0.5']
    assert rede.PINS == {'ocr': ['10.0.0.5'], 'rag': ['10.0.0.6'], 'api': ['10.0.0.7']}
    said, _ = order(callbacks, 'outra mensagem', {'order_invocation': 'i0'})  # a 2nd order in the same session
    assert said.parts[0].text.startswith('Esta sessão já tratou um pedido')


COMPOSE_DNS = {'ocr': '10.0.0.5', 'rag': '10.0.0.6', 'api': '10.0.0.7'}  # private, as Docker's DNS answers


def test_adk_run_with_the_real_address_rule_books_over_the_addresses_it_checked(adk_run, monkeypatch):
    # The real rule, with the compose names on private addresses (allowed); those addresses lead to
    # this machine's servers, like a NAT, so the run can only connect through the pinned addresses.
    local = socket.getaddrinfo  # the fixture's: the names are this machine

    def compose_dns(host, port, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name in COMPOSE_DNS:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (COMPOSE_DNS[name], port))]
        return local('127.0.0.1' if name in COMPOSE_DNS.values() else host, port, *args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', compose_dns)
    monkeypatch.setattr(rede, 'check_urls', CHECK_URLS)
    out, new, _ = adk_run(NAMED)
    assert new == all_three(), out
    assert rede.PINS == {name: [address] for name, address in COMPOSE_DNS.items()}


def test_adk_web_books_the_order_from_only_its_file_name(agent_folder, services, monkeypatch):  # noqa: F811
    # The server `adk web` starts (without its static UI): a new session, then one message to /run,
    # as the web page sends it. The report is the last event.
    folder, _ = agent_folder
    monkeypatch.setattr(adk_api_server, 'Runner', ScriptedRunner)
    before = stored_ids(services)
    server = get_fast_api_app(agents_dir=str(folder), web=False, session_service_uri='memory://',
                              artifact_service_uri='memory://', memory_service_uri='memory://', use_local_storage=False)
    with TestClient(server) as client:
        session = client.post('/apps/generated/users/pessoa/sessions', json={}).json()
        events = client.post('/run', json={'appName': 'generated', 'userId': 'pessoa', 'sessionId': session['id'],
                                           'newMessage': {'role': 'user', 'parts': [{'text': NAMED}]}}).json()
    assert [appointment(id_) for id_ in stored_ids(services) if id_ not in before] == all_three()
    last = events[-1]
    assert last['author'] == 'clinic_scheduler'
    assert 'Agendamento confirmado pela API: id ' in last['content']['parts'][0]['text']
    assert SEEN and not [request for request in SEEN if 'joao' in request.lower() or 'silva' in request.lower()]


def test_an_image_attached_in_adk_web_is_refused_and_never_sent_to_a_model(agent_folder, services, monkeypatch):  # noqa: F811
    # The page lets the person attach the image itself: it would reach Gemini unmasked, past the OCR.
    folder, _ = agent_folder
    monkeypatch.setattr(adk_api_server, 'Runner', ScriptedRunner)
    before = stored_ids(services)
    image = base64.b64encode((ROOT / 'samples' / IMAGE).read_bytes()).decode()
    server = get_fast_api_app(agents_dir=str(folder), web=False, session_service_uri='memory://',
                              artifact_service_uri='memory://', memory_service_uri='memory://', use_local_storage=False)
    with TestClient(server) as client:
        session = client.post('/apps/generated/users/pessoa/sessions', json={}).json()
        events = client.post('/run', json={'appName': 'generated', 'userId': 'pessoa', 'sessionId': session['id'],
                                           'newMessage': {'role': 'user', 'parts': [
                                               {'text': IMAGE}, {'inlineData': {'mimeType': 'image/png', 'data': image}}]}}).json()
    assert events[-1]['content']['parts'][0]['text'].startswith('Envie só o nome do arquivo do pedido, como texto')
    assert SEEN == [] and stored_ids(services) == before


def test_no_attached_file_no_other_data_and_no_real_file_name_reach_the_model():
    callbacks = BookingCallbacks(ocr_tool='extract_exam_text')
    typed = types.Content(role='user', parts=[types.Part(text='agende pedido-joao-silva.png'),
                                             types.Part.from_bytes(data=b'PNG', mime_type='image/png')])
    session = SimpleNamespace(app_name='generated', user_id='pessoa', id='s1',
                              events=[SimpleNamespace(author='user', content=typed)])
    callbacks.orders.start(session, 'pedido-joao-silva.png')
    context = SimpleNamespace(state={'image_file': 'outro.png'}, session=session)  # the state is not trusted
    smuggled = types.Part(text='Hemograma', executable_code=types.ExecutableCode(code='SMUGGLED', language='PYTHON'))
    request = LlmRequest(contents=[typed.model_copy(deep=True), types.Content(role='user', parts=[
        types.Part(text='For context: [extract] said: o arquivo pedido-joao-silva.png foi lido')]),
        types.Content(role='model', parts=[smuggled])])
    assert callbacks.before_model(context, request) is None
    assert [[part.model_dump(exclude_none=True) for part in content.parts] for content in request.contents] == [
        [{'text': 'Arquivo do pedido: pedido-1.png'}],
        [{'text': 'For context: [extract] said: o arquivo pedido-1.png foi lido'}], [{'text': 'Hemograma'}]]
