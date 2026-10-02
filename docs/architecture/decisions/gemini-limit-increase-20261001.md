# Limite local Gemini — alteração autorizada em 01/10/2026

Pedido: aumentar bastante o limite do laboratório, sem gasto monetário.

Antes da primeira escrita de código:
- Esperado: 2 -> 1.000 tentativas por dia UTC (500 vezes maior).
- Preservar o contador SQLite real, inclusive tentativas com falha. Não zerar,
  recriar volume, mudar relógio ou usar outro projeto para obter tentativas.
- Proibido: alterar cota/plano/faturamento Google, ativar Vertex, fallback pago,
  retries automáticos, chamadas periódicas ou conectar Gemini ao RAG nesta mudança.
- Menor prova: duas rodadas offline de reserva concorrente perto de 1.000,
  bloqueio da tentativa 1.001, retomada de um contador com duas tentativas e
  contabilização de falhas; contratos SDK/ADK preservados e imagem atualizada.

É teto de tentativas manuais locais, não meta de consumo nem cota Google.
Mantidos: opt-in sintético, uma chamada por execução, 32 tokens de saída,
timeouts, sem ferramentas e sem retry/fallback. A cota Free do Google pode ser
menor. Uma flag local não verifica nem impede mudanças de faturamento na conta;
confirmar Free sem billing antes de cada lote real. Nesta mudança, nenhum lote
real será disparado e nenhuma cobrança será configurada.

Contratos anteriores com duas tentativas documentam a política histórica,
substituída somente quanto ao teto diário por esta autorização.

## Resultado verificado

Teto novo presente na imagem `rag-local-backend:0.1.0`. Contador real consultado
somente leitura em 01/10/2026 UTC: 2 tentativas usadas, 998 disponíveis no limite
local. Não houve reserva/reset nem chamada Gemini durante esta alteração.

Host e container Python 3.12 sem rede: 17 checks de controle + 18 checks SDK/ADK,
em duas rodadas de cada suíte. Os 17 checks incluem reserva concorrente próxima
do teto, rejeição da tentativa 1.001, persistência e continuidade de 2 para 3 em
fixture sintética. Os 18 checks verificam endpoint, Vertex off, modelo, saída de
32 tokens, uma chamada por execução e ausência de retry/tools. Runner é falso:
estes checks não provam conectividade/inferência Gemini.

Provas: `eval/runs/gemini-limit-20261001/controls-host.json`,
`sdk-host.json`, `controls-container.json`, `sdk-container.json`,
`runtime-counter.json` e `build.log`.

Aplicação local continua respondendo a `/health/ready`, modo lab e workflow
`adk_extractive_graph`. Nenhum servidor foi encerrado/recriado. O erro remoto
400 anterior e a integração de geração Gemini no RAG continuam pendentes; esta
entrega modifica apenas o limite autorizado e verifica os controles existentes.
