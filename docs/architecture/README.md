# Arquitetura do RAG

A aplicação é um monólito modular em Python com fronteiras de infraestrutura. Pastas representam responsabilidades, não serviços novos. O produto continua usando FastAPI, Google ADK, PostgreSQL, Qdrant, Redis, RabbitMQ, MCP remoto, Gemini gratuito autorizado e observabilidade local.

| Área | Onde ler |
| --- | --- |
| Decisões e aplicação | [Decisões](decisions) |
| Isolamento, documentos, fila e políticas | [Contratos](contracts) |
| Execução e integrações atuais | [Operação](../operations) |
| Critérios e cenários | [Aceitação](../acceptance) |
| Workflow visual | [Diagramas](../diagrams) |
| Comprovantes e limites de confiabilidade | [Qualidade](../quality) |
| Versões antigas | [Histórico](../history) |

O código permanece compatível com os módulos públicos `rag_app.api`, `rag_app.process` e demais imports existentes. Seus arquivos físicos agora ficam em `entrypoints`, `domain`, `runtime`, `persistence`, `retrieval`, `models` e `integrations`. Há uma única implementação de cada módulo.
