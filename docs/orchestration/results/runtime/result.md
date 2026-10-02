Frente runtime, branch `codex/rag-runtime`, base e HEAD `3dba8c0f557421cd3f6d5bc26017278a95a53dbb`. Os 24 itens do snapshot foram examinados contra os critérios originais. A aprovação do escopo offline depende do receipt de testes com duas rodadas consecutivas, fontes SHA-256 idênticas e `complete: true`. O receipt de entrega `.local/orchestration/worker-receipt.json` registra o HEAD preservado, os hashes atuais e o bloqueio de commit.

O staging foi recusado por `Permission denied` ao criar `D:/RAG-Local/.git/worktrees/runtime/index.lock`. A leitura da ACL confirmou negação explícita de escrita pelo sandbox; não há lock antigo ou atributo read-only no índice. Nenhuma ACL foi alterada e nenhum commit foi criado. O patch completo `.local/orchestration/runtime-review.patch` permite a revisão e coleta pela Central em seu workspace autorizado. O estado de entrega é `BLOCKED_LOCAL_COMMIT_PERMISSION`, com escopo offline concluído quando o receipt final estiver aprovado.

Foram reproduzidos e corrigidos cinco bugs novos, para a Central cadastrar no fim da fila:

1. O consumidor AMQP mantinha a conexão aberta quando a criação do canal falhava. Agora fecha a conexão e propaga a falha original.
2. Repetir a configuração de logging preservava um handler bruto adicionado após o startup. Agora mantém somente o handler estruturado em ambos os namespaces ADK.
3. As fixtures de entrega e observabilidade sobrescreviam `--project-directory` e mantinham o projeto QA anterior. Agora localizam o argumento `-p` e registram o projeto correto.
4. A fixture suplementar de restart aceitava o runtime padrão do laboratório. Agora exige projeto QA, endpoint 8940 e contrato correspondente dentro de `.local`, antes de qualquer ação HTTP ou Docker.
5. Essa fixture usava `tenant` para criar uma sessão, rejeitado pelo modelo atual `LabIdentity`. Agora usa o perfil `ana`; a regressão valida o payload com o modelo real da API.

O healthcheck do PostgreSQL de restore passa a exigir TCP, preservando os 120s de parada e os 300s de período inicial. Isso prepara o controle de prontidão de BUG-121; o ciclo real de restore continua pendente.

A verificação offline executa oito fixtures existentes com suas assertions preservadas e 30 casos runtime. Cobre admissão e bytes UTF-8, replay e conflito de idempotência, limite de tentativas, fencing/lease/deadline, outbox/inbox, retenção, bulkheads/circuitos, broker, cache assinado/TTL, métricas, exportador e alertas. Os testes do scheduler usam relógio controlado e dependências injetadas; SQL e containers reais têm gates próprios. Os testes ADK/OTel usam o SDK instalado e retrieval/falhas sintéticos, sem providers remotos.

O runner bloqueia processos externos, conexões, segredos do laboratório e escrita fora de `.local`. Permite somente o par de sockets interno usado pelo `asyncio` no Windows; recusa a sondagem IPv6 de capacidade do urllib3 como indisponível. Verifica que os imports `rag_app` vêm deste worktree e que os receipts das fixtures correspondem às fontes congeladas. As execuções são da mesma autoria, sem alegação de auditoria independente.

| Itens | Cobertura disponível nesta lane | Prova ainda necessária |
| --- | --- | --- |
| RAG-09 | SDK, traces/status/redaction, métricas/alertas e exportador controlados | Collector, HTTP/SQL e outages reais |
| RAG-10 | Outbox/inbox, quotas, replay e retenção controlados | Duas rodadas HTTP/SQL/processo reais |
| RAG-12 | Admissão, backoff, reconciliação e scheduler controlados | Workload definido, 100000 ofertas reais, aceitos e SLO medidos |
| RAG-13 | Migração aplicada, guard de produção e lifecycle controlados | Persistência, restore, failover em outro domínio e release seguro |
| BUG-018, BUG-019, BUG-021, BUG-025, BUG-027, BUG-028, BUG-029, BUG-030, BUG-031, BUG-088 | Oráculos offline originais; BUG-025 preservado | Decisão de atualização dos cards pela Central |
| BUG-032 | Ordem de parada do retainer e seleção de retenção | Corrida real de retenção |
| BUG-110, BUG-111, BUG-112 | Skip/recheck, orphan locking e scheduler | Locks PostgreSQL e falhas com pedidos reais |
| BUG-113, BUG-114, BUG-115 | Normalização Windows, migração aplicada e Decimal reais no código do gate | Gate integrado de restart/SQL |
| BUG-116, BUG-121 | RTO original mantido, controles de prazo e config TCP | SIGKILL/prontidão/restore reais |
| BUG-124 | PTTL zero/-2 aceitos; -1, excessos e tipos inválidos rejeitados | Expiração e corrida SCAN no Redis real |

As falhas de reprodução e as limitações do guard anteriores às correções foram preservadas em `.local/orchestration/reproductions/`. Rodadas anteriores a mudanças não aprovam as fontes atuais. Os receipts finais, os vínculos SHA-256, os detalhes por card e os checks de escopo ficam em `.local/orchestration/worker-receipt.json` e `.local/orchestration/final-offline-commit-blocked/`. A sequência anterior em `final-offline/` foi invalidada pela atualização deste relatório e do gerador do receipt.

O plano serial de gates reais e as dependências estão em [gates.json](gates.json). A Central precisa corrigir o oráculo de três etapas em `app/tests/rag/rag_fixture.py`, adaptar a fixture HTTP compartilhada para projeto/perfis atuais e conectar os readers de evidência a `.local`. Esses arquivos ficaram preservados por estarem fora da allowlist. RAG-12 e RAG-13 continuam bloqueados para a aprovação completa; o trabalho offline não prova HA ou 100000 workflows atendidos. Esta lane não modifica journal, containers, credenciais ou serviços remotos.
