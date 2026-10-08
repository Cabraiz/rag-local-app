# Decisões, custos e limites

O que tem aqui: a versão longa das seções "Decisões e trade-offs" e "Limites conhecidos" do
[README](../README.md). Cada decisão diz a escolha, o porquê e o custo; cada limite diz o que não
funciona e o que o contorna. Os números vêm de [medicoes.md](medicoes.md); o funcionamento, de
[arquitetura.md](arquitetura.md); cada regra de página e de linha, de [regras.md](regras.md).

## Decisões

### O modelo propõe, o código decide, a pessoa confirma

**Quem faz o quê.** Na spec padrão, o Gemini faz três coisas:

- o `extract` lê as linhas de exame que o OCR devolveu, já mascaradas, e tira delas os nomes dos
  exames: rótulos, conectivos e redação livre ("Solicito também TSH e T4 livre");
- o `search` transforma cada nome, como foi lido, numa busca no catálogo e propõe o código do melhor
  resultado;
- o `schedule` chama a ferramenta de agendamento com os códigos propostos.

O código ([`runtime/`](../runtime/callbacks.py)) confere cada proposta contra o que foi lido e
buscado, decide o que agenda, o que vai para a pergunta e o que fica de fora, e escreve a mensagem
final com o que as ferramentas devolveram. A pessoa confirma a lista. Erro de OCR, abreviação e
sinônimo ficam com a busca (`difflib` e os sinônimos do catálogo): uma correção do modelo não sobe a
confiança, que é medida contra a linha lida.

**Por quê.** Num agendamento de saúde, a decisão precisa ser reprodutível e auditável linha a linha, e
uma ordem escrita na imagem não pode valer por ter convencido o modelo.

**Custo.** Cada etapa soma turnos do modelo ao tempo do pedido (a linha `Tempo:` da CLI mostra quanto),
e o que o modelo propõe só vale depois da conferência em código.

**O que o modelo recebe.** Um agente fica perigoso quando junta três coisas: lê conteúdo não
confiável, tem dados privados e pode mandar dados para fora. Aqui:

- do pedido, o modelo recebe só as linhas de exame, mascaradas no OCR. Cabeçalho, nome, documento,
  observação, ordem escrita e linha com nome mascarado chegam como `[linha de texto livre omitida]`;
- numa linha de exame, depois do nome, um número ou uma palavra com maiúscula que não é do catálogo sai
  pela forma. Ainda podem chegar restos que a máscara não reconhece (um sobrenome em minúsculas, um
  número que também está num nome do catálogo, como "125") e o que é escrito com palavras do catálogo:
  uma anotação clínica ao lado de um exame ("(HIV +)", "paciente hiv") ou um nome feito de palavras de
  exame ("Albina Ferro");
- a única ação é agendar códigos do catálogo achados no pedido, em hosts fixos. OCR e RAG não têm
  e-mail nem internet.

Por isso o risco principal aqui é a integridade do agendamento, mais do que o vazamento.

