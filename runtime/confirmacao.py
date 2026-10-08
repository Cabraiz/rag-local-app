"""The question for the exams in the middle band, asked in code: the booking call pauses (ADK's tool
confirmation), `cli run` asks one [s/N] per exam (ask_person), `adk run` and `adk web` answer once for
the call, and the same call resumes with the answers (answers_given).
"""
import os
import sys

# Why an exam is asked although it is written clearly: what its line says (runtime/confianca.py, 'why').
WHY = {'uncertain': '; o pedido tem outras palavras além do exame', 'table': '; o pedido está em tabela ou colunas',
       'instruction': '; o pedido tem uma instrução sobre este exame', 'page': '; o pedido tem texto além da lista de exames'}


def can_ask():
    """False with no interactive terminal or in CI: the middle band is then left out (`cli run --yes`
    says so in the order's record, runtime/pedido.py)."""
    return not os.environ.get('CI') and sys.stdin.isatty() and sys.stdout.isatty()


def described(item):
    """"'<line read>' → <exam> <code> (confiança 0,80)", the OCR text without terminal codes."""
    read = ''.join(char for char in item['read'] if char.isprintable())
    confidence = f'{item["confidence"]:.2f}'.replace('.', ',')
    return read, f'{item["name"]} {item["code"]} (confiança {confidence})'


def ask_person(items):
    """{code: True if accepted}, one question per exam; None when there is nobody to ask."""
    if not can_ask():
        return None
    answers = {}
    for item in items:
        read, exam = described(item)
        try:
            answer = input(f'Li "{read}" → {exam}{WHY.get(item.get("why"), "")}. Incluir? [s/N] ')
        except EOFError:
            answer = ''
        answers[item['code']] = answer.strip().lower() in ('s', 'sim')
    return answers


def call_of(tool_context):
    return str(getattr(tool_context, 'function_call_id', None))


def pause_for_answer(tool_context, order, to_ask):
    """Pause the booking call for the person's answer. The hint lists what is asked, for a client that
    shows only the hint (`adk run`'s console, `adk web`). Each call keeps its own question."""
    exams = '; '.join("'{}' → {}".format(*described(item)) for item in to_ask)
    tool_context.request_confirmation(
        hint=f'Confirme os exames lidos com confiança média: {exams}. Confirmar inclui todos; recusar deixa todos de fora.',
        payload={'perguntas': to_ask})
    tool_context.actions.skip_summarization = True  # the model does not see this reply as a result
    order['pending'] = order.get('pending', {}) | {call_of(tool_context): to_ask}
    return {'pending_confirmation': [item['code'] for item in to_ask]}


def answers_given(order, confirmation, asked):
    """{code: yes} of the run. `asked`: the codes this call asked about. Answers are given once per
    run: a repeated call never asks again."""
    answers = dict(order.get('answers', {}))
    if confirmation is None:
        return answers
    payload = confirmation.payload if isinstance(confirmation.payload, dict) else {}
    per_exam = payload.get('respostas')
    if isinstance(per_exam, dict):
        return answers | {str(code): bool(yes) for code, yes in per_exam.items() if str(code) in asked}
    return answers | {code: bool(confirmation.confirmed) for code in asked}
