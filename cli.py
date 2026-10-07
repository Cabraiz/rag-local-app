"""Command line: `python -m cli transpile <spec>` and `python -m cli run --image <file>`."""
import argparse
import asyncio
import logging
import os
import re
import sys
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any, TypedDict

# ADK announces each experimental feature in use ("[EXPERIMENTAL] feature FeatureName.PLUGGABLE_AUTH
# is enabled") as a UserWarning on stderr, on every command, before anything the person asked for.
# Only those notices are silenced, and before ADK is imported, since some come at import time.
warnings.filterwarnings('ignore', message=r'\[EXPERIMENTAL\]', category=UserWarning)

import httpx
from google.adk.apps import App, ResumabilityConfig
from google.adk.models import Gemini
from google.adk.runners import InMemoryRunner
from google.genai import errors, types
from mcp import ClientSession
from mcp.client.sse import sse_client

from runtime import BookingPolicy, confirmacao
from runtime.adk import retries
from runtime.reconcilia import order_lines, unreported
from transpiler import TranspileError, load_root_agent, load_spec, render, transpile
from transpiler.live import check_addresses, check_live, live_tools, pinned_names
from transpiler.spec import MODEL

DEFAULT_SPEC = 'specs/agent.json'
DEFAULT_AGENT = 'generated/agent.py'
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg'}
CARRIED = ('answers', 'idempotency_key')  # session state a fallback run starts from
CONFIRMATION = 'adk_request_confirmation'  # ADK's call that asks the client to confirm a tool call
IMAGE_CHECK = 'check_image'  # the OCR server's check of a file without the OCR; no spec, so no agent, has it


def redact(text):
    """The Gemini key never reaches the terminal, even inside a library's error text."""
    key = os.environ.get('GOOGLE_API_KEY')
    return text.replace(key, '[GOOGLE_API_KEY]') if key else text


class RunError(Exception):
    """A problem the user can fix; the message is shown as is."""


def cmd_transpile(args):
    checked = []
    root_agent = transpile(args.spec, args.output, checked)
    steps = ' -> '.join(agent.name for agent in root_agent.sub_agents)
    print(f'OK: {args.output} gerado e importado; root_agent "{root_agent.name}" '
          f'({type(root_agent).__name__}: {steps})')
    if checked:  # a server that did not answer keeps the spec's list; `cli run` asks it again
        print(f'Ferramentas conferidas nos servidores: {", ".join(checked)}')
    return 0


def check_services(spec):
    """Fail before calling Gemini when a server's name resolves to a local address, it is down or it
    lacks a tool the spec declares; returns what each server listed (transpiler/live.py)."""
    problems = check_addresses(spec)  # before the first request to it
    if problems:
        raise TranspileError(problems)
    for name, server in spec.servers.items():
        label, url = name.upper() + (' (MCP)' if server.mcp else ''), server.address[1]
        try:
            with httpx.stream('GET', url, timeout=3) as response:
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise RunError(f'{label} fora do ar em {url} ({type(error).__name__}); '
                           'suba os serviços com `docker compose up -d --wait`') from None
    live = live_tools(spec)
    problems = check_live(spec, live, required=True)  # the spec may not have been transpiled with them up
    if problems:
        raise TranspileError(problems)
    return live


def ocr_refused(reason):
    """The line for a file the OCR refused, before the run (check_image) or during it (ocr_problem)."""
    return f'OCR recusou a imagem: {reason.strip().rstrip(".")}; nada foi agendado'


async def ask_image_check(url, image):
    async with sse_client(url, timeout=5, sse_read_timeout=CHECK_SECONDS) as streams, \
            ClientSession(*streams) as session:
        await session.initialize()
        return await session.call_tool(IMAGE_CHECK, {'filename': image})


