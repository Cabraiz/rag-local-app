"""The whole order checked in code after the run, whatever the model searched.

Each line read (PII already masked) loses its list marker ("2.", "-") and its label ("Exames:"),
and lines of notes and personal data are left out. The catalog search takes the rest of each line
and cuts it into its exams itself (mcp_servers/rag.py, split_exams: "e", ",", ";", "+", "/", an "e"
the OCR glued to a word as in "TSHe T4 livre", a catalog name such as "HIV antigeno e anticorpos"
kept whole), each hit with its "piece": the
pieces checked here are the pieces the search uses. A piece whose untied best match passes the RAG's floor,
and is more than a resemblance of letters (it shares a word with the exam's name, or scores at
least 0,80: "laboratorio" is 0,70 like "paratormonio"), is an exam of the order, and must end in
one reported state: booked, asked, left out for its
confidence, line already used, left out by the agent, or, when no exam holds its text and its
code was neither booked nor reported, "não buscado pelo agente". On a line that says not to do the
exam, or that it was done already, or that only prepares for it (the OCR's line_intent), it is
reported with that reason instead, which is no warning about the agent. No exam the search finds
ends in silence. Nothing is booked here.
"""
import re
from collections.abc import Callable, Iterable

from catalogo import LIST_MARKER, exam_word, words

from .confianca import (
    NOT_ANCHORS,
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

MARKER = re.compile(rf'{LIST_MARKER.pattern}|^\s*(?:\d{{1,2}}\s+(?=[^\W\d_])|[A-Za-z][.)])\s*', re.I)  # "1 TGP", "A."
MASKED = re.compile(r'\[[A-Z_]+\]')  # what the OCR masked: [NOME], [CPF], [TEXTO_REMOVIDO]...
# The first word of a label before ":": one that opens a list of exams, a note around them (its text
# is checked, but only a close match counts there: "Obs.: acrescentar Ferritina" is Ferritina, "Obs:
# jejum de 8 horas" is not Proteinúria de 24 horas) and personal data (its value is left out, with or without
# the colon: "Dr. [NOME] - [CRM]"). A line the OCR joined keeps each part: "Paciente: [NOME] Exames: TSH".
LISTS = {'exame', 'exames', 'solicito', 'solicitacao', 'solicitados', 'solicitamos', 'pedido', 'pedidos',
         'requisicao', 'realizar'}
NOTES = {'obs', 'observacao', 'observacoes', 'orientacao', 'orientacoes', 'preparo', 'indicacao', 'diagnostico',
         'hipotese', 'cid'}
DATA = {'paciente', 'nome', 'data', 'nascimento', 'medico', 'medica', 'dr', 'dra', 'crm', 'rg', 'cpf', 'cns',
        'convenio', 'endereco', 'telefone', 'celular', 'email', 'assinatura', 'carimbo', 'local'}
LABELS = LISTS | NOTES | DATA
FOLLOW_UP = {'rotina', 'controle', 'urgente'}  # qualifiers of when, never of which exam: no help to the search

def exams_only(text: str) -> str:
    """Every other word made a separator, so each exam reaches the search on its own, whatever is written
    around it: "solicito TSH", "Não deixar de fazer TSH", "Ferritina somente se hemoglobina baixa", "TSH controle"."""
    return re.sub(r'[^\W_]+', lambda word: word.group() if word.group().isdigit() or (
        (plain := words(word.group())) not in FOLLOW_UP and exam_word(plain)) else ',', text)


# A parenthesis opened after a word ("Vitamina D (incluir também Ferritina)") holds a clause of its own: the
# exam inside it is checked on its own. "25(OH)D", glued, stays one name.
ASIDE = re.compile(r'(?<=\s)\(|\)(?=\s|$)')
# A time after an exam ("TSH em 30 dias", "após 3 meses") is not part of its name: a separator too.
WHEN = re.compile(r'\b(?:em|ap[oó]s|daqui a|dentro de)\s+\d+\s*(?:dias?|semanas?|m[eê]s(?:es)?|anos?|horas?)\b',
                  re.IGNORECASE)


def first_word(text: str) -> str:
    return (words(text).split() or [''])[0]


def is_label(text: str) -> bool:
    return first_word(text) in LABELS or words(text) == 'e mail'


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
        for label, value in parts(MARKER.sub('', MASKED.sub(' ', str(line)))):
            if first_word(label) in DATA or words(label) == 'e mail':
                continue
            text = WHEN.sub(',', ASIDE.sub(',', re.sub('[:|]', ',', MARKER.sub('', value))))  # a table's cells too
            if not label and first_word(text) in DATA:  # "Dr. [NOME] - [CRM]": the label without a colon
                text = text.split(None, 1)[1] if len(text.split()) > 1 else ''
            text = MARKER.sub('', exams_only(text)).strip(' ,')
            if re.search(r'[^\W\d_]{2}', text):  # the search's longest query
                lines.append((index, ' '.join(text.split())[:200], first_word(label or text) in NOTES))
    return lines


def taken(claimed: list[Accounted], texts: list[str], line: int, start: int, end: int) -> bool:
    """Whether a piece is text a proposed exam stands on as that exam: the piece's words, or the
    exam they match, are words of that exam's name ("Proteína OC" on the line of a Proteína C
    reativa). The rest of a line the exam only shares stays free: "Triglicerideos" after a
    "Colesterol total" that took the whole line as the model searched it."""
    return any(other.line == line and other.start < end and start < other.end
               and any(pieces_of(text, [other.exam]) for text in texts) for other in claimed)


def unreported(order: OrderRecord, hits_of: Callable[[str], object], policy: BookingPolicy,
               settled: Iterable[str | None]) -> list[Item]:
    """The exams of the order that ended in no reported state, as left out items (reason
    'not_searched', or 'omitted' when a search returned the code but the model did not propose it).
    hits_of(text): the catalog search's hits for a line (each with its "piece" when the search split
    it); settled: the codes booked or already reported.
    One report per exam, at the confidence the order gives it there (search score, OCR reading)."""
    lines, read, readings = order.ocr_lines or [], order.ocr_read or [], order.ocr_confidence
    claimed = list(order.accounted or [])  # the text the proposed exams stand on
    candidates, done = order.candidates or {}, set(settled)
    reported: list[Item] = []
    for index, text, note, hits in ((index, piece, note, hits) for index, line, note in order_lines(read)
                                    for piece, hits in by_piece(line, hits_of(line)).items()):
        best = untied_best(hits)
        if best is None:
            continue
        query, name = words(text), words(best.name)
        starts = f'{query} '.startswith(f'{name} ')  # "TSH em 30 dias"
        # a line that says something of its exam is checked in full, even under a note's label ("Preparo:")
        note = note and intent_of(order, index) not in (*NOT_ANCHORS, 'uncertain')
        if best.score < RESEMBLANCE and not starts and (note or not shares_a_word(query, name)):  # in a note, a close match or the name first
            continue
        spots = pieces_of(query, [lines[index]]) if index < len(lines) else []
        start, end = spots[0][1:3] if spots else (0, len(lines[index]) if index < len(lines) else 0)
        if best.code in done or taken(claimed, [query, name], index, start, end):
            continue
        reading = reading_at(readings, index, policy.ocr_floor(query, name), policy)
        kind = intent_of(order, index)
        reported.append({'code': best.code, 'name': best.name, 'line': index,
                         'reason': kind if kind in NOT_ANCHORS else 'omitted' if best.code in candidates else 'not_searched',
                         'confidence': round(min(best.score, reading), 2), 'read': read[index]})
        done.add(best.code)
        claimed.append(Accounted(index, start, end, None, name))
    return reported
