"""The rules of the PII guard (guardrails/pii.py): one regex per type of personal data, the
name labels, and the word lists that tell names from exams and from the order's structure.
guardrails/pii.py applies them, in the order of its docstring; nothing here runs on its own.
"""
import re
from pathlib import Path

from catalogo import fold

FIRST_NAMES_FILE = Path(__file__).with_name('prenomes.txt')

# --- 1. One regex per type -------------------------------------------------------------
# "[ \t]" (space or tab) instead of "\s": a value never continues on the next line.
SEP = r'[ \t]?[.,/\-]?[ \t]?'  # between two digits of a document: "123.456", "123 456", "123,456" (OCR)
AFTER_LABEL = r'[ \t]*(?:n[º°o]\.?[ \t]*)?[:;]?[ \t]*'  # "CPF: ", "RG nº ", "cpf ", "CID;" (OCR)
REST_OF_LINE = r'[^\n]*[^\s]'
MONTHS = 'janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro'
MONTH_ABBR = 'jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez'
UF = 'ac|al|ap|am|ba|ce|df|es|go|ma|mt|ms|mg|pa|pb|pr|pe|pi|rj|rn|rs|ro|rr|sc|sp|se|to'
# Up to 3 lower-case pieces the OCR split off an e-mail's user name, each ending in a dot, a
# glued mark or one space: "raimundo. gomes 80@...", "joaquim/silva@...". A mark between
# spaces ends it, so "Bilirrubina indireta - e-mail ..." keeps the exam.
EMAIL_PIECES = r'\b(?:[a-zà-ÿ][a-zà-ÿ0-9_+-]*(?:[ \t]?\.[ \t]?|[^\w\s\[\]]|[ \t])){0,3}'


def after_label(labels: str, value: str = REST_OF_LINE, group: str = 'value_after_label') -> str:
    """Regex for `value` after one of `labels` (case-insensitive), captured in `group`."""
    return r'(?i:\b(?:' + labels + '))' + AFTER_LABEL + '(?P<' + group + '>' + value + ')'


