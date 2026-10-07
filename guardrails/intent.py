"""What each line of an order asks for, read before the PII safety net strips its words.

mcp_servers/ocr.py runs read_line() on every line after the injection guard and before the PII
mask, and sends the kinds out as `line_intent`, one per returned line. The safety net keeps only
what looks like an exam, so "Obs: NAO realizar Ferritina" used to leave as "Obs: [NOME] Ferritina":
neither the model nor the booking rule could see the "não". The kinds:

- 'negated': the line says not to do an exam ("não realizar", "não repetir", "suspender", "cancelado",
  "dispensar", "evitar", "sem Ferritina"...). Its exams are never booked.
- 'history': the line says the exam was already done ("já realizado em 2025", "resultado anterior de
  TSH", "último exame: PSA"). Its exams are never booked.
- 'prep': a preparation line ("Preparo: ...", "jejum de 8 horas para Glicemia de jejum"): the exam is
  only the subject of the preparation, never a request on this line.
- 'note': a note ("Obs:", "Nota:", "Orientação:") or a line addressed to whoever reads the order
  ("considere também", "leve em conta"): an exam there is at most asked [s/N], never booked alone.
- 'request': anything else (the default).

A negation or history cue counts only when an exam comes right after it ("não realizar o exame de
Ferritina", "sem Ferritina") or when it ends the line after an exam ("PSA total - não repetir",
"Ferritina (suspensa)"). So "suspender medicação", "não precisa de jejum", "Hemograma sem plaquetas",
"Glicemia de jejum - suspender metformina" and "TSH (resultado anterior: 4,5)" stay requests. Notes
and preparation are read from the head of the line, before its first exam: "Glicemia de jejum (jejum
de 8 horas)" is a request. The cue is replaced in the line by a neutral marker ([NAO_REALIZAR],
[JA_REALIZADO]) that the safety net keeps, so the model reads it too. The kind is per line: a line
that also names another exam ("TSH; não repetir Ferritina") leaves both out, with the reason shown.

Matching is on the line without accents or case, with OCR misreadings of the cue words ("NA0",
"reallzar"); exam names come from the catalog (catalogo.py), as the PII guard reads them.
"""
import re

from catalogo import fold
from guardrails.pii import EXAM_TERMS, exam_like
from guardrails.pii_rules import EXAM_MODIFIERS, PARTICLES

KINDS = ('request', 'negated', 'history', 'note', 'prep', 'unrecognized')
BLOCKING = ('negated', 'history')  # never booked from this line
MARKERS = {'negated': '[NAO_REALIZAR]', 'history': '[JA_REALIZADO]'}

_NOT = r'n[a4][o0]'
_DONE = r'(?:r[e3]a[l1i]{1,2}[zs]ad[oa]s?|feit[oa]s?|colhid[oa]s?|coletad[oa]s?|dosad[oa]s?)'
_DO = (r'(?:r[e3]a[l1i]{1,2}[zs]\w*|faz\w*|fa[cz]a\w*|feit\w*|repet\w*|refaz\w*|colh\w*|colet\w*|dos[ae]\w*|'
       r'solicit\w*|ped\w*|agend\w*|marc\w*|inclu\w*)')
_MODAL = r'(?:(?:e|eh)\s+(?:necessario|preciso)|precisa\w*(?:\s+de)?|deve\w*|pode\w*|vai|mais)'
# (kind, cue, how many filler words may come between the cue and the exam, whether it may end the line)
CUES = [
    ('negated', re.compile(rf'\b{_NOT}\s+(?:{_MODAL}\s+)?{_DO}'), 3, True),  # não realizar, não precisa repetir
    ('negated', re.compile(rf'\b{_NOT}\b'), 1, False),  # "NÃO: Ferritina", "não o TSH"
    ('negated', re.compile(r'\b(?:suspen[ds]\w*|cancel\w*|dispens\w*|evit\w*|exclu(?:a|am|ir|ido|ida|idos|idas)\b|'
                           r'retir(?:ar|e|ado|ada)\b|contra\W?indicad\w*|desnecessari\w*)'), 3, True),
    ('negated', re.compile(r'\bsem\s+(?:necessidade|indicacao)(?:\s+de)?(?:\s+(?:repetir|realizar|fazer|colher))?'),
     2, True),
    ('negated', re.compile(r'\bsem\b'), 1, False),  # "sem Ferritina"; never "sem plaquetas", "sem contraste"
    ('history', re.compile(rf'\bj[a4]\s+(?:(?:foi|foram)\s+)?(?:{_DONE}|fez|fizemos|realizou|tem)(?:\s+em)?'), 3, True),
    ('history', re.compile(rf'\b{_DONE}\s+em\b'), 3, True),  # "realizado em 03/2025: TSH", "PSA feito em 2025"
    ('history', re.compile(r'\bresultados?\s+(?:de\s+)?(?:anterior\w*|previo\w*|antigo\w*)|'
                           r'\b(?:ultim[oa]s?|anterior\w*)\s+(?:exames?|resultados?|dosage\w*)|\bexames?\s+anterior\w*'),
     2, False),
]
# Words that may come between a cue and its exam: articles, "exame de", a date or a year.
FILLERS = {'o', 'a', 'os', 'as', 'de', 'do', 'da', 'dos', 'das', 'um', 'uma', 'e', 'em', 'no', 'na', 'exame', 'exames',
           'dosagem', 'dosagens', 'novamente', 'mais', 'este', 'esse', 'esta', 'essa', 'isso', 'isto', 'tambem',
           'nesta', 'neste', 'nessa', 'nesse', 'vez', 'ano', 'mes', 'dia', 'pedido', 'pelo', 'pela', 'foi'}
