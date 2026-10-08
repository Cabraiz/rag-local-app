"""Teste de robustez: entradas faltando ou quebradas pelo caminho real, em paralelo.

    docker compose -f docker-compose.yml -f tests/load/compose.carga.yml -p robustez \\
        run --rm tests python -m tests.load.robustez --variantes 12 --concorrencia 8

Gera com semente fixa casos de imagem (página em branco, só PII, girada, minúscula,
enorme, JPEG truncado, PNG corrompido, 0 byte...), de consulta ao RAG e de corpo da API.
Cada imagem passa pelo OCR (MCP via SSE) e cada linha lida pelo RAG (MCP via SSE). Um
"modelo" adversário propõe TODOS os códigos que o RAG devolveu, e quem decide é a regra
real do agente gerado (only_confident_codes, do transpilador). O que sobra vai a um POST
na API (HTTP). As consultas ao RAG e os corpos da API vão direto aos serviços.

Critérios de cada caso: nenhum erro 500, nenhum traceback, nenhum exame agendado que não
está na imagem, nenhuma PII no texto que sai do OCR, e toda recusa com mensagem clara.
Sai com código 1 se algum caso falhar. --caso <id> refaz só os casos indicados, com o
mesmo conteúdo (a semente é fixa).
"""
import argparse
import asyncio
import io
import json
import random
import re
import statistics
import tempfile
import time
import warnings
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from PIL import Image, ImageDraw, ImageFilter

from tests.load import carga, pedidos

SEED = 20261006
LIMITE_CASO_S = 120  # o OCR tem limite próprio de 30 s; acima disso, o caso travou
EXAMS = {exam['code']: exam['name'] for exam in pedidos.EXAMS}
CODE_OF = {name: code for code, name in EXAMS.items()}
FORA_DO_CATALOGO = ['Tomografia de crânio', 'Exame de vista', 'Biópsia hepática', 'Mapeamento genético completo',
                    'Ressonância de joelho', 'Teste de esforço']
# Nomes em inglês de exames do catálogo: o mesmo exame, então agendá-lo não é erro.
EM_INGLES = {'Complete blood count': 'Hemograma completo', 'Fasting glucose': 'Glicemia de jejum',
             'Creatinine': 'Creatinina', 'Vitamin D': 'Vitamina D', 'Total cholesterol': 'Colesterol total',
             'Urinalysis': 'Urina tipo I'}
# Texto que nunca deve chegar ao usuário: rastros de exceção ou de código.
INTERNO = re.compile(r'Traceback|File "|line \d+, in |\b\w+(?:Error|Exception)\b|NoneType|object at 0x|errno'
                     # dump de validação do pydantic: tipo interno, valor recebido e link da documentação
                     r'|validation errors? for|\[type=\w+|input_value=|errors\.pydantic\.dev', re.I)
PREFIXO_MCP = re.compile(r'^Error executing tool \w+: ')
ROTULO = re.compile(r'^\s*(?:exames?(?: solicitados?)?|solicito os exames)\s*:?\s*', re.I)
MARCADOR = re.compile(r'^\s*(?:\d+\s*[.)]|[-–•*])\s*')


def mensagem_clara(texto):
    """Uma frase para o usuário: sem traceback nem nome de exceção, de 10 a 300 caracteres."""
    texto = PREFIXO_MCP.sub('', str(texto)).strip()
    return 10 <= len(texto) <= 300 and not INTERNO.search(texto), texto


# ---------------------------------------------------------------- casos de imagem

def desenhar(texto, rng, tamanho=26, largura=1100):
    """Texto em fundo branco, como um pedido escaneado; devolve (imagem, y de cada linha)."""
    font = pedidos.fonte(rng.randrange(3), tamanho)
    passo = int(tamanho * 1.7)
    image = Image.new('L', (largura, 60 + passo * max(len(texto), 1)), 255)
    draw = ImageDraw.Draw(image)
    rows = []
    for row, text in enumerate(texto):
        y = 30 + passo * row
        draw.text((60, y), text, fill=rng.randrange(0, 60), font=font)
        rows.append(y)
    for _ in range(400):
        draw.point((rng.randrange(image.width), rng.randrange(image.height)), fill=rng.randrange(150, 230))
    return image, rows


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


def jpeg(image, quality=85):
    buffer = io.BytesIO()
    image.convert('L').save(buffer, 'JPEG', quality=quality)
    return buffer.getvalue()


