"""Deterministic consent boundary for ADK; no model can select or invoke a tool.

The gateway is a typed port owned by the API/MCP integration cards. Its outputs
are untrusted. Checkpoints are private application state, never user input.
"""
import asyncio
import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .catalog import Catalog
from .contracts import CanonicalUUID, ExamCode
from .errors import SafeError
from .privacy import query_safe
from .privacy_sinks import private_boundary


class State(StrEnum):
    UNDERSTAND = 'entender'
    LOCATE = 'localizar_exame'
    CLARIFY = 'esclarecer'
    SLOTS = 'consultar_horario'
    CONFIRM = 'confirmar'
    UNKNOWN = 'resultado_incerto'
    DONE = 'concluir'
    CANCELLED = 'cancelar'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Turn(Strict):
    turn_id: CanonicalUUID
    action: Literal['search', 'choose', 'confirm', 'decline', 'cancel', 'resume']
    exam_name: str | None = Field(default=None, max_length=120)
    preference: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_ :+.à-ÿ-]{1,80}$')
    slot_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    offer_token: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    consent: bool = False

    @field_validator('turn_id')
    @classmethod
    def uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('canonical UUID required')
        return value

    @model_validator(mode='after')
    def action_fields(self):
        allowed = {'search': {'exam_name', 'preference'}, 'choose': {'slot_id'},
                   'confirm': {'offer_token', 'consent'}, 'decline': set(),
                   'cancel': set(), 'resume': set()}[self.action]
        if self.model_fields_set - {'turn_id', 'action'} - allowed:
            raise ValueError('fields do not belong to action')
        return self


class Exam(Strict):
    code: ExamCode
    name: str = Field(min_length=1, max_length=120)
    evidence: str = Field(min_length=1, max_length=2000)


class Slot(Strict):
    slot_id: CanonicalUUID
    exam_code: ExamCode
    starts_at: str = Field(min_length=20, max_length=40)
    available: Literal[True]

    @field_validator('available', mode='before')
    @classmethod
    def true_bool(cls, value):
        if value is not True:
            raise ValueError('explicit availability required')
        return value

    @field_validator('starts_at')
    @classmethod
    def timestamp(cls, value):
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError('timezone required')
        return value


class Booking(Strict):
    request_id: CanonicalUUID
    exam_codes: list[ExamCode] = Field(min_length=1, max_length=1)
    catalog_version: str = Field(pattern=r'^[a-f0-9]{64}$')
    slot_id: CanonicalUUID
    patient_ref: str = Field(pattern=r'^FICT-PAT-[0-9]{4}$')
    confirmed: Literal[True]

    @field_validator('confirmed', mode='before')
    @classmethod
    def true_bool(cls, value):
        if value is not True:
            raise ValueError('explicit consent required')
        return value

    @field_validator('request_id')
    @classmethod
    def uuid4_key(cls, value):
        if UUID(value).version != 4:
            raise ValueError('random canonical UUIDv4 required')
        return value


class Receipt(Strict):
    request_id: CanonicalUUID
    exam_codes: list[ExamCode] = Field(min_length=1, max_length=1)
    catalog_version: str = Field(pattern=r'^[a-f0-9]{64}$')
    slot_id: CanonicalUUID
    appointment_id: CanonicalUUID
    starts_at: str = Field(min_length=20, max_length=40)
    status: Literal['CONFIRMED', 'CANCELLED']

    @field_validator('starts_at')
    @classmethod
    def timestamp(cls, value):
        if datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset() is None:
            raise ValueError('timezone required')
        return value


class Gateway(Protocol):
    async def lookup(self, exam_name: str) -> dict: ...
    async def slots(self, exam_code: str, preference: str | None) -> dict: ...
    async def reserve(self, body: dict) -> dict: ...
    async def reconcile(self, request_id: str) -> dict | None: ...
    async def cancel(self, appointment_id: str, request_id: str,
                     cancel_request_id: str) -> dict: ...


class Checkpoint(Strict):
    version: Literal[1] = 1
    request_id: CanonicalUUID
    state: str = State.UNDERSTAND.value
    code: str = 'REQUEST_EXAM'
    exam: Exam | None = None
    slots: list[Slot] = Field(default_factory=list, max_length=100)
    selected: Slot | None = None
    offer_token: str | None = None
    revision: int = Field(default=0, ge=0, le=10000)
    pending: Booking | None = None
    receipt: Receipt | None = None
    cancel_pending: bool = False
    cancel_request_id: CanonicalUUID | None = None
    turns: dict[str, str] = Field(default_factory=dict, max_length=256)
    transitions: list[str] = Field(default_factory=lambda: [State.UNDERSTAND.value], max_length=1000)


