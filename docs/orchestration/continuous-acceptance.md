# Reposição contínua dos executores

Contrato adicional autorizado pelo usuário em 02/10/2026, na tarefa
“Verificar chat avaliador RAG”, e confirmado pela Central na mensagem humana
“pois ponha ordem com isso e faça valer”. Não amplia o produto ou autoriza custos.

## Resultado esperado

A Central continua sendo o único integrador e writer do journal de cards. Uma
fila SQLite separada registra tarefas autorizadas, dependências e recursos.
Um dispatcher local persistente mantém até quatro executores existentes ativos
quando há trabalho realmente READY, sem perguntar periodicamente ao modelo.
Cada término congela a entrega; o sucessor independente pode começar enquanto
a Central revisa outra entrega. Encerrar um turno não equivale a verificar um card.

Estados: READY, EXECUTING, WAITING_REVIEW, INTEGRATING, VERIFIED, REWORK e
BLOCKED. Rework conserva a tarefa, tentativas, entrega anterior e executor.
Somente a Central promove uma entrega para INTEGRATING/VERIFIED ou libera rework.
Há no máximo uma integração e seis entregas pendentes; o limite é configurável
explicitamente, não removido para disfarçar um backlog.

Os quatro chats, worktrees, branches e configurações atuais são preservados.
Tarefas writer não podem reutilizar código aguardando revisão naquele worktree.
Tarefas somente leitura podem continuar no mesmo executor, com sandbox read-only,
entrega copiada para um snapshot privado e verificação dos hashes das fontes.
Sem tarefa elegível, registrar a razão exata; não inventar trabalho.

## Comportamento proibido

- Dois writers no mesmo worktree ou retomar um executor ainda vivo.
- Interpretar lease vencido, processo morto ou callback como aprovação.
- Reenviar automaticamente uma execução cujo despacho ficou incerto.
- Alterar código congelado, dependências, modelo/esforço/Fast ou testes para passar.
- Criar chats substitutos, pagar providers, modificar credenciais, publicar dados
  privados ou fazer deploy público. O dispatcher não executa Git merge/push.
- Atualizar o journal canônico automaticamente ou declarar blockers solucionados.

## Menor prova

Com A em revisão serial, B termina e C já estava READY e independente: C inicia
uma vez em até 30 segundos do evento terminal persistido de B, sem novo turno
de planejamento da Central. Registrar timestamps, tarefa, tentativa, executor,
lease, elegibilidade e hashes de B antes/depois. Exercitar primeiro isoladamente,
depois com tarefas reais autorizadas; identificar claramente cada tipo de prova.

Duas rodadas consecutivas congeladas devem cobrir duplicata, callback incerto,
supervisor morto com executor vivo, ausência de trabalho, dependência pendente,
limite de revisão/liberação, rework no executor original e recuperação do serviço.
Toda mudança de fonte reinicia a sequência. Incluir concorrência de dispatchers,
crash entre reserva/spawn, recibo ausente/malformado, branch divergente e fontes
alteradas. Guardar falhas; mesma autoria não é auditoria cega independente.

O status deve separar tarefa atual/próxima, PID, heartbeat, último evento
concluído, último avanço material, revisão e motivo de ociosidade. O processo
persistente roda no Task Scheduler e é registrado no watchdog existente. Medir
liveness não consome modelo; executar tarefas reais e callbacks pode consumir
a franquia Codex já autorizada. Não garante ausência universal de bugs ou HA.
