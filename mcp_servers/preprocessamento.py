"""Foto ruim e letra de mão: preparo só com Pillow e uma passada do Tesseract com confiança.

Sem LLM: a imagem bruta não sai do processo do OCR (a PII ainda não foi mascarada).
1. Preparo: tons de cinza, luz desigual achatada (fundo estimado por um máximo local e
   subtraído), contraste esticado e endireitamento de até 6 graus (variância da projeção
   das linhas). Sem ampliar: em 2x o Tesseract leu pior a letra de mão e ficou 3x mais lento.
2. Uma passada em PSM 11 (texto esparso): acha palavras soltas, como as de um pedido
   escrito à mão, que o PSM 3 descartava. As palavras voltam a formar linhas pela altura,
   e um rótulo sem valor ("Paciente:") leva a linha de baixo, para a máscara de PII ver o
   rótulo e o nome juntos.

Medido em 185 pedidos (5 de samples/, 60 da carga, 120 manuscritos): exames achados no
texto 45% -> 53% (manuscritos 23% -> 34%), latência 1,6x a do PSM 3 sem preparo.
tessdata_best e por+eng não ajudaram o bastante para valer o custo.

Cada linha devolvida é um `Linha` (um str) com `.confianca` (0 a 100, média do conf do
Tesseract nas suas palavras), que o agente usa para não agendar sozinho um exame lido com
pouca confiança.
"""
import re
from statistics import fmean, median

import pytesseract
from PIL import Image, ImageChops, ImageFilter, ImageOps

from guardrails.injection import join_split_orders

LANG = 'por'
CONFIG = '--oem 1 --psm 11'
ANGULO_MAXIMO, PASSO_GROSSO, PASSO_FINO = 6.0, 1.0, 0.2
PONTUACAO_COLADA = set(':;,.)!?')
ITEM_DE_LISTA = re.compile(r'\s*(?:\d+\s*[.)]|[-–•*])')  # o agente busca cada item no catálogo
ALTURA_DO_TRACO = 0.35  # traço medido: 6% a 26% da altura das letras; letra minúscula, 50% ou mais
NUMERO_COM_VIRGULA = re.compile(r'\d{1,2},')
CONFIANTE = 70         # conf do Tesseract a partir da qual uma palavra conta como bem lida
POBRE_PALAVRAS, POBRE_FRACAO = 8, 0.5  # pedido.png de pé: 32 de 35; de lado: 0; de cabeça para baixo: 7 de 33
OSD_MINIMA = 2.0       # orientation_conf do OSD: de 2,5 (letra de médico) a 15 (foto impressa) nos testes
GANHO_GIRADA = 2       # a girada precisa ler o dobro das palavras confiantes (e ao menos POBRE_PALAVRAS)
SOBREPOSICAO = 0.5  # fração da altura da menor (palavra ou linha) que as duas precisam dividir


class Linha(str):
    """Uma linha lida, com a confiança média do Tesseract nas suas palavras (0 a 100)."""
    confianca: float

    def __new__(cls, texto, confianca):
        linha = super().__new__(cls, texto)
        linha.confianca = round(float(confianca), 1)
        return linha


def preparar(imagem):
    """Imagem em tons de cinza, com a luz achatada, o contraste esticado e reta."""
    cinza = achatar_luz(ImageOps.exif_transpose(imagem).convert('L'))
    cinza = ImageOps.autocontrast(cinza, cutoff=1)
    angulo = inclinacao(cinza)
    if angulo:
        cinza = cinza.rotate(angulo, resample=Image.BICUBIC, expand=True, fillcolor=255)
    return cinza


