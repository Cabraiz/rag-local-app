# Recuperação da supervisão local

## Contrato antes da correção

- Esperado: um leitor temporário de `progress.json` não interrompe o executor;
  stdout fica em arquivo privado durável, e uma retomada usa a mesma sessão,
  branch e worktree somente depois de confirmar que os PIDs anteriores morreram.
- Proibido: recriar chats, iniciar dois writers, apagar mudanças ou comprovantes,
  aprovar merge sem receipt atual, alterar credenciais ou reiniciar o laboratório.
- Menor prova: reproduzir o bloqueio de substituição de JSON no Windows; testar
  retry limitado, falha persistente, saída completa de um subprocesso real mesmo
  com falha de progresso, CAS de retomada e duas rodadas com fontes congeladas.

## Diagnóstico inicial

Runtime e providers encerraram a supervisão com `PermissionError`, exit code
desconhecido e sem receipt final. O canal stdout em pipe foi fechado e o CLI
registrou erro 232. Um leitor mantendo o JSON aberto reproduziu `PermissionError`
na substituição atômica no Windows. A causa exata da exceção original não foi
registrada; o reproducer comprova uma vulnerabilidade do supervisor, não a linha
exata das duas ocorrências históricas.

Esta correção não aprova os cards de produto nem substitui seus gates.
