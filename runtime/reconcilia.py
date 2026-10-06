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

from catalogo import LIST_MARKER, exam_word, words

from .confianca import FIND_FLOOR, NOT_ANCHORS, NOT_SHARED, RESEMBLANCE, intent_of, pieces_of, reading_at

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

def exams_only(text):
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


def first_word(text):
    return (words(text).split() or [''])[0]


def is_label(text):
    return first_word(text) in LABELS or words(text) == 'e mail'


def parts(text):
    """(label, value) of each part of a line: a known label before ":" (one or two words: "Exames
    solicitados:", "Obs.:", "E-mail:") opens a part; any other ":" stays in the text."""
    found, label, start = [], '', 0
    for colon in re.finditer(':', text):
        before = text[start:colon.start()]
        tail = before.split()
        for size in (2, 1):
            if len(tail) >= size and is_label(' '.join(tail[-size:])):
                found.append((label, before[:before.rindex(tail[-size])]))
                label, start = ' '.join(tail[-size:]), colon.end()
                break
    return [*found, (label, text[start:])]


def order_lines(read):
    """(line index, text, note) of each part of the order that may name exams, as the search takes it:
    without its marker and label, the value of a personal-data label left out; any other ":" is a
    separator, so its two sides are checked. `note`: the part is the text of a note."""
    lines = []
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


def by_piece(text, hits):
    """(piece, its hits) of one search: the pieces the search cut the line into, or the line itself."""
    pieces: dict = {}
    for hit in hits or []:
        if isinstance(hit, dict) and 'code' in hit:
            pieces.setdefault(str(hit.get('piece') or text), []).append(hit)
    return pieces.items()


def taken(claimed, texts, line, start, end):
    """Whether a piece is text a proposed exam stands on as that exam: the piece's words, or the
    exam they match, are words of that exam's name ("Proteína OC" on the line of a Proteína C
    reativa). The rest of a line the exam only shares stays free: "Triglicerideos" after a
    "Colesterol total" that took the whole line as the model searched it."""
    return any(other == line and s < end and start < e and any(pieces_of(text, [exam]) for text in texts)
               for other, s, e, _, exam in claimed)


def unreported(state, hits_of, policy, settled):
    """The exams of the order that ended in no reported state, as left out items (reason
    'not_searched', or 'omitted' when a search returned the code but the model did not propose it).
    hits_of(text): the catalog search's hits for a line (each with its "piece" when the search split
    it); settled: the codes booked or already reported.
    One report per exam, at the confidence the order gives it there (search score, OCR reading)."""
    lines, read, readings = state.get('ocr_lines', []), state.get('ocr_read', []), state.get('ocr_confidence')
    claimed = [tuple(entry) for entry in state.get('accounted', [])]  # the text the proposed exams stand on
    candidates, settled, reported = state.get('candidates', {}), set(settled), []
    for index, text, note, hits in ((index, piece, note, hits) for index, line, note in order_lines(read)
                                    for piece, hits in by_piece(line, hits_of(line))):
        scores = sorted((float(hit.get('score', 0)) for hit in hits), reverse=True)
        if not scores or scores[0] < FIND_FLOOR or (len(scores) > 1 and scores[1] == scores[0]):
            continue
        best = next(hit for hit in hits if float(hit.get('score', 0)) == scores[0])
        query = words(text)
        name = words(best.get('name', ''))
        shared = (set(query.split()) & set(name.split())) - NOT_SHARED
        starts = f'{query} '.startswith(f'{name} ')  # "TSH em 30 dias"
        # a line that says something of its exam is checked in full, even under a note's label ("Preparo:")
        note = note and intent_of(state, index) not in (*NOT_ANCHORS, 'uncertain')
        if scores[0] < RESEMBLANCE and not starts and (note or not shared):  # in a note, a close match or the name first
            continue
        spots = pieces_of(query, [lines[index]]) if index < len(lines) else []
        start, end = spots[0][1:3] if spots else (0, len(lines[index]) if index < len(lines) else 0)
        if best['code'] in settled or taken(claimed, [query, words(best.get('name', ''))], index, start, end):
            continue
        reading = reading_at(readings, index, policy.ocr_floor(query, words(best.get('name', ''))), policy)
        kind = intent_of(state, index)
        reported.append({'code': best['code'], 'name': str(best.get('name', '')), 'line': index,
                         'reason': kind if kind in NOT_ANCHORS else 'omitted' if best['code'] in candidates else 'not_searched',
                         'confidence': round(min(scores[0], reading), 2), 'read': read[index]})
        settled.add(best['code'])
        claimed.append((index, start, end, None, words(best.get('name', ''))))
    return reported
