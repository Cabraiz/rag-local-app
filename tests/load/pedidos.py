"""Pedidos médicos fictícios para o teste de carga de dados sensíveis (semente fixa).

gerar(pasta, n) grava pedido-0001.png ... e manifesto.json, com os valores sensíveis e
os exames de cada imagem. Tudo é inventado a partir da semente:
- nomes: combinações de nomes e sobrenomes brasileiros comuns, com acento;
- CPF: formato real, mas o 2º dígito verificador é ERRADO de propósito, então nenhum
  CPF gerado é válido e nenhum pode ser de uma pessoa real (cpf_valido() confere);
- e-mails em .invalid (domínio reservado, nunca entregável), telefones, RG, CEP,
  CRM, carteirinha e endereço só com o formato real;
- de 1 a 5 exames de data/exams.json.
Três layouts e três fontes, com leve rotação e ruído para o OCR trabalhar.
"""
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from tests.pii_corpus import FIRST, LAST

SEED = 20261005
ROOT = Path(__file__).resolve().parents[2]
EXAMS = json.loads((ROOT / 'data' / 'exams.json').read_text(encoding='utf-8'))
FONTS = [f'/usr/share/fonts/truetype/dejavu/{name}.ttf' for name in ('DejaVuSans', 'DejaVuSerif', 'DejaVuSansMono')]
STREETS = ['Rua das Acácias', 'Avenida São João', 'Rua Professor Antônio Prado', 'Travessa da Conceição',
           'Rua Visconde de Inhaúma', 'Avenida Getúlio Vargas', 'Rua Dom Pedro II', 'Alameda dos Ipês']
DDD = ['11', '21', '31', '41', '51', '61', '71', '81', '85', '92']
UF = ['SP', 'RJ', 'MG', 'PR', 'RS', 'DF', 'BA', 'PE', 'CE', 'AM']
CID = ['E11.9', 'I10', 'E78.5', 'D50.9', 'N18.3', 'E03.9', 'Z00.0', 'K21.0']


def cpf_valido(cpf):
    """True se os dois dígitos verificadores batem (regra da Receita Federal)."""
    digits = [int(c) for c in cpf if c.isdigit()]
    for size in (9, 10):
        if (sum(d * (size + 1 - i) for i, d in enumerate(digits[:size])) * 10 % 11) % 10 != digits[size]:
            return False
    return True


def cpf_invalido(rng):
    """CPF com formato real e o 2º dígito verificador trocado: nunca é um CPF válido."""
    digits = [rng.randrange(10) for _ in range(9)]
    for size in (9, 10):
        digits.append((sum(d * (size + 1 - i) for i, d in enumerate(digits)) * 10 % 11) % 10)
    digits[10] = (digits[10] + rng.randrange(1, 10)) % 10  # o erro proposital
    d = ''.join(map(str, digits))
    return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'


def nome(rng):
    middle = rng.choice(['', 'da ', 'de ', 'dos ']) + rng.choice(LAST) + ' ' if rng.random() < 0.6 else ''
    return f'{rng.choice(FIRST)} {middle}{rng.choice(LAST)}'


def numero(rng, size):
    return ''.join(str(rng.randrange(10)) for _ in range(size))


def dados(rng):
    """Valores sensíveis de um pedido, como aparecem impressos."""
    paciente = nome(rng)
    login = '.'.join(paciente.split()[::2]).lower()
    rg = numero(rng, 8)
    return {
        'paciente': paciente,
        'nascimento': f'{rng.randrange(1, 29):02d}/{rng.randrange(1, 13):02d}/{rng.randrange(1940, 2015)}',
        'cpf': cpf_invalido(rng),
        'rg': f'{rg[:2]}.{rg[2:5]}.{rg[5:]}-{rng.choice("0123456789X")}',
        'telefone': f'({rng.choice(DDD)}) 9{numero(rng, 4)}-{numero(rng, 4)}',
        'email': f'{login}{rng.randrange(10, 99)}@exemplo.invalid',
        'endereco': f'{rng.choice(STREETS)}, {rng.randrange(10, 3000)}',
        'cep': f'{numero(rng, 5)}-{numero(rng, 3)}',
        'medico': nome(rng),
        'crm': f'CRM-{rng.choice(UF)} {numero(rng, 6)}',
        'carteirinha': f'{numero(rng, 4)} {numero(rng, 4)} {numero(rng, 4)} {numero(rng, 4)}',
        'cid': rng.choice(CID),
    }


