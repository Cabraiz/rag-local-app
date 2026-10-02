# Desafio Senior IA — transpilador JSON → Google ADK

Entrega isolada do laboratório RAG. Toda a clínica, nomes, documentos, códigos e agendamentos são fictícios. O resultado não é diagnóstico nem agendamento em clínica real.

## Executar somente em Docker

Pré-requisito: Docker Engine com Compose v2. No PowerShell, nesta pasta:

```powershell
docker compose build api
docker compose up -d api ocr rag
docker compose run --rm runner python -m clinic_adk.cli transpile
docker compose run --rm runner python -m clinic_adk.cli run --image request.png
```

Swagger local: http://127.0.0.1:8860/docs . Saúde: http://127.0.0.1:8860/health .

O runner imprime JSON com nomes/códigos, evidências, etapas e recibo `REQUESTED`. Use `--request-id <UUID>` ao executar para manter idempotência no reenvio. Um novo UUID é um pedido novo. A API recebe códigos canônicos, nunca dados de paciente.

Segundo exemplo, com outro nome de agente, outras funções geradas e outros exames:

```powershell
docker compose run --rm runner python -m clinic_adk.cli transpile --spec /app/examples/agent-variant.json --output variant.py
docker compose run --rm runner python -m clinic_adk.cli run --spec /app/examples/agent-variant.json --agent variant.py --image variant.png
docker compose run --rm tests
```

Os arquivos Python gerados ficam no volume Docker `artifacts`, em /artifacts. O SQLite de recibos fica no volume `appointments`, não na camada descartável do container. Todas as etapas da solução, inclusive CLI e testes, rodam em container. Não há dependência de Python instalado no host.

Para testar outra imagem fictícia, coloque PNG/JPEG em examples e passe o nome em --image. O servidor OCR lê essa pasta por mount somente leitura; não aceita URLs, caminhos fora de /samples nem imagens grandes. Reconhecimento incerto é bloqueado.

Para duas rodadas completas, incluindo interrupções reais dos serviços isolados:

~~~powershell
.\tools\verify.ps1 -Evidence D:\RAG-Local\eval\runs\carrefour-minha-rodada
~~~

Escolha uma pasta de evidência nova. O script interrompe somente os serviços deste desafio, preserva o RAG/Grafana e cria uma segunda instância temporária na porta 8861. Ao terminar, remove apenas os containers/rede temporários, preservando seus volumes. Não execute o gate durante uma demonstração ativa desta clínica.

## Arquitetura e comunicação

JSON → validação tipada/IR → emissão determinística de Python → validação AST/compile → importação do artefato conferido → Google ADK Workflow/Runner.

O workflow gerado executa cinco nós explícitos:

1. OCR: ferramenta `extract_exams`, descoberta e invocada pelo cliente MCP do ADK, via HTTP+SSE.
2. Recuperação: ferramenta `lookup_exams`, em outro servidor MCP HTTP+SSE.
3. Validação: compara nomes/códigos/evidência/versão com o catálogo autorizado.
4. Agendamento: POST FastAPI /appointments, UUID idempotente e recibo persistido.
5. Formatação: apresenta somente a confirmação realmente retornada pela API.

Os nós se comunicam pelo valor estruturado retornado pelo nó anterior. A API não é invocada se a evidência for incompleta. Há allowlist de ferramentas, endpoints fixos, timeouts e fechamento das sessões MCP.

Usamos MCP SSE legado para cumprir o enunciado, não stdio nem Streamable HTTP. Os MCPs são acessíveis apenas na rede interna do Compose; a única porta do host é a API em loopback. SSE legado não é recomendação genérica para projetos novos.

## Compilador, não código remoto

A DSL permite apenas schema_version=1, google-adk, sse, offline e os cinco tipos de etapa na ordem obrigatória. Os nomes do agente/nós e timeout são configuráveis; o segundo JSON comprova mudança material na emissão. Não aceitamos Python, prompts arbitrários, URLs, imports, ciclos ou execução de shell no JSON. Campos extras e chaves duplicadas são rejeitados.

O compilador usa Pydantic, representação intermediária validada e um emitter determinístico. Não chama LLM para gerar código. O arquivo emitido contém agentes exclusivamente Google ADK. Helpers de OCR/API/políticas permanecem no runtime; sua existência não substitui a execução do agente gerado.

A execução lê somente o tamanho esperado do artefato e confere igualdade exata com a emissão. Executa esses mesmos bytes verificados em memória, sem reler o caminho após a validação: substituir o arquivo nesse intervalo não injeta código. Isso não é sandbox para Python arbitrário; apenas a saída exata da DSL é executável.

