"""The whole order is checked in code after the run (runtime/reconcilia.py): an exam written in the
order ends in a reported state even when the model never searched it.

An independent review: on "2. Colesterol total e Triglicerideos" the model searched the whole line,
Colesterol total came out at 0,68 and Triglicerídeos was never searched; the CLI confirmed the
appointment without a word about it. The lines below are the OCR's real reply for that image
(PII already masked), and the search is the real catalog search, in process."""
import asyncio

import pytest

import cli
from runtime import relatorio, servidores
from runtime.pedido import OrderRecord
from runtime.reconcilia import order_lines, unreported
from tests.test_agent_mcp import servers  # noqa: F401  (the real MCP servers, as processes)
from tests.test_confianca import agent, best_of, book, read, real_search  # noqa: F401  (agent is a fixture)
from tests.test_transpiler import FakeTool, fake_run, ready_run  # noqa: F401  (ready_run is a fixture)
from transpiler import load_spec

REVIEWED = ['[TEXTO_REMOVIDO] - LABORATORIO ([TEXTO_REMOVIDO])', '[ENDERECO]', 'Paciente: [NOME]',
         'RG: [RG] Nascimento: [DATA]', 'Celular: [TELEFONE]', 'E-mail: [EMAIL]', '[TEXTO_REMOVIDO]:',
         '1. Hemoglobina glicada', '2. Colesterol total e Triglicerideos', '3. TGO', 'A. Ferritina',
         'Medica: [NOME] [CRM]', '[TEXTO_REMOVIDO], [DATA]']
READINGS = [93.7, 93.8, 96.0, 88.5, 91.7, 63.0, 95.8, 89.0, 94.0, 87.0, 76.0, 91.1, 96.0]


def catalog_search(text):
    """The search_exams tool's reply, in process: a line with several exams comes back piece by piece."""
    rag = pytest.importorskip('mcp_servers.rag')
    return rag.search_line(text, 3)


def check(agent, context, booked):
    """What the check of the whole order adds after the booking call (the CLI's settled codes)."""
    settled = set(booked) | {item['code'] for item in context.state.get('low_confidence', [])}
    return [(item['code'], item['name'], item['reason'], item['confidence'], item['read'])
            for item in unreported(agent.CALLBACKS.orders.of(context), catalog_search, agent.CALLBACKS.policy, settled)]


def test_only_the_exam_lines_of_the_reviewed_order_are_checked_and_the_search_cuts_them():
    # "LABORATORIO" is no word of an exam: a separator, not searched.
    lines = order_lines(REVIEWED)
    assert [line[:2] for line in lines] == [(7, 'Hemoglobina glicada'), (8, 'Colesterol total e Triglicerideos'),
                                            (9, 'TGO'), (10, 'Ferritina')]
    rag = pytest.importorskip('mcp_servers.rag')
    assert rag.split_exams(lines[1][1]) == ['Colesterol total', 'Triglicerideos']  # the pieces checked are the search's


def test_the_reviewed_case_reports_the_exam_the_model_never_searched(agent):
    context = read(agent, REVIEWED, READINGS)
    queries = ['Hemoglobina glicada', 'Colesterol total e Triglicerideos', 'TGO', 'Ferritina']
    for query in queries:
        real_search(agent, context, query)
    reply, args = book(agent, context, *map(best_of, queries))
    booked = [exam['code'] for exam in args['exams']]
    assert booked == ['FICT-003', 'FICT-018']  # as in the reviewed run: booking is unchanged
    assert [(item['code'], item['reason']) for item in context.state['low_confidence']] == [
        ('FICT-055', 'needs_confirmation'), ('FICT-006', 'score')]  # TGO asked, Colesterol total low (0,6x)
    assert check(agent, context, booked) == [
        ('FICT-009', 'Triglicerideos', 'not_searched', 1.0, '2. Colesterol total e Triglicerideos')]


