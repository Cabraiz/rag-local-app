# Glossário

**O que tem aqui / quando ler.** Os termos que aparecem no código, na saída do terminal e nos outros documentos, cada um
em uma ou duas frases, com o lugar onde ele mora. Leia quando um termo não for óbvio; os documentos linkam para cá.

## Leitura e decisão de cada exame

| Termo | O que é | Onde mora |
|---|---|---|
| **confiança** | O menor de três valores, de 0 a 1: a nota da busca no catálogo, o quanto a busca bate com a linha lida e a leitura que o OCR deu à linha. | `runtime/confianca.py` |
| **faixas de confiança** | As três faixas que decidem o que acontece com o exame pela confiança ([valores](#faixas-e-pisos)). | `BookingPolicy.min_confidence` (0,90) e `ask_from` (0,70) em `runtime/confianca.py` |
| **piso de leitura** | A leitura mínima (0 a 100) que o OCR precisa dar à linha para o exame agendar sozinho ([valores](#faixas-e-pisos)). | `BookingPolicy` em `runtime/confianca.py` |
| **trecho próprio** | Cada exame agendado precisa de um pedaço da linha só dele; "Hemoglobina" escrita uma vez, dentro de "Hemoglobina glicada", vale um exame, não dois. | `confianca.best_spot` |
| **`confira`** | Aviso na lista: o exame entra, mas a pessoa deve conferir no papel antes de responder `s`. | `runtime/relatorio.py` (`WHY`) |
| **`partial`** | Resultado da busca que é só parte do que a linha escreve; nem entra na lista nem é perguntado, só avisado. | `mcp_servers/rag.py` |
| **sem estado** | Nas medições: um exame que não foi agendado, nem perguntado, nem avisado. | [medicoes.md](medicoes.md#como-ler) |

### Faixas e pisos

As faixas de confiança:

- **0,90 ou mais:** o exame entra na lista sem aviso. Com [`--yes`](#agente-spec-e-serviços), agenda sozinho.
- **De 0,70 até menos de 0,90:** entra com o aviso `confira`. Com `--yes`, fica de fora.
- **Abaixo de 0,70:** não é agendado, só avisado como `baixa confiança`.

Os pisos de leitura:

- **75** numa linha comum;
- **85** quando o nome lido ou o do exame tem até 3 letras;
- **95** quando o nome lido tem até 3 letras e é a abreviação de um nome mais longo. Exemplo: "TGP" lido "TAP" seria
  Tempo de protrombina.

As mensagens de cada faixa e a calibração: [referencia.md](referencia.md#agendamento-conferido-em-código).

## Resposta do OCR

| Termo | O que é | Onde mora |
|---|---|---|
| **`line_intent`** | O que cada linha lida pede ([os 8 valores](#os-valores-de-line_intent)). | `guardrails/intent.py` (`line_kind`, `read_page`) |
| **`page_clean`** | Verdadeiro quando a página é só a lista de exames, com rótulos, campos e cabeçalho reconhecidos ([o efeito](regras.md#regras-que-só-decidem-com---yes)). | `guardrails/intent.py` (`clean_page`) |
| **`off_list`** | As linhas que fazem a página não ser limpa; a saída as mostra uma vez, no topo. | `guardrails/intent.py` |
| **`[TEXTO_REMOVIDO]`, `[NOME]`, `[CPF]`…** | Marcadores da máscara: o texto original não sai do container `ocr`. | `guardrails/pii.py`, `guardrails/pii_rules.py` |
| **`OcrReading`** | O contrato da resposta do OCR, versionado. | `leitura.py` |
| **pista** | A palavra ou marca que nega, dá como feito ou suspende um exame: "não realizar", "NR", "já realizado", "suspenso". | `guardrails/intent.py` (`CUES`) |
| **contestado** (`contested_exams`) | Um exame que alguma linha da página nega ou dá como feito, ou que aparece numa ordem ao modelo que a máscara tirou ([o efeito](regras.md#regras-que-valem-sempre)). | `leitura.py`, `guardrails/intent.py` (`contested`) |
| **`cancel_unlinked`** | Verdadeiro quando uma pista não se liga a nenhum exame. Nada na página agenda sozinho. | `leitura.py` |
| **`exam_lines`** | As únicas linhas que o modelo lê. As outras chegam a ele como `[linha de texto livre omitida]`. | `mcp_servers/ocr.py` |
| **`line_used`** | Motivo de um exame ficar de fora: o trecho da linha que o nomeia já é de outro exame. | `runtime/confianca.py` (`sort_out`) |

### Os valores de `line_intent`

- `request`: pede o exame.
- `negated`: diz para não fazer.
- `history`: diz que já foi feito.
- `uncertain`: tem outras palavras além do exame.
- `prep`: é preparo, como o jejum.
- `unrecognized`: é um item da lista que a máscara tirou inteiro.
- `table`: é uma linha de tabela.
- `form`: é uma linha de formulário com marcas.

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
| **`--yes`** | Pula a confirmação, para automação ([efeitos](como-rodar.md#3-executar-o-agente), [código 3](referencia.md#códigos-de-saída)). | `cli.py` |
| **`Idempotency-Key`** | A chave que impede que a mesma chamada agende duas vezes; vem do runtime, nunca do modelo. | `runtime/pedido.py` (a chave), `api/main.py` (a checagem) |
| **`FICT-xxx`** | Os códigos fictícios dos 120 exames do catálogo. | `data/exams.json` |