**As faixas de confiança.** A confiança de um exame é o menor valor entre o score da busca, o quanto a
busca bate com a linha e a leitura do OCR naquela linha. Com 0,90 ou mais, o exame entra na lista; de
0,70 a 0,90, entra com aviso; abaixo, só é avisado. Custo: um exame mal lido não agenda sozinho
([detalhe](arquitetura.md#agendamento-conferido-em-código)).

### A confirmação final da lista

Antes da API, a pessoa vê:

- cada exame com o código e o aviso;
- os exames que o OCR leu e não serão agendados, inclusive os que o agente não buscou (o pedido
  inteiro é conferido antes da pergunta);
- o que o OCR tirou da página e, uma vez no topo, as linhas que fizeram a página ser perguntada.

E responde `Agendar estes N exames? [s/N]` (padrão: não). A pergunta é a confirmação nativa de
ferramenta do ADK, pedida pelo callback, não pelo modelo, e funciona igual na CLI, no `adk run` e no
`adk web`. O app é retomável: a execução pausa, a CLI pergunta fora do laço de eventos e retoma a mesma
chamada, sem refazer o OCR nem a busca. Uma confirmação que o runtime não pediu, ou escrita no estado
da sessão, não agenda nada, e cada execução agenda uma vez só.

**Por quê.** Regras de página não enxergam todo layout: um X marcando só alguns exames num formulário
impresso, um carimbo "CANCELADO" ou uma nota girada que o OCR não lê, uma nota abaixo da assinatura.
Quem confirma é a pessoa.

**Custo.** Um passo a mais em todo pedido. A confirmação de ferramenta e o `ResumabilityConfig` são
experimentais no ADK 2.10, fixado no `requirements.txt`
([detalhes](transpilador.md#a-confirmação-da-lista-confirmação-nativa-do-adk)). Com `--yes`, escolha e
risco de quem opera, não há pergunta: as regras são a única barreira e só agenda o que elas agendariam
sozinhas.

### Por que existe uma camada de regras

A imagem é entrada não confiável e o pedido é médico. As regras determinísticas
([mapa das 22 regras](regras.md)) existem para que o modelo nunca seja a última barreira:

- **sempre:** uma negação clara ("não realizar Ferritina", "já realizado", um bloco "Já realizados:")
  ou uma linha de preparo não agenda; o exame é avisado com o motivo;
- **com `--yes`:** um exame só agenda sozinho se a página inteira for só a lista de exames, com os
  rótulos, o jejum, os campos de paciente e médico, a assinatura e, acima da lista, o cabeçalho
  impresso da clínica. Qualquer outro texto lido faz todo exame da página ser perguntado.

**Por quê "só a lista" e não uma lista de negações.** A primeira versão procurava palavras de negação,
e três revisões acharam o que ela não cobria ("somente se", "adiar para a próxima consulta", "Trazer os
laudos de:", "desconsiderar o 2º", outra língua, uma 2ª folha). Exigir que a página seja só a lista
falha para o lado de perguntar.

**Custo.** Páginas honestas com uma observação, ou com erros de OCR na letra de mão, perguntam tudo
([medição](medicoes.md#lista-branca-por-página)). Sem `--yes`, as regras só decidem o aviso ao lado de
cada exame, e a pessoa é a barreira final.

### Filtros determinísticos dentro do OCR, antes de qualquer modelo

A máscara de PII deixa sair do container `ocr` só o que parece exame ou estrutura do pedido, e a
ordem ao modelo é tirada da linha. Por quê: a falha fica local, reproduzível e testável, sem depender
do prompt. Custo: manter regras, mitigado por corpora de regressão (3.600 casos gerados de PII, 790
ataques).

### PII mascarada na origem, banco sem PII

Nome, CPF, telefone, e-mail e os outros tipos viram `[NOME]`, `[CPF]`… no container do OCR. A API só
aceita código e nome de exame, grava o nome do catálogo e cifra a lista com AES-256-GCM.

**Por quê.** O LLM recebe o texto já mascarado, nunca a imagem, e o banco não depende da máscara.

**Custo.** A detecção é por regras e vale para os formatos testados: um nome escrito de um jeito não
testado pode chegar ao modelo ([camadas](arquitetura.md#segurança-em-detalhe)).

**A cifra.** Cada lista recebe um nonce aleatório e fica presa ao `id`, ao status e à data do
agendamento (dado associado do GCM). Chave errada, byte alterado, valor copiado para outra linha ou data
editada são detectados. Preferi AES-GCM ao Fernet por esse vínculo com os campos em claro. A chave é
gerada na 1ª subida da API, num volume próprio (`api-key`), fora do git e fora do volume do banco
(`api-data`): uma cópia do banco sozinha não revela os exames, e o início rápido não tem passo manual de
chave. Em uso real, a chave viria de um gerenciador de segredos, por `DB_ENCRYPTION_KEY` (que tem
precedência; `python -m api.crypto --gerar-chave` cria uma). Custos e limites:

- quem tem os dois volumes (ou o host do Docker) lê tudo; a API decifra para responder ao `GET`;
- `id`, status e datas ficam em claro; rotação de chave está fora do escopo;
- `docker compose down -v` apaga a chave e o banco juntos, e uma cópia antiga do banco fica ilegível sem
  aquela chave;
- uma chave errada só aparece na primeira leitura de um agendamento (500), não ao subir a API: não há
  registro-canário, por simplicidade;
- o tamanho do valor cifrado acompanha o texto, então revela aproximadamente quantos exames o
  agendamento tem.

### Busca lexical, sem embeddings

Palavras em comum mais `difflib`, só com a biblioteca padrão. Por quê: a base tem 120 exames fictícios,
a busca é determinística e explicável (cada resultado diz o termo que deu o score) e tolera erros de
OCR; a busca semântica, medida, não agendou nenhum exame a mais. Custo: não entende paráfrases
([medição](medicoes.md#busca-semântica-avaliada-não-adotada)).

### MCP só via SSE, rede interna, containers endurecidos

OCR e RAG não têm internet nem porta no host. Os containers rodam sem root, com sistema de arquivos
somente leitura e sem capabilities; só a API publica porta, em `127.0.0.1`. Por quê: o escopo do estudo
fixa SSE, e cada serviço só tem o que usa. Custo: a especificação 2025-03-26 do MCP trocou o SSE pelo
Streamable HTTP; o ADK também tem `StreamableHTTPConnectionParams`, e mudar é trocar a conexão no
template e o `run` dos servidores.

### Transpilador estrito e genérico

- **Estrito:** Pydantic com campos extras proibidos e tipos estritos; os valores da spec entram por
  `repr()`, nunca como código; o arquivo é compilado e importado antes do OK; cada servidor que responde
  confirma as ferramentas (um servidor fora do ar no `transpile` só é conferido no `run`). Por quê: o
  erro aparece no `transpile`, com campo e motivo, não no meio de um agendamento
  ([validação](transpilador.md#validação-e-mensagens-de-erro)).
- **Genérico:** a spec descreve construções do ADK (servidores MCP e OpenAPI, `LlmAgent` com
  placeholders `{chave?}` e `output_schema`, `SequentialAgent`/`ParallelAgent`/`LoopAgent` e plugins do
  `App` com `kwargs`). O domínio (quem lê, busca e agenda, os limiares, a regra fixa sobre dados não
  confiáveis) é o `BookingPlugin`, que confere as próprias regras no `transpile`; o transpilador não
  importa nada dele.
- **Custo:** plugins só dos pacotes `runtime` e `google.adk.plugins`, e o `cli run` só roda specs com o
  `BookingPlugin`.

### O `agent.py` usa só classes do ADK; a política mora num plugin versionado

O `agent.py` gerado (cerca de 110 linhas, [exemplo](exemplo-agent.py)) instancia só classes do Google
ADK:

- `LlmAgent`, `SequentialAgent`, `App` e `ResumabilityConfig`, direto do ADK;
- o modelo: `Gemini` ou `FallbackModel` do ADK;
- os toolsets: `McpToolset` com `SseConnectionParams` e `OpenAPIToolset`, por subclasses finas em
  [`runtime/adk.py`](../runtime/adk.py) que só conferem o host antes da 1ª conexão (o
  `LiveOpenAPIToolset` monta o `OpenAPIToolset` do ADK a partir do `/openapi.json` vivo);
- a política de agendamento: o `BookingPlugin`, um `BasePlugin` do ADK, declarado na spec. Nenhum agente
  tem callback próprio.

Ele roda sozinho com `adk run` e `adk web`, sem a CLI.

**Por que uma biblioteca e não a política copiada em cada arquivo.** A regra é testada uma vez, para
toda spec. A interface é declarada (`__all__` e `API_VERSION`, hoje 6): um arquivo gerado para outra
versão para já na importação, com mensagem clara.
[`test_runtime.py`](../tests/test_runtime.py) copia o `agent.py`, o `runtime/`, o `catalogo.py` e o
`leitura.py` para uma pasta fora do repositório e, num interpretador limpo, importa o agente e chama os
callbacks com respostas simuladas do OCR e da busca.

**Custo.** O arquivo gerado depende de `runtime/` (e de `catalogo.py` e `leitura.py`) ao lado dele: o
`adk run generated` na raiz do projeto os encontra, e no container `PYTHONPATH=/app` também. Não é um
pacote instalável pelo pip. Uma spec sem o plugin ([exemplo fora do domínio](../specs/exemplo-generico.json))
importa só os toolsets e o modelo; o servidor dela não é do compose, então o `transpile` pede
`-e ALLOWED_HOSTS=docs:8010` ([comando](transpilador.md#campos-da-spec)).

**O que fica só na CLI.** O modelo reserva vale nos três modos (por requisição no `adk run` e no
`adk web`). A linha `Tempo:` e a checagem de que o `agent.py` é o que a spec gera hoje ficam só na CLI
([detalhes](como-rodar.md#4-rodar-com-adk-run-ou-adk-web)). Antes da pergunta, a conferência do pedido
inteiro roda no próprio laço de eventos do ADK: a execução espera por ela (segundos, ou até 30 s se a
busca não responder), e no `adk web` as outras sessões seguem enquanto isso.

### A resposta do OCR é um contrato versionado

[`leitura.py`](../leitura.py) (`OcrReading`, com `version`): o servidor OCR a monta, e o runtime a
valida uma vez e lê campos tipados. Uma resposta fora do contrato não é lida: nada dela chega ao modelo,
nada é agendado nem perguntado. A ferramenta continua declarando um objeto simples, porque o ADK copia o
`outputSchema` de uma ferramenta MCP para a declaração que o modelo lê.

**Por que a leitura de cada linha fica no servidor OCR** (`line_intent`, `contested_exams`,
`page_clean`): ela precisa do que só ele tem, o texto antes da máscara, a geometria das linhas e a
confiança de cada uma. Levá-la para o runtime faria esses dados, com a PII ainda não mascarada, saírem
do container. Custo: parte da regra de agendamento roda em outro processo, e um tipo de linha novo pede
uma versão nova do contrato, nos dois lados.

### `SequentialAgent`, obsoleto no ADK 2.10

Ordem fixa `extract` → `search` → `schedule`, porque cada etapa depende da anterior. O ADK 2.10 emite um
`DeprecationWarning` em favor de `Workflow`, que ainda não é um `BaseAgent`, e a spec descreve agentes.
Custo: não há replanejamento (um exame fora do catálogo fica fora do agendamento), um aviso de depreciação e uma migração futura que mexe também na CLI, que
trata o agente raiz como um `BaseAgent` (lê os `sub_agents` no `transpile` e o entrega ao `App` no
`run`).

### OCR: Tesseract com preparo

Luz achatada, contraste, endireitamento e uma passada no modo de texto esparso do Tesseract (PSM 11);
cada linha volta com a confiança do Tesseract (`line_confidence`). Em 185 pedidos (5 de `samples/`, 60
da carga, 120 manuscritos), os exames achados no texto foram de 45% para 53% (manuscritos: 23% → 34%),
com 1,6x a latência. Custo: letra de médico continua quase ilegível (5% dos exames achados no texto; 1
de 206 agendado sozinho).

Próximo passo avaliado: um OCR moderno nas linhas de baixa confiança. Em 42 manuscritos, o EasyOCR acha
35,5% (o Tesseract com o preparo, 32,3%; os dois juntos, 41,9%), mas soma +2,1 GB à imagem, 1,4 GB de memória e 26 s no
p95 por imagem em CPU; o PaddleOCR soma +2,4 GB. Fica para quando houver GPU ou um modelo menor
(avaliação medida fora do repositório, sem os dados aqui).

### Pedido em papel, em produção

Receita estruturada primeiro. No papel, para letra de médico, um modelo de visão em GPU local, ou em
nuvem sob contrato de operador de dados (dado de saúde é sensível na LGPD), aceito só quando a
transcrição livre e a escolha no catálogo concordam; o que não é certo vai para uma pessoa conferir.
Por quê: o leitor local medido em CPU aceitou 24% da letra de médico simulada sem errar, mas nenhum
exame da foto real, e a escolha no catálogo sozinha aceitou 71 errados. Custo: GPU ou contrato, e
revisão humana dos pedidos duvidosos
([medição](medicoes.md#letra-de-médico-leitor-local-avaliado-não-ligado-por-padrão)).

### API: contrato, idempotência e defesas

- **Contrato lido do `/openapi.json` vivo** (`OpenAPIToolset`): o agente usa exatamente o contrato do
  Swagger. Custo: a API precisa estar no ar quando o agente monta as ferramentas.
- **`Idempotency-Key` opcional no `POST /appointments`:** a mesma chave com os mesmos códigos devolve o
  mesmo agendamento (201); um código já agendado com a mesma chave, noutra lista, dá 409. Só os códigos
  definem o agendamento: o `name` é opcional e a API grava o do catálogo. A tabela guarda HMACs
  (derivados da chave do banco), nunca a chave nem o corpo, porque a chave vem do cliente e pode carregar
  dado pessoal. Cada chave vale por `API_IDEMPOTENCY_TTL_HOURS` (padrão 24 h). Num banco de versão
  anterior, a tabela com a chave em claro é apagada ao subir, com `secure_delete`. O agente manda só o
  `code` de cada exame e uma chave própria por execução (um uuid aleatório, nunca a do modelo), e o
  modelo reserva usa a mesma. Depois do 1º agendamento, o callback devolve esse mesmo agendamento a
  qualquer nova chamada da execução, sem chegar à API. Sem a chave, dois POSTs iguais criam dois
  agendamentos.
- **Log estruturado:** uma linha JSON por requisição, com `request_id` (sempre novo, devolvido no
  `X-Request-ID`; o do cliente pode ter dado pessoal), método, rota (o modelo, como
  `/appointments/{appointment_id}`), status e duração. Não entram corpo, exames, `Idempotency-Key` nem
  caminho desconhecido. Substitui o access log do uvicorn. Nos servidores MCP, todo valor de log que não é número, nome de ferramenta,
  método ou rota conhecida vira `…`, e uma exceção aparece só pelo tipo.
- **`Host` conferido** ([`test_api_host.py`](../tests/test_api_host.py)): só `127.0.0.1`, `localhost` e
  `api`, em qualquer porta, de `API_ALLOWED_HOSTS` (um valor inválido faz a API não subir); o resto recebe
  `400` antes do limite por cliente e do corpo. Escutar só em `127.0.0.1` não basta
  contra DNS rebinding: o navegador manda `Host: evil.example`. Foi escrito à mão porque o middleware do
  Starlette lê a lista ao ser montado, antes do `lifespan` que lê as variáveis, e responde em texto puro.
- **Limite por cliente:** um balde de tokens por IP, em memória, 1200 requisições por minuto
  (`API_RATE_LIMIT_PER_MINUTE`; 0 desliga); acima, `429` com `Retry-After`; `/health` fica de fora. É
  defesa em profundidade (os ids já são uuid4 e não há listagem), não controle de acesso, e a carga de 500
  pedidos fica bem abaixo dele; um valor inválido faz a API não subir. O IP não entra
  no log. Custo: o estado é por processo; com várias réplicas cada uma conta o seu, e atrás de um proxy
  ou da porta publicada do Docker os clientes tendem a parecer um só.
- **Cabeçalhos de segurança em toda resposta** ([`test_api_headers.py`](../tests/test_api_headers.py)):
  `nosniff`, `X-Frame-Options: DENY` e `Referrer-Policy: no-referrer`, inclusive nos erros. As rotas
  JSON levam também `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`; `/docs` e
  `/redoc` ficam sem ela, porque carregam scripts do `cdn.jsdelivr.net` e um script inline.
  `/appointments` leva `Cache-Control: no-store`. Um erro não tratado é respondido pelo Starlette fora dos
  middlewares, então o `SecurityHeaders` manda ele mesmo esse `500`, sem stack trace.

### Keep-alive de 75 s

OCR, RAG e API mantêm uma conexão ociosa por 75 s, e não pelos 5 s padrão do uvicorn, o mesmo prazo do
pool do cliente: com os dois em 5 s, um POST enviado no instante do fechamento se perdia e a chamada MCP
esperava para sempre ([python-sdk#906](https://github.com/modelcontextprotocol/python-sdk/issues/906)).
Medido em chamadas espaçadas de 5 s: 15 travamentos em 2.880 com 5 s, nenhum com 75 s.

### Um `Dockerfile` multi-stage

Cada serviço instala só o que precisa, com versões fixadas em `requirements.txt` (o que roda) e
`requirements-dev.txt` (testes e checagens). O `agent` leva só o que `transpile` e `run` usam; pytest,
ruff, mypy, o Tesseract e os testes ficam no estágio `test`. Custo: o primeiro build monta quatro
imagens, e cinco com a de testes.

### Escala

O agente não guarda estado entre execuções, OCR e RAG são serviços separados e sem estado, e o SQLite
atende o mock. Para escalar: Postgres no lugar do SQLite e mais réplicas do `agent` e da `api`.

## Limites

### Com `--yes`, um exame injetado como linha comum é agendado

Sem `--yes`, a pessoa vê a lista inteira e confirma. Com ele, as regras são a única barreira. Numa
página que é só a lista, uma linha que só diz "Ferritina" é indistinguível de um pedido real e agenda
sozinha. Qualquer texto além da lista (fora campos, assinatura e cabeçalho da clínica) faz a página
inteira ser perguntada, inclusive numa página legítima: uma observação de preparo com outras palavras,
um cabeçalho que as regras não conhecem, um erro de OCR na letra de mão ou uma assinatura à mão que o
OCR lê como texto abaixo da lista ([medição](medicoes.md#lista-branca-por-página)).

**O que ainda passa, com `--yes`.** A regra só vê o texto que o Tesseract lê:

- um carimbo, uma nota na vertical ou em diagonal, um risco à mão ou uma letra clara demais deixam o
  exame como linha comum (a letra cinza que ele lê, menor ou mais clara que a da página, é pega);
- um teste da tinta que o OCR não leu (cor saturada, tinta escura fora das caixas das palavras) pegaria
  os 4 carimbos e notas giradas de uma revisão, mas disparou em 30 de 30 fotos e 110 de 120 manuscritas,
  e ficou de fora ([medição](medicoes.md#rodapé-formulário-nome-e-nome-mais-longo));
- acima da lista, uma linha tirada inteira conta como cabeçalho da clínica se parece um (clínica,
  hospital ou laboratório, endereço, telefone, CNPJ, CRM, "Receituário" ou data) e está separada da
  lista por um campo ("Paciente:"): uma nota que traga uma dessas palavras passa. Abaixo do 1º exame,
  qualquer texto que a máscara tira ou não reconhece faz a página ser perguntada, em qualquer língua,
  menos uma assinatura que é só nome, CRM e data;
- "TSH ferro" (um sobrenome que é palavra de exame, sem parênteses) conta como dois exames. Já um exame
  entre parênteses ou ao lado de um resultado na linha de outro ("Glicemia de jejum (HIV +)", "HIV
  positivo") é anotação e faz a linha ser perguntada;
- num formulário, só os exames marcados contam: se o OCR lê a marca de uns e não de outros, tudo é
  perguntado (`; formulário com marcas: só os marcados contam; confira`); se não lê nenhuma marca, a
  lista impressa parece um pedido comum;
- o Tesseract em PSM 11 não devolve blocos de texto, então o fim de um bloco sob "Já realizados:" vem do
  vão entre as caixas das linhas.

### A pergunta mostra a linha mascarada

Em "- Ferritina - pedido por engano", a máscara tira "pedido por engano" antes do modelo e da CLI; a
lista da confirmação diz `; o pedido tem outras palavras além do exame` e mostra
`lido "- Ferritina - [TEXTO_REMOVIDO]"`. Quem responde confere o papel.

### Exame lido certo, mas com leitura fraca, fica fora da pergunta

Abaixo de 0,70 o exame não entra na pergunta: é avisado em `baixa confiança` e não é agendado. No
`samples/pedido-realista.png`, 2 dos 4 exames são lidos certos, mas o OCR dá às linhas 68 e 60 de
confiança, abaixo do piso de 75 ([como aparece](como-rodar.md#testar-outra-imagem)). É conservador de
propósito: com uma imagem nova, o resultado pode ser um agendamento parcial com avisos.

### Filtros determinísticos não são prova contra ataques novos

A máscara de PII e a remoção de injeção são regras, testadas em corpora do próprio projeto (790 ataques
e 1.482 linhas legítimas, 1.429 distintas): é teste de regressão. A última barreira é o callback do
agendamento: só são agendados códigos que a busca devolveu, ancorados nas linhas lidas, no nome mais longo
do catálogo escrito ali ("Proteína C" dentro de "Proteína C reativa" é perguntada). A lista da
confirmação ainda pode mostrar um exame que o modelo associou mal (achado na linha de outro exame),
marcado `confira`: a pessoa o vê e decide. Sem a leitura do OCR
por linha, nada é agendado sem um "sim".

### Sem autenticação: na API e entre serviços

A API é o mock local do estudo: a porta publicada fica só em `127.0.0.1`, com limite de requisições por
IP; em produção, entraria OAuth2 ou uma chave de API. OCR, RAG, API e agente dividem uma rede interna
sem internet, e qualquer um deles chama os outros sem credencial. A API também está na rede padrão, que
tem saída para a internet, porque uma porta publicada no host exige uma rede não interna; o código dela não
chama nada fora. Um OCR comprometido por uma imagem hostil poderia agendar na API. Em produção, uma rede por par (agente↔OCR, agente↔RAG, agente↔API) e um
token por serviço.

### Cadeia de suprimentos, em parte

A imagem base é fixada por digest e as Actions da CI por commit; os pacotes Python, todos por versão
(`==`): os diretos em `requirements.txt` e `requirements-dev.txt`, os que eles puxam em
`constraints.txt`. Faltam: `--require-hashes`, versão fixa dos pacotes do `apt` (Tesseract) e
atualização automática do digest da base. O `/docs` carrega o Swagger UI do `cdn.jsdelivr.net` em versão
flutuante (`swagger-ui-dist@5`, o padrão do FastAPI; o ReDoc, `@2`), sem SRI nem CSP estrita (as rotas JSON
levam `default-src 'none'`).

### Confirmação forjada e pedido repetido

Uma resposta `adk_request_confirmation` forjada que o ADK recusa recebe 400 numa linha pelo
`python -m runtime.web`; no `adk web` puro, o próprio ADK responde 500. Nos dois, a chamada de
agendamento não roda. A `Idempotency-Key` vem da sessão: dentro dela, cada exame é agendado uma vez só,
mas a mesma foto enviada numa sessão nova agenda de novo (identificar o pedido pela imagem fica para
quando houver um id de pedido real).

### Histórico compactado por camada

Os commits agrupam o trabalho por camada (API, RAG, PII, OCR, runtime, transpilador, CLI, Docker,
testes, docs); as correções não aparecem uma a uma. [revisao.md](revisao.md) liga cada correção ao teste
que a trava.

Os limites medidos, com números: [medicoes.md](medicoes.md#limites-conhecidos); o que segue em aberto:
[revisao.md](revisao.md#ainda-em-aberto).
