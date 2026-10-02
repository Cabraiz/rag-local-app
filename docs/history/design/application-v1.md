# Arquitetura da aplicação RAG em Python

Proposta para implementação local em `D:\RAG-Local`, a partir do workflow RAG já revisado. A decisão é usar um **monólito modular com arquitetura hexagonal leve**, uma base Python e processos de execução separados. A arquitetura define onde cada responsabilidade ficará; o backend ainda não está implementado.

## Decisão e justificativa

Monólito modular descreve a organização e a entrega da aplicação. Hexagonal descreve a direção das dependências: regras e casos de uso definem interfaces que os adaptadores implementam. São escolhas compatíveis, não alternativas. Aqui elas permitem trocar ADK, modelo ou armazenamento sem transportar regras de isolamento, abstenção e entrega para dentro de um framework.

Esta é uma recomendação para o projeto, não uma afirmação de ranking de mercado. Não encontramos uma medição que determine uma única arquitetura mais usada em RAG. A separação por interfaces segue um princípio documentado em [Clean Architecture](https://learn.microsoft.com/en-us/dotnet/architecture/modern-web-apps-azure/common-web-application-architectures); a aplicação desse princípio a Python é nossa escolha. FastAPI fornece [routers e dependências para aplicações maiores](https://fastapi.tiangolo.com/tutorial/bigger-applications/), mas não exige hexagonal.

| Opção | Uso neste projeto |
| --- | --- |
| Rotas com toda a lógica dentro | Não: mistura HTTP, regras, SDK e transações, dificultando teste e troca de framework. |
| Monólito modular com ports e adapters | Escolhido: fronteiras testáveis e uma base operacional pequena. |
| Microsserviços por agente ou por camada | Não inicialmente: aumentam falhas distribuídas, custo e operação sem uma necessidade de escala demonstrada. |

Não será criada uma interface para cada função, uma classe para cada tabela ou um barramento genérico de eventos no domínio. Criar ports somente nas fronteiras externas que precisam de substituição, controle ou testes de contrato. Os processos compartilham a mesma versão de pacote e contratos; não viram serviços independentes por terem entrypoints diferentes.

## Stack e perfis

| Responsabilidade | Escolha proposta | Condição |
| --- | --- | --- |
| Código e ambiente | Python 3.12, pyproject.toml, uv e lockfile | Validar compatibilidade de todos os extras antes de fixar versões. Manter o ambiente ADK existente intacto. |
| API de produto | FastAPI, Pydantic nas bordas, Uvicorn | Entrada autenticada; handlers finos, sem inferência ou SQL nos routers. |
| Workflow | Google ADK atrás de WorkflowPort | SDK 2.10.0 já testado offline neste laboratório; isso não prova compatibilidade com os demais SDKs. |
| Tarefas complexas | DeepAgents atrás de TaskPort | Dependência explícita para preparação da vaga; rota opcional por tipo de tarefa, não ativada em todo pedido. |
| Estado transacional | PostgreSQL, SQLAlchemy 2, Alembic | Ledger, jobs, budgets, catálogo, auditoria mínima e outbox. SQLite fica nos testes históricos, não neste runtime compartilhado. |
| Recuperação | Qdrant em modo servidor, qdrant-client | API e workers não abrem o mesmo índice embedded. Busca densa/esparsa, filtros e reranking avaliados no corpus. |
| Conteúdo | StoragePort, diretório privado no D: | Objetos imutáveis com hash; produção pode usar object storage. Banco guarda referências autorizadas. |
| Modelos | ModelPort, EmbeddingPort e RerankPort | Fixture offline para testes; inferência local se hardware suportar; Vertex/Gemini apenas por opt-in autorizado. |
| Ferramentas | MCP client e HTTPX atrás de ToolPort | Allowlist, identidade confiável, validação, timeouts e egress. |
| Avaliação | pytest, DeepEval e judge configurado | Suites determinísticas e adversariais mais gold/holdout; judge não autoriza conteúdo. |
| Observabilidade | Logging JSON, OpenTelemetry e métricas | Export opcional; auditoria mínima não depende do collector. |

Modelo e embedding específicos serão escolhidos por benchmark em português, licença, memória e latência. Não escolher um modelo grande apenas por ser recente. Embedding/dimensão, chunker, reranker e prompt pertencem ao release bundle versionado.

No laboratório, PostgreSQL pode fornecer a fila de jobs: claim curto com `FOR UPDATE SKIP LOCKED`, lease e fence, sem manter transação aberta durante inferência. O [PostgreSQL documenta essa técnica para tabelas semelhantes a filas](https://www.postgresql.org/docs/current/sql-select.html); não é uma consulta apropriada para leituras gerais consistentes. Isso reduz serviços locais, não promete alta disponibilidade.

O perfil de escala preserva a opção RabbitMQ já prevista no RAG: outbox publica eventos com confirm, consumidor usa o mesmo ledger e ACK só após commit terminal ou retry durável. Broker e Redis não são requisitos do primeiro exemplo. Redis pode acelerar cache, mas nunca ser a única fonte de pedidos aceitos. A implantação distribuída depende de medições e autorização, não de haver 100.000 usuários cadastrados.

## Módulos e proprietários

| Módulo lógico | Responsabilidade | O que não pode fazer |
| --- | --- | --- |
| requests | Aceitar, reivindicar, cancelar, finalizar e consultar pedidos; idempotência, leases, deadlines e entrega | Confiar na sessão ADK como ledger ou publicar proposta não verificada. |
| rag | Recuperar, integrar evidências, compor, verificar citações/números, abster | Ignorar ACL ou fazer commit final diretamente por um agente. |
| corpus | Validar fontes, extrair, dividir, indexar candidato, tombstone e promover release | Alterar coleção ativa durante ingestão ou reativar fonte revogada por rollback. |
| tools | Autorizar invocação, limitar custo e tempo, validar saída MCP/API | Usar tenant/URL/escopo sugeridos pelo modelo como autoridade. |
| tasks | Delegar tarefa complexa e validar ResultEnvelope antes de agregar | Criar filhos ilimitados, aceitar filho atrasado ou publicar resposta. |
| evaluation | Executar suites e produzir EvalReport vinculado ao bundle | Transformar judge em autorização ou promover versão sem gates completos. |

Políticas de identidade, autorização, orçamento e publicação são serviços determinísticos da aplicação/domínio, não agentes LLM. Não existe aprovação humana por resposta; operador consulta logs e métricas, e administra configuração fora do fluxo de resposta. Ingestão e administração continuam exigindo permissão de escrita específica.

## Organização do código

Árvore planejada, não pastas de implementação já criadas:

```text
D:\RAG-Local\
  app\
    pyproject.toml
    uv.lock
    src\rag_app\
      domain\                 # estados, contratos imutáveis e políticas puras
      application\
        requests\             # accept, execute, finalize, read, cancel
        rag\                  # etapas de retrieve, compose e verify
        corpus\               # ingest, revoke, promote
        tools\                # tool gateway
        tasks\                # delegação e validação de envelopes
        evaluation\           # avaliação e attestations
        ports\                # Protocols definidos pelo núcleo
      adapters\
        inbound\              # HTTP, fila, CLI e facade do ADK Web
        outbound\             # ADK, DeepAgents, SQL, Qdrant, modelos e ferramentas
      bootstrap\              # configurações, DI e ciclo de vida de recursos
      entrypoints\            # api, request_worker, ingest_worker, eval_worker, dev_agent
    migrations\               # apenas schema SQL
    tests\                    # unit, contracts, integration, security, e2e, load
    evals\                    # datasets anonimizados, rubricas e manifests
    deploy\                   # perfis locais e futuro perfil de escala
  data\                       # postgres, qdrant, objetos privados e checkpoints
  models\
  cache\
  tmp\
  logs\
  eval\runs\
  docs\architecture\         # este planejamento e DSLs
  adk\.venv\                 # ambiente existente preservado
```

`domain` usa biblioteca padrão e não importa FastAPI, ADK, DeepAgents, SQLAlchemy ou clientes externos. `application` importa domínio e seus próprios ports. Adaptadores importam o núcleo, não o contrário. Somente bootstrap conhece classes concretas, cria recursos e injeta as implementações. Entry points são finos; a rota de teste do ADK Web usa a mesma fachada de casos de uso, não uma segunda lógica de RAG.

Contratos internos usam dataclasses imutáveis e `typing.Protocol`; schemas Pydantic validam entrada/saída HTTP e envelopes externos. Não transportar objetos Session, ORM ou mensagens específicas de SDK pelo núcleo. Enforce essas regras em CI por Import Linter ou teste AST de dependências, além de Ruff e type checking.

## Ports e adaptadores

| Port | Contrato essencial | Adaptadores |
| --- | --- | --- |
| WorkflowPort | run(handle, contexto) → proposta privada; deadline/cancel | ADK; fake determinístico em testes |
| TaskPort | executar tarefa limitada → ResultEnvelope | DeepAgents separado do ADK; fake |
| RetrievalPort | consulta autorizada + snapshot → EvidenceSet | Qdrant + rerank; fake |
| ModelPort | perfil + propósito + contexto autorizado → proposta tipada e usage | Local ou Vertex/Gemini; fake |
| EmbeddingPort e RerankPort | versão/dimensão e batch/limite | Modelos locais ou provedor explicitamente autorizado |
| ToolPort | invocation autorizada → ToolResult ou erro tipado | MCP, REST; fake |
| LedgerPort e UnitOfWork | transação, claim/fence, CAS terminal, audit/outbox | PostgreSQL |
| StoragePort | get autorizado por tenant e hash; put idempotente | Arquivos privados no D:; object storage no perfil de escala |
| EvaluationPort | bundle + suite/rúbrica → EvalReport verificável | Determinístico, DeepEval e judge configurado |

ADK é o adaptador de workflow, não o dono da política de negócio. Ele recebe callbacks limitados para serviços de etapas da aplicação, sem acesso a finalize, credenciais ou DI container. O caso de uso executa o WorkflowPort e recebe sua proposta; o adaptador não chama novamente ExecuteRequest. Esse contrato evita reentrada e dependência circular. ModelPort também retorna ao caller registrado pelo serviço, não a uma rota escolhida pelo LLM.

## Comunicação e execução de um pedido

1. API valida autenticação, corpo, tamanho, escopo, quotas e idempotency key. A identidade vem do gateway/configuração confiável, nunca do prompt. Para demo local, identidade fixture fica restrita a loopback e dados sintéticos; não é uma opção válida no perfil de produção.
2. AcceptRequest grava request, job, reserva de admissão e auditoria/outbox na mesma transação PostgreSQL. Só depois responde 202 com request_id e URL de status. Repetição da chave no mesmo tenant/ator com payload diferente retorna conflito. Falha antes do commit não aceita o pedido; falha após commit permite consulta/retry idempotente.
3. Worker faz claim com lease, attempt e fence, encerra a transação e executa o workflow ADK. Etapas chamam serviços da aplicação por funções Python async e contratos tipados. Mensagens de agentes são propostas/evidências, não comandos de autoridade.
4. Recuperação usa snapshot imutável e filtros obrigatórios de tenant/ACL, seguidos de validação das fontes. Geração usa orçamento agregado, reautorização e perfil de dados antes de chamar o ModelPort. Ferramentas passam pelo gateway. Tarefa complexa pode usar TaskPort/DeepAgents; resultado só entra após validação de fence, tenant, hash, snapshot, validade e schema.
5. Verificação confere referências, evidências e regras determinísticas; evidência insuficiente causa abstenção. O request worker chama FinalizeRequest. Ele reautoriza conteúdo e compara fence/estado/versão de autorização sob uma transação curta, gravando terminal, resultado permitido, auditoria e outbox. Revogação usa a mesma ordem de locks/epochs; um veredito anterior ao lock não basta. O agente não tem capacidade de commit.
6. Cliente consulta status e resultado pelo request_id com autorização atual. SSE pode avisar progresso redigido e disponibilidade do terminal; polling autenticado sempre permite recuperar após desconexão. Nunca transmitir tokens brutos da proposta antes de verificar. Notificação duplicada não duplica resultado.

No processo, comunicação é chamada Python, não HTTP entre camadas. Entre processos, jobs/eventos duráveis transportam IDs, trace context e versão de schema — não documento, prompt ou credencial. MCP é o protocolo de ferramentas externas; não é necessário para dois agentes Python locais conversarem. A2A fica fora da primeira implementação e só será considerado se houver agentes remotos independentes.

`RequestContext` leva tenant_id, actor_id, request_id, attempt_id, fence, deadline, orçamento, acl_epoch, corpus_snapshot e release_bundle_hash. `TaskEnvelope`/`ResultEnvelope` incluem task_id, parent_id, purpose, schema_version e validade; resultados duplicados são idempotentes. Propostas ficam em armazenamento privado por execução. Checkpoints são isolados e cifrados quando persistidos; só handles e progresso seguro entram em eventos públicos ADK. Filhos recebem apenas evidência e ferramentas permitidas.

DeepAgents é uma biblioteca separada que usa [runtime LangGraph](https://docs.langchain.com/oss/python/deepagents/overview); a ponte TaskPort será uma integração nossa. Checkpoint interno não substitui o ledger externo nem autoriza resultado atrasado. A versão escolhida terá planning explicitamente configurado quando necessário e ferramentas default revisadas para root, filhos e middleware. Todos reutilizam o mesmo ToolPort/ModelPort e budget, sem caminhos diretos a provedores.

## Contratos HTTP

| Endpoint planejado | Comportamento |
| --- | --- |
| POST /v1/requests | Aceita somente depois do commit; 202, ou 409 por conflito, 429 por quota e 503 se não pode persistir. |
| GET /v1/requests/{id} | Status e resultado terminal autorizado; objetos de outro tenant retornam resposta sem revelar existência. |
| GET /v1/requests/{id}/events | SSE com progresso redigido e terminal; sem candidato bruto; reautorização também no stream. |
| POST /v1/requests/{id}/cancel | Cancelamento idempotente por CAS; vencedor entre cancel e finalização é definido no ledger. |
| POST /v1/corpora/{id}/ingestions | Escopo de escrita; valida fonte/tamanho e agenda ingestão. |
| DELETE /v1/documents/{id} | Tombstone e epoch imediatos; exclusão física assíncrona, sem esperar reindex para negar leitura. |
| GET /health/live e /health/ready | Liveness sem dependências; readiness por papel, migrations e capacidade de aceitar/servir. |

Nenhum endpoint de execução aceita tenant, IAM role, destino de callback, URL arbitrária ou capacidade de commit decididos pelo modelo. Endpoint administrativo é separado e autenticado, não backdoor de recuperação.

## Persistência e recuperação

PostgreSQL guarda requests, jobs, attempts, budgets, audit_events, outbox, corpus_releases, document_versions/ACL e eval_reports, com chaves compostas e índices tenant-scoped. A mesma UnitOfWork cobre pedido, resultado e auditoria/outbox. Não manter uma transação aberta enquanto chama LLM, ferramenta, Qdrant ou filesystem.

Objetos grandes podem ser preparados por hash antes do commit, mas não ficam publicamente legíveis até existir uma referência autorizada commitada. Falha deixa objeto órfão que o reconciliador remove após janela segura. Qdrant, filesystem e broker não participam da transação SQL: indexação idempotente, manifests e reconciliação tratam efeitos parciais. O [transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html) evita uma gravação SQL desacoplada da intenção de publicar; duplicação na entrega ainda exige idempotência.

Ingestão cria coleção/snapshot candidato separado, grava manifest, executa EVAL e promove o bundle por CAS no catálogo SQL. Requests resolvem o ID imutável da coleção pelo catálogo, não dependem de alias mutável. Falha ou UNKNOWN mantém release ativo. Delete/tombstone e ACL são rechecados na recuperação, inferência, cache e entrega; rollback não revive permissões.

Índice vetorial não é fonte de verdade nem mecanismo de autorização autossuficiente. Qdrant suporta [partição por payload de tenant](https://qdrant.tech/documentation/manage-data/multitenancy/); nossa aplicação deve impor esse filtro e revalidar fontes. Tenant com exigência de isolamento maior pode usar coleção/shard ou instância dedicada, escolhido por necessidade medida, sem coleção automática por usuário.

Reconciliador retoma lease expirado com novo fence, encontra pedidos sem job acionável, outbox parada, corpus parcialmente indexado e tentativas sem terminal. Prazo e teto limitam retry com backoff/jitter. Queda de banco antes da aceitação retorna erro, não ACK otimista. Exatamente uma publicação terminal lógica vem de CAS/idempotência; não prometemos execução física exatamente uma vez nem recuperação após perda total de todas as réplicas e backups.

## Segurança e observabilidade

Cloud fica desativada por padrão. Vertex/Gemini exige identidade de workload, região/modelo fixados, autorização de finalidade, DLP/egress, reserva de custo e política de retenção verificada; usar Vertex não garante retenção zero. Segredos vêm de mecanismo externo/configuração protegida, não do repositório, prompt ou diretório de documentos. Não haverá fallback automático de local para cloud.

MCP remoto verifica issuer/audience/scopes; stdio executa apenas servidor aprovado com credenciais reduzidas. REST usa TLS, destinos fixos, limites de redirecionamento/DNS e proteção SSRF. Tool output e documentos são dados não confiáveis, não instruções. Timeout, 429, erro permanente e UNKNOWN têm tratamentos tipados e limitados. Custo de tentativas com erro também é contabilizado.

Logs JSON registram IDs, papel, etapa, versões, duração, decisão e erro redigido; prompt, token, candidato e documento não são logados por padrão. Traces são amostrados e exportados em fila limitada; auditoria mínima do terminal está na transação. Métricas mostram fila/idade, aceite versus terminais, latência p50/p95/p99, tokens/custo, retries, abstenção, recall e citações inválidas. request_id pertence a logs/traces, não a label de métrica. Retenção, acesso e espaço máximo são configurados.

## Operação local e evolução

O perfil local usa API e dados em loopback/rede privada de containers, bind mounts explícitos no D: para PostgreSQL/Qdrant e diretórios privados da aplicação. Fixar imagens e dependências por versão/digest após compatibilidade, nunca latest no deploy reprodutível. Configurar pool SQL e concorrência por papel, batches limitados de embeddings e backpressure para não exceder GPU/RAM. Não iniciar serviços neste planejamento.

API, request worker, ingest worker e eval worker têm entrypoints distintos na mesma base. Para uma demo, o worker pode também tratar ingestão com quotas por tipo; avaliação pesada fica fora do caminho online. Recursos GPU/CPU usam pools/processos com concorrência limitada; não bloquear o event loop com parser/modelo síncrono. ADK Web é [somente desenvolvimento e debugging](https://adk.dev/runtime/web-interface/), limitado a localhost e dados sintéticos. Seu facade aplica os mesmos gates de execução; ele não expõe diretamente a proposta interna do workflow.

Ao implementar servidores persistentes no Windows, seguir a skill persistent-local-server e supervisor externo ao Codex. Shutdown drena claims e cancela filhos; request permanece recuperável no ledger. Backups precisam de teste de restauração. Logs e backups somente no mesmo D: não protegem contra perda desse disco.

Para escala medida: replicar API stateless e workers, limitar recursos por tenant, usar broker durável, Postgres HA, object storage, observabilidade e vector/model serving dimensionados. Extrair um módulo para serviço apenas com gargalo, isolamento ou ciclo de entrega independente demonstrado. Definir workload por RPS, concorrência, tamanho de corpus/contexto, distribuição de tokens e SLO — não apenas por número de usuários. Os valores de capacidade das simulações anteriores não são benchmarks desta aplicação.

## Testes e entrega em etapas

| Gate | Evidência mínima na implementação |
| --- | --- |
| Fronteiras | Teste de imports: domínio/aplicação não dependem de SDK, HTTP, SQL ou filesystem concreto. |
| Unidade | Máquina de estados, budgets, schema, autorização, abstenção, citações e idempotência com fakes. |
| Contrato | Mesma suite para fake e cada port real; erros, cancelamento e saídas malformadas. |
| Integração | Postgres e Qdrant reais: crash entre commits, duplicação, leases, cancel versus finalize, outbox e tombstone. |
| Agentes | ADK e DeepAgents reais com candidato privado, caller correto, budget dos filhos e retomada fenced. |
| Segurança | Cross-tenant, ACL revogada, injection em documento/tool, SSRF, logs e streams sem dados privados. |
| Qualidade RAG | Gold/holdout em português; retrieval recall, rerank, precisão/cobertura de citações, abstenção e judge calibrado. |
| Operação | Restart, restore, collector indisponível, disco cheio, quota, overload e testes de carga reproduzíveis. |

Implementar por fatias verticais: primeiro aceitar/persistir/consultar um pedido com workflow fake; depois recuperação real; ADK e modelo; ingestão/EVAL; DeepAgents/MCP; cloud autorizada; por fim carga/HA conforme necessidade. Cada fatia inclui tests e tracing, não apenas conexão feliz ao SDK. DeepEval/LLM-as-a-Judge ficam na avaliação versionada de releases e regressões; verificação online continua obrigatória, mas não roda o dataset inteiro por pergunta.

As duas rodadas anteriores aprovam o workflow anterior no escopo registrado, não esta nova implementação ou este planejamento de módulos. A validação nova desta proposta é estrutural e documental. Na implementação, exigir duas rodadas adversariais consecutivas sem achados nos gates acordados, com controles negativos e limite declarado; isso não prova ausência universal de bugs.

## Fontes e alcance

[ADK graph workflows](https://adk.dev/graphs/) documenta combinação de funções determinísticas com raciocínio e roteamento explícito; nossa decisão é usá-los por trás de um port. [DeepAgents](https://docs.langchain.com/oss/python/deepagents/overview) documenta seu próprio runtime e capacidades; não oferece por si só a ponte com nosso ledger ADK. A organização de processos, módulos, ports e perfis acima é uma proposta do projeto, baseada nessas capacidades e no workflow revisado, não uma arquitetura oficial de um fornecedor.
