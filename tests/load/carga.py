"""Teste de carga de dados sensíveis: N pedidos fictícios pelo OCR (MCP via SSE), em paralelo.

    docker compose -f docker-compose.yml -f tests/load/compose.carga.yml -p carga \\
        run --rm tests python -m tests.load.carga --n 500 --concorrencia 8

Para cada pedido gerado por tests/load/pedidos.py: nenhum valor sensível do manifesto
pode aparecer no texto que o OCR devolve, e os exames devem continuar legíveis. Os
exames lidos vão ao RAG (MCP via SSE) e, com os códigos, a um POST na API. No fim, cada
agendamento é lido de volta (GET) e os bytes do SQLite e do WAL são conferidos sem
decifrar. Sai com código 1 se houver vazamento ou se algum pedido não puder ser conferido.
"""
import argparse
import asyncio
import contextlib
import json
import re
import statistics
import sys
import time
import unicodedata
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.shared.exceptions import MCPError

from tests.load.pedidos import gerar

DOCUMENTOS = {'nascimento', 'cpf', 'rg', 'telefone', 'cep', 'crm', 'carteirinha'}  # comparados só por dígitos
# Não contam como nome: partículas e palavras que também são rótulos do pedido
# ("Data de nascimento"), pois Nascimento também é sobrenome.
NOT_NAME_WORDS = {'da', 'de', 'do', 'das', 'dos', 'nascimento'}
NUMBER_RUN = re.compile(r'\d(?:[ .,/()-]{0,2}\d)*')  # "(11) 91234-5678" é um número só
MARKER = r'^\s*(?:\d+\s*[.)]|[-–•*])\s*'  # "1." "2)" "-" "•" no início da linha do exame
EXAM_LINE = re.compile(MARKER + r'(?P<exame>\S.*)$')
# Gerados pela API, não vêm do pedido: os dígitos de um UUID ou de um horário batem por acaso
# com algum CRM de 6 dígitos.
API_GENERATED = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|\d{4}-\d{2}-\d{2}T[\d:.]+(?:[+-]\d{2}:\d{2}|Z)?')


def plain(text):
    """Sem acento, sem caixa, só palavras: "José da Silva!" -> "jose da silva"."""
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(text).casefold()) if not unicodedata.combining(c))
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in text).split())


def vazamentos(texto, sensiveis):
    """Campos cujo valor aparece no texto, linha a linha.

    Documentos e datas: só os dígitos de cada número da linha, aceitando até dois
    separadores entre dígitos (espaço . , - / parênteses): "123.456.789-00" lido como
    "123 456,789 00" conta.
    Nomes, e-mail, endereço e CID: sem acento e caixa; um nome vaza se qualquer palavra
    dele (fora "da", "dos"...) sobrar no texto, e o endereço se o logradouro sobrar.
    """
    found = []
    for line in str(texto).splitlines():
        numbers = [re.sub(r'\D', '', run) for run in NUMBER_RUN.findall(line)]
        words, joined = f' {plain(line)} ', plain(line).replace(' ', '')
        for field, value in sensiveis.items():
            if field in DOCUMENTOS:
                leak = any(re.sub(r'\D', '', value) in number for number in numbers)
            elif field in ('paciente', 'medico'):
                leak = any(f' {word} ' in words for word in plain(value).split() if word not in NOT_NAME_WORDS)
            elif field == 'email':
                leak = plain(value.split('@')[0]).replace(' ', '') in joined
            elif field == 'endereco':
                leak = f' {plain(value.split(",")[0])} ' in words  # o logradouro, mesmo sem o número
            else:
                leak = f' {plain(value)} ' in words
            if leak:
                found.append((field, line))
    return found


def exames_lidos(lines):
    """Itens de lista do pedido ("1. Hemograma", "- TSH"): os exames, como o OCR os leu."""
    return [m['exame'].strip() for line in lines if (m := EXAM_LINE.match(line))]


