"""Mask personal data (PII) in OCR text before it leaves the OCR server.

mask(text) -> (masked text, count per type): "Paciente: Maria Souza" -> ("Paciente: [NOME]", {"NOME": 1}); mask_page
does it for an order's lines. Nothing raises. Types: NOME, CPF, RG, TELEFONE, EMAIL, DATA, CRM, ENDERECO, SUS, PRONTUARIO,
CID, CLINICO, CONVENIO and IDADE. The rules (word lists and regexes in pii_rules.py), in the order they run:
1. PATTERNS, one regex per type, most specific first. The sensitive part is the group named "value..."; a label
   before it stays ("CPF: [CPF]"). Each regex also accepts what the OCR does to it: "," for ".", "@" read as "g"...
2. Names, word by word (mask_names): a. after a name label ("Paciente:", "Dr.", "Sr(a)."), the rest of the line up
   to the next label or exam; b. on a line with an exam, 2 or more name words next to it. An exam word, or a word
   one OCR typo away from one, is never part of a name.
3. mask_page: a CPF the OCR split in two lines ("CPF: 517.916." / "257-38").
4. mask_page, the safety net: only what looks like an exam leaves the OCR. Each piece of a line stays only if
   it is as close to an exam as the RAG accepts, or is the order's structure ("Solicito:", "CPF: [CPF]"); any other
   piece, and inside an exam's piece any word that is not exam-like, a long number, or one after the exam's whole
   name that neither qualifies it nor starts another ("Ferritina Albina Ferro", "TSH Zé"), becomes [NOME] if it
   has a common first name, else [TEXTO_REMOVIDO]. A name alone on a line, or a doctor's stamp the OCR deformed,
   stops here. So NOME counts only what a name rule saw (a label, capitals next to an exam, a first name); the rest
   is counted as TEXTO_REMOVIDO, which may hold a name the rules did not recognize.
5. mask_page, by shape (shaped): a capitalized word next to a masked name or an initial is the name's ("Érica Ferro",
   "E. Ferro"), and after an exam's name a number or a capitalized word that is neither part of an exam's name nor a
   qualifier goes ("Glicemia 1234567 mg/dl", "Ureia (11)", "TSH Franco"; "25(OH)D", "Vitamina B12" stay).
"""
import difflib
import functools
import re

import catalogo
from catalogo import MASK_TAG, MAY_LEAVE, MIN_SCORE, NOT_A_NAME, QUALIFIERS, fold, plain, words
from guardrails import pii_rules as rules

MATCHER = catalogo.matcher()  # the RAG's own: what it finds may leave. Read at import: without it, no name is told from an exam
VOCABULARY = MATCHER.with_modifiers


@functools.lru_cache(maxsize=65536)
def exam_word_or_typo(word: str) -> bool:
    """An exam word, or one OCR typo away from one ("Hemogrma", NOT_A_NAME): never part of a name."""
    parts = [fold(part) for part in re.findall(r'\w+', word)]  # the "d" of "D'Ávila" is not "Vitamina D"
    return any(MATCHER.is_exam_word(part, NOT_A_NAME) for part in parts if len(part) > 1 or len(parts) == 1)


def kind(word: str, label: bool = False, line_exams: frozenset[str] = frozenset()) -> str:
    """'particle', 'exam', 'header', 'label', 'phrase', 'name' (a capital or a common first name) or 'other'."""
    folded = fold(word)
    if folded in rules.NAME_PARTICLES or len(folded) < 2:
        return 'particle'  # "do" and "E" are also in exam names ("Hormônio do crescimento")
    if folded in line_exams or exam_word_or_typo(word):  # line_exams: words of the exams on this line
        return 'exam'
    if folded in rules.NOT_NAMES:
        return 'header'
    if label:
        return 'label'
    if folded in rules.PHRASE_WORDS or folded in rules.ORDINARY_WORDS or rules.VISIBLE.fullmatch(folded):
        return 'phrase'  # "NAO realizar", "autoriza incluir": words of the order, never a name
    if word[0].isupper() or fold(re.split(r"['’-]", word)[0]) in rules.FIRST_NAMES:
        return 'name'
    return 'other'  # a lower-case word: OCR junk or a surname, never the start of a name


