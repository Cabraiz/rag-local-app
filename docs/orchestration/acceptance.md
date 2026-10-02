# Orquestração autorizada

Pedido: publicar o código do projeto em um repositório GitHub privado, criar chats e worktrees independentes, coordenar a fila e integrar mudanças com cuidado.

## Esperado

- Preservar `D:/RAG-Local` e o laboratório existente; inicializar Git sem mover bases, segredos ou volumes.
- Criar um repositório privado separado do repositório de dados MCP `Cabraiz/rag-mcp-lab`.
- Criar quatro executores visíveis, cada um com worktree e responsabilidade própria, usando `gpt-6.1-sol` e raciocínio `max`.
- Utilizar o modo Fast/priority pedido, sem Ultrafast nem compra de créditos. A configuração não garante um fator exato de velocidade.
- Manter um writer por worktree e somente a Central como writer do journal canônico de cards.
- Distinguir novos bugs de aprovações antigas que exigem revalidação. Não converter bloqueios externos em sucesso.
- Integrar uma frente de cada vez, após revisão do diff, provas atuais e duas rodadas consecutivas dos testes relevantes. Repetir a verificação integrada antes de atualizar a fila.

## Proibido

- Publicar tokens, `.env`, dados dos bancos, pesos, logs brutos, sessões de navegador, imagens de contas ou comprovantes privados.
- Sobrescrever o laboratório com checkout de outro worktree, apagar volumes, usar `reset --hard`, forçar push ou fazer deploy público.
- Reutilizar a mesma instância QA em testes destrutivos concorrentes; os testes pesados compartilham uma trava local e usam projetos isolados.
- Criar execuções duplicadas, fechar cards por estado ativo ou considerar fonte alterada como aprovação vigente.
- Habilitar faturamento, provider pago, fallback pago, Vertex ou envio de dados reais para testes sem a autorização e evidência necessárias.

## Menor prova

Inventário e scan do índice Git sem segredos; visibilidade PRIVATE confirmada no GitHub; SHA inicial igual local/remoto; quatro worktrees listados pelo Git; quatro IDs de chats com modelo/esforço confirmados; manifesto de ownership; receipts que vinculam branch, SHA, testes e fontes; integração sem conflito e duas rodadas com os critérios originais.

O journal atual tem 110 itens incluindo cards e regressões: 8 DONE, 95 NEEDS_FIX, 6 BLOCKED e uma retirada. A expressão “20 cards” não reduz silenciosamente esse escopo.