async def chamar(session, tool, arguments):
    result = await session.call_tool(tool, arguments)
    if result.is_error:
        raise RuntimeError(result.content[0].text)
    return result.structured_content['result'] if tool == 'search_exams' else json.loads(result.content[0].text)


PEDIDO_SECONDS = 180  # um pedido que passa disso conta como falha, e a carga segue
# Uma resposta MCP que não chega em 60 s vira MCPError. Sem isso, um POST perdido na corrida de
# keep-alive (cliente e uvicorn com 5 s) prende a chamada para sempre: o ping SSE de 15 s mantém
# o stream vivo, e o sse_read_timeout nunca dispara. Em segundos (float): timedelta quebra no mcp 2.2.
CALL_SECONDS = 60.0
CLOSE_SECONDS = 10  # depois do último pedido, o tempo que as sessões MCP têm para fechar
SESSION_TRIES = 3  # tentativas seguidas de abrir ou fechar uma sessão MCP antes de o worker quebrar


def falha(item, error):
    """Métricas de um pedido que não pôde ser conferido: falha contada, nunca uma carga travada."""
    return {'erro': error, 'vazamentos': [], 'nao_lidos': [exam['name'] for exam in item['exames']],
            'mascarados': {}, 'ocr_s': None, 'codigos_certos': 0, 'agendado': False}


async def um_pedido(ocr, rag, api, filename, item):
    """OCR, conferência, RAG e POST de um pedido; devolve as métricas dele."""
    start = time.perf_counter()
    try:
        reply = await chamar(ocr, 'extract_exam_text', {'filename': filename})
    except RuntimeError as error:  # o OCR recusou: conta como falha, não derruba a carga
        return falha(item, str(error))
    except MCPError as error:  # a sessão morreu: o worker abre outra e repete o pedido uma vez
        return {**falha(item, f'MCPError: {error}'[:200]), 'sessao': True}
    ocr_seconds = time.perf_counter() - start
    texto = '\n'.join(reply['lines'])
    lidos = f' {plain(texto)} '
    nao_lidos = [exam['name'] for exam in item['exames'] if f' {plain(exam["name"])} ' not in lidos]
    found = {'erro': None, 'vazamentos': vazamentos(texto, item['sensiveis']), 'nao_lidos': nao_lidos,
             'mascarados': reply['pii_masked'], 'ocr_s': ocr_seconds, 'codigos_certos': 0, 'agendado': False}
    if rag is None:
        return found
    try:  # RAG e API: um erro aqui é falha deste pedido; a conferência do OCR acima fica
        codes = {}
        for exame in exames_lidos(reply['lines']):
            hits = await chamar(rag, 'search_exams', {'query': exame[:200], 'top_k': 1})
            if hits and hits[0]['score'] >= 0.9:
                codes.setdefault(hits[0]['code'], exame[:120])
        expected = {exam['code'] for exam in item['exames']}
        found['codigos_certos'] = len(expected & set(codes))
        if api and codes:
            body = {'exams': [{'code': code, 'name': name} for code, name in codes.items()][:20]}
            response = await api.post('/appointments', json=body)
            found['agendado'] = response.json()['id'] if response.status_code == 201 else False
            found['enviados'] = sorted(list(codes)[:20])
    except Exception as error:  # noqa: BLE001 - RuntimeError do MCP, httpx.HTTPError, JSON inválido
        found['erro'] = f'{type(error).__name__}: {error}'[:200]
        found['sessao'] = isinstance(error, MCPError)  # a sessão do RAG morreu: outra é aberta
    return found