def mask_names(line: str, counts: dict[str, int]) -> str:
    """Rules 2a and 2b of the module docstring, on one line."""
    tagged = [match.span() for match in rules.TYPED_TAG.finditer(line)]
    exams = exams_on(line)
    line_exams = frozenset(word for term in exams for word in term.split())
    items = [(match.start(), match.end(), kind(match.group(), is_label(line, match), line_exams))
             for match in rules.WORD.finditer(line) if not any(start <= match.start() < end for start, end in tagged)]
    spans = labelled_names(line, items) or names_next_to_exam(line, items, exams)
    for start, end in sorted(spans, reverse=True):
        line = line[:start] + rules.NAME_TAG + line[end:]
        counts['NOME'] = counts.get('NOME', 0) + 1
    return line


def is_label(line: str, match: re.Match) -> bool:
    """A label, not a name: the first word before ":" ("Contato: Maria"), or a field name right
    before a masked value ("CPF [CPF]", "Nascimento [DATA]")."""
    after = line[match.end():]
    return bool((fold(match.group()) in rules.FIELD_NAMES and re.match(r'[ \t]*[:;.]?[ \t]*' + rules.TYPED_TAG.pattern, after))
                or (not line[:match.start()].strip() and re.match(r'[ \t]*:', after)))


def labelled_names(line, items):
    """2a. After a name label, the rest of the line up to the next label, masked value or exam. Without a colon
    ("paciente maria"), the name starts with a capital or a common first name: "Paciente nascida em" is kept."""
    starts = [(match.end(), match.group('colon') is not None, match.group('title') is not None)
              for match in rules.LABEL.finditer(line)]
    spans = []
    for begin, colon, title in starts:
        if spans and begin <= spans[-1][1]:
            continue  # "Médico: Dr. Carlos": "Dr." is inside the name already found
        stop = min([len(line), *(found.start() for found in (rules.NEXT_LABEL.search(line, begin),
                                                            rules.TYPED_TAG.search(line, begin)) if found)])
        region = [(start, end, k) for start, end, k in items if begin <= start and end <= stop]
        if (colon or title) and region and region[0][2] in ('name', 'other', 'exam') and any(k == 'exam' for *_, k in region):
            spans += whole_name(region)  # "Paciente: Albina Ferro": a surname that is also an exam word
            continue
        exam = next((start for start, _, k in region if k == 'exam'), None)
        region = [item for item in region if exam is None or item[0] < exam]
        names = [item for item in region if item[2] in ('name', 'other')]
        if not names or (not colon and region[0][2] != 'name'):
            continue
        # Up to the next label or masked value (junk included), or to the last name word before an exam.
        end = names[-1][1] if exam is not None else len(line[:stop].rstrip(' \t-–—,;|'))
        spans.append((names[0][0], end))
    return spans


def whole_name(region):
    """[(start, end)] of the name right after a name label ("Paciente:", "Dr."): every word up to a word
    of a sentence ("Dr. Lima pede também TSH"), exam words included ("Maria Ferro", "Paulo Fosforo")."""
    stop = next((i for i, (*_, k) in enumerate(region) if k not in ('name', 'other', 'exam', 'particle')), len(region))
    return name_span(region[:stop], 1)


def exams_on(line: str) -> list[str]:
    """The whole exam names and synonyms of the catalog on this line (catalogo.words)."""
    line_words = f' {words(line)} '
    return [term for term in MATCHER.written if f' {term} ' in line_words]


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


def name_span(run, least=2):
    """[(start, end)] of a run of words if, without trailing particles, it has `least` name words."""
    while run and run[-1][2] == 'particle':
        run = run[:-1]
    return [(run[0][0], run[-1][1])] if sum(kind_ != 'particle' for _, _, kind_ in run) >= least else []


def looks_like_email(text: str) -> bool:
    """An address whose "@" the OCR misread, not a sentence ending in ". com": it has a digit,
    a "+", a domain name or a glued domain ("correio.hospital.org.br"), and no exam word."""
    return not any(exam_word_or_typo(word) for word in re.findall(r'\w+', text)) and bool(
        re.search(r'\d|\+|exemplo|example|gmail|hotmail|yahoo|outlook|\w\.\w+\.\w', text))


