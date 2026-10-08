"""The final confirmation of the list, asked in code: before the API, the booking call pauses (ADK's tool
confirmation) with the whole list, each exam with its code and warning and the ones not booked, and one
question. `cli run` asks it in the terminal (ask_person), `adk run`'s console and `adk web`'s page show
the same text, and the same call resumes with the answer: only a yes to the list that call showed books.
"""
import os
import sys

from .relatorio import WHY, confidence, left_out_line, printable


def can_ask():
    """False with no interactive terminal or in CI: `cli run` then books nothing without --yes."""
    return not os.environ.get('CI') and sys.stdin.isatty() and sys.stdout.isatty()


def review(sure, asked, left_out):
    """The list and the question: the exams to book (an asked one with what was read and why), then the
    ones not booked, with the report's words (runtime/relatorio.py)."""
    lines = ['Exames para agendar:'] + [f'- {item["name"]} ({item["code"]})' for item in sure]
    lines += [f'- {item["name"]} ({item["code"]}): lido "{printable(item["read"])}", confiança '
              f'{confidence(item["confidence"])}{WHY.get(item.get("why"), "")}; confira' for item in asked]
    lines += ['Não agendados:'] * bool(left_out) + [f'- {left_out_line(item)}' for item in left_out]
    count = len(sure) + len(asked)
    return '\n'.join(lines + ['Agendar este exame?' if count == 1 else f'Agendar estes {count} exames?'])


def ask_person(question):
    """True only for "s" or "sim"; None when there is nobody to ask."""
    if not can_ask():
        return None
    try:
        answer = input(f'{question} [s/N] ')
    except EOFError:
        answer = ''
    return answer.strip().lower() in ('s', 'sim')


def call_of(tool_context):
    return str(getattr(tool_context, 'function_call_id', None))


def pause_for_answer(tool_context, order, listed, question):
    """Pause the booking call with the list as the hint, the text `adk run`'s console and `adk web`'s page
    show. Each call keeps the list it showed."""
    tool_context.request_confirmation(hint=question)
    tool_context.actions.skip_summarization = True  # the model does not see this reply as a result
    order['pending'] = order.get('pending', {}) | {call_of(tool_context): listed}
    return {'pending_confirmation': [item['code'] for item in listed]}
