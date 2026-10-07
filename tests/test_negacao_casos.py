"""Orders that say not to do an exam, or that it was done, written the many ways an order says it.

Two outside rounds of attacks on the line-intent rule (guardrails/intent.py), kept as regression
tests. Each order is read as the OCR reads it (the real mask and line_intent), then booked by the
generated agent's callbacks with nobody to answer [s/N], as `cli run --yes` does:
- a careful model searches each exam's own name, so only the rule decides;
- a lazy model searches each line as read, and the check of the whole order (runtime/reconcilia.py)
  runs after it: no exam may end in silence.
The rule is conservative: when a line is not plainly a request, its exam is asked or reported, never
booked alone and never dropped without a word.
"""
import pytest

from runtime.reconcilia import order_lines, unreported
from tests.test_confianca import agent, read  # noqa: F401  (agent is a fixture)
from tests.test_transpiler import FakeTool, book

HEAD = ['PEDIDO MÉDICO DE EXAMES', 'Paciente: Marta Ficticia Exemplar', 'Exames solicitados:']
FOOT = ['Dra. Celina Inventada - CRM-SP 123456', 'Data: 07/10/2026']
HEMO, GLI, HBA, URE, CRE, COL, FERRO, FER, B12, VITD, TSH, T4L, PSA, CA125, URINA, PROT24 = (
    'FICT-001', 'FICT-002', 'FICT-003', 'FICT-004', 'FICT-005', 'FICT-006', 'FICT-017', 'FICT-018', 'FICT-021',
    'FICT-023', 'FICT-024', 'FICT-025', 'FICT-048', 'FICT-051', 'FICT-090', 'FICT-092')
NAMES = {'FICT-033': 'Prolactina', HEMO: 'Hemograma completo', GLI: 'Glicemia de jejum', HBA: 'Hemoglobina glicada', URE: 'Ureia',
         CRE: 'Creatinina', COL: 'Colesterol total', FERRO: 'Ferro serico', FER: 'Ferritina', B12: 'Vitamina B12',
         VITD: 'Vitamina D', TSH: 'TSH', T4L: 'T4 livre', PSA: 'PSA total', CA125: 'CA 125', URINA: 'Urina tipo I',
         PROT24: 'Proteinuria de 24 horas'}
REPORTED = ('negated', 'history', 'prep')
ASKED = ('needs_confirmation',)

