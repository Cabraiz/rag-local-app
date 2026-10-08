"""What each line of an order asks for, and whether its page may book alone, read before the PII mask.

mcp_servers/ocr.py runs read_page() (each line's kind, the exams the page contests) and clean_page() (the page
allowlist: an exam books alone only on a page whose every line is a list line, a label, a count, a fasting time, marks, a
field read whole or, above the list, a letterhead) on the lines as written. docs/regras.md maps each rule to its example
and effect. Matching ignores accents, case and OCR misreadings ("NA0"); the vocabularies are in catalogo and pii_rules.
"""
import difflib
import re

from catalogo import LIST_MARKER, MASK_TAG, QUALIFIERS, exam_word, matcher, normalize, plain, words
from guardrails import pii_rules as rules
from guardrails.pii import exam_like
from leitura import BLOCKING

EXAMS = matcher().pattern  # a catalog name or synonym, on plain() text
LIST_KINDS = ('request', 'uncertain')  # a line that may be part of the list of exams

# The cues are built from the one negation vocabulary of guardrails/pii_rules.py (NOT, DONE, STOP_CUE, EXCEPT).
_NOT_SHORT = rf'(?:{rules.NOT}|n/|n(?=\s))'  # "n/ realizar", "ñ fazer" (plain() reads "ñ" as "n"), before a verb only
_SEP = r'[\s_-]*'  # "não realizar", "não-realizar", "nao_realizar", "naorealizar" (the OCR glued it)
_DO = (r'(?:r[e3]a[l1i]{1,2}[zs]\w*|faz\w*|fa[cz]a\w*|feit\w*|repet\w*|refaz\w*|colh\w*|colet\w*|dos[ae]\w*|'
       r'solicit\w*|ped\w*|agend\w*|marc\w*|inclu\w*|autoriz\w*|liberad\w*)')
_MODAL = r'(?:(?:e|eh)\s+(?:necessario|preciso)|precisa\w*(?:\s+de)?|deve\w*|pode\w*|vai|mais)'
# A time after "feito"/"realizado": "feita mês passado", "realizado há 2 meses", "feito ontem", "dia 10/03".
_WHEN = (r'(?:ontem|anteontem|hoje|(?:n[ao]\s+)?(?:semana|mes|ano)\s+passad[oa]|ha\s+\d+\s+[a-z]+|'
         r'(?:(?:no\s+)?dia\s+)?\d{1,2}/\d{1,4}(?:/\d{2,4})?)')
# (kind, cue, how many filler words may come between the cue and the exam, whether it may end the line)
CUES = [
    ('negated', re.compile(rf'\b{_NOT_SHORT}{_SEP}(?:{_MODAL}\s+)?{_DO}'), 3, True),  # não realizar, n/ realizar
    ('negated', re.compile(rf'\b{rules.NOT}{_SEP}(?:(?:e|eh)\s+)?(?:necessari\w*|precis[ao]\w*|indicad\w*)'), 3, True),
    ('negated', re.compile(rf'\b(?:{rules.NOT}|nr|n/r)\b'), 1, True),  # "NÃO: Ferritina", "Ferritina: não", "TSH - NR"
    ('negated', re.compile(rf'\b{rules.STOP_CUE}'), 3, True),  # "suspenso", "anulado", "vetado"
    ('negated', re.compile(r'\b(?:do\s+not|don\W?t|not)\s+(?:perform|repeat|do|order|run)\b|\bno\s+(?:realizar|repetir|hacer)\b'),
     3, True),  # English and Spanish
    ('negated', re.compile(r'\b(?:sem|s/)\s*(?:necessidade|indicacao)(?:\s+de)?(?:\s+(?:repetir|realizar|fazer|colher))?'),
     2, True),
    ('negated', re.compile(rf'\b{rules.EXCEPT}\b'), 2, False),  # "todos menos PSA"
    ('history', re.compile(rf'\bj[a4]\s+(?:(?:foi|foram)\s+)?(?:{rules.DONE}|fez|fizemos|realizou|tem)(?:\s+em)?'), 3, True),
    ('history', re.compile(rf'\b{rules.DONE}\s+(?:em\b|{_WHEN})'), 3, True),  # "realizado em 03/2025", "feita mês passado"
    ('history', re.compile(r'\bresultados?\s+(?:de\s+)?(?:anterior\w*|previo\w*|antigo\w*)|'
                           r'\b(?:ultim[oa]s?|anterior\w*)\s+(?:exames?|resultados?|dosage\w*)|\bexames?\s+anterior\w*'),
     4, False),
]
# Words that may come between a cue and its exam, or end the line after it: articles, "exame de", a date
# or a year, who decided ("vetado pelo médico", "não autorizado pelo convênio").
FILLERS = {'o', 'a', 'os', 'as', 'de', 'do', 'da', 'dos', 'das', 'um', 'uma', 'e', 'em', 'no', 'na', 'exame', 'exames',
           'dosagem', 'novamente', 'mais', 'este', 'esse', 'esta', 'essa', 'isso', 'tambem', 'vez', 'ano', 'anos', 'mes',
           'meses', 'dia', 'dias', 'ha', 'pedido', 'pelo', 'pela', 'foi', 'medico', 'medica', 'convenio', 'plano'}
