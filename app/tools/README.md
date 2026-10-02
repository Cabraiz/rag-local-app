# Operações do RAG

| Pasta | Uso |
| --- | --- |
| `cards` | Consultar e manter a fila canônica |
| `ingestion` | Publicar documentos e evidências explicitamente selecionados |
| `runtime` | Iniciar e supervisionar o laboratório existente |
| `revalidation` | Executar gates por segmento |
| `receipts` | Derivar comprovantes sem inventar execução |
| `diagnostics` | Diagnósticos locais delimitados |
| `credentials` | Salvamento privado de credenciais, nunca publicação |
| `organization` | Inventário, migração e validações desta reorganização |

Use `app/manage.py tool NOME_DO_SCRIPT` para Python. Os supervisores mantêm seus limites e o Compose usa a pasta `app` como diretório do projeto, mesmo com os perfis em subpastas. Consulte o [guia da aplicação](../README.md).