def check_image(spec, image, live):
    """Fail before the first model turn when the server of the read role refuses the file (missing, too
    large, not the format its name says, corrupt, a photo it would barely read). The server is asked,
    since the agent's container does not see samples/; one that does not list IMAGE_CHECK (another
    spec's reader) leaves it to the run. The real name goes to the server only: the model gets a token."""
    name = spec.role_refs()['read'].split('.')[0]
    if IMAGE_CHECK not in ((live or {}).get(name) or {}):
        return
    server = spec.servers[name]
    try:
        result = asyncio.run(asyncio.wait_for(ask_image_check(server.url, image), CHECK_SECONDS))
    except Exception as error:  # it listed its tools a moment ago: a timeout or a dropped stream
        raise RunError(f'{name.upper()} (MCP) não conferiu a imagem em {server.url} ({type(error).__name__}); '
                       'suba os serviços com `docker compose up -d --wait`') from None
    if result.is_error:
        texts = ' '.join(item.text for item in result.content if getattr(item, 'text', None))
        raise RunError(ocr_refused(texts.removeprefix(f'Error executing tool {IMAGE_CHECK}: ')[:300]))


def api_refusal(error):
    # ADK's RestApiTool reports a non-2xx reply as
    # {"error": "Tool ... execution failed ... Status Code: <n>, <response body>"}.
    refused = re.search(r'Status Code: (\d+), (.*)', str(error), re.S)
    return f'HTTP {refused[1]}: {refused[2].strip()}' if refused else str(error)


def record(found, result):
    """Keep the reply of the API tool: blocked before the POST, refused, or the appointment.

    Only a reply that is not a block means the POST was sent (api_called): a call the
    callback blocked wrote nothing, so the fallback model may still run.
    """
    if not isinstance(result, dict) or 'pending_confirmation' in result:  # paused for the [s/N] question
        return
    found['api_called'] = found.get('api_called', False) or 'blocked' not in result
    if 'blocked' in result:
        found['blocked'] = result['blocked']
    elif 'error' in result:
        found['api_error'] = api_refusal(result['error'])
    else:
        found['appointment'] = result


def note_reply(found, spec, response):
    """A tool's reply during the run. The booking one is recorded. Any other tool that answered, but
    the reading, the search and the [s/N] question, may have written: the runtime refuses tools
    without a role, so this is defense in depth, and the fallback model must not run again."""
    if response.name == spec.tool_for('book'):
        record(found, response.response)
    elif response.name not in (spec.tool_for('read'), spec.tool_for('search'), CONFIRMATION):
        reply = response.response
        if not (isinstance(reply, dict) and {'blocked', 'pending_confirmation'} & reply.keys()):
            found['api_called'] = True


def gemini_failure(error):
    """The Gemini API error inside an agent failure, if any (ADK may wrap it)."""
    while error is not None and not isinstance(error, errors.APIError):
        error = error.__cause__ or error.__context__
    return error


class Found(TypedDict, total=False):
    """What one run left: filled by run_agent from the runner's events and the session state
    (`fallback` by retry_with_fallback). Documentation only: the functions that pass it are untyped."""
    api_called: bool  # a POST reached the API (a call the callback blocked is not one)
    appointment: dict[str, Any] | None  # the API's 201 reply
    api_error: str | None  # the API's refusal (HTTP code and body)
    blocked: str | None  # why the callback stopped the booking call before the POST
    pii_masked: dict[str, int]  # what the OCR masked, by kind
    instructions_removed: int  # orders to the model the OCR took out of the text
    candidates: dict[str, Any]  # every code a search returned, with its confidence
    low_confidence: list[dict[str, Any]]  # exams left out, each with its reason
    confirmed: list[dict[str, Any]]  # exams booked after a yes
    answers: dict[str, bool]  # the [s/N] answers, kept for the fallback run
    idempotency_key: str | None  # the run's own key, kept for the fallback run
    tools_called: set[str]  # what the model really called
    tool_seconds: dict[str, float]  # time per tool, from the events
    ocr_read: bool  # the OCR's lines reached the state
    ocr_error: str | None  # the OCR's own refusal
    file_refused: bool  # the model asked for another file than the run's
    listing: list[dict[str, Any]]  # a spec that does not book: the exams listed, after the policy
    invented: list[str]  # a spec that does not book: codes listed that no search returned
    order_unchecked: bool  # the whole order could not be checked (catalog search unavailable)
    fallback: bool  # this is the fallback model's run


