"""Pedidos médicos fictícios com aparência de letra de mão, para testar a robustez do OCR.

É uma simulação, não escrita real: desenha com Pillow e as fontes de letra de mão que vêm
com o Windows (Ink Free, Segoe Print, Segoe Script). Dois estilos:
- comum: tinta azul, inclinação e tamanho variando por palavra, tremor leve no traço;
- medico: "letra de médico", muito inclinada, letras encostando, palavras abreviadas
  ("Hemogr.", "Glic. jejum"), carimbo com CRM fictício girado e borrado e assinatura rabiscada.
Cerca de 70% das imagens viram foto ruim de celular (baixa resolução, desfoque, JPEG pesado,
sombra, pouco contraste, perspectiva, papel amassado, ruído, borda cortada); as outras ficam
limpas, como um scan. Tudo é inventado; o CPF tem o dígito verificador errado de propósito.

Na raiz do repositório (a mesma semente e as mesmas fontes geram as mesmas imagens):
    python exemplos/gerar_manuscrito.py --saida samples/manuscritos --comum 70 --medico 50
Escreve também gabarito.json: exames, PII e degradação de cada imagem.

As fontes vêm da pasta em FONTS_DIR ou, sem ela, das pastas de fontes do sistema (Windows,
Linux ou macOS). Fora do Windows, copie para uma pasta as fontes listadas em HANDS e as
Arial (arial.ttf e arialbd.ttf) e aponte FONTS_DIR para ela.
"""
import argparse
import functools
import io
import json
import os
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

SYSTEM_FONTS = {
    'win32': [Path('C:/Windows/Fonts'), Path(os.environ.get('LOCALAPPDATA', '~')) / 'Microsoft/Windows/Fonts'],
    'darwin': [Path('/Library/Fonts'), Path('~/Library/Fonts'), Path('/System/Library/Fonts')],
}
LINUX_FONTS = [Path('/usr/share/fonts'), Path('/usr/local/share/fonts'), Path('~/.local/share/fonts'), Path('~/.fonts')]
HANDS = {'comum': ['Inkfree.ttf', 'segoepr.ttf', 'segoesc.ttf'], 'medico': ['segoesc.ttf', 'segoescb.ttf', 'Inkfree.ttf']}
PAGE = (1000, 1300)
MAX_BYTES = 60_000
PAPER, INK, STAMP_INK = (246, 243, 232), (25, 45, 140), (70, 40, 150)
FIRST = ['Mariana', 'Joaquim', 'Letícia', 'Otávio', 'Bianca', 'Renato', 'Celina', 'Heitor', 'Lívia', 'Augusto']
LAST = ['Quimera', 'Ficcional', 'Sentinela', 'Exemplar', 'Modelar', 'Simulado', 'Hipotético', 'Inventado']
CLINICS = ['Aurora', 'Horizonte', 'Boreal', 'Cerrado', 'Litoral']
# (código no catálogo, como se escreve por extenso, abreviações de receita)
EXAMS = [('FICT-001', 'Hemograma completo', ['Hemogr.', 'Hemograma']),
         ('FICT-002', 'Glicemia de jejum', ['Glic. jejum', 'Glicemia']),
         ('FICT-003', 'Hemoglobina glicada', ['HbA1c', 'Hb glic.']),
         ('FICT-004', 'Ureia', ['Ur.', 'Ureia']),
         ('FICT-005', 'Creatinina', ['Creat.', 'Creatinina']),
         ('FICT-006', 'Colesterol total', ['Col. total', 'CT']),
         ('FICT-007', 'Colesterol HDL', ['HDL']),
         ('FICT-009', 'Triglicerídeos', ['Trig.', 'TG']),
         ('FICT-018', 'Ferritina', ['Ferrit.', 'Ferritina']),
         ('FICT-021', 'Vitamina B12', ['Vit B12', 'B12']),
         ('FICT-023', 'Vitamina D', ['Vit D', '25 OH vit D']),
         ('FICT-024', 'TSH', ['TSH']),
         ('FICT-025', 'T4 livre', ['T4L', 'T4 livre']),
         ('FICT-048', 'PSA total', ['PSA']),
         ('FICT-055', 'TGO', ['TGO']),
         ('FICT-056', 'TGP', ['TGP']),
         ('FICT-057', 'Gama GT', ['GGT']),
         ('FICT-071', 'Proteína C reativa', ['PCR']),
         ('FICT-090', 'Urina tipo I', ['EAS', 'Urina I'])]