async def rodar(manifesto, ocr_url, rag_url=None, api_url=None, concorrencia=8):  # noqa: C901 - cada falha de sessão ou pedido tem seu caminho, descrito abaixo e lido em ordem
    """Distribui os pedidos entre `concorrencia` sessões MCP; devolve ({arquivo: métricas}, segundos,
    sessões), em que sessões = {'nao_fecharam': n, 'quebraram': n} ajuda a achar um travamento.

    Nada trava a carga: cada chamada MCP tem CALL_SECONDS e cada pedido PEDIDO_SECONDS; uma
    sessão que morre (MCPError) é trocada por outra, e o pedido dela é repetido uma vez se
    ainda não foi agendado (o POST não se repete: não é idempotente). Um worker só sai quando
    todo pedido tem resultado, não quando a fila parece vazia: um pedido em andamento noutro
    worker pode voltar para a fila. Abrir ou fechar uma sessão tem SESSION_TRIES tentativas
    seguidas; esgotadas, o worker quebra e, se nenhum sobrar, os pedidos restantes viram
    falha. Quando todos têm resultado, um worker que ainda abre uma sessão é cancelado, e as
    sessões abertas têm CLOSE_SECONDS para fechar (as que não fecham são canceladas e contadas).
    """
    fila, results, tries, acabou = asyncio.Queue(), {}, {}, asyncio.Event()
    abrindo = set()  # workers abrindo uma sessão, sem pedido na mão
    for filename in manifesto:
        fila.put_nowait(filename)

    def registrar(filename, found):
        results[filename] = found
        if len(results) == len(manifesto):  # o último resultado: acorda quem espera na fila
            acabou.set()
            for _ in range(concorrencia):
                fila.put_nowait(None)

    async def worker():
        falhas = 0  # aberturas ou fechamentos de sessão que falharam seguidos
        while not acabou.is_set():  # sessões novas a cada vez que uma morre
            abrindo.add(asyncio.current_task())
            try:
                async with sse_client(ocr_url, sse_read_timeout=120) as ocr_streams, \
                        ClientSession(*ocr_streams, read_timeout_seconds=CALL_SECONDS) as ocr:
                    await ocr.initialize()
                    if rag_url is None:
                        await consumir(ocr, None, None)
                    else:
                        async with sse_client(rag_url, sse_read_timeout=120) as rag_streams, \
                                ClientSession(*rag_streams, read_timeout_seconds=CALL_SECONDS) as rag, \
                                httpx.AsyncClient(base_url=api_url, timeout=30) if api_url else contextlib.nullcontext() as api:
                            await rag.initialize()
                            await consumir(ocr, rag, api)
                falhas = 0
            except Exception:  # noqa: BLE001 - a sessão não abriu ou não fechou: tenta outra, até o limite
                falhas += 1
                if falhas >= SESSION_TRIES:
                    raise
                await asyncio.sleep(1)

    async def consumir(ocr, rag, api):
        """Pedidos até todos terem resultado, ou até a sessão morrer (então o worker abre outra)."""
        abrindo.discard(asyncio.current_task())
        while True:
            filename = await fila.get()  # espera: um pedido em andamento noutro worker pode voltar
            if filename is None:
                return
            try:
                found = await asyncio.wait_for(um_pedido(ocr, rag, api, filename, manifesto[filename]),
                                               PEDIDO_SECONDS)
            except Exception as error:  # noqa: BLE001 - tempo esgotado: a sessão pode ter ficado presa
                found = {**falha(manifesto[filename], f'{type(error).__name__}: {error}'[:200]), 'sessao': True}
            tries[filename] = tries.get(filename, 0) + 1
            if found.get('sessao') and not found['agendado'] and tries[filename] < 2:
                fila.put_nowait(filename)  # outra sessão tenta este pedido de novo
            else:
                registrar(filename, found)
            if found.get('sessao'):
                return

    start = time.perf_counter()
    if not manifesto:
        acabou.set()
    workers = [asyncio.create_task(worker()) for _ in range(concorrencia)]
    fim = asyncio.create_task(acabou.wait())
    while not acabou.is_set() and not all(task.done() for task in workers):
        await asyncio.wait([fim, *workers], return_when=asyncio.FIRST_COMPLETED)
    seconds = time.perf_counter() - start
    for task in abrindo:  # uma sessão que ainda abre não serve a mais nenhum pedido
        task.cancel()
    _, unclosed = await asyncio.wait(workers, timeout=CLOSE_SECONDS)
    for task in [*unclosed, fim]:
        task.cancel()
    broken = [task.exception() for task in workers if task.done() and not task.cancelled() and task.exception()]
    for filename in manifesto.keys() - results.keys():  # nenhuma sessão sobrou para eles
        results[filename] = falha(manifesto[filename], f'sem sessão MCP: {broken[0] if broken else "?"}'[:200])
    return results, seconds, {'nao_fecharam': len(unclosed), 'quebraram': len(broken)}