def test_the_question_shows_the_exam_the_model_never_searched_before_the_answer(agent, monkeypatch):
    async def in_process(url, tool, texts, top_k):  # the catalog server's search, without the server
        return {text: catalog_search(text) for text in texts}
    monkeypatch.setattr(servidores, 'search_lines', in_process)
    monkeypatch.setattr(agent.CALLBACKS, 'can_ask', lambda: True)
    context = read(agent, REVIEWED, READINGS)
    queries = ['Hemoglobina glicada', 'Colesterol total e Triglicerideos', 'TGO', 'Ferritina']
    for query in queries:
        real_search(agent, context, query)
    exams = {'exams': [{'code': code} for code in map(best_of, queries)]}
    assert asyncio.run(agent.CALLBACKS.before_tool(FakeTool('create_appointment'), exams, context)) == {
        'pending_confirmation': ['FICT-003', 'FICT-018', 'FICT-055']}
    assert context.requested[-1].split('\n')[-3:] == [
        "- baixa confiança: '2. Colesterol total e Triglicerideos' → Colesterol total FICT-006 (confiança 0,65); confira "
        "o pedido", "- não buscado pelo agente: '2. Colesterol total e Triglicerideos' → Triglicerideos FICT-009 "
        "(confiança 1,00); confira o pedido", 'Agendar estes 3 exames?']


def test_the_reviewed_order_searched_piece_by_piece_raises_nothing(agent):
    context = read(agent, REVIEWED, READINGS)
    queries = ['Hemoglobina glicada', 'Colesterol total', 'Triglicerideos', 'TGO', 'Ferritina']
    for query in queries:
        real_search(agent, context, query)
    reply, args = book(agent, context, *map(best_of, queries))
    assert check(agent, context, [exam['code'] for exam in args['exams']]) == []


def test_an_exam_a_search_returned_but_the_model_did_not_propose_is_left_out_by_the_agent(agent):
    # Triglicerídeos came back from a search (as a neighbour, never its best match), so it is "não incluído".
    context = read(agent, ['Exames: Colesterol total, Triglicerideos'])
    real_search(agent, context, 'Colesterol total e Triglicerideos')
    reply, args = book(agent, context, best_of('Colesterol total'))
    found = check(agent, context, [exam['code'] for exam in args['exams']])
    assert [(code, reason) for code, _, reason, _, _ in found] == [
        ('FICT-009', 'omitted' if 'FICT-009' in context.state['candidates'] else 'not_searched')]


@pytest.mark.parametrize('line, expected', [
    ('Obs.: acrescentar Ferritina', [('FICT-018', 'Ferritina')]),
    ('Obs: repetir TSH', [('FICT-024', 'TSH')]),
    ('Obs: repetir TSH em 30 dias', [('FICT-024', 'TSH')]),  # in a note, a piece that starts with the name
    ('Indicacao: hipotireoidismo, solicito TSH', [('FICT-024', 'TSH')]),
    ('Dr. [NOME] pede tambem TSH', [('FICT-024', 'TSH')]),
    ('Paciente: [NOME] Exames: Hemograma completo, TSH',  # two lines the OCR joined
     [('FICT-001', 'Hemograma completo'), ('FICT-024', 'TSH')]),
])
def test_an_exam_written_after_a_label_of_data_or_notes_is_still_checked(agent, line, expected):
    context = read(agent, ['- Glicose', line])
    real_search(agent, context, 'Glicose')
    reply, args = book(agent, context, best_of('Glicose'))
    assert [(code, name) for code, name, *_ in check(agent, context, [exam['code'] for exam in args['exams']])] == \
        expected


def test_the_parts_of_a_line_the_ocr_joined_are_checked_apart():
    assert order_lines(['Paciente: [NOME] Exames: Hemograma completo, TSH', 'RG: [RG] Nascimento: [DATA]',
                        'E-mail: [EMAIL]', 'Dr. [NOME] - [CRM]', 'Obs: jejum de 8 horas']) == [
        (0, 'Hemograma completo, TSH', False), (4, 'jejum de 8 horas', True)]


@pytest.mark.parametrize('lines', [
    ['Obs: jejum de 8 horas para glicose', 'Exames: Glicose'],
    ['Obs: jejum de 8 horas', '- Glicose'],  # "horas" is a word of Proteinúria de 24 horas, but it is a note
    ['Indicacao clinica: controle de glicose e tireoide', '- Glicose'],
    ['Observação: trazer exames anteriores de colesterol', '- Glicose'],
    ['Paciente: [NOME]', 'Médico: Dr. [NOME] CRM [CRM]', 'Data: [DATA]', '- Glicose'],
    ['SOLICITAÇÃO DE E [TEXTO_REMOVIDO]', '- Glicose'],  # a photo's header: "de" is not a word of an exam
])
def test_notes_and_personal_data_are_not_exams_of_the_order(agent, lines):
    context = read(agent, lines)
    real_search(agent, context, 'Glicose')
    reply, args = book(agent, context, best_of('Glicose'))
    assert check(agent, context, [exam['code'] for exam in args['exams']]) == []


