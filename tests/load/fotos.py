"""Fotos de celular de pedidos IMPRESSOS fictícios (semente fixa), só com Pillow.

O texto, os exames e os valores sensíveis vêm do gerador da carga (tests/load/pedidos.py, nos
mesmos três layouts). Cada pedido é impresso numa folha A4 e fotografado:
- papel levemente curvo (as linhas viram arcos) e um pouco fora do branco;
- perspectiva: a folha sobre uma mesa, fotografada de lado, com a mesa nas bordas;
- luz desigual e, nas fotos média e forte, a sombra da mão ou do celular;
- câmera: desfoque leve, ruído de sensor, tom de cor, 1.200 a 2.000 px no lado maior e JPEG
  com a qualidade que cabe em MAX_BYTES (as 30 somam menos de 3 MB).
Três níveis (leve, média, forte) cruzados com os três layouts. Tudo é inventado; o CPF tem o
dígito verificador errado de propósito (pedidos.cpf_invalido).

Na imagem Docker de testes (fontes DejaVu), na raiz do repositório:
    docker compose run --rm --no-deps -v "$PWD:/work" -w /work tests python -m tests.load.fotos
Escreve samples/fotos-celular/foto-01.jpg ... e gabarito.json no formato de samples/manuscritos,
então tests/load/manuscritos.py as mede pelo OCR, pelo RAG e pela regra do agente:
    ... run --rm tests python -m tests.load.manuscritos --origem samples/fotos-celular --detalhe
"""
import argparse
import io
import json
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from tests.load.pedidos import EXAMS, dados, fonte, linhas

SEED = 20261007
A4 = (1240, 1754)  # 150 dpi
MAX_BYTES = 90_000
LAYOUTS = ['rotulos', 'lado-a-lado', 'receituario']  # os layouts 0, 1 e 2 de pedidos.linhas
LEVELS = {  # curvatura (px), perspectiva (fração), luz mínima, sombra, desfoque, ruído (desvio)
    'leve': {'curva': (3, 8), 'perspectiva': 0.03, 'luz': 0.85, 'sombra': 0, 'desfoque': (0.3, 0.6), 'ruido': 2},
    'media': {'curva': (8, 16), 'perspectiva': 0.05, 'luz': 0.72, 'sombra': 70, 'desfoque': (0.6, 1.0), 'ruido': 3},
    'forte': {'curva': (14, 24), 'perspectiva': 0.07, 'luz': 0.6, 'sombra': 110, 'desfoque': (0.9, 1.4), 'ruido': 4},
}


def impresso(rng, texto, layout):
    """A folha A4 impressa: margens, fonte do layout e tinta de impressora (quase preta)."""
    size = rng.randrange(26, 32)
    step = int(size * 1.6)
    page = Image.new('RGB', (A4[0], max(A4[1], 260 + step * len(texto))), (250, 250, 247))
    draw, font = ImageDraw.Draw(page), fonte(layout, size)
    for row, text in enumerate(texto):
        draw.text((110, 130 + step * row), text, fill=(25, 25, 30), font=font)
    return page


