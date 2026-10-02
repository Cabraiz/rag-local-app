# RAG local — implementação em fatias

Backend Python + Google ADK e frontend próprio, em containers. A arquitetura é um monólito modular com portas nas fronteiras de infraestrutura. `ADK Web` é ferramenta de desenvolvimento; não substitui a interface do produto.

## Estado e limites

### Runtime atual: Gemini conectado em 01/10/2026

O laboratório ativo combina base + retrieval + semantic + integrations +
`compose.gemini-rag.yaml`. API/worker/grafo ADK/Qdrant/PostgreSQL foram testados
com Gemini Developer API real, modelo `gemini-3.5-flash-lite`, projeto Free sem
faturamento vinculado. O Gemini avalia relevância e escolhe um chunk; não gera
livremente fatos nem aciona ferramentas. A aplicação publica somente o trecho
literal com citação canônica, ou se abstém. Timeout/quota/JSON inválido não
acionam fallback pago nem retry de modelo. Apenas dados sintéticos neste recorte.

Duas rodadas consecutivas de 37 checks E2E aprovadas:
`../eval/runs/gemini-rag-20261001/live-072334.json`. Contratos SDK/ADK e guards
locais: duas rodadas de 24 checks cada suíte, também dentro do container.
UI real conferida com resposta de 45 reais e indicador de Gemini consultado.
Não são auditoria cega independente, prova de 100.000 requests ou produção.

Comando/runtime, fronteiras, recibos e limitações atuais:
[`gemini-rag-runtime.md`](../docs/architecture/gemini-rag-runtime.md).
Gemini não ingere automaticamente o feed Jira/GitHub; criar/mover cards, Vertex
e DeepAgents continuam fora deste fluxo. Login sem senha é demonstrativo.

### Perfis e resultados históricos (não substituem o estado atual)

O default continua a fatia de ciclo de vida com abstenção determinística. O perfil opt-in `compose.retrieval.yaml` acrescenta corpus sintético versionado, Qdrant real e grafo nativo ADK (retrieve → compose → verify), com resposta extrativa. Isto não é RAG neural completo nem release de produção. Não usa credenciais nem chamadas cloud. O modo `production` é recusado e `/health/ready` informa `production_ready: false`.

Novas versões lexicais usam a coleção compartilhada `rgl_shared_lexical_256_v1`,
com índices de payload para tenant, ator e release. O layout fica registrado no
catálogo SQL: versões antigas continuam usando sua coleção original, sem apagar
ou migrar implicitamente os dados. Chunks e snapshots continuam separados por
IDs de versão; o catálogo e os gates de citação permanecem canônicos. A carga
das coleções antigas usa concorrência limitada a quatro, mantendo o teto LAB de
120 segundos. Isso não comprova RTO de produção, HA nem qualidade semântica.

O índice recebe somente IDs e escopo. Texto canônico e ACL ficam no PostgreSQL;
snapshot é fixado no aceite, citações são verificadas no commit e na entrega.
Revogação invalida respostas posteriores, inclusive na lista. A baseline
`lexical-hash-256-v1` exercita dense/sparse/RRF, mas **não é embedding neural**.
EXTRACTIVE entrega o trecho literal, sem gerar conclusões ou cálculos.
Logs por etapa incluem request ID/fence/duração/resultado, não pergunta,
trechos ou credenciais. Spans usam a API OpenTelemetry; não há collector/exporter
externo entregue nem comprovado por isso.

O desenho passou duas rodadas de contramodelos do mesmo revisor. São checks de arquitetura, não auditorias independentes nem benchmark real de 100.000 requests. A liberação de produção permanece bloqueada.

## Verificações executadas em 30/09/2026

Atualização final de 01/10/2026 UTC: imagem atual passou RAG 31/31 x2 e
lifecycle 34/34 x2. Provas em
`../eval/runs/setup-final-20261001/receipt.json`. São fatias sintéticas reais
do mesmo autor, não auditoria independente nem Gate B. Falha de prontidão
Qdrant preservada e protocolo LAB corrigido para medir a API pronta, sem apagar
volumes; não certifica recuperação de produção em 30 segundos. Gemini remoto
segue reprovado HTTP 400; token MCP ainda aguarda autorização de criação.

### Recuperação extrativa — baseline histórica

