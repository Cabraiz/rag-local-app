"""What each line of an order asks for, read before the PII safety net strips its words.

mcp_servers/ocr.py runs read_page() on the lines as written, before the injection guard and the PII mask,
and sends the kinds out as `line_intent`, one per returned line. The rule is an allowlist: a line books
alone only if it is nothing but exams. The kinds:

- 'request': after its list marker ("1)", "-", "[x]"), a label of the list ("Exames:", "Solicito:") and
  the marks that join exams, the line holds only catalog names and synonyms, their qualifiers (catalogo.
  QUALIFIERS: "completo", "total", "sérico", "de jejum", "8h"...) and words one OCR error from an exam
  word. It may book alone.
- 'uncertain': any other word or mark ("Ferritina - pedido por engano", "Colesterol total ?", a name, a
  date, an empty box "[ ]"), or a line next to one that is only a negation ("(suspensa)" below it,
  "retirar o item 2"). Asked [s/N], never booked alone.
- 'negated' and 'history': a cue plainly about the exam ("não realizar Ferritina", "TSH - NR", "do not
  perform TSH", "já realizado em 2025", "Resultado de Ferritina: 45"), a box or cell that says no ("[-] TSH",
  "TSH | -"), a line in a block under a header ("Já realizados:", "Resultados anteriores:") or with the mark of
  a footnote that says no ("Ferritina (1)", "(1) suspenso"). Never booked; reported with that reason.
- 'prep': a preparation line ("Preparo: ...", "jejum de 8 horas para Glicemia de jejum"): reported.
- 'table': any other line of a page in a table or in columns (layout()). Asked.
The exams a negated or history line names, or a line with a cue not plainly about one exam or next to a
negation ('instruction'), are contested on the whole page (contested(): reported, or asked).

A cue is plainly about the exam when the exam comes right after it, with only fillers between ("não
realizar o exame de Ferritina"), or when it ends the line after the line's one exam ("Ferritina
(suspensa)"). Any other line with a cue is 'uncertain' by the allowlist. Matching is on the line
without accents or case, with OCR misreadings of the cue words ("NA0", "reallzar", "n/", "ñ").
"""
import difflib
import re

from catalogo import LIST_MARKER, QUALIFIERS, catalog, exam_word, fold, normalize
from guardrails.pii import EXAM_TERMS, exam_like

KINDS = ('request', 'negated', 'history', 'uncertain', 'prep', 'unrecognized', 'table')
BLOCKING = ('negated', 'history')  # never booked from this line

_NOT = r'n[a4][o0]'
_NOT_SHORT = r'(?:n[a4][o0]|n/|n(?=\s))'  # "n/ realizar", "ñ fazer" (plain() reads "ñ" as "n"), before a verb only
_SEP = r'[\s_-]*'  # "não realizar", "não-realizar", "nao_realizar", "naorealizar" (the OCR glued it)
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
    ('negated', re.compile(rf'\b(?:{_NOT}|nr)\b'), 1, True),  # "NÃO: Ferritina", "Ferritina: não", "TSH - NR"
    ('negated', re.compile(r'\b(?:suspen[ds]\w*|cancel\w*|desmarc\w*|dispens\w*|evit\w*|vet(?:ad[oa]s?|ar|e|ou)\b|'
                           r'exclu(?:a|am|ir|ido|ida|idos|idas)\b|retir(?:ar|e|ado|ada)\b|contra\W?indicad\w*|'
                           r'desnecessari\w*|nunca|jamais|anulad[oa]s?|desconsider\w*|remov\w*|elimin\w*)'), 3, True),
    ('negated', re.compile(r'\b(?:do\s+not|don\W?t|not)\s+(?:perform|repeat|do|order|run)\b|\bno\s+(?:realizar|repetir|hacer)\b'),
     3, True),  # English and Spanish
    ('negated', re.compile(r'\b(?:sem|s/)\s*(?:necessidade|indicacao)(?:\s+de)?(?:\s+(?:repetir|realizar|fazer|colher))?'),
     2, True),
    ('negated', re.compile(r'\b(?:sem|exceto|excluindo|tirando|(?<!pelo )menos)\b'), 2, False),  # "todos menos PSA"
    ('history', re.compile(rf'\bj[a4]\s+(?:(?:foi|foram)\s+)?(?:{_DONE}|fez|fizemos|realizou|tem)(?:\s+em)?'), 3, True),
    ('history', re.compile(rf'\b{_DONE}\s+(?:em\b|{_WHEN})'), 3, True),  # "realizado em 03/2025", "feita mês passado"
    ('history', re.compile(r'\bresultados?\s+(?:de\s+)?(?:anterior\w*|previo\w*|antigo\w*)|'
                           r'\b(?:ultim[oa]s?|anterior\w*)\s+(?:exames?|resultados?|dosage\w*)|\bexames?\s+anterior\w*'),
     4, False),
]
# Words that may come between a cue and its exam, or end the line after it: articles, "exame de", a date
# or a year, who decided ("vetado pelo médico", "não autorizado pelo convênio").
FILLERS = {'o', 'a', 'os', 'as', 'de', 'do', 'da', 'dos', 'das', 'um', 'uma', 'e', 'em', 'no', 'na', 'exame', 'exames',
           'dosagem', 'novamente', 'mais', 'este', 'esse', 'esta', 'essa', 'isso', 'tambem', 'vez', 'ano', 'anos', 'mes',
           'meses', 'dia', 'dias', 'ha', 'pedido', 'pelo', 'pela', 'foi', 'medico', 'medica', 'convenio', 'plano'}