def new_found() -> Found:
    return {'api_called': False, 'appointment': None, 'api_error': None, 'blocked': None, 'pii_masked': {},
            'instructions_removed': 0, 'candidates': {}, 'low_confidence': [], 'confirmed': [], 'answers': {},
            'idempotency_key': None, 'tools_called': set(), 'tool_seconds': {}, 'ocr_read': True,
            'listing': [], 'invented': []}


async def answers_to(requests, started):
    """The user message that resumes the paused calls: the [s/N] answers to each confirmation
    request, asked off the event loop (the MCP sessions keep running while the person reads)."""
    parts = []
    for request in requests:
        items = ((request.args or {}).get('toolConfirmation') or {}).get('payload', {}).get('perguntas', [])
        answers = await asyncio.to_thread(confirmacao.ask_person, items) or {}  # no terminal: nothing answered
        original = ((request.args or {}).get('originalFunctionCall') or {}).get('id')
        started[original] = time.time()  # the call runs again from here: the wait is not the tool's time
        parts.append(types.Part(function_response=types.FunctionResponse(
            name=CONFIRMATION, id=request.id, response={'confirmed': True, 'payload': {'respostas': answers}})))
    return types.Content(role='user', parts=parts)


async def run_agent(root_agent, image, spec, found):
    """Run the pipeline, filling `found` as it goes (also when Gemini fails midway). A call that
    asks for confirmation pauses the run; it resumes, in the same invocation, with the answers."""
    app = App(name='clinic', root_agent=root_agent, resumability_config=ResumabilityConfig(is_resumable=True))
    runner = InMemoryRunner(app=app)
    # What an earlier run (before the fallback model) left: its [s/N] answers are not asked again,
    # and its Idempotency-Key makes a POST it may have sent come back as the same appointment.
    carried = {key: found[key] for key in CARRIED if found[key]}
    # The model sees a token, never the file name, which can carry a patient's name; the OCR's
    # before_tool turns the token back into the name (runtime/callbacks.py).
    token = f'pedido-1{Path(image).suffix.lower()}'
    session = await runner.session_service.create_session(
        app_name='clinic', user_id='cli', state={**carried, 'image_token': token, 'image_file': image})
    message = types.Content(role='user', parts=[types.Part(text=f'Arquivo do pedido: {token}')])
    started, invocation = {}, None  # call id -> timestamp of the event that asked for it
    try:
        while message is not None:
            requests = []
            async for event in runner.run_async(user_id='cli', session_id=session.id, new_message=message,
                                                invocation_id=invocation):
                invocation = event.invocation_id
                spans = {}  # tool -> longest call answered in this event (parallel calls overlap)
                for part in (event.content.parts if event.content else None) or []:
                    if part.function_call and part.function_call.name == CONFIRMATION:
                        requests.append(part.function_call)
                    elif part.function_call:
                        print(f'[{event.author}] chamando {part.function_call.name}')
                        started[part.function_call.id] = event.timestamp
                        found['tools_called'].add(part.function_call.name)
                    if part.function_response and part.function_response.id in started:
                        name = part.function_response.name
                        spans[name] = max(spans.get(name, 0), event.timestamp - started.pop(part.function_response.id))
                    if part.function_response:
                        note_reply(found, spec, part.function_response)
                for name, span in spans.items():
                    found['tool_seconds'][name] = found['tool_seconds'].get(name, 0) + span
            message = await answers_to(requests, started) if requests else None
        # The agent's callbacks left in session state what the OCR read and what the search found.
        state = (await runner.session_service.get_session(app_name='clinic', user_id='cli', session_id=session.id)).state
        for key in ('pii_masked', 'instructions_removed', 'candidates', 'low_confidence', 'confirmed', 'listing',
                    'invented'):
            found[key] = state.get(key, found[key])
        found['ocr_read'] = 'ocr_lines' in state
        found['ocr_error'] = state.get('ocr_error')
        found['file_refused'] = state.get('file_refused', False)
        found['low_confidence'] = found['low_confidence'] + await whole_order(state, spec, found)
    finally:  # also after a failure, so the fallback run reuses the answers already given
        state = (await runner.session_service.get_session(app_name='clinic', user_id='cli', session_id=session.id)).state
        found.update({key: state.get(key, found[key]) for key in CARRIED})
        await runner.close()


