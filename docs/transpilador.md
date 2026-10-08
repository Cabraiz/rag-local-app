# Transpilador: spec JSON → agente Google ADK

`python -m cli transpile specs/agent.json` lê a spec, valida, confere as ferramentas nos
servidores que respondem, gera `generated/agent.py` e importa o arquivo gerado para provar que ele
expõe um `root_agent` do ADK.

A spec descreve construções genéricas do ADK: os servidores (MCP por SSE ou OpenAPI, com qualquer
nome), os `LlmAgent`, o workflow que os roda (`SequentialAgent`, `ParallelAgent`, `LoopAgent` ou
nenhum) e os plugins do `App`, cada um com o caminho da classe e os seus `kwargs`. O transpilador não
conhece pedido, exame nem agendamento: o domínio está num plugin do runtime, o
[`BookingPlugin`](../runtime/plugin.py), que a spec declara. Cinco specs de exemplo transpilam e
importam nos testes:

| Spec | Agentes | O que faz |
|---|---|---|
| [`agent.json`](../specs/agent.json) | `extract` → `search` → `schedule` | lê, busca e agenda depois da confirmação da lista; a faixa do meio vai com aviso |
| [`agent-sem-confirmacao.json`](../specs/agent-sem-confirmacao.json) | o mesmo | `ask_from: null`: abaixo de 0,90 só avisa, sem a faixa com aviso na lista |
| [`listar-exames.json`](../specs/listar-exames.json) | `ler` → `listar` | só OCR e RAG, sem API: lista os exames com código e confiança, sem agendar |
| [`agendar-variante.json`](../specs/agendar-variante.json) | `ler_e_buscar` → `revisar` → `agendar` | outros nomes de servidor e de agente; um agente lê e busca, um agente sem ferramentas revisa a lista, outro agenda |
| [`exemplo-generico.json`](../specs/exemplo-generico.json) | `answer` | fora do domínio: um `LlmAgent` só, a raiz, com um servidor MCP de documentação e nenhum plugin; o arquivo gerado não importa nada da política de agendamento |

