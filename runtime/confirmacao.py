"""The [s/N] question for the exams in the middle band, asked in code, never by the model.

The booking callback asks for ADK's tool confirmation only when someone can answer; the CLI
receives the request from the runner, asks here and resumes the call with the answers.
"""
import os
import sys


def can_ask():
    """False with no interactive terminal, `cli run --yes` or CI: the middle band is then left out."""
    return not (os.environ.get('AGENT_NO_QUESTIONS') or os.environ.get('CI')) and sys.stdin.isatty() and sys.stdout.isatty()


def ask_person(items):
    """{code: True if accepted}, one question per exam; None when there is nobody to ask."""
    if not can_ask():
        return None
    answers = {}
    for item in items:
        read = ''.join(char for char in item['read'] if char.isprintable())  # OCR text: no terminal codes
        confidence = f'{item["confidence"]:.2f}'.replace('.', ',')
        try:
            answer = input(f'Li "{read}" → {item["name"]} {item["code"]} (confiança {confidence}). Incluir? [s/N] ')
        except EOFError:
            answer = ''
        answers[item['code']] = answer.strip().lower() in ('s', 'sim')
    return answers
