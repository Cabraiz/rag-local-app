# Navegação e altura da aplicação

Esperado literal: shell ocupa 100% da altura da tela; navegação à esquerda com
Consultar, Documentos, Histórico, Integrações e Como funciona; somente uma área
ativa por vez, com altura disponível inteira e rolagem interna. Trocar área não
perde pergunta, resposta, fontes, identidade ou intenção idempotente. Abrir fonte
leva ao documento; selecionar recibo leva à resposta. Fragmentos permitem voltar
e abrir uma área diretamente. Layout permanece utilizável em largura reduzida.

Jira: adaptador de laboratório separado existe; não está conectado ao workflow
RAG. GitHub: conexão planejada e desativada. A seção Integrações lê estado local
do servidor autenticado, não apresenta recibo antigo como conexão online atual.
Links externos são acessos manuais, não ferramentas ativas do agente.

Proibido: conectar ou ampliar credenciais, expor segredos, chamar serviços pagos,
escrever externamente, excluir histórico, esconder fontes, afirmar MCP ativo
por cadastro ou por HTTP 200. Não retomar fila maior, alterar branch ou publicar.

Prova mínima: status HTTP autenticado; duas rodadas UI na mesma versão com todas
as áreas, viewport/body sem rolagem externa, rolagem interna alcançando conteúdo,
resposta/citação real, navegação fonte/recibo, consulta desconhecida, troca de
empresa e manutenção do contexto. Preservar falhas; são regressões do autor,
não auditoria cega independente. Verificar HTTP e containers ao entregar.
