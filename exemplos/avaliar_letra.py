"""Avalia um leitor local de letra difícil (Qwen3-VL 2B no llama-server) contra o Tesseract do projeto.

Experimento, fora do caminho de produção: nenhum módulo do projeto importa este arquivo. Recorta
cada linha escrita à mão dos pedidos de samples/manuscritos/ pelas caixas de
exemplos/manuscritos-linhas.json (tiradas do próprio gerador, ver `caixas`), pergunta ao modelo em
dois modos e imprime, por estilo, recall, precisão, ERRADOS CONFIANTES, abstenção e segundos por página:
  - Tesseract: o caminho atual (mcp_servers.ocr.read_lines, máscara, search_line) e a regra de
    agendamento sozinho (score >= 0,90 e a confiança mínima da linha de runtime/confianca.py);
  - livre: "transcreva a linha", e o texto passa pela busca do RAG (aceito com score >= 0,90);
  - fechado: o catálogo no prompt e uma gramática GBNF que só deixa sair um nome do catálogo ou NENHUM;
  - acordo: aceita o fechado só quando a busca do texto livre aponta o mesmo exame; senão, abstém;
  - acordo + livre >= 0,80: o mesmo, e o texto livre precisa valer um exame por si (score >= 0,80).
Um "errado confiante" é um exame aceito que o pedido não tem.

Uso (na raiz do repositório; os comandos completos, com o servidor, estão em exemplos/README.md):
    python exemplos/avaliar_letra.py --servidor http://127.0.0.1:8080 --paginas 3
    python exemplos/avaliar_letra.py caixas   # refaz o JSON das caixas (precisa de numpy e das fontes)
"""
import argparse
import base64
import io
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # run as a script from the repository root

from catalogo import CATALOG, words  # noqa: E402
from mcp_servers.rag import search_line  # noqa: E402

AMOSTRAS = ROOT / 'samples' / 'manuscritos'
CAIXAS = ROOT / 'exemplos' / 'manuscritos-linhas.json'
NOME = {exam['name']: exam['code'] for exam in CATALOG}
# Abreviações comuns em pedidos escritos à mão, só no prompt (o catálogo e a busca não mudam).
ABREVIACOES = {'FICT-001': ['HMG', 'Hemog'], 'FICT-002': ['Gli jj', 'GJ'], 'FICT-004': ['Ur'], 'FICT-005': ['Cr'],
               'FICT-090': ['U1'], 'FICT-071': ['PCR'], 'FICT-025': ['T4L'], 'FICT-023': ['25-OH vit D'],
               'FICT-048': ['PSA t'], 'FICT-055': ['TGO'], 'FICT-056': ['TGP']}
CATALOGO = '\n'.join(f"- {e['name']}" + (f" (também escrito: {', '.join(dict.fromkeys(e['synonyms'] + ABREVIACOES.get(e['code'], [])))})"
                                          if e['synonyms'] or e['code'] in ABREVIACOES else '') for e in CATALOG)
SISTEMA = ('Você lê pedidos médicos manuscritos. Catálogo de exames (nome oficial e formas abreviadas):\n' + CATALOGO +
           '\n\nResponda somente com o nome oficial do exame do catálogo escrito na imagem, ou NENHUM se a linha '
           'não é um exame do catálogo (nome de pessoa, CPF, data, cabeçalho, ilegível ou exame fora do catálogo).')
GRAMATICA = 'root ::= ' + ' | '.join(json.dumps(e['name'], ensure_ascii=False) for e in CATALOG) + ' | "NENHUM"\n'
LIVRE = 'Transcreva exatamente o texto manuscrito desta imagem, em português. Responda só com o texto, sem comentários.'


def melhor(texto):
    """(código, score) do melhor exame da linha na busca do RAG, ou (None, 0.0)."""
    if not words(texto):
        return None, 0.0
    hits = [hit for hit in search_line(texto[:200], 3) if not hit.get('partial')]
    hit = max(hits, key=lambda h: h['score'], default=None)
    return (hit['code'], hit['score']) if hit else (None, 0.0)


def perguntar(servidor, mensagens, **extra):
    corpo = json.dumps({'messages': mensagens, 'temperature': 0, 'cache_prompt': True, **extra}).encode()
    pedido = urllib.request.Request(f'{servidor}/v1/chat/completions', corpo, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(pedido, timeout=600) as resposta:
        return json.load(resposta)['choices'][0]['message']['content'].strip()


def ler_linha(servidor, recorte):
    """(código do modo livre, score, código do modo fechado ou None) de uma linha recortada."""
    buffer = io.BytesIO()
    recorte.save(buffer, 'PNG')
    imagem = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()}}
    livre = perguntar(servidor, [{'role': 'user', 'content': [imagem, {'type': 'text', 'text': LIVRE}]}], max_tokens=40)
    fechado = perguntar(servidor, [{'role': 'system', 'content': SISTEMA},
                                   {'role': 'user', 'content': [imagem, {'type': 'text', 'text': 'Qual exame do catálogo está escrito? (ou NENHUM)'}]}],
                        max_tokens=16, grammar=GRAMATICA)
    return (*melhor(livre), NOME.get(fechado))


