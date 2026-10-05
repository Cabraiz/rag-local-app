"""RAG MCP server: catalog volume, normalization, synonyms and ranking."""
import re

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_servers import rag


def first_code(query):
    return rag.search(query)[0]['code']


def test_catalog_has_120_unique_fictional_codes():
    codes = [exam['code'] for exam in rag.CATALOG]
    assert len(codes) == 120 == len(set(codes))
    assert all(re.fullmatch(r'FICT-\d{3}', code) for code in codes)


def test_exact_name_scores_one():
    assert rag.search('Hemograma completo')[0] == {'code': 'FICT-001', 'name': 'Hemograma completo', 'score': 1.0,
                                                   'term': 'Hemograma completo'}


@pytest.mark.parametrize('query, code', [
    ('Glicose', 'FICT-002'),               # synonym of "Glicemia de jejum"
    ('HbA1c', 'FICT-003'),                 # synonym of "Hemoglobina glicada"
    ('  CREATÍNINA ', 'FICT-005'),         # case, accent and spaces
    ('Hemograma compieto', 'FICT-001'),    # OCR typo
    ('Uréia sérica', 'FICT-004'),
])
def test_normalization_synonyms_and_typos(query, code):
    assert first_code(query) == code


def test_results_are_ranked_and_limited():
    hits = rag.search('hemoglobina', top_k=2)
    assert [hit['code'] for hit in hits] == ['FICT-003', 'FICT-001']
    assert [hit['score'] for hit in hits] == sorted((hit['score'] for hit in hits), reverse=True)


def test_unknown_exam_returns_no_codes():
    assert rag.search('xyzwq kkkk') == []


@pytest.mark.parametrize('query, top_k', [('', 3), ('   ', 3), ('a' * 201, 3), ('Ureia', 0), ('Ureia', 11), ('Ureia', True)])
def test_invalid_input_has_clear_error(query, top_k):
    with pytest.raises(ToolError):
        rag.search(query, top_k)


# Abbreviations as doctors write them on request forms -> catalog code (top hit, booking confidence).
ABBREVIATIONS = [
    ('Hemogr.', 'FICT-001'), ('Hemograma compl.', 'FICT-001'),
    ('Glic.', 'FICT-002'), ('Glicemia jej.', 'FICT-002'),
    ('HbA1c', 'FICT-003'), ('Hb glicada', 'FICT-003'), ('Creat.', 'FICT-005'),
    ('Col. total', 'FICT-006'), ('HDL-c', 'FICT-007'), ('LDL-c', 'FICT-008'),
    ('Triglic.', 'FICT-009'), ('Vit B12', 'FICT-021'),
    ('Vit D', 'FICT-023'), ('25-OH vit D', 'FICT-023'), ('25(OH)D', 'FICT-023'), ('TSH', 'FICT-024'), ('T4L', 'FICT-025'),
    ('PTH', 'FICT-043'), ('Somatomedina C', 'FICT-045'), ('β-HCG', 'FICT-047'), ('beta HCG', 'FICT-047'), ('PSA', 'FICT-048'),
    ('TGO', 'FICT-055'), ('TGP', 'FICT-056'), ('GGT', 'FICT-057'), ('CK-MB', 'FICT-068'),
    ('PCR', 'FICT-071'), ('VHS', 'FICT-072'), ('KPTT', 'FICT-084'), ('EAS', 'FICT-090'), ('Urina tipo 1', 'FICT-090'), ('SOF', 'FICT-096'),
    ('Toxo IgG', 'FICT-109'), ('Toxo IgM', 'FICT-110'), ('CMV IgG', 'FICT-113'), ('CMV IgM', 'FICT-114'),
]


@pytest.mark.parametrize('written, code', ABBREVIATIONS)
def test_request_form_abbreviations_find_the_exam(written, code):
    hit = rag.search(written, top_k=1)[0]
    assert (hit['code'], hit['score']) == (code, 1.0)


@pytest.mark.parametrize('text, expected', [
    ('β-HCG', 'beta hcg'), ('Hemograma compl.', 'hemograma completo'), ('Glicemia jej.', 'glicemia jejum'),
    ('α-fetoproteína', 'alfa fetoproteina'), ('Glicêmia-de JEJUM', 'glicemia de jejum'),
])
def test_normalization_expands_request_form_writing(text, expected):
    assert rag.normalize(text) == expected


