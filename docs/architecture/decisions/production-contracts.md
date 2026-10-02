# Contratos de produção do RAG

Revisão 2 do monólito modular Python com ADK e frontend próprio em containers. Fecha as lacunas do audit-100k. Originais preservados em application-v1.md e application-eraser-v1.txt. Aprovar o desenho libera começar implementação local; não libera deploy nem permite chamar o laboratório de produção.

Extensão da revisão 3: GitHub e Atlassian MCP oficiais **remotos/online**, atrás do mesmo ToolPort e gateway. [remote-mcp.md](remote-mcp.md) especifica egress, credenciais, leitura, isolamento, limites e gates adicionais. Integrações ainda não conectadas; gates antigos não se estendem automaticamente a elas. Backend/container e credenciais existentes não foram alterados nesta revisão.

## Carga e aceite

Hipótese inicial: burst de **100.000 pedidos únicos no total**, distribuídos por 100 tenants, não 100.000 LLMs simultâneos ou RPS. Contar tentativas, aceites confirmados, commits de confirmação desconhecida e rejeições. Aceite requer commit de request/job/reserva/audit/outbox. Conexão perdida após commit é resolvida por retry da mesma chave, nunca assumir rollback. GET usa primário/rota consistente.

Defaults propostos, não benchmark: backlog global 100.000; 1.024 pendentes por tenant; corpo até 16 KiB; deadline 900 s; lease 30 s com heartbeat antes de 10 s; máximo 3 tentativas; IA com concorrência inicial 2 no laboratório. Reservas de número/bytes são atômicas no SQL, sem count-then-insert. Reservar margem para resultado, índices/WAL; monitorar headroom e recusar aceite antes de encher disco. Quotas de ingestão/EVAL/logs não consomem a reserva do controle. Tamanhos reais, vazão, GPU e SLO precisam ser medidos.

Aceito não significa resposta factual: CANCELLED, EXPIRED, FAILED_FINAL e SUCCEEDED/ABSTAIN são terminais rastreáveis. Durante outage SQL, prazo e consulta podem ficar indisponíveis; preservar IDs e recuperar após restauração. Não garantir progresso sem banco e controle disponíveis, nem recuperação após perda de todas as cópias.

## Durabilidade e recuperação

