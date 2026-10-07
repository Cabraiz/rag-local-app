"""The agent's ADK callbacks, set up with the spec's tools, servers and booking policy.

start_order (before the pipeline): the order's image from `cli run` or from the user's message (`adk
  run`, `adk web`), turned into a token; the servers' addresses and the image checked before any
  model turn; one order per session.
before_model: the model sees the token, never the image's real name, and only text and tool calls.
after_tool: keep what the OCR read (its lines, readings and what each line asks for), what each search
  found and the API's reply (the order's record).
before_tool: fix the search's top_k, and let only confident codes reach the booking API, each written on
a line that asks for it (never one the order says not to do, or says was done: runtime/confianca.py).
before_agent: an earlier step that wrote nothing leaves its output_key empty.
after_agent (a pipeline that lists exams instead of booking): the list, sorted by the same policy.
report (after the pipeline): the check of the whole order and the run's final message, from the record.
The model only proposes; these checks run in code, so no text from the order can skip them.

What the policy trusts lives in the order's record, kept by BookingCallbacks per session and never
read back from the session state, which ADK's clients can write.
"""
import asyncio
import copy
import json
import uuid
from pathlib import Path

from google.adk.models.llm_response import LlmResponse
from google.genai import errors, types

from . import confirmacao, rede, relatorio, servidores
from .adk import check_host
from .confianca import BookingPolicy, omitted, remember_ocr, remember_search, sort_out
from .reconcilia import order_lines, unreported

# The booking call's arguments: the exams (checked) and the run's Idempotency-Key (ours, never the model's).
BOOKING_ARGUMENTS = {'exams', 'idempotency_key'}
# Keys of a tool reply that mean nothing was written: the callback's block or question, or an HTTP error.
NOT_SENT = {'blocked', 'pending_confirmation', 'error'}
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg')  # the OCR server's own
ASK_FOR_IMAGE = ('Informe só o nome de um arquivo de pedido em samples/ (.png, .jpg ou .jpeg), sem pastas, '
                 'por exemplo: pedido.png. Nada foi lido nem agendado.')
NO_ATTACHMENTS = ('Envie só o nome do arquivo do pedido, como texto, sem anexar a imagem nem outros dados: a imagem é '
                  'lida pelo OCR, que mascara os dados pessoais antes do modelo. Nada foi lido nem agendado.')
ONE_ORDER = ('Esta sessão já tratou um pedido. Para outro pedido, abra uma nova sessão (no `adk run`, saia com '
             'exit e rode de novo; no `adk web`, New Session). Nada foi lido nem agendado.')
# A part of what the model is sent may carry only these: text, and the tool calls and their replies.
# Anything else a client can put in a message (a file, code, a code result...) is left out.
PART_FIELDS = ('text', 'thought', 'thought_signature', 'function_call', 'function_response')
CONFIRMATION = 'adk_request_confirmation'  # the client's answer to the question, when a call resumes
PRIVATE = ('image_file',)  # never copied to the session state


def image_token(name):
    """What the model calls the order's image: never the file name, which can carry a patient's name."""
    return f'pedido-1{Path(name).suffix.lower()}'


def image_names(text):
    """The image file names a message names: words ending in .png, .jpg or .jpeg (any case), quotes
    and punctuation around them left out. A name with a folder is kept whole, to be refused."""
    around = '\'"`“”‘’()[]{}<>,;:!?'
    names = [word.lstrip(around).rstrip(around + '.') for word in str(text or '').split()]
    return list(dict.fromkeys(name for name in names if name.lower().endswith(IMAGE_SUFFIXES)))


def said(text):
    """A message from the pipeline itself (it ends the turn when returned by start_order)."""
    return types.Content(role='model', parts=[types.Part(text=text)])


def texts_of(content):
    return [part.text for part in (content.parts if content else None) or [] if part.text]


def fields_of(part):
    return set(part.model_dump(exclude_none=True))


def accepted(part):
    """A part a person may send: text only, or the answer to the question."""
    fields = fields_of(part)
    return fields == {'text'} or (fields == {'function_response'} and part.function_response.name == CONFIRMATION)


def only_allowed(part):
    """The part with only the fields of PART_FIELDS; a part with none of them becomes a marker."""
    if fields_of(part) <= set(PART_FIELDS):
        return part
    kept = {name: getattr(part, name) for name in PART_FIELDS if getattr(part, name) is not None}
    return types.Part(**kept) if kept else types.Part(text='[parte removida]')


