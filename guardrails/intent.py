"""What each line of an order asks for, read before the PII safety net strips its words.

mcp_servers/ocr.py runs read_page() on the lines after the injection guard and before the PII mask,
and sends the kinds out as `line_intent`, one per returned line. The rule is conservative, as fits a
medical booking: when the line is not plainly a request, the exam is asked or reported, never booked
silently and never dropped silently. The kinds:

- 'negated': the line says not to do the exam ("não realizar Ferritina", "PSA total - não repetir",
  "Ferritina: não", "não-realizar", "não necessário", "suspender", "cancelado", "desmarcar", "vetado",
  "não autorizado", "sem Ferritina", "todos menos PSA"). Never booked; reported.
- 'history': the line says the exam was done, or reports its result ("já realizado em 2025", "feita mês
  passado", "realizado dia 10/03", "resultado anterior de TSH", "Resultado de Ferritina: 45"). Never booked; reported.
- 'uncertain': the line has a negation, history or exception word that is not plainly about its exam
  ("TSH e T4 livre - não repetir T4 livre", "exceto Ferritina" after other exams, "Paciente trouxe PSA",
  "Ferritina - controle após suspensão do ferro"), carries a value with a lab unit ("Ferritina 45 ng/mL":
  a result, or a target?), or sits next to a line that is only a negation
  ("Não realizar:" above it, "(suspensa)" below it, "retirar o item 2"). Asked [s/N], never booked alone.
- 'prep': a preparation line ("Preparo: ...", "jejum de 8 horas para Glicemia de jejum"): it names the
  exam without asking for it. Never booked from it; reported.
- 'note': a note ("Obs:", "Nota:", "Orientação:") or a line addressed to whoever reads the order
  ("considere", "leve em conta"): asked at most. The doctor's own "solicito" there is a request.
- 'request': anything else.

A cue is plainly about the exam when the exam comes right after it, with only fillers between ("não
realizar o exame de Ferritina"), or when it ends the line after the exam ("Ferritina (suspensa)") and no
other exam is on the line. It is plainly about something else, and ignored, when a word of a known
preparation or clinical context follows it ("não tomar café", "não está em jejum", "sem plaquetas",
"suspender metformina", "resultado anterior alterado", "não deixar de fazer"). Any other word after it
leaves the line uncertain. The cue words are no personal data: the safety net keeps them (guardrails/
pii_rules.py, VISIBLE), so the model and the CLI read "Obs: NAO realizar Ferritina" too.

Matching is on the line without accents or case, with OCR misreadings of the cue words ("NA0",
"reallzar"); exam names come from the catalog (catalogo.py), as the PII guard reads them.
"""
import re

from catalogo import fold, words
from guardrails.pii import EXAM_TERMS, exam_like
from guardrails.pii_rules import EXAM_MODIFIERS, PARTICLES

KINDS = ('request', 'negated', 'history', 'uncertain', 'result', 'note', 'prep', 'unrecognized')
BLOCKING = ('negated', 'history')  # never booked from this line

_NOT = r'n[a4][o0]'
_NOT_SHORT = r'(?:n[a4][o0]|n/|n(?=\s))'  # "n/ realizar", "ñ fazer" (plain() reads "ñ" as "n"), before a verb only
_SEP = r'[\s_-]+'  # "não realizar", "não-realizar", "nao_realizar"
_DONE = r'(?:r[e3]a[l1i]{1,2}[zs]ad[oa]s?|feit[oa]s?|colhid[oa]s?|coletad[oa]s?|dosad[oa]s?)'
_DO = (r'(?:r[e3]a[l1i]{1,2}[zs]\w*|faz\w*|fa[cz]a\w*|feit\w*|repet\w*|refaz\w*|colh\w*|colet\w*|dos[ae]\w*|'
       r'solicit\w*|ped\w*|agend\w*|marc\w*|inclu\w*|autoriz\w*|liberad\w*)')
_MODAL = r'(?:(?:e|eh)\s+(?:necessario|preciso)|precisa\w*(?:\s+de)?|deve\w*|pode\w*|vai|mais)'
# A time after "feito"/"realizado": "feita mês passado", "realizado há 2 meses", "feito ontem", "dia 10/03".
_WHEN = (r'(?:ontem|anteontem|hoje|(?:n[ao]\s+)?(?:semana|mes|ano)\s+passad[oa]|ha\s+\d+\s+[a-z]+|'
         r'(?:(?:no\s+)?dia\s+)?\d{1,2}/\d{1,4}(?:/\d{2,4})?)')
