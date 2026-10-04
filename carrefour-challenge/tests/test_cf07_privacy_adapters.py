"""Adapter tests only. They do not claim runtime/CLI consumers use these gates."""
import asyncio
import copy
import json
import traceback

import pytest
from clinic_adk.catalog import Catalog
from clinic_adk.errors import SafeError
from clinic_adk.privacy import query_safe
from clinic_adk.privacy_sinks import (artifact_acknowledgement, ocr_output,
                                    rag_output, private_mcp_output, public_failure_code,
                                    private_boundary)
from test_cf07_privacy_sinks import CANARIES, assert_private


@pytest.mark.parametrize('value', ['１２３．４５６．７８９－００', '１２３ ４５６ ７８９ ００',
                                 '（１１）９００００－１２３４', 'ｃａｎａｒｉｏ＠ｅｘａｍｐｌｅ．ｉｎｖａｌｉｄ'])
def test_query_checks_pii_after_unicode_normalization(value):
    with pytest.raises(SafeError) as error:
        query_safe(value)
    assert error.value.code == 'UNSAFE_EXAM_QUERY'
    assert value not in str(error.value)


@pytest.mark.parametrize('field', ['name', 'code', 'evidence', 'extra'])
def test_rag_gate_rejects_before_publishing(field):
    catalog = Catalog()
    value = catalog.retrieve(['Hemograma completo'])
    value['exams'][0][field] = CANARIES[0]
    with pytest.raises(SafeError) as error:
        rag_output(value, ['Hemograma completo'], catalog)
    assert error.value.code == 'EXAM_EVIDENCE_MISMATCH'
    assert_private(''.join(traceback.format_exception(error.value)))


def test_rag_gate_removes_metadata_and_returns_independent_canonical_copy():
    catalog = Catalog()
    value = catalog.retrieve(['Hemograma completo'])
    value['patient'] = CANARIES[0]
    clean = rag_output(value, ['Hemograma completo'], catalog)
    value['exams'][0]['evidence'] = CANARIES[1]
    assert_private(clean)
    assert clean['exams'][0]['code'] == 'FICT-001'
    assert clean['catalog_version'] == catalog.version


def test_ocr_gate_canonicalizes_alias_and_discards_remote_pii_metadata():
    catalog = Catalog()
    value = {'ok': True, 'pii_masked': True, 'unresolved_count': 0,
             'exam_names': ['hemograma'], 'patient': list(CANARIES),
             'redacted_lines': CANARIES[0]}
    clean = ocr_output(value, catalog)
    assert clean['exam_names'] == ['Hemograma completo']
    assert_private(clean)


@pytest.mark.parametrize('failure', [RuntimeError, TimeoutError, SafeError])
def test_adapter_converts_arbitrary_exception_message_and_code_to_fixed_failure(failure):
    async def invoke(*args):
        raise failure(CANARIES[0])
    with pytest.raises(SafeError) as error:
        asyncio.run(private_mcp_output('ocr', {'image_ref': 'request.png'}, invoke, Catalog()))
    assert error.value.code == 'WORKFLOW_FAILED_SAFE'
    assert_private(''.join(traceback.format_exception(error.value)))


def test_known_booking_uncertainty_survives_wrapping_without_message():
    nested = RuntimeError(CANARIES[0])
    nested.__cause__ = SafeError('APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY')
    assert public_failure_code(nested) == 'APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY'
    group = ExceptionGroup(CANARIES[1], [nested])
    assert public_failure_code(group) == 'APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY'


def test_boundary_preserves_cancellation():
    @private_boundary
    async def cancelled():
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled())


def test_artifact_ack_has_hash_without_filename():
    value = artifact_acknowledgement(b'fixed generated source')
    assert set(value) == {'ok', 'generated', 'sha256'}
    assert value['generated'] is True and len(value['sha256']) == 64
    assert_private(value)


@pytest.mark.parametrize('value', [CANARIES[0], CANARIES[7], 'Ｐｅｓｓｏａ Ｃａｎａｒｉｏ ＺＱＸ',
                                 '12345678900', 'PESSOA\u200b CANARIO ZQX'])
def test_sink_scanner_detects_literal_normalized_and_json_escaped_canaries(value):
    from tools.scan_cf07_sinks import scan
    assert not scan('api | ' + json.dumps({'message': value}))['pii_absent']
    assert scan('{"event":"sdk_diagnostic","level":"WARNING"}')['pii_absent']


def test_sink_scanner_detects_nested_json_unicode_escape():
    from tools.scan_cf07_sinks import scan
    event = json.dumps({'event': {'text': json.dumps({'name': CANARIES[7]})}})
    assert not scan(event)['pii_absent']
