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

# ADK announces each experimental feature in use ("[EXPERIMENTAL] feature FeatureName.PLUGGABLE_AUTH
# is enabled") as a UserWarning on stderr, on every command, before anything the person asked for.
# Only those notices are silenced, and before ADK is imported, since some come at import time.
warnings.filterwarnings('ignore', message=r'\[EXPERIMENTAL\]', category=UserWarning)

import httpx
from google.adk.runners import InMemoryRunner
from google.genai import types

from runtime import BookingCallbacks, confirmacao, servidores
from runtime.entrada import image_token
from runtime.rede import pinned_names
from runtime.relatorio import api_error_in, api_refusal, model_failure, reading_lines
from runtime.servidores import CHECK_SECONDS, IMAGE_CHECK
from transpiler import TranspileError, load_root_agent, load_spec, render, transpile
from transpiler.live import check_addresses, check_live, live_tools
from transpiler.spec import MODEL

DEFAULT_SPEC = 'specs/agent.json'
DEFAULT_AGENT = 'generated/agent.py'
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg'}
CONFIRMATION = 'adk_request_confirmation'  # ADK's call that asks the client to confirm a tool call
NO_TERMINAL = 'sem terminal para confirmar a lista de exames: rode num terminal ou com --yes'


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
        reason = asyncio.run(asyncio.wait_for(servidores.image_problem(server.url, image), CHECK_SECONDS))
    except Exception as error:  # it listed its tools a moment ago: a timeout or a dropped stream
        raise RunError(f'{name.upper()} (MCP) não conferiu a imagem em {server.url} ({type(error).__name__}); '
                       'suba os serviços com `docker compose up -d --wait`') from None
    if reason:
        raise RunError(ocr_refused(reason))


def record(found, result):
    """Keep the reply of the API tool: blocked before the POST, refused, or the appointment.

    Only a reply that is not a block means the POST was sent (api_called): a call the
    callback blocked wrote nothing.
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
    without a role, so this is defense in depth: the CLI never says nothing was written."""
    if response.name == spec.tool_for('book'):
        record(found, response.response)
    elif response.name not in (spec.tool_for('read'), spec.tool_for('search'), CONFIRMATION):
        reply = response.response
        if not (isinstance(reply, dict) and {'blocked', 'pending_confirmation'} & reply.keys()):
            found['api_called'] = True


def new_found():
    """What one run left, filled by run_agent from the events and the session state: the API's reply
    (appointment, api_error, blocked; api_called: a POST, or a tool without a role, answered), the OCR's
    counts (pii_masked, text_removed, instructions_removed, unrecognized lines, ocr_read, ocr_error,
    file_refused), the policy's sorting (candidates, low_confidence, confirmed; listing, invented), the
    run (tools_called, tool_seconds, model, model_error, order_unchecked), and one input: questions,
    whether someone answers [s/N]."""
    return {'api_called': False, 'appointment': None, 'api_error': None, 'blocked': None, 'pii_masked': {},
            'text_removed': 0, 'unrecognized': [], 'instructions_removed': 0, 'candidates': {}, 'low_confidence': [], 'confirmed': [],
            'tools_called': set(), 'tool_seconds': {}, 'ocr_read': True,
            'listing': [], 'invented': []}


async def answers_to(requests, started, found):
    """The user message that resumes the paused calls: the answer to the final question of each, asked
    off the event loop (the MCP sessions keep running while the person reads). With no terminal, the
    list is shown and not confirmed."""
    parts = []
    for request in requests:
        question = ((request.args or {}).get('toolConfirmation') or {}).get('hint') or ''
        yes = await asyncio.to_thread(confirmacao.ask_person, question)
        if yes is None:
            print(question)
            found['no_terminal'] = True
        original = ((request.args or {}).get('originalFunctionCall') or {}).get('id')
        started[original] = time.time()  # the call runs again from here: the wait is not the tool's time
        parts.append(types.Part(function_response=types.FunctionResponse(
            name=CONFIRMATION, id=request.id, response={'confirmed': bool(yes)})))
    return types.Content(role='user', parts=parts)