# (kind, cue, how many filler words may come between the cue and the exam, whether it may end the line)
CUES = [
    ('negated', re.compile(rf'\b{_NOT_SHORT}{_SEP}(?:{_MODAL}\s+)?{_DO}'), 3, True),  # não realizar, n/ realizar
    ('negated', re.compile(rf'\b{_NOT}{_SEP}(?:(?:e|eh)\s+)?(?:necessari\w*|precis[ao]\w*|indicad\w*)'), 3, True),
    ('negated', re.compile(rf'\b{_NOT}\b'), 1, True),  # "NÃO: Ferritina", "não o TSH", "Ferritina: não"
    ('negated', re.compile(r'\b(?:suspen[ds]\w*|cancel\w*|desmarc\w*|dispens\w*|evit\w*|vet(?:ad[oa]s?|ar|e|ou)\b|'
                           r'exclu(?:a|am|ir|ido|ida|idos|idas)\b|retir(?:ar|e|ado|ada)\b|contra\W?indicad\w*|'
                           r'desnecessari\w*|nunca|jamais)'), 3, True),
    ('negated', re.compile(r'\bnr\b'), 1, True),  # "TSH - NR": não realizar
    ('negated', re.compile(r'\b(?:sem|s/)\s*(?:necessidade|indicacao)(?:\s+de)?(?:\s+(?:repetir|realizar|fazer|colher))?'),
     2, True),
    ('negated', re.compile(r'\bsem\b'), 1, False),  # "sem Ferritina"; never "sem plaquetas", "sem contraste"
    ('negated', re.compile(r'\b(?:exceto|excluindo|tirando|(?<!pelo )menos)\b'), 2, False),  # "todos menos PSA"
    ('history', re.compile(rf'\bj[a4]\s+(?:(?:foi|foram)\s+)?(?:{_DONE}|fez|fizemos|realizou|tem)(?:\s+em)?'), 3, True),
    ('history', re.compile(rf'\b{_DONE}\s+em\b'), 3, True),  # "realizado em 03/2025: TSH", "PSA feito em 2025"
    ('history', re.compile(rf'\b{_DONE}\s+{_WHEN}'), 3, True),  # "Ferritina feita mês passado"
    ('history', re.compile(r'\bresultados?\s+(?:de\s+)?(?:anterior\w*|previo\w*|antigo\w*)|'
                           r'\b(?:ultim[oa]s?|anterior\w*)\s+(?:exames?|resultados?|dosage\w*)|\bexames?\s+anterior\w*'),
     4, False),
]
# Any other negation, history or exception word: never a clear decision, only a doubt ('uncertain').
TRIGGERS = re.compile(r'\b(?:n[a4][o0]|nr|nunca|jamais|sem|exceto|menos|excluindo|tirando|suspen[ds]\w*|cancel\w*|'
                      r'retir\w*|desmarc\w*|vet(?:ad[oa]s?|ar|e|ou)|dispens\w*|evit\w*|exclu\w*|contra\W?indicad\w*|'
                      r'desnecessari\w*|r[e3]a[l1i]{1,2}[zs]ad[oa]s?|feit[oa]s?|fez|j[a4]|trouxe|anterior\w*|'
                      r'ultim[oa]s?|colhid[oa]s?|coletad[oa]s?)\b')
# Words that may come between a cue and its exam, or end the line after it: articles, "exame de", a date
# or a year, who decided ("vetado pelo médico", "não autorizado pelo convênio").
FILLERS = {'o', 'a', 'os', 'as', 'de', 'do', 'da', 'dos', 'das', 'um', 'uma', 'e', 'em', 'no', 'na', 'exame', 'exames',
           'dosagem', 'dosagens', 'novamente', 'mais', 'este', 'esse', 'esta', 'essa', 'isso', 'isto', 'tambem',
           'nesta', 'neste', 'nessa', 'nesse', 'vez', 'ano', 'anos', 'mes', 'meses', 'dia', 'dias', 'semana', 'semanas',
           'ha', 'pedido', 'pelo', 'pela', 'foi', 'medico', 'medica', 'convenio', 'plano', 'paciente', 'outras',
           'outros', 'outra', 'outro', 'nenhuma', 'nenhum', 'qualquer'}