def font_dirs():
    """FONTS_DIR, if set; else the usual system font folders of this platform."""
    if os.environ.get('FONTS_DIR'):
        return [Path(os.environ['FONTS_DIR'])]
    return [folder.expanduser() for folder in SYSTEM_FONTS.get(sys.platform, LINUX_FONTS)]


@functools.cache
def font_file(name):
    """The file of a font: right in a folder, or below it with any case (Linux keeps them in subfolders)."""
    for folder in font_dirs():
        if (folder / name).is_file():
            return folder / name
        if folder.is_dir():
            found = next((path for path in sorted(folder.rglob('*')) if path.name.lower() == name.lower()), None)
            if found:
                return found
    raise SystemExit(f'Fonte "{name}" não encontrada em {", ".join(str(folder) for folder in font_dirs())}. '
                     'Defina FONTS_DIR com a pasta que a contém (as de letra de mão vêm com o Windows).')


def font(name, size):
    return ImageFont.truetype(str(font_file(name)), size)


def invalid_cpf(rng):
    """Nove dígitos e um par verificador errado de propósito: nunca é um CPF válido."""
    digits = [rng.randrange(10) for _ in range(9)]
    for size in (9, 10):
        digits.append(sum(d * (size + 1 - i) for i, d in enumerate(digits)) * 10 % 11 % 10)
    digits[10] = (digits[10] + 1 + rng.randrange(9)) % 10
    d = ''.join(map(str, digits))
    return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'