def tesseract(caminho):
    """Exames agendados sozinhos pelo caminho atual do OCR, como na regra do agente."""
    from guardrails.injection import join_split_orders
    from mcp_servers import ocr
    from mcp_servers.preprocessamento import confianca_por_linha
    from runtime.confianca import BookingPolicy
    politica, agendados = BookingPolicy(), set()
    try:
        linhas = ocr.read_lines(caminho)
    except Exception:  # foto recusada pelas checagens de qualidade: nada é lido
        return agendados
    juntas, origens = join_split_orders(linhas)
    lidas = ocr.mask_lines(linhas, juntas)
    for linha, tipo, conf in zip(lidas['lines'], lidas['line_intent'], confianca_por_linha(linhas, origens=origens), strict=True):
        for hit in search_line(linha[:200], 3) if tipo == 'request' and words(linha) else []:
            piso = politica.ocr_floor(words(hit.get('piece', linha)), words(hit['name']))
            if not hit.get('partial') and hit['score'] >= politica.min_confidence and conf >= piso:
                agendados.add(hit['code'])
    return agendados


REGRAS = {'livre (RAG >= 0,90)': lambda a, s, b: a if s >= 0.90 else None,
          'fechado sozinho': lambda a, s, b: b,
          'acordo livre + fechado': lambda a, s, b: b if b and a == b else None,
          # o "Solicito:" lido "Solicito." dá Sódio nos dois modos (score 0,62): o acordo exige também
          # que o texto livre seja um exame por si (0,80, o OWN_EXAM de mcp_servers/rag.py)
          'acordo + livre >= 0,80': lambda a, s, b: b if b and a == b and s >= 0.80 else None}


def avaliar(args):
    from PIL import Image
    caixas = json.loads(CAIXAS.read_text(encoding='utf-8'))
    for estilo in ('comum', 'medico'):
        paginas = [(nome, item) for nome, item in caixas.items() if item['estilo'] == estilo][:args.paginas]
        totais = {nome: {'certos': 0, 'aceitos': 0, 'errados': 0, 'abstencoes': 0, 'segundos': 0.0}
                  for nome in ['Tesseract (página inteira)', *REGRAS]}
        exames = linhas_de_exame = 0
        for nome, item in paginas:
            pedido, imagem = set(item['codigos']), Image.open(AMOSTRAS / nome).convert('RGB')
            exames += len(pedido)
            inicio = time.perf_counter()
            somar(totais['Tesseract (página inteira)'], tesseract(AMOSTRAS / nome), pedido, time.perf_counter() - inicio)
            inicio, lidas = time.perf_counter(), []
            for linha in item['linhas']:
                lidas.append((linha.get('codigo'), ler_linha(args.servidor, imagem.crop(tuple(linha['caixa'])))))
            segundos = time.perf_counter() - inicio
            linhas_de_exame += sum(codigo is not None for codigo, _ in lidas)
            for regra, aceita in REGRAS.items():
                escolhidos = [(codigo, aceita(*leitura)) for codigo, leitura in lidas]
                totais[regra]['abstencoes'] += sum(codigo is not None and aceito is None for codigo, aceito in escolhidos)
                somar(totais[regra], {aceito for _, aceito in escolhidos if aceito}, pedido, segundos)
        imprimir(estilo, len(paginas), exames, linhas_de_exame, totais)


def somar(total, aceitos, pedido, segundos):
    total['certos'] += len(aceitos & pedido)
    total['aceitos'] += len(aceitos)
    total['errados'] += len(aceitos - pedido)
    total['segundos'] += segundos


def imprimir(estilo, paginas, exames, linhas_de_exame, totais):
    print(f'\n{estilo}: {paginas} páginas, {exames} exames')
    print('| leitor | recall | precisão | errados confiantes | abstenção (linhas de exame) | s/página |')
    print('|---|---|---|---|---|---|')
    for nome, t in totais.items():
        precisao = f"{t['certos'] / t['aceitos']:.0%}" if t['aceitos'] else '-'
        abstencao = f"{t['abstencoes'] / linhas_de_exame:.0%}" if nome in REGRAS and linhas_de_exame else '-'
        print(f"| {nome} | {t['certos'] / max(exames, 1):.0%} | {precisao} | {t['errados']} | {abstencao} | "
              f"{t['segundos'] / max(paginas, 1):.1f} |")


