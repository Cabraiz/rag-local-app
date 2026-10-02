# MCP Atlassian — primeira fatia executável

Atualização: após autorização específica, token inicial criado e política MCP
habilitada; leitura permaneceu com erro 401. Substituição equivalente aprovada,
token novo criado com os mesmos três escopos e validade; anterior preservado.
Contrato adicional: `atlassian-replacement-acceptance.md`. Compose opt-in monta
somente `.local/secrets/atlassian-mcp-token-replacement.txt`, sob o mesmo alias
privado. Esta mudança zera a sequência; testes devem ser repetidos na revisão final.
O parágrafo de step-up abaixo é histórico da primeira tentativa, não estado atual.

Autorização de 30/09/2026: criar e guardar privadamente o token MCP V2 do laboratório,
somente `read:jira:agent-interface`, `search:jira:agent-interface`, `read:me`, até
07/10/2026. Criação recusada pelo provedor por step-up expirado; nenhum token criado.
Verificação pessoal do usuário pendente. Não repetir a criação sem verificar o estado.

Esperado: probe opt-in isolado em Python/ADK, SDK MCP oficial fixado, HTTPS somente
`https://mcp.atlassian.com/v2/mcp`, Basic do usuário somente nesse destino. Ler apenas
KAN-1/KAN-2 do cloudId `15445c1f-6463-4ece-bdb1-eecd7c4d5968`. Uma sessão por execução;
sem pool/cache compartilhado. Validar autorização e argumento antes de I/O e conferir
chave/projeto na resposta antes de emitir evidência. Ferramenta ADK expõe somente chave
de issue, não endpoint, cloudId, JQL arbitrário ou nome de ferramenta do provedor.

Proibido: escrita, executeRead genérico, discover, outros sites/projetos, Confluence,
TWG, shell/stdio, redirecionamentos, proxies ambientais, conteúdo remoto como instrução,
logs de conteúdo/headers/token/erros brutos, billing, upgrades e fallback de credencial.
Nenhuma chave será montada no worker/API padrão. Sem chamar inferência Gemini nesta
janela UTC, já consumida; não zerar contadores. O probe não conecta o MCP ao workflow
de respostas RAG e não comprova autorização multiusuário em produção.

Menor prova: dois checks offline adversariais consecutivos na mesma revisão congelada;
depois, quando houver token, initialize + tools/list + getJiraIssue real de KAN-1/2,
resposta bounded validada e log apenas booleano/metadado. Falhas preservadas, qualquer
alteração zera a contagem. Oráculo definido antes das entradas aleatórias; mesmo autor,
não auditoria cega independente. Não transformar metadata 200 em leitura real aprovada.

Limites: timeout de conexão 5s, operação 20s, execução total 45s; sem retry automático;
1 MiB por resposta HTTP/SSE (inclui catálogo). Token ausente, revogado, expirado, 401,
403, 429, timeout, schema alterado, erro MCP e resposta inconsistente falham fechados.
Não há promoção automática de evidência para índice, cache ou respostas ao usuário.
Revogação upstream verificada apenas pela chamada fresca: nenhuma garantia de revogação
instantânea após o retorno. Produção, refresh OAuth, circuit breaker durável e fila de
sync são frentes posteriores, não entregas implícitas desta fatia.

Fontes oficiais verificadas nesta execução:
- https://developer.atlassian.com/cloud/rovo-mcp/guides/configuring-authentication-via-api-token/
- https://developer.atlassian.com/cloud/rovo-mcp/guides/supported-tools/
- https://pypi.org/project/mcp/ (2.2.0; dependência isolada da imagem base)

API tokens não são limitados a cloudId; os limites abaixo são da aplicação. Habilitação
por administrador pode ser necessária: não mudar política da organização como fallback
sem aprovação específica no momento da ação.
