"""Consumer regressions; real Runner/session with synthetic fault injection."""
import asyncio
import json
import traceback
import unicodedata

import pytest

from clinic_adk.catalog import Catalog
from clinic_adk.errors import SafeError
from clinic_adk.journey import Journey, State, build_agent
from clinic_adk import journey_gateway as gateway_module
from clinic_adk import runtime as runtime_module
from clinic_adk.runtime import Runtime
from test_journey import KEY, Spy, turn

CANARY = 'Pessoa Canario ZQX'


def assert_private(*values):
    raw = json.dumps(values, ensure_ascii=False, default=str)
    folded = ''.join(c for c in unicodedata.normalize('NFKD', raw.casefold()) if c.isalnum())
    assert CANARY not in raw
    assert 'pessoacanariozqx' not in folded


@pytest.mark.parametrize('arguments', [
    {'exam_names': [CANARY]}, {'exam_names': ['canario.zqx@example.invalid']},
    {'exam_names': ['Ｐｅｓｓｏａ Ｃａｎａｒｉｏ ＺＱＸ']}, {'exam_names': [CANARY + '\u200b']},
    {'exam_names': []}, {'exam_names': ['Hemograma completo'] * 21},
    {'exam_names': 'Hemograma completo'}, {'exam_names': ['Hemograma completo'], 'patient': CANARY}])
def test_runtime_rejects_untrusted_arguments_before_mcp_egress(arguments, monkeypatch):
    from google.adk.tools.mcp_tool import McpToolset
    constructors = []
    def forbidden(*args, **kwargs):
        constructors.append(True)
        raise AssertionError('MCP_CLIENT_MUST_NOT_BE_CREATED')
    monkeypatch.setattr(McpToolset, '__init__', forbidden)
    with pytest.raises(SafeError, match='EXAM_EVIDENCE_MISMATCH') as error:
        asyncio.run(runtime_module.mcp_call('rag', arguments))
    assert constructors == []
    assert_private(''.join(traceback.format_exception(error.value)))


@pytest.mark.parametrize('failure', [RuntimeError, TimeoutError, SafeError])
def test_journey_node_sanitizes_exception_before_real_adk_session(failure, monkeypatch, caplog, capsys):
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    async def run():
        journey = Journey(Spy(), request_id=KEY)
        async def broken(_):
            raise failure(CANARY)
        monkeypatch.setattr(journey, 'handle', broken)
        sessions = InMemorySessionService()
        await sessions.create_session(app_name='clinic_lab', user_id='fictional_demo', session_id=KEY)
        runner = Runner(app_name='clinic_lab', node=build_agent(journey, turn('search')),
                        session_service=sessions)
        outputs = []
        with pytest.raises(Exception) as error:
            async for event in runner.run_async(user_id='fictional_demo', session_id=KEY,
                    new_message=types.Content(role='user', parts=[types.Part(text='Pedido fictício.')])):
                outputs.append(event.model_dump(mode='json'))
        assert 'WORKFLOW_FAILED_SAFE' in ''.join(traceback.format_exception(error.value))
        session = await sessions.get_session(app_name='clinic_lab', user_id='fictional_demo', session_id=KEY)
        assert_private(outputs, session.model_dump(mode='json'), ''.join(traceback.format_exception(error.value)))
    asyncio.run(run())
    captured = capsys.readouterr()
    assert_private(captured.out, captured.err, caplog.text)


@pytest.mark.parametrize('field', ['name', 'code', 'evidence', 'extra'])
def test_journey_gateway_rejects_remote_pii_before_return(field, monkeypatch):
    value = Catalog().retrieve(['Hemograma completo'])
    value['exams'][0][field] = CANARY
    async def remote(*args, **kwargs):
        return value
    monkeypatch.setattr(gateway_module, 'mcp_call', remote)
    with pytest.raises(SafeError, match='EXAM_EVIDENCE_MISMATCH') as error:
        asyncio.run(gateway_module.ClinicGateway().lookup('Hemograma completo'))
    assert_private(''.join(traceback.format_exception(error.value)))


def test_journey_gateway_projects_independent_canonical_copy(monkeypatch):
    catalog = Catalog()
    value = catalog.retrieve(['Hemograma completo'])
    value['patient'] = CANARY
    async def remote(*args, **kwargs):
        return value
    monkeypatch.setattr(gateway_module, 'mcp_call', remote)
    clean = asyncio.run(gateway_module.ClinicGateway().lookup('Hemograma completo'))
    value['exams'][0]['name'] = CANARY
    expected = catalog.retrieve(['Hemograma completo'])
    assert clean == {key: expected[key] for key in ('ok', 'exams', 'unresolved_indices', 'catalog_version')}
    assert_private(clean)


def test_legacy_format_cannot_publish_unvalidated_canary():
    runtime = Runtime(request_id=KEY)
    runtime.stages = ['ocr', 'retrieve', 'validate', 'schedule']
    with pytest.raises(SafeError, match='JOURNEY_CONFIRMATION_REQUIRED') as error:
        asyncio.run(runtime.step('format', {'exams': [CANARY], 'receipt': CANARY}))
    assert_private(''.join(traceback.format_exception(error.value)))


def test_local_validation_projects_copy_and_rejects_explicit_stale_hash():
    runtime = Runtime(request_id=KEY)
    runtime.stages = ['ocr', 'retrieve']
    rows = runtime.catalog.retrieve(['Hemograma completo'])['exams']
    output = asyncio.run(runtime.step('validate', {'names': ['Hemograma completo'], 'exams': rows}))
    rows[0]['evidence'] = CANARY
    assert output['validated'] is True and output['catalog_version'] == runtime.catalog.version
    assert_private(output)
    runtime = Runtime(request_id=KEY)
    runtime.stages = ['ocr', 'retrieve']
    with pytest.raises(SafeError, match='RAG_INCOMPLETE_OR_STALE'):
        asyncio.run(runtime.step('validate', {'names': ['Hemograma completo'],
            'exams': runtime.catalog.retrieve(['Hemograma completo'])['exams'], 'catalog_version': '0' * 64}))


def test_journey_real_session_keeps_hostile_search_private():
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    async def run():
        spy = Spy()
        journey = Journey(spy, request_id=KEY)
        sessions = InMemorySessionService()
        await sessions.create_session(app_name='clinic_lab', user_id='fictional_demo', session_id=KEY)
        outputs = []
        runner = Runner(app_name='clinic_lab', node=build_agent(journey, turn('search', exam_name=CANARY)),
                        session_service=sessions)
        async for event in runner.run_async(user_id='fictional_demo', session_id=KEY,
                new_message=types.Content(role='user', parts=[types.Part(text='Pedido fictício.')])):
            outputs.append(event.model_dump(mode='json'))
        session = await sessions.get_session(app_name='clinic_lab', user_id='fictional_demo', session_id=KEY)
        assert journey.result()['state'] == State.CLARIFY and spy.calls == []
        assert_private(outputs, session.model_dump(mode='json'), journey.result())
    asyncio.run(run())