No ADK 2.10, o grafo Workflow é um BaseNode, executado por Runner(node=...). Ele não é uma instância do BaseAgent legado. A validação de execução confere o tipo Workflow do próprio ADK, não aceita uma classe substituta.

## Privacidade e segurança

Tesseract executa OCR local; texto bruto existe apenas em memória nessa fronteira. Antes de retornar ao ADK, o guardrail descarta linhas de paciente/médico/documentos/contatos e deixa passar apenas nomes canônicos de exames permitidos. Texto desconhecido, instrução maliciosa ou PII em linha de exame bloqueia o fluxo inteiro.

Esta política conservadora não equivale a detector universal de PII nem certificação LGPD. Imagens manuscritas não são cobertas; reconhecimento incerto abstém-se. Não alimente esta demonstração com documentos reais. A imagem inclui sentinelas fictícias para comprovar que elas não entram em estado, recibos ou logs da aplicação.

Containers: usuário sem privilégios, filesystem somente leitura, capabilities removidas, temporários limitados e limites de memória. Nenhum segredo cloud é montado. A rede interna e o modo offline evitam chamadas a Gemini/Vertex ou gastos de inferência. Usar ADK não obriga usar um LLM: aqui o workflow é determinístico.

A API também participa de uma rede de entrada bridge, necessária para publicar a porta loopback no Docker Desktop. OCR, RAG e runner permanecem apenas na rede interna. A API não tem código de inferência nem credenciais; sua rede de entrada não equivale a uma firewall de saída para produção. O acesso host é verificado além da saúde interna do Docker.

Nomes de campos desconhecidos também são tratados como dados não confiáveis: erros não refletem seus nomes/valores. O MCP rejeita ferramentas/parâmetros fora do contrato antes da validação do SDK. A API limita corpo a 4096 bytes e rejeita JSON com chaves duplicadas. Em timeout do agendamento, o CLI conserva o request_id e informa resultado desconhecido; o reenvio deve usar a mesma chave.

## Catálogo e API

120 nomes distintos com códigos FICT-001…FICT-120. Códigos são inventados, não TUSS/SUS. O catálogo JSON é o armazenamento mock permitido pelo enunciado, com recuperação lexical/aliases canônicos e evidência versionada SHA-256. Não alegamos busca vetorial/neural neste módulo. O Qdrant/PostgreSQL do RAG anterior continuam separados.

A API representa uma solicitação fictícia, não a reserva de uma data médica. Campos exatos estão em OpenAPI; adicionais são rejeitados. Mesma chave + mesmo conteúdo retorna o recibo original; mesmo UUID + conteúdo diferente retorna 409. Falha/timeout nunca vira sucesso inventado.

As dependências Python transitivas estão fixadas em requirements.lock e são verificadas com pip check no build. A base Python usa digest. Pacotes do sistema operacional vêm do repositório Debian no build; não alegamos rebuild byte a byte nem auditoria de todas as dependências.

## Avaliação

### Playwright: Swagger real, sem respostas simuladas

`tools/verify.ps1` também constrói e executa o serviço `browser` nas duas rodadas.
Chromium acessa a API real, com qualquer origem externa bloqueada e contextos novos
por caso. São 18 cenários: abertura offline, schema, exemplo executável, criação,
replay, conflito, consulta pelos dois identificadores, recibo do CLI real,
PII, JSON duplicado, limite de corpo, exames inválidos, catálogo desatualizado,
UUID inválido e viewport móvel. Traces, screenshots e JSON ficam em `browser-1/2`.
MCP/OCR/CLI são comprovados pelos testes Python e falhas reais; uma captura de
Swagger não substitui essas provas. O exemplo cria apenas uma solicitação fictícia.

Os assets Swagger UI 5.33.1 são servidos pela própria API, com licença incluída;
não há CDN, validador externo ou chamada a Gemini. Os lockfiles npm verificam
integridade dos pacotes no build. Playwright 1.62.1 combina com a imagem Chromium
fixada por digest. Internet é necessária para baixar dependências no build inicial,
não para executar a solução ou os testes. `/redoc` não é publicado para evitar
outra dependência externa; `/docs` continua sendo a interface exigida.

Para executar a mesma bateria manualmente, obtenha um request_id de uma execução
CLI bem-sucedida e persistida, e use (na pasta do desafio, PowerShell):

