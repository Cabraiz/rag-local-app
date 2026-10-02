# Resultado dos testes de durabilidade do RAG

Este relatório registra a execução histórica das 06:15 e sua atualização local das 06:47 UTC. Os números e hashes abaixo pertencem àquela execução. A revalidação mais recente passou duas rodadas e aplicou uma nova imagem às 14:52 UTC; o estado atual, os comprovantes e os bloqueios estão em [Correções e bloqueios atuais do RAG](../../cards/backlog-results-20261002.md).

Em 2 de outubro de 2026, duas rodadas completas e consecutivas passaram com fontes e imagens congeladas. Os 1.028 pedidos HTTP aceitos do workload definido terminaram em SUCCEEDED e tiveram sua notificação entregue, sem IDs perdidos ou conclusões duplicadas. A imagem validada foi aplicada ao laboratório local, preservando dados e configuração do workflow.

Isso comprova os cenários executados, não um SLA de 99% em produção. O ambiente continua identificado como laboratório, com `production_ready=false`. Não houve deploy público nem chamada ao Gemini durante este gate ou sua atualização local.

## Contrato e método

- HTTP 202 exige persistência do pedido e job. HTTP 429 ou 503 é rejeição explícita, não pedido aceito.
- Uma confirmação desconhecida exige consulta ou reenvio com a mesma chave de idempotência.
- O gate exige zero aceitos ausentes, zero conclusões duplicadas e pelo menos 99% SUCCEEDED no workload sintético.
- Cada rodada usa oito clientes HTTP, quatro fases de 128 pedidos e dois casos de confirmação, mantendo a política de admissão de 3 pedidos por segundo e burst de 32. Reenvios reutilizam a chave original.
- A drenagem deve terminar em até 120 segundos sem broker/cache e 180 segundos nos demais cenários, após aceitação. O PostgreSQL deve voltar a responder HTTP em menos de 60 segundos desde SIGKILL.
- Cada rodada também executa 44 verificações RAG e três provas de recuperação sob bloqueios, incluindo backlog de 129 pedidos e job ausente com a FK bloqueada.
- As sementes finais foram 338678 e 839682. Qualquer alteração nas fontes congeladas reinicia a exigência de dois passes.

São testes do mesmo autor, com dados fictícios e falhas controladas, não uma auditoria cega independente. SUCCEEDED com ABSTAIN nas provas de durabilidade comprova término seguro da execução, não qualidade de uma resposta de negócio.

[Contrato de aceitação](reliability-gate-20261002.md) e [comprovante das duas rodadas](../../../eval/runs/rag-local-resilience-qa-20261002045001-reliability-061522/receipt.json).

## Resultados

Em cada uma das oito fases abaixo, 128 pedidos foram aceitos, concluídos e entregues. Perdas e conclusões duplicadas foram zero. Os quatro pedidos adicionais dos casos de confirmação também passaram.

| Cenário | P99 de conclusão na rodada 338678 | P99 de conclusão na rodada 839682 |
| --- | ---: | ---: |
| Operação normal | 53,170 s | 54,032 s |
| RabbitMQ e Redis desligados | 34,556 s | 38,783 s |
| SIGKILL dos dois workers | 121,821 s | 107,791 s |
| SIGKILL do PostgreSQL | 128,540 s | 120,963 s |

Os tempos são de aceitação até conclusão, não latência total incluindo espera e reenvios de admissão. Nas oito fases, houve 7.862 respostas 429 e uma 503, todas explícitas; reenvios com a mesma chave obtiveram os 1.024 aceites. Isso demonstra backpressure no workload, não capacidade de aceitar milhares de pedidos simultâneos.

O PostgreSQL voltou a responder HTTP em 34,500 e 27,520 segundos, contados desde SIGKILL. `fsync`, `full_page_writes` e `synchronous_commit` permaneceram ligados.

Após cada rodada, a reconciliação encontrou zero pedidos ativos, jobs ausentes, auditorias de aceitação/terminal ausentes, notificações pendentes e divergências dos contadores por tenant e partição. A contagem final de 4.100 registros inclui o histórico de tentativas anteriores e probes de recuperação; não é a contagem do workload HTTP de sucesso.

## Correções verificadas

Os sete novos cards locais BUG-110 a BUG-116 foram encerrados com reprodução preservada e duas regressões aprovadas:

| Card | Correção |
| --- | --- |
| BUG-110 | Recuperação pula partições e pedidos bloqueados antes do limite, sem impedir a recuperação de outro tenant. |
| BUG-111 | Hints não produtivos não entram em hot requeue; fallback SQL mais frequente e reconexão do broker com espera progressiva limitada e jitter. |
| BUG-112 | Reparo de job ausente pula o pedido com FK bloqueada, sem abortar toda a recuperação. |
| BUG-113 | Normalização de caminhos Windows no comprovante do injetor. |
| BUG-114 | Migração registra checksum atomicamente; reinício com schema já aplicado não repete DDL sob tráfego. |
| BUG-115 | Serialização explícita dos números SQL no relatório de reconciliação. |
| BUG-116 | Injetor retoma e verifica os IDs exatos dos containers mortos; restauração usa instância e volume QA separados. |

Quatro correções são da aplicação e três são do mecanismo de teste. Falhas anteriores permanecem registradas; pedidos antigos não foram apagados para fazer o gate passar. Um clone temporário de restauração no cluster QA foi removido, mantendo seu backup validado. Nenhum banco principal ou volume do usuário foi removido.

A revisão de aprovações invalidou o comprovante antigo do BUG-028 porque `process.py` mudou. Sua regressão de status OTel, falhas ADK, fencing e redaction passou novamente em duas rodadas, sem alteração adicional de código. [Comprovante atualizado de tracing](../../../eval/runs/tracing-status-regression-20261002T064906Z-15b1aa/receipt.json).

## Restauração e atualização local

Cada rodada produziu um `pg_dump` e executou `pg_restore --exit-on-error` em outra instância PostgreSQL QA. Os hashes das coleções canônicas de metadados de requests, jobs, auditoria, outbox e inbox corresponderam ao original. Isso não é um ensaio funcional completo de recuperação do corpus ou failover; ambos os volumes continuam no mesmo computador/disco.

Backups preservados:

- [Backup da rodada 338678](../../../eval/runs/rag-local-resilience-qa-20261002045001-reliability-061522/backup-338678.dump), SHA-256 `ccf4f850775e53f56ff146ae77988aa3747a25d035fc499261d6ef900b4aee2f`.
- [Backup da rodada 839682](../../../eval/runs/rag-local-resilience-qa-20261002045001-reliability-061522/backup-839682.dump), SHA-256 `a6ba14ae91585e422266a511b326be6a2acdad994c3919b0b84d51d7b470847b8`.

A API, os dois workers, control, delivery e relay usam a imagem testada `sha256:330c07e0001294db28d999b7e97b5835ac0a3719ac63779f63809e919f3c38df`, com supervisor Docker e política `unless-stopped`.

O rollout local verificou nove condições: corpus preservado, todos os 976 recibos anteriores mantidos, imagem exata, modo laboratório, workflow preservado, smoke sintético SUCCEEDED/ABSTAIN, contador de uso do modelo inalterado, contadores de fila consistentes e Grafana saudável. Foram preservados os hashes de 693 registros de documentos, 693 chunks, 185 releases e dois heads; são registros versionados, não necessariamente 693 documentos atuais visíveis.

[Comprovante da atualização local](../../../eval/runs/reliability-local-rollout-20261002-064733/receipt.json). Na conferência final, frontend, readiness, Grafana e Swagger clínico responderam HTTP 200; Prometheus retornou `up=1` para API e RabbitMQ. O Swagger clínico foi apenas conferido, não retestado integralmente neste gate.

- [Aplicação RAG](http://127.0.0.1:8840/)
- [Grafana](http://127.0.0.1:8850/d/rag-overview/rag-local)

## Limites e próximos gates de produção

O resultado observado foi 100% de retenção e término no workload definido. Uma amostra controlada não garante a taxa futura, disponibilidade ou latência em produção. A latência observada também não demonstra uma aplicação otimizada para alta carga.

Continuam necessários: identidade real e autorização de produção; carga representativa incluindo qualidade semântica e quotas reais; operação de providers autorizados; backup em outro domínio de falha; restauração funcional e failover entre hosts; segurança, recuperação de perda de disco/energia e release autorizado. Não houve validação de 100 mil pedidos aceitos ou de perda física do disco D:.

O journal local terminou com 80 cards DONE, 11 NEEDS_FIX, nove BLOCKED e um retirado por pausa do usuário, totalizando 101. A revisão final não invalidou outras aprovações. RAG-12, de carga HTTP de 100 mil pedidos, e RAG-13, de operação/recuperação de produção, permanecem bloqueados: este gate não substitui suas provas nem conclui o projeto inteiro.