def policy_of(spec):
    """The spec's booking policy, as the generated agent builds it."""
    booking = spec.booking
    return BookingPolicy(min_confidence=booking.min_confidence, ask_from=booking.ask_from,
                         ocr_floor_line=booking.ocr_floor.line, ocr_floor_short=booking.ocr_floor.short_code,
                         ocr_floor_synonym=booking.ocr_floor.short_synonym, top_k=booking.top_k)


SEARCHES_AT_ONCE, CHECK_SECONDS = 8, 30  # the check of the whole order: searches in flight, and its limit


async def search_lines(spec, texts):
    """text -> the catalog search's hits, from the server of the spec's search role (MCP over SSE): the
    same search the agent uses, which cuts a line into its exams and tags each hit with its piece.
    The searches share one session and run at once, a few at a time."""
    server, tool = spec.role_refs()['search'].split('.')
    limit = asyncio.Semaphore(SEARCHES_AT_ONCE)
    async with sse_client(spec.servers[server].url, timeout=5, sse_read_timeout=CHECK_SECONDS) as streams, \
            ClientSession(*streams) as session:
        await session.initialize()

        async def search(text):
            async with limit:
                result = await session.call_tool(tool, {'query': text[:200], 'top_k': spec.booking.top_k})
            payload = None if result.is_error else (result.structured_content or {}).get('result')
            return text, payload if isinstance(payload, list) else []
        return dict(await asyncio.gather(*map(search, texts)))


async def whole_order(state, spec, found):
    """The exams of the order the run left in no reported state (runtime/reconcilia.py), whatever the
    model searched: each line read is searched here, on the spec's catalog server, piece by piece."""
    texts = list(dict.fromkeys(text for _, text, _ in order_lines(state.get('ocr_read', []))))
    reference = spec.role_refs()['search']
    if not texts or reference is None or not spec.servers[reference.split('.')[0]].mcp:
        return []
    try:
        hits = await asyncio.wait_for(search_lines(spec, texts), CHECK_SECONDS)
    except Exception:  # the search went down after the run: say the order was not checked
        found['order_unchecked'] = True
        return []
    appointment = found['appointment'] if isinstance(found['appointment'], dict) else {}
    settled = {exam.get('code') for exam in appointment.get('exams') or [] if isinstance(exam, dict)}
    settled |= {item['code'] for item in [*found['low_confidence'], *found['confirmed']]}
    return unreported(state, hits.get, policy_of(spec), settled)


def failure_message(error, found):
    gemini = gemini_failure(error)
    if gemini is not None and gemini.code in (429, 500, 503):  # the last run's own retries are spent
        text = f'Gemini indisponível no momento (HTTP {gemini.code}); tente novamente'
    elif gemini is not None:
        text = f'o Gemini recusou a chamada (HTTP {gemini.code}: {redact(str(gemini.message))[:500]})'
    else:
        text = f'a execução do agente falhou ({type(error).__name__}: {redact(str(error))[:500]})'
    if found['appointment']:
        text += f'; o agendamento {found["appointment"].get("id")} já foi criado, não repita'
    elif found['api_called']:
        text += '; a API já foi chamada, confira os agendamentos antes de repetir'
    return text


def timing(found, spec, total):
    """One line with the time of each step (tool call to reply, from the ADK events), the
    whole run and the model; nothing read from the order."""
    def seconds(value):
        return f'{value:.1f}'.replace('.', ',') if value < 10 else f'{value:.0f}'

    steps = (('OCR', spec.tool_for('read')), ('busca', spec.tool_for('search')),
             ('agendamento', spec.tool_for('book')))
    spent = found['tool_seconds']
    parts = [f'{label} {seconds(spent[tool])} s' for label, tool in steps if tool in spent]
    model = os.environ.get('GEMINI_MODEL') or spec.model  # what the generated agent reads
    return f'Tempo: {" · ".join([*parts, f"total {seconds(total)} s"])} (modelo {model})'


