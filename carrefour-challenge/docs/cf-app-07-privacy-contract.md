# CF-APP-07: contrato selado antes da implementacao

Base: `codex/carrefour-exec-07`, HEAD `3ed9592309fe91b70221432944dc5111636a548f`, checkout limpo verificado em 2026-10-03.
Ownership: somente `carrefour-challenge/`; a fila e integracao pertencem a Central.

Esperado: PII pessoal sintetica permanece somente na entrada efemera. OCR retorna nomes canonicos do catalogo; RAG e recibos passam validacao antes de eventos, contexto ou saida. Logs e erros usam diagnosticos sem valores de entrada. CLI nao reflete argumentos sensiveis. O checkout base ainda usa request_id UUID, exam_codes e catalog_version. No contrato CF06 recente, request_id UUIDv4, exam_codes unitario, catalog_version, slot_id, patient_ref ficticio e confirmed:true formam o request minimo. Preservar esses campos no destino autorizado de reserva, sem coletar nome/documento/contato; patient_ref nao aparece no receipt.

Proibido: nome, documento, email, telefone, endereco, nascimento, metadados livres ou instrucoes OCR em tools posteriores, eventos/sessao ADK, stdout/stderr, API, SQLite ou logs. Rejeitar depois de publicar um evento nao atende ao contrato. Nao ignorar linha de exame desconhecido para reservar parcialmente. Nao mascarar UUID/codigo/versao/slot_id/consentimento/patient_ref ficticio necessarios ao request autorizado da reserva. Patient_ref nao e nome de paciente e nao concede permissao de propaga-lo ao OCR/RAG, prompt, trace, logs ou receipt.

## Matriz de oraculos

| Entrada/caso | Sink capturado | Menor prova |
| --- | --- | --- |
| OCR com cabecalhos pessoais em ASCII, NFKC e acentos decompostos | resultado OCR, requests RAG, eventos/sessao ADK, CLI, API, SQLite | so nomes/codigos canonicos; canarios ausentes literal e normalizados |
| OCR com PII em linha de exame ou controle invisivel | erro, logs, traces | falha sem texto bruto e zero request de reserva |
| RAG com nome/evidencia/extra adulterados | evento de retrieval e excecao | rejeicao antes de retorno/evento; zero POST |
| SDK/tool com excecao ou timeout contendo canario | excecao formatada, CLI stderr, logs | codigo fixo, sem texto/argumentos de excecao |
| JSON/API invalido com canarios nos valores e chaves, erro SQLite | resposta HTTP, logs, SQLite | erro sanitizado e nenhum campo pessoal persistido |
| CLI com argumento invalido ou nome de artefato sensivel | stdout/stderr | nao refletir argumento/nome; preservar hash verificavel do fonte |
| reserva valida/retry | resposta e SQLite | campos minimos exatos, mesmo request_id/recibo, sem PII |
| request CF06 com patient_ref ficticio | schema do request e registro interno autorizado | aceitar FICT-PAT-NNNN sem substituir ou apagar a referencia; rejeitar nome/CPF/email/telefone reais ou canarios nesses campos |
| patient_ref em metadata OCR/RAG, erro e receipt | gates dos helpers e schema do receipt CF06 | descartar metadata antes de eventos, falha publica fixa, receipt rejeita campo extra; nao afirmar prova HTTP/SQLite por validacao de schema |
| logs com msg, args, exc_info, stack_info e extra | stdout/stderr | somente evento fixo |

Fixtures totalmente ficticias, incluindo Unicode. Busca de canarios usa JSON decodificado, NFKC/NFKD, casefold, remocao de marcas e controles e comparacao alfanumerica para documentos/telefones. Nenhum token ou chave real.

Prova planejada: regressao local com falhas induzidas explicitamente, mais OCR Tesseract/MCP SSE/ADK/API/SQLite reais em projeto Compose isolado. Capturar logs e eventos, repetir duas rodadas com mesmas fontes/imagem/config/semente/harness e verificar hashes. Mocks demonstram apenas fronteira de falha, nunca transporte real. Revisao independente e integracao permanecem gates da Central.

## Mapa de fronteiras

