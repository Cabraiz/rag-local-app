from pathlib import Path

import pytest
from pii_corpus import cases

from guardrails.pii import mask, mask_page

VOCABULARY = frozenset({'hemograma', 'completo', 'glicemia', 'jejum', 'glicose', 'creatinina',
                        'hemoglobina', 'glicada'})


@pytest.mark.parametrize('text, expected, kind', [
    ('Paciente: Maria da Silva Souza', 'Paciente: [NOME]', 'NOME'),
    ('Paciente: MARIA DA SILVA SOUZA', 'Paciente: [NOME]', 'NOME'),
    ('Dr. Carlos Pereira', 'Dr. [NOME]', 'NOME'),
    ('CPF: 123.456.789-00', 'CPF: [CPF]', 'CPF'),
    ('CPF 12345678900', 'CPF [CPF]', 'CPF'),
    ('cpf 12345678900', 'cpf [CPF]', 'CPF'),
    ('12345678900', '[CPF]', 'CPF'),
    ('cpf: 123456789-00', 'cpf: [CPF]', 'CPF'),
    ('Av. Paulista, 1000', '[ENDERECO]', 'ENDERECO'),
    ('Responsável: maria souza', 'Responsável: [NOME]', 'NOME'),
    ('Assinatura: Carlos Pereira', 'Assinatura: [NOME]', 'NOME'),
    ('Médico: Dr. Carlos Pereira', 'Médico: [NOME]', 'NOME'),
    ('Endereço: Rua das Flores 123', 'Endereço: [ENDERECO]', 'ENDERECO'),
    ('Rua das Flores, 123 - Centro', '[ENDERECO]', 'ENDERECO'),
    ('CEP 01310-100', 'CEP [ENDERECO]', 'ENDERECO'),
    ('RG: 12.345.678-9', 'RG: [RG]', 'RG'),
    ('Telefone: (11) 98765-4321', 'Telefone: [TELEFONE]', 'TELEFONE'),
    ('Contato: maria.souza@example.com', 'Contato: [EMAIL]', 'EMAIL'),
    # Tesseract may read "@" as "Qg" and split the address: the label still masks it.
    ('Email: pessoa .sentinelaQgexample invalid', 'Email: [EMAIL]', 'EMAIL'),
    ('Data: 05/10/2026', 'Data: [DATA]', 'DATA'),
    ('CRM-SP 123456', '[CRM]', 'CRM'),
    ('Convênio: Plano Fictício Saúde', 'Convênio: [CONVENIO]', 'CONVENIO'),
    ('Idade: 45 anos', 'Idade: [IDADE]', 'IDADE'),
    ('CID: E11.9', 'CID: [CID]', 'CID'),
    ('Indicação clínica: acompanhamento de diabetes', 'Indicação clínica: [CLINICO]', 'CLINICO'),
    ('Diagnóstico: hipertensão', 'Diagnóstico: [CLINICO]', 'CLINICO'),
])
def test_each_pii_type_is_masked_and_counted(text, expected, kind):
    assert mask(text, VOCABULARY) == (expected, {kind: 1})


def test_a_labelled_name_stops_before_the_next_label():
    assert mask('Paciente: Maria Souza   CPF: 123.456.789-00', VOCABULARY) == (
        'Paciente: [NOME]   CPF: [CPF]', {'NOME': 1, 'CPF': 1})


def test_full_order_keeps_exams_and_masks_one_value_per_line():
    text = ('PEDIDO DE EXAMES\nPaciente: MARIA DA SILVA\nJOSE CARLOS PEREIRA\nIdade: 45 anos\n'
            'HEMOGRAMA COMPLETO\nGLICEMIA EM JEJUM\nCreatinina\nDr. Carlos Lima CRM-SP 123456')
    lines, counts = mask_page(text.splitlines())
    assert lines == ['PEDIDO DE EXAMES', 'Paciente: [NOME]', '[NOME]', 'Idade: [IDADE]',
                                   'HEMOGRAMA COMPLETO', 'GLICEMIA EM JEJUM', 'Creatinina',
                                   'Dr. [NOME] [CRM]']
    assert counts == {'NOME': 3, 'IDADE': 1, 'CRM': 1}


