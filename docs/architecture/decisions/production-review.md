# Revisão de arquitetura e início da implementação

## Revisão 5 — recuperação extrativa real e regressão (30/09/2026)

**Resultado atual: duas rodadas reais positivas por suíte local, sem aprovação
global ou de produção.** São testes adversariais do mesmo autor, com oráculos
fixados antes das entradas aleatórias, não auditoria cega independente.

| Suíte real | Rodada 1 | Rodada 2 | Recorte |
| --- | ---: | ---: | --- |
| RAG extrativo | 31/31 | 31/31 | HTTP, PostgreSQL, Qdrant e grafo nativo ADK |
| Regressão de pedidos | 34/34 | 34/34 | HTTP, PostgreSQL, ADK, leases, faults e restart |

Seeds RAG: 1730738146 e 225250747. Seeds lifecycle: 2898845025 e 2881537686.
Receipts atuais:

- [RAG real](../../../eval/runs/rag-real-20260930T225724Z-7adfe8/receipt.json)
- [Regressão real](../../../eval/runs/segments-real-20260930T230210Z-7e54eb/receipt.json)
- [Mapa por caso](../../../eval/runs/segments-design-20260930T230603Z-26d978/receipt.json)

Fontes e imagens ficaram congeladas durante as rodadas. O mapa final verifica
hashes atuais e IDs das imagens, recusando evidência vencida. Os containers
temporários foram encerrados; os volumes e receipts de falhas foram preservados.
Não foi entregue servidor permanente, nem feito commit/push/deploy.

O catálogo agora tem **94 casos**: 35 PASS_REAL_SLICE, 6 STRUCTURAL_ONLY e
53 PENDING. Foram adicionados 11 casos específicos da baseline extrativa; os
casos empresariais originais não foram rebaixados para satisfazer a fixture.

Implementado no perfil opt-in: ingestão sintética limitada text/plain, releases
e catálogo SQL, Qdrant com dense/sparse/RRF, snapshot no aceite, grafo ADK
retrieve → compose → verify, citação literal canônica, revogação no commit e
na entrega/lista, bloqueio de citação adulterada, reconciliação por replay após
503, e logs por etapa sem pergunta/fontes/credenciais. OpenTelemetry tem spans
instrumentados; collector/exporter e métricas completos ainda não foram provados.

### Falhas mantidas e correções

- `rag-real-20260930T224523Z-32b0c0`: referência de imagem sem namespace correto;
  corrigida para qdrant/qdrant com digest fixo.
- `rag-real-20260930T224534Z-48d8df`: comparação não normalizada da listagem de
  imagens; IDs agora são comparados como conjunto estável.
- `rag-real-20260930T224647Z-8e0b7c`: escape inválido no harness de citação
  adulterada; corrigido, sem alterar o trecho esperado.
- `rag-real-20260930T224952Z-0523dd`: segunda rodada falhou no cenário
  instrucional. Diagnóstico SQL/HTTP mostrou que o pedido usou corretamente o
  release anterior, sem confirmação da ingestão esperada. O caller agora
  exige 201 READY antes da consulta e faz até três chamadas do mesmo bundle
  somente após 503 INDEX_UNAVAILABLE. A API informa Retry-After; fault deliberado
  continua exigindo 503 na primeira chamada. Não foi alterado o conteúdo esperado.

Qualquer falha/correção reiniciou a contagem. Os dois passes finais estão nas
fontes/imagens atuais, não em comprovante anterior à correção.

### Limites e próxima autoridade necessária

O embedding é `lexical-hash-256-v1`, baseline lexical, **não neural**. Resposta
EXTRACTIVE é um trecho, não consultoria gerada nem prova de verdade da fonte.
PDF/object storage, benchmark semântico/reranker, DeepAgents, DeepEval/judge,
Gemini/Vertex e MCPs hospedados permanecem nos gates próprios. Os dois passes
locais não certificam 100.000 HTTP requests, SLO, HA, restore/PITR ou produção.

Perfil padrão do laboratório é local. Fonte pública GitHub sugerida é
`google/adk-python`, mas não há MCP autenticado na aplicação. A descoberta do
conector Atlassian retornou sites acessíveis vazios; nenhum ticket/documento foi
lido. Não há projeto/região/modelo Vertex inventados nem autorização de cobrança.
Ver [perfis](../contracts/lab-profiles.json) e [preparação online](../../operations/online-setup.md).
Configurar contas próprias, recursos de teste e orçamento autorizado exige
participação do usuário; não usar credencial compartilhada/backdoor.

