# Transpilador: spec JSON → agente Google ADK

**O que tem aqui / quando ler.** Leia para escrever ou validar uma spec, ou para entender o código gerado:

- o que o `transpile` faz e as cinco specs de exemplo;
- o que fica na spec, no transpilador, no código gerado e no `runtime/`;
- cada campo da spec e cada erro de validação, com a mensagem real;
- como o arquivo é gerado e as proteções que nenhuma spec remove, inclusive a confirmação da lista.

`python -m cli transpile specs/agent.json`:

1. lê e valida a spec;
2. confere as ferramentas nos servidores que respondem;
3. gera `generated/agent.py`;
4. importa o arquivo gerado, para provar que ele expõe um `root_agent` do ADK.

A spec descreve construções genéricas do ADK, cada uma com o caminho da classe e os seus `kwargs`:

- os servidores: MCP por SSE ou OpenAPI, com qualquer nome;
- os `LlmAgent`;
- o workflow que os roda: `SequentialAgent`, `ParallelAgent`, `LoopAgent` ou nenhum;
- os plugins do `App`.

O transpilador não conhece pedido, exame nem agendamento. O domínio está num plugin do runtime, o
[`BookingPlugin`](../runtime/plugin.py), que a spec declara.

Cinco specs de exemplo transpilam e importam nos testes:

