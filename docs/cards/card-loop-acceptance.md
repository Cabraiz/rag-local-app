# Retomada da fila — 01/10/2026

Pedido: executar um card elegível, revisar adversarialmente, registrar cada bug
reproduzível no fim da fila, seguir para o próximo e retestar após correção.
Não duplicar bugs, não tratar pausa humana como falha, não limpar comprovantes.

Esperado: um writer; FIFO entre cards elegíveis; impedimento real registrado;
conclusão somente com critérios completos, fontes congeladas e duas rodadas
consecutivas. Mudança de fonte invalida a aprovação correspondente. Revisão
do mesmo executor é regressão adversarial, não auditoria cega independente.

Proibido: aumentar escopos/tokens, habilitar faturamento/Vertex, escrever no
Jira/GitHub, parar o runtime local para satisfazer fixtures legadas, marcar
integração de feed como ingestão RAG, inventar provas de 100.000 pedidos.
R$0 continua obrigatório. Este pedido não autoriza novos serviços pagos.

Primeiro verificar bloqueios desatualizados de RAG-01/02/03 com testes novos.
Menor prova: leitura autenticada real das duas fontes, limites/negações de acesso,
inferência Gemini no workflow, contador preservado e critérios individuais.
RAG-04 exige providers NO workflow de respostas: feed administrativo não basta.

Varredura da fila: um bug retirado explicitamente pelo usuário deve permanecer
no histórico, mas não impedir retomada/conclusão do pai como se fosse bug aberto.
Reproduzir isso em SQLite temporário antes de modificar a fila real.
Estado final não é 100% enquanto houver cards bloqueados ou gates sem execução.

## Checkpoint desta retomada

- RAG-01/02/03: novas provas reais em duas rodadas; bloqueios antigos removidos.
  Receipts individuais em remote-feed-20261001 e gemini-rag-20261001 no eval.
- BUG-040: registrado por reprodução adversarial, corrigido somente quando chegou
  à fronteira elegível. Duas rodadas de 18 checks de SQLite. Retirada por pausa
  é preservada e contabilizada separadamente, não como card implementado.
- BUG-033/020/016/018/028/030/038/029: revalidados após invalidar fontes antigas;
  novos receipts, sem chamadas de modelo nos testes locais e sem parar a aplicação.
- Revisão final do journal não encontrou aprovações com SHA vencido. Não significa
  que os demais segmentos passaram. Estado: 23 DONE, 1 retirado, 40 itens totais.
- Pendente decisão: criar PR fictício no GitHub para prova não vazia/novo PR.
  Nenhuma branch/PR/escrita externa foi criada nesta retomada.
- As fixtures legadas que exigem propriedade exclusiva da stack não foram
  executadas contra o runtime ativo: isso impediria preservar a aplicação ligada.
  Precisam de ambiente QA isolado e contratos de login atuais antes de retestar
  ingestão/semântica/restart/retencão completos. Não converter testes offline
  de controle em prova desses segmentos nem alterar critérios para fechar cards.
- RAG-04 continua sem MCP no workflow de respostas: feeds administrativos e
  seleção Gemini não equivalem à ingestão/recuperação dessas fontes.

Supervisão existente conferida pela skill codex-orchestrator-watchdog. Nenhum
novo executor/automação recorrente foi criado; este checkpoint permite retomar
na mesma fila local. Não prometer processamento automático após o fim do turno.
