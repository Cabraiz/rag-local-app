"""The whole order checked in code after the run, whatever the model searched: no exam the search finds ends in silence.

Each line read (PII masked) loses its list marker and label, and parts of personal data are left out; the catalog
search cuts the rest into its exams (mcp_servers/rag.py). A piece whose untied best match passes the RAG's floor and is
more than a resemblance of letters is an exam of the order, and must end in a reported state; one no exam holds and
nobody booked or reported is "não buscado pelo agente", or the reason its line gives (negated, history, prep). Nothing
is booked here.
"""
import re
from collections.abc import Callable, Iterable

import catalogo
from catalogo import DATA_LABELS, LIST_MARKER, MASK_TAG, exam_word, words
from leitura import NOT_ANCHORS

from .confianca import (
    RESEMBLANCE,
    BookingPolicy,
    by_piece,
    intent_of,
    pieces_of,
    reading_at,
    shares_a_word,
    untied_best,
)
from .pedido import Accounted, Item, OrderRecord

ITEM_MARKER = re.compile(rf'{LIST_MARKER.pattern}|^\s*(?:\d{{1,2}}\s+(?=[^\W\d_])|[A-Za-z][.)])\s*', re.I)  # "1 TGP", "A."
# A label before ":" opens a part of the line: a list, a note (only a close match counts: "Obs: jejum de 8 horas" is not
# Proteinúria de 24 horas), personal data (left out, colon or not: "Dr. [NOME] - [CRM]"). "Paciente: [NOME] Exames: TSH".
NOTES = catalogo.NOTE_LABELS | catalogo.CLINICAL_LABELS
LABELS = catalogo.LIST_LABELS | NOTES | DATA_LABELS
# A parenthesis opened after a word ("Vitamina D (incluir também Ferritina)") holds a clause of its own: the
# exam inside it is checked on its own. "25(OH)D", glued, stays one name.
ASIDE = re.compile(r'(?<=\s)\(|\)(?=\s|$)')
# A time after an exam ("TSH em 30 dias", "após 3 meses") is not part of its name: a separator too.
WHEN = re.compile(r'\b(?:em|ap[oó]s|daqui a|dentro de)\s+\d+\s*(?:dias?|semanas?|m[eê]s(?:es)?|anos?|horas?)\b',
                  re.IGNORECASE)


def exams_only(text: str) -> str:
    """Every other word made a separator, so each exam reaches the search on its own, whatever is written
    around it: "solicito TSH", "Não deixar de fazer TSH", "Ferritina somente se hemoglobina baixa", "TSH controle"."""
    return re.sub(r'[^\W_]+', lambda word: word[0] if part_of_an_exam(word[0]) else ',', text)


def part_of_an_exam(word: str) -> bool:
    """A number, or a word of an exam's name or of its qualifiers but for those of when ("controle")."""
    return word.isdigit() or words(word) not in catalogo.FOLLOW_UP and exam_word(words(word))


def first_word(text: str) -> str:
    return (words(text).split() or [''])[0]


def is_label(text: str, labels: frozenset[str] = LABELS) -> bool:
    return first_word(text) in labels or words(text) == 'e mail'


def parts(text: str) -> list[tuple[str, str]]:
    """(label, value) of each part of a line: a known label before ":" (one or two words: "Exames
    solicitados:", "Obs.:", "E-mail:") opens a part; any other ":" stays in the text."""
    found: list[tuple[str, str]] = []
    label, start = '', 0
    for colon in re.finditer(':', text):
        before = text[start:colon.start()]
        tail = before.split()
        for size in (2, 1):
            if len(tail) >= size and is_label(' '.join(tail[-size:])):
                found.append((label, before[:before.rindex(tail[-size])]))
                label, start = ' '.join(tail[-size:]), colon.end()
                break
    return [*found, (label, text[start:])]


