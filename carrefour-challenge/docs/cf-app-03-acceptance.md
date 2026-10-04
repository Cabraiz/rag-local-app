# CF-APP-03: contrato selado antes da implementação

Base: `3ed9592309fe91b70221432944dc5111636a548f`, branch `codex/carrefour-exec-03`.
Ownership: jornada e gate em runtime; API, CLI, compiler, catálogo e MCP permanecem com seus cards.

Esperado: estados entender, localizar, esclarecer, consultar horário, confirmar,
resultado incerto e concluir/cancelar. Só fontes autoritativas fundamentam exames,
slots e recibos. Consentimento é booleano explícito, vinculado à oferta exibida.
Retomada usa o mesmo request_id/corpo; cancelamento incerto primeiro reconcilia.

Proibido: diagnosticar, inventar exame/horário/confirmação; reserva anterior ao
consentimento; consentimento reaproveitado após mudança de preferência; tool fora
da allowlist; retry com chave nova; anunciar cancelamento de reserva sem recibo.

| Caso fixo | Oráculo mínimo |
| --- | --- |
| Feliz: hemograma, slot sintético, confirmar | estados ordenados; um reserve após consentimento; recibo CONFIRMED correspondente |
| Ambíguo / vazio / sintoma / exame ausente | esclarecer; zero reserva; nenhuma inferência clínica |
| Preferência alterada / slot não oferecido | oferta antiga invalidada; zero reserva |
| Indisponível / erro lookup ou slots | esclarecer; nenhuma disponibilidade fabricada |
| Negar / cancelar antes da confirmação | zero reserva; cancelamento local explícito |
| Timeout após commit / replay / retomada | resultado incerto; reconcile com mesma chave; uma reserva persistida |
| Cancelar após timeout | reconcile antes de cancel; nunca repetir reserve |
| Receipts forjados / status REQUESTED / slot divergente | nunca concluir; resultado incerto |
| Turno repetido / ID reutilizado com payload diferente / concorrência | nenhuma duplicação; conflito rejeitado |
| Injeção / consentimento string / oferta obsoleta / tool não declarada | falhar fechado; spies provam zero reserva |
| ADK Runner local | Workflow real executa turno estruturado; zero chamadas de modelo |
| Graph legado | schedule sem jornada/consentimento falha antes de book |

Prova: testes de domínio com gateways/spies inteiramente fictícios e Runner ADK
real offline no container de dependências existente, sem rede externa. Duas
rodadas com image ID, fontes, seed e harness congelados. Estes spies não provam
integração API/SSE; gates de integração dependem de CF-APP-06/08/09 e revisão da
Central. Não enfraquecer o contrato REQUESTED legado para chamá-lo confirmado.

## Retomada: contrato do produtor e impedimento externo

Antes da edição desta retomada, a API publicada em localhost:8860 e os refs
locais CF-APP-06/08 continuam na base inicial. O OpenAPI real lista somente
health, create/read/by-request; request contém request_id/exam_codes/catalog_version,
e receipt tem status REQUESTED. Não há slot/consentimento/cancelamento.

Menor prova adicional: lançar API e RAG existentes em subprocessos isolados no
container sem rede externa, com SQLite efêmero e catálogo fictício; executar a
Journey pelo gateway real e ADK real; conferir HTTP 404 de slots, 422 para o
payload consentido, ausência de POST no fluxo e zero linhas persistidas. Um
REQUESTED realmente persistido não pode ser convertido em CONFIRMED. O receipt
registra OpenAPI/digest e o impedimento; estes negativos não contam como fluxo
positivo integrado. Não implementar endpoints/persistência nem editar CLI de
outros owners para contornar a incompatibilidade.

O contrato de diagnósticos (`validation.issues`) mascara campos desconhecidos
como `body.[extra]`. A prova de 422 exige `extra_forbidden`, duas ocorrências no
payload combinado e uma em cada request com campo isolado; não exige eco dos
nomes desconhecidos. O primeiro teste desta retomada assumiu esse eco e falhou,
sem falha do produtor. A correção do oráculo reinicia ambas as rodadas.