## Revisão 4 histórica — casos por segmento e loop adversarial (30/09/2026)

**Resultado daquela revisão: duas rodadas positivas na fatia implementada; aprovação global
ainda NÃO obtida.** O catálogo tem 83 casos, não uma promessa de todas as
possibilidades. Cada linha fixa cenário, esperado, proibido, menor prova, runner
e check. Ver [catálogo](../contracts/segment-cases.tsv) e [contrato](../../acceptance/operations/segment-acceptance.md).

| Segmento | Casos com prova real nesta fatia | Apenas estrutural | Pendentes |
| --- | ---: | ---: | ---: |
| Python/ADK | 9 | 2 | 1 |
| RAG | 0 | 0 | 12 |
| MCP/APIs | 0 | 2 | 10 |
| Vertex/Gemini | 0 | 0 | 8 |
| Avaliação/observabilidade | 1 | 2 | 7 |
| DeepAgents | 0 | 0 | 8 |
| Operação/produção | 8 | 0 | 5 |
| Frontend | 6 | 0 | 2 |
| Total | 24 | 6 | 53 |

Frontend real aqui significa os contratos HTTP selecionados; não uma nova rodada
de E2E visual. RAG/Gemini/DeepAgents/MCPs não foram substituídos por mocks para
obter aprovação. São pendências de implementação/configuração e testes reais.

### Falhas e correções preservadas

1. [Baseline](../../../eval/runs/segments-real-20260930T221929Z-90710d/receipt.json):
   uma chave antiga conhecida recebia 409. A admissão agora consulta o mapping
   tenant/ator e confere payload antes de aplicar idade a uma chave desconhecida.
   A mesma rodada detectou que a role da aplicação não pode DELETE: preservamos
   esse privilégio restrito e movemos a injeção do job sintético para a role
   administrativa existente no serviço de migration, sem novas permissões.
2. [Reteste que falhou](../../../eval/runs/segments-real-20260930T222216Z-d1b4bb/receipt.json):
   falhas 503 expuseram a fragilidade de congelar um worker durante transação.
   Adicionado idle_in_transaction_session_timeout server-side. Um teste real
   segura o lock de admission, observa o backend terminar com timeout e compara
   com uma conexão mutante sem timeout que permanece viva. Os testes de worker
   indisponível passaram a usar parada controlada, não uma pausa em instante
   arbitrário. Falhas não foram convertidas em PASS; a sequência foi zerada.
3. Figuras do Eraser alinhadas: coordenação determinística do request worker,
   ADK orquestrando etapas, DeepAgents delegado por TaskPort, GitHub/Atlassian
   remotos através do gateway. Removida a substituição genérica por stdio desse
   perfil. Readbacks preservados; DSL da aplicação coincide com o editor por
   normalização LF/trim. [Captura atual](../../diagrams/application/application-segments-eraser.png).
4. Validador antigo corrigido: não exige mais dizer que inexiste backend, pois
   existe uma primeira fatia. Passa a exigir a distinção explícita dessa fatia
   para o RAG completo. Receipts históricos não foram sobrescritos.

### Evidência final desta execução

[HTTP/PostgreSQL/ADK reais](../../../eval/runs/segments-real-20260930T222651Z-dab4ff/receipt.json):
seeds **3745468781** e **3424015055**, **34 verificações aprovadas em cada rodada**,
sem alteração das fontes/imagens congeladas, conservação por IDs e audit/outbox
atômicos. Inclui restart PostgreSQL, recuperação de job ausente, lease vencida,
teto de tentativas, rejeição de proposta não verificada e receipt envelhecido.
Containers temporários encerrados; volumes preservados. Não foi iniciado servidor
permanente, não houve chamada de modelo pago nem acesso a MCPs/contas reais.

[Matriz final de evidências](../../../eval/runs/segments-design-20260930T223113Z-460daa/receipt.json):
duas rodadas de 16 checks de catálogo/grafo/fronteiras com controles negativos;
hashes atuais da evidência real conferidos, 24 PASS_REAL_SLICE, 6 STRUCTURAL_ONLY
e 53 PENDING. O status global fica false, não a média dos segmentos.