PATTERNS = [
    ('EMAIL', r'''
        (?P<value> ''' + EMAIL_PIECES + r''' [\w.+-]+ @ [\w-]+ (?:\.[\w-]+)+ )  # maria@example.com, "maria. souza 80@..."
      | ''' + after_label(r'e-?mail') + r'''                                  # E-mail: mariaQgexample.com
      | (?P<value_spelled> [\w.+-]+ [ \t]* (?i:arroba|\(at\)|\[at\]) [ \t]* [\w-]+    # maria arroba example ponto com
            (?:[ \t]* (?i:ponto|\(dot\)|\[dot\]|\.) [ \t]* [\w-]+)+ )
      | (?P<value_domain> ''' + EMAIL_PIECES + r'''                           # no label and the "@" misread:
            [\w+-]+ (?:[ \t]*\.[ \t]*[\w+-]+)* [ \t]*                           # "souza96gexemplo. invalid", only
            (?:\.[ \t]*com | [.,][ \t]*(?:br|org|net|edu|gov|invalid|example)) \b ) # if looks_like_email (see mask)
    '''),

    ('ENDERECO', after_label(r'endere[çc]o|logradouro|resid[êe]ncia')            # Endereço: Rua X, 10
                 + '|' + after_label('cep', r'\d{5}-?\d{3}', 'value_cep') + r'''  # CEP 01310-100
      | (?P<value> (?i:\b(?:rua|r\.|avenida|av\.|travessa|alameda|rodovia)) [ \t] ''' + REST_OF_LINE + r'''  # Av. Paulista, 1000
      | \b\d{5}-\d{3}\b )                                                       # 01310-100
      | (?P<value_unit>                                                         # ap 302, casa 3, bloco B,
          (?: (?i:\b(?:ap|apto|apt|apartamento|casa|lote|quadra|qd|conjunto|cj|sala|andar))  # apto 12 - bloco C
              \.?[ \t]*(?:n[º°o]\.?[ \t]*)? \d{1,5}[A-Za-z]?\b
            | (?i:\b(?:bloco|torre))\.?[ \t]*(?:[A-Za-z]\d{0,3}|\d{1,4}[A-Za-z]?)\b )
          (?:[ \t]*[-,/]?[ \t]*(?i:bloco|torre)\.?[ \t]*[A-Za-z0-9]{1,3}\b)? )
    '''),

    ('CRM', r'''
        (?P<value>
          (?i:\bcrm) [ \t]*[-/:=.]?[ \t]*                  # CRM-SP 123456, CRM=SP, CRMSP
          (?:[A-Za-z][^\s\d] [ \t]*[-/:=.]?[ \t]*)?         # the state, one letter possibly misread: "CRM-R]"
          (?:n[º°o]\.?[ \t]*)? \d{4,7}
          (?:[ \t]*[-/][ \t]*[A-Za-z]{2}\b)?                # 123456/SP
        | (?i:\b(?:crn|grm|grn|cbm)) [ \t]*[-/:=.]?[ \t]*   # OCR misreads of CRM, only with state and number
          [A-Za-z]{2} [ \t]*[-/:=.]?[ \t]* \d{4,7}\b
        | (?i:\b[a-z]{1,3} [ \t]*-[ \t]* (?:''' + UF + r''')) [ \t]+ \d{4,7}\b  # deformed: "a RM-SP 651813", "CRu-sp 767396"
        )
    '''),

    ('SUS', after_label(r'cart[ãa]o(?:[ \t]+do)?[ \t]+sus|cart[ãa]o[ \t]+nacional[ \t]+de[ \t]+sa[úu]de|cns',
                        r'\d[\d \t]{13,20}\d')                                  # Cartão SUS: 898 0010 0123 4567
            + r'| (?P<value> \b\d{3}[ \t]?\d{4}[ \t]?\d{4}[ \t]?\d{4}\b )'),    # 15 digits in 3-4-4-4 groups

    ('PRONTUARIO', after_label(r'(?:n[º°o]\.?[ \t]*(?:do[ \t]+)?)?prontu[áa]rio|registro[ \t]+do[ \t]+paciente',
                               r'(?:[A-Za-z]{1,3}[-. ]?)?\d[\d.\-/]{2,20}')),   # Prontuário: AB-12345

    ('CPF', r'''
        (?P<value>
          (?<!\+55[ ]) (?<!\d)                              # after "+55 " the digits are a phone
          (?!\d{2}[ \t]+9[ \t]?\d{4}[- \t]?\d{4}(?!\d))     # "11 96101-0282" is a phone too
          \d (?:''' + SEP + r'''\d){10} (?!\d) )            # 11 digits: 123.456.789-00, 123.456,789-00 (OCR), 081,737.428/11
      | ''' + after_label(r'c\.?p\.?f\.?', r'''              # after the label, 3 to 11 digits: "CPF: 517.916."
            \d (?:''' + SEP + r'''\d){2,10} (?!''' + SEP + r'''\d) [.,/\-]?''') + r'''  # (the rest is on the next line)
    '''),

    ('RG', r'''
        (?P<value> \b\d{1,2}[.,]\d{3}[.,]\d{3}-[\dXx]\b )   # 12.345.678-9, 12,345.678-9 (OCR)
      | ''' + after_label(r'r\.?g\.?', r'''                  # RG: 1234567, RG MG-15.912.070, RG nº 31 270 551 X
            (?:[A-Za-z]{2}[ \t]*[-/]?[ \t]*)? \d (?:[ \t]?[\d.,\-]){3,14} [ \t]?[\dXx]\b''')),

    ('CID', after_label(r'cid(?:-?10)?', r'[A-Za-z]\d{2}(?:\.?\d{1,2})?\b')),  # CID-10: E11.9
    ('CLINICO', after_label(r'indica[çc][ãa]o(?:[ \t]+cl[íi]nica)?|hip[óo]tese[ \t]+diagn[óo]stica|diagn[óo]stico')),
    ('CONVENIO', after_label(r'conv[êe]nio|carteirinha|carteira(?:[ \t]+do[ \t]+conv[êe]nio)?|matr[íi]cula')),
    ('IDADE', after_label('idade', r'\d{1,3}(?:[ \t]*anos)?')),                 # Idade: 45 anos

    ('DATA', r'''
        (?P<value>
          \b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b               # 05/10/2026
        | \b\d{4}-\d{2}-\d{2}\b                             # 2026-10-05
        | (?i:\b\d{1,2} [ \t]*(?:de|[/.-])?[ \t]*            # 5 de outubro de 2026, 27/agosto/1954,
              (?:''' + MONTHS + '|' + MONTH_ABBR + r''')\.?  # 2 set. 1963, 15-setembro-1957
              [ \t]*(?:de|[/.-])?[ \t]* \d{4}\b)
        | (?i:\b(?:''' + MONTHS + r''') [ \t]+(?:de[ \t]+)? \d{4}\b)  # outubro de 1997
        )
    '''),

    ('TELEFONE', r'''
        (?P<value>
          (?: \+\d{1,3}[ \t]*(?:\(\d{1,4}\)|\d{1,4})        # +55 11, +1 (415)
            | \(\d{2}\) | \b\d{2} )                         # (11), 11
          [ \t]* (?:9[ \t]?)? \d{3,4} [- \t]? \d{4}\b       # 98765-4321, 9 0933 0160, 555-0339
        | \b9?\d{4}-\d{4}\b                                 # 3333-4444
        )
    '''),
]
COMPILED = [(kind, re.compile(pattern, re.VERBOSE)) for kind, pattern in PATTERNS]
# A CPF split in two lines: "CPF: 517.916." then "257-38".
CPF_START = re.compile(r'(?i:\bc\.?p\.?f\.?)' + AFTER_LABEL + r'(?P<part>\d(?:' + SEP + r'\d){2,9})[.,/\-]?[ \t]*$')
CPF_REST = re.compile(r'^[ \t]*(?P<part>\d(?:' + SEP + r'\d){0,9})(?!\d)')

