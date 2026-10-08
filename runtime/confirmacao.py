"""The final confirmation of the list, asked in code: before the API, the booking call pauses (ADK's tool
confirmation) with what the page holds besides the list, the whole list, each exam with its code and warning, the
ones not booked, and one question. `cli run` asks it in the terminal (ask_person), `adk run`'s console and `adk web`'s
page show the same text, and the same call resumes with the answer: only a yes to the list that call showed books.
"""
import os
import sys
from typing import Any

from .pedido import Item, OrderRecord
from .relatorio import SAID, confidence, left_out_line, printable, reading_lines


def can_ask() -> bool:
    """False with no interactive terminal or in CI: `cli run` then books nothing without --yes."""
    return not os.environ.get('CI') and sys.stdin.isatty() and sys.stdout.isatty()


def review(sure: list[Item], asked: list[Item], left_out: list[Item], order: OrderRecord | None = None) -> str:
    """The list and the question: once, above it, why the page is asked (its lines off the list) and what the OCR removed
    (`order`: its record); the exams to book (an asked one with what was read and why), then the ones not booked."""
    order = order or OrderRecord()
    read = order.ocr_read or []
    off = ', '.join(f'linha {at + 1} "{printable(read[at])[:80]}"' for at in (order.off_list or [])[:3] if 0 <= at < len(read))
    lines = [f'Atenção: o pedido tem texto além da lista de exames{": " * bool(off)}{off}; confira o papel'] * any(
        item.get('why') == 'page' for item in [*asked, *left_out])
    lines += reading_lines(order.view() | {'low_confidence': [], 'confirmed': []})[1:] + ['Exames para agendar:']
    lines += [f'- {item["name"]} ({item["code"]})' for item in sure]
    lines += [f'- {item["name"]} ({item["code"]}): lido "{printable(item["read"])}", confiança '
              f'{confidence(item["confidence"])}{SAID.get(item.get("why") or "", "")}; confira' for item in asked]
    lines += ['Não agendados:'] * bool(left_out) + [f'- {left_out_line(item, SAID)}' for item in left_out]
    count = len(sure) + len(asked)
    return '\n'.join(lines + ['Agendar este exame?' if count == 1 else f'Agendar estes {count} exames?'])


def ask_person(question: str) -> bool | None:
    """True only for "s" or "sim"; None when there is nobody to ask."""
    if not can_ask():
        return None
    try:
        answer = input(f'{question} [s/N] ')
    except EOFError:
        answer = ''
    return answer.strip().lower() in ('s', 'sim')


def call_of(tool_context: Any) -> str:
    return str(getattr(tool_context, 'function_call_id', None))


def pause_for_answer(tool_context: Any, order: OrderRecord, listed: list[Item], question: str) -> dict[str, list[str]]:
    """Pause the booking call with the list as the hint, the text `adk run`'s console and `adk web`'s page
    show. Each call keeps the list it showed."""
    tool_context.request_confirmation(hint=question)
    tool_context.actions.skip_summarization = True  # the model does not see this reply as a result
    order.pending = (order.pending or {}) | {call_of(tool_context): listed}
    return {'pending_confirmation': [item['code'] for item in listed]}