@pytest.mark.parametrize('line, expected', [
    ('MARIA DA SILVA SOUZA', '[NOME]'),
    ('ANA PAULA LIMA - CRM-SP 123456', '[NOME] - [CRM]'),
    ('DRA ANA PAULA LIMA', 'DRA [NOME]'),
    ('PEDIDO DE EXAMES', 'PEDIDO DE EXAMES'),
    ('PEDIDO MEDICO FICTICIO', '[TEXTO_REMOVIDO]'),
    ('JOAO FICTICIO DA SILVA', '[NOME]'),
    ('Pessoa Sentinela ZQX', '[TEXTO_REMOVIDO]'),
    ('Laboratorio Ficticio Beta', '[TEXTO_REMOVIDO]'),
    ('Hemoglobina Glicada', 'Hemoglobina Glicada'),
    ('Dra. Fictícia Lima - CRM-SP 123456', 'Dra. [NOME] - [CRM]'),
    ('HEMOGRAMA COMPLETO', 'HEMOGRAMA COMPLETO'),
    ('GLICEMIA EM JEJUM', 'GLICEMIA EM JEJUM'),
    ('TSH', 'TSH'),
])
def test_unlabelled_name_lines_are_masked_but_exams_and_headers_are_not(line, expected):
    # A name alone on a line is the safety net's (rule 4): [NOME] with a common first name.
    assert mask_page([line])[0] == [expected]


@pytest.mark.parametrize('exam', [
    'Hemograma completo', 'Glicemia de jejum', 'TSH', 'T4 livre', 'Vitamina D 25-OH',
    'Hemoglobina glicada (HbA1c)', 'Nome do exame: Creatinina', 'Urina tipo 1', '',
])
def test_exam_names_are_not_masked(exam):
    assert mask(exam, VOCABULARY) == (exam, {})


def test_default_vocabulary_comes_from_the_catalog():
    from guardrails.pii import VOCABULARY
    assert 'hemograma' in VOCABULARY
    assert mask('HEMOGRAMA COMPLETO') == ('HEMOGRAMA COMPLETO', {})
    assert mask_page(['MARIA DA SILVA']) == (['[NOME]'], {'NOME': 1})


