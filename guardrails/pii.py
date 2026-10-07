"""Mask personal data (PII) in OCR text before it leaves the OCR server.

mask(text) returns (masked text, count per type), for example "Paciente: Maria Souza"
-> ("Paciente: [NOME]", {"NOME": 1}). mask_page(lines) does the same for the lines of
an order. Nothing here raises: masking must never stop the flow.

Types: NOME, CPF, RG, TELEFONE, EMAIL, DATA and CRM (the project's interface
contract), plus ENDERECO, SUS, PRONTUARIO, CID, CLINICO, CONVENIO and IDADE.

The rules, in the order they run on each line:
1. PATTERNS, one regex per type, most specific first. The sensitive part is the
   group named "value..."; a label before it stays ("CPF: [CPF]"). Each regex also
   accepts what the OCR does to it: "," for ".", "@" read as "g", "Q" or "€", ...
2. Names, word by word (mask_names):
   a. after a name label ("Paciente:", "Dr.", "Sr(a)."), the rest of the line up to the
      next label or exam;
   b. on a line with an exam, 2 or more name words next to it.
   An exam word, or a word one OCR typo away from one, is never part of a name.
3. mask_page: a CPF the OCR split in two lines ("CPF: 517.916." / "257-38").
4. mask_page, the safety net: only what looks like an exam leaves the OCR. Each piece of
   a line stays only if it is as close to an exam as the RAG accepts, or is the order's
   structure ("Solicito:", "CPF: [CPF]"); any other piece, and inside an exam's piece any word
   that is not exam-like or a long number, becomes [NOME] if it has a common first name, else
   [TEXTO_REMOVIDO]. A name alone on a line, or a doctor's stamp the OCR deformed, stops here.
   So NOME counts only what a name rule saw (a label, capitals next to an exam, a first name);
   the rest is counted as TEXTO_REMOVIDO, which may hold a name the rules did not recognize.

The regexes and word lists are in guardrails/pii_rules.py; this module is the engine.
"""
import difflib
import functools
import re

import catalogo  # the same score as the RAG search: what it can find may leave the OCR
from catalogo import MIN_SCORE, fold, words
from guardrails.pii_rules import (
    AMOUNT,
    COMPILED,
    CPF_REST,
    CPF_START,
    EXAM_MODIFIERS,
    FIELD_NAMES,
    FIRST_NAMES,
    JOINED,
    LABEL,
    LONG_NUMBER,
    MARKS_AFTER,
    MARKS_BEFORE,
    NAME_PARTICLES,
    NEXT_LABEL,
    NOT_NAMES,
    OCR_DIGITS,
    ORDINARY_WORDS,
    PARTICLES,
    PHRASE_WORDS,
    PIECES,
    STRUCTURE,
    TAG,
    UNITS,
    VISIBLE,
    WORD,
)


def catalog_terms(catalog: list[dict] = catalogo.CATALOG) -> list[str]:
    """Every exam name and synonym, as written. The default argument reads the catalog when this
    module is imported, so pii.py fails at import without it: names could not be told from exams."""
    return [term for row in catalog for term in [row['name'], *row.get('synonyms', [])]]


def exam_vocabulary(catalog: list[dict] = catalogo.CATALOG) -> frozenset[str]:
    """Every word of every exam name and synonym."""
    return frozenset(fold(word) for term in catalog_terms(catalog) for word in re.findall(r'\w+', term))


VOCABULARY = exam_vocabulary() | EXAM_MODIFIERS
EXAM_TERMS = frozenset(words(term) for term in catalog_terms())


@functools.lru_cache(maxsize=65536)
def exam_word(word: str, vocabulary: frozenset[str] = VOCABULARY) -> bool:
    """An exam word, or one OCR typo away from one ("Hemogrma"): never part of a name."""
    parts = [fold(part) for part in re.findall(r'\w+', word)]
    if len(parts) > 1:
        parts = [part for part in parts if len(part) > 1]  # the "d" of "D'Ávila" is not "Vitamina D"
    return any(part in vocabulary or (len(part) >= 5 and difflib.get_close_matches(part, vocabulary, 1, 0.85))
               for part in parts)


