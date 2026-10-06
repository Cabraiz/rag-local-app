"""Deterministic corpus of fictional PII for the masking tests (no LLM, fixed seed).

cases() returns (text, secrets): after mask(text) none of the secrets may remain.
Every value is generated: no real person, document or contact is used.
"""
import random
import unicodedata

SEED = 20261005
PER_KIND = 300

FIRST = ['Maria', 'José', 'João', 'Ana', 'Antônio', 'Francisca', 'Carlos', 'Paulo', 'Lúcia', 'Luiz',
         'Márcia', 'Fernanda', 'Gabriel', 'Letícia', 'Rafael', 'Sebastião', 'Conceição', 'Raimundo',
         'Aparecida', 'Benedito', 'Juliana', 'Tiago', 'Priscila', 'Otávio', 'Inês', 'Joaquim']
LAST = ['Silva', 'Souza', 'Oliveira', 'Santos', 'Pereira', 'Lima', 'Costa', 'Ribeiro', 'Almeida',
        'Carvalho', 'Gomes', 'Martins', 'Araújo', 'Barbosa', 'Rocha', 'Dias', 'Moreira', 'Cardoso',
        'Nascimento', 'Teixeira', 'Correia', 'Mendes', 'Freitas', 'Vieira', 'Assunção', 'Brandão']
PARTICLE = ['', '', 'da ', 'de ', 'dos ', 'do ']
NAME_LABELS = ['Paciente: ', 'Nome: ', 'Nome do paciente: ', 'Responsável: ', 'Médico: ', 'Medico: ',
               'Solicitante: ', 'Assinatura: ', 'Acompanhante: ', 'PACIENTE: ', 'paciente: ',
               'Dr. ', 'Dra. ', 'Dr ', 'DRA. ', 'Dr(a). ', 'Dr(a) ', 'DR(A). ', 'Dr.ª ', 'Drª ', 'Doutor ',
               'Doutora ', 'Doutor(a) ', 'Médico solicitante: ', 'Mãe: ', 'Mae: ', 'Pai: ',
               # no colon after the label, as in "paciente maria ficticia da silva"
               'paciente ', 'Paciente ', 'mae ', 'pai ']
STREETS = ['Rua', 'R.', 'Avenida', 'Av.', 'Travessa', 'Alameda', 'rua', 'AV.']
UF = ['SP', 'RJ', 'MG', 'BA', 'RS', 'PE', 'sp', 'rj']
MONTHS = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
          'outubro', 'novembro', 'dezembro']
PLANS = ['Plano Fictício Ouro', 'Unimed Fictícia', 'Saúde Exemplo Prata', 'Bradesco Fictício Saúde']


def strip_accents(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))


def digits(rng, count):
    return ''.join(rng.choice('0123456789') for _ in range(count))


def cpf_digits(rng):
    base = [rng.randrange(10) for _ in range(9)]
    for size in (9, 10):
        total = sum(d * (size + 1 - i) for i, d in enumerate(base))
        base.append((total * 10 % 11) % 10)
    return ''.join(map(str, base))


def cpf(rng):
    d = cpf_digits(rng)
    value = rng.choice([f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}', d, f'{d[:3]} {d[3:6]} {d[6:9]} {d[9:]}',
                        f'{d[:3]}.{d[3:6]}.{d[6:9]} {d[9:]}', f'{d[:9]}-{d[9:]}', f'{d[:3]}.{d[3:6]}.{d[6:9]}.{d[9:]}',
                        # one digit at a time, as written to dodge a CPF pattern or read by a poor OCR
                        ' . '.join(' '.join(d[i:i + 3]) for i in (0, 3, 6)) + ' - ' + ' '.join(d[9:])])
    return rng.choice(['CPF: {}', 'cpf {}', 'CPF nº {}', 'C.P.F.: {}', '{}', 'Documento: CPF {}', 'cpf.{}']).format(value), \
        [value, d]


def rg(rng):
    d = digits(rng, 8)
    check = rng.choice('0123456789X')
    value = rng.choice([f'{d[:2]}.{d[2:5]}.{d[5:]}-{check}', f'{d}{check}', d[:7], f'{d[:2]}.{d[2:5]}.{d[5:]}'])
    text = rng.choice(['RG: {}', 'rg {}', 'RG nº {}', 'R.G.: {}']).format(value)
    if '-' in value and rng.random() < 0.5:
        text = value  # the punctuated form with check digit is recognised without a label
    return text, [value]


def phone(rng):
    ddd, first, rest = str(rng.randrange(11, 100)), rng.choice(['9' + digits(rng, 4), '3' + digits(rng, 3)]), digits(rng, 4)
    value = rng.choice([f'({ddd}) {first}-{rest}', f'({ddd}){first}-{rest}', f'{ddd} {first}-{rest}',
                        f'{ddd} {first}{rest}', f'{ddd}{first}{rest}', f'+55 {ddd} {first}-{rest}',
                        f'+55 ({ddd}) {first}-{rest}', f'+55{ddd}{first}{rest}', f'{first}-{rest}'])
    return rng.choice(['Tel: {}', 'Telefone: {}', 'Celular: {}', 'Fone {}', 'Contato: {}', '{}']).format(value), \
        [value, first + rest]


