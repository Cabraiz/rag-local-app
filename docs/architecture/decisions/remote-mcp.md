# GitHub e Atlassian MCP na arquitetura RAG

Atualização de implementação (01/10/2026): o feed administrativo já usa um gateway MCP privado em container e chamadas aos servidores remotos oficiais. GitHub e Jira passaram na leitura autenticada; o Jira exibe KAN-1, KAN-2 e KAN-3. O token Atlassian foi criado pelo usuário como aplicativo MCP V2, substituindo os anteriores revogados por ele. O fluxo de respostas da Ana ainda não ingere essas fontes e o gateway ainda bloqueia escrita. O texto de 30/09 abaixo é o planejamento histórico, não o estado operacional atual. Veja [runtime e provas atuais](../../operations/remote-feed-runtime.md).

Planejamento de 30/09/2026. Os dois serviços são **remotos, acessados pela internet por HTTPS**. O Python/Google ADK dentro do nosso container atua como cliente. Não será instalado um servidor GitHub/Atlassian local nem usado Filesystem/stdio para substituir essas integrações.

Esta revisão altera somente arquitetura/documentação/Eraser. Não houve conexão, login, autorização de acesso, criação de credenciais, implementação de adapters, chamada de ferramentas reais ou deploy. Os checks abaixo são requisitos; nenhum está aprovado por existir no desenho.

## Serviços e finalidade

| Serviço remoto | Endpoint oficial de referência | Leitura planejada |
| --- | --- | --- |
| GitHub MCP | `https://api.githubcopilot.com/mcp/` | Documentação em repositórios autorizados, issues e PRs. |
| Atlassian MCP v2 | `https://mcp.atlassian.com/v2/mcp` | Páginas Confluence e itens Jira nos sites, espaços e projetos autorizados. |