| Spec | Agentes | O que faz |
|---|---|---|
| [`agent.json`](../specs/agent.json) | `extract` → `search` → `schedule` | lê, busca e agenda depois da confirmação da lista |
| [`agent-sem-confirmacao.json`](../specs/agent-sem-confirmacao.json) | o mesmo | `ask_from` ([os campos](#o-bookingplugin-quem-lê-quem-busca-e-quem-agenda)) `null`: abaixo de 0,90 só avisa, sem a [faixa com aviso](glossario.md#faixas-e-pisos) |
| [`listar-exames.json`](../specs/listar-exames.json) | `ler` → `listar` | lista os exames, sem agendar |
| [`agendar-variante.json`](../specs/agendar-variante.json) | `ler_e_buscar` → `revisar` → `agendar` | um agente sem ferramentas revisa a lista antes de agendar |
| [`exemplo-generico.json`](../specs/exemplo-generico.json) | `answer` | fora do domínio ([detalhe](#um-agente-fora-do-domínio)) |

A `listar-exames.json` usa só OCR e RAG, sem API. A `agendar-variante.json` também troca os nomes de agente e de
servidor.

**Uma spec sua** vai na pasta `specs/`, sem reconstruir a imagem. Os comandos e o que acontece com o arquivo anterior:
[como-rodar.md](como-rodar.md#2-rodar-o-transpilador).

**Saída:** sem `--output`, o arquivo vai para `generated/agent.py`. Para outro agente, use
`--output generated/<nome>/agent.py`. Nos dois casos, o `transpile` põe um `__init__.py` ao lado, e a pasta serve ao
`cli run --agent` e ao `adk run`.

Para ver o código gerado sem rodar nada, abra [`exemplo-agent.py`](exemplo-agent.py): é a saída
exata do `transpile` para `specs/agent.json`, e um teste falha se ela ficar desatualizada.

**Índice:** [O que fica onde](#o-que-fica-onde) · [Campos da spec](#campos-da-spec) · [Validação e mensagens de erro](#validação-e-mensagens-de-erro) · [Geração](#geração) · [Segurança no código gerado](#segurança-no-código-gerado)

## O que fica onde

- **A spec** diz o que varia: os servidores, os agentes, o workflow e os plugins com os seus `kwargs`. Cada agente tem
  instrução, ferramentas, `output_key` e modelo.
  - Na `specs/agent.json`, os `kwargs` do `BookingPlugin` dizem qual ferramenta lê, qual busca e qual agenda.
  - Eles também trazem a política de agendamento: limiares, faixa com aviso, pisos do OCR e `top_k`
    ([cada campo](#o-bookingplugin-quem-lê-quem-busca-e-quem-agenda)).
- **O transpilador** ([`transpiler/`](../transpiler/)) só confere o que é genérico:
  - o schema estrito, os nomes e os [placeholders](#campos-da-spec);
  - os hosts de [`ALLOWED_HOSTS`](#hosts-permitidos-quem-decide-é-quem-implanta-não-a-spec);
  - as classes que a spec nomeia (plugins e `output_schema`);
  - as ferramentas nos servidores que respondem.

  Ele não importa nada do domínio: só uma spec que declara o `BookingPlugin` o carrega.
- **As checagens de um plugin:** um plugin pode ter `Config` (um modelo pydantic dos seus `kwargs`), `check_spec` e
  `check_live`. O transpilador chama essas checagens, com as mesmas mensagens `campo: motivo`. As chaves de estado que
  o `BookingPlugin` reserva são dele.
- **O código gerado** instancia classes do Google ADK (`LlmAgent`, `SequentialAgent`, `SseConnectionParams`, `App`) e
  subclasses finas delas que vêm da biblioteca versionada `runtime/` (abaixo).
  - De `runtime/` vêm:
    - os toolsets, `McpToolset` e `LiveOpenAPIToolset`;
    - o modelo, `gemini(...)`, que devolve um `Gemini` ou um `FallbackModel`;
    - o plugin, um `BasePlugin`.
  - Ele só declara o agente. Para `specs/agent.json`, são cerca de 110 linhas ([`exemplo-agent.py`](exemplo-agent.py)),
    nenhuma com mais de 120 colunas:
    - o modelo Gemini;
    - cada `LlmAgent`, com a sua instrução, as suas ferramentas e o `output_key`;
    - o workflow (`SequentialAgent`) e o `App` retomável que o `adk run` e o `adk web` carregam;
    - os plugins do `App`, com os `kwargs` da spec.
  - Nenhum agente tem callback próprio, e não há função, classe nem regra de negócio no arquivo gerado.
- **As ferramentas de cada agente:** o `McpToolset` com `SseConnectionParams`, para o OCR e o RAG, ou o
  `LiveOpenAPIToolset`. Ele é uma camada fina que monta o `OpenAPIToolset` do ADK a partir do `/openapi.json` vivo da
  API.
- **Um plugin que recebe `servers`** ganha também os endereços dos servidores da spec. Por isso os `kwargs` nomeiam uma
  ferramenta como `servidor.ferramenta`.
- **O plugin de agendamento** ([`runtime/plugin.py`](../runtime/plugin.py)) é o `BookingPlugin`, um `BasePlugin` do ADK
  com os callbacks de [`callbacks.py`](../runtime/callbacks.py). O que ele faz em cada gancho:
  [Segurança no código gerado](#segurança-no-código-gerado).
- **A biblioteca de runtime, [`runtime/`](../runtime/),** guarda as regras, num código fixo e testado por conta própria.
  - A interface tem versão: `API_VERSION`, hoje 6. O arquivo gerado confere essa versão na importação.
  - Ela tem duas partes. Um agente sem o `BookingPlugin` importa só a genérica (`adk.py`, `rede.py`).

  | Parte | Módulo | O que faz |
  |---|---|---|
  | genérica | [`adk.py`](../runtime/adk.py) | o modelo, o `McpToolset` e o `OpenAPIToolset` lido do contrato vivo |
  | genérica | [`rede.py`](../runtime/rede.py) | os hosts de `ALLOWED_HOSTS` e os endereços conferidos ([Hosts permitidos](referencia.md#hosts-permitidos)) |
  | genérica | [`web.py`](../runtime/web.py) | o `adk web` com o cabeçalho `Host` conferido |
  | agendamento | [`plugin.py`](../runtime/plugin.py) | o `BookingPlugin`: os [papéis](#o-bookingplugin-quem-lê-quem-busca-e-quem-agenda) das ferramentas, a política e as checagens que o transpilador chama |
  | agendamento | [`confianca.py`](../runtime/confianca.py) | a política de agendamento: faixas, pisos do OCR, um trecho do pedido por exame |
  | agendamento | [`callbacks.py`](../runtime/callbacks.py) | os callbacks do ADK que o `BookingPlugin` registra ([os ganchos](#segurança-no-código-gerado)) |
  | agendamento | [`pedido.py`](../runtime/pedido.py) | o registro de cada pedido |
  | agendamento | [`entrada.py`](../runtime/entrada.py) | o que da mensagem da pessoa chega ao modelo |
  | agendamento | [`confirmacao.py`](../runtime/confirmacao.py) | a lista, a pergunta `[s/N]` e a pausa da chamada |
  | agendamento | [`servidores.py`](../runtime/servidores.py) | as chamadas que o próprio runtime faz aos servidores MCP, fora dos agentes |
  | agendamento | [`relatorio.py`](../runtime/relatorio.py) | a mensagem final, escrita com o que as ferramentas devolveram |
  | agendamento | [`reconcilia.py`](../runtime/reconcilia.py) | a conferência do pedido inteiro, no [`report`](#segurança-no-código-gerado) |

  [`transpiler/live.py`](../transpiler/live.py) pergunta a cada servidor quais ferramentas ele tem
  ([Ferramentas conferidas nos servidores](#ferramentas-conferidas-nos-servidores)).

  Um teste garante que o gerado importa só `google.adk`, `google.genai` e `runtime`. Por que uma biblioteca:
  [decisoes.md](decisoes.md#o-agentpy-usa-só-classes-do-adk-a-política-mora-num-plugin-versionado).

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
| `model` | `gemini-<versão>` | `gemini(...)`: o `Gemini` de cada agente sem `model` próprio |
| `fallback_model` | opcional, `gemini-<versão>` | `gemini(<modelo>, fallback=<reserva>)` ([como](#fallback_model-por-requisição)) |
| `servers` | de 1 a 10 servidores | um toolset por servidor usado por um agente |
| `servers.<nome>` | identificador escolhido pela spec (`ocr`, `leitor`, `docs`...) | o nome do servidor em `servidor.ferramenta` |
| `servers.<nome>.url` | servidor MCP por SSE: `http://host:porta/sse` | `McpToolset(SseConnectionParams(url=...))` |
| `servers.<nome>.tools` | as ferramentas MCP que os agentes podem chamar | `tool_filter=[...]` |
| `servers.<nome>.openapi_url` | API descrita por OpenAPI: `http://host:porta/openapi.json` | `LiveOpenAPIToolset` → `OpenAPIToolset`, montado desse contrato |
| `servers.<nome>.operations` | os `operationId` que os agentes podem chamar | as operações do toolset |
| `agents[]` | de 1 a 10 | um `LlmAgent` cada |
| `agents[].name` | identificador único (notas abaixo) | `LlmAgent(name=...)` e o nome da variável no gerado |
| `agents[].instruction` | texto, com placeholders `{chave}` ou `{chave?}` (notas abaixo) | `instruction` |
| `agents[].output_key` | identificador, sem repetir (notas abaixo) | `output_key` (estado compartilhado) |
| `agents[].output_schema` | opcional, `modulo.Classe`: um modelo pydantic de `runtime` ou de `google.adk.plugins` | `output_schema` |
| `agents[].model` | opcional, `gemini-<versão>` | o `Gemini` só desse agente |
| `agents[].tools` | `servidor.ferramenta` de um servidor declarado (notas abaixo) | `tools=[...]` do agente, só com essas ferramentas |
| `workflow` | `SequentialAgent` (o padrão), `ParallelAgent`, `LoopAgent` ou `null` (notas abaixo) | `root_agent` |
| `max_iterations` | de 1 a 10 (notas abaixo) | `LoopAgent(max_iterations=...)` |
| `plugins[]` | até 10, cada um com `path` e `kwargs` (notas abaixo) | `App(plugins=[Classe(**kwargs)])` |

Notas da tabela:

- **`model`:** numa execução, pode ser trocado com `-e GEMINI_MODEL=<modelo>`, validado pela mesma regra. O `Gemini`
  tem 5 tentativas automáticas em `429`, `500` e `503`.
- **`agents[].name`:** não pode ser um nome que o arquivo gerado já usa (`app`, `root_agent`, `gemini`...) nem uma
  função do Python (`print`).
- **`agents[].output_key`:** com o `BookingPlugin`, fica fora das chaves que ele usa no estado.
- **`agents[].tools`:** pode ficar vazio, num agente que só reescreve a lista do anterior.
- **`agents[].output_schema`:** o ADK confere a resposta do agente contra o modelo, também com ferramentas.
- **`servers.<nome>`:** o servidor é MCP ou OpenAPI, nunca os dois. O host da URL precisa estar em `ALLOWED_HOSTS`
  ([abaixo](#hosts-permitidos-quem-decide-é-quem-implanta-não-a-spec)).
- **`agents[].instruction`:**
  - texto de 10 a 2000 caracteres, sem caractere de controle ou invisível (fora a quebra de linha e o tab);
  - `{chave}` só pode citar o `output_key` de um agente que rodou antes;
  - `{chave?}` também, e o ADK o troca por nada quando esse agente não escreveu nada, como num pedido sem exame;
  - com o `BookingPlugin`, cada chamada ao modelo leva antes a regra fixa de segurança
    ([Segurança no código gerado](#segurança-no-código-gerado)).
- **`max_iterations`:** obrigatório com `LoopAgent`, e só com ele.
- **`workflow`:**
  - `SequentialAgent` roda os agentes em ordem;
  - `ParallelAgent` os roda ao mesmo tempo, e nenhum agente lê a saída de outro;
  - `LoopAgent` os roda em ordem, até `max_iterations` vezes;
  - `null` deixa um agente só, que é a raiz.
- **`plugins[]`:**
  - `path` é `modulo.Classe`, uma subclasse de `BasePlugin` do ADK, de `runtime` ou de `google.adk.plugins`;
  - `kwargs` é um objeto JSON com nomes identificadores.

Qualquer campo fora dessa lista é rejeitado (`extra="forbid"`). A lista de pacotes fica no código
([`spec.py`](../transpiler/spec.py), `CLASS_PACKAGES`), e uma spec nunca faz o transpilador importar outro módulo.

### Um agente fora do domínio

[`exemplo-generico.json`](../specs/exemplo-generico.json) é um
assistente de documentação: um `LlmAgent` só (`"workflow": null`), com um servidor MCP `docs` e
nenhum plugin. O servidor não é do compose, então o `transpile` só passa com ele em `ALLOWED_HOSTS`:

```bash
docker compose run --rm -e ALLOWED_HOSTS=docs:8010 agent python -m cli transpile specs/exemplo-generico.json --output generated/docs/agent.py
```

O arquivo gerado não tem nenhum callback, plugin ou palavra do domínio de agendamento, e nenhuma regra
entra antes da instrução. Transpilar a spec ou importar o gerado carrega do `runtime` só `adk.py` e
`rede.py` (testes em
[`test_spec_generica.py`](../tests/test_spec_generica.py)). Este agente roda com `adk run` e `adk web`
([como](como-rodar.md#4-rodar-com-adk-run-ou-adk-web)), não com o `cli run` ([por quê](#o-que-o-cli-run-roda)).

O repositório não traz um servidor `docs`. O `transpile` passa sem ele, mas para rodar o agente é preciso um servidor
MCP seu, com a ferramenta da spec, em `docs:8010`.

### Hosts permitidos: quem decide é quem implanta, não a spec

A spec escolhe os servidores, mas o host de cada URL precisa estar em `ALLOWED_HOSTS`, uma variável de ambiente (lista
separada por vírgula). Assim uma spec não aponta o agente para outro host (SSRF: `169.254.169.254`, a rede interna, um
serviço local).

**O formato:**

- Sem a variável (ou vazia, ou só com vírgulas), valem exatamente os três serviços do compose, cada um na sua porta:
  `ocr:8001,rag:8002,api:8000`.
- Uma entrada `host` aceita qualquer porta; `host:porta`, só aquela.
- Uma entrada que não é nenhum dos dois é um erro do `transpile`, nunca ignorada.
- `localhost`, `127.0.0.1` e outras portas ficam de fora até alguém incluí-los. Por exemplo, a API do Docker em
  `localhost:2375`.
- Outro ambiente não exige mudar o código: basta `ALLOWED_HOSTS=ocr:8001,rag:8002,api:8000,clinica.interna:8443` no
  `.env`.

Onde ela é conferida, as faixas de endereço recusadas e os limites: [Hosts permitidos](referencia.md#hosts-permitidos).

### Ferramentas conferidas nos servidores

No `transpile`, cada servidor que responde em até 3 s é consultado: o MCP pelo `list_tools`, a API pelo `/openapi.json`.

- Uma ferramenta que o servidor não tem é um erro já no `transpile`.
- Quando todas existem, o `OK` ganha a linha `Ferramentas conferidas nos servidores: ocr, rag, api`.
- Um servidor fora do ar (um `transpile` offline, a CI) fica com a lista declarada na spec, sem linha a mais.
- Um servidor que responde, mas não com JSON (um `/openapi.json` malformado), é um erro.
- Um erro do próprio transpilador sai como erro, nunca como servidor fora do ar.
- O `cli run` pergunta de novo a todos os servidores antes de chamar o Gemini. Assim, uma ferramenta errada não chega a
  uma execução.
- No `cli run`, todo servidor precisa responder e listar as ferramentas.
  - Um servidor MCP que responde ao GET, mas não termina o `list_tools`, para a execução com
    `servers.<nome>: <url> respondeu, mas não listou as ferramentas (é um servidor MCP?)`.
  - Uma API que não serve o `/openapi.json` para com a mesma mensagem, que diz `as operações (é um /openapi.json?)`.
- A mesma consulta passa pelo `check_live` de cada plugin. O do `BookingPlugin` confere cada
  [papel](#o-bookingplugin-quem-lê-quem-busca-e-quem-agenda):
  - que a ferramenta de `read` recebe `filename`;
  - que a de `search` recebe `query` e `top_k`;
  - o contrato da operação de `book` (abaixo).

### `fallback_model`, por requisição

No código gerado, o modelo é o `FallbackModel` do ADK. Uma requisição que o principal recusa com `429` ou `503` vai,
igual, ao reserva. Vale no `cli run`, no `adk run` e no `adk web`, que rodam o mesmo `app` sem mudança.

- **Não agenda em dobro nem pergunta de novo.** A chamada que se repete é ao modelo, e uma chamada ao modelo que
  falhou não rodou nenhuma ferramenta. As respostas e a `Idempotency-Key` seguem no
  [registro do pedido](#segurança-no-código-gerado), que guarda o que a política usa.
- **A troca é imediata.** Com o reserva na spec, o principal só repete o `500`, não o `429` nem o `503`. Sem ele, as
  tentativas levariam cerca de 30 s (esperas de 2, 4, 8 e 16 s).
- **O reserva tem as suas 5 tentativas.**
- **A saída mostra a troca:** `Aviso: modelo principal indisponível; usando gemini-3.5-flash-lite`.

### O `BookingPlugin`: quem lê, quem busca e quem agenda

A política de agendamento
([`runtime/`](../runtime/)) não conhece nomes de servidor nem de ferramenta: os `kwargs` do plugin
dizem qual ferramenta faz cada papel, como `servidor.ferramenta`.

| Papel | Contrato que o runtime usa | Para quê | Entra na confiança |
|---|---|---|---|
| `read` | ferramenta MCP que recebe `filename` e devolve `lines`, `line_confidence` e `pii_masked` | o pedido lido | a leitura do OCR de cada linha |
| `search` | ferramenta MCP que recebe `query` e `top_k` e devolve `[{code, name, score}]` | os códigos possíveis | o score e o quanto o resultado bate com a linha lida |
| `book` | operação OpenAPI que recebe `{"exams": [...]}` e o `Idempotency-Key` | o agendamento, só com os códigos que passaram na política | — |

Os outros `kwargs` são a política, e todos são opcionais. Nos limiares e nos pisos do OCR, o padrão é o valor medido e
também o mínimo: uma spec só pode deixar a política mais rígida ([por quê](#as-garantias-medidas-valem-para-a-política-padrão)).

| Campo | O que é | Padrão | Aceita |
|---|---|---|---|
| `min_confidence` | a confiança mínima para entrar na lista sem aviso | 0,90 | de 0,90 a 1 |
| `ask_from` | o início da [faixa com aviso](glossario.md#faixas-e-pisos) | 0,70 | de 0,70 até menos que `min_confidence`, ou `null` |
| `ocr_floor_line` | o piso de leitura do OCR numa linha | 75 | de 75 a 100 |
| `ocr_floor_short` | o piso de leitura para uma sigla curta | 85 | de 85 a 100 |
| `ocr_floor_synonym` | o piso de leitura para uma sigla que é outro nome de exame | 95 | de 95 a 100 |
| `top_k` | quantos resultados a busca devolve por [pedaço da linha](referencia.md#como-a-busca-corta-uma-linha) | 3 | de 1 a 10 |

Com `"ask_from": null`, a faixa com aviso some: o que fica abaixo de `min_confidence` só é avisado.

As regras abaixo são do plugin (`check_spec`), chamadas pelo `transpile` com as mesmas mensagens:

- **O plugin roda em ordem:** pede `"workflow": "SequentialAgent"`.
- **Quem agenda** é o agente que tem a ferramenta de `book` nas suas `tools`.
  - Só um agente pode tê-la: um pedido, um agendamento.
  - As ferramentas de `read` e `search` precisam estar em agentes anteriores, num agente só ou em dois. A checagem do
    agendamento ([`before_tool`](#segurança-no-código-gerado)) só agenda o que a busca achou, medido contra o que foi lido.
- **`book` pede `read` e `search`,** e `search` pede `read`: sem eles não há confiança a medir.
- **Sem `book`, a spec lista em vez de agendar.** Nada é enviado a uma API.
  - O último agente responde com `"output_schema": "runtime.plugin.ListedExams"` (cada exame com `code` e `name`), que
    o ADK confere.
  - Essa resposta passa pela mesma política, sem pergunta: o plugin chama `review_list` depois do último agente.
  - A CLI mostra cada exame com a sua confiança (`confira` na [faixa com aviso](glossario.md#faixas-e-pisos)), os que
    ficaram de fora e os códigos que nenhuma busca devolveu.
- **Toda ferramenta de um agente tem papel.** Uma operação de API só chega a um agente como `book`, a única chamada que
  o runtime confere antes de enviar.
  - O `transpile` recusa uma ferramenta sem papel e qualquer outra operação de API:
    - uma 2ª operação;
    - um 2º servidor OpenAPI;
    - a API renomeada sem mudar `book`;
    - uma API numa spec que só lista.
  - Em tempo de execução, o runtime também recusa qualquer ferramenta fora dos papéis.
  - No agendamento, ele recusa qualquer campo além de `exams` e da `Idempotency-Key` do runtime.
  - Com a API no ar, o `transpile` confere o contrato da operação de `book`:
    - ela recebe `Idempotency-Key` e um corpo só com `exams` (`additionalProperties: false`);
    - cada exame tem `code`; o `name` é opcional, e a API do projeto devolve o nome do catálogo.
  - Faltar um papel nunca quer dizer "sem checagem".
- **Os `kwargs` são conferidos de novo ao criar o plugin:** um `agent.py` editado à mão também não
  afrouxa a política.

### O que o `cli run` roda

O `cli run` roda specs com o `BookingPlugin` com `read` e `search`. Com `book`, agenda; sem `book`, lista, como a
`listar-exames.json`. Os comandos: [sua própria spec](como-rodar.md#2-rodar-o-transpilador), cujo exemplo parte dela.

Uma spec sem `read` e `search` é um pipeline que não lê um pedido em imagem, como o exemplo genérico. Ela transpila,
importa e roda com `adk run` e `adk web`, e o `cli run` diz que não a roda.

### As garantias medidas valem para a política padrão

"Nenhum exame errado agendado sem confirmação" foi medido com a política padrão
([os valores](#o-bookingplugin-quem-lê-quem-busca-e-quem-agenda)). Os conjuntos: os 120 pedidos manuscritos simulados,
as 30 fotos de celular e os 200 pedidos da carga do [estado atual](medicoes.md#estado-atual). Por isso esses valores
são também os mínimos.

- Abaixo do mínimo de um campo, o `transpile` para e nomeia o campo e o mínimo
  ([a mensagem](#validação-e-mensagens-de-erro)).
- O porquê do mínimo de `min_confidence`: a 0,80, um "- GA" mal lido seria agendado como IgA
  ([calibração](medicoes.md#calibração-do-limiar-de-090)).
- Baixar um mínimo pede uma mudança no código, não na spec. Pede também uma nova medição, que passa se nenhum exame
  errado for agendado sem confirmação ([como repetir](medicoes.md#estado-atual)).

A [`agent-sem-confirmacao.json`](../specs/agent-sem-confirmacao.json) é a política mais rígida da tabela da introdução.
Ela roda no runner real do ADK, com um modelo roteirizado no lugar do Gemini, e a confirmação final continua
([o teste](../tests/test_confirmacao_nativa.py)).

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
   - plugin fora dos pacotes permitidos, que não existe ou não é um `BasePlugin`;
   - `kwargs` fora do `Config` do plugin;
   - as regras do próprio plugin (`check_spec`); as do `BookingPlugin`:
     [O `BookingPlugin`](#o-bookingplugin-quem-lê-quem-busca-e-quem-agenda).
4. **Servidores que respondem** (só no `transpile` e no `cli run`): ferramenta que o servidor não
   tem, ou, pelo `check_live` do plugin, ferramenta de papel que não recebe o que o runtime envia.

Uma etapa só aparece quando a anterior passa. Saídas reais, cada uma com uma mudança em `specs/agent.json`. Os outros
mínimos (`ask_from`, `ocr_floor_short`, `ocr_floor_synonym`) dão a mesma mensagem do `ocr_floor_line`, com o seu
campo e valor:

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
| agendamento como 1º agente | `Erro: agents.0.tools: api.create_appointment só agenda códigos achados no catálogo: antes dele, agentes anteriores precisam usar ocr.extract_exam_text e rag.search_exams` |
| `ask_from` 0,95 | `Erro: plugins.0.kwargs.ask_from: deve ser menor que min_confidence (ou null, sem pergunta)` |
| `ocr_floor_short` 99 | `Erro: plugins.0.kwargs.ocr_floor_synonym: use ocr_floor_line <= ocr_floor_short <= ocr_floor_synonym (uma sigla pede leitura mais clara)` |
| `top_k` 0 | `Erro: plugins.0.kwargs.top_k: deve ser no mínimo 1` |
| `min_confidence` 0,8 | `Erro: plugins.0.kwargs.min_confidence: deve ser no mínimo 0,9, o valor medido: uma spec pode deixar a política mais rígida, nunca mais frouxa` |
| `ocr_floor_line` 0 | `Erro: plugins.0.kwargs.ocr_floor_line: deve ser no mínimo 75, o valor medido: uma spec pode deixar a política mais rígida, nunca mais frouxa` |
| `"min_confidence": "0.9"`, entre aspas | `Erro: plugins.0.kwargs.min_confidence: deve ser um número (veio como texto: escreva sem aspas)` |
| `{{exam_names}}` numa instrução | `Erro: agents.1.instruction: {{exam_names}} não é texto literal: o ADK não tem escape para chaves e lê isso como o placeholder {exam_names}; para citar o nome, escreva-o sem chaves` |
| um 4º agente que também agenda | `Erro: agents.3.tools: api.create_appointment já está em outro agente: um pedido, um agendamento` |
| `model` que não é Gemini | `Erro: model: formato inválido: esperado gemini-<versão>` |
| `"workflow": "ParallelAgent"` com o `BookingPlugin` | `Erro: workflow: o BookingPlugin lê, busca e agenda em ordem: use SequentialAgent` |
| `LoopAgent` sem `max_iterations` | `Erro: max_iterations: obrigatório com LoopAgent, e só com ele` |
| plugin de outro pacote | `Erro: plugins.0.path: "os.path.Join" fora dos pacotes de plugins (runtime, google.adk.plugins)` |
| plugin que não existe | `Erro: plugins.0.path: "runtime.plugin.Nope" não é uma classe de plugin do ADK (BasePlugin)` |
| JSON malformado: sem a vírgula do fim da linha 3 (`"model"`) | `Erro: JSON inválido (linha 4, coluna 3): Expecting ',' delimiter` |

Chaves duplicadas são detectadas no parse (`object_pairs_hook`), porque o `json` padrão
manteria só a última silenciosamente.

- **Tipos estritos:** um valor de outro tipo JSON é recusado, nunca convertido: `3.5` não é inteiro, `true` não é número.
- **BOM:** um arquivo salvo com BOM UTF-8 (comum em editores do Windows) é lido normalmente.
- **`NaN` e `Infinity`:** o `json` do Python os aceita, mas não são JSON: são recusados.
- **Chaves duplas em volta de outro texto** (`{{"code": 1}}`) ficam como texto. Em volta de um nome, veja a mensagem na tabela.
- **Mensagens:** todas em português; um erro de schema sem tradução sai como `valor inválido (<tipo do erro>)`.

## Geração

[`transpiler/generator.py`](../transpiler/generator.py) preenche [`agent_template.py.tmpl`](../transpiler/agent_template.py.tmpl) com `string.Template`, com um bloco por agente da spec.

- Os construtores do ADK (`LlmAgent`, `McpToolset`, `App`) saem com um argumento por linha. Por isso o gerador não
  decide onde quebrar uma linha. As chamadas curtas, como `gemini(...)`, ficam numa linha só.
- Só uma instrução longa vira literais adjacentes, de até 120 colunas cada.
- Cada valor da spec entra como literal Python via `repr()`, então nenhum valor da spec é executado como código.
- Em seguida o arquivo passa por `py_compile` e é importado.
- O `transpile` só imprime `OK` se `root_agent` existir:

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

- **O registro do pedido** fica no próprio `BookingPlugin`, um registro por sessão. Ele guarda o que a política usa:
  a imagem, as linhas lidas, os códigos de cada busca, as respostas e as perguntas de cada chamada, a
  `Idempotency-Key` e a resposta da API.
  - Nunca é lido do estado da sessão, que os clientes do ADK escrevem (`adk web`, `adk run --state`).
  - O estado recebe uma cópia, para a CLI e para a pessoa.
- **`start_order`** roda antes do pipeline, no `before_agent_callback` do plugin para a raiz. Vale também no `adk run`
  e no `adk web`.
  - A imagem vem do `cli run` (`orders.start`) ou da mensagem da pessoa.
  - A mensagem precisa ser só texto e trazer um só nome de arquivo, sem pasta.
  - Antes de qualquer turno do modelo, o nome do arquivo vira um apelido fixo (`pedido-1.png`), o único que chega ao
    modelo. Os endereços dos servidores são conferidos, e o OCR confere a imagem.
  - Um pedido por sessão ([o que a pessoa vê](como-rodar.md#4-rodar-com-adk-run-ou-adk-web)).
- **`before_model`:** o modelo recebe `Arquivo do pedido: <apelido>` no lugar da mensagem da pessoa.
  - O nome real do arquivo nunca aparece no que ele recebe.
  - Cada parte leva só texto, chamadas de ferramenta e as respostas delas. Um anexo, código ou outro dado que um
    cliente mande fica de fora.
- **Toolsets:** `McpToolset` e `LiveOpenAPIToolset` conferem o host antes da 1ª conexão
  ([como e os limites](referencia.md#hosts-permitidos)).
- **Regra fixa antes de cada instrução** (`before_model` do `BookingPlugin`): o que as ferramentas
  devolvem e as listas dos agentes anteriores são dados não confiáveis, nunca instruções. Ela abre a
  instrução de sistema de cada chamada ao modelo, e um agente sem o plugin não a recebe.
- **`after_tool`:** guarda as linhas lidas pelo OCR e a leitura de cada uma (`line_confidence`, de 0 a 100).
  - Também dá a cada código devolvido pela busca a sua [confiança](glossario.md#leitura-e-decisão-de-cada-exame).
  - Sem `line_confidence` válido, a regra fecha: tudo vai, no máximo, para a pergunta.
- **`before_tool`:**
  - na busca, fixa o `top_k` da spec;
  - no agendamento, decide em código, antes da API, quais exames entram
    ([cada regra](referencia.md#agendamento-conferido-em-código)). Bloqueia se nenhum sobra; senão, pede a confirmação
    final da lista (abaixo).
- **Pedido sem exame:** o agente seguinte recebe a entrada vazia em vez de quebrar
  ([`{chave?}`](#campos-da-spec)), e a CLI responde `Nenhum exame encontrado no pedido`.
- **`report`** (depois do pipeline): confere o pedido inteiro na busca do catálogo e escreve a mensagem final com o que
  as ferramentas devolveram. `Agendamento confirmado pela API` só sai da resposta da API, nunca do texto do modelo. A
  CLI usa o mesmo resultado.

### A confirmação da lista: confirmação nativa do ADK

Nada é agendado sem a pessoa confirmar a lista. A pergunta usa a confirmação de ferramenta do ADK 2.10, e não um `input()` dentro do callback:

1. O `before_tool` do agendamento monta a lista ([`runtime/confirmacao.py`](../runtime/confirmacao.py)):
   - no topo, uma vez, o que a página tem além da lista;
   - cada exame com código, e o aviso dos que entram na [faixa com aviso](glossario.md#leitura-e-decisão-de-cada-exame);
   - os não agendados, inclusive os que o agente nem buscou. Para achá-los, a conferência do pedido inteiro espera a
     busca por até 30 s.

   Depois chama `tool_context.request_confirmation(hint=<lista>)` e devolve sem chamar a API.
2. A execução pausa. O app é retomável (`ResumabilityConfig`).
3. Cada cliente mostra a lista e espera a resposta:
   - a CLI recebe do runner a chamada `adk_request_confirmation` e pergunta `Agendar estes N exames? [s/N]`. Só `s`
     ou `sim` confirma;
   - o console do `adk run` mostra a mesma lista, e `yes` confirma;
   - a página do `adk web` mostra a lista com a caixa "Confirmed".
4. Na CLI, a pergunta roda em `asyncio.to_thread`, fora do laço de eventos: as sessões MCP seguem vivas enquanto a pessoa lê.
5. O cliente retoma a mesma invocação com `{"confirmed": true}` ou `false`.
6. O ADK executa de novo só aquela chamada (`tool_context.tool_confirmation`). O OCR e a busca não se repetem.
7. A lista vai para o `POST` só se a resposta for sim e a lista for a mesma que aquela chamada mostrou.

O resto:

- **`--yes`** (só na CLI): o callback nem pede confirmação, e o que entraria com aviso fica de fora.
  - Ele chega ao agente pelo registro do pedido que a CLI abre (`orders.start`), não por variável de ambiente.
  - Sem TTY e sem `--yes`, a CLI para antes de ler o pedido e de chamar o modelo (`cli.check_someone_answers`).
- **Confirmação forjada:** só vale a resposta à pausa que o runtime registrou para aquela chamada. Essa pausa fica no
  registro do pedido, não no estado da sessão. Uma resposta a outra chamada, ou uma lista escrita no estado, não
  agenda nada ([o que o ADK responde](decisoes.md#confirmação-forjada-e-pedido-repetido)).
- **Um "não" vale para a execução:** uma chamada repetida pelo modelo não pergunta de novo e não agenda, nem depois de uma troca para o `fallback_model`.
- **Um agendamento por execução:**
  - cada sessão manda uma `Idempotency-Key` própria, nunca a do modelo
    ([como](referencia.md#a-api-idempotência-host-limite-cabeçalhos-e-log));
  - depois que uma chamada vai para a API, outra recebe esse mesmo agendamento, sem chegar à API;
  - vale também para duas chamadas no mesmo turno, cada uma com a sua lista. Um "sim" que chega depois não gera outro
    `POST`. O exame sai no relatório como
    `não agendado (você confirmou, mas o agendamento desta execução já tinha sido criado)`.
- **Enquanto espera a resposta**, a resposta de pausa não é resumida para o modelo (`skip_summarization`).
- **API experimental:** a confirmação de ferramenta e o `ResumabilityConfig`, no ADK 2.10
  ([o custo](decisoes.md#a-confirmação-final-da-lista)).