## Integração com produtor evoluído, antes da rodada qualificada

CF06 em Git: `94450860b8089b4a5b361a06f4476878af5e7680`. Consumir seus schemas
públicos em snapshot imutável em `.local`, sem editar/cherry-pickar componentes
do owner nem mudar a branch. Slots são lista de UUIDs/horários/available. Criar
requer referência inteiramente fictícia e confirmed booleano. Receipt não leva
patient_ref/confirmed: rejeitar esses campos. Retomada/cancelamento usam bearer
privado da criação; cancel tem UUIDv4 próprio e persistido no checkpoint. O
horário do recibo deve coincidir com a opção consentida, comparando instantes.

Nove casos live selados: ADK/RAG SSE/HTTP/SQLite reais; confirmar/cancelar uma
vez; interrupção antes de POST e resposta retida após commit real; cancelar
após incerteza sem novo create; negar/injetar sem gravação; disputa de slot e
consentimento concorrente sem duplicar; preferência Unicode revoga a oferta;
CLI CF08 stdin com sim e não, sem copiar/editar seus arquivos (mounts read-only).
Faults são locais ao cliente, sem alegação de perda TCP real. Mesmos dois
perfis, imagem, fontes e seed em duas rodadas; snapshot e hashes também da CLI.

A Central confirmou este executor como único writer após interromper o revisor
que alterou fontes em paralelo. A sequência contaminada permanece reprovada.
`tools/verify_journey_integrated.ps1` é o gate atual; `verify_journey.ps1` conserva
o gate histórico do produtor inicial, não é prova da integração evoluída.

## Retomada de privacidade antes da escrita

Interface estável CF07 documentada em seu `docs/cf-app-07-privacy-contract.md`:
`private_mcp_output`, `ocr_output`, `rag_output`, `private_boundary` e
`public_failure_code`. Consumir módulos e oráculos do owner somente read-only,
com hashes congelados; não editar helpers, CLI, API, OCR ou RAG server.

Esperado: validar/projetar OCR e RAG antes de devolver output ao SDK; nomes,
códigos/evidências devem ser cópias canônicas independentes de referências
remotas. Exceções de nodes tornam-se códigos fixos antes de eventos/sessões.
Proibido: PII literal/normalizada ou erro bruto em saída, sessão, traceback,
logs; falso sucesso no fluxo linear sem consentimento; hooks globais no SDK.

Menor prova: os 10 oráculos originais CF07 de runtime/ADK, sem adaptadores de
teste, mais Runner real da jornada e exceções/outputs com canários. Matriz
composta usa CF06, helpers CF07 e CLI CF08 read-only, dois rounds idênticos.
O oráculo legado CF07 de sucesso exige POST REQUESTED sem consentimento e
não é aceitação da jornada atual; registrar essa incompatibilidade separada.
O finding de filename da CLI continua fora do ownership CF03.

O gate tem três perfis por rodada: regressão no contrato base, integração real
CF06/CF08 e matriz de privacidade CF06/CF07/CF08. Modos de desenvolvimento com
menos perfis/rounds nunca geram READY_FOR_CENTRAL_REVIEW. Os helpers são
dependência explícita do candidato, fornecida pelo owner e não versionada/copied
por CF03. O validate local mantém compatibilidade com entradas históricas sem
hash explícito, mas valida todos os campos contra o catálogo exato; retrieve
remoto continua exigindo hash recebido igual ao catálogo.

Também antes do egress MCP: nomes devem corresponder ao catálogo e argumentos
não declarados/PII são rejeitados antes de construir o toolset. Menor prova:
constructor spy com zero chamadas para canários, Unicode, nome desconhecido,
lista fora de limite e campos extra; execução real confirma a compatibilidade.

Fixtures de envelopes SSE usavam palavras de modo de falha como exam_names.
Agora o controle usa nomes fictícios canônicos, mantendo respostas e asserts
originais. Contador no servidor hostil comprova uma chamada SSE por negativo,
para impedir que rejeição de entrada conte como prova do gate de envelopes.
Nenhum servidor OCR/RAG de produto foi alterado.