[MCP documental](../../../eval/runs/remote-mcp-design-20260930T223046Z-917426/receipt.json):
18/18 duas vezes; não conexão real.
[Contramodelos de produção](../../../eval/runs/production-design-20260930T223046Z-b6ba58/receipt.json):
28/28 duas vezes; 100.000 admissions somente no modelo, não carga HTTP/LLM.

**Não é auditoria cega independente:** mesmo autor, oráculos fixados antes das
entradas aleatórias, controles negativos e hashes; isso limita os pontos cegos,
mas não prova 100% de segurança nem ausência universal de bugs. Gate B continua
fechado. O loop global depende das 53 implementações/provas pendentes, corpus e
contas autorizados, políticas de dados/custo e workload/infraestrutura definidos;
repetir o mesmo desenho não remove esses bloqueios.

## Histórico preservado — revisão 2

Revisão 2 em 30/09/2026. Correções: durabilidade/failover explícitos, control-worker reservado, limites/justiça de admissão, retenção e rejeição de chaves antigas. Acrescentados frontend próprio, containers por papel, volumes POSIX, segurança, observabilidade, backups e gates de release. Não houve deploy nem acesso a dados de outras aplicações.

O loop detectou também controles negativos insuficientes no harness e uma ligação do gate cruzando storage no canvas. Corrigimos os controles e aproximamos EVAL/gate no mesmo grupo do diagrama, com layout automático. Rodadas anteriores foram preservadas e a contagem final foi reiniciada após a última mudança.

## Duas rodadas finais do desenho

Evidência: [receipt.json](../../../eval/runs/production-design-20260930T204753Z-31a710/receipt.json). Seeds 3502327428 e 1798125403: **28/28 checks cada**, incluindo contramodelos de 100.000 admissões em memória, mutantes de replicação, fence, pool saturado, FIFO e retenção. Contratos congelados por hash; gate_a_passed=true e gate_b_passed=false.

Revisão documental adicional em duas lentes: (1) conservação/recuperação sob falhas, com banco indisponível e commits ambíguos explicitamente tratados; (2) segurança/operação, com isolamento, storage Windows, permissões, credenciais, readiness, custo e release. Sem novos achados documentais abertos nos cenários escolhidos. Não são testes cegos de auditor independente, nem prova HTTP/HA/LLM real. Os testes SQLite anteriores continuam evidência histórica da referência, não prova do novo backend.

## Decisão e limites

Gate A permite iniciar implementação local no D:, em fatias. Gate B continua bloqueado para release: falta implementação completa, integração real, carga, qualidade RAG, adaptadores e infraestrutura/credenciais de produção. Defaults são hipóteses de dimensionamento, não promessa de throughput. “100%” não é conclusão sustentada por estas rodadas.

Primeira fatia: frontend de pedidos/status/cancelamento; backend FastAPI/Python, workflow Google ADK determinístico de abstenção, PostgreSQL, worker e control-worker. Tudo containerizado, sem expor bancos ou chamar cloud. Demo local tem identidade sintética; modo produção deve recusá-la. Qdrant/retrieval/corpus, modelos, DeepAgents, MCP/APIs, Vertex/Gemini e DeepEval/judge continuam planejados, não abandonados nem marcados como prontos.

O backend não publica candidato bruto nem usa humano para aprovar respostas. O operador consulta logs/métricas. Nenhuma garantia contra perda do D: único. Continuar corrigindo/regredindo cada fatia, e só marcar release elegível após duas execuções reais consecutivas dos gates de produção. A implementação iniciada não é autorização de deploy, commit ou push.

## Loop da implementação — fatia 1

Código em `D:\RAG-Local\app` e frontend em `D:\RAG-Local\frontend`. Imagens construídas com dependências travadas por hash; volumes Linux no Docker do D:. A versão Python dentro da imagem é 3.12; o venv host existente não foi alterado.

O teste de runtime encontrou montagem tmpfs mal interpretada pelo YAML e diretórios temporários Nginx incompatíveis com filesystem somente leitura. Corrigimos a montagem e os paths, mantendo non-root, read-only e cap-drop. O harness também precisou consultar as imagens pretendidas, não imagens antigas de containers parados. Os erros não foram tratados como passes.