NOT_AN_EXAM = QUALIFIERS | {'exame', 'exames', 'fezes'}  # never an exam on their own after a cue
TAG_PLAIN = re.compile(MASK_TAG.pattern.lower())  # catalogo.MASK_TAG on plain() text
TOKEN = re.compile(rf'{TAG_PLAIN.pattern}|[a-z0-9]+|[.;!?]|\S')
REFERENCE = {'item', 'itens', 'acima', 'abaixo', 'seguinte', 'seguintes', 'anterior', 'anteriores'}
_BLOCK = re.compile(r'(?::\s*$|\b(?:seguintes?|abaixo|a\s+seguir)\b)')  # "Não realizar os seguintes:", "Já realizados:"
_FASTING = re.compile(rf'\bjejum\s+(?:minimo\s+)?(?:de\s+)?\d+|\b\d+\s*{rules.HOUR_UNIT}\s+de\s+jejum|'
                      r'\bjejum\s+(?:absoluto|minimo|previo)')
_FOR_THE_EXAM = re.compile(r'\b(?:para|antes\s+d[aoe]s?)\s+(?:(?:o|a|os|as)\s+)?(?:exames?\s+(?:de\s+)?)?\W*$')
_RESULT_HEAD = re.compile(r'^\W*(?:\d{1,2}\W+)?(?:resultados?|valor(?:es)?|dosagens?\s+anterior\w*)\b')  # a result
# The allowlist: a ticked box, a label of the list or the doctor's own verb at the start, the marks that
# join exams. An empty box ("[ ]", "( )", "☐") is a word of its own: not ticked.
_TICKED = re.compile(r'^\s*(?:\[\s*[x✓✔]\s*\]|\(\s*[x✓✔]\s*\)|[☑☒✓✔①-⑳])\s*', re.I)
_LIST_NUMBER = re.compile(r'^\s*\d{1,2}\s*[;:]?\s+(?=[a-z])')  # "4 TGP", "5; TGO": a list number without its mark
_EMPTY_BOX = re.compile(r'(?<=\w)-(?=\s|$)|\[\s*\]|\(\s*\)|[☐□◻⬜]')  # "Creatinina-", "[ ]": a word of its own
_HOURS = re.compile(rf'(?<![\w-])\d{{1,2}}\s*{rules.HOUR_UNIT}(?![a-z])')  # "jejum de 8h"
_JOINERS = re.compile(r'[\s,;/+:.()\[\]{}–-]+')
_NO_BOX = re.compile(r'^\s*(?:\[\s*[-–—✗✘]\s*\]|\(\s*[-–—✗✘]\s*\)|[✗✘])')
_MARK, _BOX = re.compile(r'[\[(]?\s*[xv✓✔]\s*[\])]?|[/☑☒]'), re.compile(r'[\[(]\s*[\])]|[☐□◻⬜]')  # form()
_NO_CELL = re.compile(rf'\|\s*(?:(?:-+|[–—✗✘]|{rules.NOT}|n)\s*\|?|\|)\s*$')  # a row's last cell: no, or blank
_RESULT = re.compile(r'\W*(?:\+|-\W*$|positiv\w*|negativ\w*|(?:nao\s+)?reagente|normal|alterad\w*)(?![a-z])')  # annotated()
SENSITIVE = {'hiv', 'hcv', 'hbsag', 'vdrl', 'sifilis', 'htlv'}  # an exam only as a list item of its own