def email(rng):
    user = strip_accents(f'{rng.choice(FIRST)}.{rng.choice(LAST)}').lower() + digits(rng, rng.randrange(3))
    domain = rng.choice(['example.com', 'example.com.br', 'clinica.example.org', 'mail.test'])
    form = rng.randrange(4)
    if form == 0:
        value = f'{user}@{domain}'
        return rng.choice(['E-mail: {}', 'Email: {}', 'email {}', 'Contato: {}', '{}']).format(value), [value, user]
    if form == 3:  # spelled out, with or without a label
        at, dot = rng.choice([(' arroba ', ' ponto '), (' (at) ', ' (dot) '), (' [at] ', ' [dot] '), (' ARROBA ', ' PONTO ')])
        value = user + at + domain.replace('.', dot)
        return rng.choice(['Contato: {}', '{}', 'E-mail: {}']).format(value), [user, user.split('.')[-1]]
    # OCR reads "@" as "Qg" and may split the address with spaces: only the label finds it.
    value = f'{user}Qg{domain}' if form == 1 else f'{user.replace(".", " .")}Qg{domain.replace(".", " ")}'
    return rng.choice(['E-mail: {}', 'Email: {}', 'email: {}', 'E-MAIL {}']).format(value), [user.split('.')[-1]]


def person(rng):
    words = [rng.choice(FIRST)] + [rng.choice(PARTICLE) + rng.choice(LAST) for _ in range(rng.randrange(1, 4))]
    name = ' '.join(words)
    if rng.random() < 0.3:
        name = strip_accents(name)
    return name


def name(rng):
    value = person(rng)
    if rng.random() < 0.5:  # labelled: any casing
        value = rng.choice([value, value.upper(), value.lower()])
        text = rng.choice(NAME_LABELS) + value
    else:  # a line that is only the name: capitalized or upper case
        value = rng.choice([value, value.upper()])
        text = value
    secrets = [word for word in value.split() if len(word) > 3]
    return text, secrets


def date(rng):
    day, month, year = rng.randrange(1, 29), rng.randrange(1, 13), rng.randrange(1930, 2027)
    value = rng.choice([f'{day:02d}/{month:02d}/{year}', f'{day}/{month}/{year % 100:02d}', f'{day:02d}-{month:02d}-{year}',
                        f'{day:02d}.{month:02d}.{year}', f'{year}-{month:02d}-{day:02d}',
                        f'{day} de {MONTHS[month - 1]} de {year}'])
    return rng.choice(['Data: {}', 'Nascimento: {}', 'Data de nascimento: {}', 'DN: {}', '{}']).format(value), [value]


def crm(rng):
    number, uf = digits(rng, rng.randrange(4, 7)), rng.choice(UF)
    value = rng.choice([f'CRM-{uf} {number}', f'CRM/{uf} {number}', f'CRM {uf} {number}', f'CRM: {number}',
                        f'crm-{uf} {number}', f'CRM {number}-{uf}', f'CRM/{uf} nº {number}',
                        # separators and misreads seen in real OCR output
                        f'CRM={uf} {number}', f'CRM:{uf} {number}', f'CRM.{uf} {number}', f'CRM{uf} {number}',
                        f'CRN-{uf} {number}', f'GRM {uf} {number}', f'GRM={uf} {number}'])
    return rng.choice(['{}', 'Dr. Exemplo - {}', 'Assinado: {}']).format(value), [number]


def cid(rng):
    code = rng.choice('ABCEIJKMNZ') + digits(rng, 2) + rng.choice(['', '.' + digits(rng, 1), digits(rng, 1)])
    code = rng.choice([code, code.lower()])
    return rng.choice(['CID: {}', 'CID-10: {}', 'CID {}', 'cid10 {}', 'CID nº {}']).format(code), [code]


def address(rng):
    number = str(rng.randrange(1, 5000))
    street = f'{rng.choice(STREETS)} {rng.choice(LAST)} {rng.choice(LAST)}'
    cep = digits(rng, 5) + '-' + digits(rng, 3)
    options = [(f'{street}, {number}', street), (f'Endereço: {street}, {number} - apto {rng.randrange(1, 300)}', street),
               (f'Endereço residencial: {rng.choice(LAST)} {number}', number), (f'CEP {cep}', cep),
               (f'CEP: {cep.replace("-", "")}', cep.replace('-', '')), (cep, cep), (f'cep: {cep}', cep)]
    text, secret = rng.choice(options)
    return text, [secret]


def sus(rng):
    d = rng.choice('12789') + digits(rng, 14)
    value = rng.choice([d, f'{d[:3]} {d[3:7]} {d[7:11]} {d[11:]}'])
    return rng.choice(['Cartão SUS: {}', 'CNS: {}', 'Cartão Nacional de Saúde: {}', 'cartão do SUS {}', '{}']).format(value), \
        [value, d]


def plan(rng):
    card = rng.choice([digits(rng, 12), f'{digits(rng, 4)} {digits(rng, 4)} {digits(rng, 4)}', digits(rng, 9)])
    plan_name = rng.choice(PLANS)
    text, secrets = rng.choice([(f'Convênio: {plan_name} - Carteirinha {card}', [plan_name, card]),
                                (f'Carteirinha: {card}', [card]), (f'Nº da carteirinha: {card}', [card]),
                                (f'Matrícula do convênio: {card}', [card]), (f'convenio {plan_name}', [plan_name])])
    return text, secrets


def record(rng):
    number = rng.choice([digits(rng, 6), digits(rng, 8), f'AB-{digits(rng, 5)}', f'{digits(rng, 4)}/2026'])
    return rng.choice(['Prontuario {}', 'Prontuário: {}', 'Nº do prontuário {}', 'PRONTUARIO {}',
                       'Registro do paciente: {}']).format(number), [number]


GENERATORS = [cpf, rg, phone, email, name, date, crm, cid, address, sus, plan, record]


def cases():
    rng = random.Random(SEED)
    return [(generator.__name__,) + generator(rng) for generator in GENERATORS for _ in range(PER_KIND)]
