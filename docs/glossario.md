# Glossário

**O que tem aqui / quando ler.** Os termos que aparecem no código, na saída do terminal e nos outros documentos, cada um
em uma ou duas frases, com o lugar onde ele mora. Leia quando um termo não for óbvio; os documentos linkam para cá.

## Leitura e decisão de cada exame

| Termo | O que é | Onde mora |
|---|---|---|
| **confiança** | O menor de três valores, de 0 a 1: a nota da busca no catálogo, o quanto a busca bate com a linha lida e a leitura que o OCR deu à linha. | `runtime/confianca.py` |
| **faixas de confiança** | ≥ 0,90: o exame entra na lista sem aviso (com `--yes`, agenda sozinho). De 0,70 a 0,90: entra com aviso (`confira`); com `--yes`, fica de fora. Abaixo de 0,70: não é agendado, só avisado como `baixa confiança`. Esta é a referência das faixas; os outros documentos linkam para cá. | `BookingPolicy.min_confidence` (0,90) e `ask_from` (0,70) em `runtime/confianca.py` |
| **piso de leitura** | A leitura mínima que o OCR precisa dar à linha para o exame valer: 75 numa linha comum, 85 numa sigla curta e 95 num sinônimo. | `BookingPolicy` em `runtime/confianca.py` |
| **trecho próprio** | Cada exame agendado precisa de um pedaço da linha só dele; "Hemoglobina" escrita uma vez, dentro de "Hemoglobina glicada", vale um exame, não dois. | `confianca.best_spot` |
| **`confira`** | Aviso na lista: o exame entra, mas a pessoa deve conferir no papel antes de responder `s`. | `runtime/relatorio.py` (`WHY`) |
| **`partial`** | Resultado da busca que é só parte do que a linha escreve; nem entra na lista nem é perguntado, só avisado. | `mcp_servers/rag.py` |
| **sem estado** | Nas medições: um exame que não foi agendado, nem perguntado, nem avisado. | [medicoes.md](medicoes.md#resumo) |

## Resposta do OCR

| Termo | O que é | Onde mora |
|---|---|---|
| **`line_intent`** | O que cada linha lida pede: `request` (pede o exame), `negated` (diz para não fazer), `history` (já feito), `uncertain` (tem outras palavras além do exame), `prep` (preparo), `table`, `form`. | `guardrails/intent.py` (`line_kind`, `read_page`) |
| **`page_clean`** | Verdadeiro quando a página é só a lista de exames (mais rótulos, campos e cabeçalho reconhecidos). Só numa página limpa um exame pode agendar sozinho com `--yes`. | `guardrails/intent.py` (`clean_page`) |
| **`off_list`** | As linhas que fazem a página não ser limpa; a saída as mostra uma vez, no topo. | `guardrails/intent.py` |
| **`[TEXTO_REMOVIDO]`, `[NOME]`, `[CPF]`…** | Marcadores da máscara: o texto original não sai do container `ocr`. | `guardrails/pii.py`, `guardrails/pii_rules.py` |
| **`OcrReading`** | O contrato da resposta do OCR, versionado. | `leitura.py` |

## Agente, spec e serviços

| Termo | O que é | Onde mora |
|---|---|---|
| **spec** | O JSON que descreve o agente: servidores, agentes, workflow e plugins. | `specs/`, [transpilador.md](transpilador.md#campos-da-spec) |
| **transpilar** | Gerar o `agent.py` a partir da spec, validando antes e importando o arquivo antes de trocá-lo. | `transpiler/` |
| **`BookingPlugin`** | O plugin do ADK com a política de agendamento; o `agent.py` só o declara. | `runtime/plugin.py` |
| **`tool_filter`** | A lista de ferramentas que cada agente enxerga num servidor MCP. | spec e `agent.py` gerado |
| **`output_key`** | A chave de estado em que cada agente deixa a sua saída para o próximo. | spec |
| **MCP via SSE** | O protocolo e o transporte com que o agente chama o OCR e o RAG. | `mcp_servers/` |
| **`ALLOWED_HOSTS`** | Os únicos hosts que o agente e o transpilador aceitam como servidores. | `runtime/rede.py` |
| **`--yes`** | Pula a confirmação, para automação; só as regras decidem e o código de saída é 3 quando sobra exame sem decisão. | `cli.py` |
| **`Idempotency-Key`** | A chave que impede que a mesma chamada agende duas vezes; vem do runtime, nunca do modelo. | `api/main.py` |
| **`FICT-xxx`** | Os códigos fictícios dos 120 exames do catálogo. | `data/exams.json` |