def exam_at(text: str, position: int) -> bool:
    """Whether an exam name starts here: a catalog name or synonym, or a word one OCR error from an
    exam word that is not a qualifier of one ("Ferritlna", not "jejum")."""
    if EXAMS.match(text, position):
        return True
    word = re.match(r'[a-z0-9]+', text[position:])
    return word is not None and len(word[0]) >= 3 and word[0] not in NOT_AN_EXAM and exam_like(word[0])


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
    """Whether an exam name (exam_at) starts at a word before this position."""
    return any(exam_at(text, word.start()) for word in re.finditer(r'[a-z0-9]+', text[:position]))


def has_exam(text: str) -> bool:
    return exam_before(text, len(text))


def cues_on(text: str) -> list[tuple[str, int, int, int, bool]]:
    """(kind, start, end, fillers, trailing) of each CUES match, left to right, longer first; no two overlap."""
    taken: list[tuple[str, int, int, int, bool]] = []
    for cue in sorted(((kind, match.start(), match.end(), fillers, trailing) for kind, pattern, fillers, trailing in CUES
                       for match in pattern.finditer(text)), key=lambda cue: (cue[1], cue[1] - cue[2])):
        if not any(cue[1] < end and start < cue[2] for _, start, end, *_ in taken):
            taken.append(cue)
    return taken


def judge(text: str) -> tuple[set[str], str, set[str], set[str]]:
    """(the kinds of its cues plainly about an exam: right before it, or ending the line of the only exam before them;
    where a line with a cue and no exam points: 'block' (a header), 'note' (a footnote) or ''; its cues' kinds; those
    that may end a line). Whether the line names an exam of its own is exact: "favor" is not Fator reumatoide."""
    cues, clear, points = cues_on(text), set(), ''
    for kind, start, end, fillers, trailing in cues:
        reach, word = after_cue(text, end, fillers)
        before = exam_before(text, start)
        if not before and (reach == 'exam' or word in REFERENCE):  # "não realizar Ferritina", "retirar o item 2"
            clear.add(kind)
        elif trailing and reach == 'end' and before and not other_exams(text, start):  # "Ferritina (suspensa)"
            clear.add(kind)
        if not EXAMS.search(text) and kind in BLOCKING and (reach == 'end' or word in REFERENCE):
            points = 'block' if _BLOCK.search(text.strip()) else 'note'
    kinds = {cue[0] for cue in cues}
    if not points and kinds and header_glued_to_its_first_item(text):
        points = 'block'
    return clear, points, kinds, {cue[0] for cue in cues if cue[4]}


def header_glued_to_its_first_item(text: str) -> bool:
    """"Já realizados: TSH": a header, and after its colon a first item with no cue of its own."""
    head, colon, rest = text.partition(':')
    return bool(rest.strip()) and judge(head + colon)[1] == 'block' and not judge(rest)[2]


def other_exams(text: str, position: int) -> bool:
    """Whether the line names more than one exam before this position ("TSH e T4 livre - não repetir")."""
    return len({match.group() for match in EXAMS.finditer(text, 0, position)}) > 1


def without_list_head(text: str) -> str:
    """A plain() line without its list marker, ticked box, bare number or list label (also misread: "Solreito:")."""
    text = _TICKED.sub(' ', LIST_MARKER.sub(' ', text, count=1), count=1)
    label = re.match(r'\s*([a-z]+)\s*:', text)
    if number := _LIST_NUMBER.match(text):
        text = text[number.end():]
    elif label and difflib.get_close_matches(label[1], rules.LABEL_WORDS, 1, 0.7):
        text = text[label.end():]
    return _LIST_NUMBER.sub(' ', rules.LIST_LABEL.sub(' ', text, count=1))


