# Workspace local

URL: http://127.0.0.1:8840/#consultar

## Estado atual — 01/10/2026

Ana tem somente consulta; Bruno publica documentos e acompanha fontes/embeddings.
Sessão sem senha é demonstrativa, não autenticação de produção.
Jira e GitHub têm feeds de leitura via gateway MCP privado. Gemini real participa
da seleção de evidência no worker ADK. A interface indica Gemini consultado e
citação validada, ou evidência insuficiente; erros são abstenções, não respostas
fabricadas. Credenciais não entram no navegador. Integrações não são ingeridas
automaticamente na base e não executam ações de escrita.

Runtime vigente usa CINCO arquivos Compose, incluindo integrations e gemini-rag;
instruções em `../docs/architecture/gemini-rag-runtime.md`.

## Descrição e limites históricos da primeira interface

Shell de altura `100dvh`, sidebar persistente e uma view ativa ocupando o espaço
restante abaixo do cabeçalho. Body não rola; conteúdo longo rola na view ou nos
painéis de consulta. Em larguras menores, as colunas viram uma coluna interna.

Rotas de fragmento: `consultar`, `documentos`, `historico`, `integracoes`,
`funcionamento`. Navegar preserva o DOM e contexto da empresa; trocar a empresa
limpa os dados anteriores e mantém a área selecionada. Abrir citação navega ao
documento; abrir resultado do histórico navega à consulta. JWT fica só em memória.
Intenção idempotente persiste somente chave e hash da pergunta, nunca o texto.

Integrações é um workspace limitado à altura disponível: abas acessíveis por
setas/Home/End alternam Conectores e Fluxo previsto. Cards mantêm cabeçalho e
ações manuais fixos, com disclosure de pendências e rolagem interna dos textos.
Em larguras até 860 px, a lista de cards rola dentro da área restante. Nenhuma
informação é recortada sem acesso; alternar abas não testa/ativa provedores.

`GET /v1/lab/integrations` exige a identidade do laboratório e informa somente
ligação local dos adaptadores; não faz chamadas externas nem lê segredos.
Jira tem probe isolado, não ligado ao workflow; GitHub não tem adaptador executável
nesta versão. Os links são manuais, não activação de MCP. Não apresentar sessão
do navegador, conector do Codex ou conta criada como integração do backend.

Runtime: Compose `rag-local-v2` em D:\RAG-Local\app, perfis base + retrieval +
semantic, sem profiles pagos/online. Manter Docker em execução; a tarefa Windows
`rag-local-demo` permanece ausente porque a criação anterior foi bloqueada.