NOT_AN_EXAM = QUALIFIERS | {'exame', 'exames', 'horas', 'fezes'}  # never an exam on their own after a cue
TOKEN = re.compile(r'\[[a-z_]+\]|[a-z0-9]+|[.;!?]|\S')
ITEM = re.compile(r'\bite(?:m|ns)\s+(?:n[o.]?\s*)?(\d{1,2})\b')
REFERENCE = {'item', 'itens', 'acima', 'abaixo', 'seguinte', 'seguintes', 'anterior', 'anteriores'}
_BLOCK = re.compile(r'(?::\s*$|\b(?:seguintes?|abaixo|a\s+seguir)\b)')  # "Não realizar os seguintes:", "Já realizados:"
_LABEL_PREP = re.compile(r'^\W*(?:regras?\s+de\s+|orientac\w*\s+de\s+)?(?:preparo|prep)\b')
_FASTING = re.compile(r'\bjejum\s+(?:minimo\s+)?(?:de\s+)?\d+|\b\d+\s*(?:h|hs|hrs|horas?)\s+de\s+jejum|'
                      r'\bjejum\s+(?:absoluto|minimo|previo)')
_FOR_THE_EXAM = re.compile(r'\b(?:para|antes\s+d[aoe]s?)\s+(?:(?:o|a|os|as)\s+)?(?:exames?\s+(?:de\s+)?)?\W*$')
_RESULT_HEAD = re.compile(r'^\W*(?:\d{1,2}\W+)?(?:resultados?|valor(?:es)?|dosagens?\s+anterior\w*)\b')  # a result
# The allowlist: a ticked box, a label of the list or the doctor's own verb at the start, the marks that
# join exams. An empty box ("[ ]", "( )", "☐") is a word of its own: not ticked.
_TICKED = re.compile(r'^\s*(?:\[\s*[x✓✔]\s*\]|\(\s*[x✓✔]\s*\)|[☑☒✓✔①-⑳])\s*', re.I)
_LIST_LABEL = re.compile(r'^\s*(?:(?:exames?|pedido|requisicao|solicitacao)(?:\s+(?:de\s+)?(?:exames?|solicitad[oa]s?|'
                         r'laboratoria(?:l|is)|de\s+rotina))*\s*:|(?:solicito|solicitamos|peco)(?:\s+os\s+exames)?'
                         r'\s*:?|(?:realizar|fazer|dosar|repetir|refazer|coletar|colher|novo|nova)\b\s*:?)\s*')
LABEL_WORDS = ('solicito', 'exames', 'exame', 'pedido')
_JOINERS = re.compile(r'[\s,;/+:.()\[\]{}–-]+')
_NO_BOX = re.compile(r'^\s*(?:\[\s*[-–—✗✘]\s*\]|\(\s*[-–—✗✘]\s*\)|[✗✘])')
_NO_CELL = re.compile(r'\|\s*(?:(?:-+|[–—✗✘]|n[a4][o0]|n)\s*\|?|\|)\s*$')  # a row's last cell: no, or blank


def plain(text: str) -> str:
    """fold() char by char, so a position in it is the same position in the line: "NÃO" -> "nao"."""
    return ''.join((fold(char) or ' ')[:1] for char in text)


def _term_pattern(term: str) -> str:
    return r'(?<![a-z0-9])' + r'[\W_]+'.join(map(re.escape, term.split())) + r'(?![a-z0-9])'


EXAMS = re.compile('|'.join(_term_pattern(term) for term in sorted(EXAM_TERMS, key=len, reverse=True) if term))