**Uma spec sua** vai na pasta `specs/`, que o serviço `agent` monta só para leitura: não precisa
reconstruir a imagem, e uma edição vale no próximo `transpile`
([como rodar](como-rodar.md#2-rodar-o-transpilador)):

```bash
docker compose run --rm agent python -m cli transpile specs/<sua-spec>.json --output generated/<seu-agente>.py
```

O arquivo só é trocado depois que o novo importa: ele é escrito ao lado, importado e então movido
por cima do anterior. Se falhar, o anterior fica como estava.

Para ver o código gerado sem rodar nada, abra [`exemplo-agent.py`](exemplo-agent.py): é a saída
exata do `transpile` para `specs/agent.json`, e um teste falha se ela ficar desatualizada.

## O que fica onde

- **A spec** diz o que varia: os servidores, os agentes (instrução, ferramentas, `output_key`,
  modelo), o workflow e os plugins com os seus `kwargs`. Na `specs/agent.json`, os `kwargs` do
  `BookingPlugin` dizem qual ferramenta lê, qual busca e qual agenda, e a política de agendamento
  (limiares, faixa de pergunta, pisos do OCR, `top_k`).
- **O transpilador** ([`transpiler/`](../transpiler/)) só confere o que é genérico: o schema estrito,
  os nomes, os placeholders, os hosts de `ALLOWED_HOSTS`, as classes que a spec nomeia (plugins e
  `output_schema`) e as ferramentas nos servidores que respondem. Ele não importa nada do domínio:
  só uma spec que declara o `BookingPlugin` o carrega. Um plugin com `Config` (um modelo pydantic dos
  seus `kwargs`), `check_spec` e `check_live` tem essas checagens chamadas pelo transpilador, com as
  mesmas mensagens `campo: motivo`; as chaves de estado que o `BookingPlugin` reserva são dele.
- **O código gerado** usa o Google ADK para os agentes e os toolsets (`LlmAgent`, `SequentialAgent`, `McpToolset`, `OpenAPIToolset`, `FallbackModel`, `App`), e a biblioteca versionada `runtime/` (abaixo); não é só ADK. Ele só declara o agente, em cerca de 110 linhas, nenhuma com mais de 120 colunas:
  - o modelo Gemini;
  - cada `LlmAgent` com a sua instrução, as ferramentas dele e o `output_key`: o `McpToolset`/`SseConnectionParams` do OCR e do RAG, ou o `LiveOpenAPIToolset`, uma camada fina que monta o `OpenAPIToolset` do ADK a partir do `/openapi.json` vivo da API;
  - o workflow (`SequentialAgent`) e o `App` retomável que o `adk run` e o `adk web` carregam (o `transpile` grava também um `__init__.py` ao lado, então a pasta é uma pasta de agente do ADK: [como rodar](como-rodar.md#4-rodar-com-adk-run-ou-adk-web));
  - os plugins do `App`, com os `kwargs` da spec; um plugin que recebe `servers` ganha também os endereços dos servidores da spec, e por isso os `kwargs` nomeiam uma ferramenta como `servidor.ferramenta`.

  Nenhum agente tem callback próprio, e não há função, classe nem regra de negócio no arquivo gerado.
- **O plugin de agendamento, [`runtime/plugin.py`](../runtime/plugin.py)**: o `BookingPlugin` é um
  `BasePlugin` do ADK e são os callbacks de [`callbacks.py`](../runtime/callbacks.py), que antes iam
  em cada agente. Pelo lugar do agente no pipeline: a raiz abre o pedido (`start_order`) e o fecha no
  relatório (`report`); toda chamada ao modelo passa por `before_model`, que põe a regra fixa sobre
  dados não confiáveis antes da instrução, e por `model_failed`, e toda ferramenta por `before_tool` e
  `after_tool`; numa spec que só lista, a última resposta passa por `review_list`.
- **A biblioteca de runtime do transpilador, [`runtime/`](../runtime/)** (versão 6 da interface, `API_VERSION`: o arquivo gerado confere essa versão na importação), guarda as regras, num código fixo e testado por conta própria. Ela tem duas partes, e um agente sem o `BookingPlugin` importa só a genérica (`adk.py`, `rede.py`):

  | Parte | Módulo | O que faz |
  |---|---|---|
  | genérica | [`adk.py`](../runtime/adk.py) | o modelo, o `McpToolset` e o `OpenAPIToolset` lido do contrato vivo |
  | genérica | [`rede.py`](../runtime/rede.py) | os hosts de `ALLOWED_HOSTS` (a mesma regra do transpilador), nenhum nome de servidor num endereço local ou de metadados, e os endereços conferidos fixos nos clientes do runtime |
  | genérica | [`web.py`](../runtime/web.py) | o `adk web` com o cabeçalho `Host` conferido |
  | agendamento | [`plugin.py`](../runtime/plugin.py) | o `BookingPlugin`: os callbacks abaixo num plugin do `App`, os papéis das ferramentas e a política vindos dos `kwargs`, e as checagens que o transpilador chama |
  | agendamento | [`confianca.py`](../runtime/confianca.py) | política de agendamento: faixas, pisos do OCR, um trecho do pedido por exame |
  | agendamento | [`callbacks.py`](../runtime/callbacks.py) | os callbacks do ADK: abrir o pedido (`start_order`), `before_model` (com a regra fixa sobre dados não confiáveis), `after_tool`, `before_tool`, o relatório final (`report`) e a falha do modelo (`model_failed`) |
  | agendamento | [`pedido.py`](../runtime/pedido.py) | o registro de cada pedido, fora do estado da sessão, com um limite de pedidos guardados |
  | agendamento | [`entrada.py`](../runtime/entrada.py) | o que da mensagem da pessoa chega ao modelo: o apelido da imagem, só texto e chamadas de ferramenta |
  | agendamento | [`confirmacao.py`](../runtime/confirmacao.py) | a lista, a pergunta `[s/N]` e a pausa da chamada |
  | agendamento | [`servidores.py`](../runtime/servidores.py) | as chamadas do próprio runtime aos servidores MCP: `check_image` e a busca do pedido inteiro |
  | agendamento | [`relatorio.py`](../runtime/relatorio.py) | a mensagem final, escrita com o que as ferramentas devolveram |
  | agendamento | [`reconcilia.py`](../runtime/reconcilia.py) | a conferência do pedido inteiro |

  E [`transpiler/live.py`](../transpiler/live.py) pergunta a cada servidor quais ferramentas ele tem
  (abaixo, em "Ferramentas conferidas nos servidores").

  Um teste garante que o gerado importa só `google.adk`, `google.genai` e `runtime`: uma regra
  copiada em cada arquivo gerado não seria testada, e uma regra num pacote é testada uma vez
  para todas as specs.

## Campos da spec

```json
{
  "name": "clinic_scheduler",
  "model": "gemini-3.5-flash",
  "fallback_model": "gemini-3.5-flash-lite",
  "servers": {
    "ocr": {"url": "http://ocr:8001/sse", "tools": ["extract_exam_text"]},
    "rag": {"url": "http://rag:8002/sse", "tools": ["search_exams"]},
    "api": {"openapi_url": "http://api:8000/openapi.json", "operations": ["create_appointment"]}
  },
  "agents": [
    {"name": "extract",  "instruction": "...",                "output_key": "exam_names",  "tools": ["ocr.extract_exam_text"]},
    {"name": "search",   "instruction": "... {exam_names?}",  "output_key": "exam_codes",  "tools": ["rag.search_exams"]},
    {"name": "schedule", "instruction": "... {exam_codes?}",  "output_key": "appointment", "tools": ["api.create_appointment"]}
  ],
  "workflow": "SequentialAgent",
  "plugins": [
    {
      "path": "runtime.plugin.BookingPlugin",
      "kwargs": {
        "read": "ocr.extract_exam_text", "search": "rag.search_exams", "book": "api.create_appointment",
        "min_confidence": 0.9, "ask_from": 0.7, "ocr_floor_line": 75, "ocr_floor_short": 85, "ocr_floor_synonym": 95, "top_k": 3
      }
    }
  ]
}
```

| Campo | Regra | Vira no código gerado |
|---|---|---|
| `name` | identificador: minúsculas, dígitos e `_`, começando por letra | `App(name=...)` e o nome do workflow |
| `model` | `gemini-<versão>`; numa execução, pode ser trocado com `-e GEMINI_MODEL=<modelo>` (validado pela mesma regra) | `gemini(...)`: o `Gemini` de cada agente sem `model` próprio, com 5 tentativas automáticas em `429`/`500`/`503` |
| `fallback_model` | opcional, `gemini-<versão>` | `gemini(<modelo>, fallback=<reserva>)`: a mesma requisição vai ao reserva se o principal responder `429`/`503` (`cli run`, `adk run` e `adk web`) |
| `servers.<nome>` | de 1 a 10; nome identificador, escolhido pela spec (`ocr`, `leitor`, `docs`...); cada servidor é **ou** MCP **ou** OpenAPI; o host da URL precisa estar em `ALLOWED_HOSTS` (abaixo) | um toolset por servidor usado por um agente |
| `servers.<nome>.url` + `tools` | servidor MCP por SSE: `http://host:porta/sse` e os nomes das ferramentas que os agentes podem chamar | `McpToolset(SseConnectionParams(url=...), tool_filter=[...])` |
| `servers.<nome>.openapi_url` + `operations` | API descrita por OpenAPI: `http://host:porta/openapi.json` e os `operationId` que os agentes podem chamar | `LiveOpenAPIToolset` → `OpenAPIToolset` montado desse contrato, só com essas operações |
| `agents[]` | de 1 a 10 | um `LlmAgent` cada |
| `agents[].name` | identificador, sem repetir, sem nome reservado nem de função do Python (`print`) | `LlmAgent(name=...)` e o nome da variável no gerado |
| `agents[].instruction` | texto; `{chave}` só pode citar o `output_key` de um agente que rodou antes, e `{chave?}` também, que o ADK troca por nada quando esse agente não escreveu nada (um pedido sem exame) | `instruction`; com o `BookingPlugin`, cada chamada ao modelo leva antes a regra fixa de segurança (abaixo) |
| `agents[].output_key` | identificador, sem repetir; com o `BookingPlugin`, fora das chaves que ele usa no estado | `output_key` (estado compartilhado) |
| `agents[].output_schema` | opcional, `modulo.Classe`: um modelo pydantic num dos pacotes permitidos | `output_schema`: o ADK confere a resposta do agente contra o modelo, também com ferramentas |
| `agents[].model` | opcional, `gemini-<versão>` | o `Gemini` só desse agente |
| `agents[].tools` | `servidor.ferramenta` de um servidor declarado; pode ficar vazio (um agente que só reescreve a lista do anterior) | `tools=[...]` do agente, só com essas ferramentas |
| `workflow` | `SequentialAgent` (padrão: em ordem), `ParallelAgent` (ao mesmo tempo: nenhum agente lê a saída de outro), `LoopAgent` (em ordem, até `max_iterations` vezes) ou `null` (um agente só, que é a raiz) | `root_agent` |
| `max_iterations` | de 1 a 10, obrigatório com `LoopAgent` e só com ele | `LoopAgent(max_iterations=...)` |
| `plugins[]` | até 10; `path` é `modulo.Classe`, uma subclasse de `BasePlugin` do ADK num dos pacotes permitidos (`runtime` e `google.adk.plugins`); `kwargs`, um objeto JSON com nomes identificadores | `App(plugins=[Classe(**kwargs)])` |

Qualquer campo fora dessa lista é rejeitado (`extra="forbid"`). Os pacotes de onde uma spec carrega
uma classe (plugin ou `output_schema`) são uma lista no código ([`spec.py`](../transpiler/spec.py)): uma
spec nunca faz o transpilador importar outro módulo.

**Um agente fora do domínio.** [`exemplo-generico.json`](../specs/exemplo-generico.json) é um
assistente de documentação: um `LlmAgent` só (`"workflow": null`), com um servidor MCP `docs` e
nenhum plugin. O servidor não é do compose, então quem implanta o permite:

```bash
docker compose run --rm -e ALLOWED_HOSTS=docs:8010 agent python -m cli transpile specs/exemplo-generico.json --output generated/docs.py
```

O arquivo gerado não tem nenhum callback, plugin ou palavra do domínio de agendamento, e nenhuma regra
entra antes da instrução. Transpilar a spec ou importar o gerado carrega do `runtime` só `adk.py` e
`rede.py` (testes em
[`test_spec_generica.py`](../tests/test_spec_generica.py)). O `cli run` lê um pedido em imagem e
precisa do `BookingPlugin`; este agente roda com `adk run` e `adk web`.

**Hosts permitidos: quem decide é quem implanta, não a spec.** A spec escolhe os servidores, mas o
host de cada URL precisa estar em `ALLOWED_HOSTS`, uma variável de ambiente (lista separada por
vírgula). Sem ela (ou vazia, ou só com vírgulas), valem exatamente os três serviços do compose, cada
um na sua porta: `ocr:8001,rag:8002,api:8000`; `localhost`, `127.0.0.1` e outras portas ficam de fora
(a API do Docker em `localhost:2375`, por exemplo). Uma entrada `host` aceita qualquer porta;
`host:porta`, só aquela; uma entrada que não é nenhum dos dois é um erro do `transpile`, nunca ignorada.
Assim uma spec não aponta o agente para outro host ou porta (SSRF: `169.254.169.254`, a rede interna,
um serviço local), e um outro ambiente não exige mudar o código: basta
`ALLOWED_HOSTS=ocr:8001,rag:8002,api:8000,clinica.interna:8443` no `.env`. A checagem roda no
`transpile`, de novo no `cli run`, que lê a spec outra vez, e uma terceira vez quando o `agent.py`
é importado: os toolsets da biblioteca de runtime (`McpToolset`, `LiveOpenAPIToolset`) recusam um
host fora de `ALLOWED_HOSTS`, então um `agent.py` gerado antes de uma mudança na variável, ou editado
à mão, não importa. O `cli run` também confere que o `agent.py` é exatamente o que a spec gera hoje,
e para se não for. `ALLOWED_HOSTS` compara nomes. No `cli run`, cada nome é resolvido uma vez, antes da
primeira requisição, e um nome que aponta para um endereço local ou de metadados (`127.0.0.1`,
`169.254.169.254`, `fd00:ec2::254`…) para a execução; os endereços conferidos ficam fixados até o
fim dela nos clientes que o runtime cria (o `McpToolset`, o `OpenAPIToolset` do ADK e as consultas do
próprio runtime recebem um cliente `httpx` que conecta só a eles), e um nome que não resolveu fica sem nenhum
([regra e limites](arquitetura.md#segurança-em-detalhe)).

**Ferramentas conferidas nos servidores.** No `transpile`, cada servidor que responde em até 3 s é
consultado: o MCP pelo `list_tools`, a API pelo `/openapi.json`. Uma ferramenta que ele não tem é
um erro já no `transpile`, e o `OK` ganha a linha `Ferramentas conferidas nos servidores: ocr, rag, api`.
Um servidor fora do ar (um `transpile` offline, a CI) fica com a lista declarada na spec, sem linha
a mais. Um servidor que responde, mas não com JSON (um `/openapi.json` malformado), é um erro, e um
defeito do próprio transpilador nunca passa por servidor fora do ar; o `cli run` pergunta de novo a todos antes de chamar o Gemini, então uma ferramenta errada
na prática não chega a uma execução. No `cli run`, todo servidor precisa responder e listar as
ferramentas: um que responde ao GET mas não termina o `list_tools` (ou não serve um `/openapi.json`)
para a execução com `servers.<nome>: <url> respondeu, mas não listou as ferramentas`. A mesma consulta passa pelo `check_live` de cada plugin: o
do `BookingPlugin` confere que a ferramenta de `read` recebe `filename`, a de `search` recebe `query`
e `top_k`, e o contrato da operação de `book` (abaixo).

**`fallback_model`, por requisição.** No código gerado, o modelo é o `FallbackModel` do ADK: uma requisição que o principal recusa com `429`/`503` vai, igual, ao reserva, no `cli run`, no `adk run` e no `adk web`, que rodam o mesmo `app` sem mudança. Não agenda em dobro nem pergunta de novo: a chamada que se repete é ao modelo, e uma chamada ao modelo que falhou não rodou nenhuma ferramenta; as respostas e a `Idempotency-Key` seguem no registro do pedido. Com ele na spec, o principal não repete `429` nem `503` (só `500`): a troca para o reserva é imediata, em vez de esperar cerca de 1 minuto de tentativas.

**O `BookingPlugin`: quem lê, quem busca e quem agenda.** A política de agendamento
([`runtime/`](../runtime/)) não conhece nomes de servidor nem de ferramenta: os `kwargs` do plugin
dizem qual ferramenta faz cada papel, como `servidor.ferramenta`.

| Papel | Contrato que o runtime usa | Para quê |
|---|---|---|
| `read` | ferramenta MCP que recebe `filename` e devolve `lines`, `line_confidence` e `pii_masked` | o pedido lido; alimenta a confiança (aderência à linha e leitura do OCR) |
| `search` | ferramenta MCP que recebe `query` e `top_k` e devolve `[{code, name, score}]` | os códigos possíveis; alimenta a confiança (score) |
| `book` | operação OpenAPI que recebe `{"exams": [...]}` e o `Idempotency-Key` | o agendamento, só com os códigos que passaram na política |

As regras abaixo eram do transpilador e agora são do plugin (`check_spec`), chamadas pelo
`transpile` com as mesmas mensagens:

- **O plugin roda em ordem:** pede `"workflow": "SequentialAgent"`.
- **Quem agenda** é o agente que tem a ferramenta de `book` nas suas `tools`. Só um agente pode tê-la
  (um pedido, um agendamento), e as ferramentas de `read` e `search` precisam estar em agentes
  anteriores, num agente só ou em dois: a trava de código só agenda o que a busca achou, medido
  contra o que foi lido.
- **`book` pede `read` e `search`,** e `search` pede `read`: sem eles não há confiança a medir.
- **Sem `book`, a spec lista em vez de agendar.** O último agente responde com
  `"output_schema": "runtime.plugin.ListedExams"` (os exames, cada um com `code` e `name`), que o ADK
  confere; essa resposta passa pela mesma política, sem pergunta: o plugin chama `review_list` depois do
  último agente, e a CLI mostra cada exame com a sua confiança (`confira` na faixa do meio), os que
  ficaram de fora e os códigos que nenhuma busca devolveu. Nada é enviado a uma API.
  - Na listagem, os limiares (opcionais; os valores de `listar-exames.json` são os padrões) só
    definem as faixas de confiança: o que sai como exame lido, o que sai como `confira` e o que fica
    de fora. Nada é agendado.
- **Toda ferramenta de um agente tem papel, e uma operação de API só chega a um agente como
  `book`**, a única chamada que o runtime confere antes de enviar. O `transpile` recusa uma
  ferramenta sem papel e qualquer outra operação de API: uma 2ª operação, um 2º servidor OpenAPI, a API
  renomeada sem mudar `book` ou uma API numa spec que só lista. O runtime também recusa, em tempo de
  execução, qualquer ferramenta fora dos papéis e, no agendamento, qualquer campo além de `exams` e da
  nossa `Idempotency-Key`. Com a API no ar, o `transpile` confere o contrato da operação de `book`: ela
  recebe `Idempotency-Key` e um corpo só com `exams` (`additionalProperties: false`), cada exame com
  `code` (o `name` é opcional: a API do projeto não o guarda e devolve o nome do catálogo). Faltar um
  papel nunca quer dizer "sem checagem".
- **Os `kwargs` são conferidos de novo ao criar o plugin:** um `agent.py` editado à mão também não
  afrouxa a política.

**O que o `cli run` roda.** Specs com o `BookingPlugin` com `read` e `search`: com `book`, agenda; sem
`book`, lista. Uma spec sem eles (um pipeline que não lê um pedido em imagem, como o exemplo genérico)
transpila e importa, roda com `adk run` e `adk web`, e o `cli run` diz que não a roda:

```bash
docker compose run --rm agent python -m cli transpile specs/listar-exames.json --output generated/listar.py
docker compose run --rm agent python -m cli run --image pedido.png --spec specs/listar-exames.json --agent generated/listar.py
```

**As garantias medidas valem para a política padrão.** "Nenhum exame errado agendado sem confirmação"
foi medido com 0,90 / 0,70 / 75-85-95, e por isso esses valores são também os pisos: uma spec pode deixar a
política mais rígida (subir um valor, ou `"ask_from": null`), nunca mais frouxa. Abaixo de um piso, o
`transpile` para e nomeia o campo e o mínimo, por exemplo `plugins.0.kwargs.min_confidence: deve ser no mínimo 0,9,
o valor medido: uma spec pode deixar a política mais rígida, nunca mais frouxa` (a 0,80, um "- GA" mal lido
seria agendado como IgA). Baixar um piso pede uma nova medição, com `tests/load/manuscritos.py`, e uma
mudança no código, não na spec.

Uma 2ª spec de exemplo, [`specs/agent-sem-confirmacao.json`](../specs/agent-sem-confirmacao.json),
usa `"ask_from": null` e outra instrução de agendamento: o que fica abaixo de 0,90 só é avisado,
nunca vai para a lista (a confirmação final continua). Ela transpila e roda no runner real do ADK, com um modelo roteirizado no lugar do Gemini
([`test_confirmacao_nativa.py`](../tests/test_confirmacao_nativa.py)).

## Validação e mensagens de erro

A validação tem até quatro etapas. Os problemas saem um por linha, no formato `Erro: campo: motivo`,
sem stack trace, e o comando termina com código 2:

1. **JSON:** o JSON malformado (o primeiro erro de sintaxe) ou as chaves duplicadas (todas).
2. **Schema:** todos os problemas de uma vez: campo faltando, campo a mais, tipo, formato ou limite errado, ferramenta escrita sem o servidor.
3. **Regras entre campos:** todos juntos, e só quando o schema está válido:
   - host fora de `ALLOWED_HOSTS`, servidor com os dois tipos (ou nenhum), ferramenta em dois servidores;
   - ferramenta de servidor não declarado, ou não declarada no servidor;
   - nome ou `output_key` repetido;
   - placeholder (`{chave}` ou `{chave?}`) de um agente que não existe ou não rodou antes;
   - `output_schema` fora dos pacotes permitidos, que não existe ou não é um modelo pydantic;
   - workflow que não serve aos agentes;
   - plugin fora dos pacotes permitidos, que não existe ou não é um `BasePlugin`; `kwargs` fora do
     `Config` do plugin; e as regras do próprio plugin (`check_spec`): no `BookingPlugin`, papéis em
     ferramentas que nenhum agente usa, do tipo errado, `book` sem `read` e `search`, limiares
     incoerentes, agendamento antes da leitura e da busca, `output_key` numa chave que ele usa no
     estado e uma listagem que não termina em `ListedExams`.
4. **Servidores que respondem** (só no `transpile` e no `cli run`): ferramenta que o servidor não
   tem, ou, pelo `check_live` do plugin, ferramenta de papel que não recebe o que o runtime envia.

Uma etapa só aparece quando a anterior passa. Saídas reais, cada uma com uma mudança em `specs/agent.json`:

| Spec | Mensagem |
|---|---|
| `"campo_inexistente": true` na raiz | `Erro: campo_inexistente: campo não permitido` |
| uma segunda chave `"name"` | `Erro: name: chave duplicada no JSON` |
| URL do MCP sem `/sse` | `Erro: servers.ocr.url: formato inválido: esperado http://host:porta/sse` |
| URL do MCP com outro host | `Erro: servers.ocr.url: host "169.254.169.254:80" fora de ALLOWED_HOSTS (ocr:8001, rag:8002, api:8000); quem implanta pode incluí-lo em ALLOWED_HOSTS` |
| `ALLOWED_HOSTS=ocr:abc` | `Erro: ALLOWED_HOSTS: "ocr:abc" não é host nem host:porta (ex.: ocr:8001,clinica.interna:8443)` |
| servidor com `url` e `openapi_url` | `Erro: servers.rag: declare url e tools (servidor MCP) ou openapi_url e operations (API OpenAPI), um dos dois` |
| a mesma ferramenta em dois servidores | `Erro: servers: "search_exams" está em ocr e rag; um nome de ferramenta, um servidor` |
| `/openapi.json` que não é JSON (API no ar) | `Erro: servers.api.openapi_url: http://api:8000/openapi.json respondeu, mas não com JSON (JSONDecodeError)` |
| operação que a API não tem (API no ar) | `Erro: servers.api.operations: "nao_existe" não existe neste servidor (use create_appointment, get_appointment, health)` |
| busca que não recebe `top_k` (servidor no ar) | `Erro: plugins.0.kwargs.search: "rag.search_exams" não recebe top_k, que o runtime envia` |
| `book` sem `search` | `Erro: plugins.0.kwargs.book: agendar pede read e search: a confiança de um exame vem da leitura do pedido e da busca no catálogo` |
| `book` numa ferramenta MCP | `Erro: plugins.0.kwargs.book: "rag.search_exams" precisa ser uma operação de uma API OpenAPI` |
| papel numa ferramenta que nenhum agente usa | `Erro: plugins.0.kwargs.book: "api.get_appointment" não está nas tools de nenhum agente` |
| operação de API fora de `book` | `Erro: agents.2.tools: "clinica.create_appointment" é uma operação de API e só a de plugins.0.kwargs.book chega a um agente, porque só ela passa pela checagem dos códigos antes da chamada; declare-a em plugins.0.kwargs.book ou tire-a do agente` |
| ferramenta sem o servidor | `Erro: agents.0.tools: "extract_exam_text": use servidor.ferramenta, ex.: ocr.extract_exam_text` |
| servidor `ocr` removido de `servers` | `Erro: agents.0.tools: "ocr.extract_exam_text" usa o servidor "ocr", que não está em servers` |
| placeholder de um agente que vem depois | `Erro: agents.1.instruction: {appointment} não é saída de um agente anterior` |
| `output_key` repetido | `Erro: agents.2.output_key: "exam_codes" já é usado por outro agente` |
| nome de agente repetido | `Erro: agents.1.name: "extract" já é usado (ou é reservado)` |
| agendamento como 1º agente | `Erro: agents.0.tools: api.create_appointment só agenda códigos achados no catálogo: antes dele, agentes anteriores precisam usar ocr.extract_exam_text e rag.search_exams` (mais o placeholder `{exam_codes}` sem agente anterior) |
| `ask_from` 0,95 | `Erro: plugins.0.kwargs.ask_from: deve ser menor que min_confidence (ou null, sem pergunta)` |
| `ocr_floor_short` 99 | `Erro: plugins.0.kwargs.ocr_floor_synonym: use ocr_floor_line <= ocr_floor_short <= ocr_floor_synonym (uma sigla pede leitura mais clara)` |
| `top_k` 0 | `Erro: plugins.0.kwargs.top_k: deve ser no mínimo 1` |
| `min_confidence` 0,8 | `Erro: plugins.0.kwargs.min_confidence: deve ser no mínimo 0,9, o valor medido: uma spec pode deixar a política mais rígida, nunca mais frouxa` |
| `ocr_floor_line` 0 | `Erro: plugins.0.kwargs.ocr_floor_line: deve ser no mínimo 75, o valor medido: …` (o mesmo para `ask_from` abaixo de 0,7, `ocr_floor_short` abaixo de 85 e `ocr_floor_synonym` abaixo de 95) |
| `"min_confidence": "0.9"`, entre aspas | `Erro: plugins.0.kwargs.min_confidence: deve ser um número (veio como texto: escreva sem aspas)` |
| `{{exam_names}}` numa instrução | `Erro: agents.1.instruction: {{exam_names}} não é texto literal: o ADK não tem escape para chaves e lê isso como o placeholder {exam_names}; para citar o nome, escreva-o sem chaves` |
| um 4º agente que também agenda | `Erro: agents.3.tools: api.create_appointment já está em outro agente: um pedido, um agendamento` |
| `model` que não é Gemini | `Erro: model: formato inválido: esperado gemini-<versão>` |
| `"workflow": "ParallelAgent"` com o `BookingPlugin` | `Erro: workflow: o BookingPlugin lê, busca e agenda em ordem: use SequentialAgent` |
| `LoopAgent` sem `max_iterations` | `Erro: max_iterations: obrigatório com LoopAgent, e só com ele` |
| plugin de outro pacote | `Erro: plugins.0.path: "os.path.Join" fora dos pacotes de plugins (runtime, google.adk.plugins)` |
| plugin que não existe | `Erro: plugins.0.path: "runtime.plugin.Nope" não é uma classe de plugin do ADK (BasePlugin)` |
| JSON malformado | `Erro: JSON inválido (linha 60, coluna 3): Expecting ',' delimiter` |

Chaves duplicadas são detectadas no parse (`object_pairs_hook`), porque o `json` padrão
manteria só a última silenciosamente.

- **Tipos estritos:** um valor de outro tipo JSON é recusado, nunca convertido: `"0.9"` é texto, `3.5` não é inteiro, `true` não é número.
- **BOM:** um arquivo salvo com BOM UTF-8 (comum em editores do Windows) é lido normalmente.
- **`NaN` e `Infinity`:** o `json` do Python os aceita, mas não são JSON: são recusados.
- **Chaves duplas:** o ADK não tem escape para chaves e trata `{{nome}}` como o placeholder `{nome}`; por isso a spec recusa chaves duplas em volta de um nome. Em volta de outro texto (`{{"code": 1}}`), elas ficam como texto.
- **Mensagens:** todas em português; um erro de schema sem tradução sai como `valor inválido (<tipo do erro>)`.

## Geração

[`transpiler/generator.py`](../transpiler/generator.py) preenche
[`agent_template.py.tmpl`](../transpiler/agent_template.py.tmpl) com `string.Template`, com um bloco
por agente da spec. Toda chamada sai com um argumento por linha, então o gerador não decide onde
quebrar uma linha; só uma instrução longa vira literais adjacentes de até 120 colunas. Cada valor da
spec entra como literal Python via `repr()`, então nenhum valor da spec é executado como código. Em
seguida o arquivo passa por `py_compile` e é importado. O `transpile` só imprime `OK` se `root_agent`
existir:

```text
OK: generated/agent.py gerado e importado; root_agent "clinic_scheduler" (SequentialAgent: extract -> search -> schedule)
```

Trecho do código gerado ([inteiro](exemplo-agent.py)):

```python
from runtime import LiveOpenAPIToolset, McpToolset, gemini, require_api
from runtime.plugin import BookingPlugin

MODEL = gemini('gemini-3.5-flash', fallback='gemini-3.5-flash-lite')

extract = LlmAgent(
    name='extract',
    model=MODEL,
    instruction=(
        'Você lê pedidos médicos fictícios. Chame extract_exam_text ...'
    ),
    tools=[
        McpToolset(
            connection_params=SseConnectionParams(url='http://ocr:8001/sse'),
            tool_filter=['extract_exam_text'],
        ),
    ],
    output_key='exam_names',
)
# search e schedule seguem o mesmo padrão; schedule usa LiveOpenAPIToolset → OpenAPIToolset

root_agent = SequentialAgent(
    name='clinic_scheduler',
    sub_agents=[extract, search, schedule],
)
app = App(
    name='clinic_scheduler',
    root_agent=root_agent,
    plugins=[
        BookingPlugin(
            servers={
                'ocr': 'http://ocr:8001/sse',
                'rag': 'http://rag:8002/sse',
                'api': 'http://api:8000/openapi.json',
            },
            read='ocr.extract_exam_text',
            search='rag.search_exams',
            book='api.create_appointment',
            min_confidence=0.9,
            # ... os outros limiares da spec
        ),
    ],
    resumability_config=ResumabilityConfig(is_resumable=True),
)
```

`LiveOpenAPIToolset` busca o `/openapi.json` na primeira chamada, e não no import, para que o
`transpile` funcione com a API fora do ar. Ele também acrescenta a URL base, que o FastAPI
não declara em `servers`.

## Segurança no código gerado

Estas proteções ficam no `runtime/`, fora da spec, e por isso nenhuma spec consegue removê-las:

- **O registro do pedido:** o que a política usa (a imagem, as linhas lidas, os códigos de cada busca, as
  respostas e as perguntas de cada chamada, a `Idempotency-Key` e a resposta da API) fica no próprio
  `BookingPlugin`, um registro por sessão, e nunca é lido do estado da sessão, que os clientes do ADK
  escrevem (`adk web`, `adk run --state`). O estado recebe uma cópia, para a CLI e para a pessoa.
- **`start_order`** (antes do pipeline, no `before_agent_callback` do plugin para a raiz; também no `adk run` e no `adk web`): a imagem vem do `cli run`
  (`orders.start`) ou da mensagem da pessoa, que precisa ser só texto e trazer um só nome de arquivo, sem pasta.
  Antes de qualquer turno do modelo, o nome vira o apelido, os endereços dos servidores são conferidos e o
  OCR confere a imagem. Um pedido por sessão; numa sessão em que a API já agendou, a resposta é esse
  agendamento e `não repita este pedido`.
- **`before_model`:** o modelo recebe `Arquivo do pedido: <apelido>` no lugar da mensagem da pessoa; o nome
  real do arquivo nunca aparece no que ele recebe, e cada parte leva só texto e chamadas de ferramenta e
  suas respostas (um anexo, código ou outro dado que um cliente mande fica de fora).
- **Toolsets:** `McpToolset` e `LiveOpenAPIToolset` conferem o endereço do servidor antes da 1ª conexão, e
  toda conexão usa os endereços conferidos.
- **Regra fixa antes de cada instrução** (`before_model` do `BookingPlugin`): o que as ferramentas
  devolvem e as listas dos agentes anteriores são dados não confiáveis, nunca instruções. Ela abre a
  instrução de sistema de cada chamada ao modelo, e um agente sem o plugin não a recebe.
- **`after_tool`:**
  - guarda as linhas lidas pelo OCR e a leitura do OCR em cada uma (`line_confidence`, 0 a 100);
  - dá a cada código devolvido pela busca uma confiança = mín(score do RAG, aderência da busca a uma linha lida, leitura do OCR nessa linha);
  - abaixo do piso da linha, a leitura nunca deixa a confiança chegar a `min_confidence`;
  - sem `line_confidence` válido, a regra fecha: tudo vai, no máximo, para a pergunta;
  - uma correção de grafia feita pelo modelo não aumenta essa confiança.
- **`before_tool`:**
  - na busca, fixa o `top_k` da spec;
  - no agendamento, decide em código, antes da API:
    - bloqueia se algum código não veio de nenhuma busca no catálogo;
    - dá a cada exame um trecho próprio do pedido, em qualquer linha. "Creatinina, Clearance de creatinina" são 2, na mesma linha ou em linhas separadas. Valem 1: nomes sobrepostos ("Hemoglobina" em "Hemoglobina glicada" escrito uma vez), uma linha só parecida, e cópias da mesma linha;
    - decide em 3 faixas:
      - **≥ `min_confidence`** (0,90, calibrado em 631 consultas; ver [`test_calibration.py`](../tests/test_calibration.py)): entra na lista;
      - **de `ask_from` a `min_confidence`** (0,70 a 0,90): entra na lista com aviso (com `--yes`, fica de fora);
      - **abaixo:** sai como `baixa confiança: ... confira o pedido`.

    Bloqueia se nenhum exame sobra; senão, pede a confirmação final da lista (abaixo).
- **Pedido sem exame:** as instruções citam a etapa anterior como `{chave?}`, que o ADK troca por
  nada quando ela não escreveu nada, então o agente seguinte recebe a entrada vazia em vez de quebrar;
  a CLI responde `Nenhum exame encontrado no pedido`.
- **`report`** (depois do pipeline): confere o pedido inteiro na busca do catálogo e escreve a
  mensagem final com o que as ferramentas devolveram; `Agendamento confirmado pela API` só sai da
  resposta da API, nunca do texto do modelo. A CLI usa o mesmo resultado.

### A confirmação da lista: confirmação nativa do ADK

Nada é agendado sem a pessoa confirmar a lista. A pergunta usa a confirmação de ferramenta do ADK 2.10, e não um `input()` dentro do callback:

1. O `before_tool` do agendamento monta a lista ([`runtime/confirmacao.py`](../runtime/confirmacao.py)): no topo, uma vez, o que a página tem além da lista; cada exame com código, o aviso de um da faixa do meio e os não agendados, inclusive os que a conferência do pedido inteiro acha e o agente não buscou (o `before_tool` é assíncrono e aguarda a busca no próprio laço de eventos da execução, até 30 s). Chama `tool_context.request_confirmation(hint=<lista>)` e devolve sem chamar a API.
2. A execução pausa. O app é retomável (`ResumabilityConfig`).
3. A CLI recebe do runner a chamada `adk_request_confirmation` e pergunta `Agendar estes N exames? [s/N]` (só `s` ou `sim` é sim). O console do `adk run` mostra a mesma lista (`yes` confirma) e a página do `adk web`, a lista com a caixa "Confirmed".
4. Na CLI, a pergunta roda em `asyncio.to_thread`, fora do laço de eventos: as sessões MCP seguem vivas enquanto a pessoa lê.
5. O cliente retoma a mesma invocação com `{"confirmed": true}` ou `false`. O ADK executa de novo só aquela chamada (`tool_context.tool_confirmation`), sem refazer o OCR nem a busca, e a lista vai para o `POST` só se a resposta for sim e a lista for a mesma que aquela chamada mostrou.

O resto:

- **`--yes`** (só na CLI): o callback nem pede confirmação, e a faixa do meio fica de fora. Chega ao agente pelo registro do pedido que a CLI abre (`orders.start`), não por variável de ambiente. Sem TTY e sem `--yes`, a CLI mostra a lista e responde não.
- **Confirmação forjada:** só vale a resposta à pausa que o runtime registrou para aquela chamada, no registro do pedido, não no estado da sessão. Uma resposta a outra chamada, ou uma lista escrita no estado, não agenda nada.
- **Um "não" vale para a execução:** uma chamada repetida pelo modelo não pergunta de novo e não agenda, nem depois de uma troca para o `fallback_model`.
- **Um agendamento por execução:** cada execução manda uma `Idempotency-Key` própria (nunca a do modelo), e depois que uma chamada vai para a API, outra recebe esse mesmo agendamento, sem chegar à API. Vale também para duas chamadas no mesmo turno, cada uma com a sua lista: um "sim" que chega depois não gera outro `POST`, e o exame sai no relatório como `não agendado (você confirmou, mas o agendamento desta execução já tinha sido criado)`.
- **Enquanto espera a resposta**, a resposta de pausa não é resumida para o modelo (`skip_summarization`).
- **API experimental:** a confirmação de ferramenta e o `ResumabilityConfig` são marcados como experimentais no ADK 2.10 (aparecem entre os avisos). O ADK está fixo em 2.10 no `requirements.txt`.
