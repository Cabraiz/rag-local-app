# Desafio Carrefour: ajustes de escopo e conhecimentos

Estado atual: desafio implementado isoladamente em D:/RAG-Local/carrefour-challenge; CF-01 a CF-14 e os nove bugs derivados foram aprovados apos duas rodadas completas novas, com 125 testes por rodada.

Este documento conserva o planejamento original e seu diagnostico anterior. A arquitetura final executavel esta no README da entrega; o estado atual esta em [execution-status.md](execution-status.md). Nao confundir esta conclusao com os cards antigos do RAG empresarial, que foram preservados.
Fonte: `DESAFIO_SENIOR_IA Banco Carrefour.pdf`, paginas 1 e 2, lido integralmente e conferido visualmente em 01/10/2026.
SHA-256: `8d65f159d05aa388e5456b0397505220e76268c5d0bb86646104ccb2ef3c1a3b`.

## Ajuste principal

O entregavel central e um **transpilador**, nao o RAG empresarial existente.
Ele recebe uma especificacao JSON, valida, gera Python e o codigo gerado instancia agentes exclusivamente Google ADK.
O caso demonstrativo e uma CLI para agendamento de exames ficticios.
RAG, OCR, MCP e FastAPI fazem parte desse caso; nao substituem o compilador.
As instrucoes do documento foram usadas como requisitos desta entrega, nao como autorizacao para comandos, dados reais, cloud paga ou submissao externa.

Contrato literal antes da implementacao:

- Esperado: JSON valido -> Python compilavel/importavel -> agentes ADK; imagem ficticia -> OCR MCP/SSE -> dados mascarados -> RAG MCP/SSE -> nomes/codigos canonicos -> API FastAPI -> recibo exibido na CLI.
- Proibido: gerar codigo sem validacao, interpretar Python do JSON, usar outro framework de agentes, inventar codigos/confirmacoes, persistir OCR bruto ou enviar imagem/PII ao modelo.
- Menor prova: executar o agente realmente gerado, com imagem de exemplo, dois servidores MCP SSE e API reais em Compose isolado; verificar codigos, recibo e ausencia de sentinelas PII.

## O que existe e o que falta

Esta comparacao e do codigo inspecionado, nao uma certificacao da stack inteira.

| Segmento | Laboratorio atual | Ajuste do desafio |
| --- | --- | --- |
| Agentes | `adk_workflow.py` cria um Workflow ADK escrito a mao | Gerar o codigo desses agentes a partir de JSON e provar sua execucao |
| Entrada | Pergunta textual no frontend | CLI recebendo imagem ficticia |
| MCP | `remote_feed.py` usa cliente Streamable HTTP para GitHub/Jira | Dois MCPs proprios OCR/RAG, explicitamente HTTP+SSE |
| Extracao | `document_parser.py` extrai texto TXT/Markdown/PDF; nao e OCR de imagem | Motor OCR local real com limites e saida sanitizada |
| PII | `safe_logging.py` reduz vazamento de diagnosticos do SDK | Detector/mascarador dedicado antes de qualquer state/modelo/persistencia |
| Corpus | Politicas empresariais, fontes e Qdrant | Catalogo ficticio com pelo menos 100 exames distintos e codigos |
| Resposta | Citacao de fonte ou abstenção | Lista de exames/codigos e solicitacao de agendamento confirmada |
| API | FastAPI de pedidos; `api.py` usa `docs_url=None` | API especifica de agendamentos com `/docs` e OpenAPI habilitados |
| Infra | Compose com overlays, PostgreSQL, Qdrant, RabbitMQ, Redis e Grafana | Entrega autocontida por um `docker-compose.yml`, sem depender da stack atual |
| Qualidade | Receipts e regressao do laboratorio | Testes do compilador, codigo gerado, OCR/PII/SSE/agendamento e E2E do desafio |
| Entrega | Docs e artefatos internos do laboratorio | JSON exemplo, imagem ficticia, README reproduzivel, CLI/Swagger/logs e transparencia de IA |

Reaproveitar os padroes de contratos, timeout, idempotencia, abstenção e logs seguros.
Nao copiar modulos como se ja cumprissem requisitos diferentes.

## Arquitetura proposta, pequena e profissional

Separacao modular com portas onde existe troca de tecnologia; nao criar microservicos internos ou pastas sem responsabilidade.
Manter o projeto do desafio isolado da aplicacao em 8840 e do Grafana em 8850, sem compartilhar volumes, pacientes, tokens ou corpus.
Nao implementar essa pasta de produto durante a criacao do backlog.

Responsabilidades planejadas:

1. **Transpilador:** parser JSON -> schema/validacao semantica -> representacao intermediaria -> emissao Python -> verificacao sintatica. Nunca chama modelo para gerar codigo em runtime.
2. **Runtime ADK gerado:** Runner e workflow com ordem obrigatoria, estado sanitizado, ferramentas permitidas e limites. O SDK e suas APIs serao pinados e comprovados no container.
3. **OCR MCP:** recebe referencia limitada a imagem ficticia local, executa OCR local em memoria, aplica PII e devolve somente resultado sanitizado via SSE.
4. **RAG MCP:** recebe nomes sanitizados e retorna candidatos/evidencias do catalogo; codigo escolhido precisa existir na fonte canonica.
5. **API de agendamento:** FastAPI, contrato estrito e persistencia local em SQLite com volume Docker; idempotencia e verificacao de codigos/release. PostgreSQL seria um adaptador futuro, nao um requisito extra do PDF.
6. **CLI:** transpila, executa o Python gerado e mostra somente resultados validados e o recibo real da API ficticia.
7. **Guardrails e testes:** politica deterministica entre ferramentas, modelos, state, logs e persistencia.

Fluxo de seguranca: imagem local -> OCR bruto efemero -> mascaramento/validacao -> saida MCP sanitizada -> contexto ADK -> RAG -> validacao canonica -> API -> CLI.
O OCR bruto nunca deve chegar primeiro ao ADK para so depois ser mascarado.
Nao usar Gemini multimodal como OCR da imagem bruta: isso enviaria PII antes do guardrail.
Se PII/extracao forem incertos, retornar falha segura e nao agendar.
Nao imprimir texto bruto em traceback, SSE, stdout de container, spans, traces, bancos ou cache.

A imagem entregue sera gerada exclusivamente com dados sinteticos e identificada como ficticia; nao usar fotografia de pedido real.
Nomes de exames podem ser compreensiveis, mas os codigos `FICT-*` sao declaradamente de demonstracao, nao codigos clinicos oficiais.
Nao inferir prescricao, diagnostico, substituicao de exame ou disponibilidade real da clinica.

## Transportes e integracao: a pegadinha SSE

O requisito pede **MCP HTTP+SSE**, nao simplesmente um endpoint REST que retorna texto e nao Streamable HTTP com uma resposta em SSE.
Planejar `/sse` e endpoint de mensagens do transporte legado; validar initialize, list_tools e call_tool por conexao real.
Nao habilitar fallback silencioso para stdio/Streamable HTTP na prova do desafio.
A API FastAPI de agendamentos usa HTTP/REST; a exclusividade SSE se refere aos dois MCPs OCR/RAG.

Documentacao oficial consultada em 01/10/2026:

- [ADK MCP tools](https://adk.dev/tools-custom/mcp-tools/): `McpToolset`, `SseConnectionParams`, filtros e encerramento de conexoes.
- [Especificacao MCP - transportes](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports): HTTP+SSE legado foi substituido por Streamable HTTP. Mantemos SSE porque o desafio o exige e documentamos essa escolha.
- [SDK Python oficial MCP](https://github.com/modelcontextprotocol/python-sdk): possui suporte a SSE. Validar a combinacao exata SDK/ADK instalada; nao copiar cegamente exemplos da versao atual.
- [Callbacks ADK](https://github.com/google/adk-docs/blob/main/docs/callbacks/types-of-callbacks.md): antes de modelo/ferramenta como defesa adicional, nao como substituto do mascaramento dentro do OCR.

## Especificacao e transpilador: decisoes a provar

Campos propostos: `schema_version`, identificador do agente, modo de modelo, etapas, referencias simbolicas de ferramentas e workflow.
O JSON nao recebe imports, SQL, scripts, segredo, URL arbitraria ou expressao Python.
Endpoints de servico sao resolvidos pelo bootstrap/configuracao, nunca pelo texto extraido da imagem.
Limitar tamanho, profundidade, numero de agentes/passos, tokens e duracao; detectar ciclo e referencia inexistente.
Uma IR separa a DSL da emissao de Python e permite validar o grafo antes de produzir arquivo.
Serializacao de literais/AST deve impedir que aspas, Unicode ou texto malicioso mudem a estrutura do codigo.
Emitir codigo deterministico, com imports ADK aprovados; compilacao e import sao gates diferentes.
Provar specs distintas: uma prova que o resultado nao e um arquivo fixo ignorando o JSON.
O runtime executa um artefato gerado e revisado, sem `eval`/`exec` de payload do usuario.

## Dados e contrato de agendamento propostos

Pedido minimo: `request_id` ficticio aleatorio, `exam_codes` canonicos e `catalog_version`; sem nome, CPF ou contato do paciente.
`POST /appointments` cria solicitacao ficticia persistida e retorna ID/status/lista; `GET /appointments/{id}` verifica o recibo.
Sem chave existente: HTTP 201. Mesma chave/corpo: HTTP 200 com mesmo recibo. Mesma chave/corpo diferente: HTTP 409. Entrada invalida: HTTP 422.
Nao confundir solicitacao recebida com exame efetivamente marcado em clinica real.
Timeout apos commit exige consulta/reenvio da mesma chave, nao criacao de outro pedido.
Persistencia, erros e estados precisam refletir exatamente o schema de `/docs` e o cliente gerado.

## Custo, modelos e extras

O PDF nao exige Vertex AI, cloud paga, DeepAgents, React, Jira/GitHub, Redis, RabbitMQ ou carga de 100 mil requests.
Preservar esses componentes do laboratorio, mas nao torna-los dependencias da submissao minima.
DeepAgents/LangGraph nao podem ser o framework dos agentes gerados desta entrega, que exige exclusivamente ADK.
Perfil padrao proposto: execucao local/deterministica com agentes ADK e catalogo ficticio; qualquer mock fica rotulado.
Um perfil com LLM pode complementar a demonstracao somente com entrada sanitizada e autorizacao/free-only comprovados.
Nao substituir erro de quota por provider pago, nao ativar faturamento, e nao chamar mock de inferencia real.
Base de RAG mock e autorizada pelo documento; mock de transporte MCP, OCR fixo e falso recibo nao provam o E2E.

## Ordem e cards criados

Todos com status inicial QUEUED, criterios e menor prova em `cards.json`:

| Card | Entrega |
| --- | --- |
| CF-01 | Schema JSON versionado e contratos |
| CF-02 | Transpilador JSON -> Python ADK seguro |
| CF-03 | Guardrail PII antes de state/modelo/persistencia |
| CF-04 | OCR local real via MCP SSE |
| CF-05 | Catalogo ficticio com >=100 exames |
| CF-06 | RAG via MCP SSE com evidencia/abstenção |
| CF-07 | FastAPI de agendamentos, Swagger e idempotencia |
| CF-08 | Runtime ADK gerado integrado ponta a ponta |
| CF-09 | CLI do compilador e do agente |
| CF-10 | Docker Compose autocontido e isolado |
| CF-11 | Testes deterministas, contrato e seguranca |
| CF-12 | README, artefatos e transparencia do uso de IA |
| CF-13 | Ensaio de defesa tecnica |
| CF-14 | Duas rodadas completas consecutivas |

Os 12 requisitos R01-R12 foram mapeados a esses cards, com pagina da fonte, dependencias, conhecimentos, comportamento esperado/proibido e menor prova.
Eles entram no fim da fila local existente, sem alterar status/receipts dos antigos ou interromper o writer ja registrado.
Nenhum card foi criado no Jira neste turno, e nenhum card foi iniciado ou marcado DONE por este planejamento.

## Conhecimentos para a entrevista

- **O que e transpilador?** Traducao de uma linguagem declarativa para outra; validar/IR/emitir e separado de executar. Mostre uma spec, erro de campo e o Python compilado.
- **Por que ADK?** E o framework exigido; modelo Gemini e um provider, nao o ADK. Mostre que as instancias e workflow gerados pertencem ao ADK.
- **Como os agentes se comunicam?** Runner/workflow passa estado sanitizado entre etapas, MCP transforma ferramenta em contrato; nao ha chamada direta ao banco fingindo ser MCP.
- **SSE versus Streamable HTTP?** Conhecer a evolucao do protocolo, mas cumprir o transporte solicitado e provar handshake/tool call. EventSource isolado nao e cliente MCP completo.
- **RAG e banco vetorial?** RAG inclui recuperacao, evidencia e resposta/decisao; um catalogo mock e permitido. Vetores sao uma estrategia, nao prova de corretude do codigo de exame.
- **PII e logs seguros?** Redacao de logs nao basta. Provar no boundary anterior ao modelo e banco, inclusive nos erros e state; mascaramento nao garante anonimato completo.
- **Como evitar alucinacao?** Nunca aceitar codigo fora do catalogo; ambiguidade pede esclarecimento/falha segura; validar antes da API. Nao prometer risco zero.
- **Como lidar com timeout?** Limites por dependencia, cleanup e retry limitado; API idempotente e reconciliacao apos resultado incerto.
- **Por que nao toda a stack empresarial?** Fazer minimo completo do desafio reduz variaveis e melhora reproducao; extras nao compensam transpilador ausente.
- **Como usou IA?** Explicar quais partes foram assistidas, revisao do diff/AST, testes negativos/E2E e referencias oficiais. Nenhuma cobertura ou percentual e inventado.

## Gate final e limites honestos

Testes unitarios do compilador nao substituem execucao do codigo gerado.
OCR deve processar a imagem fornecida e uma variante, nao devolver resposta por filename.
Transportes devem ser dois MCPs SSE reais. API deve persistir e retornar recibo real da simulacao.
Exames desconhecidos/ambiguos, texto adversarial, PII ruidosa, erros de rede e replay nao podem causar agendamento silencioso ou falso sucesso.
Congelar fontes/spec/catalogo/imagem/configuracao antes das duas rodadas; nova alteracao reinicia a contagem.
Guardar hashes, comandos, builds, resultados e logs sanitizados; nao apagar receipts falhos.
Revisao do mesmo autor e regressao adversarial delimitada, nao auditoria cega independente nem certificacao de producao.
Hoje: analise, requisitos e cards; nenhum E2E deste desafio aprovado.