def residue(line: str) -> list[str]:
    """What a line holds besides exams, as read: [] for "1) Hemograma completo e TSH", ['pedido', 'por', 'engano'] for
    "- Ferritina - pedido por engano", ['?'] for "Colesterol total ?", ['='] for "=Creatinina", ['[instrucao_removida]']
    for an order to the model removed, ['[anotacao]'] for "Glicemia de jejum (HIV +)" (annotated)."""
    text = _HOURS.sub(' ', EXAMS.sub(' ', _EMPTY_BOX.sub(' = ', without_list_head(plain(line)))))
    rest = re.findall(r'[a-z0-9]+|\S', _JOINERS.sub(' ', TAG_PLAIN.sub(' ', text)))
    return ['[anotacao]'] * annotated(plain(line)) + TAG_PLAIN.findall(text) + [word for word in rest if not exam_word(word)]


def annotated(text: str) -> bool:
    """Whether a catalog word after the line's first exam notes something on it: in a parenthesis opened after it, next to
    a result ("Glicemia de jejum (HIV +)") or, sensitive, with no joiner before it ("Glicemia HIV"); not its other name."""
    first = EXAMS.search(text)
    if not first:
        return False
    exam = matcher().searched.get(normalize(first.group()))
    own = NOT_AN_EXAM | ({word for term in exam['terms'] for word in term.split()} if exam else set())
    rest = text[first.end():]
    return any(word[0] not in own and exam_word(word[0]) and notes_on(rest, word) for word in re.finditer(r'[a-z0-9]+', rest))


def notes_on(rest: str, word: re.Match[str]) -> bool:
    """Whether a word after an exam is in a parenthesis opened after it, before a result, or sensitive with no joiner."""
    gap = rest[:word.start()]
    return gap.count('(') > gap.count(')') or bool(_RESULT.match(rest, word.end())) or (
        word[0] in SENSITIVE and not re.search(r'(?:[,;/+-]|\be)\W*$', gap))


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
    if rules.PREP_LABEL.search(head) or (_FASTING.search(head) and _FOR_THE_EXAM.search(head)):
        return 'prep'
    return 'uncertain' if first and residue(line) else 'request'


def read_page(lines: list[str], breaks: frozenset[int] = frozenset()) -> tuple[list[str], list[str | None], bool]:
    """(each line's kind, why the exams it names are contested or None, whether a cue reaches no catalog exam):
    what each line says, then what a line with a cue and no exam says of the lines it reaches, then the layout."""
    texts = [plain(line) for line in lines]
    judged = [judge(text) for text in texts]
    kinds = [line_kind(line, text, clear) for line, text, (clear, *_) in zip(lines, texts, judged, strict=True)]
    contest: list[str | None] = [kind if kind in BLOCKING else None for kind in kinds]
    unlinked = False
    for index, (_, points, cues, trailing) in enumerate(judged):
        targets = reached(texts, index, points, breaks)
        for target in targets:  # "Ferritina (1)" and "(1) suspenso"; a line under "Já realizados:"
            kinds[target] = contest[target] = 'negated' if 'negated' in cues else 'history'
        if trailing and not any(EXAMS.search(texts[i]) for i in [index, *targets]):
            unlinked = True  # "suspenso" tied to no exam
    shape = 'table' if layout(texts) else 'form' if form(texts, kinds) else ''
    return [shape if shape and kind in LIST_KINDS else kind for kind in kinds], contest, unlinked