def achatar_luz(cinza):
    """Tira sombra e luz desigual: o fundo (papel) é o máximo local numa versão reduzida."""
    pequeno = cinza.resize((max(1, cinza.width // 8), max(1, cinza.height // 8)), Image.BOX)
    fundo = pequeno.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.BoxBlur(2)).resize(cinza.size, Image.BILINEAR)
    return ImageChops.invert(ImageChops.subtract(fundo, cinza))  # 255 - (fundo - pixel): papel branco por igual


def inclinacao(cinza):
    """Ângulo (graus) que deixa as linhas de texto horizontais: o de maior variância na projeção."""
    escala = 600 / max(cinza.size)
    pequeno = ImageOps.invert(cinza.resize((max(1, round(cinza.width * escala)), max(1, round(cinza.height * escala)))))

    def nitidez(angulo):
        girado = pequeno.rotate(angulo, resample=Image.BILINEAR, fillcolor=0)
        linhas = list(girado.resize((1, girado.height), Image.BOX).tobytes())  # modo L: um byte por linha
        media = fmean(linhas)
        return fmean((valor - media) ** 2 for valor in linhas)

    passos = round(ANGULO_MAXIMO / PASSO_GROSSO)
    melhor = max((nitidez(a * PASSO_GROSSO), a * PASSO_GROSSO) for a in range(-passos, passos + 1))[1]
    finos = [melhor + k * PASSO_FINO for k in range(-4, 5)]
    melhor = max((nitidez(a), a) for a in finos)[1]
    return round(melhor, 1) if abs(melhor) >= 0.3 else 0.0


def palavras(imagem, timeout, lang=LANG, config=CONFIG):
    """[(esquerda, topo, largura, altura, texto, confiança)] de cada palavra que o Tesseract leu."""
    dados = pytesseract.image_to_data(imagem, lang=lang, config=config, output_type=pytesseract.Output.DICT,
                                      timeout=timeout)
    lidas = []
    for i, texto in enumerate(dados['text']):
        conf = float(dados['conf'][i])
        if texto.strip() and conf >= 0:
            caixa = (dados['left'][i], dados['top'][i], dados['width'][i], dados['height'][i])
            lidas.append((*caixa, texto.strip(), conf))
    return lidas


def juntar_por_altura(lidas):
    """Agrupa em linhas as palavras que dividem ao menos metade da altura com uma linha já aberta.

    A faixa da linha cresce com cada palavra: letra de mão sobe e desce ao longo do nome.
    Depois, um rótulo sem valor ("Paciente:") leva a linha logo abaixo, onde o nome escrito
    à mão costuma cair, para a máscara de PII ver os dois juntos.
    """
    linhas = []
    for palavra in sorted(lidas, key=lambda p: p[1] + p[3] / 2):
        topo, base = palavra[1], palavra[1] + palavra[3]
        for linha in linhas:
            comum = min(linha['base'], base) - max(linha['topo'], topo)
            if comum >= SOBREPOSICAO * min(palavra[3], linha['base'] - linha['topo']):
                linha['palavras'].append(palavra)
                linha['topo'], linha['base'] = min(linha['topo'], topo), max(linha['base'], base)
                break
        else:
            linhas.append({'topo': topo, 'base': base, 'palavras': [palavra]})
    juntas = []
    for linha in sorted(linhas, key=lambda item: item['topo']):
        linha['palavras'].sort()  # da esquerda para a direita
        anterior = juntas[-1] if juntas else None
        if (anterior and juntar_texto(anterior['palavras']).endswith(':')
                and not ITEM_DE_LISTA.match(juntar_texto(linha['palavras']))  # "1. Hemograma" segue sozinho
                and linha['topo'] - anterior['base'] <= anterior['base'] - anterior['topo']):
            anterior['palavras'] += linha['palavras']  # o rótulo e depois a linha de baixo, nessa ordem
            anterior['base'] = max(anterior['base'], linha['base'])
            continue
        juntas.append(linha)
    return [linha['palavras'] for linha in juntas]


def consertar_marcador(linha):
    """A linha com o marcador de lista consertado.

    O PSM 11 lê o traço como "E", "=" ou "�" (uma caixa baixinha, bem menor que as letras) e
    "4." como "4,"; sem o marcador, a linha deixa de parecer um item do pedido.
    """
    if len(linha) >= 2:
        primeira, altura = linha[0], median(p[3] for p in linha[1:])
        if len(primeira[4]) <= 2 and primeira[3] < ALTURA_DO_TRACO * altura:
            return [(*primeira[:4], '-', primeira[5]), *linha[1:]]
        if NUMERO_COM_VIRGULA.fullmatch(primeira[4]):
            return [(*primeira[:4], primeira[4][:-1] + '.', primeira[5]), *linha[1:]]
    return linha


def juntar_texto(linha):
    """Texto da linha, com o marcador consertado e a pontuação que o PSM 11 lê solta (":", ",")
    colada de volta na palavra anterior."""
    texto = ''
    for *_, palavra, _conf in consertar_marcador(linha):
        texto += palavra if texto and all(c in PONTUACAO_COLADA for c in palavra) else f' {palavra}'
    return texto.strip()


def confianca(linha):
    """Média do conf do Tesseract nas palavras, sem o marcador de lista: o traço ou o "3." quase
    sempre sai com conf baixo e derrubava abaixo de 80 um exame impresso lido com certeza."""
    linha = consertar_marcador(linha)
    if len(linha) >= 2 and ITEM_DE_LISTA.fullmatch(linha[0][4]):
        linha = linha[1:]
    return fmean(p[5] for p in linha)


class ImagemGirada(ValueError):
    """A página está de lado ou de cabeça para baixo e não deu para ler nem depois de girar."""


def sobre_branco(imagem):
    """Uma imagem com transparência (RGBA, LA, PA ou paleta com cor transparente) sobre papel branco.

    Sem isso, a conversão para cinza descarta o alfa e o fundo transparente (que guarda o preto
    por baixo) vira uma página preta: "foto escura demais". Imagem sem alfa volta como está.
    """
    if imagem.mode not in ('RGBA', 'LA', 'PA') and 'transparency' not in imagem.info:
        return imagem
    imagem = ImageOps.exif_transpose(imagem)  # a composição perde o EXIF: aplica a orientação antes
    return Image.alpha_composite(Image.new('RGBA', imagem.size, 'white'), imagem.convert('RGBA')).convert('RGB')


def confiantes(lidas):
    """Quantas palavras o Tesseract leu com conf >= CONFIANTE: o placar de uma leitura."""
    return sum(p[5] >= CONFIANTE for p in lidas)


def leitura_pobre(lidas):
    """Poucas palavras confiantes, ou poucas em relação às lidas: página vazia, de lado ou invertida."""
    return confiantes(lidas) < POBRE_PALAVRAS or confiantes(lidas) < POBRE_FRACAO * len(lidas)


def rotacao(imagem, timeout):
    """Graus (90, 180, 270) que o OSD do Tesseract manda girar no sentido horário, ou 0 se não souber."""
    try:
        osd = pytesseract.image_to_osd(imagem, config='--psm 0', output_type=pytesseract.Output.DICT,
                                       timeout=timeout)
    except RuntimeError:  # pouco texto para decidir, ou sem tempo: fica com a leitura de pé
        return 0
    return osd['rotate'] if osd['orientation_conf'] >= OSD_MINIMA else 0


def ler_linhas(imagem, timeout, preparo=True, lang=LANG, config=CONFIG):
    """Linhas não vazias, de cima para baixo, cada uma com a sua confiança (Linha).

    Uma página girada nos pixels (sem EXIF) sai vazia ou em lixo. Só quando a leitura vem pobre,
    o OSD diz se a página está de lado; a versão girada só substitui a outra se ler muito mais
    (GANHO_GIRADA vezes as palavras confiantes). Se o OSD vê a página girada e nenhuma das duas
    leituras serve, ImagemGirada: melhor pedir outra foto que devolver nada sem explicar.
    """
    imagem = preparar(imagem) if preparo else ImageOps.exif_transpose(imagem).convert('L')
    lidas = palavras(imagem, timeout, lang, config)
    if leitura_pobre(lidas) and (graus := rotacao(imagem, timeout)):
        girada = palavras(imagem.rotate(-graus, expand=True, fillcolor=255), timeout, lang, config)
        if confiantes(girada) >= max(POBRE_PALAVRAS, GANHO_GIRADA * confiantes(lidas)):
            lidas = girada
        elif confiantes(lidas) < POBRE_PALAVRAS:
            raise ImagemGirada('imagem de lado ou de cabeça para baixo: gire e envie de novo')
    return [Linha(juntar_texto(linha), confianca(linha)) for linha in juntar_por_altura(lidas)]


def confianca_por_linha(lidas, minimo=0.0, origens=None):
    """A confiança de cada linha que mask_lines devolve, na mesma ordem.

    As linhas que join_split_orders junta numa só (uma ordem ao modelo quebrada em várias) são as
    mesmas que neutralize_joined julga juntas; a linha juntada fica com a menor das
    confianças. Linha sem confiança (lida sem esta etapa) conta como `minimo`. `origens` é o
    join_split_orders(lidas)[1] que quem chama já tem (o OCR junta uma vez por página).
    """
    if origens is None:
        origens = join_split_orders(lidas)[1]
    return [min(getattr(lidas[i], 'confianca', minimo) for i in origem) for origem in origens]