# A word after a cue that shows the cue is about a preparation or the clinical context, not the exam.
CONTEXT = {'jejum', 'cafe', 'comer', 'alimento', 'alimentos', 'alimentacao', 'refeicao', 'refeicoes', 'beber', 'bebida',
           'alcool', 'fumar', 'fumante', 'cigarro', 'tomar', 'ingerir', 'medicacao', 'medicacoes', 'medicamento',
           'medicamentos', 'remedio', 'remedios', 'metformina', 'levotiroxina', 'biotina', 'suplemento', 'suplementos',
           'suplementacao', 'sulfato', 'ferroso', 'anticoagulante', 'insulina', 'exercicio', 'exercicios', 'esforco',
           'atividade', 'dialise', 'ejacular', 'ejaculacao', 'relacao', 'relacoes', 'descartar', 'primeira', 'urgencia',
           'pressa', 'falta', 'atraso', 'sintoma', 'sintomas', 'queixa', 'queixas', 'uso', 'plaquetas', 'contraste',
           'conservante', 'melhora', 'esmalte', 'laboratorio', 'clinica', 'domicilio', 'casa', 'alterado', 'alterada',
           'alterados', 'alteradas', 'normal', 'normais', 'elevado', 'elevada', 'baixo', 'alto', 'repetir', 'refazer',
           'controle', 'acompanhamento', 'esquecer', 'deixar', 'falhar', 'fez', 'diabetico', 'diabetica', 'gestante',
           'gravida', 'dor', 'febre', 'problema', 'problemas', 'esta', 'estava', 'urgente', 'carne', 'carnes', 'restricao', 'restricoes', 'alergia',
           'alergias', 'intercorrencia', 'intercorrencias', 'comorbidade', 'comorbidades', 'reacao', 'reacoes'}
# Words of an exam name that never name an exam on their own after a cue ("não fazer jejum").
NOT_AN_EXAM = EXAM_MODIFIERS | PARTICLES | {'exame', 'exames', 'horas', 'fezes', 'de', 'tipo'}
TOKEN = re.compile(r'\[[a-z_]+\]|[a-z0-9]+|[.;!?]|\S')
ITEM = re.compile(r'\bite(?:m|ns)\s+(?:n[o.]?\s*)?(\d{1,2})\b')
REFERENCE = {'item', 'itens', 'acima', 'abaixo', 'seguinte', 'seguintes', 'anterior', 'anteriores'}
# A header over a list ("Não realizar os seguintes:", "Já realizados:"): its cue reaches every item below it.
_BLOCK = re.compile(r'(?::\s*$|\b(?:seguintes?|abaixo|a\s+seguir)\b)')
LIST_ITEM = re.compile(r'^\s*(?:[-–•*·>]+|\(?\d{1,2}\s*[.)\-])\s*\S')
# A box before the exam: an empty one is not ticked (asked); a ticked one is the request.
_EMPTY_BOX = re.compile(r'^\s*(?:[-–•*·>]\s*|\d{1,2}\s*[.)\-]\s*)?(?:\[\s*\]|\(\s*\)|[\u2610\u25a1\u25fb\u2b1c])')
# "realizar apenas TSH" on a line with other exams: the others are not plainly requested.
_ONLY = re.compile(r'\b(?:apenas|somente|so|unicamente|exclusivamente)\b')
_LABEL_NOTE = re.compile(r'^\W*(?:\d{1,2}\W+)?(?:obs\w*|nota\w*|observac\w*|orientac\w*|lembrete\w*|comentario\w*)\b')
_READER = re.compile(r'\b(?:consider\w*|lev(?:e|ar|em)\s+em\s+conta|leitor\w*|automatizad\w*|se\s+possivel|'
                     r'caso\s+(?:necessario|possivel)|a\s+criterio|orientac\w*\s+verbal|a\s+pedido\s+d[aoe]s?|'
                     r'conforme\s+(?:combinad\w*|orientac\w*|solicitad\w*|pedid\w*|conversad\w*))\b')
_LABEL_PREP = re.compile(r'^\W*(?:regras?\s+de\s+|orientac\w*\s+de\s+)?(?:preparo|prep)\b')
_FASTING = re.compile(r'\bjejum\s+(?:minimo\s+)?(?:de\s+)?\d+|\b\d+\s*(?:h|hs|hrs|horas?)\s+de\s+jejum|'
                      r'\bjejum\s+(?:absoluto|minimo|previo)')
# A result, not a request: "Resultado de Ferritina: 45", "Valor de TSH". An exam with a value and a lab unit
# ("Ferritina 45 ng/mL", "HbA1c 7,5%") may be a result or a target: asked. A time ("8h") is no lab unit.
_RESULT_HEAD = re.compile(r'^\W*(?:\d{1,2}\W+)?(?:resultados?|valor(?:es)?|dosagens?\s+anterior\w*)\b')
_VALUE = re.compile(r'\b\d+(?:[.,]\d+)?\s*(?:(?:mg|g|ng|pg|ug|mcg|μg|mui|miu|ui|u|mmol|nmol|pmol|meq)\s*/\s*'
                    r'(?:dl|l|ml|mm3?)\b|%|/\s*mm3?\b)')
