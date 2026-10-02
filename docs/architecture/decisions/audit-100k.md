# Auditoria adversarial: 100.000 requests sem esquecimento

Data: 30/09/2026. Escopo: arquitetura da aplicação em `application.md` e `application-eraser.txt`, correspondente ao desenho **Aplicacao RAG - monolito modular**. O backend continua não implementado. Eraser e os dois arquivos de arquitetura foram preservados.

## Veredito

**Arquitetura ainda não aprovada para uma garantia de 100.000 requests sem perda.** Ledger transacional, outbox, fences, idempotência e consulta autenticada são fundamentos adequados. Faltam contratos operacionais precisos para failover, recuperação sob saturação, capacidade/espera por tenant e retenção de recibos.

Uma referência executável isolada testa conservação de pedidos, não o ADK, o banco PostgreSQL ou o desempenho do modelo. **Duas rodadas corrigidas passaram 24/24 checks cada**, com 100.000 pedidos aceitos sintéticos por rodada e zero pedidos ausentes ou sem terminal ao fim. Isso aprova apenas os cenários da referência; as quatro lacunas operacionais abaixo permanecem abertas.

## Contrato de aceitação e menor prova

- Esperado: cada pedido aceito tem recibo durável, permanece consultável na janela contratada e chega a exatamente um terminal lógico, inclusive CANCELLED, EXPIRED ou FAILED_FINAL. ABSTAIN não é resposta factual bem-sucedida.
- Proibido: responder aceite antes da persistência; apagar o único registro; depender de memória, sessão ADK, notificação, índice vetorial ou worker original para lembrar o pedido; publicar terminal duplicado; aceitar commit de dono obsoleto.
- Menor prova nesta etapa: gravar 100.000 pedidos sintéticos em um ledger SQL isolado, injetar falhas, fechar/reabrir o banco e comparar IDs, estados, auditoria e outbox com o resultado esperado, incluindo controles deliberadamente defeituosos.
- Limite: isso não prova capacidade de receber 100.000 requests HTTP, simultâneos ou por segundo. Não substitui integração real nem dimensionamento de infraestrutura.

Pedidos oferecidos, tentativas duplicadas e pedidos aceitos são contagens diferentes. A conservação é `aceitos = pendentes + terminais`, dentro da retenção contratada. Rejeitar antes do aceite com 429/503 é backpressure; não é perder um pedido aceito. Persistir pendências para sempre também não basta: é necessário limite de idade, recuperação e terminal rastreável.

## Método e evidências executáveis

Runner: [audit_100k.py](../../audits/scripts/audit_100k.py). Dados exclusivamente sintéticos em `D:\RAG-Local\eval\runs`; sem rede, serviços, credenciais ou chamadas de modelo. SQLite/WAL com synchronous=FULL é uma fixture de queda de processo, **não o runtime compartilhado proposto**, que continua PostgreSQL. Transações em lotes de 1.000 são conveniência da fixture, não simulação de 1.000 chamadas HTTP.

Dois seeds aleatórios e hashes do runner/arquitetura são registrados antes da execução; os resultados esperados são calculados antes das transições. O mesmo revisor constrói e analisa o teste: é revisão adversarial com entradas congeladas, não auditoria cega independente.

### Histórico preservado, sem transformar falha em aprovação

Execução inicial: [receipt.json](../../../eval/runs/admission100k-20260930T195351Z-982bc1/receipt.json). Seeds `1644083566` e `4100967570`, 100.000 pedidos por rodada, 22/23 checks em cada, exit code 1. Ambas conservaram os 100.000 registros terminais, mas o controle `negative_missing_fence_detected` falhou.

Causa no harness: após recuperação, o pedido estava ACCEPTED, não RUNNING com um novo dono. Assim, tanto a implementação protegida quanto a mutante sem fence rejeitavam o estado, impedindo o teste de distinguir a proteção. A correção cria explicitamente um novo dono RUNNING com fence 3 e testa o resultado antigo com fence 1. Foi acrescentada uma asserção da precondição; não se removeu o controle nem se mudou o oráculo de resultados.

O rótulo `verdict` inicial era uma string fixa inadequada; os checks e o exit code corretamente indicaram falha. O runner corrigido deriva o rótulo dos checks e da preservação dos arquivos. Também corrigimos o nome do contramodelo FIFO: ele demonstra espera longa, não starvation infinita. O cache da fixture foi ampliado sem mudar synchronous=FULL ou fronteiras transacionais; não comparar seus tempos como benchmark.

### Execução corrigida

Contrato: [contract.json](../../../eval/runs/admission100k-20260930T200332Z-3f98be/contract.json). Resultado: [receipt.json](../../../eval/runs/admission100k-20260930T200332Z-3f98be/receipt.json). Processo encerrado com exit code 0; nenhum check falhou e os hashes congelados permaneceram iguais, inclusive em verificação posterior.

