"""The generated agent under `adk web` with requests that overlap: two messages at once in one session,
two sessions at once, and restarts of the agent or of the API. One order per session holds: the second
message of a session is refused before it runs anything, two sessions never share a record, key or answer,
and an order sent again after a restart (a session ADK kept on disk) is refused, not booked again.
Scripted models (no Gemini), the real OCR, RAG and API servers (tests/test_adk_run.py).
"""
import asyncio
import importlib
import json
import re
import shutil
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.models.llm_response import LlmResponse
from google.genai import types

import runtime.pedido as pedido
import tests.test_adk_run as adk
from runtime import confirmacao
from runtime.callbacks import mcp_payload
from tests.test_adk_run import agent_folder  # noqa: F401  (fixtures)
from tests.test_adk_seguranca import MEDIUM, SURE, last_text, new_session, web, web_app  # noqa: F401
from tests.test_alucinacao import IMAGE, NAMED, appointment, services, stored_ids  # noqa: F401

pytestmark = [
    pytest.mark.skipif(shutil.which('tesseract') is None, reason='Tesseract runs inside the Docker image'),
    pytest.mark.xdist_group('spec-ports'),
    pytest.mark.filterwarnings(r'ignore:\[EXPERIMENTAL\]:UserWarning'),
    pytest.mark.filterwarnings('ignore::DeprecationWarning'),
]

HONEST = adk.Scripted
OTHER = 'pedido-manuscrito.png'
MID = threading.Event()  # set when a run is mid-way (its search step started)


def run(client, session, parts, **extra):
    response = client.post('/run', json={'appName': 'generated', 'userId': 'pessoa', 'sessionId': session,
                                         'newMessage': {'role': 'user', 'parts': parts}, **extra})
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, []


def calls(events, name=None):
    names = [part['functionCall']['name'] for event in events if isinstance(event, dict)
             for part in (event.get('content') or {}).get('parts', []) if part.get('functionCall')]
    return names if name is None else [n for n in names if n == name]


def text_of(events):
    return last_text([event for event in events if isinstance(event, dict)])


