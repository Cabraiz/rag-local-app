# RAG Local: regras de execução

Leia `docs/orchestration/acceptance.md` e o manifesto da sua lane antes de editar.

- Novos executores deste projeto usam `gpt-6.1-sol`, raciocínio `max` e Fast/priority quando disponível. Não use Ultrafast nem compre créditos.
- Um writer por worktree; cada executor fica na branch e allowlist atribuídas. Não edite `D:/RAG-Local` a partir de um worktree.
- Somente a Central atualiza `.local/card-execution/queue.sqlite3`. Receipts dos executores não fecham o journal por conta própria.
- Não publique `.local`, `.env`, bases, pesos, logs, browser profiles ou evidência privada. Segredos nunca aparecem no chat, diff ou logs.
- Use `apply_patch` para edições. Comandos PowerShell locais usam `tty:true` e `login:false`; subprocessos Node/Python usam `windowsHide`/`CREATE_NO_WINDOW`, `shell:false` e pipes.
- Preserve o laboratório em execução e seus volumes. QA destrutivo exige ownership de projeto e trava local; nunca `docker prune` ou `down -v`.
- Não chame cloud, não altere credenciais e não faça escritas remotas a partir das lanes. A Central executa a validação online autorizada separadamente.
- Commit local da sua mudança autorizada, sem push, PR ou merge por conta própria. A Central revisa, integra, faz push e atualiza os cards.
- Cada mudança reinicia a sequência limpa. Só entregue aprovação de um segmento após duas rodadas consecutivas com fontes congeladas e critérios originais; registre falhas e limites.
- Ao terminar, falhar ou precisar de decisão, entregue um receipt curto com branch/SHA, arquivos, checks, bloqueios e caminho do artefato. O usuário autoriza reportar diretamente à Central desta atividade. Não envie pedidos repetidos de status.
- Não prometa ausência total de bugs, produção pública, 100 mil workflows, HA em outro host ou Vertex gratuito sem a prova específica.
