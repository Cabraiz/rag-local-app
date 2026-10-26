# Gravações

**O que tem aqui / quando ler.** Os players de cada vídeo, um por parte do sistema, com uma frase sobre o que ele
mostra. O que cada tela prova, em detalhe, com miniatura e duração: [`docs/gravacoes/`](gravacoes/README.md).

Gravados no commit `828ac9c`, com `API_PORT=18905` (o padrão é 8765). Em cada execução que agenda, a CLI pergunta
`Agendar estes N exames? [s/N]` e a gravação responde "s"; no 12, o console do ADK pede "yes".

### 01. Transpilador

JSON válido → `agent.py`; campo extra e chave duplicada recebem erro claro. ([mp4](gravacoes/01-transpilador.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/1afaca66-8bf2-47aa-8133-e40773609635

### 02. OCR via MCP (SSE)

`extract_exam_text` lê `pedido.png` pelo SSE e devolve as linhas com a PII já mascarada. ([mp4](gravacoes/02-ocr-mcp-sse.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/f5842359-bf12-428a-b2e4-d9b8ffafd73e

### 03. RAG via MCP (SSE)

`search_exams` acha o código com sinônimo e com erro de digitação, num catálogo de 120 exames. ([mp4](gravacoes/03-rag-mcp-sse.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/c1f8ebde-a31f-4c27-8eb0-7a6f4febd9c6

### 04. API e Swagger

`POST /appointments` → 201 no Swagger, e o `GET` pelo id criado. ([mp4](gravacoes/04-api-swagger.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/94fa3f67-d6f8-4d41-a3bc-152f0c67429b

### 05. Ponta a ponta com Gemini

`run --image pedido.png` com Gemini: a lista dos 3 exames, o "s", a tabela exame → código e a confirmação da API. ([mp4](gravacoes/05-ponta-a-ponta.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/abd03f0b-d26b-4871-b108-990666682a03

### 06. PII

Um pedido realista: só marcadores (`[NOME]`, `[CPF]`…) no terminal e nada pessoal no banco. 2 dos 4 exames são agendados; os 2 lidos com pouca confiança ficam para conferência. ([mp4](gravacoes/06-pii.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/afbcd819-2102-4635-baec-21b3077627cb

### 07. Docker e testes

Serviços healthy e a suíte inteira no serviço `tests` (`19378 passed` na gravação; o `1 skipped` é o ponta a ponta, que precisa da chave). ([mp4](gravacoes/07-docker-testes.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/4d4626a2-5d7f-46c9-add8-b401d5bddfda

### 08. Segurança

Pedidos com instruções escondidas: o OCR tira as instruções, e a lista perguntada traz só os exames legítimos, marcados `confira`. Respondida "s", só eles são agendados. ([mp4](gravacoes/08-seguranca.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/48c084df-85f0-4560-940d-a6cbe1823551

### 09. Dados sensíveis em carga

200 pedidos com dados pessoais fictícios pelo OCR, RAG e API: 0 vazamentos e nada em claro nos bytes do SQLite. ([mp4](gravacoes/09-dados-sensiveis.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/62219d86-1348-4388-a40e-c3f58acff3e0

### 10. Pedido manuscrito

Letra de mão simulada, com Gemini: os 5 exames vão para a lista marcados `confira` e são agendados depois do "s". ([mp4](gravacoes/10-manuscrito.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/678ba071-31fb-466f-a79b-48cad6e8f4ca

### 11. Foto de celular

Foto de celular simulada de um pedido impresso: os 5 exames vão para a lista e são agendados depois do "s"; a PII sai mascarada. ([mp4](gravacoes/11-foto-celular.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/4a07f2ed-78ad-4f74-a4cb-d334c3ea9b6e

### 12. ADK sem a CLI

`adk run --in_memory generated`: o mesmo agente no console do Google ADK, sem a CLI; a lista aparece em `[HITL confirm]` e só agenda depois do "yes". ([mp4](gravacoes/12-adk-run.mp4), [o que a tela mostra](gravacoes/README.md))

https://github.com/user-attachments/assets/aa502a2e-a159-474e-9959-1d1519602f47