def order_lines(read: list[str]) -> list[tuple[int, str, bool]]:
    """(line index, text, note) of each part of the order that may name exams, as the search takes it:
    without its marker and label, the value of a personal-data label left out; any other ":" is a
    separator, so its two sides are checked. `note`: the part is the text of a note."""
    lines: list[tuple[int, str, bool]] = []
    for index, line in enumerate(read):
        for label, value in parts(ITEM_MARKER.sub('', MASK_TAG.sub(' ', str(line)))):
            text = '' if is_label(label, DATA_LABELS) else searched_text(label, value)
            if re.search(r'[^\W\d_]{2}', text):  # the search's longest query
                lines.append((index, ' '.join(text.split())[:catalogo.MAX_QUERY_LENGTH], first_word(label or text) in NOTES))
    return lines


def searched_text(label: str, value: str) -> str:
    """A part's value as the search takes it: ":", "|" (a table's cells), an aside and a time are separators; after a
    personal-data word without a colon ("Dr. [NOME] - [CRM]"), only what follows it; only the words of exams."""
    text = WHEN.sub(',', ASIDE.sub(',', re.sub('[:|]', ',', ITEM_MARKER.sub('', value))))
    if not label and first_word(text) in DATA_LABELS:
        text = text.split(None, 1)[1] if len(text.split()) > 1 else ''
    return ITEM_MARKER.sub('', exams_only(text)).strip(' ,')


def taken(claimed: list[Accounted], texts: list[str], line: int, start: int, end: int) -> bool:
    """Whether a proposed exam stands on this piece as that exam: the piece's words, or the exam they match, are words of
    its name ("Proteína OC" under a Proteína C reativa; not "Triglicerideos" under a "Colesterol total" on the line)."""
    return any(other.line == line and other.start < end and start < other.end
               and any(pieces_of(text, [other.exam]) for text in texts) for other in claimed)


def only_a_resemblance(order: OrderRecord, index: int, query: str, name: str, score: float, note: bool) -> bool:
    """Whether a match is no exam of the order: below RESEMBLANCE, its name does not start the words read ("TSH em 30
    dias") and, in a note, it is not a close match, or anywhere it shares no word with them. A line that says something
    of its exam (negated, history, prep, uncertain) is checked in full even under a note's label ("Preparo:")."""
    if score >= RESEMBLANCE or f'{query} '.startswith(f'{name} '):
        return False
    return note and intent_of(order, index) not in (*NOT_ANCHORS, 'uncertain') or not shares_a_word(query, name)


def piece_on(lines: list[str], index: int, query: str) -> tuple[int, int]:
    """Where the words read are on their line: the first piece of it with them, else the whole line."""
    spots = pieces_of(query, [lines[index]]) if index < len(lines) else []
    return spots[0][1:3] if spots else (0, len(lines[index]) if index < len(lines) else 0)


def unreported(order: OrderRecord, hits_of: Callable[[str], object], policy: BookingPolicy,
               settled: Iterable[str | None]) -> list[Item]:
    """The exams of the order in no reported state, as left out items ('not_searched', or 'omitted' when a search returned
    the code), one per exam at the confidence the order gives it. hits_of(text): the catalog search's hits for a line;
    settled: the codes booked or already reported."""
    lines, read = order.ocr_lines or [], order.ocr_read or []
    claimed = list(order.accounted or [])  # the text the proposed exams stand on
    candidates, done = order.candidates or {}, set(settled)
    reported: list[Item] = []
    for index, line, note in order_lines(read):
        for text, hits in by_piece(line, hits_of(line)).items():
            best = untied_best(hits)
            query, name = words(text), words(best.name) if best else ''
            if best is None or only_a_resemblance(order, index, query, name, best.score, note):
                continue
            start, end = piece_on(lines, index, query)
            if best.code in done or taken(claimed, [query, name], index, start, end):
                continue
            reading = reading_at(order.ocr_confidence, index, policy.ocr_floor(query, name), policy)
            kind = intent_of(order, index)
            reason = kind if kind in NOT_ANCHORS else 'omitted' if best.code in candidates else 'not_searched'
            reported.append({'code': best.code, 'name': best.name, 'line': index, 'reason': reason,
                             'confidence': round(min(best.score, reading), 2), 'read': read[index]})
            done.add(best.code)
            claimed.append(Accounted(index, start, end, None, name))
    return reported
