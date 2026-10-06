"""How the project compares texts: catalogo.fold and words (RAG, PII, booking), the search's
normalize, and the injection guard's own variant, which also undoes the tricks of an attack."""
import pytest

import catalogo
from guardrails import injection


@pytest.mark.parametrize('text, expected', [
    ('Glicêmia', 'glicemia'),
    ('ÁCIDO ÚRICO', 'acido urico'),
    ('Conceição', 'conceicao'),
    ('Straße', 'strasse'),
    ('Glicêmia-de JEJUM.', 'glicemia-de jejum.'),  # punctuation and spaces stay
])
def test_fold_lowers_the_case_and_drops_the_accents(text, expected):
    assert catalogo.fold(text) == expected


@pytest.mark.parametrize('text, expected', [
    ('Ferro sérico — Conceição!', 'ferro serico conceicao'),
    ('  TSH,  T4 livre;\tPSA ', 'tsh t4 livre psa'),
    ('25(OH)D', '25 oh d'),
    ('', ''),
    (None, 'none'),  # a value from the model that is not text is compared as its str()
    (12, '12'),
])
def test_words_keeps_only_the_words_one_space_apart(text, expected):
    assert catalogo.words(text) == expected


@pytest.mark.parametrize('text, expected', [
    ('β-HCG', 'beta hcg'),
    ('Hemograma compl.', 'hemograma completo'),
    ('Glicemia jej.', 'glicemia jejum'),
])
def test_the_search_spells_out_greek_letters_and_expands_request_form_abbreviations(text, expected):
    assert catalogo.normalize(text) == expected
    assert catalogo.words(text) != expected  # the other callers compare what was written


@pytest.mark.parametrize('hidden', [
    'ig\u200bno\u200dre',  # zero-width characters inside the word
    'іgnоrе',  # Cyrillic і, о and е that look like Latin letters
])
def test_the_injection_variant_undoes_what_an_attack_hides(hidden):
    assert injection.normalize(hidden) == 'ignore'
    assert catalogo.fold(hidden) != 'ignore'  # why the injection guard keeps its own variant


def test_full_width_letters_are_folded_by_both():
    assert injection.normalize('ＩＧＮＯＲＥ') == catalogo.fold('ＩＧＮＯＲＥ') == 'ignore'


def test_the_injection_variant_ends_in_fold():
    assert injection.normalize('SISTEMA: Instrução') == catalogo.fold('SISTEMA: Instrução') == 'sistema: instrucao'
