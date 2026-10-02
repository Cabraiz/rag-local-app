# RAG local

Aplicação de laboratório em Python e Google ADK. O frontend, o backend e os bancos rodam em containers locais. Gemini e os MCPs de Jira e GitHub são integrações remotas autorizadas. O laboratório não é uma liberação de produção.

## Por onde começar

| Quero entender | Abra |
| --- | --- |
| O produto e o que já funciona | [Guia do PO](docs/product/README.md) |
| O que falta e os cards atuais | [Fila e bloqueios](docs/cards/README.md) |
| A arquitetura e os contratos | [Arquitetura](docs/architecture/README.md) |
| Como executar e manter | [Aplicação](app/README.md) |
| Testes e comprovantes | [Qualidade](eval/README.md) |
| O desafio técnico separado | [Desafio Carrefour](carrefour-challenge/README.md) |

## Pastas

- `app`: backend, agentes, testes e infraestrutura, separados por responsabilidade.
- `frontend`: interface do laboratório.
- `docs`: produto, cards, decisões, contratos e guias de operação.
- `eval`: dados de avaliação, comprovantes históricos e logs agrupados.
- `carrefour-challenge`: aplicação do desafio técnico, independente do RAG empresarial.
- `adk`: ambiente Python já instalado. Não confundir com o código da aplicação.
- `.local`, `cache` e `tmp`: dados privados, dependências ou artefatos temporários. Não são páginas de produto nem devem ser versionados.

Aplicação: <http://127.0.0.1:8840/>. Grafana: <http://127.0.0.1:8850/d/rag-overview/rag-local>. Desafio: <http://127.0.0.1:8860/docs>.