@pytest.mark.parametrize('ocr_line, expected', [
    # Exact OCR lines from fictional test requests where the name or CRM once leaked.
    ('Dr(a). Gisele Sentinela Quimera - [CRM]', 'Dr(a). [NOME] - [CRM]'),
    ('Médico solicitante: [NOME] - CRM=SP 582970', 'Médico solicitante: [NOME] - [CRM]'),
    # Load test (tests/load/carga.py): an e-mail without label whose "@" was misread,
    # an RG read with a comma and "Dra." read as "Dra,".
    ('[TELEFONE] / fernanda. souza96gexemplo. invalid', '[TELEFONE] / [EMAIL]'),
    ('[TELEFONE] / tiago.brandão56gexemplo . invalid', '[TELEFONE] / [EMAIL]'),
    ('[TELEFONE] / luiz95Gexemplo. invalid', '[TELEFONE] / [EMAIL]'),
    ('CPF [CPF] - RG 55,262.255-6', 'CPF [CPF] - RG [RG]'),
    ('CPF 035.045,985-15 - RG [RG]', 'CPF [CPF] - RG [RG]'),
    ('[TELEFONE] / raimundo. gomes80@exemplo.invalid', '[TELEFONE] / [EMAIL]'),
    ('(11) 95901-6896 / raimundo. gomes 80gexemplo. invalid', '[TELEFONE] / [EMAIL]'),
    ('CID-10; E78.5', 'CID-10; [CID]'),
    ('[TELEFONE] / priscila.nascimentollgexemplo, invalid', '[TELEFONE] / [EMAIL]'),
    ('TIAGO CARVALHO |', '[NOME] |'),
    ('Nascimento [DATA] CPF1 14.342.050-08', 'Nascimento [DATA] CPF[CPF]'),
    ('Dr. [NOME] - CRM-R] 554875', 'Dr. [NOME] - [CRM]'),
    ('[TELEFONE] / joaquim/silva45@exemplo.invalid', '[TELEFONE] / [EMAIL]'),
    ('[TELEFONE] / aparecida.teixeira93€exemplo.invalid', '[TELEFONE] / [EMAIL]'),
    # Ordinary text that ends a sentence before "com" is not an e-mail (and is not an exam either).
    ('Coletar em jejum. Com água', '[TEXTO_REMOVIDO]'),
    ('Coletar em jejum, com água', 'Coletar em jejum, [TEXTO_REMOVIDO]'),
    ('RG 55,262.255-6', 'RG [RG]'),
    ('Dra, Letícia Silva', 'Dra, [NOME]'),
    # OCR lines of fictional orders that once leaked. Names without a label: lower case, or with ' and -.
    ("JOANA D'ARC MACIEL", '[NOME]'),
    ('anne-louise bittencourt', '[NOME]'),
    ("KAUÃ D'ÁVILA BRANDÃO", '[NOME]'),
    ('josé maria assunção neto', '[NOME]'),
    ('wellington ximenes quaresma', '[NOME]'),
    ('conceição aparecida dorneles', '[NOME]'),
    ("Thaís Sant'Anna Moreira", '[NOME]'),
    ('YASMIN AL-HADDAD REZENDE', '[NOME]'),
    # ' and - no longer cut a labelled name; "Sr(a)." is a label.
    ("Paciente: Kauã D'Ávila Brandão", 'Paciente: [NOME]'),
    ("Responsável: Kauã D'Ávila Brandão", 'Responsável: [NOME]'),
    ("paciente joana d'arc maciel", 'paciente [NOME]'),
    ('Nome do paciente Anne-Louise Bittencourt', 'Nome do paciente [NOME]'),
    ("Sr(a). Ítalo D'Ângelo Furtado", 'Sr(a). [NOME]'),
    # CPF with "," and "/".
    ('CPF: 081,737.428/11', 'CPF: [CPF]'),
    ('CPF: 617,270.707/28', 'CPF: [CPF]'),
    # A name on an exam line: the exam stays.
    ('Hemograma completo - José Neto', 'Hemograma completo - [NOME]'),
    ('Ferro serico — Conceição Dorneles 11 96101-0282', 'Ferro serico — [NOME] [TELEFONE]'),
    ('Gioconda Valadares: Prolactina', '[NOME]: Prolactina'),
    ('Paratormonio, para YASMIN REZENDE', 'Paratormonio, para [NOME]'),
    ('CA 19-9 raí peçanha', 'CA 19-9 [NOME]'),
    # Phones, RG and dates.
    ('Telefone: +1 (415) 555-0339', 'Telefone: [TELEFONE]'),
    ('Whatsapp +55 (21) 9 0933 0160', 'Whatsapp [TELEFONE]'),
    ('RG MG-15.912.070', 'RG [RG]'),
    ('RG no 31 270 551 X', 'RG no [RG]'),
    ('nasc. 27/agosto/1954', 'nasc. [DATA]'),
    ('Nascimento 2 set. 1963', 'Nascimento [DATA]'),
    ('D.N.: 15-setembro-1957', '[TEXTO_REMOVIDO].: [DATA]'),
    ('Paciente nascida em outubro de 1997', '[TEXTO_REMOVIDO]'),
    # The misread e-mail no longer swallows the exam before it.
    ('Bilirrubina indireta - e-mail ozéiasWexemplo.invalid', 'Bilirrubina indireta - e-mail [EMAIL]'),
    # Handwritten orders: the OCR deforms the label ("Pactente:", "Prlo):", "ne).").
    ('Pactente: Augusto Quimera Frrcé', '[TEXTO_REMOVIDO]: [NOME]'),
    ('Poente: Mariana Hipotético Inventado', '[TEXTO_REMOVIDO]: [NOME]'),
    ('fedente: Augusto Sentinela Inventado', '[TEXTO_REMOVIDO]: [NOME]'),
    ('Prlo): Otávio Exemplar', '[TEXTO_REMOVIDO]): [NOME]'),
    ('Drta). otávio Exemplar', 'Drta). [NOME]'),
    ('Dra). Otávio Quimera', 'Dra). [NOME]'),
    ('Pra) Branca Quimera', '[TEXTO_REMOVIDO]) [TEXTO_REMOVIDO]'),
    ('prta) Joaquim sir', '[TEXTO_REMOVIDO]) [NOME]'),
    ('1). Mariana Modelar', '1). [NOME]'),
    ('ne). Mariana Exempsr', '[TEXTO_REMOVIDO]). [NOME]'),
    ('Foral. Celina Ficclonat', '[TEXTO_REMOVIDO]'),
    # ...or adds junk before, inside or after the name.
    ('cet Otávio Quimera Hipotético', '[NOME]'),
    ('den Bianca Hipotético Sentinela', '[NOME]'),
    ('o tranca Quimera Simulado', '[TEXTO_REMOVIDO]'),
    ('Augusto Hipotético simulado', '[NOME]'),
    ('Heitor Modelar piecional', '[NOME]'),
    ('Piero fosco Modelar Sumulado', '[TEXTO_REMOVIDO]'),
    ('Pere onça Exemplar Modelar', '[TEXTO_REMOVIDO]'),
    ('Mariana Tuvento to Ytod', '[NOME]'),
    ('Lívia Sentinela pç cics:', '[NOME]:'),
    ('Blanca Exemplar Insresvta.', '[TEXTO_REMOVIDO].'),
    ('Leticia E) ME', '[NOME]) [TEXTO_REMOVIDO]'),
    ('Lívia CA Quisira', '[NOME]'),
    ('Pra do din [ria Otávio Hipotético 1', '[NOME]'),
    # After a name label, the rest of the line: a surname after punctuation is masked too.
    ('Paciente: * Bianta Inventado e . [', 'Paciente: * [NOME]'),
    ('Paciente: Ot Á. a ; Modelar sã ( [', 'Paciente: [NOME]'),
    ('Paciente: fritos - Sonia «quimera', 'Paciente: [NOME]'),
    ('Paciente: Left La Sizy “estaris Sentinela', 'Paciente: [NOME]'),
    # A deformed CRM: "<letters>-<state> <number>".
    ('a RM-SP 651813', 'a [CRM]'),
    ('CRu-sp 767396', '[CRM]'),
])
def test_ocr_lines_that_once_leaked_are_masked(ocr_line, expected):
    assert mask_page([ocr_line])[0] == [expected]