def kind(word: str, vocabulary: frozenset[str], label: bool = False, line_exams: frozenset[str] = frozenset()) -> str:
    """'particle', 'exam', 'header', 'label', 'phrase', 'name' (a capital or a common first
    name) or 'other'."""
    folded = fold(word)
    if folded in NAME_PARTICLES or len(folded) < 2:
        return 'particle'  # "do" and "E" are also in exam names ("Hormônio do crescimento")
    if folded in line_exams or exam_word(word, vocabulary):  # line_exams: words of the exams on this line
        return 'exam'
    if folded in NOT_NAMES:
        return 'header'
    if label:
        return 'label'
    if folded in PHRASE_WORDS or folded in ORDINARY_WORDS or VISIBLE.fullmatch(folded):
        return 'phrase'  # "NAO realizar", "autoriza incluir": words of the order, never a name
    if word[0].isupper() or fold(re.split(r"['’-]", word)[0]) in FIRST_NAMES:
        return 'name'
    return 'other'  # a lower-case word: OCR junk or a surname, never the start of a name


def mask_names(line: str, counts: dict[str, int], vocabulary: frozenset[str] = VOCABULARY) -> str:
    """Rules 2a and 2b of the module docstring, on one line."""
    tagged = [match.span() for match in TAG.finditer(line)]
    exams = exams_on(line)
    line_exams = frozenset(word for term in exams for word in term.split())
    items = [(match.start(), match.end(), kind(match.group(), vocabulary, is_label(line, match), line_exams))
             for match in WORD.finditer(line) if not any(start <= match.start() < end for start, end in tagged)]
    spans = labelled_names(line, items) or names_next_to_exam(line, items, exams)
    for start, end in sorted(spans, reverse=True):
        line = line[:start] + '[NOME]' + line[end:]
        counts['NOME'] = counts.get('NOME', 0) + 1
    return line


def is_label(line: str, match: re.Match) -> bool:
    """A label, not a name: the first word before ":" ("Contato: Maria"), or a field name right
    before a masked value ("CPF [CPF]", "Nascimento [DATA]")."""
    after = line[match.end():]
    return bool((fold(match.group()) in FIELD_NAMES and re.match(r'[ \t]*[:;.]?[ \t]*\[[A-Z]+\]', after))
                or (not line[:match.start()].strip() and re.match(r'[ \t]*:', after)))


def labelled_names(line, items):
    """2a. After a name label, the rest of the line up to the next label, masked value or exam.

    "Paciente: * Bianta Inventado e . [" -> "Paciente: [NOME]". Without a colon ("paciente
    maria"), the name must start with a capital or a common first name: "Paciente nascida
    em" is kept.
    """
    starts = [(match.end(), match.group('colon') is not None) for match in LABEL.finditer(line)]
    spans = []
    for begin, colon in starts:
        if spans and begin <= spans[-1][1]:
            continue  # "Médico: Dr. Carlos": "Dr." is inside the name already found
        stop = len(line)
        for found in (NEXT_LABEL.search(line, begin), TAG.search(line, begin)):
            stop = min(stop, found.start()) if found else stop
        region = [(start, end, k) for start, end, k in items if begin <= start and end <= stop]
        exam = next((start for start, _, k in region if k == 'exam'), None)
        region = [item for item in region if exam is None or item[0] < exam]
        names = [item for item in region if item[2] in ('name', 'other')]
        if not names or (not colon and region[0][2] != 'name'):
            continue
        # Up to the next label or masked value (junk included), or to the last name word before an exam.
        end = names[-1][1] if exam is not None else len(line[:stop].rstrip(' \t-–—,;|'))
        spans.append((names[0][0], end))
    return spans


def exams_on(line: str) -> list[str]:
    """The whole exam names and synonyms of the catalog on this line (catalogo.words)."""
    line_words = f' {words(line)} '
    return [term for term in EXAM_TERMS if f' {term} ' in line_words]