`tests/rag_fixture.py` passou duas vezes, **31/31 checks por rodada**, seeds
1730738146 e 225250747, fontes e imagens congeladas. Receipt:
`D:\RAG-Local\eval\runs\rag-real-20260930T225724Z-7adfe8\receipt.json`.
Prova HTTP/PostgreSQL/Qdrant/grafo ADK reais com dados sintéticos: citação literal,
snapshot, isolamento, revogação no commit e na leitura/lista, citação adulterada,
restart/outage do Qdrant, recuperação do candidato e logs de etapas redigidos.
As falhas anteriores permanecem nos receipts; a sequência foi reiniciada.

Perfil é opt-in. Para reproduzir a fixture temporária após reconstruir as imagens:

```powershell
& D:\RAG-Local\adk\.venv\Scripts\python.exe D:\RAG-Local\app\tests\rag_fixture.py
```

Ela recusa projeto já ativo e encerra somente o projeto temporário que iniciou,
preservando volumes. Corpus sintético via API não substitui parser PDF/object
store, benchmark neural, Gemini/DeepAgents/MCPs, 100.000 requests ou produção.

### Histórico da primeira fatia

A regressão foi repetida nas fontes atuais após a recuperação extrativa:
34/34 em duas rodadas, seeds 2898845025 e 2881537686. Receipt:
`D:\RAG-Local\eval\runs\segments-real-20260930T230210Z-7e54eb\receipt.json`.
Consolidação atual por caso em
`D:\RAG-Local\eval\runs\segments-design-20260930T230603Z-26d978\receipt.json`:
35 casos com prova real no recorte, 6 apenas estruturais, 53 pendentes.
Os resultados anteriores abaixo são históricos, não substituem essas provas.

Após corrigir falhas de configuração dos containers e contexto residual ao trocar tenant:

- Duas rodadas HTTP/PostgreSQL/ADK, 20/20 checks cada, seeds 200742262 e 570100529.
- Duas rodadas visuais A→B e B→A, quatro cenários cada; nenhuma informação residual da identidade anterior e consulta cruzada negada.
- Duas rodadas de restart real do container PostgreSQL, cinco checks cada: pedido aceito sobrevive, dedupe preservado, terminal/audit/outbox únicos.
- Reload desconectado, layout mobile 393 px sem overflow, builds, compilação Python e sintaxe JavaScript.

Receipt final: `D:\RAG-Local\eval\runs\http-slice-20260930T212756Z-33a330\receipt.json`. Os containers deste teste foram encerrados, com volume preservado. A falha visual anterior está registrada, não apagada. Estas rodadas verificam uma fatia pequena com dados sintéticos; **não comprovam RAG factual, 100.000 HTTP requests, disponibilidade em produção ou auditoria independente**.

## Fluxo implementado

1. Frontend obtém identidade **sintética de laboratório**; o JWT fica apenas em memória. Escolher Demo A/B não é login corporativo.
2. API valida identidade, pergunta e chave `epoch.uuid`. PostgreSQL confirma pedido, job, audit e outbox numa transação antes do HTTP 202.
3. Worker reivindica um job com lease/fence usando horário do banco; executa o workflow ADK privado daquela tentativa.
4. No default ADK produz proposta via `EventActions.state_delta`; no perfil extrativo, funções do grafo comunicam-se via `node_input` e saída privada de eventos. O adapter converte a proposta para o contrato do domínio; o ledger valida citações canônicas, lease, fence e deadline antes do terminal.
5. Front consulta o ledger pelo ID; após confirmação ambígua resolve pela mesma chave. Cancelamento e expiração também ficam registrados.
6. Control-worker separado recupera leases e jobs faltantes, sem importar ADK nem depender do pool de inferência.

`SUCCEEDED` + `ABSTAIN` significa processamento concluído sem resposta factual, não uma resposta validada a partir de documentos. O outbox é gravado atomicamente; seu dispatcher externo ainda não está implementado. Não se promete execução exatamente uma vez de ferramentas externas.

## Organização

- `src/rag_app/domain.py`: identidades, estados, propostas e contrato do workflow.
- `application.py`: casos de uso e porta do ledger.
- `bootstrap.py`: composição/injeção dos adapters por processo.
- `ledger.py` / `schema.sql`: PostgreSQL, idempotência, justiça entre tenants, audit e outbox.
- `adk_workflow.py`: adapter ADK determinístico e offline.
- `corpus.py`: catálogo/snapshot, ingestão limitada e adapter HTTP fixo para Qdrant.
- `api.py`: FastAPI, limite de corpo e autenticação sintética.
- `process.py`: migration, request-worker e control-worker.
- `../frontend`: HTML/CSS/JS e Nginx não root com filesystem somente leitura.
- `tests/http_fixture.py`: integração temporária real; encerra somente os containers que iniciou e preserva o volume.

