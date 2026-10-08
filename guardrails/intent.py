"""What each line of an order asks for, and whether its page may book alone, read before the PII mask.

mcp_servers/ocr.py runs read_page() and clean_page() on the lines as written (before the injection guard and the
mask) and sends `line_intent`, `contested_exams`, `cancel_unlinked` and `page_clean`. The page allowlist: an exam
books alone only on a page where every line is a list line (only catalog names, qualifiers and words one OCR error
from them, after a marker and a label), a label, a fasting time, marks, or, off the list, a field the PII step
recognized or a letterhead it removed whole, with no cue, reference by position or word of a delay, and no exam is
read in letters far smaller or lighter than the page's (clean_page). Otherwise every exam is asked. By line:
'negated'/'history': a cue plainly about the exam ("não realizar Ferritina", "TSH - NR", "susp.", "já realizado em
2025"), a box or cell that says no, a line under a header ("Já realizados:") down to a blank, a gap between blocks
or a new header, or marked by a footnote that says no: reported, and contested on the whole page; 'prep' with an
exam: reported; 'uncertain' (an exam and other words) and 'table': asked. A cue is plainly about the exam that
follows it with only fillers between, or that it ends the line after; whether a cue's line names an exam is exact
("favor" is not Fator reumatoide). Matching ignores accents and case and takes OCR misreadings of cues ("NA0").
"""
import difflib
import re

