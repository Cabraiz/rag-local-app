# Decisões, custos e limites

O que tem aqui: a versão longa das seções "Decisões e trade-offs" e "Limites conhecidos" do
[README](../README.md). Cada decisão diz a escolha, o porquê e o custo; cada limite diz o que não
funciona e o que o contorna. Os números vêm de [medicoes.md](medicoes.md); o funcionamento, de
[arquitetura.md](arquitetura.md); cada regra de página e de linha, de [regras.md](regras.md).

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

**Quem faz o quê.** Na spec padrão, o Gemini faz três coisas:

- o `extract` lê as linhas de exame que o OCR devolveu, já mascaradas, e tira delas os nomes dos
  exames: rótulos, conectivos e redação livre ("Solicito também TSH e T4 livre");
- o `search` transforma cada nome, como foi lido, numa busca no catálogo e propõe o código do melhor
  resultado;
- o `schedule` chama a ferramenta de agendamento com os códigos propostos.

O código ([`runtime/callbacks.py`](../runtime/callbacks.py)) confere cada proposta contra o que foi lido e buscado.
Ele decide o que agenda, o que vai para a pergunta e o que fica de fora, e escreve a mensagem final com o que as
ferramentas devolveram. A pessoa confirma a lista.

Erro de OCR, abreviação e sinônimo ficam com a busca (`difflib` e os sinônimos do catálogo). Uma correção do modelo
não sobe a confiança, que é medida contra a linha lida.

**Por quê.** Num agendamento de saúde, a decisão precisa ser reprodutível e auditável linha a linha, e
uma ordem escrita na imagem não pode valer por ter convencido o modelo.

**Custo.** Cada etapa soma turnos do modelo ao tempo do pedido (a linha `Tempo:` da CLI mostra quanto),
e o que o modelo propõe só vale depois da conferência em código.

**O que o modelo recebe.** Um agente fica perigoso quando junta três coisas: lê conteúdo não
confiável, tem dados privados e pode mandar dados para fora. Aqui:

- do pedido, o modelo recebe só as linhas de exame, mascaradas no OCR. As outras chegam como
  `[linha de texto livre omitida]` ([quais](referencia.md#onde-a-pii-é-mascarada));
- numa linha de exame, depois do nome, um número ou uma palavra com maiúscula que não é do catálogo sai pela forma.
  Ainda podem chegar:
  - restos que a máscara não reconhece: um sobrenome em minúsculas, um número que também está num nome do catálogo,
    como "125";
  - o que é escrito com palavras do catálogo: uma anotação clínica ao lado de um exame ("(HIV +)", "paciente hiv") ou
    um nome feito de palavras de exame ("Albina Ferro");
- a única ação é agendar códigos do catálogo achados no pedido, em hosts fixos. OCR e RAG não têm
  e-mail nem internet.

Por isso o risco principal aqui é a integridade do agendamento, mais do que o vazamento.

**As faixas de confiança.** O código, não o modelo, decide pela confiança de cada exame, em três faixas: entra na
lista sem aviso, entra com aviso ou fica de fora ([os limites de cada uma](glossario.md#leitura-e-decisão-de-cada-exame)).

Custo: um exame mal lido não agenda sozinho ([detalhe](referencia.md#agendamento-conferido-em-código)).

### A confirmação final da lista

Antes da API, a pessoa vê:

- cada exame com o código e o aviso;
- os exames que o OCR leu e não serão agendados, inclusive os que o agente não buscou (o pedido
  inteiro é conferido antes da pergunta);
- o que o OCR tirou da página e, uma vez no topo, as linhas que fizeram a página ser perguntada.

E responde `Agendar estes N exames? [s/N]` (padrão: não).

A pergunta é a confirmação nativa de ferramenta do ADK, pedida pelo callback e não pelo modelo. Ela funciona igual na
CLI, no `adk run` e no `adk web` ([como](transpilador.md#a-confirmação-da-lista-confirmação-nativa-do-adk)).

**Por quê.** Regras de página não enxergam todo layout: um X marcando só alguns exames num formulário
impresso, um carimbo "CANCELADO" ou uma nota girada que o OCR não lê, uma nota abaixo da assinatura.
Quem confirma é a pessoa.

**Custo.** Um passo a mais em todo pedido. A confirmação de ferramenta e o `ResumabilityConfig` são
experimentais no ADK 2.10, fixado no `requirements.txt`.

Com `--yes` não há pergunta. É escolha e risco de quem opera: as regras são a única barreira, e só agenda o que elas
agendariam sozinhas.

### Por que existe uma camada de regras

A imagem é entrada não confiável e o pedido é médico. As regras determinísticas
([mapa das 22 regras](regras.md)) existem para que o modelo nunca seja a última barreira:

- **sempre:** uma negação clara ("não realizar Ferritina", "já realizado", um bloco "Já realizados:")
  ou uma linha de preparo não agenda; o exame é avisado com o motivo;
- **com `--yes`:** um exame só agenda sozinho se a página inteira for só a lista de exames
  ([o que conta](regras.md#regras-que-só-decidem-com---yes)). Qualquer outro texto lido faz todo exame da página ser
  perguntado.

**Por quê "só a lista" e não uma lista de negações.** A primeira versão procurava palavras de negação. Três revisões
acharam o que ela não cobria: "somente se", "adiar para a próxima consulta", "Trazer os laudos de:", "desconsiderar o
2º", outra língua, uma 2ª folha. Exigir que a página seja só a lista falha para o lado de perguntar.

**Custo.** Páginas honestas com uma observação, ou com erros de OCR na letra de mão, perguntam tudo
([medição](medicoes.md#lista-branca-por-página)). Sem `--yes`, as regras só decidem o aviso ao lado de
cada exame, e a pessoa é a barreira final.

### Filtros determinísticos dentro do OCR, antes de qualquer modelo

A máscara de PII deixa sair do container `ocr` só o que parece exame ou estrutura do pedido, e a
ordem ao modelo é tirada da linha. Por quê: a falha fica local, reproduzível e testável, sem depender
do prompt. Custo: manter regras, mitigado por corpora de regressão
([números](referencia.md#segurança-em-detalhe)).

### PII mascarada na origem, banco sem PII

Nome, CPF, telefone, e-mail e os outros tipos viram `[NOME]`, `[CPF]`… no container do OCR. A API só
aceita código e nome de exame, grava o nome do catálogo e cifra a lista com AES-256-GCM.

**Por quê.** O LLM recebe o texto já mascarado, nunca a imagem, e o banco não depende da máscara.

**Custo.** A detecção é por regras e vale para os formatos testados: um nome escrito de um jeito não
testado pode chegar ao modelo ([camadas](referencia.md#segurança-em-detalhe)).

**A cifra.** Cada lista fica presa ao `id`, ao status e à data do agendamento (o dado associado do GCM). Por isso uma
lista copiada para outra linha, ou uma data editada, é detectada. AES-GCM foi escolhido no lugar do Fernet por esse
vínculo com os campos em claro ([como funciona](referencia.md#segurança-em-detalhe)).

- A chave fica num volume próprio (`api-key`), fora do git e fora do volume do banco (`api-data`). Assim uma cópia do
  banco sozinha não revela os exames, e o início rápido não tem passo manual de chave.
- Em uso real, a chave viria de um gerenciador de segredos, por `DB_ENCRYPTION_KEY`.
- Custos e limites:
  - quem tem os dois volumes, ou o host do Docker, lê tudo. A API decifra para responder ao `GET`;
  - `id`, status e datas ficam em claro, e a rotação de chave está fora do escopo;
  - `docker compose down -v` apaga a chave e o banco juntos. Uma cópia antiga do banco fica ilegível sem aquela chave;
  - uma chave errada só aparece na primeira leitura de um agendamento (500), não ao subir a API. Não há
    registro-canário, por simplicidade;
  - o tamanho do valor cifrado acompanha o texto, então revela aproximadamente quantos exames o agendamento tem.

### Busca lexical, sem embeddings

Palavras em comum mais `difflib`, só com a biblioteca padrão.

- **Por quê:** a base tem 120 exames fictícios. A busca é determinística e explicável: cada resultado diz o termo que
  deu o score. Ela tolera erros de OCR, e a busca semântica, medida, não agendou nenhum exame a mais.
- **Custo:** não entende paráfrases ([medição](medicoes.md#busca-semântica-avaliada-não-adotada)).

### MCP só via SSE, rede interna, containers endurecidos

OCR e RAG não têm internet nem porta no host. Os containers rodam sem root, com sistema de arquivos
somente leitura e sem capabilities; só a API publica porta, em `127.0.0.1`. Por quê: o escopo do estudo
fixa SSE, e cada serviço só tem o que usa. Custo: a especificação 2025-03-26 do MCP trocou o SSE pelo
Streamable HTTP; o ADK também tem `StreamableHTTPConnectionParams`, e mudar é trocar a conexão no
template e o `run` dos servidores.

### Transpilador estrito e genérico

- **Estrito:**
  - Pydantic com campos extras proibidos e tipos estritos;
  - os valores da spec entram por `repr()`, nunca como código;
  - o arquivo é compilado e importado antes do OK;
  - cada servidor que responde confirma as ferramentas. Um servidor fora do ar no `transpile` só é conferido no `run`.

  Por quê: o erro aparece no `transpile`, com campo e motivo, não no meio de um agendamento
  ([validação](transpilador.md#validação-e-mensagens-de-erro)).
- **Genérico:** a spec descreve construções do ADK (servidores MCP e OpenAPI, `LlmAgent` com
  placeholders `{chave?}` e `output_schema`, `SequentialAgent`/`ParallelAgent`/`LoopAgent` e plugins do
  `App` com `kwargs`). O domínio (quem lê, busca e agenda, os limiares, a regra fixa sobre dados não
  confiáveis) é o `BookingPlugin`, que confere as próprias regras no `transpile`; o transpilador não
  importa nada dele.
- **Custo:** plugins só dos pacotes `runtime` e `google.adk.plugins`, e o `cli run` só roda specs com o
  `BookingPlugin`.

### O `agent.py` usa só classes do ADK; a política mora num plugin versionado

O `agent.py` gerado (cerca de 110 linhas, [exemplo](exemplo-agent.py)) instancia só classes do Google ADK
([quais](referencia.md#as-regras-que-o-agentpy-importa)). A política de agendamento é o `BookingPlugin`, um `BasePlugin`
do ADK declarado na spec. Ele roda sozinho com `adk run` e `adk web`, sem a CLI.

**Por que uma biblioteca e não a política copiada em cada arquivo.** A regra é testada uma vez, para
toda spec. A interface é declarada (`__all__` e `API_VERSION`, hoje 6): um arquivo gerado para outra
versão para já na importação, com mensagem clara.
[`test_runtime.py`](../tests/test_runtime.py) prova isso:

- copia o `agent.py`, o `runtime/`, o `catalogo.py` e o `leitura.py` para uma pasta fora do repositório;
- num interpretador limpo, importa o agente;
- chama os callbacks com respostas simuladas do OCR e da busca.

**Custo.** O arquivo gerado depende de `runtime/`, `catalogo.py` e `leitura.py` ao lado dele.

- O `adk run generated` na raiz do projeto os encontra. No container, `PYTHONPATH=/app` faz o mesmo.
- Não é um pacote instalável pelo pip.
- Uma spec sem o plugin ([exemplo fora do domínio](../specs/exemplo-generico.json)) importa só os toolsets e o modelo.

**O que muda fora da CLI** ([a lista](como-rodar.md#4-rodar-com-adk-run-ou-adk-web)). Antes da pergunta, a
conferência do pedido inteiro roda no próprio laço de eventos do ADK.

- A execução espera por ela: segundos, ou até 30 s se a busca não responder.
- No `adk web`, as outras sessões seguem enquanto isso.

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

- O ADK 2.10 emite um `DeprecationWarning` em favor de `Workflow`, que ainda não é um `BaseAgent`, e a spec descreve agentes.
- Custo:
  - não há replanejamento: um exame fora do catálogo fica fora do agendamento;
  - um aviso de depreciação;
  - uma migração futura que mexe também na CLI. Ela trata o agente raiz como um `BaseAgent`: lê os `sub_agents` no
    `transpile` e o entrega ao `App` no `run`.

### OCR: Tesseract com preparo

Luz achatada, contraste, endireitamento e uma passada no modo de texto esparso do Tesseract (PSM 11). Cada linha volta
com a confiança do Tesseract (`line_confidence`).

- **Ganho:** medido em 2026-10-06, ao adotar o preparo, em 185 pedidos (5 de `samples/`, 60 da carga e 120
  manuscritos).
  - Os exames achados no texto foram de 45% para 53%.
  - Nos 120 manuscritos, de 23% para 34%.
  - A latência sobe 1,6x.
- **Custo:** letra de médico continua quase ilegível: 1 de 206 exames agendado sozinho
  ([medição](medicoes.md#pedidos-manuscritos-simulados)).

Próximo passo avaliado: um OCR moderno nas linhas de baixa confiança, adiado pelo custo em imagem, memória e tempo
de CPU ([medição](medicoes.md#ocr-moderno-avaliado-não-adotado)).

### Pedido em papel, em produção

Primeiro, receber a receita estruturada (eletrônica), sem OCR: o papel fica como exceção.

- No papel, para letra de médico, um modelo de visão:
  - em GPU local, ou em nuvem sob contrato de operador de dados, porque dado de saúde é sensível na LGPD;
  - aceito só quando a transcrição livre e a escolha no catálogo concordam;
  - o que não é certo vai para uma pessoa conferir.
- Por quê, nos 202 exames de letra de médico simulada, medidos em 2026-10-07 ([medição](medicoes.md#letra-de-médico-leitor-local-avaliado-não-ligado-por-padrão)):
  - o leitor local, em CPU, aceitou 24% sem errar, mas nenhum exame da foto real;
  - a escolha no catálogo sozinha aceitou 71 errados.
- Custo: GPU ou contrato, e revisão humana dos pedidos duvidosos.

### API: contrato, idempotência e defesas

Cada defesa com os valores, os cabeçalhos e os campos do log:
[referencia.md](referencia.md#a-api-idempotência-host-limite-cabeçalhos-e-log).

- **Contrato lido do `/openapi.json` vivo** (`OpenAPIToolset`).
  - Por quê: o agente usa exatamente o contrato do Swagger.
  - Custo: a API precisa estar no ar quando o agente monta as ferramentas.
- **`Idempotency-Key` vinda do runtime, nunca do modelo.**
  - Por quê: uma chamada que o modelo repete não agenda em dobro.
  - Custo: sem a chave, dois `POST` iguais criam dois agendamentos.
- **A chave guardada só como HMAC.**
  - Por quê: ela vem do cliente e pode carregar dado pessoal.
- **`Host` conferido, escrito à mão.**
  - Por quê: escutar só em `127.0.0.1` não basta contra DNS rebinding, porque o navegador manda `Host: evil.example`.
  - À mão porque o middleware do Starlette lê a lista ao ser montado, antes do `lifespan` que lê as variáveis, e
    responde em texto puro.
- **Limite por cliente, em memória.**
  - Por quê: defesa em profundidade, não controle de acesso. Os ids já são uuid4, e não há listagem.
  - Custo: o estado é por processo. Com várias réplicas, cada uma conta o seu. Atrás de um proxy ou da porta publicada
    do Docker, os clientes tendem a parecer um só.
- **Log sem corpo, sem chave e com `request_id` próprio.**
  - Por quê: o corpo, a chave e o `request_id` do cliente podem ter dado pessoal.
- **CSP estrita só nas rotas JSON.**
  - Custo: `/docs` e `/redoc` ficam sem ela, porque carregam scripts do `cdn.jsdelivr.net` e um script inline
    ([cadeia de suprimentos](#cadeia-de-suprimentos-em-parte)).

### Keep-alive de 75 s

OCR, RAG e API mantêm uma conexão ociosa por 75 s, o mesmo prazo do pool do cliente, e não pelos 5 s padrão do
uvicorn. Com os dois em 5 s, um POST enviado no instante do fechamento se perdia, e a chamada MCP esperava para sempre
([python-sdk#906](https://github.com/modelcontextprotocol/python-sdk/issues/906)).

Medido em 2026-10-06, ao trocar o prazo, em 2.880 chamadas espaçadas de 5 s: 15 travamentos com 5 s e nenhum com
75 s.

### Um `Dockerfile` multi-stage

Cada serviço instala só o que precisa, com versões fixadas em `requirements.txt` (o que roda) e
`requirements-dev.txt` (testes e checagens). O `agent` leva só o que `transpile` e `run` usam; pytest,
ruff, mypy, o Tesseract e os testes ficam no estágio `test`. Custo: o primeiro build monta quatro
imagens, e cinco com a de testes.

### Escala

O agente não guarda estado entre execuções, OCR e RAG são serviços separados e sem estado, e o SQLite
atende o mock. Para escalar: Postgres no lugar do SQLite e mais réplicas do `agent` e da `api`. Custo, hoje: o SQLite
aceita um escritor por vez, então há uma réplica da `api`, e o limite de requisições por cliente vale por processo
([API](#api-contrato-idempotência-e-defesas)).

## Limites

### Com `--yes`, um exame injetado como linha comum é agendado

Sem `--yes`, a pessoa vê a lista inteira e confirma.

- Com ele, as regras são a única barreira.
- Numa página que é só a lista, uma linha que só diz "Ferritina" é indistinguível de um pedido real e agenda sozinha.
- Qualquer texto além da lista faz a página inteira ser perguntada, inclusive numa página legítima
  ([medição](medicoes.md#lista-branca-por-página)).
- Campos, assinatura e cabeçalho da clínica não contam como texto além da lista. Exemplos que fazem perguntar:
  - uma observação de preparo com outras palavras;
  - um cabeçalho que as regras não conhecem;
  - um erro de OCR na letra de mão;
  - uma assinatura à mão que o OCR lê como texto abaixo da lista.

**O que ainda passa, com `--yes`.** A regra só vê o texto que o Tesseract lê:

- um carimbo, uma nota na vertical ou em diagonal, um risco à mão ou uma letra clara demais deixam o
  exame como linha comum;
- uma letra cinza que o Tesseract lê, menor ou mais clara que a da página, é pega pela regra;
- um teste da tinta que o OCR não leu foi avaliado e ficou de fora
  ([medição](medicoes.md#rodapé-formulário-nome-e-nome-mais-longo)):
  - ele procurava cor saturada ou tinta escura fora das caixas das palavras;
  - pegaria os 4 carimbos e notas giradas do conjunto de ataques;
  - mas disparou em 30 de 30 fotos e em 110 de 120 manuscritas;
- acima da lista, uma linha tirada inteira conta como cabeçalho da clínica quando:
  - parece um: clínica, hospital ou laboratório, endereço, telefone, CNPJ, CRM, "Receituário" ou data;
  - e está separada da lista por um campo, como "Paciente:". Uma nota que traga uma dessas palavras passa;
- abaixo do 1º exame, qualquer texto que a máscara tira ou não reconhece faz a página ser perguntada, em qualquer
  língua. A exceção é uma assinatura que é só nome, CRM e data;
- "TSH ferro", um sobrenome que é palavra de exame, sem parênteses, conta como dois exames;
- já um exame entre parênteses, ou ao lado de um resultado na linha de outro, é anotação e faz a linha ser perguntada:
  "Glicemia de jejum (HIV +)", "HIV positivo";
- num formulário, só os exames marcados contam:
  - se o OCR lê a marca de uns e não de outros, tudo é perguntado
    (`; formulário com marcas: só os marcados contam; confira`);
  - se não lê nenhuma marca, a lista impressa parece um pedido comum;
- o Tesseract em PSM 11 não devolve blocos de texto, então o fim de um bloco sob "Já realizados:" vem do
  vão entre as caixas das linhas.

### A pergunta mostra a linha mascarada

Em "- Ferritina - pedido por engano", a máscara tira "pedido por engano" antes do modelo e da CLI; a
lista da confirmação diz `; o pedido tem outras palavras além do exame` e mostra
`lido "- Ferritina - [TEXTO_REMOVIDO]"`. Quem responde confere o papel.

### Exame lido certo, mas com leitura fraca, fica fora da pergunta

Abaixo de 0,70 o exame não entra na pergunta: é avisado em `baixa confiança` e não é agendado.

- Exemplo: 2 dos 4 exames do `samples/pedido-realista.png` ([como aparece](como-rodar.md#testar-outra-imagem)).
- É conservador de propósito: com uma imagem nova, o resultado pode ser um agendamento parcial com avisos.

### Filtros determinísticos não são prova contra ataques novos

A máscara de PII e a remoção de injeção são regras, testadas em corpora do próprio projeto
([números](referencia.md#segurança-em-detalhe)). É teste de regressão, não prova.

- A última barreira é o callback do agendamento. Só são agendados códigos que a busca devolveu, ancorados nas linhas
  lidas, no nome mais longo do catálogo escrito ali.
- A lista da confirmação ainda pode mostrar um exame que o modelo associou mal (achado na linha de outro exame), marcado `confira`: a pessoa o vê e decide.
- Sem a leitura do OCR por linha, nada é agendado sem um "sim".

### Sem autenticação: na API e entre serviços

A API é o mock local do estudo: a porta publicada fica só em `127.0.0.1`, com limite de requisições por IP; em produção, entraria OAuth2 ou uma chave de API.

- OCR, RAG, API e agente dividem a rede interna, e qualquer um deles chama os outros sem credencial
  ([as redes](referencia.md#rede-e-processos)).
- Um OCR comprometido por uma imagem hostil poderia agendar na API.
- Em produção, uma rede por par (agente↔OCR, agente↔RAG, agente↔API) e um token por serviço.

### Cadeia de suprimentos, em parte

A imagem base é fixada por digest e as Actions da CI por commit; os pacotes Python, todos por versão (`==`): os diretos em `requirements.txt` e `requirements-dev.txt`, os que eles puxam em `constraints.txt`.

- Faltam: `--require-hashes`, versão fixa dos pacotes do `apt` (Tesseract) e atualização automática do digest da base.
- O `/docs` carrega o Swagger UI do `cdn.jsdelivr.net` em versão flutuante, sem SRI nem CSP estrita.
  - É o padrão do FastAPI: `swagger-ui-dist@5` e, para o ReDoc, `@2`.
  - As rotas JSON levam `default-src 'none'`.

### Confirmação forjada e pedido repetido

Uma resposta `adk_request_confirmation` forjada que o ADK recusa recebe 400 numa linha pelo
`python -m runtime.web`. No `adk web` puro, o próprio ADK responde 500. Nos dois, a chamada de agendamento não roda.

A `Idempotency-Key` vem da sessão: dentro dela, cada exame é agendado uma vez só. Mas a mesma foto enviada numa sessão
nova agenda de novo. Identificar o pedido pela imagem fica para quando houver um id de pedido real.

Os limites medidos, com números: [medicoes.md](medicoes.md#limites-conhecidos); o que segue em aberto:
[revisao.md](revisao.md#ainda-em-aberto).
