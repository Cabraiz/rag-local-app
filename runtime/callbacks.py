"""The agent's ADK callbacks, set up with the spec's tools and booking policy.

after_tool: keep what the OCR read and what each search found (session state).
before_tool: fix the search's top_k, and let only confident codes reach the booking API, each written on
a line that asks for it (never one the order says not to do, or says was done: runtime/confianca.py).
before_agent: an earlier step that wrote nothing leaves its output_key empty.
after_agent (a pipeline that lists exams instead of booking): the list, sorted by the same policy.
The model only proposes; these checks run in code, so no text from the order can skip them.
"""
import json
import uuid

from . import confirmacao
from .confianca import BookingPolicy, omitted, remember_ocr, remember_search, sort_out

# The booking call's arguments: the exams (checked) and the run's Idempotency-Key (ours, never the model's).
BOOKING_ARGUMENTS = {'exams', 'idempotency_key'}
# Keys of a tool reply that mean nothing was written: the callback's block or question, or an HTTP error.
NOT_SENT = {'blocked', 'pending_confirmation', 'error'}


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


class BookingCallbacks:
    """Callbacks for every agent of the pipeline; each acts only on its own tool."""

    def __init__(self, *, ocr_tool: str | None = None, search_tool: str | None = None,
                 booking_tool: str | None = None, policy: BookingPolicy | None = None):
        self.ocr_tool, self.search_tool, self.booking_tool = ocr_tool, search_tool, booking_tool
        self.policy = policy or BookingPolicy()
        self.can_ask = confirmacao.can_ask  # tests replace it

    def after_tool(self, tool, args, tool_context, tool_response):
        """Keep the OCR's lines, counts and readings, and the candidates of each search."""
        result, state = mcp_payload(tool_response), tool_context.state
        if tool.name == self.ocr_tool and isinstance(tool_response, dict) and tool_response.get('isError'):
            # The OCR refused the image (not found, wrong type, corrupt, too large): keep its reason.
            texts = [item.get('text', '') for item in tool_response.get('content', []) if isinstance(item, dict)]
            prefix = f'Error executing tool {tool.name}: '  # added by the MCP SDK, not for the user
            state['ocr_error'] = ' '.join(text for text in texts if text).removeprefix(prefix)[:300]
            return self.without_file_name(tool_response, state)  # the reason may quote the real name
        elif tool.name == self.ocr_tool and isinstance(result, dict):
            remember_ocr(state, result)
        elif tool.name == self.search_tool and result is not None:
            remember_search(state, args.get('query', ''), result if isinstance(result, list) else [result], self.policy)
        elif tool.name == self.booking_tool and isinstance(tool_response, dict) and not NOT_SENT & tool_response.keys():
            # Any answer of the API that is not an error is the run's one appointment, whatever its
            # fields: a later call gets it back instead of a second POST.
            state['booked_appointment'] = tool_response
        return None  # keep the tool's reply unchanged

    def before_tool(self, tool, args, tool_context):
        """The OCR reads the real file; the search returns the spec's top_k; the booking API gets only
        confident codes. Any other tool is refused: a tool without a role is never called unchecked."""
        if tool.name is None or tool.name not in (self.ocr_tool, self.search_tool, self.booking_tool):
            # Fail closed: the transpiler gives every tool a role; this holds for a hand-edited file too.
            kind = 'operação de API' if getattr(tool, 'endpoint', None) is not None else 'ferramenta'
            return {'blocked': f'{kind} sem papel conferido pelo runtime ({tool.name}); nada foi enviado'}
        if tool.name == self.ocr_tool:
            return self.real_file(args, tool_context.state)
        if tool.name == self.search_tool:
            args['top_k'] = self.policy.top_k
            return None
        return self.only_confident_codes(args, tool_context)

    @staticmethod
    def real_file(args, state):
        """The model knows the order image only by a token (the CLI's `image_token`), so a file name
        that carries personal data ("pedido-joao-silva.png") never reaches it: the token becomes the
        real name here. Any other name is refused, like a code no search returned."""
        token, real = state.get('image_token'), state.get('image_file')
        if not (token and real) or args.get('filename') != token:
            state['file_refused'] = True
            return {'blocked': 'arquivo que não é o desta execução; use o nome informado na mensagem'}
        args['filename'] = real
        return None

    @staticmethod
    def without_file_name(response, state):
        """The OCR's error reply with the real file name turned back into the token, or None if there is
        nothing to hide."""
        token, real = state.get('image_token'), state.get('image_file')
        texts = [item for item in response.get('content', []) if isinstance(item, dict) and 'text' in item]
        if not (token and real) or not any(real in item['text'] for item in texts):
            return None
        return response | {'content': [item | {'text': item['text'].replace(real, token)} if item in texts else item
                                       for item in response['content']]}

    def only_confident_codes(self, args, tool_context):
        """Book (>= min_confidence), ask the person (>= ask_from) or leave out each exam, on its
        own piece of the order. A dict reply skips the call: nothing is written.

        The question is ADK's tool confirmation: the call asks for one, the run pauses, the CLI
        asks [s/N] and resumes this same call with the answers (tool_context.tool_confirmation).
        """
        state, candidates, exams = tool_context.state, tool_context.state.get('candidates', {}), {}
        if isinstance(state.get('booked_appointment'), dict):  # one appointment per run: a 2nd call
            return state['booked_appointment']                  # never reaches the API
        extra = sorted(set(args) - BOOKING_ARGUMENTS)
        if extra:  # only the exams, checked below, and our key reach the API: no free text from the model
            return {'blocked': 'campo(s) fora do agendamento conferido: ' + ', '.join(extra)}
        # One Idempotency-Key per run, never the model's (it could repeat across orders or carry text
        # from the order): a POST sent twice in the run gets the same appointment back from the API.
        state['idempotency_key'] = state.get('idempotency_key') or uuid.uuid4().hex
        args['idempotency_key'] = state['idempotency_key']
        for exam in (exam for exam in args.get('exams', []) if isinstance(exam, dict)):
            exams.setdefault(str(exam.get('code')), exam)  # each code once, in the model's order
        invented = sorted(set(exams) - set(candidates))
        if invented:
            return {'blocked': 'código(s) que nenhuma busca no catálogo devolveu: ' + ', '.join(invented)}
        answers = dict(state.get('answers', {}))  # given once per run: a repeated call never asks again
        confirmation = getattr(tool_context, 'tool_confirmation', None)
        if confirmation is not None:  # this call resumed with the person's answers
            answers |= {str(code): bool(yes) for code, yes in ((confirmation.payload or {}).get('respostas') or {}).items()}
        state['answers'] = answers
        accounted: list = []  # the pieces of text the proposed exams were sorted out on
        booked, to_ask, left_out = sort_out(exams, candidates, answers, state, self.policy, accounted)  # a "no" frees its text
        if to_ask and confirmation is None and self.can_ask() and hasattr(tool_context, 'request_confirmation'):
            tool_context.request_confirmation(hint='Confirme os exames lidos com confiança média.',
                                              payload={'perguntas': to_ask})
            tool_context.actions.skip_summarization = True  # the model does not see this reply as a result
            state['asked'] = [item['code'] for item in to_ask]
            return {'pending_confirmation': state['asked']}
        # Not asked: nobody to answer, no answer, or (ADK takes one question per call) an exam that only
        # fell in the band after a "no" freed its text.
        asked = state.get('asked', []) if confirmation is not None else [item['code'] for item in to_ask]
        left_out += [item | {'reason': 'needs_confirmation' if item['code'] in asked else 'second_round'}
                     for item in to_ask]
        left_out += omitted(exams, accounted, state, self.policy)  # found by a search, but left out by the model
        state['accounted'] = [list(entry) for entry in accounted]  # for the check of the whole order (cli)
        booked, left_out = by_confidence(booked, exams), by_confidence(left_out, exams)
        state['low_confidence'] = left_out
        state['confirmed'] = [item for item in booked if answers.get(item['code'])]
        if not booked:
            return {'blocked': 'nenhum exame com confiança suficiente para agendar'}
        args['exams'] = [exams[item['code']] for item in booked]
        return None

    def review_list(self, key):
        """after_agent_callback of the last agent of a pipeline that searches but does not book: the
        exams of its answer (a JSON list of {code, name}) sorted out as a booking would be, without
        a question. state['listing']: each exam, `check` when it is in the question band;
        state['low_confidence']: the ones left out, and the ones a search found but the list left
        out; state['invented']: codes no search returned."""
        def review_listed_exams(callback_context):
            state, exams = callback_context.state, {}
            for exam in listed(state.get(key)):
                exams.setdefault(str(exam['code']), exam)  # each code once, in the model's order
            candidates = state.get('candidates', {})
            state['invented'] = sorted(set(exams) - set(candidates))
            exams = {code: exam for code, exam in exams.items() if code in candidates}
            accounted = []
            sure, check, left_out = sort_out(exams, candidates, {}, state, self.policy, accounted)
            left_out += omitted(exams, accounted, state, self.policy)
            state['accounted'] = [list(entry) for entry in accounted]
            state['listing'] = by_confidence([item | {'check': False} for item in sure]
                                             + [item | {'check': True} for item in check], exams)
            state['low_confidence'] = by_confidence(left_out, exams)
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