```powershell
docker compose build browser
New-Item -ItemType Directory -Force evidence/browser | Out-Null
docker compose run --rm --no-deps -v "${PWD}/evidence/browser:/evidence" -e "CLINIC_CLI_REQUEST_ID=UUID_DO_RECIBO_CLI" browser
```

O caso de reconciliação CLI não é pulado se faltar esse ID; falha explicitamente.
Para a aprovação integral, prefira `tools/verify.ps1`, que obtém o recibo sozinho.

Os casos estão em `docs/use-cases.json`: oito componentes e três perspectivas
(usuário, desenvolvedor e atacante), totalizando 24 casos declarados com resultado
esperado, comportamento proibido e testes vinculados. Todos os 120 exames e seus
aliases têm prova de recuperação e extração. `tools/check_use_cases.py` exige que
cada caso apareça aprovado no JUnit real: documentação sozinha não aprova o gate.

As novas baterias `test_discovery_boundaries.py`, `test_discovery_outcomes.py`,
`test_persona_use_cases.py` e `test_infrastructure_boundaries.py` cobrem banco
indisponível/corrompido, JSON ambíguo, correlação, arquivos especiais, contratos,
PII legítima, limites, concorrência e isolamento. Antes de corrigir, falhas foram
reproduzidas e registradas. Qualquer edição invalida a sequência verde. As duas
rodadas usam sementes e ordens diferentes, e a segunda usa projeto/volumes novos.
Isso é busca adversarial da mesma autoria, não auditoria cega independente nem
garantia de cobrir toda entrada ou vulnerabilidade possível.

Cabeçalhos pessoais reconhecidos são descartados antes de avaliar instruções:
um nome/contato legítimo não bloqueia exames válidos. Instruções no corpo dos
exames, evidência incompleta e códigos desconhecidos continuam bloqueados.
Leituras de arquivo são limitadas no descritor aberto, rejeitando pipes/devices;
a emissão troca o artefato atomicamente. Recibos salvos também são validados.
HTTP 408/5xx preserva resultado desconhecido e orienta reconciliação pela mesma
chave, sem fabricar sucesso ou declarar rejeição definitiva.

`tests/test_unit.py`: schema/compiler/AST/import, fuzzing limitado, injeção, PII, evidências, caminhos, retry idempotente e contrato OpenAPI.

`tests/test_integration.py`: OCR em imagens reais fictícias, handshake/list/call MCP SSE real, execução CLI do Python gerado, API persistente, duplicatas concorrentes, dados desconhecidos e ausência de agendamento em falhas.

`tools/verify.ps1`: orquestra rodadas novas e testes de interrupção/reinício dos serviços isolados; evidências são geradas em pasta própria. Duas rodadas verdes sobre os mesmos hashes são regressão repetida no escopo, não auditoria independente nem prova de ausência de todos os bugs. Resultado real e limites ficam no relatório de execução.

## Transparência sobre IA e decisões

Codex auxiliou leitura dos requisitos, desenho, implementação, casos adversariais e documentação. As provas precisam ser execução real; não substituímos falhas por screenshots, mocks de MCP ou alteração dos resultados esperados. API e catálogo são fictícios porque o desafio permite, mas OCR, transporte, ADK, contratos e persistência são reais. Histórico de falhas/correções fica junto das evidências.

Veja [defesa técnica](docs/defesa-tecnica.md) e [matriz de requisitos](docs/matriz-requisitos.md). Nenhum commit, push ou submissão foi feito automaticamente.

Referências primárias:
- [ADK workflows](https://adk.dev/graphs/)
- [ADK MCP tools](https://adk.dev/tools-custom/mcp-tools/)
- [MCP HTTP+SSE 2024-11-05](https://modelcontextprotocol.io/specification/2024-11-05/basic/transports)
- [FastAPI](https://fastapi.tiangolo.com/)
- [FastAPI: Swagger com assets locais](https://fastapi.tiangolo.com/how-to/custom-docs-ui-assets/)
- [Playwright: interceptação e bloqueio de origens](https://playwright.dev/docs/api/class-page#page-route)
- [Tesseract](https://tesseract-ocr.github.io/tessdoc/)
- [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/)

## Limites para produção

Este desafio não é o RAG multiempresa anterior nem um serviço de produção com 100 mil requisições demonstradas. Expor publicamente exigiria autenticação/autorização, TLS, retenção/consentimento, gestão de segredos, filas/quotas, HA/backups/restauração, telemetria e carga medida. SQLite atende o mock local, não HA distribuído. A rede/timeout não prova segurança clínica. Os containers continuam sob supervisão do Docker com restart unless-stopped enquanto o computador/daemon estiverem ligados.
