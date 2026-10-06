"""The sample images and example specs the suite runs on: fixed lists, not whatever is in the folder.

The guides invite a user to drop an image in samples/ or a spec in specs/ to try it. Such a file must
not change the collected test count (the docs quote it) nor fail the suite, so the tests parametrized
over the shipped files read these lists. tests/test_versionados.py checks them against the repository.
"""
SAMPLE_IMAGES = ('ataque-exame-disfarcado.png', 'ataque-injecao.png', 'pedido-manuscrito-dificil.png',
                 'pedido-manuscrito.png', 'pedido-realista.png', 'pedido-sem-exame.png', 'pedido-variacao.png',
                 'pedido.png')
EXAMPLE_SPECS = ('agendar-variante.json', 'agent-sem-confirmacao.json', 'agent.json', 'listar-exames.json')
