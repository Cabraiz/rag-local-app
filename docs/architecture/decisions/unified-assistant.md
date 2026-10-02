# Assistente único: conhecimento + ferramentas + ações

Contrato: `unified-assistant-v1`, 01/10/2026. Uma entrada de conversa não significa
uma única permissão nem um único tipo de armazenamento.

## Fluxo aprovado

1. Backend estabelece identidade/empresa/capacidades. O orquestrador interpreta a
   mensagem, separa intenções e valida um plano estruturado. Texto não autoriza
   ferramentas. O modelo nunca escolhe livremente o tenant, endpoint ou segredo.
2. Conhecimento -> recuperação com ACL, evidência e citações. Dados atuais ou
   listagens completas -> API/MCP/catálogo estruturado; não depender só de top-k.
3. Criar/mover -> descoberta autenticada de destinos, metadados e estado pelo
   gateway de ações. Só opções permitidas são apresentadas. Mais de um Kanban sem
   escolha: perguntar **Em qual Kanban?**; um único destino: mostrar no plano.
4. Coletar apenas dados ausentes. Criação valida projeto, tipo e campos obrigatórios
   atuais do Jira. Movimentação resolve card e transições reais; coluna do board
   não equivale automaticamente a uma transição. Validar filtros/visibilidade.
5. Mostrar plano para confirmação do solicitante, não fila de revisão humana:
   ação, destino, título/card e campos/estado de origem e destino. Confirmação não
   altera permissões. Cancelar deve ser possível.
6. Revalidar ACL/estado/schema e executar com confirmação expirada/hash/ator/tenant
   vinculados; persistir pedido, intenção e tentativas antes do envio. Concluir
   somente com recibo ID/link e estado externo verificado.
7. Timeout após envio -> resultado desconhecido + reconciliação. Ledger local e
   idempotência não garantem exactly-once em provedor que não suporte isso. Não
   fazer retry cego de criação. Auditoria correlaciona request/conversation/action,
   tenant/ator, política, ferramenta, status e duração; nunca token/texto bruto.

## Exemplos de conversa (contrato, não demonstração já operacional)

- Usuário: “Crie um card no kanban”. Assistente: “Em qual Kanban?” e opções reais
  permitidas. Depois coleta título/campos e mostra confirmação; só então ferramenta.
- Usuário: “Mova o KAN-1”. Assistente resolve o card e pede o destino entre transições
  válidas se ainda não informado. Não adivinha ID de transição ou status.
- Usuário: “Quero todos os preços dos alimentos”. Assistente identifica catálogo e
  período, apresenta valores com moeda/unidade/data e fonte. Sem catálogo ou cobertura
  completa, informa ausência/resultado parcial. Teto de reembolso não é preço.
- Usuário: “Como criar um card?”. Consulta explicativa, nenhuma escrita.

## Componentes e comunicação

Frontend conversa -> FastAPI -> orquestrador Python/ADK -> políticas/ports ->
RAG de conhecimento OU gateway MCP/API de leitura/ações -> resultado verificado.
Retorno de esclarecimento inclui opções e referência à intenção pendente. Resposta
do usuário é vinculada à mesma intenção, não um novo pedido solto. PostgreSQL
persiste conversa, slots, confirmação e action ledger; Qdrant indexa conhecimento.
Não há necessidade de um banco vetorial por input ou por intenção. Tenant/ACL devem
continuar sendo filtros impostos pelo backend em qualquer coleção apropriada.

Ana continua cliente. No alvo, pode consultar e executar somente as ações que sua
identidade tiver explicitamente; não administrar fontes. Bruno administra ingestão,
fontes e conexões; seu papel não implica consulta ou escrita Jira. Estas capacidades
precisam ser cadastradas separadamente, não inferidas da existência de um token.

## Estado real desta entrega

- `assistant_policy.py`: instrução versionada para o futuro agente conversacional;
  planejador puro testável que coleta opções/campos e termina em `PLAN_ONLY`, sem
  executor. Seus objetos de descoberta/capacidades são entradas internas confiáveis,
  não schema de payload do usuário. Ainda não conectado a um LlmAgent/tool ADK.
- `adk_workflow.py`: proteção **ativa** para exemplos explícitos PT-BR de criar/mover
  e listar todos os preços. Retorna abstenção honesta antes de recuperar documentos.
  É guarda determinística delimitada, não classificação geral de linguagem natural.
- MCP real Jira/GitHub permanece leitura; as credenciais, o gateway e a matriz
  Ana/Bruno não foram ampliados. O RAG atual continua extrativo, sem Gemini na resposta.
- Conversa persistente, opções descobertas para ações, confirmação e executor de
  escrita ainda faltam; o texto de política sozinho não habilita automação.
- Login sem senha é apenas demonstração local. Autenticação real, autorização
  por ação e credenciais separadas com mínimo privilégio bloqueiam escrita de produção.

## Gates da próxima implementação

Obrigatórios: identidade real; ACL e isolamento por ator/tenant; descoberta real de
boards/tipos/campos/transições; slots persistentes; confirmação vinculada e expirável;
reauth antes de escrita; cancelamento; replay sem duplicação; reconciliação de timeout;
mudança de permissões/estado; prompt injection em documentos/cards; resultados mistos;
paginação e cobertura de catálogo; evidências de create/move reais no laboratório
autorizado. Nenhum gate de escrita pode ser satisfeito apenas por mocks ou esse plano.

Aceitação delimitada atual: `unified-assistant-acceptance.md` e testes
`app/tests/assistant_policy_checks.py`, `app/tests/assistant_policy_smoke.py`.

## Provas e runtime atualizado

Em 01/10/2026: duas rodadas de 42 checks determinísticos (planejador/guarda ADK)
e duas de 29 checks HTTP contra API/worker reais. Fonte congelada por SHA-256 em
cada suíte. Receipts: `eval/runs/unified-assistant-20261001/deterministic.log` e
`eval/runs/unified-assistant-20261001/live-063112.json`. A revisão corrigiu a forma
“eu quero que você crie”, IDs diretos de card, descoberta duplicada/metadados vazios
e um falso positivo de pergunta explicativa contendo “todos os preços”. Estas
rodadas não testam execução remota nem uma LLM conversacional.

Worker reconstruído e atualizado, preservando API/frontend, dados, fontes e
containers auxiliares. Runtime `rag-local-v2`, diretório `D:\RAG-Local\app`,
endpoint `http://127.0.0.1:8840/`. Supervisor Docker externo, restart
`unless-stopped`; nenhum processo durável depende do terminal Codex. Comando:
os quatro arquivos Compose de `remote-feed-runtime.md`, `build worker` e
`up -d --no-deps worker`. Logs em `eval/runs/unified-assistant-20261001/worker-build.log`
e `worker-up.log`; logs runtime via `docker logs rag-local-v2-worker-1`.
Não remover containers auxiliares classificados como orphan pelo Compose.

Referências verificadas: [Function tools no ADK](https://adk.dev/tools-custom/function-tools/)
e [ferramentas Atlassian MCP](https://developer.atlassian.com/cloud/rovo-mcp/guides/supported-tools/).