def test_words_of_an_exam_booked_by_similarity_are_not_another_exam(agent):
    # "Hemoglobina glicda" is only similar to Hemoglobina glicada: the line holds its words, and
    # "Hemoglobina" alone (another exam of the catalog) is not reported.
    context = read(agent, ['- Hemoglobina glicda'])
    real_search(agent, context, 'Hemoglobina glicada')
    reply, args = book(agent, context, 'FICT-003')
    assert check(agent, context, [exam['code'] for exam in args['exams']]) == []


def test_a_poorly_read_line_reports_at_its_reading(agent):
    context = read(agent, ['1. Hemograma completo', '2. Creatinina'], [95.0, 55.0])
    real_search(agent, context, 'Hemograma completo')
    reply, args = book(agent, context, best_of('Hemograma completo'))
    assert check(agent, context, ['FICT-001']) == [('FICT-005', 'Creatinina', 'not_searched', 0.55, '2. Creatinina')]


UNDECIDED = [{'code': 'FICT-009', 'name': 'Triglicerídeos', 'confidence': 1.0, 'line': 8,
              'read': '2. Colesterol total e Triglicerideos', 'reason': 'not_searched'}]
ONE_BOOKED = {'id': 'a1', 'status': 'scheduled', 'exams': [{'code': 'FICT-003', 'name': 'Hemoglobina glicada'}]}