def reached(texts: list[str], index: int, points: str, breaks: frozenset[int]) -> list[int]:
    """The lines a cue with no exam talks about: those with its footnote's mark, or under a header, its block."""
    mark = re.match(r'\s*(\(\w{1,2}\)|\*+)', texts[index]) if points else None
    marked = [i for i, text in enumerate(texts) if mark and i != index and re.search(rf'\w\s*{re.escape(mark[1])}', text)]
    if marked or points != 'block':
        return marked
    below = index + 1
    while below < len(texts) and not ends_the_block(texts[below], below in breaks):
        below += 1
    return list(range(index + 1, below))


def ends_the_block(text: str, after_a_gap: bool) -> bool:
    """A blank line, a gap between Tesseract's blocks of text, a new header, or a label that is no exam."""
    label = re.match(r'\s*([a-z][a-z .]{0,30}):', text)
    return not text.strip() or after_a_gap or text.rstrip().endswith(':') or bool(label and not has_exam(label[1]))


# A table or columns: a yes/no header ("Realizar?"), 3 headers ("Exame Fazer Obs"), 2 rows ("TSH | -", "TSH Sim").
_YES_NO = re.compile(r'\b(?:realizar|fazer)\s*\?|\bs(?:im)?\s*/\s*n(?:ao)?\b|^\W*(?:(?:exames?|solicitad[oa]s?|'
                     r'realizar|fazer|obs|sim|nao)\b\W*){3,}$')
_ROW = re.compile(r'\S\s*\|\s*\S|\s(?:sim|nao|n|s|x|[-–—✗✘✓✔])\s*$')


def layout(texts: list[str]) -> bool:
    rows = sum(bool(_ROW.search(text)) and ('|' in text or has_exam(text)) for text in texts)
    return rows > 1 or any(_YES_NO.search(text) for text in texts)


def form(texts: list[str], kinds: list[str]) -> bool:
    """A selection form: an exam line with an empty box, or a mark (X, ✓, "/", "v") before or after the exams on some
    exam lines but not all, where only the marked ones count ("Exames: X Hemograma completo", then "TSH")."""
    found = [(text, [*EXAMS.finditer(text)]) for text, kind in zip(texts, kinds, strict=True) if kind in LIST_KINDS]
    ends = [(rules.LIST_LABEL.sub('', LIST_MARKER.sub('', text[:exams[0].start()], count=1), count=1).strip(),
             text[exams[-1].end():].strip()) for text, exams in found if exams]  # around the exams, marker and label aside
    marked = [any(_MARK.fullmatch(end) for end in pair) for pair in ends]
    return any(_BOX.fullmatch(end) for pair in ends for end in pair) or (any(marked) and not all(marked))


def contested(lines: list[str], reasons: list[str | None]) -> list[dict]:
    """[{code, name, reason}] of the catalog exams named on lines with a reason (the first): no word of the line."""
    found: dict = {}
    for line, why in zip(lines, reasons, strict=True):
        written = f' {normalize(line)} ' if why else ''
        for exam in matcher().exams:
            if any(f' {term} ' in written for term in exam['terms']):
                found.setdefault(exam['code'], {'code': exam['code'], 'name': exam['name'], 'reason': why})
    return list(found.values())


# clean_page: what a line off the list must not say, and what may stand there.
POSITION = re.compile(r'\b(?:\d{1,2}\s*[o°]|n[o°.]\s*\d{1,2}|(?:item|itens|linha|numero)\s*\d|'
                      r'(?:primeir|segund|terceir|quart|quint|penultim|ultim)[oa]s?)\b')  # "2º", "nº 3", "o último"
DELAY = re.compile(r'\b(?:adi[ae]\w*|posterg\w*|depois|proxim\w*|retorno|aguard\w*|somente|apenas|caso|trazer|'
                   r'laudos?|resultados?|tud[oa]|tod[oa]s|hold|skip|later|defer\w*|wait|only|if)\b')
COUNT = re.compile(r'^\W*(?:total|qtd|quantidade|itens)\b[a-z .]*:\s*(?:\d{1,2}\s*a\s*)?(\d{1,2})\W*$')  # "Itens: 1 a 3"
LOW_READING = 60  # Tesseract's confidence below which text off the exam lines may hide a note
# A letterhead looks like one: a clinic, an address, a phone, a date, a name, only header or field words ("Carteiri nha").
LETTERHEAD = re.compile(r'\b(?:clinic|hospita|laborator|policlinic|consultori|medicin|diagnostic|institut|centro|saude|ltda|cnpj|'
                        r'crm|rua\b|av\b|avenida|cep\b|tel\b|telefone|fone|www)|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-?\d{4}|\bde\s+(?:19|20)\d\d\b')