def mcp_payload(response):
    """JSON inside an MCP reply: structuredContent ({"result": [...]} for a list) or one text item per value."""
    if not isinstance(response, dict) or response.get('isError'):
        return None  # a tool error (e.g. file not found) reaches the model unchanged
    structured = response.get('structuredContent')
    if structured is not None:
        return structured['result'] if structured.keys() == {'result'} else structured
    try:
        items = [json.loads(item['text']) for item in response.get('content', []) if item.get('type') == 'text']
    except (ValueError, KeyError, TypeError):
        return None
    return items[0] if len(items) == 1 else items


def listed(answer):
    """The {code, name} items of an agent's answer: a JSON list, also inside a ```json block."""
    text = str(answer or '')
    start, end = text.find('['), text.rfind(']')
    try:
        items = json.loads(text[start:end + 1]) if 0 <= start < end else []
    except ValueError:
        items = []
    return [item for item in items if isinstance(item, dict) and 'code' in item] if isinstance(items, list) else []


def by_confidence(items, exams):
    """Most confident first, then in the model's order."""
    rank = {code: index for index, code in enumerate(exams)}
    return sorted(items, key=lambda item: (-item['confidence'], rank.get(item['code'], len(rank))))


def blocked(order, reason):
    """The booking call's reply when nothing is sent, kept for the run's report."""
    order['blocked'] = reason
    return {'blocked': reason}


def answers_given(order, confirmation, asked):
    """{code: yes} of the run. `asked`: the codes this call asked about. The CLI resumes the call with
    one answer per exam (payload.respostas); `adk run`'s console and `adk web` answer the question once
    ({"confirmed": true|false}), and that answer is given to this call's exams only. Answers are given
    once per run: a repeated call never asks again."""
    answers = dict(order.get('answers', {}))
    if confirmation is None:
        return answers
    payload = confirmation.payload if isinstance(confirmation.payload, dict) else {}
    per_exam = payload.get('respostas')
    if isinstance(per_exam, dict):
        return answers | {str(code): bool(yes) for code, yes in per_exam.items() if str(code) in asked}
    return answers | {code: bool(confirmation.confirmed) for code in asked}


def call_of(tool_context):
    return str(getattr(tool_context, 'function_call_id', None))


def ask(tool_context, order, to_ask):
    """Pause the booking call for the person's answer (ADK's tool confirmation). The hint lists what is
    asked, for a client that shows only the hint (`adk run`'s console, `adk web`). Each call keeps its
    own question: an answer is given to the exams of the call it answers."""
    exams = '; '.join(f"'{relatorio.printable(item['read'])}' → {item['name']} {item['code']} "
                      f"(confiança {relatorio.confidence(item['confidence'])})" for item in to_ask)
    tool_context.request_confirmation(
        hint=f'Confirme os exames lidos com confiança média: {exams}. Confirmar inclui todos; recusar deixa todos de fora.',
        payload={'perguntas': to_ask})
    tool_context.actions.skip_summarization = True  # the model does not see this reply as a result
    order['pending'] = order.get('pending', {}) | {call_of(tool_context): to_ask}
    return {'pending_confirmation': [item['code'] for item in to_ask]}


def session_key(session):
    return session.app_name, session.user_id, session.id


