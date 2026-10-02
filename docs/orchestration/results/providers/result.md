# Frente providers — revalidação offline de 02/10/2026

Branch `codex/rag-providers`, base `3dba8c0f557421cd3f6d5bc26017278a95a53dbb`.
Escopo: os 18 cards do snapshot original da lane. A Central continua responsável
por integração e journal. O SHA final, hashes das fontes e resultados executados
ficam no receipt privado `.local/orchestration/worker-receipt.json`.

As correções fecham falhas reproduzidas nos contratos offline. O resultado de
entrega é **OFFLINE_COMPLETE_COMMIT_BLOCKED_PENDING_GATES** quando as duas rodadas
finais do receipt passam. Isso não fecha os cards que exigem provider, Docker ou SDK real.
Nenhuma chamada cloud, leitura de credencial, instalação no Python compartilhado,
alteração de container, push ou merge foi realizada pela lane. Vertex permanece
proibido e RAG-14 bloqueado.

O commit local solicitado foi impedido: `git add` retornou 128 ao criar
`D:/RAG-Local/.git/worktrees/providers/index.lock`, com `Permission denied`.
A leitura das ACLs confirmou regras explícitas de negação de escrita no diretório
de metadados. O executor não alterou essas permissões. Os treze arquivos da
allowlist permanecem no mesmo worktree e branch, com hashes e patch privados para
revisão da Central; HEAD continua na base. O receipt distingue a conclusão
offline do commit não realizado, que depende da Central/ambiente autorizado.

## Correções e reproduções novas para a Central

| Referência local, sem ID de journal | Reprodução preservada | Correção |
| --- | --- | --- |
| providers-new-01, RAG-02 | Chaves `answerable`/`chunk_id` repetidas, inclusive escapadas, eram aceitas; o parser também aceitava saída acima do limite | JSON único e limite de 4.096 bytes no parser, antes de criar citação; entradas válidas continuam usando a fonte canônica |
| providers-new-02, RAG-02 | Contador SQLite negativo permitia reserva; valor textual gerava `TypeError` | Estado inválido recebe `INVALID_DAILY_COUNTER`, sem alterar contador ou inserir nova reserva; o teto válido de 1.000 mantém as rejeições originais |
| providers-new-03, RAG-01/RAG-03/RAG-04 | RPC com `method`/nome de ferramenta duplicado era encaminhado; estruturas inválidas produziam erros sem tipo de domínio | Parser compartilhado recusa ambiguidade; transportes e autorização conferem tipos antes de I/O; sete casos negativos por transporte, com zero encaminhamentos |
| providers-new-04, RAG-02 | Validador de persistência buscava `app/src/rag_app/gemini_*.py`, ausentes após reorganização | Hashes atuais usam `app/src/rag_app/models/`, iguais aos caminhos emitidos pelos workers |
| providers-new-05, RAG-03/RAG-04 | `feed_smoke.hashes()` gerava `FileNotFoundError`; o freeze GitHub dependia de acceptance histórico privado não versionado | Manifestos usam fontes físicas atuais e contrato versionado, sem depender da existência de comprovante antigo |
| providers-new-06, RAG-07/BUG-122 | Receipt do teste de relatório apontava para três caminhos inexistentes em `app/advanced/` | Mantém subdiretórios reais de checks, adaptador e parser no vínculo SHA-256 |
| providers-new-07, RAG-02 | Segunda rodada do teste original de concorrência falhou com `database is locked`; uma trava temporária de 2,4 segundos reproduziu o erro | Espera SQLite limitada a 5 segundos compartilhada pelo probe, reserva RAG e conclusão; busy/locked recebem rejeição tipada, sem retry de provider |
| providers-new-08, RAG-07/RAG-08 | Cancelamento/deadline durante a reserva diária ainda permitiam ler a chave e realizar uma chamada ao modelo simulado | Rechecagem após reserva e antes de construir cliente; mantém reserva conservadora, zero leitura de chave/chamada depois da interrupção |