def names_next_to_exam(line, items, exams):
    """2b. On a line with an exam, 2 or more name words in a row next to it: "Hemograma
    completo - José Neto" -> "Hemograma completo - [NOME]". One word alone is kept ("cor rosa")."""
    if not exams:
        return []
    spans, run = [], []
    for item in items + [(len(line), len(line), 'end')]:
        start, _, kind_ = item
        joined = run and not line[run[-1][1]:start].strip()  # only spaces since the last word
        if joined and kind_ in ('name', 'other', 'particle'):
            run.append(item)  # the name goes on: "José da Silva", "raí peçanha"
            continue
        spans += name_span(run)  # the name ended
        run = [item] if kind_ == 'name' else []  # a name starts with a capital or a common first name
    return spans


def name_span(run):
    """[(start, end)] of a run of words if, without trailing particles, it has 2 name words."""
    while run and run[-1][2] == 'particle':
        run = run[:-1]
    return [(run[0][0], run[-1][1])] if sum(kind_ != 'particle' for _, _, kind_ in run) >= 2 else []


def looks_like_email(text: str, vocabulary: frozenset[str] = VOCABULARY) -> bool:
    """An address whose "@" the OCR misread, not a sentence ending in ". com": it has a digit,
    a "+", a domain name or a glued domain ("correio.hospital.org.br"), and no exam word."""
    if any(exam_word(word, vocabulary) for word in re.findall(r'\w+', text)):
        return False
    return bool(re.search(r'\d|\+|exemplo|example|gmail|hotmail|yahoo|outlook|\w\.\w+\.\w', text))


def mask(text: str, vocabulary: frozenset[str] = VOCABULARY) -> tuple[str, dict[str, int]]:
    """Return (masked_text, counts). Only types that were found appear in counts."""
    counts: dict[str, int] = {}

    def substitute(match, kind):
        # A regex with alternatives has one "value..." group per alternative (group names
        # must be unique); the one that matched is the only one not None.
        group = next(name for name, value in match.groupdict().items() if value is not None)
        if group == 'value_domain' and not looks_like_email(match.group(group), vocabulary):
            return match.group(0)  # "Glicemia de jejum. com 8h": a sentence, not an e-mail
        counts[kind] = counts.get(kind, 0) + 1
        start, end = (i - match.start() for i in match.span(group))
        return match.group(0)[:start] + f'[{kind}]' + match.group(0)[end:]

    for kind_, pattern in COMPILED:
        text = pattern.sub(functools.partial(substitute, kind=kind_), text)
    return '\n'.join(mask_names(line, counts, vocabulary) for line in text.split('\n')), counts


COUNTED = frozenset(kind for kind, _ in COMPILED) | {'NOME', 'TEXTO_REMOVIDO'}


def mask_page(lines: list[str]) -> tuple[list[str], dict[str, int]]:
    """mask() line by line, plus a CPF the OCR split in two lines ("CPF: 517.916." / "257-38"),
    then the safety net of rule 4: a line that does not look like an exam does not leave.

    The counts are the markers each line leaves the OCR with, so a value the safety net then took
    with the text around it counts as the [TEXTO_REMOVIDO] that stays, not as the type it first got;
    a marker already written in the order counts for nothing; the 2nd half of a split CPF is the
    same CPF."""
    masked: list[str] = []
    counts: dict[str, int] = {}
    for index, line in enumerate(lines):
        safe, found = mask(line)
        previous = CPF_START.search(lines[index - 1]) if index else None
        rest = CPF_REST.match(safe)
        split_cpf = bool(previous and rest and sum(c.isdigit() for c in previous.group('part') + rest.group('part')) == 11)
        if split_cpf and rest:
            safe = safe[:rest.start('part')] + '[CPF]' + safe[rest.end('part'):]
        masked.append(only_what_may_leave(safe, found))
        written, left = (re.findall(r'\[([A-Z_]+)\]', text) for text in (line, masked[-1]))
        for kind_ in COUNTED:
            amount = left.count(kind_) - written.count(kind_) - (kind_ == 'CPF' and split_cpf)
            if amount > 0:
                counts[kind_] = counts.get(kind_, 0) + amount
    return masked, counts


