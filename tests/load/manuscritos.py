"""Pedidos manuscritos simulados (samples/manuscritos) pelo OCR e pelo RAG, via MCP/SSE, sem Gemini.

    docker compose -f docker-compose.yml -f tests/load/compose.carga.yml -p carga run --rm tests \\
        python -m tests.load.manuscritos [--detalhe]

Copia as imagens para o volume da carga (o OCR só lê um nome de arquivo dentro de
/data/samples) e, para cada uma, manda as linhas lidas ao RAG e decide com a regra REAL do
agente transpilado (os callbacks do pacote runtime/, com o line_confidence que
o OCR devolve), sem ninguém para responder [s/N]:
- lido: exame do pedido que o agente agendaria sozinho;
- perguntado: exame do pedido na faixa de pergunta (0,70 a 0,90); sem resposta, não é agendado;
- baixa confiança: exame do pedido que o agente deixa de fora com aviso;
- agendado errado: código fora do pedido que o agente agendaria sem perguntar (precisa ser 0);
- PII sobrando: valor do gabarito no texto do OCR, com o verificador da carga (precisa ser 0).
Imprime a taxa por estilo e por degradação; sai com 1 se houver agendado errado ou PII.
"""
import argparse
import asyncio
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from mcp import ClientSession
from mcp.client.sse import sse_client

from tests.load.carga import MARKER, chamar, plain, vazamentos
from tests.load.robustez import Contexto, Ferramenta, agente_gerado


def consulta(line):
    """A linha lida sem o marcador do início ("1.", "-"): dígitos do nome ficam (B12, T4, CA 19-9)."""
    return re.sub(MARKER, '', line, count=1).strip()[:200]


def gabarito(origem):
    return json.loads((Path(origem) / 'gabarito.json').read_text(encoding='utf-8'))['imagens']


def decidir(agente, reply, buscas):
    """(agendados sozinho, perguntados, baixa confiança, {código: confiança}) pelos callbacks do agente."""
    contexto = Contexto()
    agente.CALLBACKS.after_tool(Ferramenta('extract_exam_text'), {}, contexto, {'structuredContent': reply})
    for query, hits in buscas:
        agente.CALLBACKS.after_tool(Ferramenta('search_exams'), {'query': query}, contexto,
                                     {'structuredContent': {'result': hits}})
    candidates = contexto.state.get('candidates', {})
    if not candidates:
        return set(), set(), set(), {}
    args = {'exams': [{'code': code, 'name': c['name']} for code, c in candidates.items()]}
    bloqueado = agente.CALLBACKS.before_tool(Ferramenta('create_appointment'), args, contexto)
    agendados = set() if bloqueado else {exam['code'] for exam in args['exams']}
    fora = contexto.state.get('low_confidence', [])
    perguntados = {item['code'] for item in fora if item['reason'] == 'needs_confirmation'}
    confianca = {code: c['confidence'] for code, c in candidates.items()}
    return agendados, perguntados, {item['code'] for item in fora} - perguntados, confianca


async def avaliar(ocr, rag, agente, filename, item):
    """Uma imagem: OCR, uma busca no RAG por linha lida e a decisão do agente."""
    try:
        reply = await chamar(ocr, 'extract_exam_text', {'filename': filename})
    except RuntimeError as error:
        return {'erro': str(error), 'lidos': set(), 'perguntados': set(), 'baixa': set(), 'errados': {},
                'vazamentos': []}
    buscas = []
    for line in reply['lines']:
        query = consulta(line)
        if len(plain(query).replace(' ', '')) >= 2:
            buscas.append((query, await chamar(rag, 'search_exams', {'query': query, 'top_k': 1})))
    agendados, perguntados, baixa, confianca = decidir(agente, reply, buscas)
    expected = {exam['code'] for exam in item['exames']}
    linha = {hits[0]['code']: query for query, hits in buscas if hits}
    return {'erro': None, 'lidos': agendados & expected, 'perguntados': perguntados & expected,
            'baixa': baixa & expected, 'perguntados_errados': perguntados - expected,
            'errados': {code: (confianca.get(code), linha.get(code, '')) for code in agendados - expected},
            'vazamentos': vazamentos('\n'.join(reply['lines']), item['sensiveis'])}