# The doctor's own request on a note line is a request: "Obs.: solicito também Ferritina".
_REQUESTED = re.compile(r'\b(?:solicito|solicitamos|peco)\b')
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


def after_cue(text: str, end: int, fillers: int) -> tuple[str, str]:
    """What follows a cue: ('exam', '') within `fillers` filler words, ('end', '') when the line or its
    sentence ends first, or ('word', the first other word)."""
    skipped = 0
    for token in TOKEN.finditer(text, end):
        value = token.group()
        if value in '.;!?':
            return 'end', ''
        if not re.match(r'[a-z0-9]', value):
            continue  # marks
        if value in CONTEXT:  # before the exam check: "suspender sulfato ferroso", not DHEA sulfato
            return 'word', value
        if exam_at(text, token.start()):
            return ('exam', '') if skipped <= fillers else ('word', 'far')  # too far to be plainly about it
        if value in FILLERS or re.fullmatch(r'\d+', value):
            skipped += 1
            continue
        return ('word', value) if skipped <= fillers or value in CONTEXT else ('word', 'far')
    return 'end', ''


def exam_before(text: str, position: int) -> bool:
    return any(exam_at(text, word.start()) for word in re.finditer(r'[a-z0-9]+', text[:position]))


def judge(text: str) -> tuple[set[str], bool, list[int], str]:
    """(clear kinds, doubt, item numbers it refers to, where a cue-only line points: 'next', 'previous',
    'both' or '') of one line. A clear cue is a negation or history plainly about the line's exam; a
    doubt is any other negation, history or exception word not shown to be about something else."""
    clear: set[str] = set()
    doubt, items, points = False, [int(match.group(1)) for match in ITEM.finditer(text)], ''
    line_has_exam = any(exam_at(text, word.start()) for word in re.finditer(r'[a-z0-9]+', text))
    taken: list[tuple[int, int]] = []
    matches = sorted(((kind, match.start(), match.end(), fillers, trailing)
                      for kind, pattern, fillers, trailing in CUES for match in pattern.finditer(text)),
                     key=lambda item: (item[1], item[1] - item[2]))
    generic = [('uncertain', match.start(), match.end(), 2, False) for match in TRIGGERS.finditer(text)]
    for kind, start, end, fillers, trailing in matches + generic:
        if any(start < e and s < end for s, e in taken):
            continue
        taken.append((start, end))
        reach, word = after_cue(text, end, fillers)
        before = exam_before(text, start)
        if reach == 'word' and TRIGGERS.fullmatch(word):
            continue  # "não suspender", "não, nunca": the inner cue is judged on its own
        if reach == 'word' and (word in CONTEXT or not line_has_exam) and word not in REFERENCE:
            continue  # about a preparation or the context ("não tomar café"), or a line with no exam
        if kind != 'uncertain' and not before and (reach == 'exam' or (reach == 'word' and word in REFERENCE)):
            clear.add(kind)  # "não realizar Ferritina", "retirar o item 2"
        elif kind != 'uncertain' and trailing and reach == 'end' and before and not other_exams(text, start):
            clear.add(kind)  # "Ferritina (suspensa)", "PSA total - não repetir"
        elif kind != 'uncertain' and not trailing and reach == 'end' and before:
            continue  # "TSH (resultado anterior: 4,5)": the context of the exam before it
        else:
            doubt = True
        if not line_has_exam and kind in BLOCKING and (reach == 'end' or word in REFERENCE):
            stripped = text.strip()
            points = 'block' if _BLOCK.search(stripped) else 'previous' if stripped.startswith('(') else 'both'
    return clear, doubt, items, points


def other_exams(text: str, position: int) -> bool:
    """Whether the line names more than one exam before this position ("TSH e T4 livre - não repetir")."""
    return len({match.group() for match in EXAMS.finditer(text, 0, position)}) > 1


def head_kind(head: str) -> str:
    """'prep', 'note' or 'request' from the text before the line's first exam."""
    if _LABEL_PREP.search(head) or (_FASTING.search(head) and _FOR_THE_EXAM.search(head)):
        return 'prep'
    if _REQUESTED.search(head):
        return 'request'
    if _LABEL_NOTE.search(head) or _READER.search(head):
        return 'note'
    return 'request'


def read_line(line: str) -> str:
    """The kind of one line, without its neighbours (read_page adds them)."""
    return read_page([line])[0]


