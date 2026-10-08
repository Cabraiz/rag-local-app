# Revisão: o que mudou no código e o teste que trava cada mudança

O código foi escrito com assistentes de IA (Claude Code e OpenAI Codex) sob minha direção. Esta página
mostra a revisão na prática: o que as revisões de código, os testes de carga e de robustez e os conjuntos
de ataque à máscara acharam, o que mudou por causa disso e o teste que falha se o problema voltar. Os
testes rodam sem chave e sem rede: `docker compose run --rm tests pytest -q tests/<arquivo>.py`.

## Como trabalhei

- **Direção e critérios meus:** escopo, arquitetura (agente sequencial com Gemini, OCR e RAG como
  servidores MCP via SSE, PII mascarada dentro do OCR), o contrato entre os módulos (nomes, portas,
  ferramentas MCP, formato da API), escrito antes do código, e o critério de aceite de cada parte.
  Também ficaram comigo o modelo, o limite de gasto, o que conta como dado pessoal e os segredos.
- **Uma parte de cada vez:** transpilador, servidores MCP, API e PII, e documentação foram feitos
  separadamente, todos seguindo o mesmo contrato entre os módulos. Cada parte só entrou depois de
  revisão do diff e de testes verdes.
- **Simplicidade como critério:** cada arquivo deve ser explicável em poucos minutos.
- **Como validei:** `pytest` por módulo; cliente MCP real via SSE; execução ponta a ponta com o Gemini;
  carga de 500 pedidos; 602 entradas quebradas; dois conjuntos de 79 imagens com formatos de dado pessoal
  que o gerador da carga não produz; duas revisões independentes, em que um revisor de fora (uma pessoa ou uma
  sessão de IA sem o histórico do projeto) clonou o repositório e o rodou do zero, seguindo só o README.

## Referências e orquestração do agente

