# Gravações

Um vídeo por parte do sistema, numa execução real e sem cortes (data e modelo no fim desta
página). As esperas longas (Gemini, `docker compose run`, `pytest`, a carga) aparecem aceleradas; nada é
cortado, e cada comando e cada resultado aparecem em velocidade normal. Um navegador headless
(Playwright) usa a aplicação de verdade: um terminal web para a CLI e o Swagger com cliques
reais. Não há slides nem narração, e nada é escrito sobre os vídeos de cada parte; só o 00 tem uma barra
de etapas no topo e notas curtas com os resultados que o próprio gravador conferiu na saída. O último frame de cada vídeo, usado como
miniatura, mostra a prova; no 00, a miniatura é o pedido manuscrito, o 1º frame. Cada execução que agenda roda
num terminal de verdade: a CLI mostra a lista e pergunta `Agendar estes N exames? [s/N]`, respondida "s" na
gravação, e só então chama a API; no 12, o console do ADK mostra a mesma lista em `[HITL confirm]` e espera "yes".

| Parte do sistema | Vídeo (clique na miniatura) | Duração | O que prova |
|---|---|---|---|
| Visão geral | [![00-visao-geral](00-visao-geral.png)](00-visao-geral.mp4) | 6:31 | Os 12 vídeos, do pedido manuscrito (10) ao `adk run` (12), na ordem 10, 01, 05, 11, 02, 06, 08, 03, 04, 09, 07 e 12, 1,4x mais rápidos, com uma barra de etapas no topo (a parte atual em destaque) e uma nota no começo de cada parte; as notas de resultado só repetem o que o gravador conferiu na saída real, com a linha da prova destacada, e cada pergunta da lista tem a sua nota; os 2 primeiros segundos (o pedido manuscrito, que é a miniatura) e os 2 últimos de cada parte (a prova) em velocidade normal |
| Transpilador | [![01-transpilador](01-transpilador.png)](01-transpilador.mp4) | 1:18 | JSON válido → `generated/agent.py` com Google ADK (o esqueleto do agente gerado na tela); campo extra e chave duplicada recebem erro claro |
| OCR via MCP (SSE) | [![02-ocr-mcp-sse](02-ocr-mcp-sse.png)](02-ocr-mcp-sse.mp4) | 0:20 | A ferramenta `extract_exam_text` lê `pedido.png` pelo SSE e devolve as linhas com a PII já mascarada |
| RAG via MCP (SSE) | [![03-rag-mcp-sse](03-rag-mcp-sse.png)](03-rag-mcp-sse.mp4) | 0:35 | `search_exams` acha o código com sinônimo e com erro de digitação; catálogo com 120 exames |
| API e Swagger | [![04-api-swagger](04-api-swagger.png)](04-api-swagger.mp4) | 0:17 | `/docs`: `POST /appointments` (Try it out → Execute → 201) e `GET` pelo id criado |
| Fluxo ponta a ponta | [![05-ponta-a-ponta](05-ponta-a-ponta.png)](05-ponta-a-ponta.mp4) | 0:36 | `run --image pedido.png` com Gemini: OCR → RAG → a lista dos 3 exames e a pergunta `Agendar estes 3 exames? [s/N]`, respondida "s" → API; tabela exame → código, confirmação e tempo de cada etapa. O total da linha `Tempo:` passa bem da soma das etapas: ver o fim desta página |
| Camada de PII | [![06-pii](06-pii.png)](06-pii.mp4) | 1:13 | Pedido realista: só placeholders (`[NOME]`, `[CPF]`…) no OCR e no `run`; no SQLite, a coluna de exames do agendamento está cifrada (na tela, o começo dela e o tamanho), e o `GET` devolve os exames decifrados. A lista perguntada tem 2 dos 4 exames (Glicemia de jejum e TSH), agendados depois do "s"; Colesterol total (0,68) e Hemoglobina glicada (0,60) aparecem nela em `Não agendados`, com `baixa confiança`, para conferência humana: é o comportamento conservador esperado. O `text_removed: 2` do OCR, num pedido legítimo, é o cabeçalho da clínica (`CLÍNICA FICTÍCIA HORIZONTE - DADOS FICTÍCIOS`), em 2 trechos: a rede de segurança tira do texto o que não parece exame nem estrutura do pedido |
| Docker e testes | [![07-docker-testes](07-docker-testes.png)](07-docker-testes.mp4) | 0:31 | `docker compose ps` com os serviços healthy e a suíte inteira no serviço `tests` (`19378 passed` na gravação; `exit=0` é o código de saída do próprio `pytest`), e o `ps` de novo no fim; o `1 skipped` é o teste ponta a ponta, que precisa da chave Gemini |
| Segurança contra injeção | [![08-seguranca](08-seguranca.png)](08-seguranca.mp4) | 1:34 | Os testes do corpus de injeção e de PII passam; nos pedidos com instruções escondidas (`ataque-injecao.png`, `ataque-exame-disfarcado.png`), o OCR conta as instruções (`instructions_removed`) e tira o texto delas (`[TEXTO_REMOVIDO]`), e a lista perguntada pelo `run` traz só os exames legítimos (3 e 4), todos marcados `confira`, porque a página tem texto além da lista (no 2º pedido, os 3 exames que dividiam a linha com uma instrução removida dizem também `o pedido tem outras palavras além do exame`); respondida "s", só eles são agendados; o cabeçalho (`Laboratorio Ficticio Beta`, `PEDIDO MEDICO FICTICIO`) também sai como `[TEXTO_REMOVIDO]`, não por ser instrução, mas pela rede de segurança que só deixa sair do OCR o que parece exame ou estrutura do pedido. Por isso a tela mostra um `[TEXTO_REMOVIDO]` a mais que o `instructions_removed` (5 contra 4 e 4 contra 3): `instructions_removed` conta só as instruções escondidas, e `text_removed` conta todos os trechos removidos, o título incluído |
| Dados sensíveis em carga | [![09-dados-sensiveis](09-dados-sensiveis.png)](09-dados-sensiveis.mp4) | 0:55 | A imagem de entrada de um pedido do gerador da carga (fictícia, com nome realista e CPF falso) e a saída do OCR para o mesmo pedido, com nome, CPF, RG, endereço, carteirinha e CRM mascarados; depois, a carga com 200 pedidos pelo OCR, RAG e API: 0 falhas do OCR, 0 vazamentos, os 200 agendamentos lidos de volta iguais ao enviado e nada em claro nos bytes do SQLite. A soma de `Mascarados pelo OCR` (2451) passa dos 2400 `Campos sensíveis impressos` porque a data do pedido também sai como `[DATA]` (DATA 400: nascimento e data do pedido), enquanto endereço e CEP na mesma linha saem num só `[ENDERECO]`. A tela também mostra os números parciais, perto de 99%: exames preservados no texto e códigos certos no RAG. Os poucos exames que o OCR não leu (uma letra ou sigla solta no fim do nome, como em "Troponina I") são listados pelo nome ([por quê](../medicoes.md#carga-de-dados-sensíveis)) |
| Pedido manuscrito | [![10-manuscrito](10-manuscrito.png)](10-manuscrito.mp4) | 0:24 | `run --image pedido-manuscrito.png` com Gemini (letra de mão simulada com fonte, não escrita real): a página tem texto além da lista (as linhas 13 a 15, tiradas pelo OCR: `Atenção: …; confira o papel`), então os 5 exames vão para a lista marcados `confira`, Triglicerídeos com a confiança de leitura 0,71; `Agendar estes 5 exames? [s/N]` respondido "s", e os 5 são agendados. A linha `[schedule] chamando create_appointment` aparece antes da pergunta porque a confirmação nativa do ADK pausa dentro dessa chamada: o `POST` só sai depois do "s" |
| Foto de celular | [![11-foto-celular](11-foto-celular.png)](11-foto-celular.mp4) | 0:24 | `run --image pedido-foto-celular.jpg` com Gemini: foto de celular simulada de um pedido impresso (perspectiva e sombra geradas por `tests/load/fotos.py`); os 5 exames escritos vão para a lista, `Agendar estes 5 exames? [s/N]` respondido "s", são agendados, e a PII sai mascarada |
| ADK sem a CLI | [![12-adk-run](12-adk-run.png)](12-adk-run.mp4) | 0:41 | `adk run --in_memory generated` com Gemini: o mesmo agente gerado, no console do próprio Google ADK, sem a CLI do projeto. Recebe `pedido.png`; os subagentes de leitura, busca e agendamento (`[extract]`, `[search]`, `[schedule]`) respondem um após o outro; antes da API, o `[schedule]` pausa (`Paused for input`) e o console mostra a lista em `[HITL confirm]`, com `Agendar estes 3 exames?` e `Type "yes" to confirm`; depois do "yes", o resumo final traz a PII mascarada e a confirmação da API com o id; `exit` encerra |

Os vídeos 01, 02, 03, 06 e 08 usam os arquivos de [`exemplos/`](../../exemplos/): o cliente MCP
`mcp_call.py`, que chama uma ferramenta pelo SSE e imprime a resposta, e duas specs com erro de
propósito (campo extra e chave duplicada). Comandos longos aparecem em mais de uma linha, com `\`
no fim da linha, como no shell, para nenhuma palavra ser cortada na borda do terminal. Pelo mesmo
motivo, os comandos sem pergunta com saída longa terminam em `| fold -s -w N`, que só quebra as linhas da
saída entre palavras. Os que perguntam precisam de um terminal de verdade, sem pipe (com a saída num pipe, o
`docker compose run` desliga o terminal e a CLI não pergunta); neles, o terminal web da gravação quebra as
linhas longas entre palavras, sem mudar o texto, e uma palavra no fim da linha passa inteira para a seguinte.

Gravado com `API_PORT=18905` (por isso a URL do Swagger mostra essa porta); o padrão é 8765. O 09
usa `-n 200` para a tabela inteira caber na tela (o comando de
[`tests/load/compose.carga.yml`](../../tests/load/compose.carga.yml) usa 500) e um override de rede
local (ver [Quando algo falha](../como-rodar.md#quando-algo-falha)), porque os pools de endereços
do Docker da máquina da gravação estavam esgotados; numa máquina comum ele não é necessário.

Formato: H.264/yuv420p, cada arquivo com menos de 10 MB, miniatura `.png` com o último frame em 1280x720; o 00 tem 1280x784, com a barra de etapas acima do vídeo.

Gravado em 2026-10-08, todos os vídeos e capturas no mesmo commit, `828ac9c` · esperas aceleradas pelo menos 16x; uma espera
que nem a 16x caberia em 5 s (o `pytest` do 07, a carga do 09 e os `run` com Gemini) passa mais rápido e
ocupa 5 s (o 04 não tem espera longa). Modelo: todos os vídeos com Gemini (05, 06, 08, 10, 11 e 12) rodaram
com o modelo principal da spec, `gemini-3.5-flash`. Os vídeos somam 7 execuções com Gemini
(o `run` ou o `adk run`), uma por pedido, sem tomada descartada; o [teste de alucinação](../evidencias/log-alucinacao.txt), outras 4. Cada vídeo de uma parte tem no máximo 1:34; o 00 junta todos em 6:31.

Na linha `Tempo:` dos vídeos com Gemini, as etapas (OCR, busca, agendamento) são as da execução que
terminou, e o total é o relógio do `run` inteiro: inclui os turnos do modelo entre as ferramentas. Por
isso o total passa bem da soma das etapas.