async def run_agent(app, image, spec, found):
    """Run the generated `app` as `adk run` does, filling `found` as it goes (also when Gemini fails
    midway). A call that asks for confirmation pauses the run; it resumes, in the same invocation,
    with the answers. A model the main Gemini refuses with 429/503 is answered by the reserve, per
    request (runtime/adk.py): a model call again, never a tool call, so nothing is asked or booked twice."""
    runner = InMemoryRunner(app=app)
    # The model sees a token, never the file name, which can carry a patient's name; the OCR's
    # before_tool turns the token back into the name. The image checked here, and whether anyone
    # answers the questions, go to the order's record in the agent's callbacks (runtime/pedido.py).
    session = await runner.session_service.create_session(app_name=app.name, user_id='cli')
    BookingCallbacks.of(app.root_agent).orders.start(session, image, ask=found.get('questions'))
    message = types.Content(role='user', parts=[types.Part(text=f'Arquivo do pedido: {image_token(image)}')])
    started, invocation = {}, None  # call id -> timestamp of the event that asked for it
    try:
        while message is not None:
            requests = []
            async for event in runner.run_async(user_id='cli', session_id=session.id, new_message=message,
                                                invocation_id=invocation):
                invocation = event.invocation_id
                found['model'] = getattr(event, 'model_version', None) or found.get('model')
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
            message = await answers_to(requests, started, found) if requests else None
        # The agent's callbacks left a copy of the order's record in the session state.
        state = (await runner.session_service.get_session(app_name=app.name, user_id='cli', session_id=session.id)).state
        for key in ('pii_masked', 'text_removed', 'instructions_removed', 'candidates', 'low_confidence', 'confirmed',
                    'listing', 'invented', 'model_error', 'cancel_unlinked'):
            found[key] = state.get(key, found.get(key))
        found['unrecognized'] = [index + 1 for index, kind in enumerate(state.get('ocr_intent') or [])
                                 if kind == 'unrecognized']
        found['ocr_read'] = 'ocr_lines' in state
        found['ocr_error'] = state.get('ocr_error')
        found['file_refused'] = state.get('file_refused', False)
        # The check of the whole order, made by the agent's report (runtime/callbacks.py) as the run ended.
        found['low_confidence'] = found['low_confidence'] + state.get('unreported', [])
        found['order_unchecked'] = state.get('order_unchecked', False)
    finally:
        await runner.close()


def failure_message(error, found):
    """The run's failure in one line: Gemini's (an exception, or the agent's model_error) or another."""
    gemini = api_error_in(error)
    if gemini is not None:
        text = model_failure(gemini.code, redact(str(gemini.message)))
    elif error is None:
        text = found['model_error']
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
    model = found.get('model') or os.environ.get('GEMINI_MODEL') or spec.model  # the one that answered
    return f'Tempo: {" · ".join([*parts, f"total {seconds(total)} s"])} (modelo {model})'


def print_reading(found):
    """What the OCR masked or removed, the exams the person confirmed and the ones left out (the same
    lines as the agent's own report, runtime/relatorio.py)."""
    print('\n' + '\n'.join(reading_lines(found)) + '\n')


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
    """The spec, once agent.py is what it generates, its services answer and the OCR accepts the image."""
    spec = load_spec(args.spec)
    if not (spec.tool_for('read') and spec.tool_for('search')):
        raise RunError(f'{args.spec}: `cli run` lê um pedido em imagem e busca os exames no catálogo, e esta spec '
                       'não tem roles.read e roles.search; ela pode ser transpilada, não rodada pela CLI')
    check_agent(args, spec)
    check_image(spec, args.image, check_services(spec))
    return spec


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
        reason = NO_TERMINAL if found.get('no_terminal') else found['blocked']
        return f'agendamento bloqueado antes de chamar a API: {reason}; nada foi agendado'
    if not found['api_error']:
        return 'o agente terminou sem um agendamento confirmado pela API'
    return f'a API recusou o agendamento ({found["api_error"]})'


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
    # The person confirms the list; --yes: the rules alone (the order's record says so), and the exams
    # that need a yes are left out, never assumed. With no terminal and no --yes, nothing is booked.
    start, found = time.monotonic(), new_found() | {'questions': not args.yes}
    try:
        try:
            asyncio.run(run_agent(load_root_agent(args.checked_agent, name='app'), args.image, spec, found))
        except Exception as error:  # reported to the user, never swallowed
            raise RunError(failure_message(error, found)) from None
        if found.get('model_error'):  # the agent ended the failed step in one line: the CLI says it
            raise RunError(failure_message(None, found))
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
                         help='agenda sem a confirmação final da lista, por sua conta: só as regras decidem, e só o '
                              'que elas agendariam sozinhas; os exames que pediriam confirmação ficam de fora')
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