Usa SQL/psycopg e migration inicial idempotente, sem fingir que há Alembic/SQLAlchemy ou upgrades de esquema já testados. A introdução de migrações versionadas exige novos testes.

## Reprodução local

Requer Docker Desktop com Linux containers. Os dados PostgreSQL usam volume Linux; no computador inspecionado o disco Docker está em `D:\Docker\WSL`. O código e segredos de laboratório estão em `D:\RAG-Local`. Não usar bind mount NTFS para dados PostgreSQL/Qdrant.

As imagens-base estão fixadas por digest e o lock Python contém hashes transitivos. Os segredos são gerados por `tools/init_lab.py`, excluídos do contexto de build e montados como arquivos; nunca publicar `.local`.

```powershell
Set-Location D:\RAG-Local\app
python tools\init_lab.py
docker compose build
python tests\http_fixture.py
```

O teste recusa assumir um projeto já rodando. Não usa `down -v`, não apaga pedidos e não interfere em outros projetos Docker. Logs, contratos e receipts ficam em `D:\RAG-Local\eval\runs`.

**Servidor permanente não entregue:** a tentativa de iniciar o supervisor externo previsto pela skill `persistent-local-server` foi bloqueada pela política da ferramenta. `tools/supervise.ps1` existe, mas sua execução/sobrevivência não foi comprovada. Os testes usam apenas containers temporários e os encerram ao final. Não confundir `restart: unless-stopped` com prova de supervisão Windows.

## Revisão por segmentos (30/09/2026)

O catálogo [segment-cases.tsv](../docs/architecture/segment-cases.tsv) tem casos
com resultado esperado, comportamento proibido e menor prova para Python/ADK,
RAG, MCP/APIs, Vertex/Gemini, avaliação, DeepAgents, frontend e operação.
[segment-acceptance.md](../docs/architecture/segment-acceptance.md) distingue
duas rodadas adversariais de auditoria cega independente e de aprovação global.

O teste `tests/segment_fixture.py --rounds 2` usa HTTP, ADK e PostgreSQL reais no
laboratório: soma a suíte anterior a falhas adicionais de lease, teto de tentativas,
receipt envelhecido, isolamento por ator, job ausente e restart do PostgreSQL.
Recusa projeto já ativo, congela fontes/imagens, usa somente IDs sintéticos e
preserva volumes. Requer imagens reconstruídas antes de executar. Não testa
modelo, corpus, MCPs, DeepAgents ou carga real de 100.000 pedidos.

Correção: uma chave já conhecida e retida é conferida por tenant/ator/payload
antes da restrição de idade para nova admissão; chave antiga desconhecida ainda
é rejeitada. A aplicação continua sem permissão DELETE; a injeção de job ausente
usa apenas a role administrativa existente em container temporário de migration.
Conexões SQL têm timeout server-side de transação ociosa: um cliente congelado não
pode conservar o lock de admission indefinidamente. A suíte separa esse fault real
da indisponibilidade controlada do worker e reinicia a sequência após mudanças.

`docs/architecture/review_segments.py <receipt-lifecycle> <receipt-rag>` liga cada caso à evidência
das duas rodadas e recusa hashes antigos. Cases não implementados permanecem
PENDING; verificação estrutural não vira PASS comportamental. Gate B continua
fechado.

## Critérios antes de produção

