"""CF07: reject before any callback; only detached, approved args may leave."""
import asyncio
import copy
import json
import traceback

import pytest
from clinic_adk.catalog import Catalog
from clinic_adk.errors import SafeError
from clinic_adk.privacy_sinks import private_mcp_output
from test_cf07_privacy_sinks import CANARIES, assert_private

PROVIDERS = [None, '', 'OCR', 'RAG', 'api', 'booking', CANARIES[0], 1, True, [], {}, b'ocr']
OCR_BAD = [None, [], 'request.png', {}, {'exam_names': ['Hemograma completo']},
           {'image_ref': 'request.png', 'patient_ref': 'FICT-PAT-0707'},
           {'image_ref': 'request.png', CANARIES[0]: CANARIES[1]},
           {'image_ref': 'request.png', 'headers': {'Authorization': CANARIES[0]}},
           {'image_ref': 'request.png', '_meta': {'patient': CANARIES[0]}},
           *({'image_ref': value} for value in (None, [], {}, 1, True, '', ' ',
              'x' * 97 + '.png', '../request.png', '/samples/request.png',
              'folder/request.png', 'folder\\request.png', 'https://example.invalid/x.png',
              'request.txt', 'request.png\n', 'request\x00.png', 'ｒｅｑｕｅｓｔ．ｐｎｇ', *CANARIES))]
RAG_BAD = [None, [], 'Hemograma completo', {}, {'image_ref': 'request.png'},
           {'exam_names': ['Hemograma completo'], 'patient_ref': 'FICT-PAT-0707'},
           {'exam_names': ['Hemograma completo'], CANARIES[0]: CANARIES[1]},
           {'exam_names': ['Hemograma completo'], 'request_id': CANARIES[0]},
           *({'exam_names': value} for value in (None, {}, '', 'Hemograma completo',
               (), [], [1], [True], [{}], [None], [''], [' '], ['x' * 121],
               ['Hemograma completo'] * 21, ['Exame inexistente'],
               ['Hemograma completo', 'Exame inexistente'], ['Hemograma completo\u200b'])),
           *({'exam_names': ['Hemograma completo', canary]} for canary in CANARIES)]


async def forbid(*args):
    raise AssertionError('INVOKE_MUST_NOT_RUN')


def assert_rejected(provider, arguments, code):
    calls = []
    async def invoke(*args):
        calls.append(args)
        return await forbid(*args)
    original = copy.deepcopy(arguments)
    with pytest.raises(SafeError) as error:
        asyncio.run(private_mcp_output(provider, arguments, invoke, Catalog()))
    assert error.value.code == code
    assert calls == []
    assert arguments == original
    text = ''.join(traceback.format_exception(error.value))
    assert_private(text)
    assert 'FICT-PAT-0707' not in text


@pytest.mark.parametrize('provider', PROVIDERS, ids=[f'provider-{i}' for i in range(len(PROVIDERS))])
def test_invalid_provider_precedes_callback(provider):
    assert_rejected(provider, {'image_ref': 'request.png'}, 'MCP_TOOL_MANIFEST_MISMATCH')


@pytest.mark.parametrize('arguments', OCR_BAD, ids=[f'ocr-{i}' for i in range(len(OCR_BAD))])
def test_invalid_ocr_args_have_zero_calls(arguments):
    assert_rejected('ocr', arguments, 'MCP_INVALID_TOOL_ARGUMENTS')


@pytest.mark.parametrize('arguments', RAG_BAD, ids=[f'rag-{i}' for i in range(len(RAG_BAD))])
def test_invalid_rag_args_have_zero_calls(arguments):
    assert_rejected('rag', arguments, 'MCP_INVALID_TOOL_ARGUMENTS')


@pytest.mark.parametrize('reference', ['request.png', 'request-variant.png', 'pii_as_exam.png',
                                     'injection.png', 'unknown.png', 'foo.JPG', 'x' * 96 + '.png'])
def test_valid_ocr_callback_only_receives_detached_reference(reference):
    catalog = Catalog()
    arguments, calls = {'image_ref': reference}, []
    async def invoke(provider, projected):
        calls.append((provider, projected.copy()))
        assert projected is not arguments
        projected['patient_ref'] = 'FICT-PAT-0707'
        return {'ok': True, 'pii_masked': True, 'unresolved_count': 0,
                'exam_names': ['Hemograma completo']}
    result = asyncio.run(private_mcp_output('ocr', arguments, invoke, catalog))
    assert calls == [('ocr', {'image_ref': reference})]
    assert arguments == {'image_ref': reference}
    assert_private(result)


def test_valid_rag_callback_receives_canonical_names_without_aliases_or_shared_lists():
    catalog = Catalog()
    arguments = {'exam_names': ['hemograma', 'Ｈｅｍｏｇｒａｍａ', 'Creatinina']}
    original, calls = copy.deepcopy(arguments), []
    async def invoke(provider, projected):
        calls.append((provider, copy.deepcopy(projected)))
        assert projected is not arguments and projected['exam_names'] is not arguments['exam_names']
        response = catalog.retrieve(projected['exam_names'])
        projected['exam_names'].append(CANARIES[0])
        return response
    result = asyncio.run(private_mcp_output('rag', arguments, invoke, catalog))
    assert calls == [('rag', {'exam_names': ['Hemograma completo', 'Creatinina']})]
    assert arguments == original
    assert [row['code'] for row in result['exams']] == ['FICT-001', 'FICT-005']
    assert_private(result, calls)


def test_callback_cannot_change_expected_rag_names_to_approve_another_exam():
    catalog = Catalog()
    arguments = {'exam_names': ['Hemograma completo']}
    async def invoke(provider, projected):
        projected['exam_names'][:] = ['Creatinina']
        return catalog.retrieve(projected['exam_names'])
    with pytest.raises(SafeError) as error:
        asyncio.run(private_mcp_output('rag', arguments, invoke, catalog))
    assert error.value.code == 'EXAM_EVIDENCE_MISMATCH'
    assert arguments == {'exam_names': ['Hemograma completo']}


def test_every_catalog_name_and_alias_remains_accepted_before_invoke():
    catalog = Catalog()
    calls = []
    async def invoke(provider, projected):
        calls.append(copy.deepcopy(projected))
        return catalog.retrieve(projected['exam_names'])
    async def run():
        for row in catalog.entries:
            for name in [row['name'], *row['aliases']]:
                result = await private_mcp_output('rag', {'exam_names': [name]}, invoke, catalog)
                assert calls[-1] == {'exam_names': [row['name']]}
                assert result['exams'][0]['code'] == row['code']
    asyncio.run(run())
    assert len(calls) == sum(1 + len(row['aliases']) for row in catalog.entries)
