# Vídeos do desafio

Um vídeo por ponto do desafio, gravado com `API_PORT=18904` (o padrão é 8765). A lista com miniaturas e duração está em [`videos-do-desafio/`](../videos-do-desafio/README.md).

### 10. Pedido manuscrito

com Gemini, um exame lido com confiança média é perguntado no terminal (`[s/N]`), respondido "s" e agendado com os demais. A linha `[schedule] chamando create_appointment` aparece antes da pergunta porque a confirmação nativa do ADK pausa dentro dessa chamada: o `POST` só sai depois do "s" ([mp4](../videos-do-desafio/10-manuscrito.mp4))

https://github.com/user-attachments/assets/0ead4102-8185-4c90-b87f-4cfb666ca248

### 01. Transpilador

JSON válido → `agent.py`; erro claro de campo extra e chave duplicada ([mp4](../videos-do-desafio/01-transpilador.mp4))

https://github.com/user-attachments/assets/9c9d24f5-2baa-4646-9a85-e226a6305a42

### 05. Ponta a ponta com Gemini

tabela exame → código e confirmação da API; o total da linha `Tempo:` inclui os turnos do modelo ([por quê](como-rodar.md#3-executar-o-agente)) ([mp4](../videos-do-desafio/05-ponta-a-ponta.mp4))

https://github.com/user-attachments/assets/5c8d07bd-fef7-47ec-aa58-362052bc0a31

### 11. Foto de celular

com Gemini, um pedido impresso fotografado (perspectiva, sombra): os 5 exames escritos são agendados e a PII sai mascarada ([mp4](../videos-do-desafio/11-foto-celular.mp4))

https://github.com/user-attachments/assets/d11989f1-1d2a-4512-b6fe-56c222bdc0ce

### 02. OCR via MCP (SSE)

`extract_exam_text` em `pedido.png`, com a PII já mascarada ([mp4](../videos-do-desafio/02-ocr-mcp-sse.mp4))

https://github.com/user-attachments/assets/6cd79809-df4d-4ee6-8f94-268a99475088

### 06. PII

pedido realista só com marcadores no terminal e nada pessoal no banco; 2 dos 4 exames são agendados e os outros 2 (Colesterol total 0,68, Hemoglobina glicada 0,60) saem em `baixa confiança`, listados para conferência, como esperado. O `text_removed: 2` do OCR, num pedido legítimo, é o cabeçalho da clínica (`CLÍNICA FICTÍCIA HORIZONTE - DADOS FICTÍCIOS`), em 2 trechos: a rede de segurança tira do texto o que não parece exame nem estrutura do pedido ([mp4](../videos-do-desafio/06-pii.mp4))

https://github.com/user-attachments/assets/72caf6d7-b21b-4f65-b6af-789f566eb223

### 08. Segurança

nos pedidos com instruções escondidas, o OCR conta e tira o texto delas (`Instruções neutralizadas no OCR: …`) e só os exames legítimos são agendados; o cabeçalho (`Laboratorio Ficticio Beta`, `PEDIDO MEDICO FICTICIO`) também sai como `[TEXTO_REMOVIDO]`, não por ser instrução, mas pela rede de segurança que só deixa sair do OCR o que parece exame ou estrutura do pedido. Por isso a tela mostra um `[TEXTO_REMOVIDO]` a mais que o `instructions_removed` (5 contra 4 e 4 contra 3): `instructions_removed` conta só as instruções escondidas, e `text_removed` conta todos os trechos removidos, o título incluído ([mp4](../videos-do-desafio/08-seguranca.mp4))

https://github.com/user-attachments/assets/4e563a8d-1bae-4f39-8916-a23e3c619e85

### 03. RAG via MCP (SSE)

sinônimo e erro de digitação; catálogo com 120 exames ([mp4](../videos-do-desafio/03-rag-mcp-sse.mp4))

https://github.com/user-attachments/assets/4a161b26-1243-4a39-bee5-813d72ad05ce

### 04. API e Swagger

`POST /appointments` → 201 e `GET` pelo id ([mp4](../videos-do-desafio/04-api-swagger.mp4))

https://github.com/user-attachments/assets/355158d9-d058-424b-b9d3-688280489cc0

### 09. Dados sensíveis em carga

A imagem de entrada (fictícia) de um pedido do gerador e a saída do OCR, com nome, CPF, RG, endereço, carteirinha e CRM mascarados; depois, 200 pedidos pelo OCR, RAG e API: 0 vazamentos, os 200 agendamentos lidos de volta iguais e nada em claro nos bytes do SQLite. A soma de mascarados (2509) passa dos 2400 campos sensíveis porque a data do pedido também sai como `[DATA]`, enquanto endereço e CEP na mesma linha saem num só `[ENDERECO]`. Parciais na tela, perto de 99%: exames preservados no texto e códigos certos no RAG; os poucos exames não lidos pelo OCR (letra ou sigla solta, como em "Troponina I") são listados pelo nome ([por quê](medicoes.md#carga-de-dados-sensíveis)) ([mp4](../videos-do-desafio/09-dados-sensiveis.mp4))

https://github.com/user-attachments/assets/18c47964-e4e9-4fc2-b4f8-fa814f34a093

### 07. Docker e testes

Serviços healthy e a suíte inteira no serviço `tests` (`17254 passed`, `exit=0`); o `1 skipped` é o teste ponta a ponta, que precisa da chave Gemini ([mp4](../videos-do-desafio/07-docker-testes.mp4))

https://github.com/user-attachments/assets/5cf3d983-ddaa-474a-9c21-4416c3f9d0da