def test_the_cli_says_the_agent_left_an_exam_out_and_keeps_exit_0(ready_run, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(cli, 'run_agent', fake_run({'appointment': ONE_BOOKED, 'low_confidence': UNDECIDED}))
    assert cli.main(ready_run) == 0  # the appointment exists, and the person who confirmed the list sees the warning
    out = capsys.readouterr().out
    assert ("não buscado pelo agente: '2. Colesterol total e Triglicerideos' → Triglicerídeos FICT-009 "
            "(confiança 1,00); confira o pedido") in out
    assert ('Agendamento confirmado pela API: id a1, status scheduled; ATENÇÃO: 1 possível(is) exame(s) do '
            'pedido sem decisão do agente, confira os avisos acima') in out


@pytest.mark.parametrize('low, code', [(UNDECIDED, 3), ([{**UNDECIDED[0], 'reason': 'score'}], 0), ([], 0)])
def test_with_yes_an_exam_left_undecided_exits_3_not_0(ready_run, monkeypatch, capsys, low, code):  # noqa: F811
    # An independent evaluation: with --yes, a partial booking (an exam of the order the agent never decided on,
    # seen by nobody) exited 0, and automation took it for a success. Exams the rules left out are reported, not this.
    monkeypatch.setattr(cli, 'run_agent', fake_run({'appointment': ONE_BOOKED, 'low_confidence': low}))
    assert cli.main([*ready_run, '--yes']) == code == cli.UNDECIDED_WITH_YES * bool(code)
    assert 'Agendamento confirmado pela API: id a1, status scheduled' in capsys.readouterr().out


def test_without_the_catalog_search_the_report_says_the_order_was_not_checked(agent, monkeypatch):
    async def down(url, tool, texts, top_k):
        raise OSError('connection refused')
    monkeypatch.setattr(servidores, 'search_lines', down)
    order = OrderRecord(ocr_read=['- Glicose'], ocr_lines=['glicose'])
    callbacks = agent.CALLBACKS
    assert asyncio.run(servidores.unreported_exams(order, callbacks.search_url, callbacks.search_tool, callbacks.policy)) == ([], True)
    order.order_unchecked = True
    assert 'Aviso: o pedido não foi conferido por inteiro' in relatorio.report(order, True)


def test_the_final_message_ends_the_list_of_exams_with_a_blank_line():
    # An independent run-through: in `adk web`, Markdown joined the appointment line to the last exam of the list.
    booked = OrderRecord(ocr_lines=['creatinina'], booked_appointment={
        'id': 'a1', 'status': 'scheduled', 'exams': [{'code': 'FICT-005', 'name': 'Creatinina'}]})
    assert relatorio.report(booked, True).endswith('\n- Creatinina (FICT-005)\n\nAgendamento confirmado pela API: id a1, '
                                                   'status scheduled')
    listed = OrderRecord(ocr_lines=['creatinina'], listing=[{'code': 'FICT-005', 'name': 'Creatinina', 'confidence': 1.0,
                                                              'check': False}])
    assert relatorio.report(listed, False).endswith('\n- Creatinina (FICT-005), confiança 1,00\n\n1 exame(s) listado(s); '
                                                    'nada foi agendado')


@pytest.mark.xdist_group('spec-ports')  # the real servers, on the spec's ports: one worker, in turn
def test_the_pieces_are_searched_on_the_real_rag_server(servers):  # noqa: F811
    spec = load_spec(cli.DEFAULT_SPEC)
    hits = asyncio.run(servidores.search_lines(servers['rag'], 'search_exams', ['Colesterol total e Triglicerideos'],
                                               spec.plugins[0].kwargs['top_k']))
    pieces = {hit['piece']: hit['code'] for hit in reversed(hits['Colesterol total e Triglicerideos'])}
    assert pieces == {'Colesterol total': 'FICT-006', 'Triglicerideos': 'FICT-009'}  # best hit of each piece



def tool_search(agent, context, query):
    """The search_exams tool's reply (the line cut into its exams), through the after_tool_callback."""
    hits = catalog_search(query)
    agent.CALLBACKS.after_tool(FakeTool('search_exams'), {'query': query}, context, {'structuredContent': {'result': hits}})
    best: dict = {}
    for hit in hits:  # the best hit of each piece, as the model proposes them
        best.setdefault(hit.get('piece', query), hit['code'])
    return list(best.values())


def outcome(agent, context, *codes):
    """(booked, left out with reason and confidence, added by the check of the whole order)."""
    reply, args = book(agent, context, *codes)
    booked = [] if reply else [exam['code'] for exam in args['exams']]
    left_out = [(item['code'], item['reason'], item['confidence']) for item in context.state['low_confidence']]
    return booked, left_out, [(code, reason, confidence) for code, _, reason, confidence, _ in check(agent, context, booked)]


# An order with "1) TSH e T4 livre" read as "1) TSHe T4 livre": the model searched the line without its
# marker, got T4 livre at 0,76 (the line had no separator) and TSH ended in no state at all. The check
# of the whole order cut the line the same way, so it missed TSH too.
GLUED = [  # (line read, the exam glued to the "e", the other exam, the glued exam's confidence)
    ('1) TSHe T4 livre', 'FICT-024', 'FICT-025', 0.86),
    ('1. TSHe T4 livre', 'FICT-024', 'FICT-025', 0.86),
    ('TSHe T4 livre', 'FICT-024', 'FICT-025', 0.86),
    ('- TSHe T4 livre', 'FICT-024', 'FICT-025', 0.86),
    ('1) TSH eT4 livre', 'FICT-025', 'FICT-024', 0.94),
    ('1) Ureiae Creatinina', 'FICT-004', 'FICT-005', 0.91),
]


@pytest.mark.parametrize('line, glued, other, confidence', GLUED)
def test_an_exam_glued_to_the_connective_is_booked_or_asked_by_its_confidence(agent, line, glued, other, confidence):
    context = read(agent, ['Solicito:', line])
    proposed = tool_search(agent, context, order_lines([line])[0][1])
    assert sorted(proposed) == sorted([glued, other])
    booked, left_out, late = outcome(agent, context, *proposed)
    if confidence >= agent.CALLBACKS.policy.min_confidence:
        assert sorted(booked) == sorted([glued, other]) and left_out == []
    else:  # TSHe is TSH at 0,86: asked, and without anyone to answer, left out with a warning
        assert booked == [other] and left_out == [(glued, 'needs_confirmation', confidence)]
    assert late == []


@pytest.mark.parametrize('line, glued, other, confidence', GLUED)
def test_an_exam_glued_to_the_connective_the_model_left_out_is_reported(agent, line, glued, other, confidence):
    # The reviewed run: the whole line searched, only the other exam proposed.
    context = read(agent, ['Solicito:', line])
    tool_search(agent, context, order_lines([line])[0][1])
    booked, left_out, late = outcome(agent, context, other)
    assert booked == [other] and left_out == [(glued, 'omitted', confidence)] and late == []


@pytest.mark.parametrize('line, glued, other, confidence', GLUED)
def test_an_exam_glued_to_the_connective_the_model_never_searched_is_reported(agent, line, glued, other, confidence):
    context = read(agent, ['Solicito:', '- Glicose', line])
    tool_search(agent, context, 'Glicose')
    booked, left_out, late = outcome(agent, context, best_of('Glicose'))
    assert booked == [best_of('Glicose')] and left_out == []
    assert sorted(late) == sorted([(glued, 'not_searched', confidence), (other, 'not_searched', 1.0)])
