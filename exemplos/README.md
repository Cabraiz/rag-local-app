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

`gerar_pedido_sem_exame.py` gera `samples/pedido-sem-exame.png`, o pedido sem nenhum exame do caso (b) de [`docs/evidencias/log-alucinacao.txt`](../docs/evidencias/log-alucinacao.txt), com o gerador da carga e semente fixa (as fontes DejaVu da imagem de testes desenham a mesma imagem; `tests/test_ocr.py` confere).

`gerar_manuscrito.py` gera os 120 pedidos manuscritos simulados de `samples/manuscritos/` (com as fontes de letra de mão do Windows; em outro sistema, aponte `FONTS_DIR` para uma pasta com elas); ver "Pedidos manuscritos simulados" em [docs/como-rodar.md](../docs/como-rodar.md).

## Letra de médico: avaliar um leitor local (experimento)

`avaliar_letra.py` mede se um modelo de visão local (Qwen3-VL 2B, Apache-2.0, no `llama-server`) lê as
linhas escritas à mão de `samples/manuscritos/` melhor que o Tesseract do projeto, e imprime a tabela de
[docs/medicoes.md](../docs/medicoes.md#letra-de-médico-leitor-local-avaliado-não-ligado-por-padrão). É um
experimento: nenhum serviço do projeto o importa. As linhas são recortadas pelas caixas de
`manuscritos-linhas.json`, tiradas do próprio gerador (`python exemplos/avaliar_letra.py caixas` as refaz,
com numpy e as fontes de `gerar_manuscrito.py`). Na raiz do repositório, com Docker:

```bash
# 1. Pesos com revisão fixa e SHA-256 conferido; tudo fica na pasta escolhida (2,2 GB)
docker run --rm -v "$PWD/modelos-letra:/m" -e HF_HOME=/m/.hf python:3.12-slim sh -c "pip install -q huggingface_hub==0.36.2 && hf download Qwen/Qwen3-VL-2B-Instruct-GGUF Qwen3VL-2B-Instruct-Q8_0.gguf mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf --revision 52d6c8ffea26cc873ac5ad116f8631268d7eb503 --local-dir /m && cd /m && printf '%s  %s\n' 1e8db19207c8ce0733ddd78c2eff8a9e22c27c82f4443df94c25792ed8fe04f2 Qwen3VL-2B-Instruct-Q8_0.gguf f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82 mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf | sha256sum -c"
# 2. O servidor, sem rede e com os pesos só para leitura (imagem fixada pelo digest, 1,2 GB)
docker run -d --name letra-llm --network none --cpus 4 --memory 8g -v "$PWD/modelos-letra:/models:ro" ghcr.io/ggml-org/llama.cpp@sha256:33868c035b21dc63f7c60b7438774283fd99215bc319114eb03de5df4ce7cd6b -m /models/Qwen3VL-2B-Instruct-Q8_0.gguf --mmproj /models/mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf --host 127.0.0.1 --port 8080 -t 4 -c 8192 --parallel 1 --no-ui
# 3. A avaliação, na rede do servidor (só o loopback dele, sem internet), com a imagem de testes
docker build -q --target test -t letra-avaliar .
docker run --rm --network container:letra-llm -v "$PWD:/src:ro" -w /src letra-avaliar python exemplos/avaliar_letra.py --paginas 3
docker rm -f letra-llm
```

`--paginas N` limita às N primeiras páginas de cada estilo (sem ela, as 115 com caixas: 14 a 24 s por
página em 4 núcleos). No Git Bash do Windows, prefixe os comandos com `MSYS_NO_PATHCONV=1`. A primeira linha
demora mais (o catálogo entra no cache do prompt do servidor).