def linhas(rng, d, exames, layout):
    """Texto do pedido em um de três layouts: rótulo por linha, campos lado a lado, receituário."""
    lista = [f'{i}. {exame}' for i, exame in enumerate(exames, 1)] if layout == 1 else [f'- {e}' for e in exames]
    data = f'{rng.randrange(1, 29):02d}/{rng.randrange(1, 13):02d}/2026'
    if layout == 0:
        return ['PEDIDO MÉDICO DE EXAMES', 'Clínica Exemplo de Diagnóstico', '',
                f'Paciente: {d["paciente"]}', f'Data de nascimento: {d["nascimento"]}', f'CPF: {d["cpf"]}',
                f'RG: {d["rg"]}', f'Telefone: {d["telefone"]}', f'E-mail: {d["email"]}',
                f'Endereço: {d["endereco"]}', f'CEP: {d["cep"]}', f'Carteirinha: {d["carteirinha"]}',
                f'CID-10: {d["cid"]}', '', 'Exames solicitados:', *lista, '',
                f'Dr. {d["medico"]} - {d["crm"]}', f'Data: {data}']
    if layout == 1:
        return ['SOLICITAÇÃO DE EXAMES LABORATORIAIS', '',
                f'Nome: {d["paciente"]}', f'Nascimento {d["nascimento"]}   CPF {d["cpf"]}',
                f'RG {d["rg"]}   Tel. {d["telefone"]}', f'Email: {d["email"]}',
                f'{d["endereco"]} - CEP {d["cep"]}', f'Convênio: Plano Exemplo - carteirinha {d["carteirinha"]}',
                f'CID {d["cid"]}', '', 'Exames:', *lista, '', f'Médico: {d["medico"]}', d['crm'], data]
    return ['RECEITUÁRIO', '', d['paciente'].upper(), f'Nasc. {d["nascimento"]} - Carteirinha {d["carteirinha"]}',
            f'CPF {d["cpf"]} - RG {d["rg"]}',
            f'{d["telefone"]} / {d["email"]}', f'{d["endereco"]}, CEP {d["cep"]}', f'Indicação clínica: CID {d["cid"]}',
            '', 'Solicito os exames:', *lista, '', f'Dra. {d["medico"]}', f'{d["crm"]}', f'{data}']


def fonte(index, size):
    try:
        return ImageFont.truetype(FONTS[index], size)
    except OSError:  # fora da imagem Docker, sem as fontes DejaVu
        return ImageFont.load_default(size)


def imagem(rng, texto, layout):
    """Desenha o texto em fundo branco, com leve rotação, desfoque e ruído."""
    font = fonte(layout, rng.randrange(24, 30))
    image = Image.new('L', (1100, 60 + 44 * len(texto)), 255)
    draw = ImageDraw.Draw(image)
    for row, text in enumerate(texto):
        draw.text((60, 30 + 44 * row), text, fill=rng.randrange(0, 60), font=font)
    for _ in range(600):  # sujeira de digitalização
        draw.point((rng.randrange(image.width), rng.randrange(image.height)), fill=rng.randrange(120, 220))
    image = image.rotate(rng.uniform(-1.2, 1.2), expand=True, fillcolor=255, resample=Image.BICUBIC)
    return image.filter(ImageFilter.GaussianBlur(rng.uniform(0, 0.6)))


def pedido(rng, index):
    """(nome do arquivo, imagem, manifesto) do pedido de número index."""
    layout = index % 3
    exames = [exam['name'] for exam in rng.sample(EXAMS, rng.randrange(1, 6))]
    sensiveis = dados(rng)
    image = imagem(rng, linhas(rng, sensiveis, exames, layout), layout)
    codes = {exam['name']: exam['code'] for exam in EXAMS}
    return f'pedido-{index:04d}.png', image, {
        'layout': layout, 'sensiveis': sensiveis, 'exames': [{'code': codes[e], 'name': e} for e in exames]}


def gerar(pasta, n, seed=SEED):
    """Grava n imagens e manifesto.json em pasta; devolve o manifesto {arquivo: {...}}."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    rng, manifesto = random.Random(seed), {}
    for index in range(1, n + 1):
        filename, image, item = pedido(rng, index)
        image.save(pasta / filename)
        manifesto[filename] = item
    (pasta / 'manifesto.json').write_text(json.dumps(manifesto, ensure_ascii=False, indent=1), encoding='utf-8')
    return manifesto
