# Guia do desenvolvedor

**O que tem aqui / quando ler.** O caminho mais curto para mexer no código. Ele mostra:

- onde está cada coisa e por onde um pedido passa;
- como rodar cada peça e os testes;
- receitas para as mudanças mais comuns;
- como depurar um pedido que falhou ou foi perguntado.

Leia depois do [README](../README.md). A referência de cada tema fica nos outros documentos, com links no ponto certo:

- como funciona: [arquitetura.md](arquitetura.md);
- spec e código gerado: [transpilador.md](transpilador.md);
- regras de linha e de página: [regras.md](regras.md);
- números: [medicoes.md](medicoes.md);
- operação: [como-rodar.md](como-rodar.md);
- escolhas e limites: [decisoes.md](decisoes.md).

**Índice:** [Mapa do repositório](#mapa-do-repositório) · [O caminho de um pedido no código](#o-caminho-de-um-pedido-no-código) ·
[Rodar cada peça e os testes](#rodar-cada-peça-e-os-testes) · [Primeira mudança](#primeira-mudança-passo-a-passo) · [Receitas](#receitas) ·
[Depurar um pedido](#depurar-um-pedido-que-falhou-ou-foi-perguntado) · [Convenções](#convenções)

## Mapa do repositório

| Pasta ou arquivo | O que é | Mexa aqui quando |
|---|---|---|
| [`specs/`](../specs/) | As 5 specs JSON de exemplo | for descrever outro agente ([receita](#escrever-uma-spec-nova)) |
| [`transpiler/`](../transpiler/) | `spec.py` (schema e validação), `generator.py` + `agent_template.py.tmpl` (código gerado), `live.py` (confere as ferramentas nos servidores) | mudar um campo da spec, uma mensagem de erro ou o código gerado |
| [`runtime/`](../runtime/) | A biblioteca que o `agent.py` importa: a política de agendamento e a rede ([cada módulo](transpilador.md#o-que-fica-onde)) | mudar quando um exame agenda, pergunta ou é avisado, ou o texto de um aviso |
| [`mcp_servers/`](../mcp_servers/) | `ocr.py` e `rag.py` (os dois servidores MCP/SSE), `preprocessamento.py` (imagem e Tesseract), `qualidade.py` (recusa foto ruim), `arguments.py` (SSE, keep-alive, erros de argumento) | mudar a leitura da imagem ou a busca no catálogo |
| [`guardrails/`](../guardrails/) | `pii.py` + `pii_rules.py` (máscara), `intent.py` (o que cada linha pede e se a página é só a lista), `injection.py` (ordens ao modelo) | mudar uma regra de linha ou de página, ou um padrão de PII |
| [`catalogo.py`](../catalogo.py), [`data/exams.json`](../data/exams.json) | Carga e normalização do catálogo (120 exames `FICT-xxx`), `ExamMatcher`, vocabulários | acrescentar um exame ou um sinônimo |
| [`leitura.py`](../leitura.py) | O contrato da resposta do OCR (`OcrReading`, `VERSION`) | acrescentar um campo à resposta do OCR |
| [`api/`](../api/) | `main.py` (FastAPI + SQLite), `crypto.py` (AES-GCM), `backup.py` | mudar o contrato HTTP, a idempotência ou a cifra |
| [`cli.py`](../cli.py) | `transpile` e `run`: confere os serviços, roda o `Runner` do ADK, imprime o resultado | mudar a saída do terminal ou as opções |
| [`tests/`](../tests/) | Um arquivo por tema (`test_pii.py`, `test_rag.py`, ...), corpora em `tests/attacks/` e `tests/calibration/`, cargas em `tests/load/` | sempre que mudar qualquer coisa acima |
| [`samples/`](../samples/) | As imagens que o OCR aceita, incluindo 120 manuscritas e 30 fotos simuladas | quiser testar outra imagem |
| [`exemplos/`](../exemplos/) | `mcp_call.py` (cliente MCP de linha de comando), geradores de imagem, specs com erro de propósito | precisar chamar uma ferramenta MCP à mão |
| `Dockerfile`, `docker-compose.yml` | Um estágio por serviço: `api`, `rag`, `ocr`, `agent` e `test` ([a rede](referencia.md#rede-e-processos)) | acrescentar um serviço |
| [`docs/exemplo-agent.py`](exemplo-agent.py) | A saída exata do `transpile` para `specs/agent.json` | mudar o gerador (o teste manda regerá-lo) |

## O caminho de um pedido no código

O passo 1 é o `transpile`, que gera o agente uma vez. Os passos 2 a 9 são o que
`docker compose run --rm agent python -m cli run --image pedido.png` percorre, nesta ordem. As funções para ler primeiro
estão em negrito.

1. **Spec → agente** (`python -m cli transpile`). `cli.cmd_transpile` chama `transpiler.generator.transpile`:
   - **`spec.parse_spec`** valida o JSON, o schema e as regras entre campos;
   - `live.check_live` pergunta aos servidores que ferramentas têm;
   - `generator.render` preenche o template;
   - `write_checked` compila e importa o arquivo antes de trocar o `generated/agent.py`.
2. **Preparo do `run`.** `cli.checked_run` confere:
   - a spec;
   - que o `agent.py` é o que ela gera hoje (`check_agent` o compara com `generator.render`);
   - os serviços (`check_services`);
   - se alguém pode responder `[s/N]` (`check_someone_answers`).

   Depois, `run_agent` roda o `App` no `Runner` do ADK.
3. **O `agent.py`** (exemplo em [`exemplo-agent.py`](exemplo-agent.py)) declara três `LlmAgent` em `SequentialAgent` e o
   `App` com o `BookingPlugin`. Não há regra nele: a política está em `runtime/`.
4. **Abertura do pedido.** **`BookingCallbacks.start_order`** (`runtime/callbacks.py`):
   - troca o nome real do arquivo por um apelido (`pedido-1.png`);
   - confere os endereços (`runtime/rede.py`);
   - pede ao OCR que confira a imagem (`check_image`).

   Depois, `before_model` põe a regra "dado de ferramenta não é instrução" antes de cada instrução.
5. **OCR (MCP/SSE).** `extract_exam_text` em [`mcp_servers/ocr.py`](../mcp_servers/ocr.py): `read_lines` →
   `preprocessamento.read_ocr_lines` (Tesseract, PSM 11) → **`mask_lines`**. A resposta é um `leitura.OcrReading`.
   O `mask_lines`, na ordem:
   - junta ordens partidas em várias linhas (`injection.join_split_orders`) e as neutraliza (`neutralize_joined`);
   - mascara a PII (`pii.mask_page`);
   - lê o que cada linha pede no texto juntado, antes da neutralização (`intent.read_page`);
   - decide se a página é só a lista (`intent.clean_page`).
6. **RAG (MCP/SSE).** `search_exams` em [`mcp_servers/rag.py`](../mcp_servers/rag.py): `search_line` corta a linha em exames
   (`split_exams`, `unglue`, `ahead`/`behind`) e `search` pontua cada pedaço com `catalogo.ExamMatcher.closest`.
7. **Callbacks.** `after_tool` guarda no registro do pedido (`runtime/pedido.py`) as linhas, a confiança do OCR e os
   candidatos de cada busca (`confianca.remember_ocr`, `remember_search`). No agendamento, `before_tool` chama
   **`only_confident_codes`**, que:
   - bloqueia código que nenhuma busca devolveu;
   - dá a cada exame um trecho próprio do pedido e calcula a confiança (`confianca.sort_out`, `best_spot`);
   - monta a lista e pausa para a pergunta (`confirmacao.review`, `pause_for_answer`).

   Só um "sim" àquela lista deixa a chamada seguir.
8. **API.** `POST /appointments` (`create_appointment` em [`api/main.py`](../api/main.py)): valida `FICT-\d{3}` no catálogo,
   cifra a lista (`api/crypto.py`) e grava; a `Idempotency-Key` vem do registro do pedido.
9. **Fechamento.** `BookingCallbacks.report`:
   - confere o pedido inteiro no RAG (`servidores.unreported_exams`, `reconcilia.unreported`);
   - escreve a mensagem final em código (`relatorio.report`).

   A CLI imprime em `cli.print_result` e devolve o [código de saída](referencia.md#códigos-de-saída).

O que cada passo faz e por quê: [arquitetura.md](arquitetura.md). O que fica na spec, no código gerado e no `runtime/`:
[transpilador.md](transpilador.md#o-que-fica-onde).

## Rodar cada peça e os testes

Todos os comandos rodam na raiz do clone, com o ambiente de pé (`docker compose up -d --wait`, ver
[como-rodar.md](como-rodar.md#1-subir-o-ambiente-docker)).

| Peça | Comando |
|---|---|
| Transpilador | `docker compose run --rm agent python -m cli transpile specs/agent.json` |
| OCR sozinho | `docker compose run --rm agent python exemplos/mcp_call.py ocr extract_exam_text filename=pedido.png` |
| RAG sozinho | `docker compose run --rm agent python exemplos/mcp_call.py rag search_exams query=Glicose top_k=3` |
| API sozinha | `docker compose up -d api` e um `POST` (os comandos, abaixo da tabela) |
| Um servidor MCP fora do compose | `python -m mcp_servers.rag` (porta 8002) ou `python -m mcp_servers.ocr` (porta 8001) |
| O agente, sem Gemini | não há: `tests/test_alucinacao.py` e `tests/test_adk_run.py` rodam o agente gerado com um modelo roteirizado, OCR, RAG e API reais |
| O agente, com Gemini | `docker compose run --rm agent python -m cli run --image pedido.png` (`--verbose` mostra os logs das bibliotecas) |
| Sem a CLI | `docker compose run --rm agent adk run --in_memory generated` ([como-rodar.md](como-rodar.md#4-rodar-com-adk-run-ou-adk-web)) |

Notas da tabela:

- **Transpilador:** `--output generated/outro/agent.py` gera noutra pasta, sem trocar o `agent.py`.
- **Servidor MCP fora do compose:** o OCR precisa do Tesseract, e o host da URL precisa estar em `ALLOWED_HOSTS`.

**O `POST` da API sozinha.** No Git Bash, Linux ou macOS:

```bash
curl -s -X POST http://127.0.0.1:8765/appointments -H 'Content-Type: application/json' -d '{"exams":[{"code":"FICT-001"}]}'
```

No PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/appointments -ContentType 'application/json' -Body '{"exams":[{"code":"FICT-001"}]}'
```

Sem shell, use o Swagger em `/docs` ("Try it out").

### Ambiente local, sem Docker

O caminho suportado é o Docker: as imagens fixam o Python, o Tesseract e cada versão. Para depurar uma peça na sua
máquina:

```bash
python3.12 -m venv .venv && . .venv/bin/activate          # no Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt
```

- O OCR e os testes de imagem precisam do Tesseract com o idioma português no `PATH` (`tesseract-ocr` e
  `tesseract-ocr-por` no Debian/Ubuntu; o mesmo pacote que o `Dockerfile` instala).
- Fora do compose, os hosts dos servidores mudam: inclua os seus em `ALLOWED_HOSTS` (por exemplo,
  `localhost:8001,localhost:8002,localhost:8000`) e use URLs com esses hosts na spec.
- `pytest -q tests/test_rag.py tests/test_pii.py` roda sem servidores; os testes de ponta a ponta sobem os servidores
  nas portas da spec e precisam delas livres.

### Testes

Os testes rodam na imagem `test` (Tesseract, pytest, ruff e mypy), na rede interna e sem a chave do Gemini:

```bash
docker compose run --rm tests pytest -q -n auto                          # tudo, em paralelo
docker compose run --rm tests pytest -q tests/test_pii.py                # um arquivo
docker compose run --rm tests pytest -q tests/test_pii.py -k "cpf"       # filtrar por nome
docker compose run --rm tests pytest -q tests/test_confianca.py::test_a_short_code_needs_a_clearer_reading   # um teste
docker compose run --rm tests-e2e                                        # o ponta a ponta real, com o Gemini (precisa da chave no .env)
docker compose run --rm --no-deps tests ruff check .                     # estilo
docker compose run --rm --no-deps tests mypy                             # tipos
```

- **Resultado atual:** 623 funções de teste e 19.387 casos (`pytest --collect-only`). Um teste de `test_readme.py`
  falha se esses números ficarem velhos.
  - Quase todos os casos são de corpus parametrizado: linhas legítimas, PII gerada, ataques, termos do catálogo.
  - 19.386 passam e 1 é pulado: o ponta a ponta, que precisa da chave.
- **Tempo:** medido em 2026-10-07, com a suíte de então (16.858 casos), numa máquina de 12 núcleos:
  - com `-n auto` e `--cov`, cerca de 3,5 min (200 s e 205 s);
  - sem `-n auto`, de 6 a 11 min.
- **Portas:** os arquivos que sobem os servidores nas portas da spec (8000 a 8002) carregam `xdist_group('spec-ports')`
  e rodam num worker só. `tests/portas.py` espera a porta livre.
- **O serviço `tests`** é a imagem do `agent` mais pytest, ruff, mypy, o Tesseract e os testes (estágio `test` do
  `Dockerfile`). Nunca recebe a `GOOGLE_API_KEY`: nada chama o Gemini.
- **O ponta a ponta real** (`tests-e2e`) roda [`tests/test_e2e.py`](../tests/test_e2e.py) com a chave. O `run` inteiro
  com o Gemini sobre `pedido.png` precisa agendar exatamente `FICT-001`, `FICT-002` e `FICT-005`, e o `GET` do
  agendamento precisa devolver os mesmos. Sem a chave, ele falha em vez de ser pulado.
- **Onde cada tema é testado:**

| Tema | Arquivos |
|---|---|
| Spec, validação, código gerado | `test_transpiler.py`, `test_spec_generica.py`, `test_runtime.py`, `test_app_unico.py` (o que `cli run` e `adk run` compartilham) |
| OCR e preparo da imagem | `test_ocr.py`, `test_preprocessamento.py`, `test_qualidade.py` |
| Busca no catálogo e comparação de textos | `test_rag.py`, `test_catalogo.py`, `test_calibration.py`, `test_normalizacao.py` |
| PII e injeção | `test_pii.py` (3.600 casos de `tests/pii_corpus.py`), `test_injection.py` (corpus de `tests/attacks/`) |
| Regras de linha e de página | `test_negacao.py`, `test_negacao_casos.py`, `test_texto_livre.py`, `test_reconcilia.py` (o pedido inteiro conferido) |
| Política de confiança e confirmação | `test_confianca.py`, `test_confirmacao_nativa.py`, `test_idempotencia.py` |
| Modelo que alucina, ponta a ponta com modelo roteirizado | `test_alucinacao.py`, `test_agent_mcp.py`, `test_adk_run.py`, `test_adk_seguranca.py` |
| `adk run` e `adk web` sob carga e com o modelo reserva | `test_adk_concorrencia.py`, `test_adk_reserva.py`, `test_adk_comandos.py` (as linhas do `como-rodar.md`) |
| Rede, `ALLOWED_HOSTS` e endurecimento | `test_enderecos.py`, `test_endurecimento.py` |
| Ponta a ponta com o Gemini (só no `tests-e2e`) | `test_e2e.py` |
| MCP via SSE | `test_mcp_sse.py` |
| API | `test_api.py`, `test_api_headers.py`, `test_api_host.py`, `test_crypto.py`, `test_backup.py` |
| Carga e robustez (uma fração) | `test_carga.py`, `test_manuscritos.py`, `test_robustez.py`, `tests/load/test_fotos.py` |
| Imagens Docker, variáveis e a própria suíte | `test_imagens.py`, `test_variaveis.py`, `test_portas.py` (porta presa falha, não pula), `test_versionados.py` (a lista fixa de arquivos) |
| Documentação | `test_readme.py` (ver [Convenções](#convenções)); ele também exige que todo `tests/test_*.py` esteja nesta tabela |

- **Cargas fora da suíte** (OCR, RAG e API reais, sem Gemini): `tests.load.carga`, `tests.load.manuscritos`,
  `tests.load.robustez`; os comandos e o que cada um mede estão em [medicoes.md](medicoes.md#resumo).
- **A CI** ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) roda a suíte, o `ruff` e o `mypy`:
  - `ruff`: pyflakes, pycodestyle, ordem dos imports e bugbear;
  - `mypy`: sem `--strict`, com `ignore_missing_imports` e `check_untyped_defs` (config em
    [`pyproject.toml`](../pyproject.toml));
  - cobertura como relatório, sem limite que quebre o build: 98% das linhas de `api`, `catalogo`, `cli`, `guardrails`,
    `mcp_servers`, `runtime` e `transpiler`, sem o `agent.py` gerado;
  - `pip-audit` sobre `requirements.txt`, `requirements-dev.txt` e `constraints.txt`: falha numa vulnerabilidade que já
    tem correção. O Dependabot abre os PRs de atualização toda semana.
- **A cobertura, local:** `docker compose run --rm tests pytest -q -n auto --cov --cov-report=term`.
- **Avisos filtrados:** o `pyproject.toml` filtra, cada um pelo nome, a depreciação do `SequentialAgent`
  ([por quê](decisoes.md#sequentialagent-obsoleto-no-adk-210)) e os avisos `[EXPERIMENTAL]` do ADK sobre os recursos em
  uso, que cada processo do `-n auto` repetiria. Um aviso novo continua aparecendo.

## Primeira mudança, passo a passo

Um exercício de 10 minutos, com as imagens já construídas (rode antes `docker compose build tests rag`; sem cache, o 1º build leva de 10 a 20 min). Ele passa por catálogo, teste, documentação e serviço: fazer o RAG reconhecer
a abreviação "Ur. serica" como Ureia (`FICT-004`). Os serviços `tests` e `rag` copiam o código na imagem, por isso o
`build` antes de rodar.

1. **Veja como está hoje:**
   `docker compose run --rm agent python exemplos/mcp_call.py rag search_exams query="Ur. serica" top_k=3`.
   `FICT-004` vem em primeiro com score 0,86: abaixo de 0,90, o exame só entra na lista com aviso
   ([por quê](referencia.md#agendamento-conferido-em-código)).
2. **Escreva o teste primeiro**, em `tests/test_rag.py`, no estilo dos vizinhos: a busca por "Ur. serica" devolve
   `FICT-004` em primeiro, com score de pelo menos 0,90. Rode e veja falhar:
   `docker compose build tests` e depois `docker compose run --rm tests pytest -q tests/test_rag.py`.
3. **Mude o catálogo:** em `data/exams.json`, acrescente `"Ur. serica"` aos `synonyms` de `FICT-004`.
4. **Rode o que o catálogo afeta:**
   `docker compose build tests` e depois
   `docker compose run --rm tests pytest -q tests/test_rag.py tests/test_calibration.py tests/test_pii.py tests/test_readme.py`.
   O `test_readme.py` vai apontar o arquivo e a linha dos docs que citam a contagem de nomes e sinônimos do
   catálogo; corrija o número ali. É assim que a documentação fica honesta.
5. **Veja no serviço:** `docker compose up -d --build rag` e repita o passo 1: o score passa a 1,00. Para desfazer: `git checkout -- data/exams.json tests/test_rag.py docs/` e `docker compose up -d --build rag`.

## Receitas

### Acrescentar uma ferramenta a um servidor MCP existente

1. Em [`mcp_servers/rag.py`](../mcp_servers/rag.py) (ou `ocr.py`), declare a função com `@server.tool()`.
   - Use `Annotated[str, or_default('')]` nos argumentos: um tipo errado recebe uma frase em português
     (`mcp_servers/arguments.py`).
   - Acrescente o nome da ferramenta à chamada `serve(...)` no fim do arquivo, que cala os logs com valores do cliente.
2. Declare a ferramenta na spec, em `servers.<nome>.tools`, e dê a um agente: `"tools": ["rag.minha_ferramenta"]`.
3. Com o `BookingPlugin` na spec, **toda ferramenta de um agente precisa de um papel** (`read`, `search` ou `book`) ou o
   `transpile` recusa (`runtime/plugin.py`, `check_spec`). Para uma ferramenta fora desses papéis, use uma spec sem o plugin, como a
   [`exemplo-generico.json`](../specs/exemplo-generico.json).
4. Teste em `tests/test_mcp_sse.py` (chama a ferramenta pelo SSE, como o agente) e transpile de novo.

### Acrescentar um servidor MCP

1. Crie um módulo novo em `mcp_servers/` (por exemplo, `meu_servidor.py`) a partir de `rag.py`:
   - `MCPServer(...)` e `@server.tool()`;
   - a rota `/health`;
   - `serve(server, transport_security('meu-servidor:8010'), 8010, '<ferramentas>')`.

   A porta, o `Host` aceito e o nome do serviço têm de concordar.
2. No `Dockerfile`, copie o estágio `rag` (`FROM base AS meu_servidor`, `COPY` do que ele usa, `CMD ["python", "-m", "mcp_servers.meu_servidor"]`).
3. No `docker-compose.yml`, copie o serviço `rag`: `<<: *hardening`, `networks: [internal]` (sem internet) e o healthcheck em `/health`.
4. Inclua `meu-servidor:8010` em `ALLOWED_HOSTS` (`.env`): sem isso, o `transpile` recusa a URL
   ([por quê](transpilador.md#hosts-permitidos-quem-decide-é-quem-implanta-não-a-spec)).
5. Na spec: `"servers": {"meu": {"url": "http://meu-servidor:8010/sse", "tools": [...]}}`.

### Escrever uma spec nova

Copie uma das specs abaixo, salve em `specs/` e transpile, sem rebuild:

- [`specs/exemplo-generico.json`](../specs/exemplo-generico.json): um agente e um servidor MCP, sem plugin;
- [`specs/listar-exames.json`](../specs/listar-exames.json): OCR e RAG com o `BookingPlugin`, que lista sem agendar.

Os comandos e a regra da pasta de saída: [como-rodar.md](como-rodar.md#2-rodar-o-transpilador). O exemplo fora do
domínio: [transpilador.md](transpilador.md#um-agente-fora-do-domínio).

Os campos e cada erro de validação com a mensagem real: [transpilador.md](transpilador.md#campos-da-spec). Teste uma
spec nova acrescentando-a às listas de `tests/test_transpiler.py` e `tests/test_spec_generica.py`.

### Acrescentar um exame ao catálogo

1. Edite [`data/exams.json`](../data/exams.json): `{"code": "FICT-121", "name": "...", "synonyms": [...]}`. O código segue
   `FICT-\d{3}` (a API recusa outro formato), e é único.
2. Conte os efeitos:
   - `tests/test_rag.py::test_catalog_has_120_unique_fictional_codes` fixa 120;
   - `tests/test_readme.py` confere o "120" e os "240 termos" (nomes mais sinônimos) no README e nos docs;
   - um sinônimo curto pode deixar duas siglas a um caractere uma da outra. `test_rag.py::test_no_abbreviation_names_two_exams`
     e `test_short_abbreviations_one_letter_from_others_are_not_catalog_names` pegam esse caso.
3. Nomes aninhados ("Creatinina" dentro de "Clearance de creatinina") exigem atenção: `test_confianca.py` lista os 13 pares.
4. Rode `tests/test_rag.py`, `test_calibration.py` (o limiar de 0,90 sobre as 631 consultas de `tests/calibration/queries.jsonl`) e
   `test_pii.py::test_no_catalog_exam_is_masked`. Se você mudou os números, corrija os docs.

### Mudar uma regra de página ou de linha

| O que mudar | Onde mora | O teste que a trava |
|---|---|---|
| O que uma linha pede ([os tipos](glossario.md#os-valores-de-line_intent)) | `guardrails/intent.py`: `CUES`, `line_kind`, `read_page` | `tests/test_negacao.py` (linhas e exame), corpus honesto `tests/attacks/legit.txt` |
| A página é só a lista (`page_clean`, `off_list`) | `guardrails/intent.py`: `clean_page`, `a_field`, `a_label_or_count` | `tests/test_negacao_casos.py` (páginas de ataque e honestas), `tests/attacks/legit-pages.txt` |
| Piso de leitura do OCR e faixas de confiança | `runtime/confianca.py`: `BookingPolicy`, `sort_out` | `tests/test_confianca.py`, `tests/test_calibration.py` |
| Como a linha vira exames | `mcp_servers/rag.py`: `split_exams`, `unglue`, `ahead`, `behind` | `tests/test_rag.py` |
| O texto de um aviso | `runtime/relatorio.py` (`WHY`, `LEFT_OUT`, `REFUSED`) | `tests/test_confianca.py`, `tests/test_negacao.py` |

Passos:

1. Escreva o caso que falha, em `tests/test_negacao.py` ou `tests/test_negacao_casos.py`.
2. Mude a regra.
3. Rode `tests/test_negacao*.py`, `tests/test_texto_livre.py`, `tests/test_pii.py` e `tests/test_injection.py`. A regra
   não pode apagar exame nem deixar PII.
4. Se o efeito nos conjuntos medidos mudar, rode as cargas e atualize [medicoes.md](medicoes.md).
5. Acrescente a regra ao mapa [regras.md](regras.md), com o exemplo que a motivou.
6. Se mudou o texto de um aviso, confira o exemplo de [como-rodar.md](como-rodar.md#3-executar-o-agente): ele é
   comparado com o log das evidências.

Duas coisas a lembrar. Uma regra nova falha para o lado de perguntar: "só agenda sozinho o que é só exame" é a escolha
([por quê](decisoes.md#por-que-existe-uma-camada-de-regras)). E um campo ou um tipo de linha novo na resposta do OCR pede
uma versão nova do contrato (`leitura.VERSION`), nos dois lados.

### Acrescentar um padrão de PII

1. Em [`guardrails/pii_rules.py`](../guardrails/pii_rules.py), acrescente um item a `PATTERNS`: `('TIPO', r'''...''')` (regex verbose, grupos
   `value*`; veja `EMAIL` e `ENDERECO` como modelo).
2. Se o valor passar a ter formas livres de rótulo, confira também `guardrails/pii.py` (`mask`, `mask_page`) e a
   lista de tipos contados (`COUNTED`).
3. Teste: um caso em `tests/test_pii.py::test_each_pii_type_is_masked_and_counted` e um gerador em `tests/pii_corpus.py`
   (`GENERATORS`, 300 casos por tipo, semente fixa); o corpus checa que nenhum valor sobra depois de `mask`. Rode também
   `tests/test_pii.py::test_masking_never_removes_an_exam_from_a_legitimate_line`.
4. Atualize os números: `tests/test_readme.py` confere "N tipos" e "N casos gerados" nos docs.

Dois marcadores de nome parecido não se confundem:

- [`catalogo.MASK_TAG`](../catalogo.py) acha todo marcador que sai do OCR, inclusive `[TEXTO_REMOVIDO]`;
- [`pii_rules.TYPED_TAG`](../guardrails/pii_rules.py) acha só o de um valor mascarado por tipo (`[CPF]`, `[NOME]`).

O marcador interno `[INSTRUCAO_REMOVIDA]` vira `[TEXTO_REMOVIDO]` antes de sair do OCR
([a etapa](referencia.md#onde-a-pii-é-mascarada)).

## Depurar um pedido que falhou ou foi perguntado

**Escopo:** os avisos e erros que a CLI mostra sobre um pedido e os exames dele. Falhas de ambiente (Docker, chave,
portas): [como-rodar.md](como-rodar.md#quando-algo-falha).

1. **Reproduza só o OCR**, para ver o que o servidor devolve de verdade (linhas já mascaradas, `line_intent`,
   `page_clean`, `off_list`, `contested_exams`):
   `docker compose run --rm agent python exemplos/mcp_call.py ocr extract_exam_text filename=<arquivo>`.
2. **Reproduza a busca** de uma linha: `... mcp_call.py rag search_exams query="Colesterol total e Triglicerideos"`.
3. **Rode com `--verbose`** (`cli run`): logs do ADK, do Gemini (novas tentativas) e do MCP no `stderr` e, numa falha
   inesperada, o traceback, com a chave trocada por `[GOOGLE_API_KEY]`. Sem ele, toda falha é uma linha `Erro: ...`
   com código 2.
4. **Leia cada aviso na tabela de causas:** o que significa, de onde vem no código e o que fazer, em
   [referencia.md](referencia.md#avisos-sobre-os-exames-de-um-pedido).

As demais mensagens de erro (API, Host, limite, cifra, hosts proibidos): a tabela completa em
[referencia.md](referencia.md#mensagens-e-erros); o que fazer com cada falha de ambiente:
[como-rodar.md](como-rodar.md#quando-algo-falha). Para saber **por que** uma linha foi para a pergunta, ache o tipo dela
em `line_intent` (passo 1) e procure-o no mapa [regras.md](regras.md).

## Convenções

- **Estilo.** Python 3.12, com `ruff` e `mypy`, que o CI roda. Sem formatador automático.
  - Linha de até 120 colunas, por convenção: o `ruff` ignora o `E501`.
  - O `ruff` confere `E`, `F`, `I`, `B` e `C901`, com complexidade máxima 10. Uma função com mais ramos é dividida.
  - Identificadores e comentários em inglês; nomes de módulo e mensagens ao usuário em português (`confianca`,
    `reconcilia`).
- **Mensagens.** Toda falha esperada é uma linha `Erro: ...` em português, com o campo ou o motivo e, quando existe, o
  que fazer; nunca um traceback. Texto que vem da imagem nunca vai a um log sem passar por máscara.
- **Segurança por padrão.**
  - Nada de dado pessoal no modelo, no log ou no banco.
  - A política vive em `runtime/`, e nenhuma spec a afrouxa: os limiares da spec só podem endurecer.
  - Uma regra nova falha para o lado de perguntar.
- **Testes que leem a documentação.** `tests/test_readme.py` confere, sem Docker nem rede:
  - links e âncoras relativos de `README.md` e `docs/*.md`
  - caminhos citados entre crases (têm de existir; os de fora do repositório entram em `NOT_IN_REPO`)
  - os números que o repositório sabe calcular (catálogo, corpora, funções e casos de teste)
  - a saída de exemplo contra `docs/evidencias/log-run-pedido.txt`
  - o `OK:` do `transpile` contra `specs/agent.json`
  - cada comando `docker compose` (serviço, perfil, módulo).
  - Outros testes leem os docs:
    - `test_variaveis.py`: toda variável de ambiente que o código lê tem linha na tabela de `referencia.md` e entrada no `.env.example`;
    - `test_adk_comandos.py`: os comandos `adk run` e `runtime.web` do `como-rodar.md` são executados como estão escritos;
    - `test_confirmacao_nativa.py`: a pergunta `[s/N]` do exemplo do `como-rodar.md`;
    - `test_transpiler.py`: `docs/exemplo-agent.py` é a saída exata do `transpile`.
  - Quando mudar um número, uma saída ou um comando, o teste diz o arquivo e a linha.
- **Docs.** Um fato, um lugar: a referência fica num documento e os outros apontam para ele. Texto novo no estilo dos
  vizinhos: o estado atual, sem história, com o comando ou o caminho.
- **Commits.** Conventional Commits (`feat(api): ...`, `fix(ocr): ...`, `docs: ...`, `test(...)`), uma mudança lógica
  por commit, o teste junto da mudança. O histórico é agrupado por camada.
- **Não versione** `.env`, `*.db`, `*.key`, `docker-compose.override.yml` (o `.gitignore` já cobre).