def mask(text: str) -> tuple[str, dict[str, int]]:
    """Return (masked_text, counts). Only types that were found appear in counts."""
    counts: dict[str, int] = {}

    def substitute(match, kind):
        # One "value..." group per alternative (names are unique): the one that matched is the only one not None.
        group = next(name for name, value in match.groupdict().items() if value is not None)
        if group == 'value_domain' and not looks_like_email(match.group(group)):
            return match.group(0)  # "Glicemia de jejum. com 8h": a sentence, not an e-mail
        counts[kind] = counts.get(kind, 0) + 1
        start, end = (i - match.start() for i in match.span(group))
        return match.group(0)[:start] + f'[{kind}]' + match.group(0)[end:]

    for kind_, pattern in rules.COMPILED:
        text = pattern.sub(functools.partial(substitute, kind=kind_), text)
    return '\n'.join(mask_names(line, counts) for line in text.split('\n')), counts


COUNTED = frozenset(kind for kind, _ in rules.COMPILED) | {'NOME', 'TEXTO_REMOVIDO'}


def mask_page(lines: list[str]) -> tuple[list[str], dict[str, int]]:
    """mask() line by line, then rules 3 (a CPF split in two lines: one CPF), 4 (the safety net) and 5 (by shape). Counts:
    the markers each line leaves with (a value the net took with its text is that [TEXTO_REMOVIDO]; one written, 0)."""
    masked: list[str] = []
    counts: dict[str, int] = {}
    for index, line in enumerate(lines):
        safe, found = mask(line)
        previous = rules.CPF_START.search(lines[index - 1]) if index else None
        rest = rules.CPF_REST.match(safe)
        split_cpf = bool(previous and rest and sum(c.isdigit() for c in previous.group('part') + rest.group('part')) == 11)
        if split_cpf and rest:
            safe = safe[:rest.start('part')] + '[CPF]' + safe[rest.end('part'):]
        masked.append(shaped(only_what_may_leave(safe, found)))
        written, left = ([tag[1:-1] for tag in MASK_TAG.findall(text)] for text in (line, masked[-1]))
        for kind_ in COUNTED:
            amount = left.count(kind_) - written.count(kind_) - (kind_ == 'CPF' and split_cpf)
            if amount > 0:
                counts[kind_] = counts.get(kind_, 0) + amount
    return masked, counts


def shaped(line: str) -> str:
    """Rule 5 of the module docstring, on a line the safety net already went through."""
    line = rules.NAME_TAIL.sub(rules.NAME_TAG, line)
    text = plain(line)
    spans = [match.span() for match in MATCHER.pattern.finditer(text)]
    loose = [token.span() for token in rules.TOKEN.finditer(line, spans[0][0] if spans else len(line)) if goes(token, text, spans)]
    for start, end in reversed(loose):  # a run of them, with only marks between ("4.500.000"), is one piece
        joined = re.match(r'[ \t.,/-]*(?=' + re.escape(rules.REMOVED_TAG) + ')', line[end:])
        line = line[:start] + ('' if joined else rules.REMOVED_TAG) + line[end + (joined.end() if joined else 0):]
    return line


def goes(token: re.Match[str], text: str, spans: list[tuple[int, int]]) -> bool:
    """Rule 5 for a token after an exam's name: a capital or a digit, outside the names (`spans` of plain() `text`), and
    no hour, exam word, qualifier, negation, nor exam word misread ("Antl", "Potassi0")."""
    word, written = fold(token.group()), token.group()
    if not (written[0].isupper() or any(char.isdigit() for char in written)) or rules.HOURS.match(text, token.start()):
        return False
    if any(start <= token.start() < end for start, end in spans) or word in VOCABULARY or word in rules.KEPT or visible(word):
        return False
    return word.isdigit() or not (exam_word_or_typo(word.translate(rules.OCR_DIGITS)) or short_exam_word(word))


def only_what_may_leave(line: str, counts: dict[str, int]) -> str:
    """Rule 4 on one line: each piece that is neither exam-like nor structure is replaced ("Paciente: CPF: Celina
    Inventado" -> "Paciente: CPF: [NOME]"), and in a piece kept as an exam only its words stay ("Anti HCV [NOME]")."""
    pieces = rules.PIECES.split(line)
    for index in range(0, len(pieces), 2):  # pieces at even positions, separators between them
        parts = [pieces[index]] if may_leave(pieces[index]) else rules.JOINED.split(pieces[index])
        for at in range(0, len(parts), 2):
            if not may_leave(parts[at]):
                parts[at] = removed(parts[at], counts)
            elif not is_structure(parts[at]):
                parts[at] = only_exam_words(parts[at], counts)
        pieces[index] = ''.join(parts)
    return ''.join(pieces)