def read_page(lines: list[str]) -> list[str]:
    """The kind of each line: what the line itself says, then what a line that is only a negation says
    of its neighbours ("Não realizar:" above an exam, "(suspensa)" below it, "retirar o item 2", a header
    "Não realizar os seguintes:" over a list)."""
    texts = [plain(line) for line in lines]
    judged = [judge(text) for text in texts]
    kinds = [line_kind(text, clear, doubt) for text, (clear, doubt, _, _) in zip(texts, judged, strict=True)]
    numbers = {int(match.group(1)): index for index, text in enumerate(texts)
               if (match := re.match(r'\s*\(?(\d{1,2})\s*[.)\-]', text))}
    for index, (_, _, items, points) in enumerate(judged):
        for target in reached(texts, index, [numbers[number] for number in items if number in numbers], points):
            if 0 <= target < len(kinds) and target != index and kinds[target] in ('request', 'note', 'result'):
                kinds[target] = 'uncertain'
    return kinds


def line_kind(text: str, clear: set[str], doubt: bool) -> str:
    """The kind of one line from what it says itself (judge), its head and its value."""
    first = EXAMS.search(text)
    head = head_kind(text[:first.start()] if first else text)
    if clear:
        return 'negated' if 'negated' in clear else 'history'
    if first and _RESULT_HEAD.search(text[:first.start()]):
        return 'history'
    if head == 'prep':
        return 'prep'
    if doubt or _EMPTY_BOX.match(text) or (first and _ONLY.search(text) and other_exams(text, len(text))):
        return 'uncertain'  # a doubt, a box not ticked, "realizar apenas TSH" next to Ferritina
    if first and _VALUE.search(text, first.end()):
        return 'result'  # a value with a lab unit: a result, or a target?
    return head


def reached(texts: list[str], index: int, items: list[int], points: str) -> list[int]:
    """The lines a line that is only a negation talks about: the items it numbers, the lines next to it,
    or every list item below a header, up to a blank line, a new header or a line that is no item."""
    if items:
        return items
    if points != 'block':
        return ([index - 1] if points in ('previous', 'both') else []) + ([index + 1] if points in ('next', 'both') else [])
    targets = []
    for below in range(index + 1, len(texts)):
        if not texts[below].strip() or texts[below].rstrip().endswith(':') or                 (not LIST_ITEM.match(texts[below]) and below > index + 1):
            break
        targets.append(below)
    return targets


def prose_before_exam(line: str, masked: str) -> bool:
    """Whether text the PII safety net removed comes before the line's first exam: words in another
    language, a sentence to the reader ("Per favore aggiungere anche la Ferritina" -> "[NOME] Ferritina").
    Not a label ("Exames:", "Solicito:"), a list marker, or a word or two of OCR junk: at least 2 words of
    3 letters or more were written there, and none of them stayed."""
    raw, safe = plain(line), plain(masked)
    first_raw, first_safe = EXAMS.search(raw), EXAMS.search(safe)
    if not (first_raw and first_safe):
        return False
    head = re.sub(r'^\s*(?:[-–•*·>]+|\(?\d{1,2}\s*[.)\-])', ' ', safe[:first_safe.start()])
    left = re.sub(r'\[[a-z_]+\]', ' ', head)
    removed = re.search(r'\[(?:nome|texto_removido)\]', head)
    return bool(removed) and not re.search(r'[a-z0-9]', left) and \
        len(re.findall(r'[a-z]{3,}', raw[:first_raw.start()])) >= 2


_CLAUSE = re.compile(r'[(,;:]')
_CONNECTIVE = re.compile(r'\b(?:e|ou|a|o|as|os|de|do|da|com|mais)\b')


def note_from(masked: str) -> int | None:
    """Where, in words(masked), the exams of a request line stop being plainly requested: an exam after the
    first whose own clause (after "(", ",", ";" or ":") holds only text the safety net removed ("Exame:
    Vitamina D (incluir também Ferritina)" -> "Exame: Vitamina D ([TEXTO_REMOVIDO] Ferritina)"), or a
    reported request ("(a pedido do médico, incluir Ferritina)"). From there on, an exam is at most asked;
    the exams before it are booked as usual. None when there is no such exam."""
    safe = plain(masked)
    found = list(EXAMS.finditer(safe))
    for previous, exam in zip(found, found[1:], strict=False):
        between = safe[previous.end():exam.start()]
        clause = between[max((m.end() for m in _CLAUSE.finditer(between)), default=0):]
        bare = _CONNECTIVE.sub(' ', re.sub(r'\[[a-z_]+\]', ' ', clause))
        prose = re.search(r'\[(?:nome|texto_removido)\]', clause) and not re.search(r'[a-z0-9]', bare)
        if prose or _READER.search(between):
            prefix = words(masked[:exam.start()])
            return len(prefix) + 1 if prefix else 0
    return None