# (id, lines, booked alone, asked, reported with what the order says). Nothing else may be booked.
CASES = [
    # negation and history, on the exam's line
    ('N02', ['- Hemograma completo', '- Ferritina - dispensar'], [HEMO], [], [FER]),
    ('N03', ['- Hemograma completo', '- PSA total (evitar)'], [HEMO], [], [PSA]),
    ('N04', ['- Hemograma completo', '- TSH suspenso'], [HEMO], [], [TSH]),
    ('N05', ['- Hemograma completo', '- Ferritina cancelado'], [HEMO], [], [FER]),
    ('N06', ['- Hemograma completo', '- PSA total realizado dia 10/03'], [HEMO], [], [PSA]),
    ('N07', ['- Hemograma completo', 'Resultado anterior de TSH: 2,1'], [HEMO], [], [TSH]),
    ('N08', ['- Hemograma completo', '- Ferritina: não precisa'], [HEMO], [], [FER]),
    ('N09', ['- Hemograma completo', '- Vitamina D sem necessidade'], [HEMO], [], [VITD]),
    ('N16', ['- Hemograma completo', '- FERRITINA - NÃO REALIZAR'], [HEMO], [], [FER]),
    ('N17', ['- Hemograma completo', 'Não é necessário repetir PSA total'], [HEMO], [], [PSA]),
    ('N18', ['- Hemograma completo', '- Ferritina feita mês passado'], [HEMO], [], [FER]),
    ('N19', ['- Hemograma completo', '- Ferritina — NÃO'], [HEMO], [], [FER]),
    ('N20', ['- Hemograma completo', '- Ferritina? Não.'], [HEMO], [], [FER]),
    ('N21', ['- Hemograma completo', 'Suspender Ferritina e PSA total'], [HEMO], [], [FER, PSA]),
    ('N23', ['- Hemograma completo', '- Vitamina B12: já tem (05/2026)'], [HEMO], [], [B12]),
    ('N24', ['- Hemograma completo', '- Ferritina (desnecessária)'], [HEMO], [], [FER]),
    ('N25', ['- Hemograma completo', '- Ferrltina - nao realizar'], [HEMO], [], [FER]),
    ('N26', ['- Hemograma completo', '- Ferritina: NA0 FAZER'], [HEMO], [], [FER]),
    ('R01', ['- Hemograma completo', 'Ferritina: não'], [HEMO], [], [FER]),
    ('R02', ['- Hemograma completo', 'Ferritina - não necessário'], [HEMO], [], [FER]),
    ('R03', ['- Hemograma completo', 'não-realizar Ferritina'], [HEMO], [], [FER]),
    ('R04', ['- Hemograma completo', 'Resultado de Ferritina: 45 ng/mL'], [HEMO], [], [FER]),
    ('R06', ['- Hemograma completo', 'Ferritina (desmarcar)'], [HEMO], [], [FER]),
    ('R06b', ['- Hemograma completo', 'Ferritina - vetado pelo médico'], [HEMO], [], [FER]),
    ('R07', ['- Hemograma completo', 'Ferritina feita mês passado'], [HEMO], [], [FER]),
    # a doubt: asked
    ('N01', ['- Hemograma completo', '- Ferritina', 'Obs: nao fazer Ferritina'], [HEMO], [FER], []),
    ('N10', ['Exames: Hemograma completo, Ferritina e TSH, exceto Ferritina'], [], [HEMO, FER, TSH], []),
    ('N11', ['- Hemograma completo', '- PSA total', '- TSH', 'Todos menos PSA total'], [HEMO, TSH], [PSA], []),
    ('N14', ['- Hemograma completo', 'Não realizar:', '- Ferritina'], [HEMO], [FER], []),
    ('N15', ['- Hemograma completo', '- Ferritina', '(suspensa)'], [HEMO], [FER], []),
    ('N22', ['- TSH e T4 livre - não repetir T4 livre'], [], [TSH, T4L], []),
    ('N27', ['1. Hemograma completo', '2. Ferritina', '3. TSH', 'Obs.: retirar o item 2'], [HEMO, TSH], [FER], []),
    ('N28', ['- Hemograma completo', 'Paciente trouxe PSA total de agosto'], [HEMO], [PSA], []),
    ('R04b', ['- Hemograma completo', 'Ferritina 45 ng/mL (03/2025)'], [HEMO], [FER], []),
    ('F05', ['- Ferritina - controle após suspensão do ferro'], [], [FER], []),
    # requests with words of a negation about something else: booked
    ('N12', ['- Hemograma completo', '- Não deixar de fazer TSH'], [HEMO, TSH], [], []),
    ('N13', ['- Hemograma completo', '- TSH (não esquecer)'], [HEMO, TSH], [], []),
    ('F01', ['- Glicemia de jejum (não tomar café)'], [GLI], [], []),
    ('F02', ['- TSH - paciente não está em jejum'], [TSH], [], []),
    ('F03', ['- Hemograma completo sem plaquetas'], [HEMO], [], []),
    ('F04', ['- Colesterol total (não precisa jejum)'], [COL], [], []),
    ('F06', ['- TSH - não suspender levotiroxina'], [TSH], [], []),
    ('F07', ['- Repetir TSH (resultado anterior alterado)'], [TSH], [], []),
    ('F08', ['- Vitamina D (suspender suplemento 7 dias antes)'], [VITD], [], []),
    ('F09', ['- Hemoglobina glicada (sem jejum)'], [HBA], [], []),
    ('F10', ['- Ureia - dispensa jejum'], [URE], [], []),
    ('F11', ['- Urina tipo I - evitar primeira urina da manhã'], [URINA], [], []),
    ('F13', ['- Creatinina - paciente sem diálise'], [CRE], [], []),
    ('F14', ['- Glicemia de jejum - evitar exercício na véspera'], [GLI], [], []),
    ('F15', ['- Hemograma completo - não fumante'], [HEMO], [], []),
    ('F16', ['- TSH - sem uso de biotina há 3 dias'], [TSH], [], []),
    ('F17', ['- PSA total - não ejacular 48h antes'], [PSA], [], []),
    ('F18', ['- Vitamina B12 (já em uso de metformina)'], [B12], [], []),
    ('F19', ['- Ferritina e Ferro sérico - suspender sulfato ferroso 7 dias antes'], [FER, FERRO], [], []),
    ('F20', ['- Colesterol total - último exame alterado, repetir'], [COL], [], []),
    ('F21', ['- Proteinúria de 24 horas - não descartar a 1ª urina'], [PROT24], [], []),
    ('F22', ['Obs: jejum de 8 horas', '- Glicemia de jejum'], [GLI], [], []),
    ('F23', ['- Ferritina', 'Obs: anemia ferropriva, sem melhora com ferro oral'], [FER], [], []),
    ('R12', ['Obs.: solicito também Ferritina'], [FER], [], []),
    # numbers on an exam's line
    ('P01', ['- Vitamina B12 1000'], [B12], [], []),
    ('P04', ['- CA 125'], [CA125], [], []),
    ('P05', ['- 25-OH vitamina D'], [VITD], [], []),
    ('P07', ['- Glicemia de jejum 98 mg/dL'], [], [GLI], []),  # a result or a target: asked
    ('P09', ['- Colesterol total 180 (03/2026)'], [COL], [], []),
    # A third round: abbreviations, a restriction, a header over a list, boxes, a sentence before the exam.
    ('V01', ['- Hemograma completo', '- Ferritina (n/ realizar)'], [HEMO], [], [FER]),
    ('V02', ['- Hemograma completo', '- ñ fazer PSA total'], [HEMO], [], [PSA]),
    ('V03', ['- Hemograma completo', '- TSH - NR'], [HEMO], [], [TSH]),
    ('V03b', ['- Hemograma completo', '- Vitamina D s/ necessidade'], [HEMO], [], [VITD]),
    ('V03c', ['- Hemograma completo', '- Ferritina dispensado'], [HEMO], [], [FER]),
    ('V07', ['- Ferritina e TSH: realizar apenas TSH'], [], [FER, TSH], []),
    ('V08', ['Não realizar os seguintes:', '- Ferritina', '- PSA total', 'Realizar:', '- Hemograma completo'],
     [HEMO], [FER, PSA], []),
    ('V08b', ['Já realizados:', '1. Ferritina', '2. TSH', '', '- Hemograma completo'], [HEMO], [FER, TSH], []),
    ('V15', ['- TSH - não há outras queixas'], [TSH], [], []),
    ('V16', ['- Hemograma completo - sem restrições'], [HEMO], [], []),
    ('N1', ['- Hemograma completo', 'Per favore aggiungere anche la Ferritina'], [HEMO], [], []),
    ('N2', ['- Hemograma completo', "Merci d'inclure le PSA total"], [HEMO], [], []),
    ('N3', ['- Hemograma completo', 'Conforme orientação verbal, acrescentar PSA total'], [HEMO], [PSA], []),
    ('N4', ['[x] Ferritina', '[ ] PSA total', '☐ TSH', '☑ Hemograma completo'], [FER, HEMO], [PSA, TSH], []),
    ('N4b', ['- Hemograma completo', 'Gioconda Valadares falou: Prolactina'], [HEMO], ['FICT-033'], []),
    # An exam added inside a request line, in a clause of its own: the line's own exam is booked, the other asked.
    ('P12', ['Exame: Vitamina D (incluir também Ferritina)'], [VITD], [FER], []),
    ('P13', ['Exame: Vitamina D (a pedido do médico, incluir Ferritina)'], [VITD], [FER], []),
    ('P14', ['Exames: Hemograma completo, Creatinina e TSH'], [HEMO, CRE, TSH], [], []),
]


