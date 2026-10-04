"""Fixed local MCP/API ports for Journey, using the producer's public schemas.

The initial API lacks slots and confirmation. It is deliberately incompatible:
no adapter may turn its REQUESTED receipt into a confirmed reservation.
"""
import json
from datetime import datetime
from uuid import UUID

import httpx2

from . import contracts
from .errors import SafeError
from .journey import Booking, Receipt
from .runtime import API, mcp_call, unique_fields
from .catalog import Catalog
from .privacy_sinks import private_mcp_output


class ClinicGateway:
    async def lookup(self, exam_name):
        return await private_mcp_output('rag', {'exam_names': [exam_name]}, mcp_call, Catalog())

    async def slots(self, exam_code, preference):
        # The producer's public operation filters only by exam code. Apply a
        # requested time-of-day preference to its returned authoritative slots.
        value = await self._http('GET', '/slots', params={'exam_code': exam_code})
        if not isinstance(value, list) or len(value) > 100:
            raise SafeError('API_JOURNEY_INVALID_RESULT')
        if not value:
            return {'ok': True, 'slots': []}
        try:
            from .reservation_contracts import Slot as ProducerSlot
        except ImportError:
            raise SafeError('API_AVAILABILITY_CONTRACT_UNAVAILABLE') from None
        rows = [ProducerSlot.model_validate(row).model_dump() for row in value]
        if any(row['exam_code'] != exam_code for row in rows) or len({r['slot_id'] for r in rows}) != len(rows):
            raise SafeError('API_JOURNEY_INVALID_RESULT')
        value = [row for row in rows if row['available'] is True]
        if preference is None:
            return {'ok': True, 'slots': value}
        requested = preference.strip().casefold()
        periods = {'manha': (0, 12), 'manhã': (0, 12), 'morning': (0, 12),
                   'tarde': (12, 18), 'afternoon': (12, 18),
                   'noite': (18, 24), 'evening': (18, 24)}
        period = periods.get(requested)
        if period is None:
            return {'ok': True, 'slots': []}
        slots = [slot for slot in value if period[0] <=
                 datetime.fromisoformat(slot['starts_at'].replace('Z', '+00:00')).hour < period[1]]
        return {'ok': True, 'slots': slots}

    @staticmethod
    def _body(body):
        value = Booking.model_validate(body).model_dump(mode='json')
        if not {'slot_id', 'confirmed', 'patient_ref'}.issubset(contracts.AppointmentRequest.model_fields):
            raise SafeError('API_CONFIRMATION_CONTRACT_UNAVAILABLE')
        return contracts.AppointmentRequest.model_validate(value).model_dump(mode='json')

    @staticmethod
    def _receipt(value):
        try:
            # New producer versions keep the legacy REQUESTED parser separately.
            # Validation failure in the confirmed schema is never a fallback.
            schema = getattr(contracts, 'ConfirmedAppointmentReceipt', contracts.AppointmentReceipt)
            result = schema.model_validate(value).model_dump(mode='json')
            result = Receipt.model_validate(result).model_dump(mode='json')
        except (ValueError, TypeError):
            raise SafeError('API_UNCONFIRMED_RECEIPT')
        return result

    async def reserve(self, body):
        value = await self._http('POST', '/appointments', json=self._body(body))
        return self._receipt(value)

    async def reconcile(self, request_id):
        key = self._key(request_id)
        value = await self._http('GET', '/appointments/by-request/' + key, absent=True,
                                 headers={'Authorization': 'Bearer ' + key})
        return None if value is None else self._receipt(value)

    async def cancel(self, appointment_id, request_id, cancel_request_id):
        if not hasattr(contracts, 'CancelRequest'):
            raise SafeError('API_CANCELLATION_CONTRACT_UNAVAILABLE')
        key = self._key(request_id)
        cancel_key = self._key(cancel_request_id)
        return self._receipt(await self._http(
            'POST', '/appointments/' + self._key(appointment_id) + '/cancel',
            headers={'Authorization': 'Bearer ' + key},
            json=contracts.CancelRequest(request_id=cancel_key, confirmed=True).model_dump(mode='json')))

    @staticmethod
    def _key(value):
        try:
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise ValueError()
        except (TypeError, ValueError):
            raise SafeError('INVALID_REQUEST_ID') from None
        return value

    async def _http(self, method, path, absent=False, **kwargs):
        async with httpx2.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            async with client.stream(method, API + path, **kwargs) as response:
                if response.status_code == 404 and absent:
                    return None
                if response.status_code == 404 and method == 'GET' and path == '/slots':
                    raise SafeError('API_AVAILABILITY_CONTRACT_UNAVAILABLE')
                if response.status_code not in (200, 201):
                    raise SafeError('API_JOURNEY_UNAVAILABLE_OR_UNKNOWN')
                if (response.headers.get('content-encoding', 'identity').lower() != 'identity'
                        or response.headers.get('content-type', '').split(';')[0] != 'application/json'):
                    raise SafeError('API_JOURNEY_INVALID_RESULT')
                raw = bytearray()
                async for chunk in response.aiter_raw():
                    if len(raw) + len(chunk) > 16000:
                        raise SafeError('API_JOURNEY_RESULT_LIMIT')
                    raw.extend(chunk)
                try:
                    return json.loads(raw, object_pairs_hook=unique_fields,
                                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, TypeError, RecursionError):
                    raise SafeError('API_JOURNEY_INVALID_RESULT') from None
