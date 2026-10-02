"""Untrusted task output is format-normalized, then strictly authorized."""
import json
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from free_model import LabBlocked


class Report(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_ids: list[StrictStr] = Field(min_length=2, max_length=2)


class ReadProof(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_id: StrictStr
    quote: StrictStr


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('AMBIGUOUS_JSON_KEY')
        result[key] = value
    return result


def _decode(raw, *, limit=8192, fence=False):
    if not isinstance(raw, str) or len(raw) > limit:
        raise LabBlocked('TASK_REPORT_NOT_GROUNDED')
    try:
        if len(raw.encode('utf8')) > limit:
            raise ValueError('REPORT_BYTES')
        text = raw.strip()
        if fence and text.startswith('```'):
            lines = text.splitlines()
            if len(lines) < 3 or lines[0] != '```json' or lines[-1] != '```' or any('```' in line for line in lines[1:-1]):
                raise ValueError('INVALID_JSON_FENCE')
            text = '\n'.join(lines[1:-1])
        return json.loads(text, object_pairs_hook=_pairs)
    except (ValueError, TypeError, RecursionError):
        raise LabBlocked('TASK_REPORT_NOT_GROUNDED') from None


def source_ids(raw, authorized):
    try:
        result = Report.model_validate(_decode(raw, fence=True)).source_ids
    except (ValueError, TypeError):
        raise LabBlocked('TASK_REPORT_NOT_GROUNDED') from None
    if len(set(result)) != 2 or set(result) != set(authorized):
        raise LabBlocked('TASK_REPORT_NOT_GROUNDED')
    return result


def read_sources(messages, authorized):
    result = set()
    for message in messages:
        if getattr(message, 'status', 'success') != 'success':
            raise LabBlocked('TASK_READ_PROOF_REQUIRED')
        try:
            proof = ReadProof.model_validate(_decode(message.content, limit=60000))
        except (ValueError, TypeError):
            raise LabBlocked('TASK_READ_PROOF_REQUIRED') from None
        if proof.source_id not in authorized or not proof.quote:
            raise LabBlocked('TASK_READ_PROOF_REQUIRED')
        result.add(proof.source_id)
    if result != set(authorized):
        raise LabBlocked('TASK_READ_PROOF_REQUIRED')
    return result