class BookingCallbacks:
    """Callbacks for every agent of the pipeline; each acts only on its own tool.

    What the policy trusts (the image, the lines read, the codes each search returned, the answers
    and the questions asked, the Idempotency-Key, the API's reply) is kept here, one record per
    session, and never read back from the session state: ADK's clients can write the state (`adk
    web` when a session is created and on each /run, `adk run --state`). The state gets a copy of the
    record after each callback, for the CLI and the person to read. A context with no session (a
    direct call outside ADK's runner) keeps the record in its own state.
    """

    def __init__(self, *, ocr_tool: str | None = None, search_tool: str | None = None,
                 booking_tool: str | None = None, policy: BookingPolicy | None = None,
                 ocr_url: str | None = None, search_url: str | None = None, servers: list[str] | tuple = ()):
        self.ocr_tool, self.search_tool, self.booking_tool = ocr_tool, search_tool, booking_tool
        self.policy = policy or BookingPolicy()
        # The MCP servers of the reading and search roles, and every server of the spec (their addresses
        # are checked when an order starts), each on a host that ALLOWED_HOSTS allows.
        self.ocr_url, self.search_url, self.servers = ocr_url, search_url, [*servers]
        for url in (ocr_url, search_url, *servers):
            if url:
                check_host(url)
        self.can_ask = confirmacao.can_ask  # tests replace it
        self.orders: dict = {}  # (app, user, session) -> the order's record: one small dict per session

    @staticmethod
    def of(agent):
        """The BookingCallbacks of a generated pipeline (its start_order belongs to them)."""
        owner = getattr(getattr(agent, 'before_agent_callback', None), '__self__', None)
        if not isinstance(owner, BookingCallbacks):
            raise ValueError(f'{getattr(agent, "name", agent)} não é um pipeline gerado pelo transpile')
        return owner

    def order(self, context):
        """The session's order record (see the class)."""
        session = getattr(context, 'session', None)
        if session is None:
            return context.state
        return self.orders.setdefault(session_key(session), {})

    @staticmethod
    def publish(context, order):
        """A copy of the order's record in the session state, for the CLI and the person to read."""
        if order is not context.state:
            context.state.update({key: copy.deepcopy(value) for key, value in order.items() if key not in PRIVATE})

    def open_order(self, session, image_file, **carried):
        """cli run: the order of a session the CLI created, with the image it checked and, for the
        reserve model's run, the answers and the Idempotency-Key of the first run."""
        self.orders[session_key(session)] = {'image_file': image_file, 'image_token': image_token(image_file),
                                             **carried}

    async def start_order(self, callback_context):
        """before_agent_callback of the pipeline. The order's image comes from `cli run` (open_order) or
        from the user's message, which must be text naming one bare image file (`adk run`, `adk web`):
        it becomes the same token, the servers' addresses are checked and the reading server checks the
        file, all before any model turn. A message returned here ends the turn: nothing was read. One
        order per session: its key, answers and appointment are the session's."""
        order, invocation = self.order(callback_context), callback_context.invocation_id
        if order.get('order_invocation', invocation) != invocation:
            return said(self.already_handled(order))
        content = callback_context.user_content
        if not all(accepted(part) for part in (content.parts if content else None) or []):
            return said(NO_ATTACHMENTS)
        problems = await asyncio.to_thread(rede.check_urls, self.servers)  # cli run: checked and pinned already
        if problems:
            return said('Endereço recusado: ' + '; '.join(problems) + '. Nada foi lido nem agendado.')
        if self.ocr_tool is None or order.get('image_file'):
            order['order_invocation'] = invocation
            self.publish(callback_context, order)
            return None
        names = image_names(' '.join(texts_of(content)))
        if len(names) != 1 or '/' in names[0] or '\\' in names[0]:
            return said(ASK_FOR_IMAGE)
        name, token = names[0], image_token(names[0])
        problem = await self.image_problem(name)
        if problem:
            return said(problem.replace(name, token) + ' Nada foi lido nem agendado.')
        order.update({'image_token': token, 'image_file': name, 'order_invocation': invocation})
        self.publish(callback_context, order)
        return None

    def already_handled(self, order):
        """The answer to a new message in a session whose order already ran. If the API was called, what
        it answered, so that the person does not book the same order again in another session."""
        appointment = order.get('booked_appointment')
        if isinstance(appointment, dict):
            return (f'Esta sessão já tratou um pedido, e o agendamento {appointment.get("id")} já foi criado: não '
                    'repita este pedido.\n' + relatorio.report(order, books=self.booking_tool is not None))
        if order.get('idempotency_key'):
            return ('Esta sessão já tratou um pedido, e a execução parou com o agendamento já preparado: a API pode '
                    'tê-lo criado. Confira os agendamentos antes de repetir o pedido.')
        return ONE_ORDER

    async def image_problem(self, name):
        """Why the reading server refuses the file, or None; asked as `cli run` asks it (check_image)."""
        if not self.ocr_url:
            return None
        try:
            reason = await asyncio.wait_for(servidores.image_problem(self.ocr_url, name), servidores.CHECK_SECONDS)
        except Exception as error:  # down, or the stream dropped
            return (f'O servidor de leitura não conferiu a imagem em {self.ocr_url} ({type(error).__name__}); '
                    'suba os serviços com `docker compose up -d --wait`.')
        return f'OCR recusou a imagem: {reason}.' if reason else None

    def model_failed(self, callback_context, llm_request, error):
        """on_model_error_callback of every step: when Gemini refuses the call (also the reserve model,
        which ADK's FallbackModel already tried: runtime/adk.py), the step ends with one line saying so,
        instead of a traceback in `adk run` or `adk web`, and the next steps make no model call
        (before_model). The report then says nothing was booked. Any other error goes on as it is.
        `cli run` takes these callbacks off: it reports a model failure itself (cli.py)."""
        while error is not None and not isinstance(error, errors.APIError):
            error = error.__cause__ or error.__context__
        if error is None:
            return None
        order = self.order(callback_context)
        order['model_error'] = (f'Gemini indisponível no momento (HTTP {error.code}), também no modelo reserva; '
                                'tente novamente' if error.code in (429, 500, 503) else
                                f'o Gemini recusou a chamada (HTTP {error.code})')
        self.publish(callback_context, order)
        return LlmResponse(content=said(order['model_error']))

    def before_model(self, callback_context, llm_request):
        """before_model_callback of every step: in what the model is sent, the user's own text is the
        message `cli run` sends ("Arquivo do pedido: <token>"), the image's real name, anywhere else, is
        the token, and a part carries only text and tool calls and replies: no file, code or other data
        a client sent, and no part of the user's own that is not text. The person typed the name; the
        model never sees it, nor anything unmasked."""
        order = self.order(callback_context)
        if order.get('model_error'):  # an earlier step's model failed: no further model call in this run
            return LlmResponse(content=said(order['model_error']))
        token, real = order.get('image_token'), order.get('image_file')
        sent = [part for event in callback_context.session.events if event.author == 'user'
                for part in (event.content.parts if event.content else None) or []]
        typed = {part.text for part in sent if part.text}
        others = [part for part in sent if fields_of(part) != {'text'}]  # an answer, or a part start_order refused
        for content in llm_request.contents:
            parts = [only_allowed(part) for part in content.parts or [] if part not in others]
            content.parts = parts or [types.Part(text='[parte removida]')]
        for part in (part for content in llm_request.contents for part in content.parts if part.text):
            if part.text in typed:
                part.text = f'Arquivo do pedido: {token}' if token else '[mensagem]'
            elif token and real and real in part.text:
                part.text = part.text.replace(real, token)
        return None

    async def report(self, callback_context):
        """after_agent_callback of the pipeline: the check of the whole order (runtime/reconcilia.py,
        on the spec's catalog server) and the run's final message, written from the order's record:
        the appointment is the API's reply as the runtime saw it, never the model's account of it."""
        order = self.order(callback_context)
        if order.get('pending'):  # a call still waits for the person's answer: the run is not over
            return None
        order['unreported'], order['order_unchecked'] = await self.whole_order(order)
        self.publish(callback_context, order)
        return said(relatorio.report(order, books=self.booking_tool is not None))

    async def whole_order(self, order):
        """(the exams of the order the run left in no reported state, True if the search did not answer)."""
        texts = list(dict.fromkeys(text for _, text, _ in order_lines(order.get('ocr_read', []))))
        if not texts or not (self.search_url and self.search_tool):
            return [], False
        try:
            hits = await asyncio.wait_for(servidores.search_lines(self.search_url, self.search_tool, texts,
                                                                  self.policy.top_k), servidores.CHECK_SECONDS)
        except Exception:  # the search went down after the run: say the order was not checked
            return [], True
        appointment = order.get('booked_appointment') if isinstance(order.get('booked_appointment'), dict) else {}
        settled = {exam.get('code') for exam in appointment.get('exams') or [] if isinstance(exam, dict)}
        settled |= {item['code'] for item in [*order.get('low_confidence', []), *order.get('confirmed', [])]}
        return unreported(order, hits.get, self.policy, settled), False

    def after_tool(self, tool, args, tool_context, tool_response):
        """Keep the OCR's lines, counts and readings, the candidates of each search and the API's reply."""
        order = self.order(tool_context)
        reply = self.remember(order, tool, args, tool_response)
        self.publish(tool_context, order)
        return reply

    def remember(self, order, tool, args, tool_response):
        result = mcp_payload(tool_response)
        if tool.name == self.ocr_tool and isinstance(tool_response, dict) and tool_response.get('isError'):
            # The OCR refused the image (not found, wrong type, corrupt, too large): keep its reason.
            texts = [item.get('text', '') for item in tool_response.get('content', []) if isinstance(item, dict)]
            prefix = f'Error executing tool {tool.name}: '  # added by the MCP SDK, not for the user
            order['ocr_error'] = ' '.join(text for text in texts if text).removeprefix(prefix)[:300]
            return self.without_file_name(tool_response, order)  # the reason may quote the real name
        elif tool.name == self.ocr_tool and isinstance(result, dict):
            remember_ocr(order, result)
        elif tool.name == self.search_tool and result is not None:
            remember_search(order, args.get('query', ''), result if isinstance(result, list) else [result], self.policy)
        elif tool.name == self.booking_tool and isinstance(tool_response, dict) and not NOT_SENT & tool_response.keys():
            # Any answer of the API that is not an error is the run's one appointment, whatever its
            # fields: a later call gets it back instead of a second POST.
            order['booked_appointment'] = tool_response
        elif tool.name == self.booking_tool and isinstance(tool_response, dict) and 'error' in tool_response:
            order['api_error'] = str(tool_response['error'])[:1000]  # the API's refusal, for the report
        return None  # keep the tool's reply unchanged

    def before_tool(self, tool, args, tool_context):
        """The OCR reads the real file; the search returns the spec's top_k; the booking API gets only
        confident codes. Any other tool is refused: a tool without a role is never called unchecked."""
        if tool.name is None or tool.name not in (self.ocr_tool, self.search_tool, self.booking_tool):
            # Fail closed: the transpiler gives every tool a role; this holds for a hand-edited file too.
            kind = 'operação de API' if getattr(tool, 'endpoint', None) is not None else 'ferramenta'
            return {'blocked': f'{kind} sem papel conferido pelo runtime ({tool.name}); nada foi enviado'}
        if tool.name == self.search_tool:
            args['top_k'] = self.policy.top_k
            return None
        order = self.order(tool_context)
        if tool.name == self.ocr_tool:
            reply = self.real_file(args, order)
        else:
            reply = self.only_confident_codes(args, tool_context, order)
        self.publish(tool_context, order)
        return reply

    @staticmethod
    def real_file(args, order):
        """The model knows the order image only by a token (`image_token`, set by `cli run` or by
        start_order), so a file name that carries personal data ("pedido-joao-silva.png") never
        reaches it: the token becomes the real name here. Any other name is refused, like a code no
        search returned."""
        token, real = order.get('image_token'), order.get('image_file')
        if not (token and real) or args.get('filename') != token:
            order['file_refused'] = True
            return {'blocked': 'arquivo que não é o desta execução; use o nome informado na mensagem'}
        args['filename'] = real
        return None

    @staticmethod
    def without_file_name(response, order):
        """The OCR's error reply with the real file name turned back into the token, or None if there is
        nothing to hide."""
        token, real = order.get('image_token'), order.get('image_file')
        texts = [item for item in response.get('content', []) if isinstance(item, dict) and 'text' in item]
        if not (token and real) or not any(real in item['text'] for item in texts):
            return None
        return response | {'content': [item | {'text': item['text'].replace(real, token)} if item in texts else item
                                       for item in response['content']]}

    @staticmethod
    def settle(order, asked_here, answers):
        """A call that resumed after the run's appointment was created: its exams are reported (a yes that
        came too late to be booked, or a no), never sent in a second POST."""
        reported = {item['code'] for item in order.get('low_confidence', [])}
        order['low_confidence'] = order.get('low_confidence', []) + [
            item | {'reason': 'after_booking' if answers.get(item['code']) else 'declined'}
            for item in asked_here if item['code'] not in reported]

    def only_confident_codes(self, args, tool_context, order):
        """Book (>= min_confidence), ask the person (>= ask_from) or leave out each exam, on its
        own piece of the order. A dict reply skips the call: nothing is written.

        The question is ADK's tool confirmation: the call asks for one, the run pauses, the client
        (the CLI's [s/N], `adk run`'s console, `adk web`) answers, and the same call resumes with the
        answers (tool_context.tool_confirmation).
        """
        candidates, exams = order.get('candidates', {}), {}
        confirmation = getattr(tool_context, 'tool_confirmation', None)
        pending = dict(order.get('pending', {}))
        asked_here = pending.pop(call_of(tool_context), []) if confirmation is not None else []
        order['pending'] = pending  # this call's question, if it asked one, is answered now
        order['answers'] = answers = answers_given(order, confirmation, [item['code'] for item in asked_here])
        if isinstance(order.get('booked_appointment'), dict):  # one appointment per run: a 2nd call
            self.settle(order, asked_here, answers)            # never reaches the API
            return order['booked_appointment']
        extra = sorted(set(args) - BOOKING_ARGUMENTS)
        if extra:  # only the exams, checked below, and our key reach the API: no free text from the model
            return blocked(order, 'campo(s) fora do agendamento conferido: ' + ', '.join(extra))
        # One Idempotency-Key per run, never the model's (it could repeat across orders or carry text
        # from the order): a POST sent twice in the run gets the same appointment back from the API.
        order['idempotency_key'] = order.get('idempotency_key') or uuid.uuid4().hex
        args['idempotency_key'] = order['idempotency_key']
        for exam in (exam for exam in args.get('exams', []) if isinstance(exam, dict)):
            exams.setdefault(str(exam.get('code')), exam)  # each code once, in the model's order
        invented = sorted(set(exams) - set(candidates))
        if invented:
            return blocked(order, 'código(s) que nenhuma busca no catálogo devolveu: ' + ', '.join(invented))
        accounted: list = []  # the pieces of text the proposed exams were sorted out on
        booked, to_ask, left_out = sort_out(exams, candidates, answers, order, self.policy, accounted)  # a "no" frees its text
        if to_ask and confirmation is None and self.can_ask() and hasattr(tool_context, 'request_confirmation'):
            return ask(tool_context, order, to_ask)
        # Not asked: nobody to answer, no answer, or (ADK takes one question per call) an exam that only
        # fell in the band after a "no" freed its text.
        asked = [item['code'] for item in (asked_here if confirmation is not None else to_ask)]
        left_out += [item | {'reason': 'needs_confirmation' if item['code'] in asked else 'second_round'}
                     for item in to_ask]
        left_out += omitted(exams, accounted, order, self.policy)  # found by a search, but left out by the model
        order['accounted'] = [list(entry) for entry in accounted]  # for the check of the whole order
        booked, left_out = by_confidence(booked, exams), by_confidence(left_out, exams)
        order['low_confidence'] = left_out
        order['confirmed'] = [item for item in booked if answers.get(item['code'])]
        if not booked:
            return blocked(order, 'nenhum exame com confiança suficiente para agendar')
        args['exams'] = [exams[item['code']] for item in booked]
        return None

    def review_list(self, key):
        """after_agent_callback of the last agent of a pipeline that searches but does not book: the
        exams of its answer (a JSON list of {code, name}) sorted out as a booking would be, without
        a question. listing: each exam, `check` when it is in the question band; low_confidence: the
        ones left out, and the ones a search found but the list left out; invented: codes no search
        returned."""
        def review_listed_exams(callback_context):
            order, exams = self.order(callback_context), {}
            for exam in listed(callback_context.state.get(key)):  # the model's answer
                exams.setdefault(str(exam['code']), exam)  # each code once, in the model's order
            candidates = order.get('candidates', {})
            order['invented'] = sorted(set(exams) - set(candidates))
            exams = {code: exam for code, exam in exams.items() if code in candidates}
            accounted = []
            sure, check, left_out = sort_out(exams, candidates, {}, order, self.policy, accounted)
            left_out += omitted(exams, accounted, order, self.policy)
            order['accounted'] = [list(entry) for entry in accounted]
            order['listing'] = by_confidence([item | {'check': False} for item in sure]
                                             + [item | {'check': True} for item in check], exams)
            order['low_confidence'] = by_confidence(left_out, exams)
            self.publish(callback_context, order)
            return None
        return review_listed_exams

    @staticmethod
    def fill_missing(*keys):
        """before_agent_callback: the output_keys of earlier steps that wrote nothing (an order
        with no exam) become '' instead of failing this step's instruction."""
        def fill_missing_inputs(callback_context):
            for key in keys:
                if key not in callback_context.state:
                    callback_context.state[key] = ''
            return None
        return fill_missing_inputs
