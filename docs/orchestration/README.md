# Trabalho paralelo

Quatro chats executam frentes independentes. Cada chat tem branch, worktree e allowlist próprios em [workstreams.json](workstreams.json). A Central integra e mantém o journal de cards; os executores nunca fecham a fila compartilhada.

## Execução

O código fica no repositório privado `Cabraiz/rag-local-app`. O repositório privado `Cabraiz/rag-mcp-lab` continua sendo uma fonte de demonstração MCP diferente; não é substituído.

Novos executores usam GPT-6.1 Sol, raciocínio máximo e Fast autorizado. Fast não oferece um fator fixo de 1,5× para esse modelo. Não há compra de créditos, Ultrafast ou alteração de faturamento de providers.

O launcher usa o CLI oficial com sessões persistidas, não sessões efêmeras. Quando o projeto não está na lista de projetos salvos do app, worktrees Git reais e o CLI evitam escolher um projeto diferente. IDs dos chats são conferidos no app após o início.

## Integração segura

1. Snapshot read-only da fila: cada card e seus bugs recebem uma frente, sem writer concorrente no SQLite.
2. Executor lê critérios e testes, reproduz falhas e corrige somente sua allowlist. Provas antigas não substituem testes atuais.
3. Duas rodadas consecutivas com fontes congeladas. Mudança ou nova falha zera a sequência. Ausência de credencial/infrastrutura gera gate pendente, não aprovação.
4. Executor faz commit local e devolve receipt com SHA, diff, comandos, contagens, limites e novos bugs. Não faz push nem merge.
5. Central revê escopo, segurança, diff e dependências antes de integrar uma branch por vez. Conflito exige resolução revisada, nunca escolher tudo de um lado.
6. Central reexecuta gates afetados na árvore integrada, valida os comprovantes e somente então atualiza cards e envia o código ao GitHub. Deploy público e serviços pagos seguem proibidos.

Testes destrutivos de Docker ficam serializados na Central, em projetos QA de ownership confirmado. Builds dos executores não podem sobrescrever tags usadas pelo laboratório ativo. A integração não pode apagar dados ou volumes.

## Supervisão econômica

Um supervisor externo acompanha o processo local e mantém estado privado em `.local/orchestration`. Somente eventos terminais notificam a Central. O watchdog existente detecta stalls reais; não há prompts periódicos solicitando status.

O snapshot, eventos, PIDs, recibos e mensagens finais locais não são publicados no GitHub. Chamada gratuita de Gemini ainda usa cota: somente a Central executa gates online e nunca zera o contador global.
