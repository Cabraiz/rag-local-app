# Decisões, custos e limites

O que tem aqui: a versão longa das seções "Decisões e trade-offs" e "Limites conhecidos" do
[README](../README.md). Cada decisão diz a escolha, o porquê e o custo; cada limite diz o que não
funciona e o que o contorna.

- Números: [medicoes.md](medicoes.md). Só o do keep-alive foi medido nesta página.
- Funcionamento: [arquitetura.md](arquitetura.md).
- Regras de página e de linha: [regras.md](regras.md).
- Termos do projeto (spec, `--yes`, `BookingPlugin`, faixas de confiança): [glossário](glossario.md).

**Índice**

- **[Decisões](#decisões):**
  - [O modelo propõe, o código decide, a pessoa confirma](#o-modelo-propõe-o-código-decide-a-pessoa-confirma)
  - [A confirmação final da lista](#a-confirmação-final-da-lista)
  - [Por que existe uma camada de regras](#por-que-existe-uma-camada-de-regras)
  - [Filtros determinísticos dentro do OCR, antes de qualquer modelo](#filtros-determinísticos-dentro-do-ocr-antes-de-qualquer-modelo)
  - [PII mascarada na origem, banco sem PII](#pii-mascarada-na-origem-banco-sem-pii)
  - [Busca lexical, sem embeddings](#busca-lexical-sem-embeddings)
  - [MCP só via SSE, rede interna, containers endurecidos](#mcp-só-via-sse-rede-interna-containers-endurecidos)
  - [Transpilador estrito e genérico](#transpilador-estrito-e-genérico)
  - [O `agent.py` usa só classes do ADK; a política mora num plugin versionado](#o-agentpy-usa-só-classes-do-adk-a-política-mora-num-plugin-versionado)
  - [A resposta do OCR é um contrato versionado](#a-resposta-do-ocr-é-um-contrato-versionado)
  - [`SequentialAgent`, obsoleto no ADK 2.10](#sequentialagent-obsoleto-no-adk-210)
  - [OCR: Tesseract com preparo](#ocr-tesseract-com-preparo)
  - [Pedido em papel, em produção](#pedido-em-papel-em-produção)
  - [API: contrato, idempotência e defesas](#api-contrato-idempotência-e-defesas)
  - [Keep-alive de 75 s](#keep-alive-de-75-s)
  - [Um `Dockerfile` multi-stage](#um-dockerfile-multi-stage)
  - [Escala](#escala)
- **[Limites](#limites):**
  - [Com `--yes`, um exame injetado como linha comum é agendado](#com---yes-um-exame-injetado-como-linha-comum-é-agendado)
  - [A pergunta mostra a linha mascarada](#a-pergunta-mostra-a-linha-mascarada)
  - [Exame lido certo, mas com leitura fraca, fica fora da pergunta](#exame-lido-certo-mas-com-leitura-fraca-fica-fora-da-pergunta)
  - [Filtros determinísticos não são prova contra ataques novos](#filtros-determinísticos-não-são-prova-contra-ataques-novos)
  - [Sem autenticação: na API e entre serviços](#sem-autenticação-na-api-e-entre-serviços)
  - [Cadeia de suprimentos, em parte](#cadeia-de-suprimentos-em-parte)
  - [Confirmação forjada e pedido repetido](#confirmação-forjada-e-pedido-repetido)

## Decisões

### O modelo propõe, o código decide, a pessoa confirma

**Quem faz o quê.** Na [spec](glossario.md#agente-spec-e-serviços) padrão (`specs/agent.json`), o Gemini só propõe,
em três etapas ([quais](arquitetura.md#etapas)). O código, em [`runtime/callbacks.py`](../runtime/callbacks.py), confere
cada proposta e decide o que agenda, o que vai para a pergunta e o que fica de fora
([as checagens](arquitetura.md#por-que-o-código-decide-o-agendamento)). A pessoa confirma a lista.

Erro de OCR, abreviação e sinônimo ficam com a busca (`difflib` e os sinônimos do catálogo). Uma correção do modelo
não sobe a confiança, que é medida contra a linha lida.

**Por quê.** Num agendamento de saúde, a decisão precisa ser reprodutível e auditável linha a linha, e
uma ordem escrita na imagem não pode valer por ter convencido o modelo.

**Custo.** Cada etapa soma turnos do modelo ao tempo do pedido. A linha `Tempo:` da CLI mostra quanto.

**O que o modelo recebe.** Um agente fica perigoso quando junta três coisas: lê conteúdo não
confiável, tem dados privados e pode mandar dados para fora. Aqui:

- do pedido, o modelo recebe só as linhas de exame, já mascaradas
  ([o quê, e o que ainda passa](referencia.md#onde-a-pii-é-mascarada));
- a única ação é agendar códigos do catálogo achados no pedido, em hosts fixos. OCR e RAG não têm
  e-mail nem internet.

Por isso o risco principal aqui é a integridade do agendamento, mais do que o vazamento.

**As faixas de confiança.** O código, não o modelo, decide pela confiança de cada exame, em três faixas: entra na
lista sem aviso, entra com aviso ou fica de fora ([os limites de cada uma](glossario.md#faixas-e-pisos)).

**Custo.** Um exame lido certo, mas com leitura fraca, fica de fora e precisa ser agendado à mão
([detalhe](referencia.md#agendamento-conferido-em-código)).

### A confirmação final da lista

Antes da API, a pessoa vê a lista inteira, com os avisos e os exames que ficam de fora, e responde
`Agendar estes N exames? [s/N]` (padrão: não).

A pergunta é a confirmação nativa de ferramenta do ADK, pedida pelo callback e não pelo modelo. Ela funciona igual na
CLI, no `adk run` e no `adk web` ([como](transpilador.md#a-confirmação-da-lista-confirmação-nativa-do-adk)).

**Por quê.** Regras de página não enxergam todo layout: um X marcando só alguns exames num formulário
impresso, um carimbo "CANCELADO" ou uma nota girada que o OCR não lê, uma nota abaixo da assinatura.
Quem confirma é a pessoa.

**Custo.** Um passo a mais em todo pedido. A confirmação de ferramenta e o `ResumabilityConfig` são
experimentais no ADK 2.10, fixado no `requirements.txt`.

Com [`--yes`](glossario.md#agente-spec-e-serviços) não há pergunta: é escolha e risco de quem opera
([o que passa](#com---yes-um-exame-injetado-como-linha-comum-é-agendado)).

### Por que existe uma camada de regras

A imagem é entrada não confiável e o pedido é médico. As regras determinísticas
([mapa das 22 regras](regras.md)) existem para que o modelo nunca seja a última barreira:

- **sempre:** uma negação clara ("não realizar Ferritina", "já realizado", um bloco "Já realizados:")
  ou uma linha de preparo não agenda; o exame é avisado com o motivo;
- **com `--yes`:** um exame só agenda sozinho se a página inteira for só a lista de exames
  ([o que conta](regras.md#regras-que-só-decidem-com---yes)). Qualquer outro texto lido faz todo exame da página ser
  perguntado.

**Por que "só a lista", e não uma lista de negações.** A primeira versão procurava palavras de negação. Até
2026-10-08, quando entrou a [regra por página](medicoes.md#lista-branca-por-página), três revisões acharam o que ela não
cobria: "somente se", "adiar para a próxima consulta", "Trazer os laudos de:", "desconsiderar o
2º", outra língua, uma 2ª folha. Exigir que a página seja só a lista falha para o lado de perguntar.

**Custo.** Páginas honestas com uma observação, ou com erros de OCR na letra de mão, perguntam tudo
([medição](medicoes.md#lista-branca-por-página)). Sem `--yes`, as regras só decidem o aviso ao lado de
cada exame, e a pessoa é a barreira final.

### Filtros determinísticos dentro do OCR, antes de qualquer modelo

A máscara de PII e a remoção de injeção de prompt rodam no container `ocr`, antes de qualquer modelo
([o que cada uma faz](arquitetura.md#por-que-a-pii-não-chega-ao-modelo)).

**Por quê.** A falha fica local, reproduzível e testável, sem depender do prompt.

**Custo.** Manter as regras, com corpora de regressão ([números](referencia.md#segurança-em-detalhe)).

### PII mascarada na origem, banco sem PII

Nome, CPF, telefone, e-mail e os outros tipos viram `[NOME]`, `[CPF]`… no container do OCR. A API só
aceita código e nome de exame, grava o nome do catálogo e cifra a lista com AES-256-GCM.

**Por quê.** O LLM recebe o texto já mascarado, nunca a imagem, e o banco não depende da máscara.

**Custo.** A detecção é por regras e vale para os formatos testados: um nome escrito de um jeito não
testado pode chegar ao modelo ([camadas](referencia.md#segurança-em-detalhe)).

**A cifra.** AES-GCM foi escolhido no lugar do Fernet porque prende cada lista aos campos em claro da sua linha. Esses
campos entram como dado associado: alterar um deles faz a leitura falhar. Código:
[`api/crypto.py`](../api/crypto.py), `encrypt(..., bound_to)`.

- A chave fica num volume próprio (`api-key`), fora do git e fora do volume do banco (`api-data`). Assim, uma cópia do
  banco sozinha não revela os exames. E o primeiro `up` não tem passo manual de chave.
- Em uso real, a chave viria de um gerenciador de segredos, por `DB_ENCRYPTION_KEY`.
- **Custos e limites:**
  - quem tem os dois volumes, ou o host do Docker, lê tudo. A API decifra para responder ao `GET`;
  - `id`, status e datas ficam em claro, e a rotação de chave está fora do escopo;
  - `docker compose down -v` apaga a chave e o banco juntos. Uma cópia antiga do banco fica ilegível sem aquela chave;
  - uma chave válida, mas diferente da que cifrou o banco, só aparece na primeira leitura de um agendamento (500), não
    ao subir a API. Uma chave malformada para a API já na subida. Não há registro-canário, por simplicidade;
  - o tamanho do valor cifrado acompanha o texto, então revela aproximadamente quantos exames o agendamento tem.

### Busca lexical, sem embeddings

Palavras em comum mais `difflib`, só com a biblioteca padrão.

- **Por quê:** a base tem 120 exames fictícios. A busca é determinística e explicável: cada resultado diz o termo que
  deu o score. Ela tolera erros de OCR.
- **Por que não a semântica:** medida em 2026-10-07 na calibração, nas paráfrases e nas linhas do OCR, ela não agendou
  nenhum exame a mais ([medição](medicoes.md#busca-semântica-avaliada-não-adotada)).
- **Custo:** não entende paráfrases.

### MCP só via SSE, rede interna, containers endurecidos

OCR e RAG não têm internet nem porta no host. Os containers rodam sem root, com sistema de arquivos
somente leitura e sem capabilities; só a API publica porta, em `127.0.0.1`. Por isso a API tem internet: uma porta
publicada exige uma rede não interna.

- **Por quê:** o escopo do estudo fixa SSE, e cada serviço só tem o que usa.
- **Custo:** a especificação 2025-03-26 do MCP trocou o SSE pelo Streamable HTTP. O ADK já tem
  `StreamableHTTPConnectionParams`. Migrar é trocar o `SseConnectionParams` em
  [`transpiler/generator.py`](../transpiler/generator.py) e o `sse_app` em
  [`mcp_servers/arguments.py`](../mcp_servers/arguments.py) pelo app Streamable HTTP do SDK. Depois, rode
  `tests/test_mcp_sse.py` e `tests/test_agent_mcp.py`.

### Transpilador estrito e genérico

- **Estrito:**
  - Pydantic com campos extras proibidos e tipos estritos;
  - os valores da spec entram por `repr()`, nunca como código;
  - o arquivo é compilado e importado antes do OK;
  - cada servidor que responde confirma as ferramentas. Um servidor fora do ar no `transpile` só é conferido no `run`.

  **Por quê:** o erro aparece no `transpile`, com campo e motivo, não no meio de um agendamento
  ([validação](transpilador.md#validação-e-mensagens-de-erro)).
- **Genérico:** a spec descreve construções do ADK ([quais](transpilador.md#campos-da-spec)). O domínio fica no
  [`BookingPlugin`](transpilador.md#o-bookingplugin-quem-lê-quem-busca-e-quem-agenda), que confere as próprias regras
  no `transpile`. O transpilador não importa nada dele.
- **Custo:** plugins só dos pacotes `runtime` e `google.adk.plugins`, e o `cli run` só roda specs com o
  `BookingPlugin`.

### O `agent.py` usa só classes do ADK; a política mora num plugin versionado

O `agent.py` gerado ([exemplo](exemplo-agent.py)) instancia só classes do Google ADK. Algumas são subclasses finas,
vindas de `runtime/` ([quais](referencia.md#as-regras-que-o-agentpy-importa)). A política de agendamento é o
`BookingPlugin`, um `BasePlugin` do ADK declarado na spec. Ele roda sozinho com `adk run` e `adk web`, sem a CLI.

**Por que uma biblioteca e não a política copiada em cada arquivo.** A regra é testada uma vez, para
toda spec. A interface é declarada e versionada: um arquivo gerado para outra versão para já na importação, com
mensagem clara ([como](transpilador.md#o-que-fica-onde)).
[`test_runtime.py`](../tests/test_runtime.py) prova isso:

- copia o `agent.py`, o `runtime/`, o `catalogo.py` e o `leitura.py` para uma pasta fora do repositório;
- num interpretador limpo, importa o agente;
- chama os callbacks com respostas simuladas do OCR e da busca.

**Custo.** O arquivo gerado depende de `runtime/`, `catalogo.py` e `leitura.py` ao lado dele.

- O `adk run generated` na raiz do projeto os encontra. No container, `PYTHONPATH=/app` faz o mesmo.
- Não é um pacote instalável pelo pip.
- Uma spec sem o plugin ([exemplo fora do domínio](../specs/exemplo-generico.json)) importa só os toolsets e o modelo.

**O que muda fora da CLI:** [a lista](referencia.md#fora-da-cli-adk-run-e-adk-web). A conferência do pedido inteiro
roda no laço de eventos do ADK: [como](transpilador.md#a-confirmação-da-lista-confirmação-nativa-do-adk).

### A resposta do OCR é um contrato versionado

[`leitura.py`](../leitura.py) (`OcrReading`, com `version`): o servidor OCR a monta, e o runtime a
valida uma vez e lê campos tipados. Uma resposta fora do contrato não é lida: nada dela chega ao modelo,
nada é agendado nem perguntado. A ferramenta continua declarando um objeto simples, porque o ADK copia o
`outputSchema` de uma ferramenta MCP para a declaração que o modelo lê.

**Por que a leitura de cada linha fica no servidor OCR** ([`line_intent`, `contested_exams`,
`page_clean`](glossario.md#resposta-do-ocr)). Ela precisa do que só ele tem: o texto antes da máscara, a geometria das
linhas e a confiança de cada uma. Levá-la para o runtime faria esses dados, com a PII ainda não mascarada, saírem do
container.

**Custo.** Parte da regra de agendamento roda em outro processo. Um tipo de linha novo pede uma versão nova do contrato,
nos dois lados.

### `SequentialAgent`, obsoleto no ADK 2.10

Ordem fixa `extract` → `search` → `schedule`, porque cada etapa depende da anterior.

- **Por quê:** o ADK 2.10 marca o `SequentialAgent` como obsoleto e indica o `Workflow`. O `Workflow` ainda não é um
  `BaseAgent`, e a spec descreve agentes.
- **Custo:**
  - não há replanejamento: um exame fora do catálogo fica fora do agendamento;
  - um aviso de depreciação;
  - uma migração futura que mexe também na CLI. A CLI trata o agente raiz como um `BaseAgent`: no `transpile`, lê os
    `sub_agents` dele; no `run`, entrega o agente raiz ao `App`.

### OCR: Tesseract com preparo

A imagem passa por um preparo antes do Tesseract, e cada linha volta com a sua confiança (`line_confidence`)
([os passos](referencia.md#o-preparo-da-imagem)).

- **Ganho:** mais exames achados no texto, ao custo de mais latência ([números](medicoes.md#pedidos-manuscritos-simulados)).
- **Custo:** letra de médico continua quase ilegível ([medição](medicoes.md#limites-conhecidos)).

Próximo passo avaliado: um OCR moderno nas linhas de baixa confiança, adiado pelo custo em imagem, memória e tempo
de CPU ([medição](medicoes.md#ocr-moderno-avaliado-não-adotado)).

### Pedido em papel, em produção

Primeiro, receber a receita estruturada (eletrônica), sem OCR: o papel fica como exceção.

- No papel, para letra de médico, um modelo de visão:
  - em GPU local, ou em nuvem sob contrato de operador de dados, porque dado de saúde é sensível na LGPD;
  - aceito só quando a transcrição livre e a escolha no catálogo concordam;
  - o que não é certo vai para uma pessoa conferir.
- **Por quê:** medido em 2026-10-07, o modelo de visão local só não errou quando a transcrição livre e a escolha no
  catálogo concordaram ([medição](medicoes.md#letra-de-médico-leitor-local-avaliado-não-ligado-por-padrão)).
- **Custo:** GPU ou contrato, e revisão humana dos pedidos duvidosos.

### API: contrato, idempotência e defesas

Cada defesa com os valores, os cabeçalhos e os campos do log:
[referencia.md](referencia.md#a-api-idempotência-host-limite-cabeçalhos-e-log).

- **Contrato lido do `/openapi.json` vivo** (`OpenAPIToolset`).
  - **Por quê:** o agente usa exatamente o contrato do Swagger.
  - **Custo:** a API precisa estar no ar quando o agente monta as ferramentas.
- **`Idempotency-Key` vinda do runtime, nunca do modelo.**
  - **Por quê:** uma chamada que o modelo repete não agenda em dobro.
  - **Custo:** sem a chave, dois `POST` iguais criam dois agendamentos.
- **A chave guardada só como HMAC.**
  - **Por quê:** ela vem do cliente e pode carregar dado pessoal.
- **`Host` conferido, escrito à mão.**
  - **Por quê:** escutar só em `127.0.0.1` não basta contra DNS rebinding, porque o navegador manda `Host: evil.example`.
  - À mão, por dois motivos. O middleware de hosts do Starlette lê a lista quando é montado, antes de o `lifespan`
    ler as variáveis. E esse middleware responde em texto puro, não no JSON da API.
- **Limite por IP, em memória.**
  - **Por quê:** defesa em profundidade, não controle de acesso. Os ids já são uuid4, e não há listagem.
  - **Custo:** o estado é por processo. Com várias réplicas, cada uma conta o seu. Atrás de um proxy ou da porta publicada
    do Docker, os clientes tendem a parecer um só.
- **Log sem corpo, sem chave e com `request_id` próprio.**
  - **Por quê:** o corpo, a chave e o `request_id` do cliente podem ter dado pessoal.
- **CSP estrita só nas rotas JSON.**
  - **Custo:** `/docs` e `/redoc` ficam sem ela, porque carregam scripts do `cdn.jsdelivr.net` e um script inline
    ([cadeia de suprimentos](#cadeia-de-suprimentos-em-parte)).

### Keep-alive de 75 s

OCR, RAG e API mantêm uma conexão ociosa por 75 s, e não pelos 5 s padrão do uvicorn. O pool do cliente (httpx)
fecha a sua em 5 s, então o servidor nunca fecha primeiro. Com os dois em 5 s, um POST enviado no instante do
fechamento se perdia, e a chamada MCP esperava para sempre
([python-sdk#906](https://github.com/modelcontextprotocol/python-sdk/issues/906)).

Medido em 2026-10-06, ao trocar o prazo, com chamadas espaçadas de 5 s: com o prazo de 5 s, 15 travamentos em 2.880
chamadas; com 75 s, nenhum.

**Custo.** Cada servidor segura conexões ociosas por mais tempo.

### Um `Dockerfile` multi-stage

Cada serviço instala só o que precisa, com versões fixadas em `requirements.txt` (o que roda) e
`requirements-dev.txt` (testes e checagens). O `agent` leva só o que `transpile` e `run` usam. O Tesseract
fica só nas imagens `ocr` e `test`, e pytest, ruff, mypy e os testes, só na `test`.

**Por quê.** Cada imagem leva só o que o seu serviço usa: menos pacotes para atualizar e para atacar.

**Custo.** O primeiro build monta quatro imagens, e cinco com a de testes.

### Escala

O agente não guarda estado entre execuções. OCR e RAG são serviços separados e sem estado. O SQLite basta para a API
mock do estudo.
Para escalar: Postgres no lugar do SQLite e mais réplicas do `agent` e da `api`.

**Custo, hoje.** O SQLite aceita um escritor por vez, então há uma réplica da `api`. O limite de requisições por
cliente vale por processo ([API](#api-contrato-idempotência-e-defesas)).

## Limites

### Com `--yes`, um exame injetado como linha comum é agendado

Sem `--yes`, a pessoa vê a lista inteira e confirma.

- Com ele, as regras são a única barreira.
- Numa página que é só a lista, uma linha que só diz "Ferritina" é indistinguível de um pedido real e agenda sozinha.
- Qualquer texto além da lista faz a página inteira ser perguntada, inclusive numa página legítima
  ([o que conta como lista](regras.md#regras-que-só-decidem-com---yes), [medição](medicoes.md#lista-branca-por-página)).

**O que ainda passa, com `--yes`.** A regra só vê o texto que o Tesseract lê.

- Um carimbo, uma nota na vertical ou em diagonal, um risco à mão ou uma letra clara demais deixam o exame como linha
  comum.
- Uma letra cinza que o Tesseract lê, menor ou mais clara que a da página, é pega pela regra.
- Detectar tinta fora das palavras lidas (cor saturada ou tinta escura) pegaria carimbos. Foi avaliado em 2026-10-08 e
  não foi ligado: dispara em quase toda foto ([medição](medicoes.md#rodapé-formulário-nome-e-nome-mais-longo)).
- Os outros casos medidos que ainda passam: [limites conhecidos](medicoes.md#limites-conhecidos). As regras de página:
  [regras.md](regras.md).

### A pergunta mostra a linha mascarada

Em "- Ferritina - pedido por engano", a máscara tira "pedido por engano" antes do modelo e da CLI; a
lista da confirmação diz `; o pedido tem outras palavras além do exame` e mostra
`lido "- Ferritina - [TEXTO_REMOVIDO]"`. Quem responde confere o papel.

### Exame lido certo, mas com leitura fraca, fica fora da pergunta

Com confiança abaixo de 0,70 ([as faixas](glossario.md#faixas-e-pisos)), o exame não entra na pergunta: é avisado em
`baixa confiança` e não é agendado.

- Exemplo: 2 dos 4 exames do `samples/pedido-realista.png` ([como aparece](como-rodar.md#testar-outra-imagem)).
- É conservador de propósito: com uma imagem nova, o resultado pode ser um agendamento parcial com avisos.
- O que fazer: conferir o papel. Se o pedido traz o exame, agende-o à parte pelo `POST /appointments` do Swagger
  ([avisos](referencia.md#avisos-sobre-os-exames-de-um-pedido)).

### Filtros determinísticos não são prova contra ataques novos

A máscara de PII e a remoção de injeção são regras, testadas em corpora do próprio projeto
([números](referencia.md#segurança-em-detalhe)). É teste de regressão, não prova.

- A última barreira é o callback do agendamento. Só são agendados códigos que a busca devolveu.
- Cada código precisa de um [trecho próprio](glossario.md#leitura-e-decisão-de-cada-exame) numa linha lida. Quando
  dois nomes do catálogo cabem no mesmo trecho, vale o mais longo ([a regra](regras.md#regras-que-só-decidem-com---yes)).
- A lista da confirmação ainda pode mostrar um exame que o modelo associou à linha de outro exame. Ele vem marcado
  [`confira`](glossario.md#leitura-e-decisão-de-cada-exame), e a pessoa decide.
- Se a resposta do OCR não traz o tipo de cada linha ([`line_intent`](glossario.md#os-valores-de-line_intent)), nada é
  agendado sem um "sim".

### Sem autenticação: na API e entre serviços

A API é o mock local do estudo. A porta publicada fica só em `127.0.0.1`, com limite de requisições por IP. Em
produção, entraria OAuth2 ou uma chave de API.

- OCR, RAG, API e agente dividem a rede interna, e qualquer um deles chama os outros sem credencial
  ([as redes](referencia.md#rede-e-processos)).
- Um OCR comprometido por uma imagem hostil poderia agendar na API.
- Em produção, uma rede por par (agente↔OCR, agente↔RAG, agente↔API) e um token por serviço.

### Cadeia de suprimentos, em parte

A imagem base é fixada por digest, e as Actions da CI, por commit. Os pacotes Python são fixados por versão (`==`):
os diretos em `requirements.txt` e `requirements-dev.txt`, os que eles puxam em `constraints.txt`.

- Faltam: `--require-hashes`, versão fixa dos pacotes do `apt` (Tesseract) e atualização automática do digest da base.
- Para fechar: gerar os hashes (`pip-compile --generate-hashes`), instalar com `--require-hashes` e fixar as versões
  do `apt` no `Dockerfile`.
- O `/docs` carrega o Swagger UI do `cdn.jsdelivr.net` em versão flutuante, sem SRI nem CSP estrita.
  - É o padrão do FastAPI: `swagger-ui-dist@5` e, para o ReDoc, `@2`.
  - As rotas JSON levam `default-src 'none'`.

### Confirmação forjada e pedido repetido

O ADK pausa o agendamento com uma chamada `adk_request_confirmation`, que espera o "sim"
([o fluxo](transpilador.md#a-confirmação-da-lista-confirmação-nativa-do-adk)). O ADK recusa uma resposta forjada a essa
pausa. Pelo `python -m runtime.web` (o `adk web` com o `Host` conferido), ela recebe um 400 com uma mensagem de uma
linha. No `adk web` puro, o próprio ADK responde 500. Nos dois, a chamada de agendamento não roda.

A `Idempotency-Key` vem da sessão: dentro dela, cada exame é agendado uma vez só. Mas a mesma foto enviada numa sessão
nova agenda de novo. Identificar o pedido pela imagem fica para quando houver um id de pedido real.

Até lá, não reenvie uma foto já agendada: o id sai na linha `Agendamento confirmado pela API` do `cli run`, e um
`GET /appointments/{appointment_id}` mostra o que já foi criado.

Os limites medidos, com números: [medicoes.md](medicoes.md#limites-conhecidos); o que segue em aberto:
[revisao.md](revisao.md#ainda-em-aberto).