def reserve_ready(root_agent):
    """The primary run of a spec with a fallback_model: a 429 (quota) or 503 (overloaded) ends it at
    once and retry_with_fallback runs the reserve model, instead of about a minute of backoff first
    (runtime/adk.py). A 500 is still retried, and the reserve's own run keeps every retry."""
    agents = [root_agent]
    while agents:
        agent = agents.pop()
        agents.extend(getattr(agent, 'sub_agents', None) or [])
        if isinstance(getattr(agent, 'model', None), Gemini):
            agent.model.retry_options = retries(500)


def run_once(args, spec, carried=None):
    """(found, error) of one run: the primary model's (carried is None) or the reserve's."""
    found = new_found()
    found.update(carried or {})
    try:
        root_agent = load_root_agent(args.checked_agent)
        if carried is None and spec.fallback_model:
            reserve_ready(root_agent)
        asyncio.run(run_agent(root_agent, args.image, spec, found))
        return found, None
    except Exception as error:  # reported to the user, never swallowed
        return found, error


# How each exam left out is shown, by its reason; {guess} is "'<line read>' → <exam> <code> (confiança 0,xx)".
LEFT_OUT = {
    'second_round': "não perguntado nesta execução (só ficou em dúvida depois de um 'não'): {guess}; confira o pedido",
    'needs_confirmation': 'não agendado sem confirmação: {guess}; rode num terminal, sem --yes, para responder',
    'declined': 'não incluído (você respondeu não): {guess}',
    'omitted': 'não incluído pelo agente: {guess}; confira o pedido',  # a search found it, the model left it out
    'not_searched': 'não buscado pelo agente: {guess}; confira o pedido',  # only the check of the whole order found it
    'score': 'baixa confiança: {guess}; confira o pedido',
}


def print_reading(found):
    """What the OCR masked or removed, the exams the person confirmed and the ones left out."""
    masked = ', '.join(f'{kind} x{count}' for kind, count in found['pii_masked'].items())
    print(f'\nPII mascarada pelo OCR: {masked or "nenhuma"}')
    if found['instructions_removed']:
        print(f'Instruções neutralizadas no OCR: {found["instructions_removed"]}')
    for item in found['confirmed']:
        print(f"incluído com a sua confirmação: '{item['read']}' → {item['name']} {item['code']}")
    for item in found['low_confidence']:
        # the same number, and the same words, as the [s/N] question: the confidence the policy decided on
        confidence = f'{item["confidence"]:.2f}'.replace('.', ',')
        guess = f"'{item['read']}' → {item['name']} {item['code']} (confiança {confidence})"
        if item.get('reason') == 'line_used':
            print(f"não agendado: '{item['read']}' já foi usada por {item['used_by']}; confira o pedido")
        else:
            print(LEFT_OUT.get(item.get('reason'), LEFT_OUT['score']).format(guess=guess))
    if found.get('order_unchecked'):
        print('Aviso: o pedido não foi conferido por inteiro (a busca no catálogo não respondeu); confira o pedido')
    print()


def show_listing(found, spec):
    """A spec that does not book: the exams the agent listed, each with the confidence the policy
    gives it; nothing reaches an API."""
    print_reading(found)
    problem = ocr_problem(found, spec)
    if problem:
        raise RunError(problem)
    for code in found['invented']:
        print(f'ignorado: {code} não veio de nenhuma busca no catálogo')
    listing = found['listing']
    if not listing:
        raise RunError('nenhum exame listado com confiança suficiente; veja os avisos acima'
                       if found['low_confidence'] else 'Nenhum exame encontrado no pedido')
    shown = [f'{item["confidence"]:.2f}'.replace('.', ',') + (' confira' if item['check'] else '') for item in listing]
    width, column = max(len('Exame'), *(len(item['name']) for item in listing)), max(len('Confiança'), *map(len, shown))
    print(f'| {"Exame":<{width}} | Código   | {"Confiança":<{column}} |')
    print(f'|{"-" * (width + 2)}|----------|{"-" * (column + 2)}|')
    for item, confidence in zip(listing, shown, strict=True):
        print(f'| {item["name"]:<{width}} | {item["code"]:<8} | {confidence:<{column}} |')
    if found['api_called']:  # a tool outside the roles answered: never say that nothing was written
        print(f'\n{len(listing)} exame(s) listado(s); a spec não declara roles.book, mas uma ferramenta fora dos '
              'papéis respondeu e pode ter gravado: confira')
    else:
        print(f'\n{len(listing)} exame(s) listado(s); nada foi agendado (a spec não declara roles.book)')