- [ ] Corpus autorizado, ingestão versionada, Qdrant e recuperação híbrida/rerank com filtros e ACL revogada até o commit.
- [ ] Citações verificáveis e gates de evidência/abstenção; nenhuma alegação de zero alucinação.
- [ ] ADK com modelo/configuração aprovados; DeepAgents separado por TaskPort e limites de recursão/custo/checkpoints.
- [ ] GitHub e Atlassian MCP **remotos/online**, com allowlist de leitura, identidade delegada mínima, timeout, circuit breaker e isolamento; [contrato planejado](../docs/architecture/remote-mcp.md), ainda sem conexão real. APIs/efeitos externos continuam sujeitos aos gates.
- [ ] Integração opt-in Vertex AI/Gemini, credenciais e orçamento aprovados; custo e egress mensurados.
- [ ] DeepEval/judge calibrado + testes determinísticos, datasets versionados e duas rodadas reais sem regressão.
- [ ] OIDC corporativo, TLS, rate limit, roles SQL separadas e testes negativos de autorização.
- [ ] Tracing OpenTelemetry e métricas de idade dos pedidos, controle, erros, tokens/custo; logs sem texto/segredos.
- [ ] Outbox dispatcher/checkpoints e supervisão comprovada.
- [ ] Admission de bytes/headroom além de contagem; pooling e gargalo do lock global medidos. Sem GC nesta fatia.
- [ ] Carga real com 100.000 requests definidos por cenário (total/concorrência/RPS), resultados reconciliados e SLA medido.
- [ ] Chaos, restart, restore/PITR e HA em domínios de falha distintos; disco D: único não oferece essa garantia.
- [ ] Duas rodadas consecutivas de Gate B, infraestrutura definida e autorização explícita de deploy.

Contratos e evidências ficam em `../docs/architecture/production-contracts.md` e `production-review.md`. A documentação distingue checks executados, limitações e bloqueios, em vez de converter lacunas em aprovação.
## Probe Gemini gratuito separado do RAG padrão

`compose.gemini-lab.yaml` adiciona apenas um serviço one-shot opt-in, com chave
privada fora do build e contador persistente. Não coloca egress/chave no worker.
Executar manualmente:

```powershell
docker compose -f compose.yaml -f compose.gemini-lab.yaml --profile gemini-lab run --rm --no-deps gemini-probe
```

Usa modelo fixo `gemini-3.5-flash-lite`, thinking minimal, prompt sintético,
no máximo duas tentativas por dia UTC, uma
chamada LLM por execução e nenhum retry/fallback pago. Pressupõe projeto Free sem
billing, confirmado no provedor; não é hard cap financeiro nem leitura automática
de billing. Não transmitir dados corporativos. Controle local validado duas vezes;
primeiras duas inferências reais com o modelo 2.5 retornaram HTTP 404 e NÃO
passaram. A restrição oficial a novos projetos motivou a troca para 3.5,
ainda reprovada remotamente: duas tentativas na janela UTC seguinte retornaram
HTTP 400 `INVALID_ARGUMENT`, sem causa específica confirmada. Não houve reset
do contador nem ativação de billing. Contrato SDK/ADK offline: 18/18 em duas rodadas,
também no container sem rede. São testes do mesmo autor, não auditoria cega
independente. Não zerar o
volume de uso para contornar o limite. Identidade Atlassian verificada; token
read-only de curta duração criado com autorização e salvo fora do build/Git.

## Probe MCP Atlassian isolado

`Dockerfile.mcp-lab` estende a imagem base local sem reconstruí-la. Dependências
MCP extras fixadas com hashes; `pip check` no build. `compose.atlassian-lab.yaml`
adiciona um serviço one-shot, não root, read-only, sem portas e sem acesso aos
serviços RAG. Não é firewall de domínio: o destino e RPC são fixados no transporte
Python; a rede bridge permite egress, separado do worker padrão.

```powershell
docker build --pull=false -f Dockerfile.mcp-lab -t rag-local-mcp-lab:0.1.0 .
docker compose -p rag-local-v2 -f compose.yaml -f compose.atlassian-lab.yaml --profile atlassian-lab run --rm --no-deps atlassian-probe
```

Pré-condições: autorização de token/armazenamento e política MCP da organização
permitindo API token. A política de API token foi ligada com autorização específica
e conferida na organização de laboratório. O token substituto privado foi criado
com os mesmos escopos e prazo; o token anterior foi preservado. Initialize/catalog
passaram, mas a leitura de issue continuou retornando 401 com ambos os tokens.
A causa permanece não confirmada. Zero passes remotos; autorização de OAuth seria
uma nova concessão de segurança, não uma correção silenciosa. Gateway/SDK/ADK com HTTP controlado passou 54 checks
em duas rodadas no host e container sem rede; não substitui leitura online.

Contrato: `../docs/architecture/atlassian-lab-acceptance.md`. Só KAN-1/KAN-2,
cloudId fixo e getJiraIssue. Argumentos de sites/tools/JQL não expostos ao agente.
Sem retry automático, redirect, schema com referências remotas, cache/pool global,
logs de conteúdo ou promoção ao índice. Conteúdo recebido é evidência não confiável.
Probe não está conectado ao workflow RAG de respostas, ledger ou jobs de sync.

