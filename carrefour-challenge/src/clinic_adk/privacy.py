"""Conservative structured extraction: raw OCR never leaves this boundary."""
import re
import unicodedata
from .errors import SafeError

def normalize(value):
    if not isinstance(value, str) or len(value) > 120:
        raise SafeError('INVALID_EXAM_QUERY')
    value = unicodedata.normalize('NFKC', value)
    if any(unicodedata.category(c).startswith('C') for c in value):
        raise SafeError('INVALID_EXAM_QUERY')
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                           if not unicodedata.combining(c)).split())

PII = re.compile(r'(?i)([\w.+-]+@[\w.-]+\.[a-z]{2,}|\b\d[\d .()/+-]{7,}\d\b|https?://)')
LABEL = re.compile(r'(?i)^\s*(paciente|nome|m[eé]dic[oa]|doutor|dra?|cpf|rg|documento|telefone|celular|email|e-mail|contato|endere[cç]o|nascimento)\b')
INJECTION = re.compile(r'(?i)(ignore|ignor[ea]|instru[cç]|system|prompt|execute|crie|agende|sudo|eval|__import__|<script|https?://|FICT-\d)')
def query_safe(value):
    key = normalize(value)
    if not key or PII.search(value) or INJECTION.search(value) or not re.fullmatch(r'[a-z0-9 ()/.,+-]{1,100}', key):
        raise SafeError('UNSAFE_EXAM_QUERY')
    return key

def sanitize_ocr(raw, catalog):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 16000:
        raise SafeError('OCR_EMPTY_OR_OVERSIZED')
    names, unresolved, redacted = [], 0, 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Discard labelled sensitive headers before examining their text as commands.
        # They never reach a tool, LLM or exam list: a patient's name is not an instruction.
        if LABEL.search(line):
            redacted += 1
            continue
        if INJECTION.search(line):
            raise SafeError('UNTRUSTED_IMAGE_INSTRUCTIONS')
        if PII.search(line):
            if re.match(r'(?i)^exame\s*:', line):
                raise SafeError('OCR_UNRESOLVED_EXAMS')
            redacted += 1
            continue
        explicit = bool(re.match(r'(?i)^exame\s*:', line))
        candidate = re.sub(r'(?i)^exame\s*:\s*', '', line).strip(' *-')
        if not explicit and candidate.upper().startswith(('PEDIDO', 'DEMONSTRACAO', 'DEMONSTRAÇÃO', 'DADOS FICT', 'CLINICA', 'CLÍNICA')):
            continue
        try:
            key = query_safe(candidate)
        except SafeError:
            unresolved += 1
            continue
        # Return only catalog-approved labels. Unknown text/names are not echoed.
        entry = catalog.by_name.get(key)
        if entry:
            if entry['name'] not in names:
                names.append(entry['name'])
        else:
            unresolved += 1
    if not names or unresolved:
        raise SafeError('OCR_UNRESOLVED_EXAMS')
    if len(names) > 20:
        raise SafeError('TOO_MANY_EXAMS')
    return {'ok': True, 'exam_names': names, 'pii_masked': True,
            'redacted_lines': redacted, 'unresolved_count': 0}
