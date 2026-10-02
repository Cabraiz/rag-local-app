# Conexão Gemini gratuita — contrato antes da implementação

**Atualização autorizada em 01/10/2026:** teto local aumentado para 1.000
tentativas por dia UTC, sem zerar contador nem alterar cotas/faturamento Google.
Veja [contrato atual](../../architecture/decisions/gemini-limit-increase-20261001.md). Os limites de duas
tentativas citados abaixo são históricos. Todos os outros controles permanecem.

Esperado: usar exclusivamente chave substituta do projeto
`gen-lang-client-0580698701`, confirmada Free e sem billing, dentro do backend
Python/ADK em container. Segredo somente por arquivo montado em `/run/secrets`;
no host, `.local/secrets` com ACL do usuário e fora de Git/build context.

Primeira prova remota é um probe sintético isolado, não geração de respostas RAG.
Modelo fixo `gemini-2.5-flash-lite`, endpoint Developer API, Vertex explicitamente
desligado, sem tools, grounding, imagens, cache pago ou fallback. No máximo uma
chamada LLM por execução e duas tentativas por dia UTC, reservadas antes de I/O em
contador SQLite persistente. Falha/timeout também consome tentativa. Nenhum retry
automático. Timeout de HTTP e timeout total. Saída apenas marcador de resultado e
classe/código numérico de erro; nunca segredo, prompt ou mensagem bruta do SDK.

Proibido: vincular billing, usar chave anterior exposta, implementar cap USD 10
como promessa de hard cap monetário, transmitir corpus/questões reais, mudar o
workflow extrativo padrão ou ampliar produção. Perfil remoto somente opt-in.
Budget local limita chamadas, não impede o usuário de alterar o billing no Google.

Menor prova: ACL local, segredo presente sem ler seu valor no chat, compose válido,
duas rodadas determinísticas dos controles no código inalterado; probe remoto
ADK retornando `RAG_LAB_OK` se a quota Free desse modelo permitir. HTTP 429 é
bloqueio de quota, não prova de inferência. Preservar falhas, não aumentar quota
via plano pago. Não certificar MCP/Vertex/DeepAgents/100k requests por esse probe.

## Revisão do contrato antes da troca de modelo

O contrato acima descreve a revisão inicial, que retornou dois HTTP 404. Em
30/09/2026, a documentação oficial confirmou que acesso à família 2.5 é restrito
a usuários com uso prévio; recomenda 3.5 Flash-Lite para novos projetos. É uma
hipótese fundamentada para o 404, não confirmação do erro específico recebido.

Nova revisão implementada: modelo fixo `gemini-3.5-flash-lite`, Standard gratuito,
thinking level `minimal`, sem resumos de pensamento e sem budget legado 2.5.
Todos os demais limites e proibições permanecem: 32 tokens de saída, uma chamada
por execução, duas tentativas por dia UTC, sem retry/fallback/billing/corpus real.
Não zerar o contador, criar outro projeto ou esperar artificialmente para obter
tentativas. Fixtures da revisão inicial concluídas antes da alteração: RAG
31/31 em duas rodadas e lifecycle 34/34 em duas rodadas. Provas preservadas;
não certificam a imagem posterior à troca. Reexecutar na revisão final.

Prova adicional: construção offline dos tipos SDK/ADK e Runner falso verifica
endpoint, modelo, tokens, thinking e rejeição de respostas diferentes do marcador.
Esse teste não realiza inferência; nova revisão continua não validada remotamente
até a próxima janela permitida e resposta real correta, sem contornar limites.

Fontes oficiais consultadas:
- https://ai.google.dev/gemini-api/docs/deprecations
- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/gemini-api/docs/thinking
