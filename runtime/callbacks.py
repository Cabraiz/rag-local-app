"""The agent's ADK callbacks, set up with the spec's tools, servers and booking policy: open the order
(start_order), keep what each tool returned (after_tool), let only confident codes, each on a line that
asks for it, reach the booking API (before_tool), and close with a report written from what the tools
returned (report). The model only proposes; these checks run in code, on the order's record
(runtime/pedido.py), never on the session state.
"""
import asyncio
import json
import uuid

from google.adk.models.llm_response import LlmResponse
from google.genai import types

from . import confirmacao, entrada, rede, relatorio, servidores
from .adk import check_host
from .confianca import BookingPolicy, omitted, remember_ocr, remember_search, sort_out
from .pedido import Orders

# The booking call's arguments: the exams (checked) and the run's Idempotency-Key (ours, never the model's).
BOOKING_ARGUMENTS = {'exams', 'idempotency_key'}
# Keys of a tool reply that mean nothing was written: the callback's block or question, or an HTTP error.
NOT_SENT = {'blocked', 'pending_confirmation', 'error'}
ASK_FOR_IMAGE = ('Informe só o nome de um arquivo de pedido em samples/ (.png, .jpg ou .jpeg), sem pastas, '
                 'por exemplo: pedido.png. Nada foi lido nem agendado.')
NO_ATTACHMENTS = ('Envie só o nome do arquivo do pedido, como texto, sem anexar a imagem nem outros dados: a imagem é '
                  'lida pelo OCR, que mascara os dados pessoais antes do modelo. Nada foi lido nem agendado.')
RAN_BEFORE = ('Esta sessão já tratou um pedido, antes de o agente ser reiniciado: confira os agendamentos e não '
              'repita este pedido. Para outro pedido, abra uma nova sessão. Nada foi lido nem agendado.')
ONE_ORDER = ('Esta sessão já tratou um pedido. Para outro pedido, abra uma nova sessão (no `adk run`, saia com '
             'exit e rode de novo; no `adk web`, New Session). Nada foi lido nem agendado.')


def said(text):
    """A message from the pipeline itself (it ends the turn when returned by start_order)."""
    return types.Content(role='model', parts=[types.Part(text=text)])


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


