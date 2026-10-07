# Exemplos usados nos vídeos

`mcp_call.py` chama uma ferramenta MCP pelo SSE, como o agente faz, e imprime a resposta
(vídeos 02, 03, 06 e 08). `specs-com-erro/` tem duas cópias de `specs/agent.json` com um erro de
propósito cada, um campo extra e uma chave duplicada (vídeo 01). Com a stack no ar:

```bash
docker compose run --rm agent python exemplos/mcp_call.py ocr extract_exam_text filename=pedido.png
docker compose run --rm agent python exemplos/mcp_call.py rag search_exams query=Glicose
docker compose run --rm agent python -m cli transpile exemplos/specs-com-erro/campo-extra.json
docker compose run --rm agent python -m cli transpile exemplos/specs-com-erro/chave-duplicada.json
```

Os argumentos da ferramenta vão como `chave=valor`, que funciona igual no PowerShell 5.1 e no
Git Bash. Um objeto JSON (`'{"filename": "pedido.png"}'`) também é aceito, mas o PowerShell 5.1
tira as aspas internas dele. Números e `true`/`false` viram valores JSON (`top_k=3`); o resto é
texto. Um argumento inválido ou a stack fora do ar dão uma linha `Erro: …`, com código 2.
Saída esperada: as linhas do OCR já mascaradas (`Paciente: [NOME]`, `CPF: [CPF]`…), os exames do
RAG em ordem de score (`Glicose` → `FICT-002`, score 1,0) e, nas specs,
`Erro: campo_inexistente: campo não permitido` e `Erro: name: chave duplicada no JSON`.

`gerar_pedido_sem_exame.py` gera `samples/pedido-sem-exame.png`, o pedido sem nenhum exame do caso (b) de [`evidencias/log-alucinacao.txt`](../evidencias/log-alucinacao.txt), com o gerador da carga e semente fixa (as fontes DejaVu da imagem de testes desenham a mesma imagem; `tests/test_ocr.py` confere).

`gerar_manuscrito.py` gera os 120 pedidos manuscritos simulados de `samples/manuscritos/` (com as fontes de letra de mão do Windows; em outro sistema, aponte `FONTS_DIR` para uma pasta com elas); ver "Pedidos manuscritos simulados" em [docs/como-rodar.md](../docs/como-rodar.md).