def word_mask(word, style, rng, writer):
    """Uma palavra em tons de cinza (255 = tinta): inclinada, com tremor e traço irregular."""
    medico = style == 'medico'
    size = int(writer['size'] * rng.uniform(0.88, 1.12))  # a mesma letra, maior ou menor por palavra
    face = font(writer['font'], size)
    mask = Image.new('L', (int(face.getlength(word) * 1.3) + 2 * size, 2 * size), 0)
    draw, x = ImageDraw.Draw(mask), size // 2
    for char in word:  # letra por letra, para o espaçamento variar (no médico, às vezes negativo)
        draw.text((x, size // 3 + rng.uniform(-1.5, 1.5)), char, font=face, fill=255)
        x += face.getlength(char) + (rng.uniform(-6, 2) if medico else rng.uniform(-1, 2))
    mask = mask.crop((0, 0, int(x) + size // 2, mask.height))
    slant = rng.uniform(0.35, 0.6) if medico else rng.uniform(0.0, 0.25)
    mask = mask.transform(mask.size, Image.AFFINE, (1, slant, -slant * mask.height / 2, 0, 1, 0), Image.BICUBIC)
    pixels, amplitude = np.array(mask), rng.uniform(2, 4) if medico else rng.uniform(0.5, 1.5)
    phase, period = rng.uniform(0, 6.3), rng.uniform(25, 60)
    for column in range(pixels.shape[1]):  # tremor: cada coluna sobe ou desce um pouco
        pixels[:, column] = np.roll(pixels[:, column], int(round(amplitude * np.sin(column / period * 6.3 + phase))))
    mask = Image.fromarray(pixels)
    if rng.random() < (0.6 if medico else 0.3):  # traço que engrossa numa parte da palavra
        cut = rng.randrange(1, max(2, mask.width))
        mask.paste(mask.crop((0, 0, cut, mask.height)).filter(ImageFilter.MaxFilter(3)), (0, 0))
    return mask.rotate(rng.uniform(-6, 6) if medico else rng.uniform(-3, 3), expand=True, resample=Image.BICUBIC)


def write(page, text, x, y, style, rng, writer):
    """Escreve uma linha à mão a partir de (x, y), palavra por palavra; devolve o x final."""
    ink = tuple(max(0, c + rng.randint(-15, 15)) for c in INK)
    for word in text.split():
        mask = word_mask(word, style, rng, writer)
        opacity = rng.uniform(0.75, 1.0)
        mask = mask.point([int(value * opacity) for value in range(256)])  # the same table point() builds
        left, top = int(x), int(y + rng.uniform(-6, 6))
        page.paste(ink, (left, top, left + mask.width, top + mask.height), mask)
        x += mask.width * (0.78 if style == 'medico' else 0.9) + rng.uniform(4, 14)
    return x


def stamp(page, rng, doctor, crm):
    """Carimbo do médico: retângulo com nome, CRM e especialidade, girado, borrado e falhado."""
    face = font('arialbd.ttf', 26)
    image = Image.new('L', (420, 140), 0)
    draw = ImageDraw.Draw(image)
    draw.rectangle((4, 4, 415, 135), outline=255, width=4)
    for row, text in enumerate([f'Dr(a). {doctor}', f'CRM-SP {crm}', 'Clínica Médica']):
        draw.text((20, 14 + row * 40), text, font=face, fill=255)
    holes = Image.fromarray((np.random.default_rng(rng.randrange(2**32)).random((140, 420)) > 0.25).astype('uint8') * 255)
    image = Image.fromarray(np.minimum(np.asarray(image), np.asarray(holes)))  # tinta falhada
    image = image.rotate(rng.uniform(-18, 18), expand=True, resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(1.2))
    left, top = rng.randint(470, 540), rng.randint(1000, 1060)
    page.paste(STAMP_INK, (left, top, left + image.width, top + image.height), image.point(lambda value: int(value * 0.75)))


def signature(page, rng, x, y):
    """Assinatura rabiscada: uma linha aleatória contínua, sem letras."""
    points, px, py = [], x, y
    for _ in range(40):
        px, py = px + rng.uniform(4, 14), py + rng.uniform(-18, 18)
        points.append((px, py))
    ImageDraw.Draw(page).line(points, fill=INK, width=3, joint='curve')


def order(rng, style, index):
    """Desenha um pedido e devolve (imagem, gabarito)."""
    patient = f'{rng.choice(FIRST)} {rng.choice(LAST)} {rng.choice(LAST)}'
    doctor, cpf, crm = f'{rng.choice(FIRST)} {rng.choice(LAST)}', invalid_cpf(rng), str(rng.randint(100000, 999999))
    chosen = rng.sample(EXAMS, rng.randint(3, 5))
    writer = {'font': rng.choice(HANDS[style]), 'size': rng.randint(40, 48) if style == 'comum' else rng.randint(36, 44)}
    page = Image.new('RGB', PAGE, PAPER)
    printed, draw = font('arial.ttf', 26), ImageDraw.Draw(page)
    draw.text((60, 50), f'CLÍNICA FICTÍCIA {rng.choice(CLINICS).upper()} - PEDIDO DE EXAMES', font=font('arialbd.ttf', 30),
              fill=(60, 60, 60))
    for y in range(200, 1250, 70):  # pauta clara, como um bloco de receita
        draw.line((50, y + 52, 950, y + 52), fill=(200, 210, 230), width=2)
    for y, label in ((200, 'Paciente:'), (270, 'CPF:'), (340, 'Data:')):
        draw.text((60, y + 18), label, font=printed, fill=(70, 70, 70))
    hand = {'style': style, 'rng': rng, 'writer': writer}
    write(page, patient, 200, y=200, **hand)
    write(page, cpf, 200, y=270, **hand)
    write(page, f'{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/2026', 200, y=340, **hand)
    write(page, 'Solicito:' if style == 'comum' else rng.choice(['Solicito:', 'Pedido:']), 70, y=430, **hand)
    written = []
    for row, (code, full, short) in enumerate(chosen):
        text = full if style == 'comum' else rng.choice(short)
        marker = rng.choice(['- ', '', f'{row + 1}. ']) if style == 'comum' else rng.choice(['', '- '])
        write(page, marker + text, 90, y=500 + row * 90, **hand)
        written.append({'code': code, 'written': text})
    if style == 'comum' and rng.random() < 0.5:
        write(page, f'Dr(a). {doctor}', 70, y=1050, **hand)
        write(page, f'CRM-SP {crm}', 70, y=1120, **hand)
    else:
        stamp(page, rng, doctor, crm)
        signature(page, rng, 90, 1110)
    truth = {'estilo': style, 'exames': written,
             'sensiveis': {'paciente': patient, 'cpf': cpf, 'medico': doctor, 'crm': crm}}
    return page, truth


def paper_texture(page, rng):
    noise = np.random.default_rng(rng.randrange(2**32)).normal(0, 2.5, (PAGE[1], PAGE[0], 1))
    return Image.fromarray(np.clip(np.asarray(page, dtype=float) + noise, 0, 255).astype('uint8'))


def perspective(page, rng, strength):
    """Foto tirada de lado: o papel sobre uma mesa, com os cantos fora de esquadro."""
    w, h = page.size
    table = Image.new('RGB', (w + 160, h + 160), tuple(rng.randint(70, 120) for _ in range(3)))
    table.paste(page, (80, 80))
    jitter = [rng.uniform(-strength, strength) * w for _ in range(8)]
    quad = (jitter[0], jitter[1], jitter[2], h + 160 + jitter[3], w + 160 + jitter[4], h + 160 + jitter[5],
            w + 160 + jitter[6], jitter[7])
    return table.transform((w, h), Image.QUAD, quad, Image.BICUBIC)


def crumple(page, rng):
    """Papel amassado: deforma uma malha de 4 x 5 quadrados com deslocamentos pequenos."""
    w, h = page.size
    cols, rows, push = 4, 5, 10
    offset = {(i, j): (rng.uniform(-push, push), rng.uniform(-push, push)) for i in range(cols + 1) for j in range(rows + 1)}
    mesh = []
    for i in range(cols):
        for j in range(rows):
            box = (i * w // cols, j * h // rows, (i + 1) * w // cols, (j + 1) * h // rows)
            corners = [(i, j), (i, j + 1), (i + 1, j + 1), (i + 1, j)]
            quad = []
            for ci, cj in corners:
                dx, dy = offset[(ci, cj)] if 0 < ci < cols and 0 < cj < rows else (0, 0)
                quad += [ci * w // cols + dx, cj * h // rows + dy]
            mesh.append((box, quad))
    return page.transform(page.size, Image.MESH, mesh, Image.BICUBIC)


def shadow(page, rng):
    """Luz desigual e a sombra da mão ou do celular sobre parte do papel."""
    w, h = page.size
    light = np.linspace(rng.uniform(0.55, 0.8), 1.0, w)[None, :] * np.linspace(rng.uniform(0.7, 0.95), 1.0, h)[:, None]
    hand = Image.new('L', page.size, 0)
    cx, cy = rng.uniform(0, w), rng.uniform(h * 0.4, h)
    ImageDraw.Draw(hand).ellipse((cx - 300, cy - 200, cx + 300, cy + 400), fill=90)
    hand = np.asarray(hand.filter(ImageFilter.GaussianBlur(60)), dtype=float) / 255
    factor = (light * (1 - hand))[..., None]
    return Image.fromarray(np.clip(np.asarray(page, dtype=float) * factor, 0, 255).astype('uint8'))


def blur(page, rng, heavy):
    """Foto tremida ou fora de foco, sorteada."""
    if rng.random() < 0.5:  # tremido: o mesmo papel deslocado alguns pixels, em média
        shifts = [page.transform(page.size, Image.AFFINE, (1, 0, dx, 0, 1, 0)) for dx in range(-3, 4)]
        return Image.fromarray(np.mean([np.asarray(s, dtype=float) for s in shifts], axis=0).astype('uint8'))
    return page.filter(ImageFilter.GaussianBlur(rng.uniform(1.0, 2.0) if heavy else rng.uniform(0.6, 1.2)))  # fora de foco


def degrade(page, rng, level):
    """Aplica os efeitos de foto ruim (sorteados pela semente); devolve (imagem, efeitos, qualidade JPEG)."""
    if level == 'scan':
        return page.filter(ImageFilter.GaussianBlur(0.4)), ['scan limpo'], 80
    heavy = level == 'foto ruim'
    effects = ['perspectiva']
    page = perspective(page, rng, 0.06 if heavy else 0.03)
    options = ['papel amassado', 'sombra', 'pouco contraste', 'baixa resolução', 'desfoque', 'ruído', 'borda cortada']
    for effect in rng.sample(options, rng.randint(3, 5) if heavy else rng.randint(1, 3)):
        effects.append(effect)
        if effect == 'papel amassado':
            page = crumple(page, rng)
        elif effect == 'sombra':
            page = shadow(page, rng)
        elif effect == 'pouco contraste':
            page = ImageEnhance.Contrast(page).enhance(rng.uniform(0.45, 0.7))
            page = Image.blend(page, Image.new('RGB', page.size, (230, 205, 150)), 0.15)  # papel amarelado
        elif effect == 'baixa resolução':
            scale = rng.uniform(0.35, 0.5) if heavy else rng.uniform(0.5, 0.7)
            small = page.resize((int(page.width * scale), int(page.height * scale)), Image.BILINEAR)
            page = small.resize(page.size, Image.BILINEAR)
        elif effect == 'desfoque':
            page = blur(page, rng, heavy)
        elif effect == 'ruído':
            noise = np.random.default_rng(rng.randrange(2**32)).normal(0, 10 if heavy else 6, (page.height, page.width, 1))
            page = Image.fromarray(np.clip(np.asarray(page, dtype=float) + noise, 0, 255).astype('uint8'))
        elif effect == 'borda cortada':
            cut = int(page.width * rng.uniform(0.03, 0.08))
            page = page.crop((cut, 0, page.width, page.height)) if rng.random() < 0.5 else page.crop((0, 0, page.width - cut, page.height))
    return page, effects, rng.randint(25, 40) if heavy else rng.randint(40, 55)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python exemplos/gerar_manuscrito.py', description=__doc__.split('\n')[0])
    parser.add_argument('--saida', default='samples/manuscritos')
    parser.add_argument('--comum', type=int, default=70)
    parser.add_argument('--medico', type=int, default=50)
    parser.add_argument('--semente', type=int, default=20261006)
    args = parser.parse_args(argv)
    out = Path(args.saida)
    out.mkdir(parents=True, exist_ok=True)
    truth = {}
    jobs = [('comum', i) for i in range(1, args.comum + 1)] + [('medico', i) for i in range(1, args.medico + 1)]
    for style, index in jobs:
        rng = random.Random(f'{args.semente}-{style}-{index}')
        page, item = order(rng, style, index)
        level = rng.choices(['scan', 'foto', 'foto ruim'], weights=[30, 40, 30])[0]
        page, effects, quality = degrade(paper_texture(page, rng), rng, level)
        name = f'{style}-{index:03d}.jpg'
        page = page.resize((850, int(page.height * 850 / page.width)), Image.LANCZOS)  # como a foto chega do celular
        while True:  # até 60 KB por imagem: baixa a qualidade em passos, se precisar
            buffer = io.BytesIO()
            page.save(buffer, 'JPEG', quality=quality, optimize=True)
            if buffer.tell() <= MAX_BYTES or quality <= 20:
                break
            quality -= 5
        (out / name).write_bytes(buffer.getvalue())
        truth[name] = {**item, 'degradacao': level, 'efeitos': effects, 'jpeg_qualidade': quality}
    truth = {'gerado_por': f'python exemplos/gerar_manuscrito.py --saida {args.saida} --comum {args.comum} '
                           f'--medico {args.medico} --semente {args.semente}', 'imagens': truth}
    (out / 'gabarito.json').write_text(json.dumps(truth, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'{len(jobs)} imagens em {out}')


if __name__ == '__main__':
    main()