São oito grupos de falhas reproduzidas, não uma contagem de todos os casos
negativos ou dos 95 itens antigos de revalidação. A sequência inicial também
registrou falhas mecânicas do runner ao bloquear o socket privado do asyncio no
Windows; esses registros são classificados como harness, não como bugs do produto.
A interrupção do supervisor preservou alterações e evidências; nenhuma rodada
anterior à última escrita é usada como aprovação final.
O primeiro `final-offline` falhou no teste original do SQLite e foi preservado;
`final-offline-verified` confirmou a correção do código; somente
`final-offline-delivery`, após corrigir a contagem documental de 19 para os 18
IDs reais do snapshot, fornece a sequência final vinculada à entrega.
A espera do contador aumentou de 2 para 5 segundos para contenção transitória,
com rejeição tipada após o limite; isso aumenta a latência possível da reserva,
sem ampliar teto diário, billing, chamadas de modelo ou retries remotos.

## Critérios examinados em todos os cards

| Card | Critérios originais e verificação offline | Limite para conclusão do card |
| --- | --- | --- |
| RAG-01 | `KAN-1`, `KAN-2`, `authorized_read_only`: SDK MCP/ADK com HTTP sintético, cloud/issue fixados, negativas antes de I/O e revogação antes da entrega | Leitura real autenticada de KAN-1/KAN-2 pendente |
| RAG-02 | `real_inference`, `no_billing`, `persistent_daily_limit`: construção real ADK/GenAI sem rede; seleção canônica, falhas seguras, SQLite temporário concorrente, limites, cache e rejeição de provas inválidas | Inferência real, billing atual e persistência física dos workers pendentes |
| RAG-03 | `repository_allowlist`, `real_read`, `negative_authorization`: repositório/ref/arquivo fixos, handshake MCP sintético, SSRF, redirects, quotas, respostas e escrita recusada | Provider GitHub real e limites de papéis na API implantada pendentes |
| RAG-04 | `provider_workflow`, `evidence_gate`, `authorization`, `bounded_execution`: snapshot atual tipado, publicação limitada, dados não confiáveis, expiração e preservação de casos aprovados após falha | MCP → corpus → ADK/Gemini real e revogação por expiração pendentes |
| RAG-07 | `real_task`, `restricted_tools`, `checkpoint`, `cancellation`, `budgets`: contrato declarado de ferramentas, parser real e definições exatas de Budget/invoke, sem executar os SDKs opcionais | SDK DeepAgents, ferramentas efetivamente apresentadas, checkpoint e cancelamento de tarefa real pendentes |
| RAG-08 | `versioned_dataset`, `holdout`, `calibrated_judge`, `deterministic_tests`: dataset autoral v1 intacto, calibragem/holdout separados, rótulos protegidos, judge explicitamente Free e retries limitados | SDK DeepEval e calibragem observada com modelo real pendentes; dataset pequeno autoral não é certificação geral |
| RAG-14 | `approved_identity_region`, `real_vertex_call`, `cost_authorization`: política conferida | BLOCKED: Vertex/custo não autorizado; nenhuma chamada nem identidade inferida |
| BUG-061 | `reproduction`, `two_regression_rounds`: guard se abstém da injeção sem chamar seletor; smoke continua exigindo inferência para a pergunta normal | Contrato offline preservado; smoke remoto de RAG-02 pendente |
| BUG-062 | `reproduction`, `two_regression_rounds`: pin explícito pip-tools 7.5.2/pip 25.1.1 preservado e examinado | Compilador real em ambiente isolado pendente |
| BUG-063 | `reproduction`, `two_regression_rounds`: lock separado fixa DeepEval 4.2.7/click 8.3.3; nada foi instalado no ADK compartilhado | `pip check` e imports reais na imagem isolada pendentes |
| BUG-087 | `reproduction`, `two_regression_rounds`: setuptools 84.0.0 com hashes e `--allow-unsafe`/`--require-hashes` preservados | Build da imagem e instalação limpa com hashes pendentes |
| BUG-117 | `reproduction`, `two_regression_rounds`: allowlist/exclusões declaradas preservadas; isso não prova ferramentas apresentadas pelo DeepAgents instalado | Enumeração e bloqueio do SDK real antes de inferência pendentes |
| BUG-118 | `reproduction`, `two_regression_rounds`: suite original de publicação executada; timestamp nulo é rejeitado de forma tipada | Correção anterior preservada, sem nova falha offline |
| BUG-119 | `reproduction`, `two_regression_rounds`: suite original preserva tentativa parcial e caso aprovado após falha posterior | Correção anterior preservada; prova sintética não fecha RAG-04 |
| BUG-120 | `reproduction`, `two_regression_rounds`: 14 controles originais executam definições exatas de Budget/invoke, com respostas simuladas; chamadas/reservas/fechamentos, transient, cancelamento e deadlines verificados | Imports opcionais e integração SDK real pendentes; controle extraído não é execução DeepEval |
| BUG-122 | `reproduction`, `two_regression_rounds`: JSON/schema/IDs/leitura autorizada do parser atual revalidados, com fontes corretas no receipt | O DONE antigo não é reescrito; adaptador DeepAgents completo precisa do SDK isolado |
| BUG-123 | `reproduction`, `two_regression_rounds`: billing ausente/velho/futuro/outro projeto, booleanos incorretos e scaffold simulado recusados; entrypoint CLI exercitado em processo isolado de teste, sem subprocesso remoto | Não certifica billing atual; observar Console antes da próxima inferência |
| BUG-125 | `reproduction`, `two_regression_rounds`: validador usa fontes atuais; snapshots sintéticos inválidos e fixture impedem aprovação; contador real de aplicação testado em SQLite temporário | Reinício físico dos dois workers/volume/imagem atuais pendente; nunca resetar uso |