| Rodada | Seed | Pedidos aceitos | Checks | Pendentes/ausentes ao fim | Tempo da fixture |
| --- | --- | --- | --- | --- | --- |
| 1 corrigida | 1208812628 | 100.000 | 24/24 | 0/0 | 225,453 s |
| 2 corrigida | 3774836765 | 100.000 | 24/24 | 0/0 | 190,076 s |

Em **cada** rodada: 90.000 SUCCEEDED (86.000 ANSWER e 4.000 ABSTAIN), 3.000 FAILED_FINAL, 4.000 EXPIRED e 3.000 CANCELLED. Assim, todos os 100.000 têm um desfecho, mas não todos têm uma resposta factual. Tempos descrevem esta fixture serial em lotes, não capacidade HTTP/LLM.

Checks executados por rodada:

- Persistência de 100.000 IDs; 20.000 submissões repetidas sem novo registro; comparação de 2.000 payloads conflitantes.
- Queda abrupta de subprocesso antes do commit com rollback e depois do commit com terminal/outbox preservados.
- Detecção e reparação de 333 jobs removidos; recuperação de 7.999 leases expirados (o oitavo milésimo já havia commitado terminal no crash-after).
- Novo dono RUNNING/fence 3 explícito; rejeição do worker com fence 1; a mutante sem fence é detectada como insegura.
- 8.000 retries com agendamento durável; expiração sem inferência; cancelamento e abstenção explícitos.
- Igualdade com o oráculo de estados; um audit e uma outbox terminal por request; 20.000 redeliveries sem segundo terminal.
- 5.000 notificações duplicadas deduplicadas; 2.000 notificações de cliente perdidas sem apagar a verdade no ledger.
- Controle negativo remove outbox terminal e é detectado; SQLite integrity_check e foreign_key_check aprovados.

Depois do runner, consultas SQL somente leitura em ambos os bancos deram **zero** divergências entre estado e auditoria, zero identidades de evento terminal incorretas e zero IDs com quantidade de eventos terminais diferente de um. Essa releitura é uma verificação adicional dos artefatos, não revisão independente de outra pessoa.

### Preservação da arquitetura

SHA-256 da proposta, iguais antes e depois dos testes:

- `application.md`: `cfd0c21bbf4ddb61d84e3d03203878d2f039fc7e1bcf52cc585a91369f360369`
- `application-eraser.txt`: `54e7784f0d97b06deac5d653376ecd3d90386f3f07a000396b52398472acb10d`

O Eraser não foi acessado nem editado nesta rodada. Os arquivos SQLite e os receipts, inclusive os da execução inicial não aprovada, estão preservados nos diretórios de evidência.

## Quatro lacunas que impedem a aprovação operacional

São lacunas da especificação, não incidentes reproduzidos no backend, que ainda não existe. Os contramodelos usam hipóteses explícitas para demonstrar por que certas configurações permitidas por um contrato incompleto seriam inseguras.

| Lacuna | Cenário adversarial | Contrato necessário | Menor teste real futuro |
| --- | --- | --- | --- |
| P0: aceite versus failover | Primário confirma e cai antes de replicar; promoção de réplica atrasada remove pedidos confirmados. “Postgres HA” não define RPO. | Definir se o perfil oferece RPO zero para os tipos de falha acordados; commit síncrono durável, réplica elegível e fencing do antigo primário; não confirmar quando a durabilidade exigida estiver indisponível. O perfil D: único não oferece essa garantia contra perda do disco. | Receber recibos HTTP, interromper primário, promover réplica conforme política e consultar todos os IDs confirmados; testar também ausência de quorum e split-brain. |
| P1: recuperação sem capacidade reservada | Jobs existem, mas workers/modelos saturados ou bloqueados impedem varredura, expiração e retomada. | Entry point e supervisão do recovery/control worker explícitos; capacidade SQL/CPU/IO reservada; varredura limitada e indexada, deadline, novo fence e alerta por idade. Não depender de coletor de logs nem de chamada LLM para concluir EXPIRED. | Bloquear todos os workers de inferência e verificar que controle/status continuam vivos e expiram/retomam dentro do SLO. |
| P1: capacidade e espera por tenant não dimensionadas | 100.000 chegam de uma vez; limite de armazenamento/filas é menor. Um tenant também pode pôr 100.000 à frente de outro. | Limites de pedidos e bytes, reserva atômica, headroom de WAL/terminais, quotas, escalonamento justo e idade máxima. Fixar workload/SLO e política: aceitar todos duravelmente ou rejeitar excedente explicitamente antes do aceite. | Gerador HTTP com burst, concorrência e tamanhos definidos; auditoria dos recibos; flood por tenant, disco perto do limite, retry storm e latência por tenant. |
| P1: retenção e idempotência sem janela definida | Limpeza apaga a chave antes do fim do retry; o mesmo cliente cria outro pedido, ou perde a consulta de um recibo antigo. | Vincular chave a tenant/ator e hash; definir janela de retries/status, tombstone ou recibo mínimo e comportamento explícito após expiração. Não prometer deduplicação eterna nem guardar conteúdo privado para sempre. | Retry nos limites da janela, limpeza concorrente com aceite/consulta e tentativa de acesso cross-tenant; conferir IDs estáveis dentro do contrato. |