def print_result(found):
    print_reading(found)
    exams = found['appointment']['exams']
    width = max(len('Exame'), *(len(exam['name']) for exam in exams))
    print(f'| {"Exame":<{width}} | Código   |')
    print(f'|{"-" * (width + 2)}|----------|')
    for exam in exams:
        print(f'| {exam["name"]:<{width}} | {exam["code"]:<8} |')
    appointment = found['appointment']
    # The appointment exists, so the exit code stays 0; the line says when the agent left exams out.
    left = sum(item.get('reason') in ('omitted', 'not_searched') for item in found['low_confidence'])
    check = (f'; ATENÇÃO: {left} possível(is) exame(s) do pedido sem decisão do agente, confira os avisos acima'
             if left else '')
    print(f'\nAgendamento confirmado pela API: id {appointment["id"]}, status {appointment["status"]}{check}')


def validate_args(args):
    """The --image name, the generated agent, the key and GEMINI_MODEL, before any service or Gemini call."""
    # Early, friendly message; the OCR server is what actually enforces it.
    if '/' in args.image or '\\' in args.image:
        raise RunError('--image: informe só o nome do arquivo dentro de samples/, ex.: pedido.png')
    if not args.image.strip():
        raise RunError('--image: o nome do arquivo está vazio; informe um arquivo de samples/, ex.: pedido.png')
    # Same suffixes as the OCR server. Existence is left to it (check_image, before the first model
    # turn): the agent image does not mount samples/, so a file added after the build exists only there.
    if Path(args.image).suffix.lower() not in IMAGE_SUFFIXES:
        # A bare name ("pedido") most likely means the PNG sample of the same name.
        hint = f'; quis dizer "{args.image}.png"?' if not Path(args.image).suffix and not args.image.startswith('.') else ''
        raise RunError(f'--image: "{args.image}" não é uma imagem aceita; use .png, .jpg ou .jpeg{hint}')
    if not Path(args.agent).is_file():
        # The README runs everything in Docker, so suggest the full command.
        raise RunError(f'{args.agent} não existe: rode antes: '
                       f'docker compose run --rm agent python -m cli transpile {DEFAULT_SPEC}')
    if not os.environ.get('GOOGLE_API_KEY'):
        raise RunError('GOOGLE_API_KEY não definida: preencha GOOGLE_API_KEY= no .env '
                       '(crie com "cp .env.example .env" se ele não existir)')
    model = os.environ.get('GEMINI_MODEL')
    if model and not re.fullmatch(MODEL, model):  # the spec's own rule: the variable does not bypass it
        shown = ''.join(char for char in model[:60] if char.isprintable())
        raise RunError(f'GEMINI_MODEL inválido ("{shown}"): use um modelo como gemini-3.5-flash, '
                       'ou deixe a variável vazia para usar o modelo da spec')


def check_agent(args, spec):
    """The agent that runs is the one this spec generates today: the spec checked above is what the
    run does, not an older agent.py (other servers, tools or thresholds) or one edited by hand."""
    output = '' if args.agent == DEFAULT_AGENT else f' --output {args.agent}'
    transpile_again = f'gere de novo: docker compose run --rm agent python -m cli transpile {args.spec}{output}'
    try:
        text = Path(args.agent).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as error:
        raise RunError(f'{args.agent}: não foi possível ler ({type(error).__name__}); {transpile_again}') from None
    if text != render(spec, args.spec):  # read_text turns a Windows checkout's CRLF into LF
        raise RunError(f'{args.agent} não é o que {args.spec} gera hoje (gerado de outra spec, antes de uma mudança '
                       f'ou editado à mão); {transpile_again}')
    args.checked_agent.write_text(text, encoding='utf-8')  # what the run imports: these bytes, not a 2nd read