A primeira execução funcional passou 20 checks por rodada, mas a inspeção UI detectou recibo/aviso residual ao trocar tenant. A API já negava o acesso cruzado; limpamos também o contexto visual e reiniciamos a contagem. Evidência da falha preservada em `http-slice-20260930T212303Z-7e4e04/visual-findings.json`.

Evidência final da fatia: [receipt](../../../eval/runs/http-slice-20260930T212756Z-33a330/receipt.json), seeds **200742262** e **570100529**, **20/20 checks HTTP/PostgreSQL/ADK em cada rodada**, com fontes do build/harness congeladas. Inclui 16 submits duplicados concorrentes por rodada, isolamento, confirmação perdida resolvida pela chave, limites/validação, cancelamento idempotente, expiração com inferência pausada, lease/fence e um terminal/audit/outbox por ID aceito. São 4 pedidos únicos por rodada, não 100.000.

Checks adicionais: [duas rodadas visuais](../../../eval/runs/http-slice-20260930T212756Z-33a330/visual-checks.json), quatro cenários por direção A→B/B→A, reload sem sessão persistida e mobile 393 px sem overflow horizontal; [duas rodadas de restart PostgreSQL](../../../eval/runs/http-slice-20260930T212756Z-33a330/restart-checks.json), cinco checks cada, pedido aceito preservado e concluído após restart, dedupe e audit/outbox terminal únicos. Restart de container não é backup/restore, perda do host, failover nem prova de RPO zero. UI é observação do mesmo revisor; não é auditoria cega independente.

Build das duas imagens, compilação Python e sintaxe JavaScript passaram. A tentativa de supervisor persistente foi bloqueada pela política da ferramenta; não criamos tarefa agendada nem contornamos o bloqueio. O fixture temporário encerrou somente este projeto, confirmou `containers_stopped=true` e preservou volume e evidências. Outros containers/projetos não foram alterados. Não existe servidor permanente entregue.

**Gate B permanece false:** workflow real ADK, porém determinístico/offline/sem corpus, com abstenção. Falta recuperação vetorial, ingestão, modelos, DeepAgents, MCP/Vertex, métricas/tracing completos, evals de qualidade, segurança corporativa, carga real e infraestrutura de produção. O outbox ainda não tem dispatcher. Os logs são mínimos com IDs/tipos de erro, não uma plataforma de observabilidade completa. O primeiro slice usa migration SQL idempotente, não migrações de upgrade já certificadas. O checklist de implementação/release está no [README](../../history/application/implementation-notes-20261002.md).

## GitHub e Atlassian MCP remotos

Revisão 3 de 30/09/2026, somente arquitetura: GitHub oficial remoto e Atlassian oficial remoto (Confluence/Jira) adicionados ao Eraser e aos contratos. O gateway ToolPort/MCP concentra as invocações mediadas de ADK/DeepAgents e as conexões HTTPS; não há novo servidor MCP local. O canvas mantém as figuras existentes, layout automático e separação dos serviços externos. Código do editor lido de volta coincide com o DSL local; prova em [application-remote-mcp-eraser.jpg](../../diagrams/application/application-remote-mcp-eraser.jpg).

O [contrato](remote-mcp.md) distingue fatos dos provedores das políticas propostas: leitura, credenciais por identidade, cloudId/repo autorizado, cache/revogação, egress, deadline, 429, circuit breaker, sync com checkpoint e logs redigidos. GitHub tem modo read-only documentado; não presumimos o mesmo header na Atlassian. Providers permanecem desativados no manifest de planejamento, sem alterar o runtime/backend/containers.

[Receipt da checagem documental](../../../eval/runs/remote-mcp-design-20260930T220158Z-45af2e/receipt.json): 18/18 checks em cada uma das duas ordens de execução, seeds 2135931835 e 4111994404, arquivos congelados por hash e contramodelos de bypass/rota ausente. São checks de documentos/grafo do mesmo revisor, não testes cegos independentes, integrações reais ou uma nova aprovação completa dos gates de produção. `real_mcp_tested=false` e `gate_b_passed=false`. Receipts anteriores continuam históricos, não certificam os hashes desta revisão. Conexão real requer autorização/configuração de contas e credenciais e o checklist específico; nenhuma conta foi conectada nesta alteração.
