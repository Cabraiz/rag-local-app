# Critérios para MCPs remotos na arquitetura

Pedido de 30/09/2026: incluir GitHub e Atlassian como serviços online na arquitetura existente, também no Eraser.

Esperado: dois servidores MCP oficiais remotos, conexão HTTPS a partir do backend/container por ToolPort e gateway autorizado, GitHub para documentação/issues/PRs e Atlassian para Confluence/Jira. Autenticação, isolamento, somente leitura, quotas, timeout, logging redigido e gates de teste devem estar documentados. O índice local e o ledger continuam privados.

Proibido nesta alteração: iniciar login/consentimento, criar ou copiar credenciais, chamar contas reais, conceder acesso, criar ferramentas de escrita, modificar runtime/backend/containers, fazer deploy ou revalidar produção usando receipts antigos. Não substituir MCP remoto por Filesystem/stdio local nem declarar conexão real testada.

Menor prova: código DSL lido do editor do Eraser deve coincidir com o arquivo local atualizado; o canvas deve renderizar os dois MCPs ligados ao gateway, sem substituir a figura RAG anterior. Os contratos devem incluir os controles e distinguir checks documentais de testes reais pendentes. Guardar screenshot e receipt desta revisão. Testes anteriores permanecem históricos, vinculados aos hashes antigos.