def banco_em_claro(db_path, manifesto, results):
    """(a) Os bytes do SQLite da API, do WAL e do SHM, sem decifrar: nenhum código FICT, e nenhum
    exame ou valor sensível de um pedido agendado, pode estar em claro.

    Documentos por dígitos; nomes, e-mail, endereço e exames pelas palavras de 4 letras ou mais
    (o CID, curto demais, coincidiria por acaso com um pedaço do texto cifrado). Devolve
    [(o que, pedido, valor)].
    """
    raw = b''.join(Path(f'{db_path}{suffix}').read_bytes() for suffix in ('', '-wal', '-shm')
                   if Path(f'{db_path}{suffix}').exists())
    text = API_GENERATED.sub(' ', raw.decode('utf-8', errors='replace'))
    tokens = set(plain(text).split())
    numbers = [re.sub(r'\D', '', run) for run in NUMBER_RUN.findall(text)]
    found = [('código FICT', '', code) for code in sorted(set(re.findall(r'FICT-?\d{3}', text)))]
    for filename, item in results.items():
        if not item['agendado']:
            continue
        values = {**manifesto[filename]['sensiveis'],
                  **{f'exame {exam["code"]}': exam['name'] for exam in manifesto[filename]['exames']}}
        for field, value in values.items():
            if field in DOCUMENTOS:
                leak = any(re.sub(r'\D', '', value) in number for number in numbers)
            else:
                parts = [word for word in plain(value.split('@')[0]).split() if len(word) >= 4]
                leak = bool(parts) and all(word in tokens for word in parts)
            if leak:
                found.append((field, filename, value))
    return found


async def conferir_api(api_url, results, concorrencia=8):
    """(b) GET /appointments/{id} de cada agendamento: a API decifra e devolve os exames enviados.

    Devolve os pedidos cujo agendamento não voltou igual (um erro de rede conta como diferente)."""
    booked = {filename: item for filename, item in results.items() if item['agendado']}
    limit = asyncio.Semaphore(concorrencia)
    async with httpx.AsyncClient(base_url=api_url, timeout=30) as api:
        async def same(item):
            async with limit:
                response = await api.get(f'/appointments/{item["agendado"]}')
            if response.status_code != 200:
                return False
            return sorted(exam['code'] for exam in response.json()['exams']) == item['enviados']
        checks = await asyncio.gather(*(same(item) for item in booked.values()), return_exceptions=True)
    return [filename for filename, ok in zip(booked, checks, strict=True) if ok is not True]


def percentil(values, p):
    return statistics.quantiles(values, n=100)[p - 1] if len(values) > 1 else values[0]


def quebrar(value, size=64):
    """Uma lista longa (`CPF 200 · NOME 400 · …`) em linhas de até `size`, sempre entre itens."""
    parts = []
    for item in value.split(' · '):
        if parts and len(parts[-1]) + 3 + len(item) <= size:
            parts[-1] += ' · ' + item
        else:
            parts.append(item)
    return parts


