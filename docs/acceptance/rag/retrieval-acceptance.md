# Continuação: recuperação real em laboratório

Esperado antes da escrita: ingestão de bundles sintéticos text/plain, catálogo e
chunks imutáveis no PostgreSQL, índice Qdrant real em volume POSIX, snapshot fixo
no aceite do pedido, filtro tenant/ator e reautorização antes de compor, commitar
e ler a resposta. Citações devem apontar para chunks canônicos; revogação impede
publicação e entrega. Mesmo bundle repetido não duplica release ativo; falha de
indexação nunca promove candidato incompleto. API e ADK usam os mesmos serviços.

Proibido: importar dados reais, habilitar contas/egress/cloud/modelo pago, trocar
índices de outro projeto, apagar volumes antigos, transmitir candidato bruto ou
chamar hash lexical de embedding semântico avançado. Produção continua recusada.

Menor prova: HTTP + PostgreSQL + Qdrant + grafo ADK reais, corpus sintético novo
por seed, cenários fixados antes da execução, controles negativos e duas rodadas
consecutivas com fontes/imagens congeladas. Reexecutar o lifecycle anterior após
mudanças. Nenhum teste de transporte local certifica GitHub/Atlassian hospedados.

Perfil inicial é `extractive_lab`, opt-in separado do default de abstenção.
Embedding hash lexical versionado é uma baseline para exercitar contratos e
busca dense/sparse/RRF; não é modelo neural nem benchmark de qualidade empresarial.
Resposta EXTRACTIVE cita o trecho literal, sem gerar conclusões ou cálculos.
Modelo neural, reranker semântico, Gemini, DeepAgents, judge e produção mantêm
seus gates específicos. Nova evidência não renomeia esses PENDING como aprovados.

Formatos PDF/ZIP/HTML não estão habilitados: rejeitar é o resultado esperado,
não fingir que existe parser seguro para todos os formatos. Bundles completos
têm até 8 documentos e 12.000 bytes UTF-8; a borda HTTP continua limitada.

Protocolo do produtor no laboratório: 503 INDEX_UNAVAILABLE conserva candidato,
não confirma READY e informa Retry-After. O caller repete o MESMO bundle até três
chamadas com espera limitada; não consulta usando uma versão esperada antes do
201 READY. Queda deliberada testa a primeira resposta 503 sem retry. O serviço
não promete reconciliação autônoma de ingestões sem caller neste perfil.
Logs do worker devem registrar exatamente retrieve/compose/verify por pedido,
sem conteúdo de fontes/pergunta. A prova verifica o JSON emitido; spans sem um
exporter configurado não certificam tracing distribuído.

## Prontidão de índice com volumes preservados

Falha preservada: `eval/runs/rag-real-20260930T235308Z-ee1453/receipt.json`.
Qdrant iniciou em 23:54:05 UTC e abriu REST em 23:54:37 UTC após recuperar as
coleções imutáveis existentes. O limite de 30 segundos do harness expirou antes
disso; nenhum OOM foi registrado. Não apagar volumes ou coleções para obter pass.

Correção do protocolo LAB antes de novos inputs: aguardar resposta válida da API
de coleções na inicialização e após restart/outage, com teto fixo de 120 segundos
e duração/contagem registrados no receipt. Processo iniciado não significa API
pronta. Após prontidão, reconciliação mantém seu limite original de 30 segundos.
Resultados esperados, citações, isolamento e conservação de pedidos não mudam.

Um pass nessa janela NÃO certifica recuperação de produção em 30 segundos,
prontidão contínua do produto, HA ou crescimento ilimitado de coleções. GC seguro,
RTO e retenção de versões em produção permanecem gates próprios não concluídos.