def test_no_abbreviation_names_two_exams():
    owners = {}
    for exam in rag.CATALOG:
        for term in exam['terms']:
            owners.setdefault(term, set()).add(exam['code'])
    assert {term: codes for term, codes in owners.items() if len(codes) > 1} == {}


# A line with several exams is split before the search: searched whole, "Colesterol total e
# Triglicerideos" scored 0.65 and Triglicerideos was not even in the top 3.
@pytest.mark.parametrize('line, codes', [
    ('Colesterol total e Triglicerideos', ['FICT-006', 'FICT-009']),            # the line a blind judge saw
    ('COLESTEROL TOTAL E TGO', ['FICT-006', 'FICT-055']),                       # "E" in an upper-case line
    ('TSH, T4 livre / Ferritina', ['FICT-024', 'FICT-025', 'FICT-018']),        # clean print, 3 separators
    ('Calcitonina + Anti HBc total', ['FICT-044', 'FICT-105']),
    ('Ureia; Hemoglobina glicada; Fator reumatoide', ['FICT-004', 'FICT-003', 'FICT-073']),
    ('Ferritina e PSA total', ['FICT-018', 'FICT-048']),                         # real OCR lines, joined
    ('Proteinas totais, Alfa fetoproteina', ['FICT-063', 'FICT-054']),
    ('HIV antigeno e anticorpos, TSH', ['FICT-102', 'FICT-024']),               # a catalog name holding " e "
])
def test_each_exam_of_a_line_is_searched_on_its_own(line, codes):
    hits = rag.search_line(line, 1)
    assert [(hit['code'], hit['score']) for hit in hits] == [(code, 1.0) for code in codes]
    assert all(hit['piece'] in line for hit in hits)


def test_a_catalog_name_with_e_is_never_split():
    assert rag.split_exams('HIV antigeno e anticorpos') == ['HIV antigeno e anticorpos']
    assert rag.search_line('HIV antigeno e anticorpos') == rag.search('HIV antigeno e anticorpos')


def test_a_list_marker_read_as_a_piece_is_dropped():
    # "5. TGO" read as "5; TGO" scored 0.75 searched whole; the "5" is not an exam.
    assert rag.split_exams('5; TGO') == ['TGO']
    assert [(hit['code'], hit['score'], hit['piece']) for hit in rag.search_line('5; TGO', 1)] == [('FICT-055', 1.0, 'TGO')]


@pytest.mark.parametrize('query', ['Hemograma completo', 'Glicose', 'Hemograma compieto', '25-OH vit D', 'CA 19-9',
                                   'hemoglobina', 'xyzwq kkkk', 'e, ;'])
def test_a_line_without_separators_gets_the_same_reply_as_before(query):
    assert rag.search_line(query, 3) == rag.search(query, 3)


def test_top_k_and_the_score_floor_hold_for_every_piece():
    hits = rag.search_line('hemoglobina, colesterol e glicose', 2)
    by_piece = {}
    for hit in hits:
        by_piece.setdefault(hit['piece'], []).append(hit)
    assert list(by_piece) == ['hemoglobina', 'colesterol', 'glicose']
    assert all(1 <= len(piece_hits) <= 2 for piece_hits in by_piece.values())
    assert all(hit['score'] >= rag.MIN_SCORE for hit in hits)
    assert [hit['score'] for hit in by_piece['hemoglobina']] == sorted((hit['score'] for hit in by_piece['hemoglobina']), reverse=True)


@pytest.mark.parametrize('query, top_k', [('a' * 201, 3), ('TSH, T4 livre', 0), ('TSH, T4 livre', 11), ('TSH, T4 livre', True)])
def test_invalid_input_has_a_clear_error_on_a_split_line_too(query, top_k):
    with pytest.raises(ToolError):
        rag.search_exams(query, top_k)


def best_by_piece(line):
    """piece -> (best exam, score, partial) of search_line."""
    best = {}
    for hit in rag.search_line(line, 3):
        best.setdefault(hit['piece'], (hit['name'], hit['score'], hit.get('partial', False)))
    return best


