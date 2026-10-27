# Revisão: o que mudou no código e o teste que trava cada mudança

O que tem aqui: cada achado de revisão ligado à correção e ao teste que o trava, o que segue em aberto, as referências
e, no fim, como o código foi feito com IA. Leia quando quiser saber por que uma regra ou um teste existe.

O código foi escrito com assistentes de IA (Claude Code e OpenAI Codex) sob minha direção. Esta página mostra a revisão
na prática:

- o que as revisões de código, os testes de carga e de robustez e os conjuntos de ataque à máscara acharam;
- o que mudou por causa disso;
- o teste que falha se o problema voltar.

Os testes rodam sem chave e sem rede: `docker compose run --rm tests pytest -q tests/<arquivo>.py`.

## O que a revisão e os testes acharam

São 25 achados, da primeira versão, até 2026-10-06.

- 24 mudaram o código, e 23 dessas mudanças têm um teste que as trava. Na 12, o teste cobre só parte, e a varredura das
  siglas foi feita uma vez.
- A 21 testou o agente e não pediu mudança.

| # | Achado | O que mudou | Teste que trava |
|---|---|---|---|
| 1 | A carga de 500 pedidos achou 13 formas de vazamento causadas por erro de leitura do OCR: e-mail com o "@" lido como "g", CPF e RG com vírgula, `CPF1 14.…` colado ao rótulo | A máscara ganhou uma regra para cada forma (hoje em `guardrails/pii_rules.py`) | `test_pii.py::test_ocr_lines_that_once_leaked_are_masked` |
| 2 | <a name="pii-31"></a>Num conjunto à parte, com formatos que a carga não gera, **31 de 84** valores pessoais passavam: nome sem rótulo ou com `'` e `-`, CPF partido em 2 linhas, data por extenso | Máscara aplicada à página inteira e uma rede de segurança: da linha do OCR só sai o que parece exame. Agora passam **0 de 84** | `test_pii.py::test_a_cpf_split_in_two_lines_is_masked_on_both`, `test_pii.py::test_what_does_not_look_like_an_exam_does_not_leave` |
| 3 | Um segundo conjunto de testes de PII, com outras 79 imagens e nomes que a correção nunca viu, achou 1 de 83: `Anti HCV tobias fagundes` saía inteiro, porque o prenome não estava na lista | Numa linha de exame, só as palavras do exame ficam. Agora passam **0 de 83** | `test_pii.py::test_what_does_not_look_like_an_exam_does_not_leave` |
| 4 | O e-mail apagava exame: `Bilirrubina indireta - e-mail ozéiasWexemplo.invalid` virava `Bilirrubina [EMAIL]`, e `Glicemia de jejum. com 8h` era tomado por e-mail | Um sinal entre espaços encerra o e-mail, e só é e-mail o que parece um | `test_pii.py::test_ocr_lines_that_once_leaked_are_masked`, `test_pii.py::test_phrases_and_exam_lines_without_a_name_are_kept` |
| 5 | A máscara apagava exame legítimo: `TSH ULTRASSENSÍVEL` virava `[NOME]`, e `Acido urlco e Vitamlna D` sumia | Lista de qualificadores de exame, e cada exame de uma linha é julgado sozinho | `test_pii.py::test_phrases_and_exam_lines_without_a_name_are_kept`, `test_pii.py::test_two_misread_exams_joined_by_e_are_kept` |
| 6 | Uma palavra comum virava nome: `coletar em março` (por causa de "Marco") | Os meses saíram da lista de prenomes | `test_pii.py::test_first_names_are_never_exam_or_header_words` |
| 7 | Em `pedido.png`, `DADOS FICTICIOS` virava `[NOME]`, e a contagem de PII mostrada no README ficava errada | Palavra sem prenome conhecido vira `[TEXTO_REMOVIDO]`, contado à parte | `test_ocr.py::test_the_readme_sample_masks_the_same_personal_data` |
| 8 | <a name="aninhados"></a>Com os **13 pares aninhados** do catálogo, `Creatinina` e `Clearance de creatinina` em linhas separadas agendavam um exame só | O exame é procurado em todas as linhas, cada um no seu trecho | `test_confianca.py::test_the_catalog_has_the_13_nested_pairs_of_the_review`, `test_confianca.py::test_nested_names_on_separate_lines_are_two_exams_in_either_order` |
| 9 | <a name="vizinho"></a>Um **exame repetido agendava o vizinho**: `- Colesterol LDL` escrito 3 vezes agendava também Colesterol HDL (0,93) | Só o melhor resultado da busca fica preso às palavras buscadas, e cópias idênticas valem um exame | `test_confianca.py::test_an_exam_written_again_is_the_same_exam_and_never_books_its_neighbour` |
| 10 | <a name="tgp-tap"></a>Um **"TGP" manuscrito lido como "TAP"** (outro nome de Tempo de protrombina), com leitura 93, seria agendado sozinho | Pisos de leitura do OCR: 75 por linha, 85 para sigla curta e 95 para sigla que é outro nome de exame. Abaixo do piso, o exame no máximo vira pergunta | `test_confianca.py::test_a_short_code_needs_a_clearer_reading` |
| 11 | O piso do OCR **falhava aberto**: sem a confiança de cada linha, toda leitura valia 1,0 | O OCR envia `line_confidence` alinhada às linhas. Se ela falta ou não bate, nada é agendado sem um "sim" (**falha fechado**) | `test_confianca.py::test_without_an_ocr_reading_nothing_is_booked_without_a_yes`, `test_preprocessamento.py::test_the_tool_reply_has_one_confidence_per_line` |
| 12 | **Siglas ambíguas:** TG, CT, Cr, Ur, U1, BT, BD, BI, FR, GJ e HMG ficavam a 1 erro de OCR de outro exame, com score 1,0 | As 11 saíram do catálogo. GH, HDL, LDL e T3L ficaram como risco aceito | `test_rag.py::test_no_abbreviation_names_two_exams` (garante que nenhuma sigla nomeia dois exames; a varredura de 1 letra foi feita na revisão, não é teste) |
| 13 | **Keep-alive do SSE:** a carga travou depois de 499 POSTs. Cliente e servidor fechavam conexões ociosas no mesmo segundo 5, um POST se perdia, e o ping do SSE impedia o tempo limite ([python-sdk #906](https://github.com/modelcontextprotocol/python-sdk/issues/906)) | OCR, RAG e API mantêm conexões ociosas por 75 s, mais que o cliente | `test_mcp_sse.py::test_servers_keep_idle_connections_longer_than_the_client_pool`, `test_agent_mcp.py::test_agent_tools_answer_again_after_the_sessions_sit_idle` |
| 14 | Uma ordem ao modelo partida em linhas ou no infinitivo passava pelo filtro: `IA: favor marcar PSA total` | O detector junta as linhas e reconhece "favor", "deve marcar" e "agendar" | `test_injection.py::test_orders_in_the_infinitive_or_split_over_lines_are_removed_with_their_exams` |
| 15 | A cifra do banco não autenticava a data nem o status: um `created_at` editado no SQLite voltava com 200 | O dado associado do AES-GCM passou a ser id, status e data | `test_crypto.py::test_a_value_moved_or_a_clear_field_edited_in_the_database_gives_a_fixed_500` |
| 16 | A carga procurava vazamentos dentro do dado cifrado, então o "0 no banco" não provava nada | Confere os bytes crus do SQLite e do WAL, e o `GET` decifrado | `test_carga.py::test_values_stored_in_clear_are_found_in_the_database_bytes` |
| 17 | O `transpile` dizia OK para uma ferramenta inexistente (`rag.tool = "nao_existe"`) | Lista de ferramentas permitidas na spec | `test_spec_generica.py::test_a_tool_the_server_does_not_have_fails_in_transpile` |
| 18 | Uma mensagem de erro era cortada em 500 caracteres antes de esconder a chave, então um pedaço dela podia aparecer | A chave é escondida antes do corte | `test_confianca.py::test_the_key_is_removed_before_the_message_is_cut` |
| 19 | Um argumento de tipo errado (`filename: 123`) recebia o dump de validação do Pydantic | Responde com uma frase: `filename deve ser o nome de um arquivo, ex.: pedido.png.` | `test_mcp_sse.py::test_tool_errors_reach_the_client_with_a_clear_message` |
| 20 | Uma chamada bloqueada pelo agente contava como chamada à API | Só conta a chamada que chegou à API | `test_confianca.py::test_a_blocked_call_then_a_failed_model_does_not_say_the_api_was_called` |
| 21 | <a name="alucinacao"></a>Testei o que acontece se o **modelo alucinar**, de 10 jeitos ([a lista](medicoes.md#modelo-que-alucina)). Nada errado foi agendado e nada vazou, exceto no achado 22 | Nenhuma. A suíte roda o agente gerado pelo Runner real do ADK, com um modelo roteirizado e o OCR, o RAG e a API reais | `test_alucinacao.py`, ex.: `test_alucinacao.py::test_a_code_no_search_returned_blocks_the_whole_call` |
| 22 | **Agendamento em dobro:** se o modelo chamasse `create_appointment` duas vezes na mesma execução, a API gravava dois agendamentos iguais | Cada sessão tem uma chave própria, e uma 2ª chamada devolve o agendamento já criado, sem novo `POST` ([como](referencia.md#a-api-idempotência-host-limite-cabeçalhos-e-log)) | `test_alucinacao.py::test_the_appointment_requested_twice_is_booked_once` |
| 23 | **Pedido órfão na carga:** quando a resposta do último pedido se perdia e a sessão MCP não reabria (503), o pedido voltava para a fila depois que o outro trabalhador já tinha saído, e ficava sem ninguém | Cada trabalhador só sai quando todo pedido tem resultado, não quando a fila parece vazia: um pedido que volta para a fila é pego por outro | `test_carga.py::test_an_order_whose_session_dies_is_not_orphaned_when_the_reopen_fails` |
| 24a | **Imagem girada:** uma página de lado ou de cabeça para baixo era lida torta | O OCR endireita a página pelo EXIF ou pela orientação que o Tesseract detecta, e recusa com motivo a que não consegue ler | `test_ocr.py::test_a_page_turned_in_its_pixels_is_read_upright`, `test_ocr.py::test_a_phone_photo_with_exif_orientation_is_read_upright` |
| 24b | **PNG transparente:** o fundo transparente virava texto sobre preto | O fundo transparente vira branco | `test_ocr.py::test_a_png_with_a_transparent_background_is_read_on_white` |
| 24c | **PDF renomeado:** um PDF renomeado para `.png` caía num erro genérico | O PDF recebe a frase `é um PDF, não uma imagem: exporte a página como PNG ou JPEG` | `test_ocr.py::test_a_pdf_renamed_to_png_says_it_is_a_pdf` |
| 25 | **Efeitos na importação da API:** importar `api.main` lia a chave e, se ela fosse inválida, encerrava o processo; os testes dependiam da ordem e de recarregar o módulo | A chave, o banco e o catálogo são lidos quando a API sobe (`lifespan`), não na importação | `test_crypto.py::test_importing_the_api_reads_no_setting_and_touches_no_file` |

## Simplificações da revisão

- A regra de placeholders da spec era um `model_validator` que levantava `PydanticCustomError`. Virou uma
  função simples, chamada depois da validação (`placeholder_problem`, chamada por `check_agents` em
  [`transpiler/spec.py`](../transpiler/spec.py)).
- A CLI identificava a resposta da API pelo formato do JSON. Passou a identificar pelo nome da ferramenta
  (`create_appointment`).
- Quatro checagens de caminho sobrepostas no OCR viraram uma regra só.
- Quatro normalizadores de texto parecidos (PII, confiança, catálogo e injeção) viraram `catalogo.fold` e `words`, com variantes nomeadas para a busca e para a injeção, e as regex e listas da PII foram para `guardrails/pii_rules.py`. Nada mudou: 0 diferenças contra o código antigo em 7.378 textos dos corpora e em todos os 1.112.064 caracteres Unicode.
- Os testes acharam um nome de arquivo de 300 caracteres que estourava `OSError` sem mensagem, e um teste
  do RAG que passaria com resultado vazio.

## Ainda em aberto

- **`SequentialAgent` obsoleto** no ADK 2.10, em favor de `Workflow`
  ([decisão](decisoes.md#sequentialagent-obsoleto-no-adk-210)).
- **Sorologias escritas por extenso podem sair sem aviso:** 7 dos 325 exames medidos. A doença e a primeira classe
  ficam num pedaço só, que não é um nome do catálogo ([limites](medicoes.md#limites-conhecidos)).
- **Exames que o catálogo não tem,** como Chagas IgM e Hepatite A (Anti HAV): uma linha com eles não agenda nada no
  lugar ([limites](medicoes.md#limites-conhecidos)).
- **Exame que o OCR não lê não é avisado:** a conferência do pedido só vê o que o OCR leu. Nas fotos e manuscritas
  ilegíveis, esses exames continuam sem aviso.
- **Ferramentas conferidas só nos servidores que respondem:** um servidor que não responde em 3 s no `transpile` fica
  com a lista da spec.
- **Limite de requisições por processo** ([custo](decisoes.md#api-contrato-idempotência-e-defesas)).
- **Nomes em português e inglês:** os nomes de módulo são em português (`confianca`, `reconcilia`) e os identificadores
  em inglês, mas alguns arquivos de teste ainda misturam os dois (ex.: `tests/load/carga.py`).
- **Sessão MCP morta:** o ADK não reabre uma sessão MCP que morreu. O keep-alive de 75 s tira o gatilho conhecido, não a
  causa ([medição](decisoes.md#keep-alive-de-75-s)).
- **Exame acrescentado como item da lista:** com `--yes`, é agendado
  ([limite](decisoes.md#com---yes-um-exame-injetado-como-linha-comum-é-agendado)).

## Referências e orquestração do agente

- **Referências:**
  - Google ADK ([LLM agents](https://google.github.io/adk-docs/agents/llm-agents/), [Sequential agents](https://google.github.io/adk-docs/agents/workflow-agents/sequential-agents/), [MCP tools](https://google.github.io/adk-docs/tools/mcp-tools/), [OpenAPI tools](https://google.github.io/adk-docs/tools/openapi-tools/))
  - Model Context Protocol ([transporte HTTP+SSE](https://modelcontextprotocol.io/specification/2024-11-05/basic/transports), [Python SDK](https://github.com/modelcontextprotocol/python-sdk); usei SSE, como fixa o escopo do estudo, embora a especificação 2025-03-26 tenha introduzido o Streamable HTTP)
  - [Gemini API](https://ai.google.dev/gemini-api/docs), [FastAPI](https://fastapi.tiangolo.com/), [Pydantic v2](https://docs.pydantic.dev/latest/), [Tesseract OCR](https://tesseract-ocr.github.io/tessdoc/), [Docker Compose: healthcheck](https://docs.docker.com/reference/compose-file/services/#healthcheck).
- **Estratégia de orquestração do agente:** `SequentialAgent` com três `LlmAgent` em ordem fixa, `extract` → `search` →
  `schedule` ([as etapas](arquitetura.md#etapas)).
- **Histórico:** o histórico publicado agrupa o trabalho por camada (API, RAG, PII, OCR, runtime, transpilador, CLI,
  Docker, testes e documentação).
  - Ele foi montado de uma vez antes da publicação, e as datas de autor são as do trabalho.
  - As correções da [tabela de achados](#o-que-a-revisão-e-os-testes-acharam) não aparecem como commits separados:
    a tabela liga cada uma ao teste que a trava.

## Segunda versão: o que as avaliações apontaram

Depois da primeira versão, em 2026-10-07, avaliações independentes e revisões de código apontaram 7 pontos. 6 mudaram
o projeto, todos com teste que trava a mudança. Um deles, a busca semântica (#31), foi medido e não adotado.

| # | O que a revisão achou | O que mudou | Teste que trava |
|---|---|---|---|
| 26a | <a name="perda-silenciosa"></a>**Exame perdido sem aviso:** numa avaliação independente, em `2. Colesterol total e Triglicerideos`, o Triglicerídeos terminou sem estado nenhum ([números](medicoes.md#linhas-com-vários-exames-e-sorologias)) | A busca separa os exames de uma linha e completa um pedaço com o vizinho ([como](referencia.md#como-a-busca-corta-uma-linha)). Um exame que o pedido não nomeia só sai em `baixa confiança` | `test_rag.py::test_each_exam_of_a_line_is_searched_on_its_own`, `test_rag.py::test_a_piece_that_is_part_of_the_exam_next_to_it_is_searched_as_that_exam`, `test_confianca.py::test_an_antibody_class_the_order_does_not_name_is_never_booked_alone` |
| 26b | **O pedido inteiro, conferido depois:** um exame que o agente não decidia sumia sem aviso | Antes da pergunta `[s/N]` e depois da execução, o runtime confere o pedido inteiro com a mesma busca ([como](referencia.md#agendamento-conferido-em-código)). Nada a mais é agendado sozinho | `test_reconcilia.py::test_the_reviewed_case_reports_the_exam_the_model_never_searched`, `test_reconcilia.py::test_the_cli_says_the_agent_left_an_exam_out_and_keeps_exit_0`, `test_alucinacao.py::test_an_exam_the_model_never_searched_is_reported_after_the_run` |
| 26c | **"e" grudado pelo OCR:** `1) TSH e T4 livre` lido `1) TSHe T4 livre` era buscado inteiro, e o TSH não aparecia nem na conferência | Um "e" grudado separa os exames quando os dois lados são exames: TSH sai com 0,86, perguntado | `test_rag.py::test_an_e_the_ocr_glued_to_a_word_still_separates_the_exams`, `test_rag.py::test_a_word_that_ends_or_starts_with_e_is_not_cut`, `test_reconcilia.py::test_an_exam_glued_to_the_connective_the_model_never_searched_is_reported` |
| 27 | **Transpilador de um agente só:** servidores, ferramentas e o papel de cada uma (ler, buscar, agendar) eram fixos no código, e só os 3 endereços do compose eram aceitos | A spec declara os servidores, as ferramentas de cada agente e o papel de cada uma ([transpilador.md](transpilador.md#o-que-fica-onde)). Os hosts vêm de `ALLOWED_HOSTS`, de quem implanta: com o padrão, 32 endereços hostis são recusados ([as regras](referencia.md#hosts-permitidos)) | `test_spec_generica.py::test_servers_take_any_name_and_the_roles_follow_the_spec`, `test_spec_generica.py::test_allowed_hosts_come_from_the_deployment_not_from_the_spec`, `test_spec_generica.py::test_a_tool_the_server_does_not_have_fails_in_transpile`, `test_spec_generica.py::test_the_listing_spec_lists_what_the_policy_accepts_and_books_nothing` |
| 28 | **Código gerado que não se sustenta sozinho:** o `agent.py` dependia do repositório inteiro, sem dizer de que versão das regras precisava | `runtime/` virou uma biblioteca com interface declarada (`API_VERSION`, `require_api`). Um arquivo gerado para outra versão para com uma mensagem clara | `test_runtime.py::test_agent_py_runs_with_the_runtime_library_alone_outside_the_repository`, `test_runtime.py::test_every_example_spec_imports_with_the_runtime_library_alone`, `test_runtime.py::test_a_generated_file_for_another_interface_stops_with_a_clear_message` |
| 29 | **Imagem do agente com ferramentas de desenvolvimento:** a imagem que roda o agente levava pytest, ruff, mypy e o Tesseract (662 MB) | O `agent` ficou só com o que roda o agente (322 MB). As ferramentas e o Tesseract foram para a imagem `test` ([medição](medicoes.md#tamanho-das-imagens)) | `test_imagens.py::test_the_agent_stage_has_no_test_tools_tests_or_ocr_engine`, `test_imagens.py::test_the_test_stage_is_the_agent_plus_the_dev_tools_and_the_project`, `test_imagens.py::test_runtime_and_dev_requirements_are_split_and_pinned`; no `tests-e2e`, `test_e2e.py::test_order_image_is_scheduled_with_codes_from_the_catalog` falha sem a chave (`E2E_REQUIRED=1`) |
| 30 | **README e docs desatualizando:** números, links e comandos eram conferidos à mão; uma avaliação achou "16.733 passam" quando eram 16.734 | Um teste lê o README e `docs/` sem Docker nem rede e confere links, caminhos, números, a saída de exemplo e cada comando `docker compose` ([o que confere](guia-dev.md#testes)) | `test_readme.py::test_every_relative_link_and_anchor_resolves`, `test_readme.py::test_numbers_quoted_in_the_docs_match_the_repository`, `test_readme.py::test_the_sample_run_output_is_the_one_in_the_evidence_log`, `test_readme.py::test_compose_commands_name_files_profiles_services_and_modules_that_exist` |
| 31 | <a name="busca-semantica"></a>**"O RAG é só lexical":** uma avaliação apontou que a busca não entende paráfrases | Nenhuma no código. A busca semântica medida não agendou nenhum exame a mais e trouxe perguntas erradas a mais ([medições](medicoes.md#busca-semântica-avaliada-não-adotada)) | Nenhum: é uma medição. O limiar da busca lexical segue travado por `test_calibration.py` |
| 32 | **API sem limite por cliente:** nada limitava a quantidade de requisições de um mesmo cliente | Limite por IP, com `429` e `Retry-After` ([valores](referencia.md#a-api-idempotência-host-limite-cabeçalhos-e-log)). Na carga de 500 pedidos, nenhum `429` | `test_api.py::test_over_the_limit_is_429_with_retry_after`, `test_api.py::test_health_is_exempt_and_zero_turns_the_limit_off`, `test_api.py::test_the_clients_kept_are_capped_and_the_least_recently_seen_goes_first`, `test_api.py::test_an_invalid_limit_stops_the_start_with_one_clear_line`, `test_api.py::test_429_is_in_the_openapi_contract_and_the_ip_is_not_logged` |

## O que as revisões independentes acharam

Nas duas revisões independentes, um revisor de fora rodou o projeto do zero, num clone limpo,
seguindo só o README. Os dois esbarraram em pontos de uso, não de código:

- o `up` falhava com as redes do Docker esgotadas;
- não havia como editar o `.env` no Windows;
- não estava dito que a pergunta `[s/N]` exige um terminal interativo;
- não estava dito como parar e limpar.

O README e `docs/como-rodar.md` passaram a cobrir cada um.

## Como trabalhei

- **Direção e critérios meus:**
  - o escopo e a arquitetura: agente sequencial com Gemini, OCR e RAG como servidores MCP via SSE, PII mascarada dentro
    do OCR;
  - o contrato entre os módulos (nomes, portas, ferramentas MCP, formato da API), escrito antes do código;
  - o critério de aceite de cada parte;
  - o modelo, o limite de gasto, o que conta como dado pessoal e os segredos.
- **Uma parte de cada vez:** transpilador, servidores MCP, API e PII, e documentação foram feitos
  separadamente, todos seguindo o mesmo contrato entre os módulos. Cada parte só entrou depois de
  revisão do diff e de testes verdes.
- **Simplicidade como critério:** cada arquivo deve ser explicável em poucos minutos.
- **Como validei:**
  - `pytest` por módulo e um cliente MCP real via SSE;
  - execução ponta a ponta com o Gemini;
  - carga de 500 pedidos e 602 entradas quebradas;
  - dois conjuntos de 79 imagens com formatos de dado pessoal que o gerador da carga não produz;
  - duas [revisões independentes](#o-que-as-revisões-independentes-acharam), por uma pessoa ou uma sessão de IA sem o
    histórico do projeto.
