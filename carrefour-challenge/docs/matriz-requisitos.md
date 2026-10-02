# Requisitos do PDF → implementação → prova

Fonte: DESAFIO_SENIOR_IA Banco Carrefour.pdf, 2 páginas, SHA-256 8d65f159d05aa388e5456b0397505220e76268c5d0bb86646104ccb2ef3c1a3b.

| Requisito | Implementação | Prova executável |
| --- | --- | --- |
| JSON → Python, todos os agentes ADK | compiler.py, cli.py, dois JSONs | emissão determinística, import Workflow ADK, CLI nos dois specs |
| Validação e erros claros | AgentSpec/Stage, SafeError | tipos/limites/duplicatas/unknown fields/injeção/fuzz |
| Imagem de pedido fictício | tools/make_samples.py, /samples | OCR Tesseract real em duas imagens e negativos |
| OCR/RAG exclusivamente MCP SSE | ocr_server.py, rag_server.py, McpToolset | initialize/list/call e chamadas via ADK |
| Pelo menos 100 exames | data/exams.json, Catalog | 120 nomes/códigos distintos; oracle 001/002/024 |
| FastAPI e Swagger | api.py, contracts.py, assets locais fixados | schema vs resposta real; Playwright Chromium executa Try it out offline, replay/conflito, consulta e erros; desktop/mobile |
| PII removida antes modelo/estado/persistência | privacy.py, sanitização no OCR | sentinelas excluídas, PII como exame bloqueada, scan de logs/ledger |
| Fluxo CLI: lista/códigos/confirmação | cli.py, Runtime | resultado/request id/códigos e recibo API consultável |
| Somente dados fictícios | fixtures, catálogo, API | marcadores fictícios e nenhuma credencial montada |
| Docker, um Compose | Dockerfile, docker-compose.yml | build, saúde, CLI/testes em container, rede interna |
| README/JSON/imagem/evidências/URL Swagger | README, examples, evidence | comandos reais, relatórios/artefatos exportados, screenshots e traces Swagger; recibo CLI reconciliado no browser |
| Transparência IA/arquitetura/guardrails/testes | docs, README | defesa técnica e recebimentos com hashes |

Os estados de execução vêm dos relatórios, não desta tabela. Não confundir catálogo mock permitido com mock do transporte ou agente: esses são exercitados de verdade.