class Journey:
    # Application code supplies implementations; no turn controls method names.
    TOOL_ALLOWLIST = frozenset({'lookup', 'slots', 'reserve', 'reconcile', 'cancel'})

    def __init__(self, gateway: Gateway, catalog=None, request_id=None, checkpoint=None):
        self.gateway = gateway
        self.catalog = catalog if catalog is not None else Catalog()
        self.lock = asyncio.Lock()
        try:
            self.data = (Checkpoint.model_validate_json(checkpoint) if checkpoint is not None
                         else Checkpoint(request_id=str(uuid4()) if request_id is None else request_id))
            if str(UUID(self.data.request_id)) != self.data.request_id:
                raise ValueError()
            if UUID(self.data.request_id).version != 4:
                raise ValueError()
            state = State(self.data.state)
            if (not self.data.transitions or self.data.transitions[-1] != state.value
                    or any(s not in State._value2member_map_ for s in self.data.transitions)):
                raise ValueError()
            if self.data.exam is not None:
                self._exam(self.data.exam.model_dump())
            if self.data.slots and (self.data.exam is None
                    or any(s.exam_code != self.data.exam.code for s in self.data.slots)
                    or len({s.slot_id for s in self.data.slots}) != len(self.data.slots)):
                raise ValueError()
            if self.data.selected is not None and (self.data.selected not in self.data.slots
                    or self.data.offer_token != self._token(self.data.selected)):
                raise ValueError()
            if self.data.pending is not None:
                if (self.data.pending.request_id != self.data.request_id
                        or self.data.pending.catalog_version != self.catalog.version
                        or self.data.exam is None
                        or self.data.pending.exam_codes != [self.data.exam.code]
                        or self.data.selected is None
                        or self.data.pending.slot_id != self.data.selected.slot_id):
                    raise ValueError()
            if state in (State.UNKNOWN, State.DONE) and self.data.pending is None:
                raise ValueError()
            if self.data.receipt is not None:
                self._receipt(self.data.receipt.model_dump(), self.data.receipt.status)
            if state == State.DONE and (self.data.receipt is None or self.data.receipt.status != 'CONFIRMED'):
                raise ValueError()
            if state == State.CONFIRM and (self.data.selected is None or self.data.offer_token is None):
                raise ValueError()
        except (ValueError, TypeError, SafeError):
            raise SafeError('JOURNEY_INVALID_CHECKPOINT') from None

    def checkpoint(self):
        return self.data.model_dump_json()

    def result(self):
        d = self.data
        return {'fictional': True, 'state': d.state, 'code': d.code, 'request_id': d.request_id,
                'exam': d.exam.model_dump() if d.exam else None,
                'slots': [s.model_dump() for s in d.slots],
                'offer_token': d.offer_token,
                'selected': d.selected.model_dump() if d.selected else None,
                'receipt': d.receipt.model_dump() if d.receipt else None,
                'transitions': list(d.transitions), 'model_calls': 0}

    def _move(self, state, code):
        self.data.state, self.data.code = state.value, code
        if self.data.transitions[-1] != state.value:
            self.data.transitions.append(state.value)

    async def _tool(self, name, *args):
        if name not in self.TOOL_ALLOWLIST:
            raise SafeError('JOURNEY_TOOL_DENIED')
        # A fixed bounded wait; errors never include remote/user content.
        async with asyncio.timeout(12):
            return await getattr(self.gateway, name)(*args)

    def _exam(self, value):
        exam = Exam.model_validate(value)
        canonical = self.catalog.by_code.get(exam.code)
        if not canonical or any(getattr(exam, key) != canonical[key] for key in ('name', 'evidence')):
            raise SafeError('JOURNEY_EXAM_EVIDENCE_MISMATCH')
        return exam

    def _receipt(self, value, status='CONFIRMED'):
        receipt = Receipt.model_validate(value)
        if self.data.pending is None or any(
                getattr(receipt, k) != getattr(self.data.pending, k)
                for k in ('request_id', 'exam_codes', 'catalog_version', 'slot_id')):
            raise SafeError('JOURNEY_RECEIPT_MISMATCH')
        if receipt.status != status:
            raise SafeError('JOURNEY_RECEIPT_STATUS')
        if (self.data.selected is None or datetime.fromisoformat(receipt.starts_at.replace('Z', '+00:00'))
                != datetime.fromisoformat(self.data.selected.starts_at.replace('Z', '+00:00'))):
            raise SafeError('JOURNEY_RECEIPT_MISMATCH')
        if self.data.receipt and receipt.appointment_id != self.data.receipt.appointment_id:
            raise SafeError('JOURNEY_RECEIPT_MISMATCH')
        return receipt

    async def handle(self, payload):
        try:
            turn = Turn.model_validate(payload)
        except (ValueError, TypeError):
            raise SafeError('JOURNEY_INVALID_TURN') from None
        digest = hashlib.sha256(turn.model_dump_json().encode()).hexdigest()
        async with self.lock:
            d = self.data
            if turn.turn_id in d.turns:
                if d.turns[turn.turn_id] != digest:
                    raise SafeError('JOURNEY_TURN_CONFLICT')
                return self.result()
            if len(d.turns) >= 256 or len(d.transitions) >= 990:
                raise SafeError('JOURNEY_SESSION_LIMIT')
            # Record before awaiting a tool. Interrupted commits cannot repeat on replay.
            d.turns[turn.turn_id] = digest
            state = State(d.state)
            if state == State.CANCELLED:
                return self.result()
            if turn.action == 'cancel':
                d.cancel_pending = True
                if d.pending:
                    if d.cancel_request_id is None:
                        d.cancel_request_id = str(uuid4())
                    await self._resume()
                else:
                    self._clear_offer()
                    self._move(State.CANCELLED, 'DIALOGUE_CANCELLED_NO_RESERVATION')
            elif turn.action == 'resume':
                if d.pending:
                    await self._resume()
            elif state in (State.UNKNOWN, State.DONE):
                d.code = 'RECONCILE_REQUIRED' if state == State.UNKNOWN else 'ALREADY_CONFIRMED'
            elif turn.action == 'search':
                await self._search(turn)
            elif turn.action == 'choose':
                self._choose(turn)
            elif turn.action == 'decline':
                self._clear_offer()
                self._move(State.CLARIFY, 'CONFIRMATION_DECLINED')
            elif turn.action == 'confirm':
                await self._confirm(turn)
            return self.result()

    def _clear_offer(self):
        self.data.slots = []
        self.data.selected = None
        self.data.offer_token = None
        self.data.revision += 1

    async def _search(self, turn):
        d = self.data
        self._clear_offer()
        d.exam = None
        self._move(State.LOCATE, 'LOOKUP_PENDING')
        try:
            # Exact supported names/aliases only; symptoms cannot select an exam.
            name = query_safe(turn.exam_name)
            canonical = self.catalog.by_name.get(name)
            if canonical is None:
                self._move(State.CLARIFY, 'EXAM_NOT_FOUND_OR_AMBIGUOUS')
                return
            value = await self._tool('lookup', canonical['name'])
            if (not isinstance(value, dict) or value.get('ok') is not True
                    or value.get('catalog_version') != self.catalog.version
                    or value.get('unresolved_indices') != []
                    or not isinstance(value.get('exams'), list) or len(value['exams']) != 1):
                raise SafeError('JOURNEY_LOOKUP_UNRESOLVED')
            exam = self._exam(value['exams'][0])
            if exam.code != canonical['code']:
                raise SafeError('JOURNEY_EXAM_EVIDENCE_MISMATCH')
            d.exam = exam
            self._move(State.SLOTS, 'AVAILABILITY_PENDING')
            value = await self._tool('slots', exam.code, turn.preference)
            if (not isinstance(value, dict) or set(value) != {'ok', 'slots'}
                    or value['ok'] is not True or not isinstance(value['slots'], list)
                    or len(value['slots']) > 100):
                raise SafeError('JOURNEY_INVALID_SLOTS')
            slots = [Slot.model_validate(s) for s in value['slots']]
            if any(s.exam_code != exam.code for s in slots) or len({s.slot_id for s in slots}) != len(slots):
                raise SafeError('JOURNEY_INVALID_SLOTS')
            d.slots = slots
            if slots:
                d.code = 'SELECT_SLOT'
            else:
                self._move(State.CLARIFY, 'NO_AVAILABILITY')
        except asyncio.CancelledError:
            self._move(State.CLARIFY, 'LOOKUP_INTERRUPTED')
            raise
        except SafeError as error:
            # Keep the producer capability failure distinct from a tool outage.
            # Only this fixed local reason is safe to expose, never remote text.
            code = ('API_AVAILABILITY_CONTRACT_UNAVAILABLE'
                    if error.code == 'API_AVAILABILITY_CONTRACT_UNAVAILABLE'
                    else 'LOOKUP_OR_AVAILABILITY_UNAVAILABLE')
            self._move(State.CLARIFY, code)
        except Exception:
            self._move(State.CLARIFY, 'LOOKUP_OR_AVAILABILITY_UNAVAILABLE')

    def _choose(self, turn):
        d = self.data
        if State(d.state) not in (State.SLOTS, State.CONFIRM) or d.exam is None:
            d.code = 'SEARCH_REQUIRED'
            return
        slot = next((s for s in d.slots if s.slot_id == turn.slot_id), None)
        if slot is None:
            d.code = 'SLOT_NOT_OFFERED'
            return
        d.selected = slot
        d.revision += 1
        d.offer_token = self._token(slot)
        self._move(State.CONFIRM, 'EXPLICIT_CONFIRMATION_REQUIRED')

    def _token(self, slot):
        d = self.data
        material = [d.request_id, d.exam.code, slot.model_dump(), d.revision, self.catalog.version]
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    async def _confirm(self, turn):
        d = self.data
        if (State(d.state) != State.CONFIRM or d.exam is None or d.selected is None
                or turn.consent is not True or turn.offer_token != d.offer_token):
            d.code = 'EXPLICIT_CONFIRMATION_REQUIRED'
            return
        d.pending = Booking(request_id=d.request_id, exam_codes=[d.exam.code],
                            catalog_version=self.catalog.version, slot_id=d.selected.slot_id,
                            patient_ref='FICT-PAT-0001', confirmed=True)
        self._move(State.UNKNOWN, 'RESERVATION_OUTCOME_UNKNOWN')
        try:
            value = await self._tool('reserve', d.pending.model_dump())
            d.receipt = self._receipt(value)
            self._move(State.DONE, 'RESERVATION_CONFIRMED')
        except asyncio.CancelledError:
            raise
        except Exception:
            # Includes rejection/invalid receipt: never conclude or allocate a new key.
            d.code = 'RESERVATION_OUTCOME_UNKNOWN'

    async def _resume(self):
        d = self.data
        self._move(State.UNKNOWN, 'RECONCILIATION_PENDING')
        try:
            value = await self._tool('reconcile', d.request_id)
            if value is None:
                if d.cancel_pending or d.receipt is not None:
                    # A negative read cannot rule out an in-flight commit. Keep unknown.
                    d.code = 'CANCELLATION_OUTCOME_UNKNOWN' if d.cancel_pending else 'RESERVATION_OUTCOME_UNKNOWN'
                    return
                value = await self._tool('reserve', d.pending.model_dump())
            if isinstance(value, dict) and value.get('status') == 'CANCELLED':
                d.receipt = self._receipt(value, 'CANCELLED')
                self._move(State.CANCELLED, 'RESERVATION_CANCELLED')
                return
            d.receipt = self._receipt(value)
            if d.cancel_pending:
                if d.cancel_request_id is None:
                    d.cancel_request_id = str(uuid4())
                value = await self._tool('cancel', d.receipt.appointment_id,
                                         d.request_id, d.cancel_request_id)
                d.receipt = self._receipt(value, 'CANCELLED')
                self._move(State.CANCELLED, 'RESERVATION_CANCELLED')
            else:
                self._move(State.DONE, 'RESERVATION_CONFIRMED')
        except asyncio.CancelledError:
            raise
        except Exception:
            d.code = 'CANCELLATION_OUTCOME_UNKNOWN' if d.cancel_pending else 'RESERVATION_OUTCOME_UNKNOWN'


def build_agent(journey, turn):
    """One turn per real ADK Workflow; the UI supplies structured consent.

    Reuse the Journey or restore its trusted checkpoint between turns. User,
    OCR, catalog and model text cannot become Python or a tool name.
    """
    from google.adk import Workflow
    from google.adk.workflow import START

    @private_boundary
    async def dialogue(node_input):
        return {'result': await journey.handle(turn)}

    return Workflow(name='clinic_dialogue', edges=[(START, dialogue)], max_concurrency=1, timeout=30)
