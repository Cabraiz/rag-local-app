# Feed MCP real: estado operacional de 01/10/2026

## Entrega e fronteira

Bruno acessa Integrações / Fontes ao vivo em `http://127.0.0.1:8840/#integracoes`.
Ana recebe HTTP 403 nos endpoints do feed e da atualização. A seleção sem senha continua exclusivamente demonstrativa; qualquer pessoa no laboratório pode escolher Bruno. Não é identidade segura para produção.

Comunicação implementada: frontend -> FastAPI autenticada -> gateway MCP privado -> servidores MCP oficiais GitHub/Atlassian. API -> gateway também usa MCP JSON-RPC. As credenciais externas estão montadas somente no gateway, nunca no navegador/API/worker. O gateway não tem porta publicada no host.

- GitHub: `Cabraiz/rag-mcp-lab`, somente `list_pull_requests`, leitura autenticada bem-sucedida, zero PRs na consulta real. Falta comprovar a chegada de um PR novo; não foi criado PR sem autorização específica.
- Jira: Kanban existente `RAG Local Lab`, projeto KAN, board 2; não foi duplicado. Após o usuário criar o token do aplicativo Atlassian MCP V2, `searchJiraIssuesUsingJql` leu KAN-1, KAN-2 e KAN-3. As falhas 401 anteriores estão preservadas nos receipts antigos; não representam o estado atual.
- Token salvo sem exibição no chat; arquivo ativo protegido por ACL do usuário, montado somente no gateway. Expiração confirmada na interface: 08/10/2026, com bloqueio local conservador a partir dessa data em UTC. Metadados privados substituem a data hardcoded antiga. O token foi criado pelo usuário com 25 escopos, incluindo escrita em outros produtos: não é uma credencial de privilégio mínimo. O gateway limita as chamadas a busca/leitura mesmo assim; antes de produção/escrita, reduzir/separar credenciais e implementar autenticação real e autorização por ação.
- O resultado Jira real usa envelope `data` e campos compactos; a data de atualização não veio na resposta. A interface informa sua ausência, sem usar a criação ou o horário da consulta como falsa data de atualização. Status reais, inclusive localizados, são exibidos sem fabricar colunas vazias.
- Não há ingestão automática desses itens na base que Ana consulta. Cards/PRs externos não são tratados como instruções ou políticas aprovadas.
- Nenhuma chamada Gemini, escrita nos provedores, publicação na internet ou alteração de faturamento faz parte desta entrega.

## Atualização e cobertura

Consulta do gateway: 60 segundos após sucesso. A interface lê o snapshot a cada 10 segundos quando a seção está aberta e visível. Botão Atualizar solicita leitura assíncrona; intervalo mínimo de 15 segundos. Não é webhook nem atualização instantânea.

Snapshots persistem no volume `feeddata`, com timestamps, estado e cobertura. Após falha de autenticação, dados antigos não são exibidos. 401/403/expiração pausam a tentativa automática por uma hora; outras falhas usam 120 segundos. A interface marca leitura antiga após 180 segundos.

Paginação limitada a 10 páginas de 50 itens por fonte. O indicador de cobertura parcial sinaliza truncamento; a lista não é um ledger de eventos nem garante histórico ilimitado ou preservação de cada transição entre polls. Não foi realizado teste de carga com 100.000 pedidos nesta entrega.

## Runtime preservado

Projeto Compose `rag-local-v2`, pasta `D:\RAG-Local\app`. Docker Engine supervisiona containers com `restart: unless-stopped`; não dependem do PTY do Codex. Atualização após conexão Gemini: usar sempre os CINCO arquivos; omitir os overlays finais pode remover configurações MCP/Gemini:

```powershell
& 'D:\Docker\App\resources\bin\docker.exe' compose --project-directory 'D:\RAG-Local\app' `
  -f D:\RAG-Local\app\infrastructure\compose\runtime\compose.yaml `
  -f D:\RAG-Local\app\infrastructure\compose\runtime\compose.retrieval.yaml `
  -f D:\RAG-Local\app\infrastructure\compose\runtime\compose.semantic.yaml `
  -f D:\RAG-Local\app\infrastructure\compose\runtime\compose.integrations.yaml `
  -f D:\RAG-Local\app\infrastructure\compose\labs\compose.gemini-rag.yaml ps
```

Gateway: usuário não-root, filesystem read-only, sem capabilities, sem porta publicada, rede interna para API e rede de egress própria. API não monta segredos GitHub/Atlassian. SQLite contém snapshots de dados privados; o disco/volume continua exigindo proteção local. Rede de egress não é firewall externo: destinos são limitados pela aplicação, não por ACL de rede. Produção permanece bloqueada por `RAG_MODE=lab`.

## Provas e pendências

- `eval/runs/remote-feed-20261001/live-061551.json`: duas rodadas de 15 checks de feed/fronteiras; GitHub e Jira aprovados, três cards reais. `complete_online_gate=true` significa autenticação/leitura das fontes, não validação de escrita, PR novo, ingestão ou produção.
- `eval/runs/remote-feed-20261001/boundary-jira-connected-final.log`: duas rodadas de 39 checks determinísticos (destinos, argumentos, proibição explícita de criar/editar/mover cards, envelope, campo ausente e expiração). Não são provas live de PR não vazio.
- `eval/runs/roles-20261001/receipt-061511.json`: duas rodadas de 38 checks de papéis/API/worker local.
- `eval/runs/remote-feed-20261001/jira-connected-desktop.jpg`: interface real com os três cards e duas fontes autenticadas. As imagens anteriores com 401 foram preservadas como histórico.
- Logs de build/recriação: `frontend-final-build.log`, `frontend-final-up.log`, `gateway-build.log`, `update.log`, na mesma pasta do feed.

Essas rodadas são regressões delimitadas do mesmo executor, não auditoria cega independente, certificação de produção ou prova de ausência total de bugs.

Jira destravado. Autorização para novo PR de demonstração ainda aguardada. Antes de afirmar conclusão integral, provar leitura real não vazia de PRs e repetir os gates após qualquer alteração. Escrita/automação continua não implementada e a ingestão automática para Ana também não.

Referências oficiais: [GitHub MCP remoto](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md), [Atlassian API token MCP](https://developer.atlassian.com/cloud/rovo-mcp/guides/configuring-authentication-via-api-token/).