| Fronteira | Minimizacao antes do sink | Valor integral autorizado |
| --- | --- | --- |
| JSON e CLI | schema fechado, diagnostico campo/tipo, sem eco de argumentos | DSL validada em artefato local solicitado; nao e entrada de paciente |
| imagem/OCR | imagem efemera, stderr Tesseract descartado, cabecalhos descartados, catalogo fechado | bytes de imagem somente OCR local |
| MCP OCR | so nomes canonicos, contadores e codigos fixos | referencia local de imagem somente ferramenta OCR |
| MCP RAG | nomes canonicos e evidencia comparada ao catalogo antes de eventos | codigos/nome/evidencia de exame ficticio |
| prompt/contexto e eventos ADK | mensagem fixa, sem imagem/texto OCR/patient_ref, somente saida validada e projecao de receipt privado | identificador de correlacao nao secreto separado; request_id capability CF06 nao e metadado publico |
| logs/traces | handler nao formata msg/args/excecao; access log e OTEL desabilitados no Compose | evento/diagnostico fixo |
| excecoes | codigos fixos e supressao da cadeia bruta no formato publico | diagnosticos estruturados de schema sem input/contexto |
| API/SQLite | contrato fechado e validacao de recibo armazenado | UUID, codigos, versao, slot_id, consentimento, digest e patient_ref ficticio no request/registro interno CF06; receipt sem patient_ref |
| artefatos de teste | canarios sinteticos somente fixtures locais; captura nao versionada em .local | IDs sinteticos e hashes para reproducao |

Limites: catalogo local e DSL de configuracao sao fontes administradas, nao documentos de pacientes. OTEL externo/provider/cloud e PII real nao fazem parte desta prova. Compatibilidade com o novo contrato de slots/consentimento de CF-APP-06 exige revalidacao integrada pela Central; nao mudar dominio de reserva neste card.

## Decisao apos diretriz corretiva de ownership

Somente `privacy.py`, `privacy_sinks.py` e testes/harness/documentacao dedicados sao alterados. Os hunks experimentais de `runtime.py` e `cli.py` foram retirados integralmente; diff desses arquivos permanece vazio. Nenhum alinhamento adicional e necessario para os helpers compativeis abaixo. Nao instalar hooks globais nem alterar o contrato de reserva para contornar ownership.

`privacy.py` normaliza NFKC antes de regex de PII e reconhecimento de cabecalhos OCR. Controles invisiveis no OCR rejeitam a extracao completa. `privacy_sinks.py` fornece `ocr_output`, `rag_output`, `private_mcp_output`, `private_boundary`, `public_failure_code` e `artifact_acknowledgement` para consumidores, sem mutar componentes dos outros cards.

Integracao necessaria pelo owner: aplicar `private_mcp_output(provider, arguments, invoke, catalog)` ao cliente MCP antes de publicar qualquer evento; aplicar `private_boundary` antes da excecao do node chegar ao SDK; imprimir `artifact_acknowledgement(source)` em vez de nome livre no ack da CLI. Os helpers preservam o codigo de resultado de reserva incerto. Eles tratam somente OCR/RAG, falhas publicas e ack, nunca o request da reserva: o owner deve enviar patient_ref ficticio intacto a CF06 e impedir sua propagacao ao receipt/eventos publicos.

Os testes `test_cf07_privacy_sinks.py` exercitam os consumidores sem instalar adaptadores. Eles reproduzem 11 falhas no estado inicial: 4 rows RAG adulteradas publicadas, 1 referencia remota mutavel, 2 excecoes de node brutas, 3 capturas de sessao/trace ADK com canarios e 1 filename refletido. Esses oraculos permanecem obrigatorios e vermelhos; nao usar skip/xfail ou marcar helpers verdes como conclusao do card. O contrato exige revisao e integracao dos owners pela Central antes de aceite.

Comando reproduzivel do segmento: `python carrefour-challenge/tools/run_cf07_privacy.py --output D:/RAG-Worktrees/rag-exec-07/.local/cf07/novo-run`. O harness verifica a imagem local fixa, nao baixa dependencias, usa projeto/port/volumes proprios, repete o mesmo seed e encerra somente o projeto criado, sem remover volumes. Artefatos antigos depois de mudanca nao contam como aprovacao.

