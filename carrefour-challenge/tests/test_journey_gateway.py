"""Producer-schema compatibility and bounded HTTP spies, not live API evidence."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from clinic_adk import contracts
from clinic_adk.errors import SafeError
from clinic_adk.journey import Booking, Receipt
from clinic_adk import journey_gateway as module

BODY = {'request_id': '00000000-0000-4000-8000-000000000003',
        'exam_codes': ['FICT-001'], 'catalog_version': 'a' * 64,
        'slot_id': '00000000-0000-5000-8000-000000000001', 'patient_ref': 'FICT-PAT-0001', 'confirmed': True}
RECEIPT = {**{k:v for k,v in BODY.items() if k not in ('patient_ref','confirmed')},
           'starts_at': '2030-01-03T10:00:00-03:00',
           'appointment_id': '00000000-0000-4000-8000-000000000006', 'status': 'CONFIRMED'}


@pytest.mark.parametrize('status', ['CONFIRMED', 'REQUESTED'])
def test_distinct_confirmed_schema_is_selected_without_legacy_fallback(monkeypatch, status):
    legacy_calls = []
    class Legacy:
        @classmethod
        def model_validate(cls, value):
            legacy_calls.append(value)
            raise AssertionError('LEGACY_SCHEMA_MUST_NOT_PARSE_CONFIRMED_ROUTE')
    monkeypatch.setattr(contracts, 'AppointmentReceipt', Legacy)
    monkeypatch.setattr(contracts, 'ConfirmedAppointmentReceipt', Receipt, raising=False)
    value = {**RECEIPT, 'status': status}
    if status == 'CONFIRMED':
        assert module.ClinicGateway._receipt(value) == value
    else:
        with pytest.raises(SafeError, match='API_UNCONFIRMED_RECEIPT'):
            module.ClinicGateway._receipt(value)
    assert legacy_calls == []


def test_initial_producer_contract_cannot_authorize_post(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('POST_WITHOUT_PRODUCER_CONSENT_SCHEMA')
    monkeypatch.setattr(module.ClinicGateway, '_http', forbidden)
    with pytest.raises(SafeError, match='API_CONFIRMATION_CONTRACT_UNAVAILABLE'):
        asyncio.run(module.ClinicGateway().reserve(BODY))


def test_upgraded_producer_schema_is_used_before_post(monkeypatch):
    monkeypatch.setattr(contracts, 'AppointmentRequest', Booking)
    monkeypatch.setattr(contracts, 'AppointmentReceipt', Receipt)
    calls = []
    async def send(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return RECEIPT
    monkeypatch.setattr(module.ClinicGateway, '_http', send)
    assert asyncio.run(module.ClinicGateway().reserve(BODY)) == RECEIPT
    assert calls == [('POST', '/appointments', {'json': BODY})]


def test_legacy_requested_receipt_never_promoted_to_confirmed():
    requested = {k: v for k, v in RECEIPT.items() if k not in ('slot_id', 'confirmed')}
    requested['status'] = 'REQUESTED'
    with pytest.raises(SafeError, match='API_UNCONFIRMED_RECEIPT'):
        module.ClinicGateway._receipt(requested)


def test_no_guessed_cancel_endpoint_is_invoked(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('UNDECLARED_MUTATION')
    monkeypatch.setattr(module.ClinicGateway, '_http', forbidden)
    with pytest.raises(SafeError, match='API_CANCELLATION_CONTRACT_UNAVAILABLE'):
        asyncio.run(module.ClinicGateway().cancel(RECEIPT['appointment_id'], BODY['request_id'], BODY['request_id']))


class Response:
    def __init__(self, status=200, chunks=None, headers=None):
        self.status_code = status
        self.headers = headers or {'content-type': 'application/json'}
        self.chunks = chunks or [b'[]']
        self.reads = 0
        self.closed = False

    async def aiter_raw(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk


def client(monkeypatch, response):
    calls = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        @asynccontextmanager
        async def stream(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            try:
                yield response
            finally:
                response.closed = True
    def create(**kwargs):
        assert kwargs == {'timeout': 5, 'trust_env': False, 'follow_redirects': False}
        return Client()
    monkeypatch.setattr(module.httpx2, 'AsyncClient', create)
    return calls


@pytest.mark.parametrize('status', [202, 204, 301, 404, 409, 500, 503])
def test_unexpected_status_does_not_read_or_log_remote_body(status, monkeypatch):
    response = Response(status=status, chunks=[b'private unbounded text' * 10000])
    client(monkeypatch, response)
    code = 'API_AVAILABILITY_CONTRACT_UNAVAILABLE' if status == 404 else 'API_JOURNEY_UNAVAILABLE_OR_UNKNOWN'
    with pytest.raises(SafeError, match=code):
        asyncio.run(module.ClinicGateway().slots('FICT-001', None))
    assert response.reads == 0 and response.closed


@pytest.mark.parametrize('headers', [
    {'content-type': 'text/plain'},
    {'content-type': 'application/json', 'content-encoding': 'gzip'}])
def test_encoded_or_untyped_body_rejected_before_read(headers, monkeypatch):
    response = Response(headers=headers)
    client(monkeypatch, response)
    with pytest.raises(SafeError, match='API_JOURNEY_INVALID_RESULT'):
        asyncio.run(module.ClinicGateway().slots('FICT-001', None))
    assert response.reads == 0 and response.closed


@pytest.mark.parametrize('raw', [b'{"ok":true,"ok":false}', b'{"ok":NaN}', b'{broken'])
def test_ambiguous_api_json_is_rejected(raw, monkeypatch):
    response = Response(chunks=[raw])
    client(monkeypatch, response)
    with pytest.raises(SafeError, match='API_JOURNEY_INVALID_RESULT'):
        asyncio.run(module.ClinicGateway().slots('FICT-001', None))
    assert response.closed


def test_oversized_body_stops_before_third_chunk(monkeypatch):
    response = Response(chunks=[b'x' * 16000, b'x', b'x' * 100000])
    client(monkeypatch, response)
    with pytest.raises(SafeError, match='API_JOURNEY_RESULT_LIMIT'):
        asyncio.run(module.ClinicGateway().slots('FICT-001', None))
    assert response.reads == 2 and response.closed


def test_absent_reconciliation_is_not_a_receipt(monkeypatch):
    response = Response(status=404)
    calls = client(monkeypatch, response)
    assert asyncio.run(module.ClinicGateway().reconcile(BODY['request_id'])) is None
    assert calls[0][1] == module.API + '/appointments/by-request/' + BODY['request_id']
    assert response.reads == 0 and response.closed


def test_slots_are_from_fixed_local_api(monkeypatch):
    response = Response()
    calls = client(monkeypatch, response)
    assert asyncio.run(module.ClinicGateway().slots('FICT-001', 'tarde')) == {'ok': True, 'slots': []}
    assert calls == [('GET', module.API + '/slots', {'params': {'exam_code': 'FICT-001'}})]


@pytest.mark.parametrize('key', ['not-a-uuid', '', None, {}, 1])
def test_invalid_key_cannot_leak_untrusted_value_or_call_api(key, monkeypatch):
    response = Response()
    calls = client(monkeypatch, response)
    with pytest.raises(SafeError, match='^INVALID_REQUEST_ID$'):
        asyncio.run(module.ClinicGateway().reconcile(key))
    assert calls == []