# --- 2. Names, word by word ------------------------------------------------------------
WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")  # letters only: "Sant'Anna", "Anne-Louise"
TAG = re.compile(r'\[[A-Z]+\]')                      # a value already masked
NEXT_LABEL = re.compile(r'[^\W\d_][\w.]*[ \t]*:')   # "CPF:", "Data:": where a labelled name ends
# Name labels. "Paciente:" needs the colon (a header like "PEDIDO MEDICO" is not a label);
# "paciente", "mãe" and "pai" without it, and the titles, do not.
LABEL = re.compile(r'''(?ix)
    \b (?: paciente | nome (?:\s+do\s+paciente)? | m[ée]dic[oa] (?:\s+solicitante)? | respons[áa]vel
         | solicitante | assinatura | acompanhante | m[ãa]e | pai ) [ \t]*: (?P<colon>)
  | \b (?: paciente | m[ãa]e | pai ) [ \t]
  | \b (?: dr | doutor | sr | senhor ) (?: \(a\) | a | ª | ta )? [.,]? ª? (?=[\s:]|$) :?  # Dr., Dra, (OCR), Sr(a).
''')

NAME_PARTICLES = frozenset(('da', 'de', 'do', 'das', 'dos', 'e'))
# Words of a sentence, never of a name: "coletar em março", "ana com dor".
PHRASE_WORDS = frozenset(('em', 'no', 'na', 'nos', 'nas', 'com', 'por', 'para', 'pela', 'pelo', 'os', 'as',
                          'um', 'uma', 'ao', 'sem', 'que', 'se', 'ou'))
PARTICLES = NAME_PARTICLES | PHRASE_WORDS | {'a', 'o'}
# Words of an order that are never names ("NAO realizar", "autoriza incluir", "Favor repetir"): they break
# a run of capitals the name rule would take for a name. They are not structure: the safety net still
# removes them, as [TEXTO_REMOVIDO], never counted as a name.
ORDINARY_WORDS = frozenset((
    'nao', 'sim', 'tambem', 'ja', 'favor', 'realizar', 'realizado', 'realizada', 'fazer', 'feito', 'feita',
    'repetir', 'refazer', 'incluir', 'acrescentar', 'adicionar', 'dosar', 'colher', 'coletar', 'pedir', 'solicitar',
    'autoriza', 'autorizo', 'considere', 'considerar', 'leve', 'levar', 'conta', 'evitar', 'suspender', 'suspenso',
    'suspensa', 'cancelar', 'cancelado', 'cancelada', 'dispensar', 'dispensado', 'agendar', 'marcar', 'nota',
    'leitor', 'automatizado', 'anterior', 'resultado', 'ultimo', 'reagiu', 'mal', 'urgente'))
