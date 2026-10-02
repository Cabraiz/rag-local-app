# Token substituto MCP — contrato de aceitação

Usuário confirmou no momento da ação a criação de um substituto para o token
Atlassian do laboratório, com os mesmos três escopos e validade. A autorização
genérica para ações sem dinheiro não permite ampliar acesso, apagar tokens,
alterar outras organizações ou aceitar novos termos automaticamente.

Esperado: nome `RAG Local Lab MCP Read Only Replacement`, Atlassian MCP V2,
somente `read:jira:agent-interface`, `search:jira:agent-interface`, `read:me`,
expiração 07/10/2026. Persistir privadamente em arquivo novo com criação exclusiva,
ACL NTFS apenas usuário atual; preservar token e arquivo anteriores.

Proibido: exibir token em saída/AX/screenshot/log/prompt; mudar escopos, billing,
limites Gemini, outra organização, IP allowlist ou autenticação enterprise.
Não revogar o token anterior sem nova confirmação específica.

Menor prova: UI final confirma nome, scopes e expiry antes de criar; lista após
fechar campo confirma criação sem segredo; arquivo privado novo e ACL conferidos.
Probe isolado mantém endpoint, cloudId e KAN-1/2 fixos. Qualquer erro MCP, schema,
auth ou timeout reprova leitura, ainda que initialize/HTTP 200 tenham passado.
Configuração alterada zera sequência: dois controles offline na revisão final
e, separadamente, duas leituras remotas completas consecutivas. Falhas preservadas.
Não declarar projeto completo, produção ou auditoria independente a partir disso.