@pytest.mark.parametrize('attack, secret', [
    # Writings that once reached the LLM unmasked: no label colon, spelled-out e-mail, digit by digit.
    ('paciente maria ficticia da silva', 'maria'),
    ('Mae: Joana Ficticia Souza', 'Joana'),
    ('maria.ficticia arroba example ponto invalid', 'ficticia'),
    ('maria (at) example (dot) invalid', 'maria'),
    ('1 2 3 . 4 5 6 . 7 8 9 - 0 0', '4 5 6'),
    ('cpf.123.456.789.00', '456'),
    ('Prontuario 778899', '778899'),
])
def test_evasive_writings_are_masked(attack, secret):
    masked, counts = mask(attack)
    assert counts and secret.casefold() not in masked.casefold()


def test_a_labelled_value_made_of_exam_words_is_not_a_name():
    assert mask('Obs: paciente em jejum') == ('Obs: paciente em jejum', {})


# --- Generated corpus (tests/pii_corpus.py, fixed seed): zero leaks, zero exams masked.


CORPUS = cases()


@pytest.mark.parametrize('kind, text, secrets', CORPUS, ids=[f'{c[0]}-{i}' for i, c in enumerate(CORPUS)])
def test_generated_pii_never_leaks(kind, text, secrets):
    lines, counts = mask_page(text.splitlines())
    masked = '\n'.join(lines)
    assert counts, f'nada mascarado em {text!r}'
    assert not [secret for secret in secrets if secret.casefold() in masked.casefold()], masked


@pytest.mark.parametrize('page, expected', [
    # The OCR put the end of the CPF on the next line.
    (['CPF: 517.916.', '257-38', 'Solicito:'], ['CPF: [CPF]', '[CPF]', 'Solicito:']),
    (['CPF: 826.126.', '509-47'], ['CPF: [CPF]', '[CPF]']),
    # A whole CPF does not take the next line's number with it (the safety net removes that alone).
    (['CPF: 123.456.789-00', '257-38'], ['CPF: [CPF]', '[TEXTO_REMOVIDO]']),
])
def test_a_cpf_split_in_two_lines_is_masked_on_both(page, expected):
    lines, counts = mask_page(page)
    assert lines == expected and counts['CPF'] == 1