def load_checked_spec(args):
    """The spec, once agent.py is what it generates, its services answer and the OCR accepts the image;
    --yes tells the generated agent to ask nothing."""
    if args.yes:  # read by the generated agent: exams that need a yes are left out, never assumed
        os.environ['AGENT_NO_QUESTIONS'] = '1'
    spec = load_spec(args.spec)
    if not (spec.tool_for('read') and spec.tool_for('search')):
        raise RunError(f'{args.spec}: `cli run` lê um pedido em imagem e busca os exames no catálogo, e esta spec '
                       'não tem roles.read e roles.search; ela pode ser transpilada, não rodada pela CLI')
    check_agent(args, spec)
    check_image(spec, args.image, check_services(spec))
    return spec


def retry_with_fallback(args, spec, found, error):
    """(found, error) of the first run, or of one more run with the spec's fallback model when the
    primary was unavailable (503, overloaded) or out of quota (429), which reserve_ready makes fail
    at once, and nothing was sent to the API yet: a second run after a POST could schedule the
    same exams twice. The reserve model is a normal path, not an error: the run goes on with it."""
    gemini = gemini_failure(error)
    if gemini is not None and gemini.code in (429, 503) and spec.fallback_model and not found['api_called']:
        print(f'Aviso: modelo principal indisponível; usando {spec.fallback_model}')
        os.environ['GEMINI_MODEL'] = spec.fallback_model  # read by the generated agent on import
        found, error = run_once(args, spec, {key: found[key] for key in CARRIED})
        found['fallback'] = True
    return found, error


def ocr_problem(found, spec):
    """Why the order was not read, or None when the OCR's lines arrived."""
    if found['ocr_read']:
        return None
    if found.get('file_refused'):
        return 'o agente pediu um arquivo diferente do informado; nada foi agendado'
    if found.get('ocr_error'):
        return ocr_refused(found['ocr_error'])
    if spec.tool_for('read') not in found['tools_called']:  # what the model really called
        return 'o agente não leu a imagem (não chamou o OCR); nada foi agendado'
    return 'o OCR não devolveu o texto do pedido (serviço indisponível?); nada foi agendado'


def booking_problem(found, spec):
    """Why an order that was read booked nothing: no search, no exam, a block or the API's refusal."""
    called = found['tools_called']  # what the model really called, not what it said it did
    skipped_search = spec.tool_for('book') in called and spec.tool_for('search') not in called
    if not found['candidates'] and skipped_search:
        return 'a busca no catálogo não foi feita (o agente tentou agendar sem buscar os exames); nada foi agendado'
    if not found['candidates'] and not found['api_error']:  # nothing searched, or the call was blocked
        return 'Nenhum exame encontrado no pedido; nada foi agendado'
    if found['blocked']:
        return f'agendamento bloqueado antes de chamar a API: {found["blocked"]}; nada foi agendado'
    if not found['api_error']:
        return 'o agente terminou sem um agendamento confirmado pela API'
    # A 409 on the fallback run means the key carried from the first run was already used: that run's
    # POST may have created an appointment even though its reply never came back.
    earlier = ('; um agendamento da 1ª tentativa pode já ter sido criado, confira antes de repetir'
               if 'HTTP 409' in str(found['api_error']) and found.get('fallback') else '')
    return f'a API recusou o agendamento ({found["api_error"]}){earlier}'


def show_appointment(found):
    try:
        print_result(found)
    except (KeyError, TypeError):
        raise RunError('a API respondeu sem o formato esperado (id, status e exams com code e name)') from None


def cmd_run(args):
    validate_args(args)
    # The names check_services resolves keep those addresses for the whole run, and the agent imported
    # is a private copy of the bytes check_agent compared: agent.py changed after the check is not run.
    with pinned_names(), tempfile.TemporaryDirectory(prefix='agent-') as folder:
        args.checked_agent = Path(folder) / 'agent.py'
        return checked_run(args)


def checked_run(args):
    spec = load_checked_spec(args)
    start, found = time.monotonic(), new_found()
    try:
        found, error = run_once(args, spec)  # kept if Ctrl+C stops the fallback run: its step times still print
        found, error = retry_with_fallback(args, spec, found, error)
        if error is not None:
            raise RunError(failure_message(error, found))
        if spec.tool_for('book') is None:
            show_listing(found, spec)
            return 0
        if found['appointment'] is None:
            print_reading(found)
            raise RunError(ocr_problem(found, spec) or booking_problem(found, spec))
        show_appointment(found)
        return 0
    finally:  # also when nothing was scheduled: the time comes before the error line
        print(timing(found, spec, time.monotonic() - start))