def exam_at(text: str, position: int) -> bool:
    """Whether an exam name starts here: a catalog name or synonym, or a word one OCR error from an
    exam word that is not a qualifier of one ("Ferritlna", not "jejum")."""
    word = re.match(r'[a-z0-9]+', text[position:])
    return bool(EXAMS.match(text, position)) or (
        word is not None and len(word.group()) >= 3 and word.group() not in NOT_AN_EXAM and exam_like(word.group()))


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
        if exam_at(text, token.start()):
            return ('exam', '') if skipped <= fillers else ('word', 'far')  # too far to be plainly about it
        if (value in FILLERS or value.isdigit()) and skipped < fillers:
            skipped += 1
            continue
        return 'word', value
    return 'end', ''


def exam_before(text: str, position: int) -> bool:
    return any(exam_at(text, word.start()) for word in re.finditer(r'[a-z0-9]+', text[:position]))


def judge(text: str) -> tuple[set[str], list[int], str, set[str]]:
    """(clear kinds, item numbers it refers to, where a cue-only line points: 'block', 'previous', 'both'
    or '', its cues' kinds) of one line; "Já realizados: TSH" is a 'block' too. A clear cue is plainly about it."""
    clear: set[str] = set()
    items, points, cues = [int(match.group(1)) for match in ITEM.finditer(text)], '', set()
    taken: list[tuple[int, int]] = []
    line_has_exam = exam_before(text, len(text))
    for kind, start, end, fillers, trailing in sorted(
            ((kind, match.start(), match.end(), fillers, trailing)
             for kind, pattern, fillers, trailing in CUES for match in pattern.finditer(text)),
            key=lambda item: (item[1], item[1] - item[2])):
        if any(start < e and s < end for s, e in taken):
            continue
        taken.append((start, end))
        cues.add(kind)
        reach, word = after_cue(text, end, fillers)
        before = exam_before(text, start)
        if not before and (reach == 'exam' or word in REFERENCE):
            clear.add(kind)  # "não realizar Ferritina", "retirar o item 2"
        elif trailing and reach == 'end' and before and not other_exams(text, start):
            clear.add(kind)  # "Ferritina (suspensa)", "PSA total - não repetir"
        if not line_has_exam and kind in BLOCKING and (reach == 'end' or word in REFERENCE):
            stripped = text.strip()
            points = 'block' if _BLOCK.search(stripped) else 'previous' if stripped.startswith('(') else 'both'
    head, colon, rest = text.partition(':')  # a header glued to its first item, which has no cue of its own
    points = points or ('block' if cues and rest.strip() and judge(head + colon)[2] == 'block' and not judge(rest)[3] else '')
    return clear, items, points, cues


def other_exams(text: str, position: int) -> bool:
    """Whether the line names more than one exam before this position ("TSH e T4 livre - não repetir")."""
    return len({match.group() for match in EXAMS.finditer(text, 0, position)}) > 1


def residue(line: str) -> list[str]:
    """What a line holds besides exams, as read (before the PII mask, which would hide it): [] for "1)
    Hemograma completo e TSH", ['pedido', 'por', 'engano'] for "- Ferritina - pedido por engano", ['?']
    for "Colesterol total ?", ['='] for "=Creatinina", ['[instrucao_removida]'] for a line whose order to
    the model was removed."""
    text = _TICKED.sub(' ', LIST_MARKER.sub(' ', plain(line), count=1), count=1)
    head = re.match(r'\s*(?:\d{1,2}\s*[;:]?\s+(?=[a-z])|([a-z]+)\s*:)', text)  # "4 TGP", "5; TGO", "Solreito:"
    if head and (not head.group(1) or difflib.get_close_matches(head.group(1), LABEL_WORDS, 1, 0.7)):
        text = text[head.end():]  # a list number without its mark, or a label of the list the OCR misread
    text = re.sub(r'^\s*\d{1,2}\s*[;:]?\s+(?=[a-z])', ' ', _LIST_LABEL.sub(' ', text, count=1))  # "Solicito: 4 TGP"
    text = re.sub(r'(?<=\w)-(?=\s|$)|\[\s*\]|\(\s*\)|[☐□◻⬜]', ' = ', text)  # "Creatinina-", "[ ]"
    text = re.sub(r'(?<![\w-])\d{1,2}\s*(?:h|hs|hrs|horas?)(?![a-z])', ' ', EXAMS.sub(' ', text))  # "jejum de 8h"
    left = re.findall(r'\[[a-z_]+\]', text)  # a marker of the injection guard, or one written on the image
    return left + [token for token in re.findall(r'[a-z0-9]+|\S', _JOINERS.sub(' ', re.sub(r'\[[a-z_]+\]', ' ', text)))
                   if not exam_word(token)]