from catalogo import LIST_MARKER, QUALIFIERS, catalog, exam_word, fold, normalize, words
from guardrails.pii import EXAM_TERMS, exam_like
from guardrails.pii_rules import STRUCTURE

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
    ('negated', re.compile(rf'\b(?:{_NOT}|nr|n/r)\b'), 1, True),  # "NÃO: Ferritina", "Ferritina: não", "TSH - NR"
    ('negated', re.compile(r'\b(?:suspen[ds]\w*|susp\b|canc\b|cancel\w*|desmarc\w*|dispens\w*|evit\w*|vet(?:ad[oa]s?|ar|e|ou)\b|'
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


def judge(text: str) -> tuple[set[str], str, set[str], set[str]]:
    """(clear kinds, where a cue-only line points: 'block' (a header), 'note' or '', its cues' kinds, the kinds of
    its cues that may end a line) of one line; "Já realizados: TSH" is a 'block' too. A clear cue is plainly
    about it. Whether the line names an exam of its own is exact: "favor" is not Fator reumatoide."""
    clear: set[str] = set()
    points, cues, strong = '', set(), set()
    taken: list[tuple[int, int]] = []
    line_has_exam = bool(EXAMS.search(text))
    for kind, start, end, fillers, trailing in sorted(
            ((kind, match.start(), match.end(), fillers, trailing)
             for kind, pattern, fillers, trailing in CUES for match in pattern.finditer(text)),
            key=lambda item: (item[1], item[1] - item[2])):
        if any(start < e and s < end for s, e in taken):
            continue
        taken.append((start, end))
        cues.add(kind)
        strong.update([kind] if trailing else [])
        reach, word = after_cue(text, end, fillers)
        before = exam_before(text, start)
        if not before and (reach == 'exam' or word in REFERENCE):
            clear.add(kind)  # "não realizar Ferritina", "retirar o item 2"
        elif trailing and reach == 'end' and before and not other_exams(text, start):
            clear.add(kind)  # "Ferritina (suspensa)", "PSA total - não repetir"
        if not line_has_exam and kind in BLOCKING and (reach == 'end' or word in REFERENCE):
            points = 'block' if _BLOCK.search(text.strip()) else 'note'
    head, colon, rest = text.partition(':')  # a header glued to its first item, which has no cue of its own
    points = points or ('block' if cues and rest.strip() and judge(head + colon)[1] == 'block' and not judge(rest)[2] else '')
    return clear, points, cues, strong


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


def read_page(lines: list[str], breaks: frozenset[int] = frozenset()) -> tuple[list[str], list[str | None], bool]:
    """(each line's kind, why the exams it names are contested or None, whether a cue reaches no catalog exam):
    what each line says, then what a cue-only line says of the lines it reaches, then layout()."""
    texts = [plain(line) for line in lines]
    judged = [judge(text) for text in texts]
    kinds = [line_kind(line, text, clear) for line, text, (clear, *_) in zip(lines, texts, judged, strict=True)]
    contest: list[str | None] = [kind if kind in BLOCKING else None for kind in kinds]
    unlinked = False
    for index, (_, points, cues, strong) in enumerate(judged):
        targets = reached(texts, index, points, breaks)
        for target in targets:  # "Ferritina (1)" and "(1) suspenso"; a line under "Já realizados:"
            kinds[target] = contest[target] = 'negated' if 'negated' in cues else 'history'
        unlinked = unlinked or bool(strong) and not any(EXAMS.search(texts[i]) for i in [index, *targets])
    return [('table' if kind in ('request', 'uncertain') else kind) for kind in kinds] if layout(texts) else kinds, \
        contest, unlinked


def reached(texts: list[str], index: int, points: str, breaks: frozenset[int]) -> list[int]:
    """The lines a cue-only line plainly talks about: those with its footnote's mark, or, under a header, every
    line down to a blank, a gap between Tesseract's blocks of text (`breaks`) or a new header."""
    mark = re.match(r'\s*(\(\w{1,2}\)|\*+)', texts[index]) if points else None
    marked = [i for i, text in enumerate(texts) if mark and i != index and re.search(rf'\w\s*{re.escape(mark[1])}', text)]
    if marked or points != 'block':
        return marked
    targets = []
    for below in range(index + 1, len(texts)):
        label = re.match(r'\s*([a-z][a-z .]{0,30}):', texts[below])
        if not texts[below].strip() or below in breaks or texts[below].rstrip().endswith(':') or \
                label and not exam_before(label[1], 99):
            break
        targets.append(below)
    return targets


# A table or columns: a yes/no header ("Realizar?"), 3 headers ("Exame Fazer Obs"), 2 rows ("TSH | -", "TSH Sim").
_YES_NO = re.compile(r'\b(?:realizar|fazer)\s*\?|\bs(?:im)?\s*/\s*n(?:ao)?\b|^\W*(?:(?:exames?|solicitad[oa]s?|'
                     r'realizar|fazer|obs|sim|nao)\b\W*){3,}$')
_ROW = re.compile(r'\S\s*\|\s*\S|\s(?:sim|nao|n|s|x|[-–—✗✘✓✔])\s*$')


def layout(texts: list[str]) -> bool:
    rows = sum(bool(_ROW.search(text)) and ('|' in text or exam_before(text, len(text))) for text in texts)
    return rows > 1 or any(_YES_NO.search(text) for text in texts)


def contested(lines: list[str], reasons: list[str | None]) -> list[dict]:
    """[{code, name, reason}] of the catalog exams named on lines with a reason (the first): no word of the line."""
    found: dict = {}
    for line, why in zip(lines, reasons, strict=True):
        written = f' {normalize(line)} ' if why else ''
        for exam in (exam for exam in catalog() if any(f' {term} ' in written for term in exam['terms'])):
            found.setdefault(exam['code'], {'code': exam['code'], 'name': exam['name'], 'reason': why})
    return list(found.values())


# clean_page: the words of a header or a field (not of a note), and what a line off the list must not say.
FIELDS = (STRUCTURE | {'sexo', 'local', 'hipotese'}) - {'obs', 'observacao', 'observacoes', 'nota', 'orientacao',
                                                        'orientacoes', 'preparo'}
POSITION = re.compile(r'\b(?:\d{1,2}\s*[o°]|n[o°.]\s*\d{1,2}|(?:item|itens|linha|numero)\s*\d|'
                      r'(?:primeir|segund|terceir|quart|quint|penultim|ultim)[oa]s?)\b')  # "2º", "nº 3", "o último"
DELAY = re.compile(r'\b(?:adi[ae]\w*|posterg\w*|depois|proxim\w*|retorno|aguard\w*|somente|apenas|caso|trazer|'
                   r'laudos?|resultados?|tud[oa]|tod[oa]s|hold|skip|later|defer\w*|wait|only|if)\b')
LOW_READING = 60  # Tesseract's confidence below which text off the exam lines may hide a note
TAG, PREP_LABEL = re.compile(r'\[[A-Z_]+\]'), re.compile(r'^\W*(?:obs|observa\w*|preparo|orienta\w*)\b')


def clean_page(lines: list[str], masked: list[str], kinds: list[str], odd: list[bool], readings: list[float]) -> bool:
    """The page allowlist of the module docstring. lines as written, masked as they leave the OCR, kinds as sent;
    odd[i]: letters far smaller or lighter than the page's; readings: Tesseract's confidence of each line."""
    texts = [plain(line) for line in lines]
    exams = [i for i, (line, text) in enumerate(zip(lines, texts, strict=True)) if kinds[i] in ('request', 'uncertain')
             and exam_before(text, len(text)) and all(len(word) < 3 for word in residue(line))]  # "TSH ?" is asked itself
    if not exams or layout(texts):
        return False
    marks = [(set(re.findall(r'\[([A-Z_]+)\]', safe)), set(re.findall(r'[a-z]\w*', words(TAG.sub(' ', safe))))) for safe in masked]
    named = [bool(rest or tags - {'TEXTO_REMOVIDO'}) for tags, rest in marks]  # a field or a header: says what it is
    for i, (text, safe, (tags, rest)) in enumerate(zip(texts, masked, marks, strict=True)):
        quiet = not (any(cue.search(text) for _, cue, *_ in CUES) or POSITION.search(text) or DELAY.search(text))
        listed = kinds[i] in ('request', 'prep') and not exam_before(text, len(text)) and not residue(  # "Soleito:"
            PREP_LABEL.sub(' ', text).rstrip(' :') + ':')  # "Pedido de exames", "Obs: jejum de 8 horas", a misread label
        # a line removed whole: a letterhead or a stamp, with a field between it and the list ("Paciente:", "Dra.")
        apart = any(named[exams[-1] + 1:i]) if i > exams[-1] else i < exams[0] and any(named[i + 1:exams[0]])
        field = rest <= FIELDS and (named[i] and (i < exams[0] or i > exams[-1]) or apart and not safe.rstrip().endswith(':'))
        worded = re.search(r'[a-z]{3}', text)  # without a word of 3 letters, it is noise ("t", "- We 2", "|")
        hidden = worded and not named[i] and 'TEXTO_REMOVIDO' in tags and readings[i] < LOW_READING  # removed, read poorly
        if not (not odd[i] if i in exams else quiet and not hidden and (listed or field or not worded)):
            return False
    return True