class Redacted(logging.Formatter):
    """A log line with the Gemini key hidden, like every line the CLI prints."""

    def format(self, record):
        return redact(super().format(record))


LOGS: dict[str, logging.Handler] = {}  # the --verbose handler of the last main(), replaced by the next one


def show_logs(verbose):
    """Library logs (ADK, the Gemini client's retries, MCP) and the CLI's own, on stderr, only with
    --verbose. By default they are off: every failure ends in one "Erro: ..." line instead."""
    root = logging.getLogger()
    if 'handler' in LOGS:  # main() runs many times in one process (tests): one handler, on today's stderr
        root.removeHandler(LOGS.pop('handler'))
    if not verbose:
        logging.disable(logging.CRITICAL)
        return
    logging.disable(logging.NOTSET)
    handler = LOGS['handler'] = logging.StreamHandler(sys.stderr)
    handler.setFormatter(Redacted('%(levelname)s %(name)s: %(message)s'))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class Parser(argparse.ArgumentParser):
    """argparse with its usage errors in Portuguese, as one "Erro: ..." line like the rest."""

    def error(self, message):
        required = re.search(r'the following arguments are required: (.+)', message)
        choice = re.search(r"invalid choice: '?([^' ]+)'?", message)
        if required:
            example = ' (ex.: --image pedido.png)' if '--image' in required[1] else ''
            raise RunError(f'falta o argumento obrigatório {required[1]}{example}')
        if choice:
            raise RunError(f'"{choice[1]}" não é um comando; use transpile ou run (veja python -m cli --help)')
        raise RunError(f'argumentos inválidos ({message}); veja python -m cli --help')


def main(argv=None):
    # Library notices and their tracebacks (ADK flags, auth probes, MCP reconnects)
    # are not for the user: every failure ends in one "Erro: ..." line instead.
    # --verbose shows the logs (show_logs).
    warnings.filterwarnings('ignore', message=r'\[EXPERIMENTAL\]')
    logging.disable(logging.CRITICAL)
    parser = Parser(prog='python -m cli', description='Transpilador JSON -> agente Google ADK.')
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--verbose', action='store_true',
                        help='mostra no stderr os logs das bibliotecas (ADK, Gemini, MCP), sem a chave da API')
    commands = parser.add_subparsers(dest='command', required=True)
    transpile_cmd = commands.add_parser('transpile', parents=[common], help='gera e valida o agente a partir da spec')
    transpile_cmd.add_argument('spec', nargs='?', default=DEFAULT_SPEC)
    transpile_cmd.add_argument('--output', default=DEFAULT_AGENT)
    run_cmd = commands.add_parser('run', parents=[common], help='executa o agente gerado sobre uma imagem de pedido')
    run_cmd.add_argument('--image', required=True, help='nome do arquivo em samples/, ex.: pedido.png')
    run_cmd.add_argument('--agent', default=DEFAULT_AGENT)
    run_cmd.add_argument('--spec', default=DEFAULT_SPEC, help='spec usada no transpile (URLs dos serviços)')
    run_cmd.add_argument('--yes', action='store_true',
                         help='não pergunta nada: agenda só o que tem confiança alta; os exames que pediriam '
                              'confirmação ficam de fora')
    try:
        args = parser.parse_args(argv)
        show_logs(args.verbose)
        return cmd_transpile(args) if args.command == 'transpile' else cmd_run(args)
    except TranspileError as error:
        problems = error.problems
    except RunError as error:
        problems = [str(error)]
    except Exception as error:  # anything unexpected is still one line, never a traceback
        logging.getLogger('cli').exception('falha inesperada')  # the traceback, only with --verbose
        problems = [f'falha inesperada ({type(error).__name__}: {redact(str(error))[:500]})']
    print('\n'.join(redact(f'Erro: {problem}') for problem in problems), file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
