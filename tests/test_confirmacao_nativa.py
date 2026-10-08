"""The final confirmation of the list through ADK's native tool confirmation, end to end, without Gemini.

The agent transpiled from each example spec runs in ADK's real runner, through the CLI's
run_agent, with a scripted model and the three tools replaced by local functions that answer
like the OCR, the catalog search and the API. The booking call asks for confirmation with the
whole list, the run pauses, the CLI asks "Agendar estes N exames? [s/N]" off the event loop and
resumes the same call with the answer.
"""
import asyncio
import re
from pathlib import Path

import pytest
from google.adk.models import FallbackModel
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import FunctionTool
from google.genai import errors as genai_errors
from google.genai import types

import cli
from runtime import confirmacao
from runtime.pedido import OrderRecord
from runtime.plugin import BookingPlugin
from transpiler import load_root_agent, load_spec, transpile

ROOT = Path(__file__).resolve().parents[1]
# "- GA": a handwritten IGF-1 that the search takes as IgA (0.80, the question band).
ORDER = ['Pedido de exames', '- GA', '- Creatinina']
HITS = {'IgA': {'code': 'FICT-079', 'name': 'IgA', 'score': 1.0},
        'Creatinina': {'code': 'FICT-005', 'name': 'Creatinina', 'score': 1.0}}
CALLS = {'extract_exam_text': [], 'search_exams': [], 'create_appointment': []}


def extract_exam_text(filename: str) -> dict:
    """Reads the order (stand-in for the OCR server)."""
    CALLS['extract_exam_text'].append(filename)
    return {'structuredContent': {'version': 1, 'lines': ORDER, 'line_confidence': [96.0] * len(ORDER),
                                  'line_intent': ['request'] * len(ORDER), 'contested_exams': [], 'page_clean': True, 'pii_masked': {}}}


def search_exams(query: str, top_k: int = 3) -> dict:
    """Searches the catalog (stand-in for the RAG server)."""
    CALLS['search_exams'].append((query, top_k))
    return {'structuredContent': {'result': [HITS[query]]}}


def create_appointment(exams: list[dict]) -> dict:
    """Books the exams (stand-in for POST /appointments)."""
    CALLS['create_appointment'].append([exam['code'] for exam in exams])
    return {'id': 'a1', 'status': 'scheduled', 'exams': exams}


class Scripted(BaseLlm):
    """Calls its agent's tool the way the instructions ask, then answers with text."""
    model: str = 'scripted'
    bookings: list = [[{'code': 'FICT-079', 'name': 'IgA'}, {'code': 'FICT-005', 'name': 'Creatinina'}]]

    async def generate_content_async(self, llm_request, stream=False):
        last = llm_request.contents[-1] if llm_request.contents else None
        tools = list(llm_request.tools_dict or {})
        if not tools or (last and any(part.function_response for part in last.parts or [])):
            yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text='pronto')]))
            return
        told = re.search(r'Arquivo do pedido: ([\w.-]+)', llm_request.model_dump_json())  # the token, not the file
        calls = {'extract_exam_text': [{'filename': told[1] if told else ''}],
                 'search_exams': [{'query': 'IgA'}, {'query': 'Creatinina'}],  # in parallel, like Gemini
                 'create_appointment': [{'exams': exams} for exams in self.bookings]}[tools[0]]
        parts = [types.Part(function_call=types.FunctionCall(name=tools[0], args=args)) for args in calls]
        yield LlmResponse(content=types.Content(role='model', parts=parts))


def scripted_agent(spec_file, tmp_path, someone_answers, bookings=None):
    """The generated app, each model scripted and each tool replaced by its stand-in."""
    transpile(ROOT / 'specs' / spec_file, tmp_path / 'agent.py')
    app = load_root_agent(tmp_path / 'agent.py', name='app')
    stand_ins = {'extract': extract_exam_text, 'search': search_exams, 'schedule': create_appointment}
    for agent in app.root_agent.sub_agents:
        agent.model, agent.tools = Scripted(**({'bookings': bookings} if bookings else {})), [
            FunctionTool(stand_ins[agent.name])]
    BookingPlugin.of(app).can_ask = lambda: someone_answers
    return app


@pytest.fixture(autouse=True)
def fresh_calls():
    for calls in CALLS.values():
        calls.clear()