NAME = re.compile(r"(?:(?:d[aeo]s?|e)\s+|[A-ZÀ-Þ][\w'’.-]*\s+){1,4}[A-ZÀ-Þ][\w'’-]*")


def clean_page(lines: list[str], masked: list[str], kinds: list[str], odd: list[bool], readings: list[float]) -> list[int]:
    """The lines off the module docstring's allowlist ([]: a clean page; [-1]: no list, a table, a count below it). lines as
    written, masked as they leave the OCR, kinds as sent; odd[i]: letters far smaller or lighter; readings: Tesseract's."""
    texts = [plain(line) for line in lines]
    exams = [i for i, (line, text) in enumerate(zip(lines, texts, strict=True)) if kinds[i] in LIST_KINDS
             and has_exam(text) and all(len(word) < 3 for word in residue(line))]  # the list ("TSH ?" is asked itself)
    if not exams or layout(texts) or any(int(found[1]) < len(exams) for text in texts if (found := COUNT.match(text))):
        return [-1]  # a count of the exams below how many the page lists: one was added
    tags = [set(MASK_TAG.findall(safe)) for safe in masked]
    rest = [set(re.findall(r'[a-z]\w*', words(MASK_TAG.sub(' ', safe)))) for safe in masked]  # the words besides them
    says = [bool(left or found - {rules.REMOVED_TAG}) for found, left in zip(tags, rest, strict=True)]  # what it is
    off = []
    for i, text in enumerate(texts):
        if i in exams:
            stays = not odd[i]  # an exam in letters far smaller or lighter than the page's was added later
        elif any(cue.search(text) for _, cue, *_ in CUES) or POSITION.search(text) or DELAY.search(text):
            stays = False  # a cue, a reference by position ("o 2º", "item 3") or a delay ("depois", "somente se")
        elif not re.search(r'[a-z]{3}', text):  # without a word of 3 letters, it is noise ("t", "- We 2", "|")
            stays = True
        elif not says[i] and rules.REMOVED_TAG in tags[i] and readings[i] < LOW_READING:
            stays = False  # text removed from a line read poorly may hide a note
        else:
            stays = a_label_or_count(text, kinds[i]) or rest[i] <= rules.FIELDS and a_field(i, lines[i], masked[i], tags[i], says, exams)
        if not stays:
            off.append(i)
    return off


def a_label_or_count(text: str, kind: str) -> bool:
    """"Pedido de exames", "Obs: jejum de 8 horas", "Soleito:", "Itens: 3": no exam, nothing but labels after a note's."""
    return bool(COUNT.match(text)) or kind in ('request', 'prep') and not has_exam(text) and not residue(
        rules.NOTE_LABEL.sub(' ', text).rstrip(' :') + ':')


def a_field(i: int, line: str, safe: str, tags: set[str], says: list[bool], exams: list[int]) -> bool:
    """A field read whole (a signature, a CRM, a date) above the list, or below it with nothing removed. Only above it, a
    line whose text was all removed, with a field after it before the list, when it looks like a letterhead: a clinic,
    an address, a phone, a date, a name, or only header and field words, one OCR error each ("Carteiri nha")."""
    if says[i]:
        return i < exams[0] or (i > exams[-1] and rules.REMOVED_TAG not in tags)
    text = plain(line)
    letterhead = LETTERHEAD.search(text) or NAME.fullmatch(line.strip()) or all(
        difflib.get_close_matches(word, rules.HEADER, 1, 0.8) for word in re.findall(r'[a-z]{4,}', text))
    return i < exams[0] and any(says[i + 1:exams[0]]) and not safe.rstrip().endswith(':') and bool(letterhead)
