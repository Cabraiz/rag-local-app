# Código do backend RAG

Os arquivos estão agrupados por responsabilidade, sem criar novos serviços.

| Pasta | Conteúdo |
| --- | --- |
| `entrypoints` | API HTTP, inicialização e papéis demonstrativos |
| `domain` | Contratos e regras de decisão do assistente |
| `runtime` | Workers, mensageria, cache, limites e observabilidade |
| `persistence` | Ledger, catálogo e schema SQL |
| `retrieval` | Documentos, ingestão, embeddings, busca e publicação |
| `models` | Workflow Google ADK e Gemini |
| `integrations` | Gateway e adapters MCP GitHub e Atlassian |

Os imports públicos continuam sendo `rag_app.api`, `rag_app.ledger`, `rag_app.process` e os demais nomes anteriores. `__init__.py` registra os diretórios físicos no caminho desse mesmo pacote Python. Não há cópias paralelas do código nem alteração das regras de negócio para reorganizar arquivos.