def only_what_may_leave(line: str, counts: dict[str, int]) -> str:
    """Rule 4 on one line: each piece that is neither exam-like nor structure is replaced, and
    in a piece kept as an exam only the exam's own words stay.

    "Paciente: CPF: Celina Inventado" -> "Paciente: CPF: [NOME]"; "DADOS FICTICIOS" ->
    "[TEXTO_REMOVIDO]" (no common first name, so it is not counted as a name); "Anti HCV
    tobias fagundes" -> "Anti HCV [NOME]".
    """
    pieces = PIECES.split(line)
    for index in range(0, len(pieces), 2):  # pieces at even positions, separators between them
        parts = [pieces[index]] if may_leave(pieces[index]) else JOINED.split(pieces[index])
        for at in range(0, len(parts), 2):
            if not may_leave(parts[at]):
                parts[at] = removed(parts[at], counts)
            elif not is_structure(parts[at]):
                parts[at] = only_exam_words(parts[at], counts)
        pieces[index] = ''.join(parts)
    return ''.join(pieces)


def only_exam_words(piece: str, counts: dict[str, int]) -> str:
    """In a piece kept as an exam, the words that are not exam-like, structure (connectors,
    labels), short numbers or units go: each run of them becomes [NOME] if it has a common first
    name (the name rule), else [TEXTO_REMOVIDO]. "Hemograma completo - José Neto" is the name rule's
    (rule 2b); here "Hemograma completo uirá araripe" -> "Hemograma completo [TEXTO_REMOVIDO]" and
    "Glicose 98765432" -> "Glicose [TEXTO_REMOVIDO]" (LONG_NUMBER: no exam name has 5 digits)."""
    tokens = list(re.finditer(r'\S+', piece))
    long_numbers = [match.span() for match in LONG_NUMBER.finditer(piece)]
    outside = [token for token in tokens if not TAG.fullmatch(token.group()) and (
        any(start < token.end() and token.start() < end for start, end in long_numbers) or not all(
            word in STRUCTURE or word in UNITS or AMOUNT.fullmatch(word) or exam_like(word) or short_exam_word(word)
            or visible(word) for word in words(token.group()).split()) and not short_cue(piece, token))]
    # An exam word the OCR split in two ("Colesti erol total"): glued again, it is exam-like, and stays.
    split = {index for index, (first, second) in enumerate(zip(outside, outside[1:], strict=False))
             if not piece[first.end():second.start()].strip() and exam_like(words(first.group() + second.group()))}
    outside = [token for index, token in enumerate(outside) if index not in split and index - 1 not in split]
    runs: list[list[re.Match[str]]] = []
    for token in outside:  # tokens in a row, with only spaces between them, form one run
        if runs and not piece[runs[-1][-1].end():token.start()].strip():
            runs[-1].append(token)
        else:
            runs.append([token])
    for run in reversed(runs):
        tag = 'NOME' if has_first_name(' '.join(token.group() for token in run)) else 'TEXTO_REMOVIDO'
        counts[tag] = counts.get(tag, 0) + 1
        piece = piece[:run[0].start()] + f'[{tag}]' + piece[run[-1].end():]
    return piece


@functools.lru_cache(maxsize=65536)
def short_exam_word(word: str) -> bool:
    """Inside an exam, a 2-4 letter word one letter off an exam word: "lgM" (IgM), "Co" (CA),
    "Arti" (Anti). Never a common first name."""
    return 2 <= len(word) <= 4 and word not in FIRST_NAMES and any(
        len(exam) == len(word) and sum(a != b for a, b in zip(exam, word, strict=True)) == 1 for exam in VOCABULARY)


def is_structure(piece: str) -> bool:
    """Only the order's structure around masked values, or what it says of its exams: "Solicito:",
    "CPF: [CPF]", "não precisa"."""
    return all(word in STRUCTURE or (word.isdigit() and len(word) <= 2) or visible(word)
               for word in words(TAG.sub(' ', piece)).split())


def short_cue(piece: str, token: re.Match[str]) -> bool:
    """Whether the token is a short "não" or "sem" (SHORT_CUE), marks before it aside: "(n/"."""
    found = SHORT_CUE.search(piece, token.start())
    return found is not None and found.start() < token.end()


