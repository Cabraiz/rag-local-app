# Aplicação RAG

Este diretório contém o backend e sua infraestrutura local. Para compreender o produto, comece pelo [guia do PO](../docs/product/README.md). Para acompanhar trabalho pendente, abra a [fila de cards](../docs/cards/README.md).

## Responsabilidades

| Pasta | Conteúdo |
| --- | --- |
| `src/rag_app` | Entradas HTTP, domínio, recuperação, modelos, persistência, integrações e runtime |
| `infrastructure/compose` | Perfis do ambiente normal, observabilidade, resiliência, laboratório e QA |
| `infrastructure/images` | Dockerfiles do backend e do gateway MCP |
| `infrastructure/dependencies` | Dependências fixadas e hashes, sem mudança de versões nesta organização |
| `monitoring` | Prometheus, Grafana e collector OpenTelemetry |
| `semantic` | Serviço de embeddings locais e seus pesos preservados |
| `advanced` | DeepAgents, avaliação DeepEval e modelo gratuito restrito |
| `tests` | Testes agrupados por área, não um único diretório com dezenas de scripts |
| `tools` | Operações, publicação de documentos, fila e validações |

## Comandos

Execute no terminal integrado. O ambiente Python já existente é mantido em `D:\RAG-Local\adk\.venv`.

```powershell
& D:\RAG-Local\adk\.venv\Scripts\python.exe D:\RAG-Local\app\manage.py cards status
& D:\RAG-Local\adk\.venv\Scripts\python.exe D:\RAG-Local\app\manage.py runtime ps
& D:\RAG-Local\adk\.venv\Scripts\python.exe D:\RAG-Local\app\manage.py runtime config --quiet
& D:\RAG-Local\adk\.venv\Scripts\python.exe D:\RAG-Local\app\manage.py locate meal_policy_smoke.py
```

Para executar um teste existente: `manage.py test NOME_DO_ARQUIVO`. Para uma ferramenta: `manage.py tool NOME_DO_ARQUIVO`. Cada script mantém seus limites; selecionar um teste real de Gemini pode consumir a cota gratuita, embora não habilite faturamento. As checagens da reorganização não precisam chamar modelos remotos.

O Compose deve usar `--project-directory D:\RAG-Local\app`, pois os perfis foram separados em subpastas. `manage.py runtime` e os supervisores já fazem isso. `runtime up` inicia somente o projeto local existente, sem build, sem excluir volumes e sem ativar os perfis QA ou advanced.

O histórico extenso do antigo README foi preservado em [notas históricas](../docs/history/application/implementation-notes-20261002.md). Ele documenta versões anteriores, não substitui a [situação atual](../docs/cards/backlog-results-20261002.md).