def pedido_completo(rng, exames, sem=()):
    """Linhas de um pedido com PII e exames; `sem` tira campos (paciente, médico, data)."""
    d = pedidos.dados(rng)
    texto = ['PEDIDO MÉDICO DE EXAMES', 'Clínica Exemplo de Diagnóstico', '']
    if 'paciente' not in sem:
        texto += [f'Paciente: {d["paciente"]}', f'CPF: {d["cpf"]}', f'Telefone: {d["telefone"]}']
    texto += ['', 'Exames solicitados:', *[f'- {exame}' for exame in exames], '']
    if 'medico' not in sem:
        texto.append(f'Dr. {d["medico"]} - {d["crm"]}')
    if 'data' not in sem:
        texto.append(f'Data: {rng.randrange(1, 29):02d}/{rng.randrange(1, 13):02d}/2026')
    sensiveis = {k: d[k] for k in ('paciente', 'cpf', 'telefone') if 'paciente' not in sem}
    if 'medico' not in sem:
        sensiveis.update(medico=d['medico'], crm=d['crm'])
    return texto, sensiveis


def alguns_exames(rng, n=None):
    return rng.sample(sorted(EXAMS.values()), n or rng.randrange(1, 5))


def caso_imagem(categoria, rng):  # noqa: C901 - um ramo por categoria de robustez, lido como tabela
    """(arquivos {nome: bytes}, nomes a enviar ao OCR, códigos que podem ser agendados, valores sensíveis)."""
    exames = alguns_exames(rng)
    permitidos = {CODE_OF[e] for e in exames}
    sensiveis = {}
    ext = '.png'
    if categoria == 'página em branco':
        dados, permitidos = png(Image.new('L', (1100, 1400), 255)), set()
    elif categoria == 'só PII, sem exame':
        texto, sensiveis = pedido_completo(rng, [])
        texto = [line for line in texto if not line.startswith('Exames')]
        dados, permitidos = png(desenhar(texto, rng)[0]), set()
    elif categoria == 'só cabeçalho':
        dados, permitidos = png(desenhar(['PEDIDO MÉDICO DE EXAMES', 'Clínica Exemplo de Diagnóstico'], rng)[0]), set()
    elif categoria == '"Exames:" vazio':
        texto, sensiveis = pedido_completo(rng, [])
        dados, permitidos = png(desenhar(texto, rng)[0]), set()
    elif categoria == 'linhas cortadas na metade':
        texto, sensiveis = pedido_completo(rng, exames)
        image, rows = desenhar(texto, rng)
        corte = rng.randrange(texto.index('Exames solicitados:') + 1, len(texto) - 1)
        # corta a página no meio da altura de uma linha: as de baixo somem, essa fica pela metade
        dados = png(image.crop((0, 0, image.width, rows[corte] + 13)))
    elif categoria == 'ilegível ou riscado':
        texto, sensiveis = pedido_completo(rng, exames)
        image, rows = desenhar(texto, rng)
        draw = ImageDraw.Draw(image)
        for y in rows:  # risco por cima de cada linha
            draw.line((40, y + 14 + rng.randrange(-4, 5), image.width - 40, y + 10 + rng.randrange(-4, 5)),
                      fill=0, width=rng.randrange(4, 9))
        dados = png(image.filter(ImageFilter.GaussianBlur(rng.uniform(1.5, 3.0))))
    elif categoria in ('girada 90°', 'girada 180°', 'de cabeça para baixo'):
        texto, sensiveis = pedido_completo(rng, exames)
        image = desenhar(texto, rng)[0]
        image = {'girada 90°': lambda i: i.transpose(Image.Transpose.ROTATE_90),
                 'girada 180°': lambda i: i.transpose(Image.Transpose.ROTATE_180),
                 'de cabeça para baixo': lambda i: i.transpose(Image.Transpose.FLIP_TOP_BOTTOM)}[categoria](image)
        dados = png(image)
    elif categoria == 'minúscula (50 px)':
        texto, sensiveis = pedido_completo(rng, exames)
        image = desenhar(texto, rng)[0]
        dados = png(image.resize((max(1, image.width * 50 // image.height), 50)))
    elif categoria == 'enorme (perto do limite)':
        texto, sensiveis = pedido_completo(rng, exames)
        lado = rng.choice([4900, 4990])  # 24,0 e 24,9 milhões de pixels; o limite é 25 milhões
        image = Image.new('L', (lado, lado), 255)
        image.paste(desenhar(texto, rng, tamanho=60, largura=2600)[0], (100, 100))
        dados = png(image)
    elif categoria == 'acima do limite':
        dados, permitidos = png(Image.new('L', (5100, 5100), 255)), set()  # 26 milhões de pixels
    elif categoria == 'JPEG truncado':
        texto, sensiveis = pedido_completo(rng, exames)
        completo = jpeg(desenhar(texto, rng)[0])
        dados, ext = completo[:int(len(completo) * rng.uniform(0.2, 0.8))], '.jpg'
    elif categoria == 'PNG corrompido':
        texto, sensiveis = pedido_completo(rng, exames)
        raw = bytearray(png(desenhar(texto, rng)[0]))
        for _ in range(rng.randrange(3, 30)):
            raw[rng.randrange(60, len(raw))] = rng.randrange(256)
        dados = bytes(raw)
    elif categoria == 'extensão errada':
        texto, sensiveis = pedido_completo(rng, exames)
        image = desenhar(texto, rng)[0]
        dados, ext = rng.choice([(png(image), '.jpg'), (jpeg(image), '.png'), (png(image), '.gif'),
                                 ('\n'.join(texto).encode('utf-8'), '.txt'), (png(image), '.PNG')])
        if ext != '.PNG':
            permitidos = set()  # recusado pela extensão ou pelo conteúdo: nada é lido
    elif categoria == '0 byte':
        dados, permitidos = b'', set()
    elif categoria == 'arquivo inexistente':
        return {}, [f'nao-existe-{rng.randrange(10**6)}.png'], set(), {}
    elif categoria == 'caminho no nome':
        return {}, [rng.choice(['../etc/passwd.png', '/data/samples/pedido.png', '..\\pedido.png', 'a/b.png'])], set(), {}
    elif categoria == 'duplicada':
        texto, sensiveis = pedido_completo(rng, exames)
        dados = png(desenhar(texto, rng)[0])
        # duas cópias com nomes diferentes, e a primeira pedida duas vezes ao mesmo tempo
        return {'a.png': dados, 'b.png': dados}, ['a.png', 'a.png', 'b.png'], permitidos, sensiveis
    elif categoria == 'exame repetido 3x':
        exame = alguns_exames(rng, 1)[0]
        texto, sensiveis = pedido_completo(rng, [exame] * 3)
        dados, permitidos = png(desenhar(texto, rng)[0]), {CODE_OF[exame]}
    elif categoria == 'exame fora do catálogo':
        texto, sensiveis = pedido_completo(rng, rng.sample(FORA_DO_CATALOGO, rng.randrange(1, 4)))
        dados, permitidos = png(desenhar(texto, rng)[0]), set()
    elif categoria == 'exames em inglês':
        nomes = rng.sample(sorted(EM_INGLES), rng.randrange(1, 4))
        texto, sensiveis = pedido_completo(rng, nomes)
        dados, permitidos = png(desenhar(texto, rng)[0]), {CODE_OF[EM_INGLES[n]] for n in nomes}
    elif categoria in ('sem paciente', 'sem médico', 'sem data'):
        campo = {'sem paciente': 'paciente', 'sem médico': 'medico', 'sem data': 'data'}[categoria]
        texto, sensiveis = pedido_completo(rng, exames, sem=(campo,))
        dados = png(desenhar(texto, rng)[0])
    else:
        raise ValueError(categoria)
    return {'pedido' + ext: dados}, ['pedido' + ext], permitidos, sensiveis


CATEGORIAS_IMAGEM = ['página em branco', 'só PII, sem exame', 'só cabeçalho', '"Exames:" vazio',
                     'linhas cortadas na metade', 'ilegível ou riscado', 'girada 90°', 'girada 180°',
                     'de cabeça para baixo', 'minúscula (50 px)', 'enorme (perto do limite)', 'acima do limite',
                     'JPEG truncado', 'PNG corrompido', 'extensão errada', '0 byte', 'arquivo inexistente',
                     'caminho no nome', 'duplicada', 'exame repetido 3x', 'exame fora do catálogo',
                     'exames em inglês', 'sem paciente', 'sem médico', 'sem data']


# ---------------------------------------------------------------- casos do RAG e da API

def consultas_rag(rng, n):
    """{categoria: [argumentos de search_exams]}."""
    letras = 'abcdefghijklmnopqrstuvwxyzáéíóúãõç '
    emoji = '🧪💉😀🩸🔬❤️🇧🇷👍'
    return {
        'RAG: consulta vazia': [{'query': ''} for _ in range(n)],
        'RAG: só espaços': [{'query': ' ' * rng.randrange(1, 50) + rng.choice(['', '\t', '\n'])} for _ in range(n)],
        'RAG: 5.000 caracteres': [{'query': ''.join(rng.choice(letras) for _ in range(5000))} for _ in range(n)],
        'RAG: emoji': [{'query': ''.join(rng.choice(emoji) for _ in range(rng.randrange(1, 8)))
                        + rng.choice(['', ' hemograma', ' glicose'])} for _ in range(n)],
        'RAG: caracteres de controle': [{'query': ''.join(rng.choice(['\x00', '\x07', '\x1b[31m', '\x7f', '\u202e', 'tsh', 'ferritina'])
                                                         for _ in range(rng.randrange(2, 8)))} for _ in range(n)],
        'RAG: SQL no texto': [{'query': rng.choice(["'; DROP TABLE exams; --", "1' OR '1'='1", 'hemograma" UNION SELECT *',
                                                    "glicose'); DELETE FROM appointments; --", "%' AND 1=1 --"])}
                              for _ in range(n)],
        'RAG: JSON no texto': [{'query': rng.choice(['{"code": "FICT-001"}', '[{"query": 1}]', '{"top_k": 99}',
                                                     '{"exams": [{"code": "FICT-120"}]}', 'null'])} for _ in range(n)],
        'RAG: tipo ou top_k inválido': [rng.choice([{'query': 123}, {'query': None}, {'query': ['tsh']},
                                                    {'query': 'tsh', 'top_k': 0}, {'query': 'tsh', 'top_k': 1000},
                                                    {'query': 'tsh', 'top_k': 'x'}, {'query': 'tsh', 'top_k': True}])
                                        for _ in range(n)],
    }


def argumentos_ocr(rng, n):
    """{categoria: [argumentos de extract_exam_text]}: tipos errados, chave faltando ou a mais."""
    return {'OCR: argumento inválido': [rng.choice([{'filename': None}, {'filename': 123}, {'filename': ['a.png']},
                                                    {}, {'filename': {'nome': 'a.png'}}, {'filename': True}])
                                        for _ in range(n)]}


def corpos_api(rng, n):
    """{categoria: [(método, caminho, kwargs do httpx, status aceitos)]}."""
    codes = sorted(EXAMS)
    um = lambda: {'code': rng.choice(codes), 'name': 'exame'}  # noqa: E731
    muitos = lambda k: [{'code': c, 'name': 'exame'} for c in rng.sample(codes, k)]  # noqa: E731
    post = lambda **kw: ('POST', '/appointments', kw, {400, 413, 415, 422})  # noqa: E731
    return {
        'API: corpo vazio': [post(content=b'', headers={'Content-Type': 'application/json'}) for _ in range(n)],
        'API: campos faltando': [post(json=rng.choice([{}, {'exams': [{}]}, {'exams': [{'code': 'FICT-001'}]},
                                                       {'exams': [{'name': 'Hemograma'}]}])) for _ in range(n)],
        'API: null': [post(json=rng.choice([None, {'exams': None}, {'exams': [None]}, {'exams': [{'code': None, 'name': None}]}]))
                      for _ in range(n)],
        'API: tipos trocados': [post(json=rng.choice([{'exams': 'FICT-001'}, {'exams': {'code': 'FICT-001'}},
                                                      {'exams': [{'code': 1, 'name': 2}]}, {'exams': [['FICT-001']]},
                                                      {'exams': [{'code': 'fict-001', 'name': 'x'}]}, [um()]]))
                                for _ in range(n)],
        'API: mais de 20 exames': [post(json={'exams': muitos(rng.randrange(21, 60))}) for _ in range(n)],
        'API: códigos repetidos': [post(json={'exams': [e := um(), e, *([e] * rng.randrange(0, 3))]}) for _ in range(n)],
        'API: código fora do catálogo': [post(json={'exams': [{'code': f'FICT-{rng.randrange(121, 1000):03d}', 'name': 'x'}]})
                                         for _ in range(n)],
        'API: JSON quebrado ou outro formato': [post(content=rng.choice([b'{"exams": [', b'\xff\xfe\x00', b'exams=FICT-001']),
                                                     headers={'Content-Type': rng.choice(['application/json', 'text/plain'])})
                                                for _ in range(n)],
        'API: corpo acima de 16 KB': [post(json={'exams': [{'code': 'FICT-001', 'name': 'x' * 20000}]}) for _ in range(n)],
        'API: id inválido ou inexistente': [('GET', rng.choice(['/appointments/abc', '/appointments/123',
                                                                '/appointments/00000000-0000-4000-8000-000000000000']),
                                             {}, {404, 422}) for _ in range(n)],
    }


# ---------------------------------------------------------------- execução

class Ferramenta:
    def __init__(self, name):
        self.name = name


class Contexto:
    def __init__(self):
        self.state = {}


def agente_gerado():
    """O agent.py que o transpilador gera da spec do projeto, com a regra real de agendamento."""
    import importlib.util

    from transpiler import transpile
    destino = Path(tempfile.mkdtemp()) / 'agent.py'
    with warnings.catch_warnings():  # avisos de recurso experimental do ADK, irrelevantes aqui
        warnings.simplefilter('ignore')
        transpile(pedidos.ROOT / 'specs' / 'agent.json', destino)  # também importa o arquivo gerado
        spec = importlib.util.spec_from_file_location('agente_robustez', destino)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    module.CALLBACKS = module.app.plugins[0]  # o BookingPlugin: os callbacks que este teste chama
    return module


def resposta_mcp(result):
    """A resposta MCP como o ADK a entrega aos callbacks (dict com structuredContent / isError)."""
    return result.model_dump(mode='json', by_alias=True, exclude_none=True)


def consulta(linha):
    """O nome do exame numa linha lida, como o modelo pediria ao RAG: sem rótulo nem marcador."""
    return MARCADOR.sub('', ROTULO.sub('', linha)).strip()[:200]


async def caso_de_imagem(sessoes, agente, nome, permitidos, sensiveis):  # noqa: C901 - um roteiro linear: cada passo devolve sua falha
    """OCR, PII, RAG de cada linha, a regra do agente e o POST; devolve (resultado, detalhe)."""
    ocr, rag, api = sessoes
    result = await ocr.call_tool('extract_exam_text', {'filename': nome})
    if result.is_error:
        clara, texto = mensagem_clara(' '.join(item.text for item in result.content if hasattr(item, 'text')))
        return ('clara', texto) if clara else ('falhou', f'mensagem do OCR pouco clara: {texto!r}')
    reply = json.loads(result.content[0].text)
    linhas = reply['lines']
    vazou = carga.vazamentos('\n'.join(linhas), sensiveis)
    if vazou:
        return 'falhou', f'PII sobrando no texto do OCR: {vazou[:3]}'
    contexto = Contexto()
    agente.CALLBACKS.after_tool(Ferramenta('extract_exam_text'), {'filename': nome}, contexto, resposta_mcp(result))
    propostos = {}
    for linha in linhas:
        query = consulta(linha)
        if not query:
            continue
        hits = await rag.call_tool('search_exams', {'query': query})
        if hits.is_error:
            clara, texto = mensagem_clara(hits.content[0].text)
            if not clara:
                return 'falhou', f'mensagem do RAG pouco clara para {query!r}: {texto!r}'
            continue
        agente.CALLBACKS.after_tool(Ferramenta('search_exams'), {'query': query}, contexto, resposta_mcp(hits))
        for hit in hits.structured_content['result']:
            propostos.setdefault(hit['code'], hit['name'])
    if not propostos:
        return 'ok', 'nenhum exame encontrado; nada agendado'
    # O "modelo" adversário pede tudo o que o RAG devolveu; a regra do agente decide.
    args = {'exams': [{'code': code, 'name': name} for code, name in propostos.items()]}
    bloqueio = await agente.CALLBACKS.before_tool(Ferramenta('create_appointment'), args, contexto)
    if bloqueio is not None:
        return 'ok', f'bloqueado antes da API: {bloqueio["blocked"]}'
    response = await api.post('/appointments', json={'exams': args['exams'][:20]})
    if response.status_code != 201:
        return 'falhou', f'POST com os códigos aprovados respondeu {response.status_code}: {response.text[:200]}'
    agendados = {exam['code'] for exam in response.json()['exams']}
    if agendados - permitidos:
        nomes = {code: EXAMS[code] for code in sorted(agendados - permitidos)}
        return 'falhou', f'agendou exame que não está na imagem: {nomes}; lido: {linhas}'
    return 'ok', f'agendados {sorted(agendados)}'


def recusa_mcp(result, servico):
    """O resultado de uma ferramenta MCP que recusou a chamada: ('clara', texto) ou ('falhou', motivo)."""
    texto = ' '.join(item.text for item in result.content if hasattr(item, 'text'))
    # Um argumento AUSENTE ({}) é recusado pelo SDK do MCP antes de a ferramenta rodar, com o
    # "Field required" do pydantic: filename e query continuam obrigatórios no schema publicado,
    # que o Gemini lê para montar a chamada (decisão do projeto). Essa recusa nomeia o campo e
    # o motivo, então conta como clara. Só ela: os outros dumps de validação continuam falha.
    if 'Field required' in texto and '[type=missing' in texto:
        return 'clara', 'campo obrigatório ausente (recusa do SDK do MCP)'
    clara, texto = mensagem_clara(texto)
    return ('clara', texto) if clara else ('falhou', f'mensagem do {servico} pouco clara: {texto!r}')


async def caso_de_rag(rag, argumentos):
    result = await rag.call_tool('search_exams', argumentos)
    if result.is_error:
        outcome = recusa_mcp(result, 'RAG')
    else:
        hits = result.structured_content['result']
        ruins = [hit for hit in hits if hit.get('code') not in EXAMS or not 0 <= hit.get('score', -1) <= 1]
        outcome = ('falhou', f'resultado fora do catálogo: {ruins}') if ruins else ('ok', f'{len(hits)} resultado(s)')
    # a mesma sessão continua respondendo depois da entrada estranha
    depois = await rag.call_tool('search_exams', {'query': 'Glicose'})
    if depois.is_error or depois.structured_content['result'][0]['code'] != 'FICT-002':
        return 'falhou', 'a sessão do RAG parou de responder depois desta consulta'
    return outcome


async def caso_de_ocr(ocr, argumentos):
    result = await ocr.call_tool('extract_exam_text', argumentos)
    if result.is_error:
        outcome = recusa_mcp(result, 'OCR')
    else:
        outcome = 'ok', 'lido'
    depois = await ocr.call_tool('extract_exam_text', {'filename': 'nao-existe.png'})
    if not depois.is_error or 'não encontrado' not in depois.content[0].text:
        return 'falhou', 'a sessão do OCR parou de responder depois desta chamada'
    return outcome


async def caso_de_api(api, metodo, caminho, kwargs, aceitos):
    response = await api.request(metodo, caminho, **kwargs)
    if response.status_code >= 500:
        return 'falhou', f'{response.status_code}: {response.text[:200]}'
    if response.status_code not in aceitos:
        return 'falhou', f'status {response.status_code} inesperado: {response.text[:200]}'
    try:
        detail = response.json()['detail']
    except (ValueError, KeyError, TypeError):
        return 'falhou', f'{response.status_code} sem JSON com "detail": {response.text[:200]}'
    mensagens = [detail] if isinstance(detail, str) else [f'{item.get("loc")}: {item.get("msg")}' for item in detail]
    if not mensagens or not all(mensagem_clara(m)[0] for m in mensagens):
        return 'falhou', f'mensagem pouco clara: {mensagens[:2]}'
    return 'clara', f'{response.status_code}: {mensagens[0][:120]}'


async def concorrencia_api(api, n):
    """n POSTs válidos ao mesmo tempo: todos 201, ids distintos, e o GET devolve o mesmo corpo."""
    rng = random.Random(SEED + 7)
    corpos = [{'exams': [{'code': c, 'name': 'x'} for c in rng.sample(sorted(EXAMS), rng.randrange(1, 4))]} for _ in range(n)]
    start = time.perf_counter()
    respostas = await asyncio.gather(*(api.post('/appointments', json=corpo) for corpo in corpos))
    segundos = (time.perf_counter() - start) / n
    resultados = []
    ids = [r.json().get('id') if r.status_code == 201 else None for r in respostas]
    for corpo, resposta, id_ in zip(corpos, respostas, ids, strict=True):
        if resposta.status_code != 201:
            resultados.append(('falhou', f'{resposta.status_code}: {resposta.text[:200]}', segundos))
            continue
        lido = await api.get(f'/appointments/{id_}')
        esperado = sorted(e['code'] for e in corpo['exams'])
        if lido.status_code != 200 or sorted(e['code'] for e in lido.json()['exams']) != esperado:
            resultados.append(('falhou', f'GET {id_} devolveu {lido.status_code}: {lido.text[:200]}', segundos))
        elif ids.count(id_) > 1:
            resultados.append(('falhou', f'id repetido: {id_}', segundos))
        else:
            resultados.append(('ok', f'201 e GET igual ({id_[:8]})', segundos))
    return resultados


def montar(variantes, pasta):
    """Grava as imagens em `pasta` e devolve a lista de casos [(id, categoria, tipo, dados)]."""
    Path(pasta).mkdir(parents=True, exist_ok=True)
    rng, casos = random.Random(SEED), []
    for categoria in CATEGORIAS_IMAGEM:
        for indice in range(variantes):
            id_ = f'img-{CATEGORIAS_IMAGEM.index(categoria):02d}-{indice:03d}'
            arquivos, nomes, permitidos, sensiveis = caso_imagem(categoria, random.Random(f'{SEED}-{id_}'))
            renomear = {nome: f'{id_}-{nome}' for nome in arquivos}
            for nome, dados in arquivos.items():
                (Path(pasta) / renomear[nome]).write_bytes(dados)
            for nome in nomes:
                casos.append((id_, categoria, 'imagem', (renomear.get(nome, nome), permitidos, sensiveis)))
    slug = lambda categoria: carga.plain(categoria.split(': ', 1)[1]).replace(' ', '-')[:16]  # noqa: E731
    for categoria, lista in consultas_rag(rng, variantes).items():
        casos += [(f'rag-{slug(categoria)}-{i:03d}', categoria, 'rag', args) for i, args in enumerate(lista)]
    for categoria, lista in argumentos_ocr(rng, variantes).items():
        casos += [(f'ocr-{slug(categoria)}-{i:03d}', categoria, 'ocr', args) for i, args in enumerate(lista)]
    for categoria, lista in corpos_api(rng, variantes).items():
        casos += [(f'api-{slug(categoria)}-{i:03d}', categoria, 'api', pedido) for i, pedido in enumerate(lista)]
    return casos


async def rodar(casos, ocr_url, rag_url, api_url, concorrencia=8, posts_simultaneos=50, transporte=None):  # noqa: C901 - um ramo por tipo de caso e a reabertura das sessões
    """Executa os casos em `concorrencia` trabalhadores; devolve [(id, categoria, resultado, detalhe, segundos)].

    `transporte` (httpx) troca a rede pela API em processo, como no teste da CI."""
    agente = agente_gerado()
    fila, resultados = asyncio.Queue(), []
    for caso in casos:
        fila.put_nowait(caso)

    async def trabalhador():
        quedas = 0
        while not fila.empty() and quedas < 5:
            try:
                async with sse_client(ocr_url, sse_read_timeout=180) as o, ClientSession(*o) as ocr, \
                        sse_client(rag_url) as r, ClientSession(*r) as rag, \
                        httpx.AsyncClient(base_url=api_url, timeout=60, transport=transporte) as api:
                    await ocr.initialize()
                    await rag.initialize()
                    while not fila.empty():
                        id_, categoria, tipo, dados = fila.get_nowait()
                        start = time.perf_counter()
                        if tipo == 'imagem':
                            caso = caso_de_imagem((ocr, rag, api), agente, *dados)
                        elif tipo == 'rag':
                            caso = caso_de_rag(rag, dados)
                        elif tipo == 'ocr':
                            caso = caso_de_ocr(ocr, dados)
                        else:
                            caso = caso_de_api(api, *dados)
                        try:
                            # Um caso sem resposta não pode travar a suíte: conta como falha e reabre as sessões.
                            outcome = await asyncio.wait_for(caso, LIMITE_CASO_S)
                        except Exception as error:  # um caso que derruba o cliente é um achado, não o fim da suíte
                            motivo = (f'sem resposta em {LIMITE_CASO_S} s' if isinstance(error, TimeoutError)
                                      else f'exceção no caminho: {error!r}')
                            resultados.append((id_, categoria, 'falhou', motivo[:300], time.perf_counter() - start))
                            raise
                        resultados.append((id_, categoria, *outcome, time.perf_counter() - start))
            except Exception as error:  # a sessão caiu: abre outra e segue com a fila
                quedas += 1
                print(f'sessão reaberta ({quedas}/5): {error!r}'[:200], flush=True)
                await asyncio.sleep(0.5)

    start = time.perf_counter()
    await asyncio.gather(*(trabalhador() for _ in range(concorrencia)))
    if posts_simultaneos:
        async with httpx.AsyncClient(base_url=api_url, timeout=60, transport=transporte) as api:
            for indice, (outcome, detalhe, segundos) in enumerate(await concorrencia_api(api, posts_simultaneos)):
                resultados.append((f'api-conc-{indice:03d}', f'API: {posts_simultaneos} POSTs ao mesmo tempo',
                                   outcome, detalhe, segundos))
    return resultados, time.perf_counter() - start


def percentil(values, p):
    return statistics.quantiles(values, n=100)[p - 1] if len(values) > 1 else (values[0] if values else 0.0)


def resumo(resultados, segundos, ordem=()):
    """Tabela por categoria (casos / ok / mensagem clara / falhou / p50 / p95) e a lista das falhas."""
    ordem = list(dict.fromkeys([*ordem, *(categoria for _, categoria, *_ in resultados)]))
    linhas = [('Categoria', 'casos', 'ok', 'msg clara', 'falhou', 'p50 (s)', 'p95 (s)')]
    for categoria in [c for c in ordem if any(r[1] == c for r in resultados)]:
        grupo = [r for r in resultados if r[1] == categoria]
        tempos = [r[4] for r in grupo]
        linhas.append((categoria, len(grupo), sum(r[2] == 'ok' for r in grupo), sum(r[2] == 'clara' for r in grupo),
                       sum(r[2] == 'falhou' for r in grupo), f'{percentil(tempos, 50):.2f}', f'{percentil(tempos, 95):.2f}'))
    tempos = [r[4] for r in resultados]
    falhas = [r for r in resultados if r[2] == 'falhou']
    linhas.append(('TOTAL', len(resultados), sum(r[2] == 'ok' for r in resultados),
                   sum(r[2] == 'clara' for r in resultados), len(falhas),
                   f'{percentil(tempos, 50):.2f}', f'{percentil(tempos, 95):.2f}'))
    largura = max(len(str(linha[0])) for linha in linhas)
    texto = [f'{linha[0]:<{largura}}  ' + '  '.join(f'{str(v):>9}' for v in linha[1:]) for linha in linhas]
    texto.append(f'{len(resultados)} casos em {segundos:.0f} s')
    texto += [f'  FALHOU {id_} ({categoria}): {detalhe}' for id_, categoria, _, detalhe, _ in falhas]
    return '\n'.join(texto), len(falhas)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m tests.load.robustez', description=__doc__.split('\n')[0])
    parser.add_argument('--variantes', type=int, default=12, help='casos por categoria')
    parser.add_argument('--concorrencia', type=int, default=8)
    parser.add_argument('--posts-simultaneos', type=int, default=50)
    parser.add_argument('--caso', action='append', help='só os casos com este id (repetível)')
    parser.add_argument('--pasta', default='/data/carga', help='onde gravar as imagens (o OCR lê dali)')
    parser.add_argument('--ocr', default='http://ocr:8001/sse')
    parser.add_argument('--rag', default='http://rag:8002/sse')
    parser.add_argument('--api', default='http://api:8000')
    parser.add_argument('--relatorio', help='grava cada caso (id, categoria, resultado, detalhe) neste JSON')
    args = parser.parse_args(argv)
    Path(args.pasta).mkdir(parents=True, exist_ok=True)
    casos = montar(args.variantes, args.pasta)
    if args.caso:
        casos = [caso for caso in casos if caso[0] in args.caso]
    print(f'{len(casos)} casos ({args.variantes} por categoria); {args.concorrencia} sessões em paralelo...', flush=True)
    resultados, segundos = asyncio.run(rodar(casos, args.ocr, args.rag, args.api, args.concorrencia,
                                             0 if args.caso else args.posts_simultaneos))
    if args.relatorio:
        Path(args.relatorio).write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding='utf-8')
    tabela, falhas = resumo(resultados, segundos, [caso[1] for caso in casos])
    print(tabela)
    return 1 if falhas else 0


if __name__ == '__main__':
    raise SystemExit(main())