def resumo(manifesto, results, seconds, banco=None, diferentes=None, sessoes=None):
    """Tabela final em texto; banco e diferentes vêm de banco_em_claro e conferir_api."""
    found = list(results.values())
    masked, leaks = {}, [leak for item in found for leak in item['vazamentos']]
    for item in found:
        for kind, amount in item['mascarados'].items():
            masked[kind] = masked.get(kind, 0) + amount
    exams = sum(len(item['exames']) for item in manifesto.values())
    kept = exams - sum(len(item['nao_lidos']) for item in found)
    right = sum(item['codigos_certos'] for item in found)
    latency = sorted(item['ocr_s'] for item in found if item['ocr_s'] is not None) or [0.0]
    errors = [item['erro'] for item in found if item['erro']]
    booked = sum(bool(item['agendado']) for item in found)
    rows = [('Pedidos', f'{len(results)}'),
            ('Campos sensíveis impressos', f'{sum(len(item["sensiveis"]) for item in manifesto.values())}'),
            ('Mascarados pelo OCR, por tipo', ' · '.join(f'{k} {v}' for k, v in sorted(masked.items()))),
            ('Falhas do OCR', f'{len(errors)}' + (f' (ex.: {errors[0]})' if errors else '')),
            ('Vazamentos', f'{len(leaks)}'),
            ('Exames preservados', f'{kept}/{exams} ({100 * kept / exams:.1f}%)'),
            ('Códigos certos no RAG', f'{right}/{exams} ({100 * right / exams:.1f}%)'),
            ('Agendados na API', f'{booked}' + ('' if diferentes is None else
                                                f'; lidos de volta (GET) iguais ao enviado: {booked - len(diferentes)}')),
            ('SQLite em bytes (banco e WAL)', 'não conferido (sem o volume api-data)' if banco is None
             else f'{len(banco)} em claro (código FICT, exame ou valor sensível)'),
            ('Latência do OCR p50 / p95', f'{percentil(latency, 50):.2f} s / {percentil(latency, 95):.2f} s'),
            ('Vazão', f'{len(results) / seconds:.2f} pedidos/s ({seconds:.0f} s no total)')]
    if sessoes is not None:  # aviso, não falha: uma sessão que não fecha não muda nenhum pedido
        rows.append(('Sessões MCP que quebraram / não fecharam', f'{sessoes["quebraram"]} / {sessoes["nao_fecharam"]}'))
    width = max(len(label) for label, _ in rows)
    lines = [f'{label if i == 0 else "":<{width}}  {part}'
             for label, value in rows for i, part in enumerate(quebrar(value))]
    for filename, item in sorted(results.items()):
        lines += [f'  VAZOU {field} em {filename}: {line}' for field, line in item['vazamentos']]
        lines += [f'  exame não lido em {filename}: {name}' for name in item['nao_lidos']]
    lines += [f'  EM CLARO no SQLite: {what} {filename} {value}'.rstrip() for what, filename, value in banco or []]
    lines += [f'  agendamento diferente do enviado: {filename}' for filename in diferentes or []]
    return '\n'.join(lines), len(leaks) + len(errors) + len(banco or []) + len(diferentes or [])


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m tests.load.carga', description=__doc__.split('\n')[0])
    parser.add_argument('--n', type=int, default=500)
    parser.add_argument('--concorrencia', type=int, default=8)
    parser.add_argument('--pasta', default='/data/carga', help='onde gravar as imagens (o OCR lê dali)')
    parser.add_argument('--ocr', default='http://ocr:8001/sse')
    parser.add_argument('--rag', default='http://rag:8002/sse')
    parser.add_argument('--api', default='http://api:8000')
    parser.add_argument('--banco', default='/data/api/appointments.db', help='SQLite da API (montado só para ler)')
    args = parser.parse_args(argv)
    manifesto = gerar(args.pasta, args.n)
    print(f'{args.n} pedidos gerados em {args.pasta}; enviando ao OCR com {args.concorrencia} sessões...', flush=True)
    results, seconds, sessoes = asyncio.run(rodar(manifesto, args.ocr, args.rag, args.api, args.concorrencia))
    diferentes = asyncio.run(conferir_api(args.api, results, args.concorrencia))
    banco = banco_em_claro(args.banco, manifesto, results) if Path(args.banco).exists() else None
    table, problems = resumo(manifesto, results, seconds, banco, diferentes, sessoes)
    print(table)
    return 1 if problems else 0  # um vazamento ou um pedido não conferido


if __name__ == '__main__':
    sys.exit(main())