@pytest.mark.parametrize('page, expected', [
    # The safety net: a line, or a piece of one, that does not look like an exam does not leave.
    (['Feres Dria). Clínica Ren Médica ato Ficcional'], ['Feres [TEXTO_REMOVIDO]). [TEXTO_REMOVIDO]']),
    (['Paciente: CPF: Celina Inventado Inventado'], ['Paciente: CPF: [TEXTO_REMOVIDO]']),  # no common first name
    (['prta a). Joaquim'], ['[TEXTO_REMOVIDO]). [NOME]']),
    (['RMSP 436497', 'Inventado'], ['[TEXTO_REMOVIDO]', '[TEXTO_REMOVIDO]']),
    # A surname alone is not an exam ("Lima" is 0.67 from "glicemia" by characters).
    (['Lima', 'OLIVEIRA', 'Hemograma completo - Lima'], ['[TEXTO_REMOVIDO]', '[TEXTO_REMOVIDO]',
                                                       'Hemograma completo - [TEXTO_REMOVIDO]']),
    # Two exams the OCR misread, joined by "e": each one is judged alone.
    (['Acido urlco e Vitamlna D', 'Urino tipo 1 e Vltamina D', 'Calcio i0nizado e Colestcrol LDL', 'Co 125 e Ferritino'],
     ['Acido urlco e Vitamlna D', 'Urino tipo 1 e Vltamina D', 'Calcio i0nizado e Colestcrol LDL', 'Co 125 e Ferritino']),
    # Inside an exam, only the exam's own words stay: a lower-case name with a first name off the
    # list goes too, as [TEXTO_REMOVIDO]: no name rule saw it, so it is not counted as a name.
    (['Anti HCV tobias fagundes', 'Anti HCV ruth senna', 'Anti HCV najla mourão', 'Hemograma completo uirá araripe'],
     ['Anti HCV [TEXTO_REMOVIDO]', 'Anti HCV [TEXTO_REMOVIDO]', 'Anti HCV [TEXTO_REMOVIDO]',
      'Hemograma completo [TEXTO_REMOVIDO]']),
    # ...while its modifiers, amounts, units and short words the OCR misread stay with it.
    (['1. Glicemia de jejum 8h', 'Glicose 100 mg/dl', 'Rubeola lgM e Bil. lndireta', 'Co total e Proteina C reotiva'],
     ['1. Glicemia de jejum 8h', 'Glicose [TEXTO_REMOVIDO] mg/dl', 'Rubeola lgM e Bil. lndireta', 'Co total e Proteina C reotiva']),
    # An abbreviation of an exam stays; a capitalized word that is not a first name is not a name.
    (['- Ferrit.', 'DADOS FICTICIOS - DEMONSTRACAO'], ['- Ferrit.', '[TEXTO_REMOVIDO] - [TEXTO_REMOVIDO]']),
    (['Clínica Exemplo de Diagnóstico'], ['[TEXTO_REMOVIDO]']),
    # What looks like an exam, and the order's structure, stays.
    (['PEDIDO MÉDICO DE EXAMES', 'Exames solicitados:', '- Hemograma completo', 'Hemoglobina glicada (HbA1c)',
      '- Hemogrma compieto', 'Paciente: [NOME]', 'CPF: [CPF] - RG [RG]', 'Data: [DATA]'],
     ['PEDIDO MÉDICO DE EXAMES', 'Exames solicitados:', '- Hemograma completo', 'Hemoglobina glicada (HbA1c)',
      '- Hemogrma compieto', 'Paciente: [NOME]', 'CPF: [CPF] - RG [RG]', 'Data: [DATA]']),
    (['1. Glicemia de jejum (coletar pela manhã)'], ['1. Glicemia de jejum ([TEXTO_REMOVIDO])']),
])
def test_what_does_not_look_like_an_exam_does_not_leave(page, expected):
    assert mask_page(page)[0] == expected