class BookingCallbacks:
    """Callbacks for every agent of the pipeline; each acts only on its own tool."""

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
        self.orders = Orders()

    @staticmethod
    def of(agent):
        """The BookingCallbacks of a generated pipeline (its start_order belongs to them)."""
        owner = getattr(getattr(agent, 'before_agent_callback', None), '__self__', None)
        if not isinstance(owner, BookingCallbacks):
            raise ValueError(f'{getattr(agent, "name", agent)} não é um pipeline gerado pelo transpile')
        return owner

    async def start_order(self, callback_context):
        """before_agent_callback of the pipeline. The order's image comes from `cli run` (orders.start) or
        from the user's message, which must be text naming one bare image file (`adk run`, `adk web`):
        it becomes the same token, the servers' addresses are checked and the reading server checks the
        file, all before any model turn. A message returned here ends the turn: nothing was read. One
        order per session: its key, answers and appointment are the session's."""
        order, invocation = self.orders.of(callback_context), callback_context.invocation_id
        if order.get('order_invocation', invocation) != invocation:
            return said(self.already_handled(order))
        content, need_image = callback_context.user_content, self.ocr_tool is not None and not order.get('image_file')
        if not all(entrada.accepted(part) for part in (content.parts if content else None) or []):
            return said(NO_ATTACHMENTS)
        if 'order_invocation' not in order and any(  # an order of this session ran before this process
                event.invocation_id != invocation and event.author not in ('user', callback_context.agent_name)
                for event in getattr(getattr(callback_context, 'session', None), 'events', [])):  # ADK's, not state
            return said(RAN_BEFORE)
        names = entrada.image_names(' '.join(entrada.texts_of(content))) if need_image else []
        if need_image and (len(names) != 1 or '/' in names[0] or '\\' in names[0]):
            return said(ASK_FOR_IMAGE)
        # Taken before any await: a 2nd message of this session, sent at once, is refused, not run beside it.
        if self.orders.claim(order, invocation) != invocation:
            return said(self.already_handled(order))
        problems = await asyncio.to_thread(rede.check_urls, self.servers)  # cli run: checked and pinned already
        problem = ('Endereço recusado: ' + '; '.join(problems) + '.' if problems else
                   (await self.image_problem(names[0]) or '').replace(names[0], entrada.image_token(names[0]))
                   if need_image else '')
        if problem:
            order.pop('order_invocation', None)  # nothing ran: a corrected message may start the order
            return said(problem.rstrip('.') + '. Nada foi lido nem agendado.')
        if need_image:
            order.update({'image_token': entrada.image_token(names[0]), 'image_file': names[0]})
        self.orders.publish(callback_context, order)
        return None

    def already_handled(self, order):
        """The answer to a new message in a session whose order already ran. If the API was called, what
        it answered, so that the person does not book the same order again in another session."""
        appointment = order.get('booked_appointment')
        if isinstance(appointment, dict):
            return (f'Esta sessão já tratou um pedido, e o agendamento {appointment.get("id")} já foi criado: não '
                    'repita este pedido.' + ('\n' + relatorio.report(order, books=self.booking_tool is not None)
                                             if 'ocr_lines' in order else ''))  # an evicted order keeps no lines
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
        and the next steps make no model call (before_model). The report then says nothing was booked,
        and `cli run` shows the same line. Any other error goes on as it is."""
        error = relatorio.api_error_in(error)
        if error is None:
            return None
        order = self.orders.of(callback_context)
        order['model_error'] = relatorio.model_failure(error.code, error.message)
        self.orders.publish(callback_context, order)
        return LlmResponse(content=said(order['model_error']))

    def before_model(self, callback_context, llm_request):
        """before_model_callback of every step: the model never sees the image's real name, the person's
        own text or anything but text and tool calls (runtime/entrada.py)."""
        order = self.orders.of(callback_context)
        if order.get('model_error'):  # an earlier step's model failed: no further model call in this run
            return LlmResponse(content=said(order['model_error']))
        sent = [part for event in callback_context.session.events if event.author == 'user'
                for part in (event.content.parts if event.content else None) or []]
        entrada.scrub(llm_request, sent, order.get('image_token'), order.get('image_file'))
        return None

    async def report(self, callback_context):
        """after_agent_callback of the pipeline: the check of the whole order (runtime/reconcilia.py,
        on the spec's catalog server) and the run's final message, written from the order's record:
        the appointment is the API's reply as the runtime saw it, never the model's account of it."""
        order = self.orders.of(callback_context)
        if order.get('pending'):  # a call still waits for the person's answer: the run is not over
            return None
        order['unreported'], order['order_unchecked'] = await servidores.unreported_exams(
            order, self.search_url, self.search_tool, self.policy)
        order['finished'] = True
        self.orders.publish(callback_context, order)
        return said(relatorio.report(order, books=self.booking_tool is not None))

    def after_tool(self, tool, args, tool_context, tool_response):
        """Keep the OCR's lines, counts and readings, the candidates of each search and the API's reply."""
        order, result = self.orders.of(tool_context), mcp_payload(tool_response)
        reply = None  # the tool's reply goes to the model unchanged, but for an OCR error naming the file
        if tool.name == self.ocr_tool and isinstance(tool_response, dict) and tool_response.get('isError'):
            # The OCR refused the image (not found, wrong type, corrupt, too large): keep its reason.
            texts = [item.get('text', '') for item in tool_response.get('content', []) if isinstance(item, dict)]
            prefix = f'Error executing tool {tool.name}: '  # added by the MCP SDK, not for the user
            order['ocr_error'] = ' '.join(text for text in texts if text).removeprefix(prefix)[:300]
            reply = entrada.without_file_name(tool_response, order)
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
        self.orders.publish(tool_context, order)
        return reply

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
        order = self.orders.of(tool_context)
        if tool.name == self.ocr_tool:
            reply = entrada.real_file(args, order)
        else:
            reply = self.only_confident_codes(args, tool_context, order)
        self.orders.publish(tool_context, order)
        return reply

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
        own piece of the order. A dict reply skips the call: nothing is written. The question is ADK's
        tool confirmation: the call pauses, the client answers, and the same call resumes with the
        answers (tool_context.tool_confirmation; runtime/confirmacao.py).
        """
        candidates, exams = order.get('candidates', {}), {}
        confirmation = getattr(tool_context, 'tool_confirmation', None)
        pending = dict(order.get('pending', {}))
        asked_here = pending.pop(confirmacao.call_of(tool_context), []) if confirmation is not None else []
        order['pending'] = pending  # this call's question, if it asked one, is answered now
        order['answers'] = answers = confirmacao.answers_given(order, confirmation, [item['code'] for item in asked_here])
        if isinstance(order.get('booked_appointment'), dict):  # one appointment per run: a 2nd call
            self.settle(order, asked_here, answers)            # never reaches the API
            return order['booked_appointment']
        extra = sorted(set(args) - BOOKING_ARGUMENTS)
        if extra:  # only the exams, checked below, and our key reach the API: no free text from the model
            return blocked(order, 'campo(s) fora do agendamento conferido: ' + ', '.join(extra))
        # One Idempotency-Key per session (derived from it, runtime/pedido.py), never the model's (it could carry text
        # from the order): a POST sent twice in the session gets the same appointment back from the API.
        order['idempotency_key'] = order.get('idempotency_key') or order.get('own_key') or uuid.uuid4().hex
        args['idempotency_key'] = order['idempotency_key']
        for exam in (exam for exam in args.get('exams', []) if isinstance(exam, dict)):
            exams.setdefault(str(exam.get('code')), exam)  # each code once, in the model's order
        invented = sorted(set(exams) - set(candidates))
        if invented:
            return blocked(order, 'código(s) que nenhuma busca no catálogo devolveu: ' + ', '.join(invented))
        accounted: list = []  # the pieces of text the proposed exams were sorted out on
        booked, to_ask, left_out = sort_out(exams, candidates, answers, order, self.policy, accounted)  # a "no" frees its text
        asks = order['ask'] if 'ask' in order else self.can_ask()  # cli run --yes: nobody will answer
        if to_ask and confirmation is None and asks and hasattr(tool_context, 'request_confirmation'):
            return confirmacao.pause_for_answer(tool_context, order, to_ask)
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
            order, exams = self.orders.of(callback_context), {}
            for exam in listed(callback_context.state.get(key)):  # the model's answer
                exams.setdefault(str(exam['code']), exam)  # each code once, in the model's order
            candidates = order.get('candidates', {})
            order['invented'] = sorted(set(exams) - set(candidates))
            exams = {code: exam for code, exam in exams.items() if code in candidates}
            accounted: list = []
            sure, check, left_out = sort_out(exams, candidates, {}, order, self.policy, accounted)
            left_out += omitted(exams, accounted, order, self.policy)
            order['accounted'] = [list(entry) for entry in accounted]
            order['listing'] = by_confidence([item | {'check': False} for item in sure]
                                             + [item | {'check': True} for item in check], exams)
            order['low_confidence'] = by_confidence(left_out, exams)
            self.orders.publish(callback_context, order)
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