## Revisao independente e contrato CF06 recente

A Central comunicou revisao independente confirmando os helpers aprovados e as mesmas 11 falhas consumers em duas rodadas. Isso aprova o segmento de helpers revisado, nao instala os helpers nem fecha CF-APP-07. Runtime/ADK pertencem a CF03 e CLI a CF08. Nenhum hunk nesses consumidores ou API/OCR/RAG/Compose e aplicado por esta lane.

Fonte CF06 lida somente leitura: `D:/RAG-Worktrees/rag-exec-06/carrefour-challenge/docs/cf-app-06-reservations.md` e `src/clinic_adk/contracts.py`. O teste dedicado usa snapshot local nao versionado desse arquivo real, por hash, sem inventar outro schema ou implementar API/ledger. Campos do receipt sao appointment_id, request_id, exam_codes, catalog_version, slot_id, starts_at e status CONFIRMED/CANCELLED; patient_ref e confirmed nao sao campos publicos do receipt.

Request_id agora tambem e capability privada: corpo/header autorizado da API e recibo privado sao destinos necessarios; logs/traces/prompts/MCP nao podem publica-lo. O endpoint CF06 by-request ainda inclui essa chave no caminho, portanto nao logar URLs e preferir reconciliacao por appointment_id quando conhecido. A ausencia de log de acesso nao equivale a uma prova de todos os sinks. CF03/08 devem projetar o receipt privado antes de publicar eventos ou diagnosticos. Os helpers existentes nao sao projetores de receipt/capability e nao devem ser usados para apaga-los do request.

## API estavel de helpers para CF03/08

Importar de `clinic_adk.privacy_sinks`; erros sao `clinic_adk.errors.SafeError`. Nenhuma dessas funcoes instala hooks nem faz rede/persistencia por conta propria.

| Assinatura | Entrada/saida e ponto de uso |
| --- | --- |
| ocr_output(value, catalog) -> dict | Payload OCR decodificado; requer ok:true, pii_masked:true, unresolved_count inteiro zero e 1..20 exam_names. Retorna somente nomes canonicos deduplicados e flags; metadata, inclusive patient_ref, nao segue a eventos. Aplicar antes do retorno do node OCR. |
| rag_output(value, names, catalog) -> dict | Requer catalog_version atual, unresolved_indices vazio e rows exatas name/code/evidence, iguais ao catalogo e aos nomes solicitados. Retorna copia canonica nova; extras por row sao rejeitados, metadata do envelope descartada. Aplicar antes do retorno do node retrieve, nunca somente no node validate seguinte. |
| await private_mcp_output(provider, arguments, invoke, catalog) -> dict | Valida provider string ocr/rag e dict exato image_ref ou exam_names antes de chamar invoke. OCR exige basename ASCII png/jpg/jpeg, 1..100 caracteres; RAG exige lista 1..20 de strings 1..120 conhecidas no catalogo, projetadas para nomes canonicos deduplicados em copia nova. Extras/tipos/valores indevidos rejeitam tudo com zero chamadas, codigo MCP_INVALID_TOOL_ARGUMENTS. Invoke e o cliente MCP decodificado existente, await invoke(provider, projected_arguments). Usa os gates de saida acima e converte erro/timeout em codigo publico fixo. Nao envolver booking com este helper. |
| private_boundary(async_function) -> async_function | Decorator antes de o SDK publicar erro do node. Preserva cancelamento e converte Exception em SafeError com codigo publico. Nao altera payload de sucesso; nao substitui gates OCR/RAG. |
| public_failure_code(error) -> str | Examina ate 16 nos de cause/context/group, aceitando somente PUBLIC_CODES fixos; fallback WORKFLOW_FAILED_SAFE. Preserva APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY. Nao serializa texto, argumentos ou codigos arbitrarios. |
| artifact_acknowledgement(source: bytes) -> dict | Retorna ok:true, generated:true e sha256 do fonte; nao recebe filename. Usar no stdout do transpile depois de escrita autorizada. |