O [GitHub documenta seu servidor remoto hospedado e autenticação](https://github.com/github/github-mcp-server#remote-github-mcp-server). A [Atlassian documenta OAuth 2.1, endpoint v2 e escopo por site/cloudId](https://support.atlassian.com/atlassian-ai-gateway/docs/configure-oauth-2-1/). Recursos disponíveis dependem da conta e das políticas da organização. Endpoints são configuração fixa aprovada, não argumento do modelo.

## Comunicação e fronteiras

O fluxo de execução é `casos de uso → ADK/DeepAgents → fachada de tools autorizada → ToolPort/MCP gateway → GitHub ou Atlassian → ToolResult → evidência privada → verificação → commit terminal`. No desenho, as setas ADK/DeepAgents para gateway representam invocações mediadas por essa fachada, não imports do adapter concreto no domínio. Os SDKs não têm caminho alternativo até o provedor; o control-worker continua sem LLM/MCP.

O módulo `tools` resolve identidade confiável, autorização, destino e reserva de orçamento. O adapter usa `McpToolset`/Streamable HTTP, adapta os schemas, impõe timeout/limite de resposta e fecha recursos no ciclo de vida. Descoberta não habilita ferramentas automaticamente: nomes/schemas são comparados com um manifest aprovado. O [ADK documenta MCP, filtragem e credenciais por contexto](https://adk.dev/tools-custom/mcp-tools/advanced/); `tool_filter` ajuda a reduzir exposição, mas não substitui autorização em toda invocação.

Resultado esperado: `ToolResult(provider, source_id, version, fetched_at, authorization_context, bounded_content)` ou erro tipado. `authorization_context` é referência opaca, nunca token. Nenhuma sessão MCP/ADK substitui o ledger; falha de conexão não remove o pedido aceito. Gateway não publica resultado factual direto no frontend.

## Autenticação e isolamento

- OAuth autorizado por usuário quando houver delegação interativa. Fluxos não interativos usam apenas mecanismos suportados pelo provedor e identidade/escopo aprovados; não presumir que uma configuração OAuth de IDE funciona automaticamente no worker.
- Credenciais ficam em secret manager/file protegido ou armazenamento cifrado, fora de prompt, documento, log, navegador e session state serializável. A identidade sintética de laboratório não autoriza acesso corporativo. Sem credenciais/aprovação, provider permanece desativado.
- Associação de tenant/ator, conta, repositório ou cloudId vem da aplicação autenticada. Nunca confiar em site, URL, tenant ou scope proposto pelo modelo.
- Conexões/sessões e caches são particionados por provedor, tenant, ator/delegação, identidade da credencial e versão de autorização. Nunca usar um token global com permissão ampla para atender todos os tenants. Cancelamento, revogação e expiração invalidam acesso/cache; renovar uma credencial não amplia seus escopos.
- Revalidar autorização antes da chamada e antes de usar/entregar a evidência. Se não houver prova atual de acesso à fonte, não usar o conteúdo privado nem como citação; expirar/remover evidência em cache por política. ACL de recurso deve ser obtida/checável pelo connector escolhido. Sem mecanismo confiável de propagação de revogação, negar o uso do snapshot/cache privado, não promover um índice que apenas conserva ACL antiga.

## Somente leitura e egress

GitHub: usar modo remoto `X-MCP-Readonly: true`, credencial de menor privilégio e lista mínima de ferramentas/recursos. A [documentação oficial distingue read-only de lockdown](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md): lockdown é filtro best-effort de conteúdo, não fronteira de autorização nem garantia contra prompt injection.

Atlassian: allowlist de operações de busca/leitura, recursos e escopos mínimos suportados pela conta. Não presumir que o header read-only do GitHub existe na Atlassian. O gateway nega cada operação não aprovada, inclusive chamadas diretas que contornem o catálogo de ferramentas. Se a plataforma oferecer política read-only, também aplicá-la; caso os scopes não permitam restringir o suficiente, bloquear a integração até definir um controle adequado. Criar/editar/comentar/transition/merge/delete ficam proibidos nos dois providers.

Egress ocorre somente no backend autorizado, com TLS validado, allowlist de hosts/redirects e discovery OAuth confiável. Não permitir URL arbitrária, redirect para rede privada, token passthrough a outro serviço ou credencial recebida do frontend em argumento de tool. O perfil atual dos workers tem rede interna e não conecta estes MCPs: acesso de saída será implementado e testado numa etapa específica, sem expor PostgreSQL/Qdrant nem montar Docker socket.

Enviar somente a consulta mínima necessária, sob política de classificação/egress; não encaminhar todo o corpus, prompt ou documentos privados ao outro provedor ou ao modelo cloud. Questões/documentos/tool outputs são dados não confiáveis. Não executar instruções contidas em issue, comentário ou página nem aceitar links/HTML/código como autorização de novas ações.

## Online não significa uma chamada externa por pedido

Há dois usos: consulta online pontual e sincronização de fontes autorizadas para o corpus. Sync é job de ingestão com checkpoints, cursor/paginação, dedupe por versão/hash, quota separada e promoção de snapshot após EVAL. A disponibilidade de dados completos/paginação/ACL pelo MCP precisa ser comprovada; se exigir REST complementar, isso será mudança explícita de contrato e autorização, não fallback escondido.

Documentos guardam provider/site/repo/source/version/link/fetched_at e referência de autorização. Qdrant continua derivado, com filtros e rechecagem de revogação. Citações apontam para a fonte/versão efetivamente recuperada, não uma URL fabricada. MCP indisponível pode permitir uso de evidência cacheada somente se autorização e frescor forem comprovados; caso contrário há resposta degradada/abstenção explícita. Não afirmar consulta online atual se foi usado snapshot.

Para 100.000 requests, sincronização e quotas evitam multiplicar chamadas externas cegamente. Cada provedor tem concorrência/conexões/limites próprios, além da quota por tenant e do deadline do pedido; medir rate limits e latência reais. Esses limites não aumentam automaticamente por escalar containers.

## Falhas e observabilidade

Defaults propostos para benchmark, não promessa: connect 5 s, deadline de invocação 20 s ou prazo restante menor; resposta até 1 MiB e contexto separado limitado; no máximo 2 tentativas totais para leituras transitórias, concorrência inicial 2 por provider e quota por tenant. Paginação também consome chamadas/orçamento. Tune após testes; sem slots disponíveis aplicar backpressure, sem filas em memória ilimitadas.

401/token expirado permite uma renovação suportada e limitada; se falhar, erro `AUTH_REQUIRED`, sem fallback para credencial ampla. 403/escopo/recurso negado não é retry automático. 429 respeita Retry-After, teto e deadline; espera fica durável no ledger/job, sem dormir mantendo transação/lease vencida. Timeout/5xx usam retry restrito de leitura e circuit breaker por provider. Resposta malformada/oversize falha fechada. Chamadas tardias, canceladas ou sem fence/prazo atuais não são incorporadas. UNKNOWN de efeito externo não permite retry cego; ferramentas de escrita estão desativadas.

Logs/traces redigidos registram request/invocation ID, provider, nome autorizado da ferramenta, decisão, erro tipado, duração, tentativa, quota, cache/snapshot e circuit state. Não registram OAuth/PAT, corpo de resposta, pergunta, documento, headers ou identidade privada em URLs. Métricas usam provider/tool/result e cardinalidade limitada, sem IDs por pedido/usuário como labels. Medir latência, chamadas, 401/403/429/5xx, timeouts, cache autorizado, bytes e idade dos jobs; logs não podem ser pré-requisito para o commit terminal.

## Checklist obrigatório antes de habilitar

- [ ] Contas/sites/repositórios, finalidades, credenciais e política de egress autorizados.
- [ ] `tools/list` real compatível com manifest; permitir apenas leitura e negar invocação de escrita mesmo fora do catálogo.
- [ ] Isolamento A/B com credenciais/sessões/cache distintos; tokens expirados, scope insuficiente e cloudId/repo errado negados.
- [ ] Cache/corpus deixa de fornecer fonte após ACL revogada; citação válida para source/version autorizados.
- [ ] Prompt injection, redirects/SSRF, schema malformado, resposta excessiva, cancelamento e chamada atrasada bloqueados.
- [ ] Timeout, disconnect, 429/Retry-After e 5xx com limites, deadline, circuit breaker e retry/job durável verificados.
- [ ] Sync reinicia sem perder/dobrar versões; paginação/checkpoints comprovados; indisponibilidade causa degradação/abstenção correta.
- [ ] Logs/traces não contêm segredo/conteúdo privado; métricas com cardinalidade limitada e health separados por provider.
- [ ] Carga/quotas reais dos providers medidas e duas rodadas consecutivas da suite de contrato/integração sem regressão.

Checks de documentação/layout não aprovam este checklist. Aprovação anterior do desenho e testes da fatia 1 não prova conexão MCP, integração ADK real com esses providers ou capacidade de produção. DeepAgents, Vertex/Gemini e os demais gates continuam no escopo.
# Assistente único

Contrato mais recente de consulta/ações: [unified-assistant.md](unified-assistant.md).
MCP conectado para leitura não significa criação/movimentação integrada. Não
ampliar a allowlist de ferramentas apenas porque uma credencial permite escrita.