def run(app, spec_file, **found):
    """cli run's run_agent; questions=True: someone confirms the list, False: --yes (the rules alone)."""
    found = cli.new_found() | found
    asyncio.run(cli.run_agent(app, 'pedido.png', load_spec(ROOT / 'specs' / spec_file), found))
    return found


def person(answer, asked):
    """confirmacao.ask_person after the final question: True for "s", None with no terminal."""
    def answers(question):
        asked.append(question)
        return answer
    return answers


LIST = ('Exames para agendar:\n- Creatinina (FICT-005)\n- IgA (FICT-079): lido "- GA", confiança 0,80; confira\n'
        'Agendar estes 2 exames?')


@pytest.mark.parametrize('answer', [True, False])
def test_the_run_pauses_shows_the_whole_list_and_only_a_yes_books_it(tmp_path, monkeypatch, answer):
    asked: list = []
    monkeypatch.setattr(confirmacao, 'ask_person', person(answer, asked))
    found = run(scripted_agent('agent.json', tmp_path, someone_answers=True), 'agent.json', questions=True)
    assert asked == [LIST]  # one question, with every exam, its code and its warning
    assert CALLS['create_appointment'] == ([['FICT-005', 'FICT-079']] if answer else [])  # one POST, or none
    # the pause did not run the earlier steps again: one OCR read, one search per exam, with the spec's top_k
    assert CALLS['extract_exam_text'] == ['pedido.png'] and CALLS['search_exams'] == [('IgA', 3), ('Creatinina', 3)]
    assert (found['appointment'] or {}).get('id') == ('a1' if answer else None)
    assert [item['code'] for item in found['confirmed']] == (['FICT-079'] if answer else [])
    assert found['blocked'] == (None if answer else 'você não confirmou a lista de exames')


@pytest.mark.parametrize('typed, books', [('s', True), ('sim', True), ('S ', True), ('n', False), ('', False),
                                          ('yes', False), ('agendar', False)])
def test_only_s_or_sim_confirms_the_list_and_the_default_is_no(monkeypatch, capsys, typed, books):
    monkeypatch.setattr(confirmacao, 'can_ask', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt: print(prompt, end='') or typed)
    assert confirmacao.ask_person(LIST) is books
    assert capsys.readouterr().out == LIST + ' [s/N] '