## Verificação reproduzível

No worktree da lane, usando exclusivamente o Python autorizado:

```powershell
& D:/RAG-Local/adk/.venv/Scripts/python.exe -B app/tests/provider/providers_offline_suite.py --phase final-offline-delivery
```

O runner cria somente `.local/orchestration/final-offline-delivery`, requer nome novo por
execução, congela fontes atuais e roda duas rodadas sequenciais de dez suítes.
Cada processo de teste usa ambiente sem credenciais herdadas, bytecode desativado,
subprocesso oculto, arquivos temporários privados e bloqueio de rede/providers,
novos subprocessos e bases externas. O único socket permitido é o par interno
criado pela função padrão do asyncio no Windows.

As suítes originais de Gemini, SDK/ADK, GitHub, Atlassian, feed e publicação MCP
continuam com suas assertions. Os novos testes cobrem usuário malicioso, erro de
desenvolvimento/estrutura e abuso de limites. Budget/invoke e guards avançados
são executados a partir de suas definições AST atuais para contornar somente
imports opcionais indisponíveis; não há SDK falso nem inferência simulada tratada
como real. Fontes, dataset, lock e resultados estão vinculados por SHA-256.
Os testes são do mesmo autor/executor e não constituem auditoria independente.

## Gates exatos pendentes da Central

Executar depois da integração, sob ownership/trava da Central, mantendo fontes
e imagens congeladas. Os comandos abaixo descrevem os entrypoints existentes;
a lane não os executou. Nenhum gate deve ser selado por estado ativo, HTTP 200,
tag de imagem ou comprovante antigo. Nova alteração reinicia as duas rodadas.

1. **Dependências/SDKs isolados — BUG-062/063/087 e pré-requisito RAG-07/08.**
   Build da imagem advanced a partir do SHA integrado, instalação com hashes,
   `pip check`, `dependency_checks` e compilador pip-tools/pip fixado, duas vezes:
   `app/advanced/dependencies/verify-dependencies.ps1 -Evidence <PASTA_PRIVADA_NOVA>`.
   Essa ferramenta não compila a imagem; o build é pré-requisito separado.
   Ela usa o laboratório canônico e só pode ser acionada pela Central.
   Comparar bytes incorporados com fontes atuais e usar ID imutável da imagem.
   Rodar `retry_checks` e `task_report_checks` na imagem sem rede. Isso verifica
   os contratos originais com os imports reais, sem aprovar as tarefas online.

2. **Superfície real de ferramentas — BUG-117.** Na imagem isolada sem rede,
   enumerar o `create_deep_agent` configurado exatamente por `task_lab.py`, com
   orçamento zero. Aceitar somente `lookup_evidence`/`write_todos`; execução,
   filesystem, rede livre e subagentes devem falhar antes de qualquer efeito.
   `tool_surface_diagnostic.py` é diagnóstico histórico e registra outro nome
   de harness; não usar sua mera saída como aprovação do adaptador atual.