def only_exam_words(piece: str, counts: dict[str, int]) -> str:
    """In a piece kept as an exam, each run of words that are not the exam's (the_exams) becomes [NOME] if it has a
    common first name, else [TEXTO_REMOVIDO]: "Hemograma completo uirá araripe", "Glicose 98765432" (LONG_NUMBER)."""
    tokens = list(re.finditer(r'[^\s-]+', piece))  # "Ferritina-naorealizar": the exam stays, the rest goes
    long_numbers, named = [match.span() for match in rules.LONG_NUMBER.finditer(piece)], after_the_name(tokens)
    outside = [token for token in tokens
               if token in named or not rules.TYPED_TAG.fullmatch(token.group()) and not the_exams(piece, token, long_numbers)]
    # An exam word the OCR split in two ("Colesti erol total"): glued again, it is exam-like, and stays.
    split = {index for index, (first, second) in enumerate(zip(outside, outside[1:], strict=False))
             if not piece[first.end():second.start()].strip() and exam_like(words(first.group() + second.group()))}
    outside = [token for index, token in enumerate(outside) if index not in split and index - 1 not in split]
    runs: list[list[re.Match[str]]] = []
    for token in outside:  # tokens in a row, with only spaces or a hyphen between them, form one run
        if runs and not piece[runs[-1][-1].end():token.start()].strip().strip('-'):
            runs[-1].append(token)
        else:
            runs.append([token])
    for run in reversed(runs):
        piece = piece[:run[0].start()] + counted_tag(' '.join(token.group() for token in run), counts) + piece[run[-1].end():]
    return piece


def the_exams(piece: str, token: re.Match[str], long_numbers: list[tuple[int, int]]) -> bool:
    """Whether a token of a piece kept as an exam is the exam's: no long number, and words of the exam or the order."""
    if any(start < token.end() and token.start() < end for start, end in long_numbers):
        return False
    return all(word in rules.STRUCTURE or word in rules.UNITS or rules.AMOUNT.fullmatch(word) or exam_like(word)
               or short_exam_word(word) or visible(word) for word in words(token.group()).split()) or (
        short_cue(piece, token) or roman_one(piece, token))


def after_the_name(tokens: list[re.Match[str]]) -> list[re.Match[str]]:
    """The tokens of a name after an exam's whole name ("Ferritina Albina Ferro"): from the first that neither qualifies
    the exam (or is one OCR error from a qualifier) nor is an exam word, structure, a unit or an amount, up to the next
    connective; one word alone only capitalized and not exam-like: "TSH Zé" goes, "Proteina C reotiva" stays."""
    plain, at, named = [words(token.group()) for token in tokens], 0, False
    while at < len(plain):
        size = max((n for n in range(1, 7) if ' '.join(plain[at:at + n]) in MATCHER.written), default=0)
        if size or not named or all(word in QUALIFIERS or word in VOCABULARY or word in rules.STRUCTURE or visible(word)
                                    or word in rules.UNITS or rules.AMOUNT.fullmatch(word)
                                    or difflib.get_close_matches(word, QUALIFIERS, 1, MAY_LEAVE.cutoff) for word in plain[at].split()):
            at, named = at + (size or 1), (named or size > 0) and plain[at] not in ('', 'e')  # a new exam may follow
            continue
        run = tokens[at:next((i for i in range(at, len(plain)) if plain[i] in ('', 'e')), len(plain))]
        return run if len(run) > 1 or run[0].group().istitle() and not exam_like(plain[at]) else []
    return []


def roman_one(piece: str, token: re.Match[str]) -> bool:
    """Whether the token is the "I" of the exam before it, read "l": "Troponina l", "Urina tipo l"."""
    return token.group() == 'l' and f' {words(piece[:token.start()])} i'.endswith(
        tuple(f' {term}' for term in MATCHER.written if term.endswith(' i')))


