"""Read the actual CF06 schema snapshot; prove helper limits, not API integration."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import traceback

import pytest
from pydantic import ValidationError
from clinic_adk.catalog import Catalog
from clinic_adk.errors import SafeError
from clinic_adk.privacy_sinks import ocr_output, rag_output, private_boundary, public_failure_code
from test_cf07_privacy_sinks import CANARIES, assert_private

PATIENT_REF = 'FICT-PAT-0707'


@pytest.fixture(scope='module')
def cf06():
    # Required artifact provided by the scoped runner; missing is a failure, not SKIP.
    path = Path(os.environ['CF07_CF06_CONTRACTS_PATH'])
    spec = importlib.util.spec_from_file_location('cf06_schema_snapshot', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request_body():
    return {'request_id': '00000000-0000-4000-8000-000000000707',
            'exam_codes': ['FICT-001'], 'catalog_version': Catalog().version,
            'slot_id': '00000000-0000-4000-8000-000000000709',
            'patient_ref': PATIENT_REF, 'confirmed': True}


def test_cf06_request_preserves_required_fictional_ref(cf06):
    body = request_body()
    value = cf06.AppointmentRequest.model_validate(body).model_dump()
    assert value == body
    assert value['patient_ref'] == PATIENT_REF


def test_cf06_ref_is_required_not_silently_discarded(cf06):
    body = request_body()
    del body['patient_ref']
    with pytest.raises(ValidationError):
        cf06.AppointmentRequest.model_validate(body)


@pytest.mark.parametrize('canary', CANARIES)
def test_cf06_personal_values_are_not_patient_refs(cf06, canary):
    body = {**request_body(), 'patient_ref': canary}
    with pytest.raises(ValidationError) as error:
        cf06.AppointmentRequest.model_validate(body)
    assert public_failure_code(error.value) == 'WORKFLOW_FAILED_SAFE'
    assert_private(public_failure_code(error.value))


def test_cf06_receipt_preserves_minimum_fields_without_patient_ref(cf06):
    body = request_body()
    receipt = {key: body[key] for key in ('request_id', 'exam_codes', 'catalog_version', 'slot_id')}
    receipt.update(appointment_id='00000000-0000-4000-8000-000000000708',
                   starts_at='2099-01-01T08:00:00Z', status='CONFIRMED')
    assert cf06.AppointmentReceipt.model_validate(receipt).model_dump() == receipt
    assert PATIENT_REF not in json.dumps(receipt)
    with pytest.raises(ValidationError):
        cf06.AppointmentReceipt.model_validate({**receipt, 'patient_ref': PATIENT_REF})


@pytest.mark.parametrize('provider', ['ocr', 'rag'])
def test_helpers_never_publish_patient_metadata(provider):
    catalog = Catalog()
    if provider == 'ocr':
        payload = {'ok': True, 'pii_masked': True, 'unresolved_count': 0,
                   'exam_names': ['Hemograma completo'], 'patient_ref': PATIENT_REF}
        clean = ocr_output(payload, catalog)
    else:
        payload = catalog.retrieve(['Hemograma completo']) | {'patient_ref': PATIENT_REF}
        clean = rag_output(payload, ['Hemograma completo'], catalog)
    assert 'patient_ref' not in clean and PATIENT_REF not in json.dumps(clean)
    assert_private(clean)


@pytest.mark.parametrize('failure', [RuntimeError, TimeoutError, SafeError])
def test_private_boundary_does_not_expose_fictional_ref_in_trace(failure):
    @private_boundary
    async def operation():
        raise failure(PATIENT_REF)
    with pytest.raises(SafeError) as error:
        asyncio.run(operation())
    text = ''.join(traceback.format_exception(error.value))
    assert error.value.code == 'WORKFLOW_FAILED_SAFE'
    assert PATIENT_REF not in text
