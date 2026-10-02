from typing import Annotated, Literal
from uuid import UUID
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator

UUID_PATTERN = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
CanonicalUUID = Annotated[str, Field(pattern=UUID_PATTERN, json_schema_extra={'format':'uuid'})]
ExamCode = Annotated[str, Field(pattern=r'^FICT-[0-9]{3}$', examples=['FICT-001'])]

class AppointmentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_id: CanonicalUUID
    exam_codes: list[ExamCode] = Field(min_length=1, max_length=20, json_schema_extra={'uniqueItems':True})
    catalog_version: str = Field(pattern=r'^[a-f0-9]{64}$')
    @field_validator('request_id')
    @classmethod
    def valid_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('canonical UUID required')
        return value
    @field_validator('exam_codes')
    @classmethod
    def distinct(cls, value):
        if len(set(value)) != len(value) or any(not re.fullmatch(r'FICT-[0-9]{3}', code) for code in value):
            raise ValueError('unique fictional code format required')
        return sorted(value)

class AppointmentReceipt(AppointmentRequest):
    appointment_id: CanonicalUUID
    status: Literal['REQUESTED']
    @field_validator('appointment_id')
    @classmethod
    def valid_appointment_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError('canonical UUID required')
        return value

class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    field: str
    type: str

class ValidationFailure(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    code: Literal['INVALID_REQUEST']
    errors: list[ValidationIssue]

class APIError(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    detail: Literal['CATALOG_VERSION_MISMATCH', 'IDEMPOTENCY_CONFLICT', 'NOT_FOUND', 'LEDGER_UNAVAILABLE_RETRY_SAME_KEY']

class EnvelopeError(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    code: Literal['INVALID_JSON_ENVELOPE', 'REQUEST_BODY_TIMEOUT', 'REQUEST_SIZE_LIMIT', 'JSON_REQUIRED']

class CatalogError(APIError):
    detail: Literal['CATALOG_VERSION_MISMATCH']

class ConflictError(APIError):
    detail: Literal['IDEMPOTENCY_CONFLICT']

class NotFoundError(APIError):
    detail: Literal['NOT_FOUND']

class LedgerError(APIError):
    detail: Literal['LEDGER_UNAVAILABLE_RETRY_SAME_KEY']

class InvalidEnvelope(EnvelopeError):
    code: Literal['INVALID_JSON_ENVELOPE']

class BodyTimeout(EnvelopeError):
    code: Literal['REQUEST_BODY_TIMEOUT']

class BodyLimit(EnvelopeError):
    code: Literal['REQUEST_SIZE_LIMIT']

class JSONRequired(EnvelopeError):
    code: Literal['JSON_REQUIRED']
