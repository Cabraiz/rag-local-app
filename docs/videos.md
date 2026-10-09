# Gravações

O que tem aqui: um vídeo por parte do sistema, com o que ele mostra.

Gravados no commit `828ac9c`, com `API_PORT=18905` (o padrão é 8765). Cada execução que agenda roda num terminal: a CLI mostra a lista e pergunta `Agendar estes N exames? [s/N]`, respondida "s"; no 12, o console do ADK pede "yes" à mesma lista. A lista com miniaturas e duração está em [`docs/gravacoes/`](gravacoes/README.md).

### 01. Transpilador

JSON válido → `agent.py`; erro claro de campo extra e chave duplicada ([mp4](gravacoes/01-transpilador.mp4))

https://github.com/user-attachments/assets/1afaca66-8bf2-47aa-8133-e40773609635

### 02. OCR via MCP (SSE)

`extract_exam_text` em `pedido.png`, com a PII já mascarada ([mp4](gravacoes/02-ocr-mcp-sse.mp4))

https://github.com/user-attachments/assets/f5842359-bf12-428a-b2e4-d9b8ffafd73e

### 03. RAG via MCP (SSE)

sinônimo e erro de digitação; catálogo com 120 exames ([mp4](gravacoes/03-rag-mcp-sse.mp4))

https://github.com/user-attachments/assets/c1f8ebde-a31f-4c27-8eb0-7a6f4febd9c6

### 04. API e Swagger

`POST /appointments` → 201 e `GET` pelo id ([mp4](gravacoes/04-api-swagger.mp4))

https://github.com/user-attachments/assets/94fa3f67-d6f8-4d41-a3bc-152f0c67429b

### 05. Ponta a ponta com Gemini

a lista dos 3 exames e `Agendar estes 3 exames? [s/N]`, respondida "s"; tabela exame → código e confirmação da API; o total da linha `Tempo:` inclui os turnos do modelo ([por quê](como-rodar.md#3-executar-o-agente)) ([mp4](gravacoes/05-ponta-a-ponta.mp4))

https://github.com/user-attachments/assets/abd03f0b-d26b-4871-b108-990666682a03

### 06. PII

pedido realista só com marcadores no terminal e nada pessoal no banco; a lista perguntada tem 2 dos 4 exames, agendados depois do "s", e os outros 2 (Colesterol total 0,68, Hemoglobina glicada 0,60) aparecem nela em `Não agendados`, com `baixa confiança`, para conferência, como esperado. O `text_removed: 2` do OCR, num pedido legítimo, é o cabeçalho da clínica (`CLÍNICA FICTÍCIA HORIZONTE - DADOS FICTÍCIOS`), em 2 trechos: o OCR só deixa sair o que parece exame ou estrutura do pedido ([mp4](gravacoes/06-pii.mp4))

https://github.com/user-attachments/assets/afbcd819-2102-4635-baec-21b3077627cb

### 07. Docker e testes

Serviços healthy e a suíte inteira no serviço `tests` (`19378 passed` na gravação; `exit=0`); o `1 skipped` é o teste ponta a ponta, que precisa da chave Gemini ([mp4](gravacoes/07-docker-testes.mp4))

https://github.com/user-attachments/assets/4d4626a2-5d7f-46c9-add8-b401d5bddfda

### 08. Segurança

nos pedidos com instruções escondidas, o OCR conta e tira o texto delas (`Instruções neutralizadas no OCR: …`) e a lista perguntada traz só os exames legítimos, todos marcados `confira` porque a página tem texto além da lista (no 2º pedido, os 3 que dividiam a linha com uma instrução removida dizem também `o pedido tem outras palavras além do exame`); respondida "s", só eles são agendados; o cabeçalho (`Laboratorio Ficticio Beta`, `PEDIDO MEDICO FICTICIO`) também sai como `[TEXTO_REMOVIDO]`, porque o OCR só deixa sair o que parece exame ou estrutura do pedido. Por isso a tela mostra um `[TEXTO_REMOVIDO]` a mais que o `instructions_removed` (5 contra 4 e 4 contra 3): `text_removed` conta todos os trechos removidos, o título incluído. Pela regra da [página inteira só com a lista](medicoes.md#lista-branca-por-página), nenhum desses exames é agendado sem a resposta ([mp4](gravacoes/08-seguranca.mp4))

https://github.com/user-attachments/assets/48c084df-85f0-4560-940d-a6cbe1823551

### 09. Dados sensíveis em carga

A imagem de entrada (fictícia) de um pedido do gerador e a saída do OCR, com nome, CPF, RG, endereço, carteirinha e CRM mascarados; depois, 200 pedidos pelo OCR, RAG e API: 0 vazamentos, os 200 agendamentos lidos de volta iguais e nada em claro nos bytes do SQLite. A soma de mascarados (2451) passa dos 2400 campos sensíveis porque a data do pedido também sai como `[DATA]`, enquanto endereço e CEP na mesma linha saem num só `[ENDERECO]`. Parciais na tela, perto de 99%: exames preservados no texto e códigos certos no RAG; os poucos exames não lidos pelo OCR (letra ou sigla solta, como em "Troponina I") são listados pelo nome ([por quê](medicoes.md#carga-de-dados-sensíveis)) ([mp4](gravacoes/09-dados-sensiveis.mp4))

https://github.com/user-attachments/assets/62219d86-1348-4388-a40e-c3f58acff3e0

### 10. Pedido manuscrito

com Gemini, numa letra de mão simulada (fonte, não escrita real): a página tem texto além da lista, então os 5 exames vão para a lista marcados `confira` (Triglicerídeos lido com confiança 0,71); `Agendar estes 5 exames? [s/N]` respondido "s", e os 5 são agendados. A linha `[schedule] chamando create_appointment` aparece antes da pergunta porque a confirmação nativa do ADK pausa dentro dessa chamada: o `POST` só sai depois do "s". Pela regra da [página inteira só com a lista](medicoes.md#lista-branca-por-página), nenhum deles é agendado sem a resposta ([mp4](gravacoes/10-manuscrito.mp4))

https://github.com/user-attachments/assets/678ba071-31fb-466f-a79b-48cad6e8f4ca

### 11. Foto de celular

com Gemini, uma foto de celular simulada de um pedido impresso (perspectiva e sombra geradas): os 5 exames escritos vão para a lista, respondida "s", são agendados, e a PII sai mascarada ([mp4](gravacoes/11-foto-celular.mp4))

https://github.com/user-attachments/assets/4a07f2ed-78ad-4f74-a4cb-d334c3ea9b6e

### 12. ADK sem a CLI

`adk run --in_memory generated` com Gemini: o mesmo agente gerado, no console do próprio Google ADK, sem a CLI do projeto; recebe `pedido.png`; antes da API, o console mostra a lista em `[HITL confirm]` e espera "yes"; depois do "yes", agenda os exames e mostra a confirmação da API ([mp4](gravacoes/12-adk-run.mp4))

https://github.com/user-attachments/assets/aa502a2e-a159-474e-9959-1d1519602f47