def test_two_misread_exams_joined_by_e_are_kept():
    # One OCR error in each exam of "<exam> e <exam>": each one is judged alone. "Anti DrA" (for
    # Anti DNA) reads as the title "Dra.", so a few in a hundred may still lose a piece.
    import random

    from guardrails.pii import catalog_terms as terms
    rng, errors = random.Random(20261006), {'i': 'l', 'l': '1', 'o': '0', 'e': 'c', 'a': 'o', 'u': 'o', 'n': 'r', 's': '5'}

    def misread(term):
        spot = rng.choice([i for i, char in enumerate(term) if char.lower() in errors])
        return term[:spot] + errors[term[spot].lower()] + term[spot + 1:]

    names = sorted({term for term in terms() if len(term) >= 6})
    lines = [f'{misread(a)} e {misread(b)}' for a, b in (rng.sample(names, 2) for _ in range(400))]
    lost = [line for line in lines if '[' in mask_page([line])[0][0]]
    assert len(lost) <= 4, lost  # 1% at most


@pytest.mark.parametrize('line', [
    # Ordinary phrases in lower case: not names (the first word is not a first name, or a
    # word like "com" shows it is a sentence).
    'suspender medicação', 'trazer documento com foto', 'coleta domiciliar', 'ana com dor de cabeça',
    'e trazer exames anteriores', 'manter jejum de 8 horas',
    # An exam line with no name keeps everything.
    'Hemograma completo - Urgente', 'Glicemia de jejum - coletar pela manhã', 'Exame: TSH',
    # First names that are also words ("março"/Marco, rosa, Glória) need a name around them.
    'coletar em março', 'Hemograma completo - coletar em março', 'Urina tipo 1 - cor rosa',
    'Hemograma completo Glória',
    # Exam names with a modifier outside the catalog are never a name.
    'TSH ULTRASSENSÍVEL', 'COLESTEROL TOTAL E FRAÇÕES', 'Hemograma Completo Com Plaquetas',
    # ". com" after an exam is a sentence, not an e-mail.
    'Glicemia de jejum. com 8h de jejum', 'Hemograma completo. com urgência', 'TSH. com jejum',
    'Vitamina B12. com jejum',
])
def test_phrases_and_exam_lines_without_a_name_are_kept(line):
    assert mask(line) == (line, {})


def test_first_names_are_never_exam_or_header_words():
    from catalogo import fold
    from guardrails.pii import VOCABULARY
    from guardrails.pii_rules import FIRST_NAMES, MONTHS, NOT_NAMES, PARTICLES
    months = set(fold(MONTHS).split('|'))
    assert len(FIRST_NAMES) > 300 and not FIRST_NAMES & (VOCABULARY | NOT_NAMES | PARTICLES | months)


def catalog_terms(spellings=True):
    """Every exam name and synonym, in 6 spellings (or just as written)."""
    from guardrails.pii import catalog_terms as terms
    if not spellings:
        return sorted(set(terms()))
    return sorted({v for t in terms() for v in (t, t.upper(), t.lower(), t.title(), '- ' + t, 'Exame: ' + t)})


MODIFIERS = ('ultrassensível', 'frações', 'com plaquetas', 'total', 'livre', 'sérico')


def legitimate_lines():
    """The legitimate lines of the injection corpus, plus every catalog exam in capitals and
    Title Case, followed by ". com jejum" and by a common modifier."""
    attacks = Path(__file__).resolve().parent / 'attacks'
    lines = {line for name in ('legit.txt', 'legit-pages.txt')
             for line in (attacks / name).read_text(encoding='utf-8').splitlines() if line and line != '---'}
    for term in catalog_terms(spellings=False):
        lines |= {term.upper(), term.title(), f'{term}. com jejum', f'{term.title()}. com 8h de jejum'}
        lines |= {f'{variant} {modifier}' for variant in (term, term.upper(), term.title())
                  for modifier in (MODIFIERS + tuple(m.upper() for m in MODIFIERS))}
    return sorted(lines)


@pytest.mark.parametrize('line', legitimate_lines())
def test_masking_never_removes_an_exam_from_a_legitimate_line(line):
    # Through mask_page: the masking rules and the safety net (rule 4) together.
    from catalogo import words
    from guardrails.pii import EXAM_TERMS
    masked = mask_page([line])[0][0]
    before = {term for term in EXAM_TERMS if f' {term} ' in f' {words(line)} '}
    after = {term for term in EXAM_TERMS if f' {term} ' in f' {words(masked)} '}
    assert before <= after, masked


