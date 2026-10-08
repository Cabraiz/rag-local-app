"""The [s/N] question through ADK's native tool confirmation, end to end, without Gemini.

The agent transpiled from each example spec runs in ADK's real runner, through the CLI's
run_agent, with a scripted model and the three tools replaced by local functions that answer
like the OCR, the catalog search and the API. The booking call asks for confirmation, the run
pauses, the CLI asks [s/N] off the event loop and resumes the same call with the answers.
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
from tests.test_transpiler import app_of
from transpiler import load_spec, transpile

ROOT = Path(__file__).resolve().parents[1]
# "- GA": a handwritten IGF-1 that the search takes as IgA (0.80, the question band).
ORDER = ['Pedido de exames', '- GA', '- Creatinina']
HITS = {'IgA': {'code': 'FICT-079', 'name': 'IgA', 'score': 1.0},
        'Creatinina': {'code': 'FICT-005', 'name': 'Creatinina', 'score': 1.0}}
CALLS = {'extract_exam_text': [], 'search_exams': [], 'create_appointment': []}


def extract_exam_text(filename: str) -> dict:
    """Reads the order (stand-in for the OCR server)."""
    CALLS['extract_exam_text'].append(filename)
    return {'structuredContent': {'lines': ORDER, 'line_confidence': [96.0] * len(ORDER),
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
    root_agent = transpile(ROOT / 'specs' / spec_file, tmp_path / 'agent.py')
    stand_ins = {'extract': extract_exam_text, 'search': search_exams, 'schedule': create_appointment}
    for agent in root_agent.sub_agents:
        agent.model, agent.tools = Scripted(**({'bookings': bookings} if bookings else {})), [
            FunctionTool(stand_ins[agent.name])]
    root_agent.sub_agents[2].before_tool_callback.__self__.can_ask = lambda: someone_answers
    return root_agent


@pytest.fixture(autouse=True)
def fresh_calls():
    for calls in CALLS.values():
        calls.clear()


def run(root_agent, spec_file):
    found = cli.new_found()
    asyncio.run(cli.run_agent(app_of(root_agent), 'pedido.png', load_spec(ROOT / 'specs' / spec_file), found))
    return found


@pytest.mark.parametrize('answer, posted, reason', [
    ('s', ['FICT-005', 'FICT-079'], None),
    ('n', ['FICT-005'], 'declined'),
])
def test_the_run_pauses_asks_and_resumes_the_same_booking_call(tmp_path, monkeypatch, answer, posted, reason):
    asked = []

    def person(items):  # what confirmacao.ask_person returns after the [s/N] answers
        asked.extend(item['code'] for item in items)
        return {item['code']: answer == 's' for item in items}

    monkeypatch.setattr(confirmacao, 'ask_person', person)
    found = run(scripted_agent('agent.json', tmp_path, someone_answers=True), 'agent.json')
    assert asked == ['FICT-079']
    assert CALLS['create_appointment'] == [posted]  # one POST, after the answer
    # the pause did not run the earlier steps again: one OCR read, one search per exam, with the spec's top_k
    assert CALLS['extract_exam_text'] == ['pedido.png'] and CALLS['search_exams'] == [('IgA', 3), ('Creatinina', 3)]
    assert found['appointment']['id'] == 'a1'
    assert [(item['code'], item['reason']) for item in found['low_confidence']] == ([('FICT-079', reason)] if reason else [])
    assert [item['code'] for item in found['confirmed']] == (['FICT-079'] if answer == 's' else [])


def test_without_a_terminal_nothing_is_asked_and_the_middle_band_is_left_out(tmp_path, monkeypatch):
    def must_not_ask(items):
        raise AssertionError('asked without a terminal')

    monkeypatch.setattr(confirmacao, 'ask_person', must_not_ask)
    found = run(scripted_agent('agent.json', tmp_path, someone_answers=False), 'agent.json')
    assert CALLS['create_appointment'] == [['FICT-005']]
    assert [(item['code'], item['reason']) for item in found['low_confidence']] == [('FICT-079', 'needs_confirmation')]


def test_the_second_example_spec_runs_without_the_question(tmp_path, monkeypatch):
    def must_not_ask(items):
        raise AssertionError('the spec has no question band')

    monkeypatch.setattr(confirmacao, 'ask_person', must_not_ask)
    spec = 'agent-sem-confirmacao.json'
    found = run(scripted_agent(spec, tmp_path, someone_answers=True), spec)
    assert CALLS['create_appointment'] == [['FICT-005']] and found['appointment']['id'] == 'a1'
    assert [(item['code'], item['reason']) for item in found['low_confidence']] == [('FICT-079', 'score')]


def test_two_booking_calls_in_one_turn_post_once_even_if_only_one_asks(tmp_path, monkeypatch):
    # The model sends two create_appointment calls at once: one needs the question (IgA at 0.80), the
    # other does not. The one that does not ask books; the one resumed after the answer gets that same
    # appointment back, so the API sees one POST.
    monkeypatch.setattr(confirmacao, 'ask_person', lambda items: {item['code']: True for item in items})
    bookings = [[{'code': 'FICT-079', 'name': 'IgA'}, {'code': 'FICT-005', 'name': 'Creatinina'}],
                [{'code': 'FICT-005', 'name': 'Creatinina'}]]
    found = run(scripted_agent('agent.json', tmp_path, someone_answers=True, bookings=bookings), 'agent.json')
    assert CALLS['create_appointment'] == [['FICT-005']]
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
    asked = []
    monkeypatch.setattr(confirmacao, 'ask_person', lambda items: asked.extend(item['code'] for item in items)
                        or {item['code']: True for item in items})
    root_agent = scripted_agent('agent.json', tmp_path, someone_answers=True)
    schedule = root_agent.sub_agents[2]
    schedule.model = FallbackModel(models=[Overloaded(), schedule.model], retriable_status_codes=frozenset({429, 503}))
    found = run(root_agent, 'agent.json')
    assert asked == ['FICT-079'] and CALLS['create_appointment'] == [['FICT-005', 'FICT-079']]
    assert found['appointment']['id'] == 'a1' and not found.get('model_error')