async def rodar(itens, ocr_url, rag_url, concorrencia=4):
    """Distribui as imagens entre sessões MCP; devolve {arquivo: resultado}."""
    fila, results, agente = asyncio.Queue(), {}, agente_gerado()
    agente.CALLBACKS.can_ask = lambda: False  # ninguém para responder: a faixa do meio é contada, não agendada
    for filename in itens:
        fila.put_nowait(filename)

    async def worker():
        async with sse_client(ocr_url, sse_read_timeout=120) as o, ClientSession(*o) as ocr, \
                sse_client(rag_url) as r, ClientSession(*r) as rag:
            await ocr.initialize()
            await rag.initialize()
            while not fila.empty():
                filename = fila.get_nowait()
                results[filename] = await avaliar(ocr, rag, agente, filename, itens[filename])

    await asyncio.gather(*(worker() for _ in range(concorrencia)))
    return results


def resumo(itens, results, detalhe=False):
    """Tabela por estilo e degradação (e, com detalhe, uma linha por imagem); devolve (texto, problemas)."""
    groups = defaultdict(lambda: defaultdict(int))
    lines, problems = [], 0
    for filename, found in sorted(results.items()):
        item = itens[filename]
        for key in (f'{item["estilo"]} · {item["degradacao"]}', f'{item["estilo"]} · total', 'todas'):
            group = groups[key]
            group['imagens'] += 1
            group['exames'] += len(item['exames'])
            group['lidos'] += len(found['lidos'])
            group['perguntados'] += len(found['perguntados'])
            group['baixa'] += len(found['baixa'])
            group['errados'] += len(found['errados'])
            group['pii'] += len(found['vazamentos'])
            group['falhas'] += bool(found['erro'])
        if detalhe:
            lines.append(f'  {filename:<16} {item["estilo"]:<6} {item["degradacao"]:<9} exames {len(item["exames"])} '
                         f'lidos {len(found["lidos"])} perguntados {len(found["perguntados"])} '
                         f'baixa {len(found["baixa"])} errados {len(found["errados"])} '
                         f'PII {len(found["vazamentos"])}' + (f' falha: {found["erro"]}' if found['erro'] else ''))
        for code, (score, line) in found['errados'].items():
            lines.append(f'  AGENDARIA ERRADO em {filename}: {code} (confiança {score}) pela linha {line!r}')
        lines += [f'  perguntaria (fora do pedido) em {filename}: {code}' for code in found.get('perguntados_errados', ())]
        lines += [f'  PII SOBROU em {filename}: {field} em {line!r}' for field, line in found['vazamentos']]
        problems += len(found['errados']) + len(found['vazamentos'])
    header = f'{"grupo":<20} {"imagens":>7} {"exames":>6} {"lidos":>11} {"perguntados":>11} {"baixa conf.":>11} ' \
             f'{"não lidos":>9} {"errados":>7} {"PII":>3} {"falhas OCR":>10}'
    table = [header]
    for key in sorted(groups, key=lambda k: (k == 'todas', k)):
        g = groups[key]
        missing = g['exames'] - g['lidos'] - g['perguntados'] - g['baixa']
        table.append(f'{key:<20} {g["imagens"]:>7} {g["exames"]:>6} {g["lidos"]:>4} ({100 * g["lidos"] / g["exames"]:3.0f}%) '
                     f'{g["perguntados"]:>11} {g["baixa"]:>11} {missing:>9} {g["errados"]:>7} {g["pii"]:>3} {g["falhas"]:>10}')
    return '\n'.join(table + lines), problems


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m tests.load.manuscritos', description=__doc__.split('\n')[0])
    parser.add_argument('--origem', default='samples/manuscritos', help='imagens e gabarito.json')
    parser.add_argument('--pasta', default='/data/carga', help='volume que o OCR lê como /data/samples')
    parser.add_argument('--ocr', default='http://ocr:8001/sse')
    parser.add_argument('--rag', default='http://rag:8002/sse')
    parser.add_argument('--concorrencia', type=int, default=4)
    parser.add_argument('--detalhe', action='store_true', help='uma linha por imagem')
    args = parser.parse_args(argv)
    itens = gabarito(args.origem)
    for filename in itens:
        shutil.copy(Path(args.origem) / filename, Path(args.pasta) / filename)
    print(f'{len(itens)} imagens copiadas para {args.pasta}; OCR e RAG via MCP...', flush=True)
    table, problems = resumo(itens, asyncio.run(rodar(itens, args.ocr, args.rag, args.concorrencia)), args.detalhe)
    print(table)
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