- **Referências:** Google ADK ([LLM agents](https://google.github.io/adk-docs/agents/llm-agents/), [Sequential agents](https://google.github.io/adk-docs/agents/workflow-agents/sequential-agents/), [MCP tools](https://google.github.io/adk-docs/tools/mcp-tools/), [OpenAPI tools](https://google.github.io/adk-docs/tools/openapi-tools/)); Model Context Protocol ([transporte HTTP+SSE](https://modelcontextprotocol.io/specification/2024-11-05/basic/transports), [Python SDK](https://github.com/modelcontextprotocol/python-sdk); usei SSE, como fixa o escopo do estudo, embora a especificação 2025-03-26 tenha introduzido o Streamable HTTP); [Gemini API](https://ai.google.dev/gemini-api/docs), [FastAPI](https://fastapi.tiangolo.com/), [Pydantic v2](https://docs.pydantic.dev/latest/), [Tesseract OCR](https://tesseract-ocr.github.io/tessdoc/), [Docker Compose: healthcheck](https://docs.docker.com/reference/compose-file/services/#healthcheck).
- **Estratégia de orquestração do agente:** `SequentialAgent` com três `LlmAgent` em ordem fixa, `extract` (OCR via MCP) → `search` (RAG via MCP) → `schedule` (API via OpenAPI). É sequencial porque cada etapa depende da anterior, e o agendamento só acontece depois que um callback confere, em código, que os códigos vieram do RAG. Cada etapa grava sua saída numa chave de estado (`output_key`), que a seguinte lê por placeholder, e só enxerga as ferramentas que a spec lhe dá (`tool_filter`).
- **Histórico:** o histórico publicado agrupa o trabalho por camada (API, RAG, PII, OCR, runtime, transpilador, CLI, Docker, testes e documentação), montado de uma vez antes da publicação; as datas de autor são as do trabalho. As correções abaixo não aparecem como commits separados: a tabela liga cada uma ao teste que a trava.

## O que a revisão e os testes acharam

São 25 achados: 24 mudaram o código, e 23 dessas mudanças têm um teste que as trava (na 12, o teste cobre
só parte, e a varredura das siglas foi feita uma vez); a 21 testou o agente e não pediu mudança.

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
| 11 | O piso do OCR **falhava aberto**: sem a confiança de cada linha, toda leitura valia 1,0 | O OCR envia `line_confidence` alinhada às linhas. Se ela falta ou não bate, nada é agendado sem um "sim" (**falha fechado**) | `test_confianca.py::test_without_a_usable_ocr_reading_nothing_is_booked_without_a_yes`, `test_preprocessamento.py::test_the_tool_reply_has_one_confidence_per_line` |
| 12 | **Siglas ambíguas:** TG, CT, Cr, Ur, U1, BT, BD, BI, FR, GJ e HMG ficavam a 1 erro de OCR de outro exame, com score 1,0 | As 11 saíram do catálogo. GH, HDL, LDL e T3L ficaram como risco aceito | `test_rag.py::test_no_abbreviation_names_two_exams` (garante que nenhuma sigla nomeia dois exames; a varredura de 1 letra foi feita na revisão, não é teste) |
| 13 | **Keep-alive do SSE:** a carga travou depois de 499 POSTs. Cliente e servidor fechavam conexões ociosas no mesmo segundo 5, um POST se perdia, e o ping do SSE impedia o tempo limite ([python-sdk #906](https://github.com/modelcontextprotocol/python-sdk/issues/906)) | OCR, RAG e API mantêm conexões ociosas por 75 s, mais que o cliente | `test_mcp_sse.py::test_servers_keep_idle_connections_longer_than_the_client_pool`, `test_agent_mcp.py::test_agent_tools_answer_again_after_the_sessions_sit_idle` |
| 14 | Uma ordem ao modelo partida em linhas ou no infinitivo passava pelo filtro: `IA: favor marcar PSA total` | O detector junta as linhas e reconhece "favor", "deve marcar" e "agendar" | `test_injection.py::test_orders_in_the_infinitive_or_split_over_lines_are_removed_with_their_exams` |
| 15 | A cifra do banco não autenticava a data nem o status: um `created_at` editado no SQLite voltava com 200 | O dado associado do AES-GCM passou a ser id, status e data | `test_crypto.py::test_a_value_moved_or_a_clear_field_edited_in_the_database_gives_a_fixed_500` |
| 16 | A carga procurava vazamentos dentro do dado cifrado, então o "0 no banco" não provava nada | Confere os bytes crus do SQLite e do WAL, e o `GET` decifrado | `test_carga.py::test_values_stored_in_clear_are_found_in_the_database_bytes` |
| 17 | O `transpile` dizia OK para uma ferramenta inexistente (`rag.tool = "nao_existe"`) | Lista de ferramentas permitidas na spec | `test_transpiler.py::test_tools_the_services_do_not_have_are_rejected_before_any_run` |
| 18 | Uma mensagem de erro era cortada em 500 caracteres antes de esconder a chave, então um pedaço dela podia aparecer | A chave é escondida antes do corte | `test_confianca.py::test_the_key_is_removed_before_the_message_is_cut` |
| 19 | Um argumento de tipo errado (`filename: 123`) recebia o dump de validação do Pydantic | Responde com uma frase: `filename deve ser o nome de um arquivo, ex.: pedido.png.` | `test_mcp_sse.py::test_tool_errors_reach_the_client_with_a_clear_message` |
| 20 | Uma chamada bloqueada pelo agente contava como chamada à API | Só conta a chamada que chegou à API | `test_confianca.py::test_a_blocked_call_then_a_failed_model_does_not_say_the_api_was_called` |
| 21 | <a name="alucinacao"></a>Testei o que acontece se o **modelo alucinar**: inventar código ou exame, trocar o nome, pular o OCR ou a busca, fingir linhas lidas, escolher um candidato mais fraco, pôr nome e CPF nos argumentos, dizer "agendado" sem chamar a API, insistir depois de um bloqueio, pedir ao OCR outro arquivo, deixar de fora um exame que buscou. Nada errado foi agendado e nada vazou, exceto no achado 22 | Nenhuma: a suíte roda o agente gerado pelo Runner real do ADK, com um modelo roteirizado no lugar do Gemini e o OCR, o RAG e a API reais, e confere o que a API gravou | `test_alucinacao.py` (13 cenários e 1 de controle: 15 testes, 20 casos com as variações), ex.: `test_alucinacao.py::test_a_code_no_search_returned_blocks_the_whole_call` |
| 22 | **Agendamento em dobro:** se o modelo chamasse `create_appointment` duas vezes na mesma execução, a API gravava dois agendamentos iguais, e a CLI mostrava só o último | Cada execução tem uma chave própria, e o agendamento criado fica guardado: uma 2ª chamada devolve o mesmo, sem novo POST | `test_alucinacao.py::test_the_appointment_requested_twice_is_booked_once` |
| 23 | **Pedido órfão na carga:** quando a resposta do último pedido se perdia e a sessão MCP não reabria (503), o pedido voltava para a fila depois que o outro trabalhador já tinha saído, e ficava sem ninguém | Cada trabalhador só sai quando todo pedido tem resultado, não quando a fila parece vazia: um pedido que volta para a fila é pego por outro | `test_carga.py::test_an_order_whose_session_dies_is_not_orphaned_when_the_reopen_fails` |
| 24 | **Imagem girada, transparente ou PDF:** uma página de lado ou de cabeça para baixo era lida torta; um PNG de fundo transparente virava texto sobre preto; um PDF renomeado para `.png` caía num erro genérico | O OCR endireita pela orientação do EXIF ou pela detecção de orientação do Tesseract, e recusa com motivo a página que não consegue ler; o fundo transparente vira branco; o PDF recebe a frase `é um PDF, não uma imagem: exporte a página como PNG ou JPEG` | `test_ocr.py::test_a_page_turned_in_its_pixels_is_read_upright`, `test_ocr.py::test_a_phone_photo_with_exif_orientation_is_read_upright`, `test_ocr.py::test_a_png_with_a_transparent_background_is_read_on_white`, `test_ocr.py::test_a_pdf_renamed_to_png_says_it_is_a_pdf` |
| 25 | **Efeitos na importação da API:** importar `api.main` lia a chave e, se ela fosse inválida, encerrava o processo; os testes dependiam da ordem e de recarregar o módulo | A chave, o banco e o catálogo são lidos quando a API sobe (`lifespan`), não na importação | `test_crypto.py::test_importing_the_api_reads_no_setting_and_touches_no_file` |

## Segunda versão: o que as avaliações apontaram

Depois da primeira versão, avaliações independentes e revisões de código apontaram 7 pontos. 6 mudaram
o projeto, todos com teste que trava a mudança; o 7º (busca semântica) foi medido e não adotado.

| # | O que a revisão achou | O que mudou | Teste que trava |
|---|---|---|---|
| 26 | <a name="perda-silenciosa"></a>**Exame perdido sem aviso:** numa avaliação independente, em `2. Colesterol total e Triglicerideos` o agente buscou a linha inteira, agendou o Colesterol total, e o Triglicerídeos terminou sem estado nenhum (nem agendado, nem perguntado, nem avisado). Em escala, buscar a linha inteira agendava 10 de 966 exames escritos juntos em linhas limpas ([medição](medicoes.md#linhas-com-vários-exames-e-sorologias)). Separar a linha não bastava: sem olhar o exame vizinho, "Toxoplasmose IgG e IgM" agendava a IgM genérica, e 85 de 325 exames de sorologias e qualificadores saíam errados | A busca separa os exames de uma linha (sem partir um nome do catálogo) e completa um pedaço com o vizinho quando isso forma um nome: "Toxoplasmose IgG e IgM" → Toxoplasmose IgM, "PSA total e livre" → PSA livre, "Vitamina B12 e D" → Vitamina D. Um exame que o pedido não nomeia (a IgM genérica de "Chagas IgG e IgM", ou Chagas IgG para "Chagas IgM") nunca é agendado nem perguntado: só sai em `baixa confiança`. Depois da execução, a CLI confere o pedido inteiro com a mesma busca: um exame que o agente não decidiu sai como `não buscado pelo agente` ou `não incluído pelo agente`, e a confirmação termina com `ATENÇÃO: N possível(is) exame(s) do pedido sem decisão do agente, confira os avisos acima`. Nada a mais é agendado sozinho. Nas linhas limpas, 961 de 966 agendados; nas sorologias e qualificadores, 293 de 325 agendados, 0 errados e 7 sem aviso (antes, 112) ([medição](medicoes.md#linhas-com-vários-exames-e-sorologias)). Depois, o mesmo caso com o "e" grudado pelo OCR: `1) TSH e T4 livre` lido `1) TSHe T4 livre` não tinha separador, a linha era buscada inteira (T4 livre com 0,76) e o TSH não aparecia nem na conferência. Agora um "e" grudado no fim ou no começo de uma palavra que não é do catálogo separa os exames quando os dois lados são exames: TSH sai com 0,86 (perguntado, ou avisado se o modelo o deixar de fora), e `Ureiae Creatinina` deixa de virar Clearance de creatinina | `test_rag.py::test_each_exam_of_a_line_is_searched_on_its_own`, `test_rag.py::test_an_e_the_ocr_glued_to_a_word_still_separates_the_exams`, `test_rag.py::test_a_word_that_ends_or_starts_with_e_is_not_cut`, `test_reconcilia.py::test_an_exam_glued_to_the_connective_the_model_never_searched_is_reported`, `test_rag.py::test_a_piece_that_is_part_of_the_exam_next_to_it_is_searched_as_that_exam`, `test_confianca.py::test_an_antibody_class_the_order_does_not_name_is_never_booked_alone`, `test_reconcilia.py::test_the_reviewed_case_reports_the_exam_the_model_never_searched`, `test_reconcilia.py::test_the_cli_says_the_agent_left_an_exam_out_and_keeps_exit_0`, `test_alucinacao.py::test_an_exam_the_model_never_searched_is_reported_after_the_run` |
| 27 | **Transpilador de um agente só:** servidores, ferramentas e o papel de cada uma (ler, buscar, agendar) eram fixos no código, e só os 3 endereços do compose eram aceitos | A spec declara os servidores (MCP ou OpenAPI), as ferramentas de cada agente e o papel de cada uma (`roles`); sem o papel de agendar, o fluxo só lista os exames. O host e a porta de cada URL precisam estar em `ALLOWED_HOSTS`, configurado por quem implanta (padrão `ocr:8001,rag:8002,api:8000`): com o padrão, 32 endereços hostis (`169.254.169.254`, `0x7f000001`, `localhost.evil`, `localhost:2375`, `127.0.0.1:6379`, outras portas dos serviços, IPv6, Unicode) são recusados com mensagem clara, e variantes legítimas (`https`, maiúsculas, espaços nas pontas) passam. No `transpile`, cada servidor que responde confirma as ferramentas. 4 specs de exemplo, uma que só lista | `test_spec_generica.py::test_servers_take_any_name_and_the_roles_follow_the_spec`, `test_spec_generica.py::test_allowed_hosts_come_from_the_deployment_not_from_the_spec`, `test_spec_generica.py::test_a_tool_the_server_does_not_have_fails_in_transpile`, `test_spec_generica.py::test_the_listing_spec_lists_what_the_policy_accepts_and_books_nothing` |
| 28 | **Código gerado que não se sustenta sozinho:** o `agent.py` dependia do repositório inteiro, sem dizer de que versão das regras precisava | `runtime/` virou uma biblioteca com interface declarada (`API_VERSION`, `require_api`): cada arquivo gerado grava a versão e, se ela não bater, para com uma mensagem clara. O `agent.py` explica nos comentários o que declara, de acordo com a spec (agenda, agenda sem perguntar ou só lista). Importar `runtime` ou `catalogo` não lê arquivo | `test_runtime.py::test_agent_py_runs_with_the_runtime_library_alone_outside_the_repository`, `test_runtime.py::test_every_example_spec_imports_with_the_runtime_library_alone`, `test_runtime.py::test_a_generated_file_for_another_interface_stops_with_a_clear_message` |
| 29 | **Imagem do agente com ferramentas de desenvolvimento:** a imagem que roda o agente levava pytest, ruff, mypy e o Tesseract (662 MB) | O `agent` ficou só com o que roda o agente (322 MB; [medição](medicoes.md#tamanho-das-imagens)). As ferramentas e o Tesseract foram para a imagem `test`, do serviço `tests` (`docker compose run --rm tests pytest -q`), que roda só na rede interna e sem a chave do Gemini; o ponta a ponta real é o serviço `tests-e2e`, que falha sem a chave em vez de ser pulado | `test_imagens.py::test_the_agent_stage_has_no_test_tools_tests_or_ocr_engine`, `test_imagens.py::test_the_test_stage_is_the_agent_plus_the_dev_tools_and_the_project`, `test_imagens.py::test_runtime_and_dev_requirements_are_split_and_pinned`; no `tests-e2e`, `test_e2e.py::test_order_image_is_scheduled_with_codes_from_the_catalog` falha sem a chave (`E2E_REQUIRED=1`) |
| 30 | **README e docs desatualizando:** números, links e comandos eram conferidos à mão; uma avaliação achou "16.733 passam" quando eram 16.734 | Um teste lê o README e `docs/` sem Docker nem rede e confere links e âncoras, caminhos citados, os números que o repositório sabe calcular (catálogo, corpora, testes coletados), a saída de exemplo contra o log das evidências e cada comando `docker compose` | `test_readme.py::test_every_relative_link_and_anchor_resolves`, `test_readme.py::test_numbers_quoted_in_the_docs_match_the_repository`, `test_readme.py::test_the_sample_run_output_is_the_one_in_the_evidence_log`, `test_readme.py::test_compose_commands_name_files_profiles_services_and_modules_that_exist` |
| 31 | <a name="busca-semantica"></a>**"O RAG é só lexical":** uma avaliação apontou que a busca não entende paráfrases | Nenhuma no código. Medi uma busca semântica (embeddings e5-small) somada à lexical, com teto de 0,89 para que o sentido sozinho nunca agende: não agendou nenhum exame a mais em 3 conjuntos e só transformou algumas paráfrases e linhas do OCR em perguntas, com perguntas erradas a mais (3 → 7 na calibração, 16 → 18 nas linhas do OCR), ao custo de cerca de 460 MB por imagem. A busca continua lexical ([medições](medicoes.md#busca-semântica-avaliada-não-adotada)) | Nenhum: é uma medição. O limiar da busca lexical segue travado por `test_calibration.py` |
| 32 | **API sem limite por cliente:** nada limitava a quantidade de requisições de um mesmo cliente | Limite por IP (`API_RATE_LIMIT_PER_MINUTE`, padrão 1200, `0` desliga): acima dele, `429` com `Retry-After`, antes de ler o corpo; `/health` fica de fora; a tabela de clientes tem teto; um valor inválido faz a API não subir, com uma linha clara. Na carga de 500 pedidos, nenhum `429` | `test_api.py::test_over_the_limit_is_429_with_retry_after`, `test_api.py::test_health_is_exempt_and_zero_turns_the_limit_off`, `test_api.py::test_the_clients_kept_are_capped_and_the_least_recently_seen_goes_first`, `test_api.py::test_an_invalid_limit_stops_the_start_with_one_clear_line`, `test_api.py::test_429_is_in_the_openapi_contract_and_the_ip_is_not_logged` |

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

## O que as revisões independentes acharam

Nas duas revisões independentes, um revisor de fora rodou o projeto do zero, num clone limpo,
seguindo só o README. Os dois esbarraram em pontos de uso, não de código:

- o `up` falhava com as redes do Docker esgotadas;
- não havia como editar o `.env` no Windows;
- não estava dito que a pergunta `[s/N]` exige um terminal interativo;
- não estava dito como parar e limpar.

O README e `docs/como-rodar.md` passaram a cobrir cada um.


## Ainda em aberto

- **`fallback_model`:** no código gerado, o reserva da spec responde a mesma requisição quando o principal
  falha com `429`/`503`, igual no `cli run`, no `adk run` e no `adk web`.
- **`SequentialAgent` obsoleto:** no ADK 2.10 ele é marcado como obsoleto em favor de `Workflow`. Ficou
  porque `Workflow` ainda não é um `BaseAgent`, e a CLI usa o agente raiz como um
  ([decisão](arquitetura.md#decisões-técnicas-em-detalhe)).
- **Sorologias escritas por extenso podem sair sem aviso:** nas 198 linhas de sorologias e qualificadores,
  7 exames ainda terminam sem estado, como em `Sorologia para rubéola IgG e IgM`, `Sorologia para doença de
  Chagas IgG e IgM`, `IgG para Doença de Chagas` e a IgA de `Imunoglobulinas IgA e IgE total`: a doença e a
  primeira classe ficam num pedaço só, que não é um nome do catálogo.
- **Exames que o catálogo não tem:** Chagas IgM e Hepatite A (Anti HAV) não estão nos 120 exames. Uma
  linha com eles não agenda nada no lugar; a IgG ou IgM genérica que a busca acha só sai em `baixa confiança`.
- **Exame que o OCR não lê não é avisado:** a conferência do pedido só vê o que o OCR leu; nas fotos e
  manuscritas ilegíveis, esses exames continuam sem aviso.
- **Ferramentas conferidas só nos servidores que respondem:** um servidor que não responde em 3 s no
  `transpile` fica com a lista da spec.
- **Limite de requisições por processo:** com várias réplicas da API, cada uma conta o seu; atrás de
  um proxy, ou da porta publicada do Docker, os clientes do host tendem a parecer um só.
- **Nomes em português e inglês:** o código mistura os dois (ex.: `mcp_servers/preprocessamento.py`,
  `tests/load/carga.py`).
- **Sessão MCP morta:** o ADK não reabre uma sessão MCP que morreu. O keep-alive de 75 s tira o gatilho
  conhecido, não a causa.
- **Exame acrescentado como item da lista:** escrito como item próprio, igual aos outros, só a pessoa que confere
  a lista o distingue; com `--yes`, é agendado. Dentro de uma linha legítima, não agenda sozinho: em `Exame: Vitamina D
  (incluir também Ferritina)`, os dois são perguntados (a linha tem outras palavras).