def caixas(_args):
    """Refaz CAIXAS: repete o gerador com a mesma semente, guardando a caixa da tinta de cada linha, e
    repete a degradação (mesma sequência aleatória) numa página branca com as caixas pintadas, uma por
    canal de cor, para que perspectiva, papel amassado, corte e redução movam as caixas como moveram a
    tinta. Imagens que o gerador desta máquina não reproduz (tamanho ou pixels diferentes) ficam de fora."""
    import numpy as np
    from PIL import Image, ImageDraw

    import exemplos.gerar_manuscrito as gm
    registro = []

    def write(page, text, x, y, style, rng, writer):  # o corpo de gm.write, guardando a caixa da tinta
        ink, caixa = tuple(max(0, c + rng.randint(-15, 15)) for c in gm.INK), None
        for word in text.split():
            mask = gm.word_mask(word, style, rng, writer)
            opacity = rng.uniform(0.75, 1.0)
            mask = mask.point([int(value * opacity) for value in range(256)])
            left, top = int(x), int(y + rng.uniform(-6, 6))
            page.paste(ink, (left, top, left + mask.width, top + mask.height), mask)
            if tinta := mask.point(lambda v: 255 if v > 40 else 0).getbbox():
                b = (left + tinta[0], top + tinta[1], left + tinta[2], top + tinta[3])
                caixa = b if caixa is None else (min(caixa[0], b[0]), min(caixa[1], b[1]), max(caixa[2], b[2]), max(caixa[3], b[3]))
            x += mask.width * (0.78 if style == 'medico' else 0.9) + rng.uniform(4, 14)
        registro.append(caixa)
        return x
    gm.write = write
    final = lambda imagem: imagem.resize((850, int(imagem.height * 850 / imagem.width)), Image.LANCZOS)  # noqa: E731
    gabarito, saida = json.loads((AMOSTRAS / 'gabarito.json').read_text(encoding='utf-8'))['imagens'], {}
    for nome, item in gabarito.items():
        estilo, indice = item['estilo'], int(nome.split('-')[1][:3])
        rng = random.Random(f"20261006-{estilo}-{indice}")
        registro.clear()
        pagina, verdade = gm.order(rng, estilo, indice)
        nivel = rng.choices(['scan', 'foto', 'foto ruim'], weights=[30, 40, 30])[0]
        estado = rng.getstate()
        refeita = final(gm.degrade(gm.paper_texture(pagina, rng), rng, nivel)[0])
        amostra = Image.open(AMOSTRAS / nome).convert('L')
        if refeita.size != amostra.size or np.abs(np.asarray(refeita.convert('L'), float) - np.asarray(amostra, float)).mean() > 8:
            continue
        # paciente, CPF e o "Solicito:" ficam como linhas que não são exame (devem dar NENHUM)
        linhas = [(None, registro[0]), (None, registro[1]), (None, registro[3])] + \
            [(exame['code'], registro[4 + k]) for k, exame in enumerate(verdade['exames'])]
        marcada, branca = Image.new('RGB', gm.PAGE, 'white'), Image.new('RGB', gm.PAGE, 'white')
        for k, (_, (x0, y0, x1, y1)) in enumerate(linhas):
            cor = [255, 255, 255]
            cor[k % 3] = 0
            ImageDraw.Draw(marcada).rectangle((x0 - 6, y0 - 6, x1 + 6, y1 + 6), fill=tuple(cor))
        degradadas = []
        for base in (marcada, branca):
            copia = random.Random()
            copia.setstate(estado)
            degradadas.append(np.asarray(final(gm.degrade(gm.paper_texture(base, copia), copia, nivel)[0]), float))
        caixas_linha = [caixa_do_canal(degradadas, k % 3, k // 3) for k in range(len(linhas))]
        if None in caixas_linha:
            continue
        saida[nome] = {'estilo': estilo, 'codigos': [e['code'] for e in verdade['exames']],
                       'linhas': [{'codigo': codigo, 'caixa': caixa} for (codigo, _), caixa in zip(linhas, caixas_linha, strict=True)]}
    CAIXAS.write_text(json.dumps(saida, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'{len(saida)} de {len(gabarito)} imagens em {CAIXAS.name}')


def caixa_do_canal(degradadas, canal, ordem):
    """A caixa (x0, y0, x1, y1) da ordem-ésima faixa escura do canal, ou None."""
    import numpy as np
    marcada, branca = degradadas[0][..., canal], degradadas[1][..., canal] + 1.0  # +1: as caixas medidas usaram este piso
    dentro = ((branca - marcada) > 0.3 * branca) & (branca > 50)
    linhas = np.flatnonzero(dentro.sum(1) > 5)
    faixas = np.split(linhas, np.flatnonzero(np.diff(linhas) > 1) + 1) if linhas.size else []
    faixas = [faixa for faixa in faixas if faixa.size > 8]
    if ordem >= len(faixas):
        return None
    y0, y1 = int(faixas[ordem][0]), int(faixas[ordem][-1]) + 1
    colunas = np.flatnonzero(dentro[y0:y1].sum(0) > 3)
    return [int(colunas.min()), y0, int(colunas.max()) + 1, y1]


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python exemplos/avaliar_letra.py', description=__doc__.split('\n')[0])
    parser.add_argument('acao', nargs='?', default='avaliar', choices=['avaliar', 'caixas'])
    parser.add_argument('--servidor', default='http://127.0.0.1:8080', help='llama-server com o Qwen3-VL')
    parser.add_argument('--paginas', type=int, default=1000, help='páginas por estilo (as primeiras)')
    args = parser.parse_args(argv)
    (caixas if args.acao == 'caixas' else avaliar)(args)


if __name__ == '__main__':
    main()