def ocr_reply(lines):
    ocr = pytest.importorskip('mcp_servers.ocr')
    return ocr.mask_lines(HEAD + lines + FOOT)


def search(agent, context, query):
    rag = pytest.importorskip('mcp_servers.rag')
    agent.CALLBACKS.after_tool(FakeTool('search_exams'), {'query': query}, context,
                                {'structuredContent': {'result': rag.search_line(query, 3)}})


def decide(agent, reply, queries):
    """{code: 'booked' or the reason it was left out}, after the booking call and the check of the order."""
    rag = pytest.importorskip('mcp_servers.rag')
    context = read(agent, reply['lines'], None, reply['line_intent'], line_note_from=reply['line_note_from'])
    for query in queries:
        search(agent, context, query)
    candidates = context.state.get('candidates', {})
    result: dict = {}
    if candidates:
        reply_, args = book(agent, context, *candidates)
        result = {exam['code']: 'booked' for exam in ([] if reply_ else args['exams'])}
        result |= {item['code']: item['reason'] for item in context.state['low_confidence']}
    texts = {text for _, text, _ in order_lines(context.state.get('ocr_read', []))}
    hits = {text: rag.search_line(text, 3) for text in texts}
    late = unreported(context.state, hits.get, agent.CALLBACKS.policy, set(result))
    return result | {item['code']: item['reason'] for item in late}