def together(*jobs):
    """Run the jobs on threads released at the same instant; their results in order."""
    barrier, out = threading.Barrier(len(jobs)), [None] * len(jobs)

    def go(index, job):
        barrier.wait()
        out[index] = job()
    threads = [threading.Thread(target=go, args=(index, job)) for index, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    return out


class Slow(HONEST):
    """Honest, but the search step takes a second (and says when it started): a run stays mid-way."""

    async def generate_content_async(self, llm_request, stream=False):
        tool = next(iter(llm_request.tools_dict or {}), None)
        if tool == 'search_exams' and not adk.replies(llm_request):
            MID.set()
            await asyncio.sleep(1.0)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


def instruction_list(request):
    text = str(request.config.system_instruction or '')
    return text.split('Exames:\n', 1)[1] if 'Exames:\n' in text else ''


class Reader(HONEST):
    """An honest model that works from what it is given, not from a fixed script: it lists the lines the
    OCR returned, searches the names its instruction lists and books the codes its instruction lists."""

    async def generate_content_async(self, llm_request, stream=False):
        adk.SEEN.append(llm_request.model_dump_json())
        tool, answered = next(iter(llm_request.tools_dict or {}), None), adk.replies(llm_request)
        if tool == 'extract_exam_text' and not answered:
            out = [(tool, {'filename': adk.file_named_in(llm_request)})]
        elif tool == 'extract_exam_text':
            reply = mcp_payload(answered[0]) or {}
            out = '\n'.join(line for line, kind in zip(reply.get('lines', []), reply.get('line_intent', []), strict=False)
                            if kind == 'request' and '[' not in line) or 'nenhum'
        elif tool == 'search_exams' and not answered:
            names = [line.strip() for line in instruction_list(llm_request).splitlines() if line.strip()]
            out = [(tool, {'query': name}) for name in names[:8]]
        elif tool == 'search_exams':
            best = [((mcp_payload(reply) or [{}]) or [{}])[0] for reply in answered]
            out = json.dumps([{'code': hit['code'], 'name': hit['name']} for hit in best if hit.get('code')],
                             ensure_ascii=False)
        elif tool == 'create_appointment' and not answered:
            listed = instruction_list(llm_request)
            match = re.search(r'\[.*\]', listed, re.S)
            out = [(tool, {'exams': json.loads(match[0]) if match else []})]
        else:
            out = 'Pronto.'
        parts = ([types.Part(text=out)] if isinstance(out, str) else
                 [types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in out])
        yield LlmResponse(content=types.Content(role='model', parts=parts))


def codes(stored):
    return sorted(code for code, _ in stored)


def confirmation_request(events):
    for event in events:
        for part in (event.get('content') or {}).get('parts', []):
            call = part.get('functionCall') or {}
            if call.get('name') == 'adk_request_confirmation':
                return event, call
    return None, None


def answer(call, confirmed=True, payload=None):
    response = {'confirmed': confirmed} | ({'payload': payload} if payload is not None else {})
    return [{'functionResponse': {'id': call['id'], 'name': 'adk_request_confirmation', 'response': response}}]


# --- (1) two requests at once in the SAME session -----------------------------------------------------

def test_the_same_message_twice_at_once_books_once_and_runs_each_tool_once(web, services, monkeypatch):  # noqa: F811
    before = stored_ids(services)
    with TestClient(web_app(web), raise_server_exceptions=False) as client:
        session = new_session(client)
        (s1, e1), (s2, e2) = together(lambda: run(client, session, [{'text': NAMED}]),
                                      lambda: run(client, session, [{'text': NAMED}]))
        after = run(client, session, [{'text': NAMED}])
    new = [appointment(i) for i in stored_ids(services) if i not in before]
    assert len(new) == 1 and 'já foi criado: não repita' in text_of(after[1])
    assert any('Esta sessão já tratou um pedido' in text_of(events) for events in (e1, e2))  # the 2nd: refused at once
    assert len(calls(e1 + e2, 'extract_exam_text')) == 1, 'the OCR ran in both requests'
    assert len(calls(e1 + e2, 'create_appointment')) == 1, 'the booking tool ran in both requests'


def test_the_same_message_resent_while_the_first_is_mid_run_is_refused(web, services, monkeypatch):  # noqa: F811
    monkeypatch.setattr(adk, 'Scripted', Slow)
    MID.clear()
    before = stored_ids(services)
    with TestClient(web_app(web), raise_server_exceptions=False) as client:
        session = new_session(client)
        first = {}
        thread = threading.Thread(target=lambda: first.update(r=run(client, session, [{'text': NAMED}])))
        thread.start()
        assert MID.wait(60)
        s2, e2 = run(client, session, [{'text': NAMED}])
        thread.join(120)
        s1, e1 = first['r']
    new = [appointment(i) for i in stored_ids(services) if i not in before]
    assert len(new) == 1
    assert calls(e2) == [], 'the second request ran tools while the first was mid-run'
    assert text_of(e2), 'no answer to the second request'


def test_two_different_orders_at_once_in_one_session_book_one_coherent_order(web, services, monkeypatch):  # noqa: F811
    monkeypatch.setattr(adk, 'Scripted', Reader)
    with TestClient(web_app(web), raise_server_exceptions=False) as client:
        alone = {}
        for image in (IMAGE, OTHER):  # what each order books on its own session
            mark = stored_ids(services)
            run(client, new_session(client), [{'text': image}])
            alone[image] = [codes(appointment(i)) for i in stored_ids(services) if i not in mark]
        mark = stored_ids(services)
        session = new_session(client)
        (s1, e1), (s2, e2) = together(lambda: run(client, session, [{'text': IMAGE}]),
                                      lambda: run(client, session, [{'text': OTHER}]))
    new = [codes(appointment(i)) for i in stored_ids(services) if i not in mark]
    assert alone[IMAGE] != alone[OTHER], 'the two orders must differ for this test to mean anything'
    assert len(new) <= 1 and (not new or new[0] in (alone[IMAGE][0], alone[OTHER][0])), 'a mixed order was booked'


# --- (2) two sessions at once, different orders -----------------------------------------------------------

def test_two_sessions_at_once_each_book_their_own_order_with_their_own_key(web, services, monkeypatch):  # noqa: F811
    monkeypatch.setattr(adk, 'Scripted', Reader)
    with TestClient(web_app(web), raise_server_exceptions=False) as client:
        alone = {}
        for image in (IMAGE, OTHER):
            mark = stored_ids(services)
            run(client, new_session(client), [{'text': image}])
            alone[image] = [codes(appointment(i)) for i in stored_ids(services) if i not in mark]
        for _ in range(3):
            a, b = new_session(client), new_session(client)
            together(lambda a=a: run(client, a, [{'text': IMAGE}]), lambda b=b: run(client, b, [{'text': OTHER}]))
            state = {s: client.get(f'/apps/generated/users/pessoa/sessions/{s}').json()['state'] for s in (a, b)}
            booked = {s: state[s].get('booked_appointment') or {} for s in (a, b)}
            assert codes((e['code'], 0) for e in booked[a].get('exams', [])) == alone[IMAGE][0]
            assert codes((e['code'], 0) for e in booked[b].get('exams', [])) == alone[OTHER][0]
            assert booked[a]['id'] != booked[b]['id']
            assert state[a]['idempotency_key'] != state[b]['idempotency_key']
            assert state[a].get('ocr_lines') != state[b].get('ocr_lines')


def test_a_yes_in_one_session_is_never_the_other_sessions_yes(web, services, monkeypatch):  # noqa: F811
    monkeypatch.setattr(confirmacao, 'can_ask', lambda: True)
    monkeypatch.setitem(adk.BOOK, 'exams', SURE + MEDIUM)
    before = stored_ids(services)
    with TestClient(web_app(web), raise_server_exceptions=False) as client:
        a, b = new_session(client), new_session(client)
        (_, ea), (_, eb) = together(lambda: run(client, a, [{'text': NAMED}]), lambda: run(client, b, [{'text': NAMED}]))
        (eva, ca), (evb, cb) = confirmation_request(ea), confirmation_request(eb)
        assert ca and cb
        (_, ra), (_, rb) = together(
            lambda: run(client, a, answer(ca, True), invocationId=eva['invocationId']),
            lambda: run(client, b, answer(cb, False), invocationId=evb['invocationId']))
        state = {s: client.get(f'/apps/generated/users/pessoa/sessions/{s}').json()['state'] for s in (a, b)}
    new = sorted(codes(appointment(i)) for i in stored_ids(services) if i not in before)
    assert new == [['FICT-001', 'FICT-002'], ['FICT-001', 'FICT-002', 'FICT-067']]
    assert codes((e['code'], 0) for e in state[a]['booked_appointment']['exams']) == ['FICT-001', 'FICT-002', 'FICT-067']
    assert codes((e['code'], 0) for e in state[b]['booked_appointment']['exams']) == ['FICT-001', 'FICT-002']


# --- (3) restarts ------------------------------------------------------------------------------------

def test_the_same_key_after_an_api_restart_returns_the_same_appointment(services):  # noqa: F811
    import api.main
    body, headers = {'exams': [{'code': 'FICT-001', 'name': 'Hemograma completo'}]}, {'Idempotency-Key': 'reinicio-' + str(time.time())}
    with TestClient(importlib.reload(api.main).app, base_url='http://api:8000') as first:
        one = first.post('/appointments', json=body, headers=headers)
    with TestClient(importlib.reload(api.main).app, base_url='http://api:8000') as second:  # a new process' app
        two = second.post('/appointments', json=body, headers=headers)
    assert one.status_code in (200, 201) and two.json()['id'] == one.json()['id']


def restart_adk(folder, uri, monkeypatch):
    """A new `adk web` process on the same session store: new modules (new records), a new secret."""
    for name in [name for name in sys.modules if name == 'generated' or name.startswith('generated.')]:
        del sys.modules[name]
    monkeypatch.setattr(pedido, 'SECRET', __import__('secrets').token_bytes(32))
    return adk_web_app(folder, uri)


def adk_web_app(folder, uri):
    if uri is None:  # ADK's default: per-agent local SQLite under the agent's .adk/
        return get_fast_api_app(agents_dir=str(folder), web=False, artifact_service_uri='memory://',
                                memory_service_uri='memory://', use_local_storage=True)
    return get_fast_api_app(agents_dir=str(folder), web=False, session_service_uri=uri,
                            artifact_service_uri='memory://', memory_service_uri='memory://', use_local_storage=False)


@pytest.mark.parametrize('store', ['local', 'memory'])
def test_an_order_resent_after_an_adk_web_restart_is_not_booked_again(web, services, monkeypatch, tmp_path, store):  # noqa: F811
    # sqlite stands for ADK's default local storage (generated/.adk/, `adk web` without
    # --no_use_local_storage): the session outlives the process. memory: the documented command.
    uri = None if store == 'local' else 'memory://'
    before = stored_ids(services)
    first_app = adk_web_app(web, uri)
    with TestClient(first_app, raise_server_exceptions=False) as client:
        session = new_session(client)
        _, booked = run(client, session, [{'text': NAMED}])
    assert 'Agendamento confirmado pela API' in text_of(booked)
    with TestClient(restart_adk(web, uri, monkeypatch), raise_server_exceptions=False) as client:
        status, again = run(client, session, [{'text': NAMED}])
    new = [i for i in stored_ids(services) if i not in before]
    assert len(new) == 1, f'{len(new)} appointments for one order after a restart ({store})'