def line_kind(line: str, text: str, clear: set[str]) -> str:
    """The kind of one line from what it says itself."""
    first = EXAMS.search(text)
    head = text[:first.start()] if first else text
    if clear:
        return 'negated' if 'negated' in clear else 'history'
    if first and (_NO_BOX.match(text) or _NO_CELL.search(text)):  # "[-] TSH", "TSH | -", "| TSH | |"
        return 'negated'
    if first and _RESULT_HEAD.search(head):
        return 'history'
    if _LABEL_PREP.search(head) or (_FASTING.search(head) and _FOR_THE_EXAM.search(head)):
        return 'prep'
    return 'uncertain' if first and residue(line) else 'request'


def read_page(lines: list[str]) -> tuple[list[str], list[str | None]]:
    """(each line's kind, why the exams it names are contested on the page or None): what the line says, then
    what a cue-only line says of others ("(suspensa)", "retirar o item 2", a footnote, a block), then layout()."""
    texts = [plain(line) for line in lines]
    judged = [judge(text) for text in texts]
    kinds = [line_kind(line, text, clear) for line, text, (clear, *_) in zip(lines, texts, judged, strict=True)]
    contest = [kind if kind in BLOCKING else 'instruction' if cues else None for kind, (*_, cues) in zip(kinds, judged, strict=True)]
    numbers = {int(match.group(1)): index for index, text in enumerate(texts)
               if (match := re.match(r'\s*\(?(\d{1,2})\s*[.)\-]', text))}
    for index, (_, items, points, cues) in enumerate(judged):
        targets, plainly = reached(texts, index, [numbers[number] for number in items if number in numbers], points)
        for target in (target for target in targets if 0 <= target < len(kinds) and target != index):
            if plainly:  # "Ferritina (1)" and "(1) suspenso"; a line under "Já realizados:"
                kinds[target] = contest[target] = 'negated' if 'negated' in cues else 'history'
            elif kinds[target] in ('request', 'uncertain'):
                kinds[target], contest[target] = 'uncertain', contest[target] or 'instruction'
    return [('table' if kind in ('request', 'uncertain') else kind) for kind in kinds] if layout(texts) else kinds, contest


def reached(texts: list[str], index: int, items: list[int], points: str) -> tuple[list[int], bool]:
    """(the lines a cue-only line talks about, whether plainly): the items it numbers or the lines next to it;
    plainly, the lines with its footnote's mark, or every line below a header up to a blank or a new header."""
    mark = re.match(r'\s*(\(\w{1,2}\)|\*+)', texts[index]) if points else None
    marked = [i for i, text in enumerate(texts) if mark and i != index and re.search(rf'\w\s*{re.escape(mark[1])}', text)]
    if marked or items or points != 'block':
        return marked or items or ([index - 1] if points in ('previous', 'both') else []) + (
            [index + 1] if points == 'both' else []), bool(marked)
    targets = []
    for below in range(index + 1, len(texts)):
        label = re.match(r'\s*([a-z][a-z .]{0,30}):', texts[below])
        if not texts[below].strip() or texts[below].rstrip().endswith(':') or label and not exam_before(label[1], 99):
            break
        targets.append(below)
    return targets, True


# A table or columns: a yes/no header ("Realizar?"), 3 headers ("Exame Fazer Obs"), 2 rows ("TSH | -", "TSH Sim").
_YES_NO = re.compile(r'\b(?:realizar|fazer)\s*\?|\bs(?:im)?\s*/\s*n(?:ao)?\b|^\W*(?:(?:exames?|solicitad[oa]s?|'
                     r'realizar|fazer|obs|sim|nao)\b\W*){3,}$')
_ROW = re.compile(r'\S\s*\|\s*\S|\s(?:sim|nao|n|s|x|[-–—✗✘✓✔])\s*$')


def layout(texts: list[str]) -> bool:
    rows = sum(bool(_ROW.search(text)) and ('|' in text or exam_before(text, len(text))) for text in texts)
    return rows > 1 or any(_YES_NO.search(text) for text in texts)


def contested(lines: list[str], reasons: list[str | None]) -> list[dict]:
    """[{code, name, reason}] of the catalog exams named on lines with a reason (a no first): no word of the line."""
    found: dict = {}
    for line, why in zip(lines, reasons, strict=True):
        written = f' {normalize(line)} ' if why else ''
        for exam in (exam for exam in catalog() if any(f' {term} ' in written for term in exam['terms'])):
            if found.get(exam['code'], {}).get('reason', 'instruction') == 'instruction':
                found[exam['code']] = {'code': exam['code'], 'name': exam['name'], 'reason': why}
    return list(found.values())