@functools.lru_cache(maxsize=65536)
def short_exam_word(word: str) -> bool:
    """Inside an exam, a 2-4 letter word one letter off an exam word ("lgM", "Arti"); never a common first name."""
    return 2 <= len(word) <= 4 and word not in rules.FIRST_NAMES and any(
        len(exam) == len(word) and sum(a != b for a, b in zip(exam, word, strict=True)) == 1 for exam in VOCABULARY)


def is_structure(piece: str) -> bool:
    """Only the order's structure around masked values, or what it says of its exams: "CPF: [CPF]", "não precisa"."""
    return all(word in rules.STRUCTURE or (word.isdigit() and len(word) <= 2) or visible(word)
               for word in words(rules.TYPED_TAG.sub(' ', piece)).split()) and not rules.LONG_NUMBER.search(piece)


def short_cue(piece: str, token: re.Match[str]) -> bool:
    """Whether the token is a short "não" or "sem" (pii_rules.SHORT_CUE), marks before it aside: "(n/"."""
    return (found := rules.SHORT_CUE.search(piece, token.start())) is not None and found.start() < token.end()


def visible(word: str) -> bool:
    """A negation, history or exception word (pii_rules.VISIBLE): never removed, never a name."""
    return bool(rules.VISIBLE.fullmatch(fold(word)))


def may_leave(piece: str) -> bool:
    """Structure, an exam (a whole name or only exam words), or as close to one as the RAG accepts with an exam-like
    word in it ("Hemogrma compieto"; not "MARIA DO RIBEIRO", 0.6 from "Hormônio do crescimento"). One word alone must
    be exam-like or start an exam word: "Lima" stops, "Ferrit." stays."""
    text = rules.TYPED_TAG.sub(' ', piece)
    rest = words(text).split()
    if is_structure(piece) or exams_on(text) or all(word in VOCABULARY or word in rules.STRUCTURE for word in rest):
        return True  # "D ultrassensível" of "25(OH)D ultrassensível" is only exam words
    if len(rest) == 1:
        return exam_like(rest[0]) or (len(rest[0]) >= 4 and any(word.startswith(rest[0]) for word in VOCABULARY))
    return any(exam_like(word) for word in rest if word not in rules.PARTICLES) and MATCHER.search_score(text) >= MIN_SCORE


@functools.lru_cache(maxsize=65536)
def exam_like(word: str) -> bool:
    """An exam word, exact however short ("T3", "19" of CA 19-9), or misread (MAY_LEAVE: "Urlna", "Potassi0"); never a
    common first name ("Márcia" is 0.77 from "parcial"; no first name is an exam word)."""
    forms = {word, word.translate(rules.OCR_DIGITS)} if any(char.isalpha() for char in word) else {word}
    return any(form not in rules.FIRST_NAMES and MATCHER.is_exam_word(form, MAY_LEAVE) for form in forms)


def has_first_name(text: str) -> bool:
    """Whether the text has a common first name (prenomes.txt): what makes removed text a name."""
    return any(fold(re.split(r"['’-]", word)[0]) in rules.FIRST_NAMES for word in rules.WORD.findall(text))


def removed(piece: str, counts: dict[str, int]) -> str:
    """The piece without its text, but for the negation, history and exception words in it: "não
    tomar café" -> "não [TEXTO_REMOVIDO]". Each stretch between them is replaced."""
    kept = [match for match in rules.WORD.finditer(piece) if visible(match.group()) or rules.SHORT_CUE.match(piece, match.start())]
    out, at = [], 0
    for start, end in [(match.start(), match.end()) for match in kept] + [(len(piece), len(piece))]:
        stretch = piece[at:start]
        out += [replaced(stretch, counts) if re.search(r'[^\W_]', stretch) else stretch, piece[start:end]]
        at = end
    return ''.join(out)


def counted_tag(text: str, counts: dict[str, int]) -> str:
    """[NOME] if removed text has a common first name, else [TEXTO_REMOVIDO]; counted."""
    tag = 'NOME' if has_first_name(text) else 'TEXTO_REMOVIDO'
    counts[tag] = counts.get(tag, 0) + 1
    return f'[{tag}]'


def replaced(piece: str, counts: dict[str, int]) -> str:
    """counted_tag() of the piece; marks around it stay."""
    before, after = rules.MARKS_BEFORE.match(piece), rules.MARKS_AFTER.search(piece)  # both always match, maybe empty
    return (before.group() if before else '') + counted_tag(piece, counts) + (after.group() if after else '')