# Field names: right before a masked value they are labels ("CPF [CPF]"), not names.
FIELD_NAMES = frozenset(('cpf', 'rg', 'crm', 'cep', 'cid', 'cns', 'sus', 'data', 'nascimento', 'nasc', 'telefone',
                         'tel', 'celular', 'whatsapp', 'contato', 'email', 'endereco', 'idade', 'convenio',
                         'carteirinha', 'prontuario', 'identidade'))
# Words that qualify an exam ("TSH ULTRASSENSÍVEL", "Hemograma Completo Com Plaquetas"): exam words too.
EXAM_MODIFIERS = frozenset(('ultrassensivel', 'ultra', 'sensivel', 'fracoes', 'fracao', 'plaquetas', 'total',
                            'livre', 'serico', 'serica', 'soro', 'plasma', 'sangue', 'urina', 'jejum', 'basal',
                            'dosagem', 'pesquisa', 'contagem', 'quantitativo', 'quantitativa', 'qualitativo',
                            'qualitativa', 'completo', 'completa', 'automatizado', 'colesterol'))
# Header words: a line with one of them is a header, not a name.
NOT_NAMES = frozenset(('pedido', 'exame', 'exames', 'solicitacao', 'requisicao', 'laboratorio', 'clinica',
                       'hospital', 'centro', 'unidade', 'medico', 'medica', 'assinatura', 'diagnostico',
                       'indicacao', 'observacao', 'urgente', 'rotina', 'paciente', 'nome'))
FIRST_NAMES = frozenset(fold(line.strip()) for line in FIRST_NAMES_FILE.read_text(encoding='utf-8').splitlines()
                        if line.strip() and not line.startswith('#')) - set(fold(MONTHS).split('|'))

# --- 4. Safety net: only what looks like an exam leaves the OCR ----------------------------
# A line is split in pieces; a piece that fails is split again where two exams may be joined
# by the OCR ("Acido urlco e Vitamlna D"): the whole piece first keeps "HIV antigeno e anticorpos".
PIECES = re.compile(r'([,;():]|\s[-–—]\s)')
JOINED = re.compile(r'(\s(?:e|E|\+|/)\s)')
# Words of an order's structure: with labels and masked values they make a piece that may leave.
STRUCTURE = NOT_NAMES | FIELD_NAMES | PARTICLES | frozenset((
    'pedidos', 'medicos', 'solicito', 'solicita', 'solicitados', 'solicitado', 'laboratoriais', 'laboratorial',
    'clinico', 'receituario', 'obs', 'observacoes', 'carimbo', 'dr', 'dra', 'sr', 'sra', 'doutor', 'doutora',
    'responsavel', 'solicitante', 'cartao', 'plano', 'fone', 'mail'))

UNITS = frozenset(('mg', 'ml', 'dl', 'ui', 'h', 'hs', 'hrs', 'min', 'x'))
AMOUNT = re.compile(r'\d+(?:' + '|'.join(UNITS) + r')?')  # "100", "8h", "12hs"
# A long number on an exam line is neither part of an exam's name nor a lab value: a document, a card or
# a phone the rules did not recognize ("Glicose 98765432", "TSH 1234 5678 9012"). 5 or more digits, in
# groups or not, unless a unit follows ("150.000/mm3"); no exam name has more than 3 ("CA 125", "Urina 24h").
LAB_UNIT = (r'(?:%|/[ \t]*mm[³3]?|mm[³3]|[mµun]?g[ \t]*/[ \t]*d?l|[mµ]?ui[ \t]*/[ \t]*m?l|u[ \t]*/[ \t]*l|mmol|meq|'
            r'ng|pg|ml|mg|ui|cels?|c[ée]lulas|h|hs|hrs|horas?|dias?)')
LONG_NUMBER = re.compile(r'(?<![\w.,/-])\d(?:[ \t.\-/]?\d){4,}(?![\w])(?![ \t]*' + LAB_UNIT + r'(?![^\W\d_]))',
                         re.IGNORECASE)
OCR_DIGITS = str.maketrans('0158', 'olsb')  # digits the OCR reads for letters: "25(0H)D", "Lipa5e"
MARKS_BEFORE, MARKS_AFTER = re.compile(r'[^\w\[\]]*'), re.compile(r'[^\w\[\]]*$')  # marks around a piece