@pytest.mark.parametrize('line', catalog_terms())
def test_no_catalog_exam_is_masked(line):
    assert mask_page([line]) == ([line], {})


@pytest.mark.parametrize('line, expected', [
    # Digits of a document, card or phone with any one mark between them: counted after the marks go.
    ('TSH 898*0010*0123*4567', 'TSH [TEXTO_REMOVIDO]'),
    ('Glicose 9_8765_4321', 'Glicose [TEXTO_REMOVIDO]'),
    ('Creatinina 1 2 3 4 5 6 7 8 9', 'Creatinina [TEXTO_REMOVIDO]'),
    ('Hemograma 123/456/789-00', 'Hemograma [CPF]'),
    ('CPF: 12_34_56_78_90_1', 'CPF: [TEXTO_REMOVIDO]'),  # short groups are no structure when they add up
    # After the exam's name, a number goes, whatever unit follows; a fasting time and a number of the name stay.
    ('Hemograma completo 12345678 h', 'Hemograma completo [TEXTO_REMOVIDO] h'),
    ('TSH 1234567 horas', 'TSH [TEXTO_REMOVIDO] horas'),
    ('Glicose 4.500.000 celulas', 'Glicose [TEXTO_REMOVIDO]'),
    ('TSH 12345 mg', 'TSH [TEXTO_REMOVIDO] mg'),
    ('- Glicemia 1234567 mg/dl', '- Glicemia [TEXTO_REMOVIDO] mg/dl'),
    ('- Ureia (11) 9O876-5432', '- Ureia ([TEXTO_REMOVIDO]) [TEXTO_REMOVIDO]'),
    ('- Creatinina Av Paulista 1000', '- Creatinina [NOME] [TEXTO_REMOVIDO]'),
    ('TSH 45 anos', 'TSH [TEXTO_REMOVIDO]'),
    ('Glicemia de jejum 12 horas', 'Glicemia de jejum 12 horas'),
    ('Urina 24 h', 'Urina 24 h'),
    # A capitalized word after the exam's name that is neither of an exam's name nor a qualifier: a surname.
    ('Exame: TSH Nascimento', 'Exame: TSH [TEXTO_REMOVIDO]'),
    ('Exame: Ureia (Franco)', 'Exame: Ureia ([TEXTO_REMOVIDO])'),
    ('Rubeola IgM e Goma glutamil transferase', 'Rubeola IgM e Goma glutamil transferase'),  # misread, kept
    # A name after the exam's whole name, even made of exam words ("Albina" is one OCR error from Albumina,
    # "Ferro" is an exam): 2 or more words that neither qualify the exam nor start another.
    ('Ferritina Albina Ferro', 'Ferritina [TEXTO_REMOVIDO]'),
    ('FERRITINA ALBINA FERRO', 'FERRITINA [TEXTO_REMOVIDO]'),
    ('TSH e T4 livre Joana Prado', 'TSH e T4 livre [NOME]'),
    ('Creatinina Clara Nunes', 'Creatinina [NOME]'),
    ('TSH Zé', 'TSH [TEXTO_REMOVIDO]'),  # one capitalized word that is not one OCR error from an exam word
    ('Ferritina Bia', 'Ferritina [TEXTO_REMOVIDO]'),
    # ...while qualifiers, a qualifier the OCR misread, an amount and the next exam stay.
    ('Hemograma completo com plaquetas', 'Hemograma completo com plaquetas'),
    ('HEMOGRAMA COMPIETO COM PLAQUETAS', 'HEMOGRAMA COMPIETO COM PLAQUETAS'),
    ('Ferritina e Ferro serico', 'Ferritina e Ferro serico'),
    ('TSH e T4 livre', 'TSH e T4 livre'),
    ('Vitamina D 25 OH', 'Vitamina D 25 OH'),
])
def test_numbers_and_names_on_an_exam_line_are_masked_by_their_shape(line, expected):
    assert mask_page([line])[0] == [expected]