## Fila local de implementação e perfis opt-in

A fila está em `../.local/card-execution/progress.json`, com histórico SQLite
local e bugs verificados acrescentados ao fim. Bloqueado não significa concluído;
cada aprovação exige o tipo de evidência do card e duas rodadas congeladas.
Falhas e recibos anteriores são preservados. As fixtures são adversariais do mesmo
autor, não testes cegos independentes.

Perfis implementados em revisão (a presença do YAML não comprova aprovação):

- `compose.semantic.yaml`, após `compose.retrieval.yaml`: embeddings neurais
  multilíngues de 384 dimensões por ONNX/FastEmbed em CPU, pesos públicos MiniLM
  fixados por revisão e SHA-256. Serviço sem portas públicas, segredos ou egress.
  Pesos ficam no disco D: e na imagem; nenhum download ocorre em execução.
  Busca híbrida usa vetor neural + índice lexical esparso e fusão RRF no Qdrant;
  as citações continuam verificadas contra SQL, ACL, versão e revogação.
  A família neural tem coleção própria e não mistura vetores com as coleções
  lexicais de 256 dimensões, inclusive snapshots anteriores.
  Limiar é escolhido apenas no conjunto de calibração, com zero falsos aceites
  naquele conjunto. O conjunto de avaliação em português não está na imagem
  do serviço. Isso não promete ausência de alucinação ou qualidade universal:
  o modo é extrativo e regras de resposta cobrem só o FAQ sintético do laboratório.
  Sem evidência suficiente, ambiguidade ou campo não suportado, deve abster-se.
  Serviço indisponível retorna erro recuperável, sem fallback silencioso.
  Consulta de reparo aceita aliases controlados de computador; o texto original
  do pedido permanece no ledger. A avaliação v1 revelou um falso negativo e virou
  regressão conhecida; v2 é um novo conjunto autoral. Nenhum é auditoria
  independente, e não se escolhe o limiar pelas perguntas desses conjuntos.
  Fixture real: `tests/semantic_fixture.py`; contrato e conjuntos versionados
  em `../docs/architecture/semantic-contract.json` e `../eval/datasets/`.
- `compose.documents.yaml`, após `compose.retrieval.yaml`: TXT/Markdown UTF-8 e
  PDF textual, arquivo original em volume privado content-addressed, download
  autenticado, revogação de todas as versões da fonte. Arquivo de até 8 KiB,
  até 3 páginas, parser isolado com timeout de 4 s e volume de 20 MiB. PDF
  escaneado exige OCR e é rejeitado; OCR não está implementado.
  A rota `POST /v1/lab/documents/{id}/revoke` conserva revogação só daquela versão;
  `POST /v1/lab/documents/{id}/revoke-source` é a revogação forte de toda a fonte,
  incluindo histórico e tombstone que impede reativação implícita.
- `compose.observability.yaml`: collector OpenTelemetry oficial fixado por digest,
  métricas agregadas SQL, spans filtrados e alertas estruturados por heartbeat.
  Sem portas públicas ou envio externo. O arquivo rotativo de traces é recurso
  de laboratório, não armazenamento HA de auditoria. Não se exportam prompts,
  eventos de SDK, conteúdo, segredos ou atributos arbitrários.
- `compose.delivery.yaml`: outbox para inbox SQL durável, polling autenticado e
  deduplicação de replay. Não é um broker externo. Reserva de bytes é transacional;
  limite global de 32 MiB pendentes e 8 MiB por tenant, além de contagem e teto
  de admissão de 256 MiB para o banco. Isso é backpressure de laboratório, não
  proteção comprovada de disco/WAL/HA de produção.

Retenção opt-in limita-se ao conteúdo de pedidos sintéticos geridos pelo perfil
de entrega: após 30 dias do terminal, remove pergunta/resultado, sinaliza
`content_expired` e preserva recibo, chave, hash, auditoria e estado. Não toca
pedidos antigos não geridos nem não terminais. Originais e snapshots do corpus
não são coletados fisicamente automaticamente; acesso pode ser revogado.

Os probes Gemini e MCP continuam separados desses perfis; nenhuma chamada paga
ou fallback foi habilitado. Produção permanece recusada pelo startup guard.
