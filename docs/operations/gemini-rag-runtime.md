# Gemini conectado ao laboratório RAG — 01/10/2026

## Fluxo implementado

Ana -> frontend local -> FastAPI -> PostgreSQL (pedido/snapshot/job)
-> worker -> grafo Google ADK -> embedding multilíngue local + Qdrant
-> trechos/ACL canônicos no PostgreSQL -> Gemini Developer API
-> seleção de um chunk ou abstenção -> validação estrita + autorização atual
-> commit do resultado no PostgreSQL -> frontend consulta o recibo.

Gemini `gemini-3.5-flash-lite` escolhe evidência, não escreve prosa livre neste
recorte. A saída permitida é apenas answerable e chunk_id. A aplicação exige
campos exatos, tipos corretos e ID presente nos candidatos; o texto e a citação
vêm da fonte, não do modelo. Suporte insuficiente produz abstenção. Isso reduz
fatos inventados mas não prova que todo julgamento de relevância será correto.

MCP Jira/GitHub continua em gateway separado para leitura de feeds por Bruno;
não recebe credencial Gemini nem concede ferramentas ao seletor. Criar/mover
cards, ingestão desses feeds, Vertex e DeepAgents não fazem parte deste fluxo.

## Custo e segurança

Antes das chamadas deste lote, verificado no AI Studio: projeto
gen-lang-client-0580698701 no Nível gratuito; Console Cloud: projeto sem conta
de faturamento vinculada. Nenhum billing, plano, fallback pago, Vertex ou
grounding Google Search foi habilitado. Foram enviados somente dados sintéticos.
Não é relatório financeiro auditado nem garantia caso alguém mude billing depois.

Opt-in exige RAG_MODE=lab, RAG_GEMINI_RESPONSES=free_lab,
RAG_GEMINI_FREE_CONFIRMED=no_billing e Vertex=false. Essa flag registra a
verificação operacional, NÃO consulta nem trava o faturamento Google.
Confirmar o estado externo antes de próximos lotes. Google pode aplicar quota
Free inferior ao teto local; quota esgotada falha fechada, sem pagar para contornar.

Chave somente no worker, como secret privado; nunca frontend/API/logs. Modelo e
endpoint fixos. Até 5 trechos e 24.000 bytes de payload; 256 tokens de saída;
uma chamada por request; timeout HTTP 15 s e total 20 s; sem tools ou retry SDK.
Workflow até 35 s, lease 60 s. Pergunta vazia/espaços rejeitada antes do ledger.

Volume durável gemini_probe_usage compartilha o contador com o probe: máximo
1.000 reservas/dia UTC. Reserva do request e orçamento são transacionais.
Resultado validado pode ser reutilizado no mesmo request; reserva com resultado
desconhecido não dispara outra chamada. Contador NÃO foi resetado. Em 07:24 UTC,
17 reservas no dia, 10 seleções concluídas e 3 reservas sem resposta reutilizável
(incluem configurações recusadas); reservas não equivalem a cobranças.
Os probes anteriores explicam as outras 4 reservas diárias.

Falha de provedor/schema/timeout torna-se resultado terminal de abstenção, sem
fallback extrativo escondido. Fonte/ACL é revalidada no commit e na leitura.
Segredos não aparecem nos diagnósticos; logs guardam request ID/etapa/status/tempo.
Egress da rede Docker NÃO é firewall por destino: endpoint é limitado no código.

## Runtime persistente

Nome estável: projeto rag-local-v2, diretório D:\RAG-Local\app.
Docker Engine supervisiona com restart: unless-stopped, fora do processo Codex;
skill persistent-local-server aplicada para preservar esse supervisor existente.
Endpoint: http://127.0.0.1:8840/#consultar; readiness: /health/ready.
Não há publicação na internet. production_ready=false permanece correto.

Usar SEMPRE os cinco arquivos no runtime vigente:

```powershell
& 'D:\Docker\App\resources\bin\docker.exe' compose --project-directory 'D:\RAG-Local\app' `
  -f D:\RAG-Local\app\infrastructure\compose\runtime\compose.yaml `
  -f D:\RAG-Local\app\infrastructure\compose\runtime\compose.retrieval.yaml `
  -f D:\RAG-Local\app\infrastructure\compose\runtime\compose.semantic.yaml `
  -f D:\RAG-Local\app\infrastructure\compose\runtime\compose.integrations.yaml `
  -f D:\RAG-Local\app\infrastructure\compose\labs\compose.gemini-rag.yaml ps
```

Para atualizar, mesmos arquivos com build api frontend, depois up -d --no-deps
api worker frontend. Não usar down -v nem remover órfãos/volumes. Omitir o quinto
arquivo pode desabilitar a seleção Gemini. Logs operacionais: docker logs
rag-local-v2-worker-1; não exibir traces/erros brutos ou env/segredos.

## Provas anteriores e limites

Pasta: D:\RAG-Local\eval\runs\gemini-rag-20261001.

- probe-fixed.log: chamada real ADK/Gemini bem-sucedida após deadline corrigido.
- live-072334.json: duas rodadas consecutivas de 37 checks com SHA das fontes.
  Alimentação -> 45 reais/uma citação; nonsense -> abstenção sem modelo;
  abobrinha -> abstenção após Gemini; instrução 999 -> abstenção após Gemini.
  Replays preservam recibo, operador não lê pedidos do cliente, espaços -> 422.
- sdk-container.json: 24 checks x2, SDK/ADK reais com Runner falso, sem rede.
- grounded-container.log: 24 checks x2, fake provider/DB em testes locais de
  schema, citações, revogação no commit, endpoint/modelo e reserva idempotente.
  Não simular como corrida SQL real a revalidação mockada neste lote.
- gemini-connected-ui.png: consulta pela UI real com Gemini e fonte de 45 reais.
- build-final.log, up-final.log e frontend-final-*.log: imagens/runtime atualizados.
- Falhas anteriores foram preservadas; sequência limpa reiniciada após correção.

São regressões delimitadas do mesmo executor, NÃO duas auditorias cegas
independentes. Não houve carga 100.000, certificação de produção, escrita MCP,
ingestão externa ou liberação de dados reais. A conta demonstrativa continua
sem autenticação forte; não publicar essa seleção de perfis na internet.

Correções ADK seguem [contrato de LlmAgent/output_schema](https://adk.dev/agents/llm-agents/).
O JSON schema limita formato; autorização e verificação continuam no backend.

## Ampliação da alimentação — versão atual

Em 01/10/2026, 20 regras fictícias de alimentação foram publicadas de forma
aditiva, preservando os 10 documentos anteriores. Teto R$45 e prazo 12 dias
úteis mantidos; período do teto continua pendente. O Gemini recebe até cinco
candidatos qualificados sem exigir a margem de vencedor da resposta extrativa;
no modo sem Gemini, a margem conservadora continua necessária. A seleção,
validação de citações e autorização permanecem separadas.

Prova atual do recorte alimentação: duas rodadas reais de 22 cenários e 234
checks cada em `eval/runs/meal-policy-20261001T073758Z/checks-081040.json`.
Os recibos antigos acima são históricos, não substituem uma regressão atual.
Falhas transitórias do provedor foram preservadas, não omitidas ou contadas como
passes. Detalhes: `docs/architecture/meal-expansion-acceptance.md`.

Quando semantic_policy.py mudar, reconstruir **api e embeddings** e recriar
**embeddings, api e worker** com os mesmos cinco arquivos Compose, sem remover
volumes. Backend e serviço de embeddings precisam concordar no SHA da política.