3. **Billing/Free atual — RAG-02/07/08 e BUG-123.** Observar AI Studio Free e
   Console Cloud autenticados para o projeto autorizado, antes de cada lote,
   sem alterar contas, plano ou credenciais. Gerar observação privada com os
   campos de `validate_billing_proof`, timestamp consciente de fuso e janela
   máxima de 30 minutos. O receipt não é um hard cap monetário.

4. **Persistência física — BUG-125 e RAG-02.** Somente com workers conhecidos e
   quiescentes, volume e imagem atuais, executar
   `app/tests/provider/provider_usage_checks.py --output <ARQUIVO_NOVO_EM_EVAL_RUNS>`.
   O gate reinicia os dois workers de forma serial e usa um controle temporário
   isolado; não zerar, substituir ou migrar o contador real. Exigir antes/depois,
   `/usage` no mesmo volume, SHA dos módulos, limite de 1.000, nenhuma requisição
   ativa, incremento ligado aos request IDs e ausência de replay adicional.

5. **Gemini ADK real — RAG-02/BUG-061.** No laboratório implantado com fontes
   verificadas, executar `app/tests/gemini/gemini_rag_smoke.py`: duas rodadas,
   resposta canônica de alimentação por Gemini fixo, negativas, replay, ACL e
   snapshots de uso antes/depois. Selar apenas com
   `app/tests/provider/provider_card_receipt.py --card RAG-02 --proof <SMOKE> --billing-proof <BILLING_ATUAL> --usage-persistence-proof <PERSISTENCIA_ATUAL>`.
   Injeção corretamente abstida não precisa consumir inferência; consulta
   normal continua exigindo provider real. Quota/403/timeout são bloqueios.

6. **MCP real — RAG-01/RAG-03.** Profiles isolados existentes, sem criar tokens:
   probes `python -m rag_app.atlassian_lab` e `python -m rag_app.github_lab`,
   com initialize/tools/list e leitura real fixa. Depois,
   `app/tests/mcp/feed_smoke.py`: duas rodadas com feed real atual, KAN-1/KAN-2,
   repository allowlist e negativas de perfil anônimo/cliente/operador.
   Selar `provider_card_receipt.py --card RAG-01` e `--card RAG-03` com o feed
   completo atual. O selador vigente exige os dois providers aprovados; erro
   Jira permanece bloqueio, mesmo se a leitura GitHub passar.

7. **MCP no RAG — RAG-04.** Executar
   `app/tests/mcp/mcp_evidence_gate.py`: snapshot explicitamente publicado pelo
   operador, duas rodadas reais, corpus preservado, ADK/Gemini, modelo/citação
   canônicos, separação de papéis e reautorização após expiração de no máximo
   180 segundos. Guardar tentativa e casos anteriores também em falha.

8. **DeepAgents real — RAG-07.** Executar
   `app/tools/revalidation/advanced-card-gate.py task`. Exige SDK real, tarefa
   concluída, ferramentas efetivas restritas, checkpoint após fechamento,
   cancelamento durante tarefa, budgets e duas rodadas na imagem verificada.
   O entrypoint faz build/Docker e chamadas Free; não é teste offline da lane.

9. **DeepEval real — RAG-08.** Executar
   `app/tools/revalidation/advanced-card-gate.py judge`. Congelar judge-v1,
   threshold 1.0, calibragem e holdout intactos; dois passes sem falso aceite
   nos casos autorais, judge Free explícito, zero upload/tracing e budgets.
   Não ajustar o holdout para fazer passar nem chamar esse conjunto autoral
   de auditoria independente ou certificação geral.

10. **Vertex — RAG-14.** Não executar. Identidade, região e custo não estão
    autorizados; o card permanece BLOCKED. Não usar como fallback de quota.

O Python reutilizado tem ADK 2.10.0, GenAI 2.25.0 e MCP 2.2.0. Faltam DeepAgents,
DeepEval, langchain-core, langgraph-checkpoint-sqlite, pip-tools e setuptools;
nenhum pacote foi instalado ou atualizado. Não houve bug de outro arquivo que
exigisse escrita fora da allowlist. Dependências restantes são integração da
Central, permissão para commit Git, SDK/imagem isolados e as provas reais listadas acima.