def test_without_a_terminal_and_without_yes_nothing_is_booked_and_the_cli_says_how(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(confirmacao, 'can_ask', lambda: False)  # docker compose run -T, a pipe, CI
    found = run(scripted_agent('agent.json', tmp_path, someone_answers=True), 'agent.json', questions=True)
    assert CALLS['create_appointment'] == [] and found['appointment'] is None and found['no_terminal']
    assert LIST in capsys.readouterr().out  # the list is still shown
    assert cli.booking_problem(found, load_spec(ROOT / 'specs' / 'agent.json')) == (
        'agendamento bloqueado antes de chamar a API: sem terminal para confirmar a lista de exames: rode num terminal '
        'ou com --yes; nada foi agendado')


def test_with_yes_nothing_is_asked_and_only_the_clean_exams_are_booked(tmp_path, monkeypatch):
    def must_not_ask(question):
        raise AssertionError('asked with --yes')

    monkeypatch.setattr(confirmacao, 'ask_person', must_not_ask)
    found = run(scripted_agent('agent.json', tmp_path, someone_answers=True), 'agent.json', questions=False)
    assert CALLS['create_appointment'] == [['FICT-005']]
    assert [(item['code'], item['reason']) for item in found['low_confidence']] == [('FICT-079', 'needs_confirmation')]


def test_the_second_example_spec_asks_only_for_the_list(tmp_path, monkeypatch):
    asked: list = []
    monkeypatch.setattr(confirmacao, 'ask_person', person(True, asked))
    spec = 'agent-sem-confirmacao.json'
    found = run(scripted_agent(spec, tmp_path, someone_answers=True), spec, questions=True)
    assert asked == ["Exames para agendar:\n- Creatinina (FICT-005)\nNão agendados:\n- baixa confiança: '- GA' → IgA "
                     'FICT-079 (confiança 0,80); confira o pedido\nAgendar este exame?']
    assert CALLS['create_appointment'] == [['FICT-005']] and found['appointment']['id'] == 'a1'
    assert [(item['code'], item['reason']) for item in found['low_confidence']] == [('FICT-079', 'score')]


def test_two_booking_calls_in_one_turn_post_once(tmp_path, monkeypatch):
    # The model sends two create_appointment calls at once: each shows its own list. The first yes books;
    # the call resumed after it gets that same appointment back, so the API sees one POST.
    asked: list = []
    monkeypatch.setattr(confirmacao, 'ask_person', person(True, asked))
    bookings = [[{'code': 'FICT-079', 'name': 'IgA'}, {'code': 'FICT-005', 'name': 'Creatinina'}],
                [{'code': 'FICT-005', 'name': 'Creatinina'}]]
    found = run(scripted_agent('agent.json', tmp_path, someone_answers=True, bookings=bookings), 'agent.json')
    assert len(asked) == 2 and CALLS['create_appointment'] == [['FICT-005', 'FICT-079']]
    assert found['appointment']['id'] == 'a1'


class Overloaded(BaseLlm):
    """The main Gemini model, overloaded on every request (503)."""
    model: str = 'gemini-principal'

    async def generate_content_async(self, llm_request, stream=False):
        raise genai_errors.ServerError(503, {'error': {'code': 503, 'message': 'high demand', 'status': 'UNAVAILABLE'}})
        yield  # an async generator, as every model is


def test_the_reserve_answers_per_request_and_nothing_is_asked_or_booked_twice(tmp_path, monkeypatch):
    # The booking step's main model is overloaded: each of its requests goes to the reserve (ADK's
    # FallbackModel, as the generated file builds it), also the one after the [s/N] answer. The run goes
    # on: the question is asked once and the API gets one POST, whatever model proposes the booking.
    asked: list = []
    monkeypatch.setattr(confirmacao, 'ask_person', person(True, asked))
    app = scripted_agent('agent.json', tmp_path, someone_answers=True)
    schedule = app.root_agent.sub_agents[2]
    schedule.model = FallbackModel(models=[Overloaded(), schedule.model], retriable_status_codes=frozenset({429, 503}))
    found = run(app, 'agent.json')
    assert asked == [LIST] and CALLS['create_appointment'] == [['FICT-005', 'FICT-079']]
    assert found['appointment']['id'] == 'a1' and not found.get('model_error')


def test_the_guides_sample_question_is_the_one_the_cli_asks():
    sure = [{'code': 'FICT-001', 'name': 'Hemograma completo'}, {'code': 'FICT-002', 'name': 'Glicemia de jejum'},
            {'code': 'FICT-005', 'name': 'Creatinina'}]  # pedido.png, read clearly; its OCR removes 3 pieces
    question = confirmacao.review(sure, [], [], OrderRecord(text_removed=3, ocr_read=[], off_list=[]))
    assert question + ' [s/N] s' in (ROOT / 'docs' / 'como-rodar.md').read_text('utf-8')


def test_the_page_reason_comes_once_above_the_list_with_its_line_and_what_the_ocr_removed():
    # A typed note above the list: each exam is asked, and the question says why once, pointing to the line.
    order = OrderRecord(ocr_read=['[TEXTO_REMOVIDO]: [TEXTO_REMOVIDO], [TEXTO_REMOVIDO]', 'Paciente: [NOME]', '- TSH',
                                  '- T4 livre'], off_list=[0], text_removed=3, instructions_removed=1)
    asked = [{'code': code, 'name': name, 'read': f'- {name}', 'confidence': 0.89, 'why': 'page'}
             for code, name in (('FICT-024', 'TSH'), ('FICT-025', 'T4 livre'))]
    assert confirmacao.review([], asked, [], order) == '\n'.join([
        'Atenção: o pedido tem texto além da lista de exames: linha 1 "[TEXTO_REMOVIDO]: [TEXTO_REMOVIDO], '
        '[TEXTO_REMOVIDO]"; confira o papel', 'Trechos removidos pelo OCR (não pareciam exame): 3',
        'Instruções neutralizadas no OCR: 1', 'Exames para agendar:', '- TSH (FICT-024): lido "- TSH", confiança 0,89; '
        'confira', '- T4 livre (FICT-025): lido "- T4 livre", confiança 0,89; confira', 'Agendar estes 2 exames?'])