# The short forms of "não" and "sem" ("n/ realizar", "ñ fazer", "s/ necessidade"): visible too, but only
# written this way, so a lone letter ("D.N.", the initial of a name) is still removed.
SHORT_CUE = re.compile(r'(?<![^\s(\[-])(?:[nNsS]/|[ñÑ])(?=\s)')


def visible(word: str) -> bool:
    """A negation, history or exception word (pii_rules.VISIBLE): never removed, never a name."""
    return bool(VISIBLE.fullmatch(fold(word)))


def may_leave(piece: str) -> bool:
    """Structure around masked values, an exam (a whole name, or only exam words), or as close
    to an exam as the RAG accepts with an exam-like word in it: a misread exam keeps one
    ("Hemogrma compieto"), a name does not ("MARIA DO RIBEIRO" is 0.6 from "Hormônio do
    crescimento" by characters). One word alone must be exam-like, or start an exam word:
    "Lima" stops, "Ferrit." stays."""
    text = TAG.sub(' ', piece)
    rest = words(text).split()
    if is_structure(piece):
        return True
    if exams_on(text) or all(word in VOCABULARY or word in STRUCTURE for word in rest):
        return True  # "D ultrassensível" of "25(OH)D ultrassensível" is only exam words
    if len(rest) == 1:
        return exam_like(rest[0]) or (len(rest[0]) >= 4 and any(word.startswith(rest[0]) for word in VOCABULARY))
    return any(exam_like(word) for word in rest if word not in PARTICLES) and rag_score(text) >= MIN_SCORE


@functools.lru_cache(maxsize=65536)
def exam_like(word: str) -> bool:
    """An exam word, exact however short ("T3", "GT", "19" of CA 19-9), or 4+ letters one OCR
    error from one ("Dlmero" 0.83, "Urlna" 0.8), also with digits read as letters; never a
    common first name ("Márcia" is 0.77 from "parcial")."""
    forms = {word, word.translate(OCR_DIGITS)} if any(char.isalpha() for char in word) else {word}
    return any(form in VOCABULARY or (len(form) >= 4 and form not in FIRST_NAMES
                                      and difflib.get_close_matches(form, VOCABULARY, 1, 0.8))
               for form in forms)


def has_first_name(text: str) -> bool:
    """Whether the text has a common first name (prenomes.txt): what makes removed text a name."""
    return any(fold(re.split(r"['’-]", word)[0]) in FIRST_NAMES for word in WORD.findall(text))


def removed(piece: str, counts: dict[str, int]) -> str:
    """The piece without its text, but for the negation, history and exception words in it: "não
    tomar café" -> "não [TEXTO_REMOVIDO]". Each stretch between them is replaced."""
    kept = [match for match in WORD.finditer(piece) if visible(match.group())
            or SHORT_CUE.match(piece, match.start())]
    out, at = [], 0
    for start, end in [(match.start(), match.end()) for match in kept] + [(len(piece), len(piece))]:
        stretch = piece[at:start]
        out.append(replaced(stretch, counts) if re.search(r'[^\W_]', stretch) else stretch)
        out.append(piece[start:end])
        at = end
    return ''.join(out)


def replaced(piece: str, counts: dict[str, int]) -> str:
    """[NOME] if the piece has a common first name, else [TEXTO_REMOVIDO]; marks around it stay."""
    tag = 'NOME' if has_first_name(piece) else 'TEXTO_REMOVIDO'
    counts[tag] = counts.get(tag, 0) + 1
    before, after = MARKS_BEFORE.match(piece), MARKS_AFTER.search(piece)  # both always match, maybe empty
    return (before.group() if before else '') + f'[{tag}]' + (after.group() if after else '')


@functools.lru_cache(maxsize=65536)
def rag_score(text: str) -> float:
    """The best score the RAG search (mcp_servers/rag.py) would give this text, list number aside."""
    query = catalogo.normalize(text).lstrip('0123456789 ').strip()
    return max((catalogo.similarity(query, term) for exam in catalogo.CATALOG for term in exam['terms']), default=0.0) \
        if query else 0.0