Lab: fsync e synchronous_commit=on, volume Linux, sem HA de host/disco. Produção RPO-zero para falha do primário: réplica síncrona durável em outro domínio de falha, synchronous_standby_names configurado, commits sem override local/off, promoção somente de réplica elegível contendo commits reconhecidos e fencing do antigo primário. Sem durabilidade exigida, não confirmar aceite. Não criar eleição distribuída caseira. Um flag RPO_ZERO não basta: validar topologia/TLS/schema e exigir evidência de failover/restore. Backup/PITR fora do domínio primário com RTO/RPO acordados. [PostgreSQL](https://www.postgresql.org/docs/current/warm-standby.html).

Containers api, request-worker, control-worker, ingest-worker e eval-worker usam a mesma imagem Python com entrypoints distintos. **Control-worker não faz LLM nem parsing/judge**: tem CPU/pool SQL reservados, scan indexado em batches, heartbeat e supervisão. Recria job ausente, retoma lease com novo fence, agenda retry, expira deadline e detecta outbox parada. Banco é relógio. Finalização exige RUNNING, fence atual, lease válida, prazo e reautorização sob locks ordenados. Heartbeat atrasado não revive lease. Cancel/expire versus finalize resolve por CAS; terminal nunca é sobrescrito.

Claim justo: cursor de round-robin durável entre tenants elegíveis, quotas inflight/pendentes e aging pela data original, sem reset em retry. Locks/SKIP LOCKED só em transação curta; sem transação durante inferência. Testar progresso de tenant pequeno sob flood e custo variável. Outbox é at-least-once com deduplicação; RabbitMQ opcional por perfil. Falha de notificação não apaga pedidos; polling autenticado independe de SSE. Sessões ADK/DeepAgents não são ledger.

## Retenção e frontend

Chave `<epoch_seconds>.<uuid>`, gerada uma vez por intento. Servidor rejeita formato inválido, mais de 60 s no futuro ou idade acima de 7 dias, mesmo sem vínculo SQL. UNIQUE(tenant, actor, key) com hash canônico; payload diferente retorna 409. Mudar a chave é novo intento. Vínculos/tombstones duram ao menos 8 dias após terminal; recibo mínimo 30 dias; nunca coletar não terminal. Conteúdo privado pode ter retenção menor. Expiração é explícita quando existe prova autorizada; UUID sem prova pode retornar 404 para não revelar existência. GC fica desativado na primeira fatia até passar sua suite.

Frontend próprio mostra pedidos/status/evidências, cancela e retoma por request_id. Renderiza conteúdo como texto, não HTML; distingue falha, expiração e abstenção. Token em memória na demo, nunca localStorage/URL/log. Produção: OIDC/BFF ou fluxo autorizado, com CSRF se cookies, tenant/ator e ACL atual em toda leitura. ADK Web somente dev/loopback, nunca frontend de produto nem exposição de candidato bruto.

## Containers e operação

Imagens pinadas por digest e dependências completas em lock com hashes; builds sem segredo/bind de fonte; usuário não root, cap_drop ALL, no-new-privileges, rootfs read-only quando possível, tmpfs limitado e limites CPU/RAM/PIDs. Healthchecks por papel e shutdown drenando claims/leases. Migrations por job único expand/contract; rollback de imagem sem rollback destrutivo de dados. Não montar Docker socket. Bancos/Qdrant sem portas host; frontend local loopback; TLS/ingress/rate limit na implantação pública.

Credenciais por secret file/manager, papéis distintos de leitura/ingestão/migrations; serviços sem superuser. Produção recusa fixture auth/model. MCP/REST usam allowlist, timeout e validação; UNKNOWN de efeito externo não recebe retry cego. Egress autorizado separado da rede de dados. Vertex/Gemini opt-in sem fallback automático e com orçamento/confidencialidade. DeepAgents, MCP/APIs, Vertex/Gemini e DeepEval continuam no escopo, não implementados só por existir um port.

PG/Qdrant usam filesystem Linux/POSIX; não bindar índice Qdrant em NTFS. Docker aponta dataFolder para D:\Docker\HyperV; confirmar armazenamento físico antes da execução. A documentação alerta para perda de dados em mounts Windows/WSL. Produção usa volumes Linux duráveis e réplicas em domínios distintos, não o mesmo D: para tudo. [Qdrant](https://qdrant.tech/documentation/installation/), [Docker production](https://docs.docker.com/compose/how-tos/production/).

Logs JSON redigidos, audit transacional, métricas sem request_id como label e traces amostrados. Alertas por idade/backlog, lease/retry/outbox, disco/WAL/replication lag e erros/custo. Collector morto não bloqueia commit. Operador olha logs/métricas, sem aprovação humana por resposta.

## Gates e ordem de construção

Gate A: duas revisões adversariais distintas consecutivas sem achados documentais abertos, contratos congelados por hash e controles negativos. Achado reinicia contagem após correção. Mesmo revisor, não auditoria independente; PASS significa desenho coerente nos cenários, não aplicação 100% pronta. Esse gate libera implementação local.

Gate B: duas execuções reais consecutivas com imagens exatas, HTTP/PostgreSQL/Qdrant, concorrência/isolamento/falhas, carga definida, qualidade gold/holdout PT, adaptadores ADK/DeepAgents/MCP/Vertex opt-in, DeepEval/judge calibrado, HA/restore, observabilidade/custo e SBOM/vulnerabilidades. Infra/credencial/hardware ausentes ficam BLOCKED, não PASS. Produção exige autorização específica de deploy; nenhuma chamada cloud paga ou exposição pública nesta fase.

Fatias: (1) front/status/cancel + API/ledger/worker/control em containers, ADK determinístico de abstenção; (2) corpus/Qdrant/retrieval/verificação; (3) modelos/ADK/DeepAgents/MCP; (4) EVAL/cloud opt-in; (5) carga/HA e gate de release. UI declara capacidades reais; abstenção de teste não prova um RAG útil completo.
