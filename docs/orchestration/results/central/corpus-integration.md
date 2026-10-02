# Integração e validação do corpus

Em 2 de outubro de 2026, a Central integrou o worktree de corpus e aprovou
os segmentos RAG-05 e RAG-06 no laboratório isolado. A aprovação cobre os
cenários executados com fontes e imagens congeladas; não certifica produção
pública, auditoria cega independente ou ausência de todo bug possível.

## Integridade da entrega

A entrega ficou limitada a quatro arquivos autorizados: `corpus.py`,
`semantic_policy.py`, `corpus_offline_checks.py` e o relatório do executor.
A Central verificou os 155 hashes de fontes e os 33 artefatos do receipt,
sem alterar datasets, oráculos originais ou critérios de aprovação.
O manifesto canônico tinha apenas a atualização central anterior do BUG-126;
as lanes e suas allowlists permaneceram iguais.

O executor estava encerrado, e a tarefa de supervisão estava em estado Ready.
A Central criou o commit local que o sandbox do executor havia impedido,
sem modificar ACLs, recriar o chat ou abrir outro writer no worktree.

- Branch do corpus: `codex/rag-corpus`.
- Commit da entrega: `67344ef3953584d3a1435d4696565ab769d3a090`.
- Merge sem conflito: `11e9a520c2a9a6f6c0b364e787ee0f133fd80c78`.

## Correções incorporadas

O corpus agora divide as chamadas de embeddings em lotes de até 32 textos,
sem publicar pontos antes de concluir todos os lotes. Também rejeita formatos
de entrada inválidos e Unicode não codificável antes da ingestão, valida os
UUIDs, a unicidade e o limite de candidatos retornados pelo Qdrant, e distingue
preço de produto de teto de reembolso. A condição sobre trabalhar em casa
deixou de ser interpretada obrigatoriamente como uma pergunta sobre horários.

A Central reproduziu seis falhas nos blobs imutáveis do código anterior,
sem checkout ou alteração dos arquivos físicos. Os cinco bugs novos foram
acrescentados ao fim da fila como BUG-127 a BUG-131 e depois encerrados com
regressões offline verificadas. A correção da condição temporal complementa
o BUG-036 já existente, sem criar um card duplicado.

## Testes comprovados

| Gate | Resultado atual | Limite da prova |
| --- | --- | --- |
| Offline no worktree | Nove suítes passaram duas vezes | Oráculos originais e adaptadores controlados |
| Offline após o merge | Nove suítes passaram duas vezes | Fontes canônicas congeladas |
| Ingestão e arquivos | 41 verificações em cada uma de duas rodadas | HTTP, PostgreSQL, Qdrant e volume real do QA |
| Recuperação semântica | 48 verificações por rodada, incluindo 28 perguntas reservadas | ONNX local, busca híbrida e ADK reais |
| Reinício do Qdrant | 15,74 s e 15,30 s, preservando duas coleções | Limite LAB original de 120 s; não é RTO de produção |
| Falha e retry de índice | Duas rodadas; 503 conserva READY e retry reutiliza candidato | Mesmo payload aditivo, com 44 chunks neurais reais |
| Condição e negativos | Duas rodadas com três perguntas cada | Aprovação em trabalho remoto; abstenção para horário e preço sem evidência |

O modelo manteve a calibração original, threshold de 0,3 e margem de 0,05.
Os testes reservados não foram usados para ajustar esses parâmetros.
O código dos dois módulos alterados também foi conferido dentro da imagem QA.

Os testes pesados usaram exclusivamente o projeto previamente existente
`rag-local-qa-20261001`, com trava local, imagens próprias e parada ao concluir.
Os 16 containers ativos do laboratório principal permaneceram com os mesmos
IDs. Nenhum volume foi apagado, nenhuma imagem ativa foi substituída e nenhuma
chamada cloud foi feita.

Uma tentativa inicial de QA falhou ao tentar criar uma rede default extra;
o comprovante foi preservado. A composição foi corrigida para reutilizar apenas
as redes data e edge existentes. A execução sequencial também passou a tratar
o SystemExit de sucesso da fixture original sem pular a suíte seguinte.
Esses ajustes não mudaram as assertions das fixtures.

## Cards e trabalho restante

Foram encerrados ou revalidados 17 itens nesta etapa: os cinco bugs novos,
BUG-017, BUG-022, BUG-023, BUG-026, BUG-034 a BUG-038, BUG-060, RAG-05 e RAG-06.
O BUG-024 manteve sua aprovação vigente. O BUG-039 permaneceu retirado pelo
usuário e não foi reativado.

RAG-16 e BUG-057 a BUG-059 continuam pendentes da publicação aditiva das
regras de alimentação e de 22 consultas grounded reais com Gemini, em duas
rodadas. Os passes offline não substituem esse gate. Faturamento, fallback
pago, Vertex e bypass de quota continuam proibidos.

O snapshot do journal ao concluir esta integração tinha 116 itens: 21 DONE,
88 NEEDS_FIX, seis BLOCKED e um retirado. As outras frentes não foram aprovadas
por este relatório. Uma alteração posterior de fonte invalida os comprovantes
afetados e exige nova sequência de duas rodadas.

## Localização das provas privadas

Os comprovantes permanecem locais, fora do Git. A auditoria da entrega e o
registro dos novos bugs estão em `.local/orchestration/parallel-20261002-v1/corpus/`.
As nove suítes integradas estão em `.local/orchestration/corpus-integrated-01/`.
Os receipts reais de arquivos e semântica estão em `eval/runs/current-files-20261002T183755Z-1fa5d8/`
e `eval/runs/current-semantic-20261002T184202Z-9b6c19/`.
As provas de reinício, retry, condição e fechamento dos cards estão em
`eval/reports/orchestration/`, nas pastas de corpus com sufixo `11e9a520`.

O código integrado ainda não substitui as imagens do laboratório principal.
O rollout local deve ocorrer após a integração e a validação das demais frentes.