@pytest.mark.parametrize('cid, lines, booked, asked, reported', CASES, ids=[case[0] for case in CASES])
def test_what_the_order_says_decides_booked_asked_or_reported(agent, cid, lines, booked, asked, reported):
    # The careful model: each exam searched by its own name.
    found = decide(agent, ocr_reply(lines), [NAMES[code] for code in [*booked, *asked, *reported]])
    assert {code for code, state in found.items() if state == 'booked'} == set(booked), found
    assert all(found.get(code) in ASKED for code in asked), found
    assert all(found.get(code) in REPORTED for code in reported), found


@pytest.mark.parametrize('cid, lines, booked, asked, reported', CASES, ids=[case[0] for case in CASES])
def test_no_exam_of_the_order_ends_in_silence_with_a_lazy_model(agent, cid, lines, booked, asked, reported):
    # The lazy model: each line searched as read, every code found proposed; never a code the order cancels.
    from tests.load.manuscritos import consulta
    reply = ocr_reply(lines)
    found = decide(agent, reply, [query for query in map(consulta, reply['lines']) if len(query) >= 2])
    assert not {code for code in [*asked, *reported] if found.get(code) == 'booked'}, found
    assert all(code in found for code in [*booked, *asked, *reported]), found


# Personal data next to an exam, and long numbers that are part of an exam's line.
PII_LINES = [
    ('Cartão SUS 898 0012 3456 7890', ['898', '0012', '3456', '7890']),
    ('Prontuário 00123456789', ['00123456789']),
    ('Rua das Acácias Ficticias 1234 apto 56', ['Acácias', '1234']),
    ('CPF 123.456.789-09', ['123.456.789-09', '789']),
    ('123.456.789-09', ['123.456.789-09']),
    ('CNS 700 0000 0000 0000', ['700 0000']),
    ('Bairro Jardim Inventado, CEP 01310-100', ['01310', 'Jardim Inventado']),
    ('Tel (11) 98765-4321', ['98765', '4321']),
    ('Matrícula 4455667788 - Plano Exemplo', ['4455667788']),
    ('Ferritina - paciente RG 12.345.678-9', ['12.345.678', '678-9']),
    ('Fone 11 9 8765 4321', ['8765', '4321']),
    ('Av Paulista 1000 sala 12', ['Paulista', '1000']),
    ('joao ponto silva arroba exemplo ponto com', ['silva', 'exemplo']),
]


@pytest.mark.parametrize('line, secrets', PII_LINES, ids=[line for line, _ in PII_LINES])
def test_personal_numbers_and_addresses_leave_the_ocr_masked(line, secrets):
    reply = ocr_reply([line])
    masked = '\n'.join(reply['lines'])
    assert not [secret for secret in secrets if secret in masked], masked
    # each kind counted is a marker the line really leaves with
    for kind, count in reply['pii_masked'].items():
        assert masked.count(f'[{kind}]') >= count, (kind, masked)


@pytest.mark.parametrize('line, code, name', [
    ('- Vitamina D 50000 UI semanal', VITD, 'Vitamina D'),
    ('- Clearance de creatinina 120 ml/min', 'FICT-094', 'Clearance de creatinina'),
    ('- Beta HCG quantitativo 25000', 'FICT-047', 'Beta HCG quantitativo'), ('- Vitamina B12 5000 mcg', B12, 'Vitamina B12'),
])
def test_a_long_number_on_an_exam_line_does_not_remove_the_exam(agent, line, code, name):
    reply = ocr_reply([line])
    assert name.split()[0] in reply['lines'][3]
    assert decide(agent, reply, [name]).get(code) == 'booked'


def test_the_counts_are_the_markers_that_stay():
    # A value the safety net then took with the text around it counts as the text removed.
    ocr = pytest.importorskip('mcp_servers.ocr')
    reply = ocr.mask_lines(['Av Paulista 1000 sala 12', 'Paciente: [NOME] CPF: [CPF]'])
    assert reply['lines'] == ['[TEXTO_REMOVIDO]', 'Paciente: [NOME] CPF: [CPF]']
    assert reply['pii_masked'] == {} and reply['text_removed'] == 1  # markers written in the order count for nothing
