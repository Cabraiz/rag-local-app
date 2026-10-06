"""Pedido fictício com dados do paciente e nenhum exame: samples/pedido-sem-exame.png.

É o caso (b) de docs/evidencias/log-alucinacao.txt: o modelo não pode inventar um exame para um pedido
que não tem nenhum. Vem do gerador da carga (tests/load/pedidos.py), com semente fixa e o 1º
layout, que mantém "Exames solicitados:" sem nada embaixo. Tudo é inventado: o CPF tem o dígito
verificador errado de propósito e o e-mail é .invalid.

Na raiz do repositório, com as fontes DejaVu (as da imagem de testes; sem elas, o Pillow usa a
fonte padrão e a imagem sai diferente):
    python exemplos/gerar_pedido_sem_exame.py samples/pedido-sem-exame.png
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script from the repository root

from tests.load import pedidos  # noqa: E402

SEED, LAYOUT = 20261007, 0


def desenhar():
    """(imagem, valores sensíveis) do pedido sem exame; a mesma semente dá a mesma imagem."""
    rng = random.Random(SEED)
    sensiveis = pedidos.dados(rng)
    texto = pedidos.linhas(rng, sensiveis, [], LAYOUT)
    return pedidos.imagem(rng, texto, LAYOUT), sensiveis


if __name__ == '__main__':
    saida = Path(sys.argv[1] if len(sys.argv) > 1 else 'samples/pedido-sem-exame.png')
    imagem, _ = desenhar()
    imagem.save(saida)
    print(f'{saida}: pedido sem exame, {imagem.width}x{imagem.height}')