### Contramodelos: exemplos, não medições

- Replicação assíncrona: conjunto primário de 100.000 e réplica de 99.683 permite perder 317 IDs ao promover a atrasada. **317 é um valor escolhido para o exemplo**, não uma taxa de perda observada. PostgreSQL documenta que replicação streaming é assíncrona por padrão e pode perder commits no failover; o modo síncrono exige configuração e política operacional apropriadas. [Documentação PostgreSQL](https://www.postgresql.org/docs/current/warm-standby.html).
- Pool compartilhado hipotético: 32 tarefas de IA presas em 32 slots deixam zero espaço para expiração. Não medimos esse pool nem afirmamos que os entrypoints atuais compartilham tal executor; falta fixar explicitamente o isolamento do controle.
- FIFO: B chega após 100.000 de A e não aparece nas primeiras 1.000 execuções. Isso prova atraso no exemplo finito, não starvation infinita. SKIP LOCKED auxilia claims de filas, mas não é um algoritmo de justiça. [Documentação PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html).
- Retenção: apagar o único vínculo de uma chave permite tratar seu retry como novo. Não existe rotina de limpeza implementada para ter sido executada aqui.
- Admissão hipotética com limite de 10.000: 100.000 oferecidos = 10.000 aceitos + 90.000 recusados explicitamente. Zero aceitos esquecidos não significa aceitar todos.
- Se a vazão completa fosse 10, 100 ou 1.000 pedidos/s, drenar 100.000 levaria ao menos 10.000, 1.000 ou 100 segundos, sem retries. Esses números são cálculo com vazão **assumida**, não benchmark.

No perfil com RabbitMQ, confirms, mensagens persistentes, filas duráveis/replicadas, tratamento de mensagens não roteadas e ACK após transferência durável precisam de testes reais. Confirm perdido e redelivery podem duplicar trabalho; consumidores precisam ser idempotentes. O inbox desta fixture não testa o broker. [RabbitMQ reliability](https://www.rabbitmq.com/docs/reliability).

## Checklist de aprovação do backend real

- [ ] Workload: 100.000 no total ou simultâneos? Burst/RPS, tenants, bytes/tokens, duração, prazo e SLO definidos.
- [ ] POST/202 vinculado a commit durável; testar conexão perdida antes/depois do commit e retry com a mesma chave.
- [ ] Recibos HTTP aceitos conservados após restart, failover elegível e consulta autenticada; RPO documentado por perfil.
- [ ] Concorrência real: claims, leases, fences, deadline, cancel versus finalize e crash em cada fronteira transacional.
- [ ] Reconciliador independente e reservado: nenhum pedido sem job acionável; alertas por idade e outbox parada.
- [ ] Limites de backlog/bytes e headroom de disco/WAL; ingestão, EVAL e logs não bloqueiam o ledger/control worker.
- [ ] Justiça por tenant, retry/backoff/jitter e teto de tentativas; não medir somente latência média global.
- [ ] Duplicação de broker, confirmações perdidas, roteamento falho e notification disconnect; status não depende de SSE.
- [ ] Retenção de recibos/chaves, limpeza e exclusão de dados com contrato explícito e isolamento tenant/ator.
- [ ] Integração real ADK/DeepAgents, Qdrant, modelo e ferramentas: timeout, cancelamento, autorização e resposta verificada.
- [ ] Restauração de backup fora do mesmo domínio de falha; falha total de todas as cópias permanece fora de garantia.
- [ ] Duas rodadas adversariais consecutivas aprovadas com controles negativos, evidência de HTTP/infraestrutura real e escopo declarado. Não equivalem a ausência universal de bugs.

## O que este teste não cobre

Não houve carga HTTP, requests simultâneos, PostgreSQL real, failover de broker, outage do ADK, benchmark de GPU/modelo, qualidade de recuperação/citações, perda de energia/disco, testes de isolamento HTTP ou endpoints reais de polling. Checar payload divergente é comparação SQL, não teste de resposta HTTP 409. Ler terminal após perder uma notificação é prova do ledger consultável, não execução de GET autenticado.

O operador pode continuar olhando logs/métricas, sem aprovação humana de cada resposta. A garantia de recuperação deve vir de processos automáticos e do ledger, **não de um backdoor nem de alguém lembrar manualmente do request**. Esta rodada entrega diagnóstico e evidência isolada; não altera políticas, diagramas ou implementação.
