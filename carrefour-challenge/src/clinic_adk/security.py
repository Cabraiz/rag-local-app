"""Data can never expand the fixed workflow's authority."""
import re
import unicodedata
from .errors import SafeError
from .privacy import INJECTION, PII, query_safe

INSTRUCTIONS = re.compile(
    r'(?i)(reveal|disclose|secret|segredo|token|password|senha|system|developer|'
    r'prompt|instruc|instru[çc]|ignore|ignor[ea]|execute|exec\b|eval\b|sudo|'
    r'__import__|tool\b|ferramenta|identity|identidade|override|bypass|'
    r'file://|https?://|localhost|/etc/|\.\.[/\\]|<script|'
    r'(?:leia|ler|read|abra)\s+(?:o\s+)?(?:arquivo|file)|'
    r'(?:troque|mude|change)\s+(?:a\s+)?(?:identidade|identity))')


def image_reference(value):
    # Reject before resolve/stat/open: callers cannot choose directories, schemes
    # or symlink targets. bounded_file also refuses symlinks on the descriptor.
    if (not isinstance(value, str) or not 1 <= len(value) <= 100
            or '..' in value or not re.fullmatch(
                r'[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:png|jpg|jpeg)', value, re.IGNORECASE)):
        raise SafeError('IMAGE_REFERENCE_DENIED')
    return value


def tool_arguments(tool, arguments):
    if not isinstance(arguments, dict):
        raise SafeError('MCP_INVALID_TOOL_ENVELOPE')
    if tool == 'extract_exams' and set(arguments) == {'image_ref'}:
        image_reference(arguments['image_ref'])
        return
    if tool == 'lookup_exams' and set(arguments) == {'exam_names'}:
        names = arguments['exam_names']
        if isinstance(names, list) and 1 <= len(names) <= 20:
            for name in names:
                query_safe(name)
            return
    raise SafeError('MCP_INVALID_TOOL_ENVELOPE')


def catalog_text(value):
    if not isinstance(value, str):
        raise SafeError('CATALOG_UNTRUSTED_TEXT')
    normalized = unicodedata.normalize('NFKC', value)
    if (any(unicodedata.category(c).startswith('C') for c in normalized)
            or PII.search(normalized) or INSTRUCTIONS.search(normalized)
            or INJECTION.search(normalized)):
        raise SafeError('CATALOG_UNTRUSTED_TEXT')


def ocr_text(raw, catalog, sanitizer):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 16000:
        raise SafeError('OCR_EMPTY_OR_OVERSIZED')
    # Inspect instructions before the privacy sanitizer discards personal labels.
    # A labelled attacker command must not become a partial authorized request.
    normalized = unicodedata.normalize('NFKC', raw)
    if INSTRUCTIONS.search(normalized):
        raise SafeError('UNTRUSTED_IMAGE_INSTRUCTIONS')
    return sanitizer(raw, catalog)


def tool_payload(provider, value):
    """Accept only the negotiated response schema, before advancing the graph."""
    if not isinstance(value, dict) or value.get('ok') is not True:
        raise SafeError('MCP_INVALID_RESULT')
    if provider == 'ocr':
        fields = {'ok', 'exam_names', 'pii_masked', 'redacted_lines', 'unresolved_count'}
        if (set(value) != fields or type(value['redacted_lines']) is not int
                or not 0 <= value['redacted_lines'] <= 16000
                or value['pii_masked'] is not True
                or type(value['unresolved_count']) is not int or value['unresolved_count'] != 0
                or not isinstance(value['exam_names'], list) or not 1 <= len(value['exam_names']) <= 20):
            raise SafeError('OCR_UNAUTHORIZED_OUTPUT')
        for name in value['exam_names']:
            query_safe(name)
    elif provider == 'rag':
        fields = {'ok', 'exams', 'unresolved_indices', 'abstentions', 'catalog_version', 'catalog_count'}
        if (set(value) != fields or type(value['catalog_count']) is not int
                or not 100 <= value['catalog_count'] <= 1000
                or value['unresolved_indices'] != []
                or value['abstentions'] != []
                or not isinstance(value['catalog_version'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', value['catalog_version'])
                or not isinstance(value['exams'], list) or not 1 <= len(value['exams']) <= 20):
            raise SafeError('RAG_UNAUTHORIZED_OUTPUT')
        for row in value['exams']:
            if (not isinstance(row, dict) or set(row) != {'name', 'code', 'evidence'}
                    or any(not isinstance(row[k], str) for k in row)):
                raise SafeError('RAG_UNAUTHORIZED_OUTPUT')
    else:
        raise SafeError('MCP_PROVIDER_DENIED')
    return value
