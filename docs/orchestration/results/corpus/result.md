# Corpus: revalidação offline de 02/10/2026

Branch `codex/rag-corpus`, base `3dba8c0f557421cd3f6d5bc26017278a95a53dbb`.
Escopo offline concluído; aprovação de integração pendente dos gates da Central.
Commit local bloqueado: `git add` recebeu Permission denied ao criar o
`index.lock` administrativo deste worktree. HEAD permanece na base, sem staged
files ou alterações na branch. O diff está preservado no worktree e em
`.local/orchestration/corpus-handoff.patch` para revisão e commit pela Central.
Os 18 cards do snapshot atribuído foram examinados. Estados antigos, inclusive
DONE e a retirada de BUG-039, não foram convertidos em aprovação atual nem alterados.

## Correções reproduzidas

- Um corpus aditivo válido de 22 documentos/44 chunks era enviado de uma vez ao
  RPC neural, que aceita no máximo 32 textos. A indexação divide a entrada em
  lotes de 32/12, preserva ordem, IDs e escopo, e publica os pontos somente depois
  de obter todos os embeddings. Falha no segundo lote não publica pontos parciais.
- Documentos e `source_key` de tipos inválidos escapavam como TypeError ou
  AttributeError. A validação retorna erro controlado 422 antes da ingestão.
- Surrogates Unicode isolados em título/texto escapavam como UnicodeEncodeError.
  A validação rejeita esses dados com 422, sem gravar candidato.
- Respostas Qdrant malformadas escapavam como exceções genéricas; uma resposta
  com 17 IDs também era aceita e gravada no cache apesar do limite de 16.
  Estrutura, UUIDs, limite e unicidade são verificados antes de preencher o cache.
- Uma consulta de preço de alimento podia qualificar um teto de estacionamento
  por conter moeda. Preços/custos exigem uma afirmação correspondente na fonte;
  limites de reembolso continuam válidos para consultas sobre limites.
- BUG-036: uma condição inicial como “Quando trabalho em casa preciso de
  aprovação?” exigia indevidamente dias/horas. A condição é separada da pergunta
  temporal; perguntas de horário/prazo continuam exigindo evidência temporal.

As cinco primeiras falhas são propostas para deduplicação e inclusão pela Central
ao fim da fila. A última é uma reprodução adicional de um card existente. Nenhum
ID novo foi reservado e o journal não foi editado.

## Provas e limites

Duas rodadas consecutivas finais executam nove suítes: reconstrução/publicação,
readiness de restart, retry de índice, validação neural em Python normal e `-O`,
planejamento/guard ADK, conta coletiva, procedimentos de teto, candidatos para
grounding e 31 testes adicionais de corpus/documentos. Fontes, critérios e datasets
são vinculados por SHA-256 nos receipts privados da lane. As falhas anteriores
continuam preservadas; a sequência final começa depois da última edição.

Os oito scripts originais e seus oráculos não foram editados. Calibração e os dois
holdouts portugueses permanecem byte a byte preservados. Teto base de 45 reais,
prazo de 12 dias úteis, 20 regras Aurora e período ainda indefinido foram mantidos.
Nenhum limiar foi ajustado usando holdouts. As rodadas são do mesmo executor;
não constituem auditoria independente.

HTTP/SQL/Qdrant/POSIX são adaptadores controlados nesses testes. O parser real
é exercitado com entrada TXT/Markdown/PDF e um adaptador de `resource`; os limites
de CPU/memória e os locks precisam de prova POSIX real. Os testes SQL verificam
contratos e chamadas, sem certificar transações, locks ou concorrência reais.
Não há `fastembed` no Python autorizado nem pesos no worktree. Nenhum modelo,
provider, banco ou container canônico foi chamado ou alterado.

| Card | Prova atual offline | Gate ainda pendente |
| --- | --- | --- |
| RAG-05 | formatos, originais, manifest/replay, publicação aditiva, revogação e chamadas com escopo | API/SQL/Qdrant/volume originais reais |
| RAG-06 | validação RPC, filtros dense/sparse/RRF, layouts, abstenção e holdouts preservados | ONNX/Qdrant/ADK e holdouts reais |
| BUG-017 | headers mistos e ausência de headers obrigatórios | download HTTP do pai |
| BUG-022 | helper original: readiness em 30s, timeout preservado | restart/persistência do pai |
| BUG-023 | helper original: mesmo manifesto, três tentativas, apenas INDEX_UNAVAILABLE | indisponibilidade e retry reais do pai |
| BUG-024 | os dois trechos Python gerados originais compilam | execução SQL do pai |
| BUG-026 | versão isolada versus tombstone de fonte, sem reviver fonte | versões/downloads e SQL reais |
| BUG-034 | helper de 120s preserva timeout; fixture real examinada | boots reais com coleções preservadas em até 120s |
| BUG-035 | 100 releases usam um nome compartilhado por família; legacy preservado | quantidade/schema e isolamento Qdrant reais |
| BUG-036 | condição temporal adicional corrigida, aprovação e prazo preservados | recuperação real das consultas |
| BUG-037 | aliases de reparo e exclusões industriais preservados | FAQ português com embeddings reais |
| BUG-038 | fixture original em Python normal e otimizado | contrato RPC completo no serviço integrado |
| BUG-039 | critérios examinados; retirada preservada | não retomado nem marcado DONE |
| RAG-16 | dataset/versionamento, adição, regras e negativos locais | publicação e 22 consultas grounded reais |
| BUG-057 | fixture original preserva threshold/margin, cap de cinco e candidatos | seletor e qualidade grounded reais |
| BUG-058 | dez controles originais de conta coletiva/banco | consulta grounded do pai |
| BUG-059 | doze controles originais de processo/valor | consulta grounded do pai |
| BUG-060 | readiness de índice, deadline de 90s e ordem anterior à ingestão | QA isolado real |

## Dependências para integração

1. A Central integra o diff e revalida os arquivos compartilhados de API, roles,
   ledger e runtime. As fixtures antigas de documento e seed usam rotas de
   substituição desabilitadas; não devem substituir as fixtures atuais nem ter
   assertions relaxadas. Preferir `current_queue_fixture.py` e
   `current_semantic_fixture.py` no projeto QA isolado e serializado.
2. A alteração de política muda seu fingerprint. O serviço semântico precisa
   gerar gates para a fonte integrada com a mesma calibração pinned antes da
   verificação real. Fontes/cache com hash anterior devem continuar falhando
   de forma fechada. Pesos públicos pinned e dependências pertencem ao gate pesado;
   nada foi instalado no ambiente compartilhado.
3. Executar boots preservados de Qdrant sem apagar coleções/volumes nem aceitar
   mais que os 120s originais. A fixture de boot exige ownership e coleções
   preservadas; não operá-la contra o laboratório ativo por inferência.
4. Publicar Aurora aditivamente e executar `meal_policy_smoke.py` somente na
   validação online autorizada da Central, com dados sintéticos, duas rodadas,
   intervalo mínimo original de 8s, sem fallback pago nem reset de contadores.
5. Repetir os gates integrados antes de atualizar cards. Este resultado não
   certifica produção, HA, 100 mil workflows ou ausência total de bugs.
6. Resolver o acesso de escrita ao Git administrativo ou aplicar/commitar o patch
   pela Central. O executor não alterou ACLs nem contornou o bloqueio do sandbox.

Receipt terminal: `.local/orchestration/worker-receipt.json` no worktree corpus.
Logs, fontes congeladas e tentativas ficam exclusivamente em `.local/orchestration/`.
