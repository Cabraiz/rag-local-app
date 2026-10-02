# Gate de durabilidade e recuperação — 2026-10-02

## Contrato antes da primeira alteração

Esperado: todo HTTP 202 corresponde a uma transação durável; após falhas,
o mesmo id/key continua rastreável e alcança exatamente um estado terminal.
Um timeout de confirmação exige reconciliação/reenvio com a mesma chave.
Proibido: considerar 429/503 como aceitação, chamar FAILED_FINAL/EXPIRED de
resposta bem-sucedida, apagar pedidos para ajustar contagens, flexibilizar
asserções, usar Gemini pago ou chamar testes locais de certificação de produção.

Meta neste gate: zero pedidos aceitos ausentes, zero conclusões duplicadas,
100% dos aceitos contabilizados; >=99% SUCCEEDED no workload sintético definido.
SUCCEEDED/ABSTAIN aqui prova execução do workflow sem modelo remoto, não
qualidade de resposta de negócio. O gate RAG real também exercita MiniLM,
Qdrant, ADK, evidência, revogação e isolamento em duas rodadas.

Menor prova de isolamento de recuperação: dois pedidos vencidos em buckets
distintos; manter bloqueado o bucket do mais antigo; o outro deve expirar
sem aguardar/liberar esse bloqueio. Depois ambos devem ter um único terminal.

Carga/falhas: HTTP real, confirmação descartada pelo cliente, concorrência
com mesma chave, SIGKILL/reinício de workers, queda de broker e Redis,
reinício abrupto de PostgreSQL, reparo de job ausente, prazos e fencing.
Os aceitos são comparados por IDs com SQL, audit, jobs, outbox e inbox.
Cada mudança invalida a sequência; exigidas duas rodadas sem mudanças.
Oráculos adicionais: 129 rows bloqueadas, job órfão com FK bloqueada,
fsync/full_page_writes/synchronous_commit ligados; contadores por shard e tenant
iguais ao ledger e zero jobs/auditorias ausentes. Backup pg_dump é restaurado
em outro banco e as coleções canônicas são comparadas por hash.
O destino de restauração é uma instância/volume QA separados, sem acumular
clones no cluster ativo. Ambos ainda compartilham host/disco: não é HA/DR.
O injetor retoma os IDs exatos dos containers mortos e confirma todas as
réplicas RUNNING; isso não é a aprovação final. PostgreSQL deve voltar a
responder HTTP em menos de 60s contando desde SIGKILL (não após o comando start).
Migração registra checksum na mesma transação do DDL; schema já aplicado não
repete ALTER nem inicialização de contadores em reinício. O gate mantém o
reinício Compose com dependências e repete migração sob row lock. Primeira
adoção/mudança de schema exige parada controlada dos processos do núcleo.

Workload: quatro fases de 128 pedidos distintos por rodada (1024 aceitos nas
duas), mais dois casos HTTP de confirmação por rodada (1028 ao todo), oito
clientes HTTP, política real de 3 pedidos/s + burst de 32 para Ana.
Retry de admissão usa a mesma chave e prazo de 180s; drenagem de broker
indisponível deve terminar em 120s, outras fases em 180s após aceitação.
Latências reportadas separadamente para admissão e conclusão; sem elevar
limites de produção para facilitar o teste.

Somente projeto Compose QA exclusivo: dados fictícios, sem credenciais cloud,
sem tocar nos volumes do laboratório principal; imagens e fontes congeladas.
O teste antigo de 100000 ofertas teve 770 aceitos e 99230 rejeitados: não é
prova de 100000 workflows atendidos. Este gate não fecha RAG-12/RAG-13 por
inferência, não testa perda física de D: nem failover em outro host.

Produção ainda requer identidade real, capacidade por workload, backup em
outro domínio de falha e restauração/failover comprovados, segurança e release.