## Retomada após correção CF07, contrato antes da escrita

Receipt do owner: `.local/cf07/receipt-egress-fix.md`. Helper exato obrigatório
`960069e7f4532c8c60371c59e3d6a67dc014ed3da17d302ec08d3b44db9f6811`;
privacy.py permanece `6b6b718267bb95631d2794264faef98da220966abac753b117dd635f169ed17e`.
O hash anterior `83cfee...` e sua sequência composta estão invalidados.

Esperado: provider builtin OCR/RAG e schema exato dos argumentos são conferidos
antes de invoke; nomes conhecidos projetados/deduplicados e argumentos copiados.
Proibido: executar parcialmente argumentos com extras, callback para provider
inválido, e aceitar exame diferente após mutação do callback. A API/assinatura
continua compatível com os consumidores CF03; não editar/copiar helpers no repo.

Menor prova: harness rejeita hashes diferentes antes dos testes e repete 89
oráculos novos CF07 (79 zero invoke, 10 válidos/cópias/mutação) no perfil composto,
mais os testes originais do consumidor. Duas novas rodadas completas com fontes,
helpers, testes, imagem, configuração e seed congelados; revisão independente
posterior ainda obrigatória. Esta prova nova não reutiliza os resultados antigos.

## Reconciliação CF03 sobre main após PR 3

Base final recebida: `9b80d2133a9a855cf7cb11616ca36d753c0725aa`.
Preservar somente o delta CF03 e os helpers mergeados com hashes aprovados.
Rebase comum tentava reaplicar sete commits ancestrais externos ao delta e foi
abortado; rebase com boundary na base antiga transporta o trabalho sem reescrever
esses componentes. Backup completo por arquivo/hash e stash são preservados.

Esperado: runtime revisado mantém decode de envelopes sem fallback ambíguo,
transporte/recibos limitados, identidade e gates de consentimento/privacidade.
Proibido: perder essas proteções na resolução automática; alterar helpers/API/
CLI/compiler/catalog/OCR/RAG de produto ou reaplicar commits alheios inteiros.
Menor prova: mesmos asserts de envelopes/transporte, fixture API efêmera dentro
do harness CF03 e duas sequências completas na árvore final. Helpers/testes CF07
vêm da base mergeada, sem shadow de outra lane. Live usa source final com somente
API/contracts CF06 como dependência em snapshot privado, não branch integrada.

CF08 checkpoint publicado fixa os quatro cli*.py; conferir antes e depois das
rodadas. A matriz contém exatamente 10 casos legacy runtime/ADK, não 11.

Checkpoint CF08 supersede o anterior: gateway
`304e92edc9c238df519384e41bd0f67884002acd1f617e60d63e4e0d240ef034`.
CF06 público evoluído commit `5f13c25c4d1fb86bf88bb26f97a7a2e75ad15c9b`
separa AppointmentReceipt legacy de ConfirmedAppointmentReceipt. Consumidor
usa o schema confirmado se publicado, sem fallback ao legacy após erro de
validação, sem fabricar confirmed e sem promover REQUESTED. Menor prova nova:
seleção explícita do schema e rejeição de REQUESTED, mais OpenAPI/HTTP/SQLite
reais dessa versão. O overlay privado contém somente os cinco módulos API/
contracts/reservation; todo o restante vem da árvore final CF03 sobre main.

CF08 novo checkpoint de linha longa: `cli_dialogue.py`
`6d05c2a8fc1bdf41cb0479113f36e15239fe04518a846fa80ee658044990ac6a`.
Publicação do owner: `.local/cf08-qa/sources.json`
`35b4602ff29b82bf881cb8e16bd48757fed7950aa0569872c02c900d540d0c97`.
O pin antigo b4a2d4 foi superado após o run integrado 212956; seus passes são
históricos. Esperado: repetir duas sequências completas com o novo dialogue e
demais pins inalterados, sem editar a CLI. Proibido: transferir os passes do
snapshot antigo para o atual. Menor prova: seis perfis e manifesto sem drift.
