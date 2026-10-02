# Roteiro de defesa técnica

## Demonstração de cinco minutos

1. Mostrar examples/agent.json e explicar schema versionado/ordem obrigatória.
2. Transpilar com a CLI e abrir /artifacts/agent.py: funções e Workflow emitidos a partir do JSON, imports só Google ADK + runtime local.
3. Transpilar agent-variant.json: nome do agente e funções diferentes, mesmo contrato de segurança.
4. Executar request.png; conferir três nomes/códigos e recibo real da API.
5. Reexecutar com mesmo UUID: mesmo appointment_id; mudar o corpo: 409.
6. Executar unknown.png ou injection.png: erro não zero, sem recibo.
7. Mostrar /docs, suite de testes e limites honestos.

## Perguntas e respostas

**Por que não adicionar muitas camadas hexagonais?** Compiler, runtime/políticas e adaptadores MCP/API são fronteiras claras. Este problema pequeno ganha testabilidade sem hierarquia desnecessária de pastas; o RAG anterior tem outras exigências.

**Por que ADK sem Gemini?** O enunciado exige agentes ADK, não inferência remota. Workflow + Runner são o runtime efetivamente executado. OCR/catálogo/contratos são tarefas determinísticas. Isso facilita reprodução e custo zero de cloud, sem fingir interpretação aberta por LLM.

**Isso realmente transpila?** O JSON é parseado em IR tipada. Emitter gera módulo Python executável com um Workflow ADK, nomes das funções, edges e timeout. O runtime não ignora esse arquivo: confere seus bytes e o importa. Dois JSONs geram fontes diferentes e ambos são executados em testes.

**Por que não aceitar Python no JSON?** Transformaria a DSL em execução arbitrária. Allowlist reduz a superfície: apenas cinco tipos; identificadores estritos; sem URLs, eval ou ciclos enviados pelo usuário.

**Como os agentes/ferramentas se comunicam?** O retorno estruturado de cada nó vira a entrada do próximo. McpToolset estabelece sessão SSE, initialize/list/call, com filtro de ferramenta. HTTP só é usado para a API contratada. Os endpoints não vêm do JSON.

**Onde remover PII?** Dentro do servidor OCR, antes da saída da ferramenta entrar no ADK. Whitelist conservadora de exames; rótulos de nomes/documentos/contatos descartados; resto incerto bloqueia. O OCR bruto não é colocado em prompts, session state, API ou logs. Não é anonimizador universal.

**É RAG mesmo sem Qdrant?** Recupera uma fonte externa ao workflow, devolvendo identificadores e evidências verificáveis. O desafio permite catálogo mock; esta recuperação lexical canônica não é embedding nem busca híbrida. Produção poderia trocar o adaptador sem retirar gates de integridade.

**Como não duplicar depois de timeout?** UUID estável por solicitação, payload canônico, índice primário e transação. Retry reutiliza a chave. Nunca gera uma nova chave para esconder resultado desconhecido.

**Dois testes sem erros provam 100%?** Não. Provam apenas execução de casos definidos na mesma revisão, sobre hash congelado. Falhas de hardware, HA, dados manuscritos, produção pública e escala não foram garantidas.

**Por que SSE legado?** Exigência literal do PDF. A decisão de transporte fica explícita e não é apresentada como melhor default atual para qualquer sistema.

**O que falta para produção?** Identidade real, isolamento, TLS, filas/cotas, HA, backups testados, observabilidade, retenção, defesa de parser OCR e benchmarking. Não ampliar o desafio para clouds pagas ou processamento de dados médicos reais sem autoridade.