@pytest.mark.parametrize('line, expected', [
    # an ellipsis of the exam before it is searched completed, and located by the words written
    ('Toxoplasmose IgG e IgM', {'Toxoplasmose IgG': ('Toxoplasmose IgG', 1.0, False), 'IgM': ('Toxoplasmose IgM', 1.0, False)}),
    ('Rubeola IgG e IgM', {'Rubeola IgG': ('Rubeola IgG', 1.0, False), 'IgM': ('Rubeola IgM', 1.0, False)}),
    ('Citomegalovirus IgG / IgM', {'Citomegalovirus IgG': ('Citomegalovirus IgG', 1.0, False),
                                   'IgM': ('Citomegalovirus IgM', 1.0, False)}),
    ('PSA total e livre', {'PSA total': ('PSA total', 1.0, False), 'livre': ('PSA livre', 1.0, False)}),
    ('T4 livre e total', {'T4 livre': ('T4 livre', 1.0, False), 'total': ('T4 total', 1.0, False)}),
    ('Bilirrubina direta e indireta', {'Bilirrubina direta': ('Bilirrubina direta', 1.0, False),
                                       'indireta': ('Bilirrubina indireta', 1.0, False)}),
    ('Vitamina B12 e D', {'Vitamina B12': ('Vitamina B12', 1.0, False), 'D': ('Vitamina D', 1.0, False)}),
    # the reverse form: the disease after the classes
    ('IgG e IgM para toxoplasmose', {'IgG': ('Toxoplasmose IgG', 1.0, False),
                                     'IgM para toxoplasmose': ('Toxoplasmose IgM', 1.0, False)}),
    # the catalog has no Chagas IgM, nor any Anti HAV: the generic IgM is never booked alone from them
    ('Chagas IgG e IgM', {'Chagas IgG': ('Chagas IgG', 1.0, False), 'IgM': ('IgM', 1.0, True)}),
    ('Anti HAV IgG e IgM', {'Anti HAV IgG': ('HIV antigeno e anticorpos', 0.7, False), 'IgM': ('IgM', 1.0, True)}),
    # a sample or a time of the exam before it is no question about another exam
    ('Clearance de creatinina, urina 24h', {'Clearance de creatinina': ('Clearance de creatinina', 1.0, False)}),
    ('Beta HCG quantitativo, sangue', {'Beta HCG quantitativo': ('Beta HCG', 1.0, False)}),
    ('Urina 24h: proteinuria e clearance de creatinina',  # a sample before the exams: no question about Urina tipo I
     {'proteinuria': ('Proteinuria de 24 horas', 0.85, False), 'clearance de creatinina': ('Clearance de creatinina', 1.0, False)}),
    # exams of their own stay so
    ('Hemograma, urina', {'Hemograma': ('Hemograma completo', 1.0, False), 'urina': ('Urina tipo I', 0.83, False)}),
    ('Colesterol HDL e LDL', {'Colesterol HDL': ('Colesterol HDL', 1.0, False), 'LDL': ('Colesterol LDL', 1.0, False)}),
    ('TGO e TGP', {'TGO': ('AST', 1.0, False), 'TGP': ('ALT', 1.0, False)}),
    ('Ureia e creatinina', {'Ureia': ('Ureia', 1.0, False), 'creatinina': ('Creatinina', 1.0, False)}),
    ('Ferro e ferritina', {'Ferro': ('Ferro serico', 1.0, False), 'ferritina': ('Ferritina', 1.0, False)}),
    ('Hemograma, IgG, IgM', {'Hemograma': ('Hemograma completo', 1.0, False), 'IgG': ('IgG', 1.0, False),
                             'IgM': ('IgM', 1.0, False)}),
    ('FAN / IgE total', {'FAN': ('FAN', 1.0, False), 'IgE total': ('IgE total', 1.0, False)}),
    ('Rubeola IgM, Lipase, IgA', {'Rubeola IgM': ('Rubeola IgM', 1.0, False), 'Lipase': ('Lipase', 1.0, False),
                                  'IgA': ('IgA', 1.0, True)}),  # a line that names a disease: the generic IgA is only reported
    # The abbreviations of request forms take the ellipsis too: "Toxo IgG/IgM" is Toxoplasmose IgM, never the generic IgM.
    ('Toxo IgG/IgM', {'Toxo IgG': ('Toxoplasmose IgG', 1.0, False), 'IgM': ('Toxoplasmose IgM', 1.0, False)}),
    ('IgG e IgM para CMV', {'IgG': ('Citomegalovirus IgG', 1.0, False), 'IgM para CMV': ('Citomegalovirus IgM', 1.0, False)}),
])
def test_a_piece_that_is_part_of_the_exam_next_to_it_is_searched_as_that_exam(line, expected):
    assert best_by_piece(line) == expected