# Words of an exam name that never name an exam on their own after a cue ("não fazer jejum").
NOT_AN_EXAM = EXAM_MODIFIERS | PARTICLES | {'exame', 'exames', 'horas', 'fezes', 'de', 'tipo'}
TOKEN = re.compile(r'\[[a-z_]+\]|[a-z0-9]+|[.;!?]|\S')
_LABEL_NOTE = re.compile(r'^\W*(?:\d{1,2}\W+)?(?:obs\w*|nota\w*|observac\w*|orientac\w*|lembrete\w*|comentario\w*)\b')
_READER = re.compile(r'\b(?:consider\w*|lev(?:e|ar|em)\s+em\s+conta|leitor\w*|automatizad\w*|se\s+possivel|'
                     r'caso\s+(?:necessario|possivel)|a\s+criterio)\b')
_LABEL_PREP = re.compile(r'^\W*(?:regras?\s+de\s+|orientac\w*\s+de\s+)?(?:preparo|prep)\b')
_FASTING = re.compile(r'\bjejum\s+(?:minimo\s+)?(?:de\s+)?\d+|\b\d+\s*(?:h|hs|hrs|horas?)\s+de\s+jejum|'
                      r'\bjejum\s+(?:absoluto|minimo|previo)')
_FOR_THE_EXAM = re.compile(r'\b(?:para|antes\s+d[aoe]s?)\s+(?:(?:o|a|os|as)\s+)?(?:exames?\s+(?:de\s+)?)?\W*$')


def plain(text: str) -> str:
    """fold() char by char, so a position in it is the same position in the line: "NÃO" -> "nao"."""
    return ''.join((fold(char) or ' ')[:1] for char in text)


def _term_pattern(term: str) -> str:
    return r'(?<![a-z0-9])' + r'[\W_]+'.join(map(re.escape, term.split())) + r'(?![a-z0-9])'


EXAMS = re.compile('|'.join(_term_pattern(term) for term in sorted(EXAM_TERMS, key=len, reverse=True) if term))


def exam_at(text: str, position: int) -> bool:
    """Whether an exam name starts here: a catalog name or synonym, or a word one OCR error from an
    exam word that is not a modifier of one ("Ferritlna", not "jejum")."""
    if EXAMS.match(text, position):
        return True
    word = re.match(r'[a-z0-9]+', text[position:])
    if word is None:
        return False
    return len(word.group()) >= 3 and word.group() not in NOT_AN_EXAM and exam_like(word.group())


def after_cue(text: str, end: int, fillers: int) -> str | None:
    """'exam' if an exam follows the cue within `fillers` filler words, 'end' if the line (or its
    sentence) ends with no other word, else None."""
    skipped = 0
    for token in TOKEN.finditer(text, end):
        value = token.group()
        if value in '.;!?':
            return 'end'
        if not re.match(r'[a-z0-9]', value):
            continue  # marks, a marker already in the line
        if exam_at(text, token.start()):
            return 'exam'
        if value in FILLERS or re.fullmatch(r'\d+', value):
            skipped += 1
            if skipped > fillers:
                return None
            continue
        return None
    return 'end'


def cues(text: str) -> list[tuple[str, int, int]]:
    """(kind, start, end) of every cue that applies to the line, without overlaps (the longest first)."""
    found: list[tuple[str, int, int]] = []
    matches = sorted(((kind, match.start(), match.end(), fillers, trailing)
                      for kind, pattern, fillers, trailing in CUES for match in pattern.finditer(text)),
                     key=lambda item: (item[1], item[1] - item[2]))
    for kind, start, end, fillers, trailing in matches:
        if any(start < e and s < end for _, s, e in found):
            continue
        reach = after_cue(text, end, fillers)
        exam_before = any(exam_at(text, word.start()) for word in re.finditer(r'[a-z0-9]+', text[:start]))
        if reach == 'exam' or (reach == 'end' and trailing and exam_before):
            found.append((kind, start, end))
    return found


def head_kind(head: str) -> str:
    """'prep', 'note' or 'request' from the text before the line's first exam."""
    if _LABEL_PREP.search(head) or (_FASTING.search(head) and _FOR_THE_EXAM.search(head)):
        return 'prep'
    if _LABEL_NOTE.search(head) or _READER.search(head):
        return 'note'
    return 'request'


def read_line(line: str) -> tuple[str, str]:
    """(kind, the line with each negation or history cue replaced by its marker)."""
    text = plain(line)
    found = cues(text)
    if found:
        kind = 'negated' if any(kind == 'negated' for kind, _, _ in found) else 'history'
        for cue_kind, start, end in sorted(found, key=lambda item: -item[1]):
            line = line[:start] + MARKERS[cue_kind] + line[end:]
        return kind, line
    first = EXAMS.search(text)
    return head_kind(text[:first.start()] if first else text), line