def curvar(page, rng, amplitude):
    """Papel levemente curvo: cada coluna desce conforme um arco; fora da folha fica transparente."""
    w, h = page.size
    strips, phase = 48, rng.uniform(-0.3, 0.3)
    drop = [amplitude * math.sin(math.pi * (i / strips + phase)) for i in range(strips + 1)]
    mesh = [((i * w // strips, 0, (i + 1) * w // strips, h),
             (i * w // strips, -drop[i], i * w // strips, h - drop[i],
              (i + 1) * w // strips, h - drop[i + 1], (i + 1) * w // strips, -drop[i + 1]))
            for i in range(strips)]
    return page.convert('RGBA').transform(page.size, Image.MESH, mesh, Image.BICUBIC, fillcolor=(0, 0, 0, 0))


def sobre_a_mesa(page, rng, strength):
    """A folha sobre uma mesa, fotografada de lado: os cantos saem do esquadro e a mesa aparece."""
    w, h = page.size
    margin_x, margin_y = int(w * 0.08), int(h * 0.06)
    color = tuple(rng.randint(60, 140) for _ in range(3))
    table = Image.new('RGB', (w + 2 * margin_x, h + 2 * margin_y), color)
    table.paste(page, (margin_x, margin_y), page)
    tw, th = table.size
    j = [rng.uniform(-strength, strength) for _ in range(8)]
    quad = (j[0] * tw, j[1] * th, j[2] * tw, th + j[3] * th, tw + j[4] * tw, th + j[5] * th, tw + j[6] * tw, j[7] * th)
    return table.transform(table.size, Image.QUAD, quad, Image.BICUBIC, fillcolor=color)


def gradiente(size, low, rng):
    """Fator de luz (L) de low a 1, em diagonal e para um lado sorteado."""
    low = math.sqrt(low)  # dois gradientes multiplicados: o canto mais escuro fica em low
    ramp = Image.linear_gradient('L').point(lambda v: int(255 * low + (255 - 255 * low) * v / 255))
    across = ImageOps.mirror(ramp.rotate(90)) if rng.random() < 0.5 else ramp.rotate(90)
    down = ImageOps.flip(ramp) if rng.random() < 0.5 else ramp
    return ImageChops.multiply(across, down).resize(size, Image.BILINEAR)


def sombra(size, rng, strength):
    """Fator de luz (L) com a sombra borrada da mão ou do celular sobre uma borda."""
    small = (size[0] // 8, size[1] // 8)
    shade = Image.new('L', small, 0)
    cx, cy = rng.uniform(0, small[0]), rng.choice([rng.uniform(-0.1, 0.15), rng.uniform(0.85, 1.1)]) * small[1]
    rx, ry = small[0] * rng.uniform(0.25, 0.4), small[1] * rng.uniform(0.15, 0.25)
    ImageDraw.Draw(shade).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=strength)
    return ImageOps.invert(shade.filter(ImageFilter.GaussianBlur(small[0] * 0.06))).resize(size, Image.BILINEAR)


def ruido(size, rng, sigma):
    """Ruído de sensor em torno de 128 (uniforme, desvio sigma), pronto para ImageChops.add."""
    half = (size[0] // 2, size[1] // 2)
    noise = Image.frombytes('L', half, rng.randbytes(half[0] * half[1]))
    return noise.point(lambda v: int(128 + (v - 127.5) * sigma / 73.6)).resize(size, Image.BILINEAR).convert('RGB')


def jpeg(image, rng):
    """Bytes do JPEG de celular: qualidade inicial sorteada, baixando até caber em MAX_BYTES."""
    quality = rng.randrange(80, 90)
    while True:
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=quality, optimize=True)
        if buffer.tell() <= MAX_BYTES or quality <= 35:
            return buffer.getvalue(), quality
        quality -= 3


def fotografar(page, rng, level):
    """A foto de celular da folha; devolve (bytes JPEG, efeitos, qualidade, tamanho)."""
    p = LEVELS[level]
    photo = sobre_a_mesa(curvar(page, rng, rng.uniform(*p['curva'])), rng, p['perspectiva'])
    light = gradiente(photo.size, p['luz'], rng)
    effects = ['papel curvo', 'perspectiva', 'luz desigual']
    if p['sombra']:
        light = ImageChops.multiply(light, sombra(photo.size, rng, p['sombra']))
        effects.append('sombra')
    photo = ImageChops.multiply(photo, light.convert('RGB'))
    tint = (255, rng.randrange(238, 252), rng.randrange(215, 245)) if rng.random() < 0.6 else \
        (rng.randrange(225, 245), rng.randrange(238, 250), 255)  # lâmpada quente ou luz do dia
    photo = ImageChops.multiply(photo, Image.new('RGB', photo.size, tint))
    long_side = rng.randrange(1200, 2001)
    scale = long_side / max(photo.size)
    photo = photo.resize((round(photo.width * scale), round(photo.height * scale)), Image.LANCZOS)
    photo = photo.filter(ImageFilter.GaussianBlur(rng.uniform(*p['desfoque'])))
    photo = ImageChops.add(photo, ruido(photo.size, rng, p['ruido']), offset=-128)
    data, quality = jpeg(photo, rng)
    return data, effects + ['desfoque', 'ruído', f'JPEG {quality}'], quality, list(photo.size)


def foto(seed, index):
    """(nome do arquivo, bytes JPEG, item do gabarito) da foto de número index (1 a n)."""
    rng = random.Random(f'{seed}-{index}')
    layout, level = (index - 1) % 3, list(LEVELS)[(index - 1) // 3 % 3]
    exames = [exam['name'] for exam in rng.sample(EXAMS, rng.randrange(1, 6))]
    sensiveis = dados(rng)
    data, effects, quality, size = fotografar(impresso(rng, linhas(rng, sensiveis, exames, layout), layout), rng, level)
    codes = {exam['name']: exam['code'] for exam in EXAMS}
    return f'foto-{index:02d}.jpg', data, {
        'estilo': LAYOUTS[layout], 'degradacao': level, 'efeitos': effects, 'jpeg_qualidade': quality, 'tamanho': size,
        'exames': [{'code': codes[e], 'name': e} for e in exames], 'sensiveis': sensiveis}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m tests.load.fotos', description=__doc__.split('\n')[0])
    parser.add_argument('--saida', default='samples/fotos-celular')
    parser.add_argument('--n', type=int, default=30)
    parser.add_argument('--semente', type=int, default=SEED)
    args = parser.parse_args(argv)
    out = Path(args.saida)
    out.mkdir(parents=True, exist_ok=True)
    truth = {}
    for index in range(1, args.n + 1):
        filename, data, item = foto(args.semente, index)
        (out / filename).write_bytes(data)
        truth[filename] = item
        print(f'{filename} {item["estilo"]:<12} {item["degradacao"]:<6} {item["tamanho"]} {len(data):>7} bytes', flush=True)
    command = f'python -m tests.load.fotos --saida {args.saida} --n {args.n} --semente {args.semente}'
    (out / 'gabarito.json').write_text(json.dumps({'gerado_por': command, 'imagens': truth}, ensure_ascii=False, indent=1)
                                       + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