`privacy.py` tambem exporta query_safe(value) -> str normalizado e sanitize_ocr(raw, catalog) -> dict; seu hash e o de privacy_sinks.py sao emitidos no receipt. Alteracao futura desses bytes invalida a aprovacao revisada ate novas rodadas/review. A atualizacao CF06 altera somente docs/testes, mantendo os dois helpers byte a byte.

## Correcao do apontamento independente: argumentos antes do invoke

Esperado selado antes da escrita: provider deve ser string ocr/rag antes de qualquer callback. Arguments deve ser dict com exatamente image_ref para OCR ou exam_names para RAG. Extras (patient_ref, request_id, headers, _meta, chaves com canarios), chaves ausentes, tipos incompatíveis, tamanhos invalidos e valores indevidos rejeitam a operacao inteira com zero chamadas. Nao descartar silenciosamente um argumento desconhecido e executar parcialmente.

OCR: referencia string nao vazia com ate 100 caracteres, basename ASCII com letras/digitos/hifen/underscore e extensao png/jpg/jpeg; sem caminho, texto pessoal, controles ou URL. Nao verificar arquivo ou executar filesystem nesta fronteira; existencia/permissao permanecem no servidor OCR. O caller deve fornecer referencia sintetica opaca, nunca filename derivado de paciente.

RAG: lista com 1..20 strings de 1..120 caracteres, cada nome passa query_safe e pertence ao catalogo. Projetar nomes/aliases para nomes canonicos deduplicados, em copia nova. Nunca encaminhar texto livre ou nomes desconhecidos ao callback. Provider OCR nao aceita exam_names, e RAG nao aceita image_ref.

Menor prova: spies registram zero chamadas para cada provider/argumento invalido; payloads validos produzem uma chamada com apenas campos autorizados e copia independente; callback que muta a copia nao pode adulterar o request original nem o oraculo de nomes RAG. Erros publicos fixos MCP_TOOL_MANIFEST_MISMATCH/MCP_INVALID_TOOL_ARGUMENTS nao refletem input. Callback so ocorre depois da validacao/projecao completa. Duas rodadas congeladas e novo hash do helper; aprovacao anterior desse helper fica supersedida ate review da correcao.

## Candidato de integracao CF07 sobre main

Base solicitada e verificada em origin/main: `4de574ff62a62381eeda180c7c9567db11eec1e1`. Branch propria `codex/carrefour-cf-app-07-integration`, worktree `D:/RAG-Worktrees/rag-exec-07-integration`. O worktree original e os consumidores permanecem preservados. Transferir somente os dez arquivos de helpers, testes, harness e este contrato; sem commit, push, PR ou merge nesta fase.

A Central confirmou review independente da correcao com 131 testes em cada uma de duas rodadas. SHA-256 aprovado de privacy_sinks.py: `960069e7f4532c8c60371c59e3d6a67dc014ed3da17d302ec08d3b44db9f6811`; privacy.py: `6b6b718267bb95631d2794264faef98da220966abac753b117dd635f169ed17e`. Esses bytes sao preservados no candidato. A regra de cabecalho OCR completo com dois-pontos tambem e preservada: `Nome do exame: ...` nao pode ser descartado como cabecalho pessoal para permitir reserva parcial.

Revalidar o segmento de 131 testes duas vezes na arvore final, com src/tests/tools/data deste candidato montados somente leitura em imagem local fixa e rede desabilitada. Isso inclui o catalogo CF05 presente na base atual. O contrato CF06 futuro vem do snapshot revisado `bcdbe4d413244f8c06bc663af2f531bdc552ddcebfa64c700af9dbd262db8d86`, pois contracts.py nesta base ainda e legado REQUESTED. O fonte atual do owner CF06 avancou depois desse snapshot; registrar seu hash e nao afirmar compatibilidade com essa nova versao. Esse teste comprova compatibilidade com o schema revisado, sem comprovar HTTP, SQLite ou reserva integrada CF06.

Os 11 oraculos dos consumidores continuam pendentes de adocao pelos owners e revalidacao pela Central. Rodadas verdes dos helpers nao aprovam consumidores, transporte real, logs reais, reservas ou o card integrado. O receipt local registra base, arvore candidata, diff, hashes, comandos, duas rodadas e limites; a Central revisa antes de qualquer PR/merge.
