# Operar a fila contínua

A fila de execução é privada em `.local/orchestration/continuous-v2/execution.sqlite3`.
Não substitui o journal dos cards nem fecha cards automaticamente. Seu contrato está
em [continuous-acceptance.md](continuous-acceptance.md).

O controlador fica em `app/tools/orchestration/continuous/control.py`. Usar o Python
do projeto, `-B`, `--database <caminho>` antes do subcomando; escritas da Central
também exigem `--owner <thread da Central>`.

| Comando | Efeito |
| --- | --- |
| `init --root <repo> --previous <lote anterior>` | Vincula os quatro chats/worktrees existentes, após verificar término e PIDs mortos. |
| `adopt --lane <frente> --terminal-sha <hash>` | Congela uma entrega anterior em WAITING_REVIEW, sem aprová-la. |
| `submit --spec <json>` | Autoriza uma tarefa concreta e vincula fontes atuais, dependências e recursos. |
| `status` | Mostra tarefa atual/próxima, estados e razões de ociosidade. |
| `review --task <id> --action integrating/verified/rework --proof <evidência>` | Revisão exclusiva da Central; uma integração por vez. |
| `release-rework --task <id> --prompt-file <texto>` | Libera nova tentativa no mesmo executor, conservando histórico. |
| `recover-uncertain --run <id> --proof <evidência>` | Decisão explícita da Central após PIDs mortos e inventário sem CLI sobrevivente; libera REWORK, não replay. |
| `reconcile-adoption --task <id> --adopted-run <id> --proof <evidência>` | Reassocia exclusivamente uma entrega importada comprovada após a corrida legada; exige fontes/receipt intactos, tentativas terminais, nenhum lease ou processo vivo. Preserva tentativas e volta a WAITING_REVIEW, sem aprovação. |
| `serve` | Dispatcher determinístico supervisionado pelo Task Scheduler. |

Uma spec contém `id`, `lane`, `mode` (`read_only` ou `writer`), `card_ids`,
`prompt`, `dependencies` e `resources`; writer também exige `write_prefixes`
contidos na allowlist da frente. O controlador acrescenta manifest/base Git.
Dependências exigem VERIFIED, não apenas término. IDs repetidos não redisparam.
`resources` reserva exclusivamente recursos realmente compartilhados, como QA pesado.

Tarefa concluída gera receipt privado e cópia das fontes em `frozen/<run>`;
somente leitura usa sandbox read-only. Uma tarefa writer só reutiliza o worktree
quando não há entrega congelada pendente nele. Somente leitura pode continuar
se as fontes e snapshots permanecem idênticos. A revisão precisa verificar as
provas do segmento; VERIFIED nesta fila não promove o card canônico.

Executar `install.ps1 -Database <db>` registra um processo oculto no Task Scheduler.
O JSON `status.json` é a projeção legível; `progress.json` alimenta o watchdog.
Progresso material é separado de heartbeat; o detector não chama modelo.
Os quatro chats podem legitimamente ficar ociosos: dependência, fontes alteradas,
recursos, limite de revisão ou falta de trabalho READY autorizado.

Se o despacho/processo ficar incerto, o lease permanece. Não apagar banco, remover
lock, reenviar prompt ou encerrar PID desconhecido: a Central deve verificar o
processo/receipt e decidir a recuperação exata. Callback incerto não é repetido.
Ausência de receipt válido é BLOCKED, nunca sucesso presumido.
No Windows, launcher/redirector e supervisor Python têm PIDs separados. A fila
registra ambos; o processo interno registra seu próprio PID antes de criar o CLI.

A adoção mantém a trava de reserva durante o freeze e publica tarefa, tentativa
e evento em uma única transação, diretamente em WAITING_REVIEW. Nunca publica
READY. Uma falha preserva eventuais arquivos parciais privados, sem publicar uma
tarefa executável. A repetição verifica o snapshot existente, sem copiá-lo novamente.
O término exige que tentativa, tarefa EXECUTING e lease ainda tenham o mesmo ID.

A transferência técnica explícita em `technical-owner.json` permite ao único
integrador ACTIVE operar esta fila no root canônico com seu próprio ID. Ela
exige o binding original da Central, revogação do writer anterior e caminho
canônico do banco; não transfere a posse do journal de cards nem autoriza Git.

Entregas BLOCKED ou REWORK com snapshot continuam congeladas e consomem a
capacidade de revisão. Um sucessor writer aguarda sua liberação; o rework
explicitamente liberado pode editar sua própria tarefa. As mesmas barreiras
existem como triggers SQLite, incluindo para um dispatcher já carregado, sem
interromper processos. O serviço existente continua com seu código em memória;
novos supervisores e comandos usam as fontes atuais, e os triggers impedem
reservas incompatíveis do dispatcher anterior. Não reinicie executores vivos.
Snapshots reutilizados continuam conferindo `expected`; caminhos privados são
recusados em qualquer nível; resultados rejeitam campos extras e chaves repetidas.
UUIDs equivalentes não criam dois executores, e recovery exige a mesma tentativa
na tarefa e na lease, preservando uma execução mais nova.
O manifesto também recusa nomes privados no destino resolvido e ancestrais
symlink/junction, antes de calcular hashes. Uma folha regular abaixo de um alias
não pode encaminhar arquivos de `.local`, `.git` ou `.env*` ao congelamento.
