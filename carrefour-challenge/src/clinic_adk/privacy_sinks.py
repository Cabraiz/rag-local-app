"""Explicit privacy gates for consumers to apply BEFORE events or output.

No regex masking of clinical identifiers: retain exact canonical fields and
reject untrusted rows. This module does not install hooks or change owners' code.
"""
from functools import wraps
import hashlib
import re

from .errors import SafeError
from .privacy import query_safe

PUBLIC_CODES = frozenset((
    'OCR_PRIVACY_OR_EXTRACTION_GATE', 'OCR_UNAUTHORIZED_OUTPUT',
    'OCR_UNRESOLVED_EXAMS', 'OCR_EMPTY_OR_OVERSIZED', 'TOO_MANY_EXAMS',
    'UNTRUSTED_IMAGE_INSTRUCTIONS', 'MCP_UNAVAILABLE', 'MCP_TOOL_FAILED',
    'MCP_INVALID_RESULT', 'MCP_TOOL_MANIFEST_MISMATCH', 'MCP_INVALID_TOOL_ARGUMENTS', 'EXAM_EVIDENCE_MISMATCH',
    'RAG_INCOMPLETE_OR_STALE', 'APPOINTMENT_REJECTED',
    'APPOINTMENT_INVALID_RECEIPT', 'APPOINTMENT_RECEIPT_MISMATCH',
    'APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY',
    'WORKFLOW_TIMEOUT_BEFORE_APPOINTMENT', 'WORKFLOW_FAILED_SAFE',
    'INVALID_REQUEST_ID', 'CLI_INVALID_ARGUMENTS',
))


def public_failure_code(error):
    """Unwrap known codes only; error text, arbitrary codes and args are private."""
    pending, seen = [error], set()
    for _ in range(16):
        if not pending:
            break
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, SafeError) and isinstance(current.code, str) and current.code in PUBLIC_CODES:
            return current.code
        pending.extend(item for item in (current.__cause__, current.__context__) if item is not None)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions[:8])
    return 'WORKFLOW_FAILED_SAFE'


def private_boundary(function):
    """Wrap an async node/tool boundary before an SDK publishes its exception."""
    @wraps(function)
    async def guarded(*args, **kwargs):
        try:
            return await function(*args, **kwargs)
        except Exception as error:
            raise SafeError(public_failure_code(error)) from None
    return guarded


def ocr_output(value, catalog):
    if (not isinstance(value, dict) or value.get('ok') is not True
            or value.get('pii_masked') is not True
            or type(value.get('unresolved_count')) is not int
            or value['unresolved_count'] != 0):
        raise SafeError('OCR_PRIVACY_OR_EXTRACTION_GATE')
    names = value.get('exam_names')
    if not isinstance(names, list) or not 1 <= len(names) <= 20:
        raise SafeError('OCR_PRIVACY_OR_EXTRACTION_GATE')
    canonical = []
    for name in names:
        try:
            row = catalog.by_name.get(query_safe(name))
        except SafeError:
            raise SafeError('OCR_UNAUTHORIZED_OUTPUT') from None
        if row is None:
            raise SafeError('OCR_UNAUTHORIZED_OUTPUT')
        if row['name'] not in canonical:
            canonical.append(row['name'])
    # Free-form metadata and counts from remote tools are never event content.
    return {'ok': True, 'exam_names': canonical, 'pii_masked': True, 'unresolved_count': 0}


def rag_output(value, names, catalog):
    if (not isinstance(value, dict) or value.get('ok') is not True
            or value.get('unresolved_indices') != [] or value.get('catalog_version') != catalog.version):
        raise SafeError('RAG_INCOMPLETE_OR_STALE')
    if not isinstance(names, list) or not 1 <= len(names) <= 20:
        raise SafeError('EXAM_EVIDENCE_MISMATCH')
    expected = set()
    for name in names:
        try:
            row = catalog.by_name.get(query_safe(name))
        except SafeError:
            raise SafeError('EXAM_EVIDENCE_MISMATCH') from None
        if row is None:
            raise SafeError('EXAM_EVIDENCE_MISMATCH')
        expected.add(row['code'])
    rows = value.get('exams')
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise SafeError('EXAM_EVIDENCE_MISMATCH')
    seen, result = set(), []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'name', 'code', 'evidence'}
                or any(not isinstance(row[key], str) for key in ('name', 'code', 'evidence'))):
            raise SafeError('EXAM_EVIDENCE_MISMATCH')
        approved = catalog.by_code.get(row['code'])
        if (approved is None or row['code'] in seen
                or any(row[key] != approved[key] for key in ('name', 'evidence'))):
            raise SafeError('EXAM_EVIDENCE_MISMATCH')
        seen.add(row['code'])
        result.append({key: approved[key] for key in ('name', 'code', 'evidence')})
    if seen != expected:
        raise SafeError('EXAM_EVIDENCE_MISMATCH')
    return {'ok': True, 'exams': result, 'unresolved_indices': [], 'catalog_version': catalog.version}


@private_boundary
async def private_mcp_output(provider, arguments, invoke, catalog):
    """Adapter for runtime owners; invoke is the existing decoded MCP client."""
    if type(provider) is not str or provider not in ('ocr', 'rag'):
        raise SafeError('MCP_TOOL_MANIFEST_MISMATCH')
    field = 'image_ref' if provider == 'ocr' else 'exam_names'
    if type(arguments) is not dict or set(arguments) != {field}:
        raise SafeError('MCP_INVALID_TOOL_ARGUMENTS')
    if provider == 'ocr':
        reference = arguments[field]
        if (type(reference) is not str or not 1 <= len(reference) <= 100
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*\.(?:png|jpg|jpeg)',
                                    reference, flags=re.ASCII | re.IGNORECASE)):
            raise SafeError('MCP_INVALID_TOOL_ARGUMENTS')
        projected = {'image_ref': reference}
        value = await invoke(provider, projected)
        return ocr_output(value, catalog)
    names = arguments[field]
    if type(names) is not list or not 1 <= len(names) <= 20:
        raise SafeError('MCP_INVALID_TOOL_ARGUMENTS')
    approved = []
    for name in names:
        if type(name) is not str or not 1 <= len(name) <= 120:
            raise SafeError('MCP_INVALID_TOOL_ARGUMENTS')
        try:
            row = catalog.by_name.get(query_safe(name))
        except SafeError:
            raise SafeError('MCP_INVALID_TOOL_ARGUMENTS') from None
        if row is None:
            raise SafeError('MCP_INVALID_TOOL_ARGUMENTS')
        if row['name'] not in approved:
            approved.append(row['name'])
    # Keep expected names separate from the mutable object given to the client.
    projected = {'exam_names': approved.copy()}
    value = await invoke(provider, projected)
    return rag_output(value, approved, catalog)


def artifact_acknowledgement(source):
    """Filename already belongs to caller; only emit a fixed ack and source hash."""
    return {'ok': True, 'generated': True, 'sha256': hashlib.sha256(source).hexdigest()}
