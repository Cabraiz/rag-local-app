# Como rodar: do clone ao pedido agendado

**O que tem aqui / quando ler.** O primeiro uso, do clone ao pedido agendado:

- subir o ambiente e transpilar a [spec](glossario.md#agente-spec-e-serviços), isto é, gerar o `agent.py` a partir do JSON;
- executar o agente pela CLI ou pelo `adk run`/`adk web`;
- o que fazer quando algo falha.

Leia depois do [README](../README.md#rodar-em-4-comandos), que resume tudo em 4 comandos.

**Fica em outro lugar:**

- as [variáveis de ambiente](referencia.md#variáveis-de-ambiente) e os [códigos de saída](referencia.md#códigos-de-saída);
- o backup do banco: [backup.md](backup.md);
- os testes e a CI: [guia-dev.md](guia-dev.md#testes);
- as cargas, os manuscritos e a robustez, com os comandos: [medicoes.md](medicoes.md#resumo);
- o porquê de cada regra: [regras.md](regras.md) e [decisoes.md](decisoes.md).

**Índice:** [Pré-requisitos](#pré-requisitos) · [1. Subir o ambiente Docker](#1-subir-o-ambiente-docker) · [2. Rodar o transpilador](#2-rodar-o-transpilador) · [3. Executar o agente](#3-executar-o-agente) · [4. Rodar com `adk run` ou `adk web`](#4-rodar-com-adk-run-ou-adk-web) · [Quando algo falha](#quando-algo-falha)

## Pré-requisitos

- Docker com Compose ≥ 2.1.1 (`up --wait`). Não é preciso Python no host.
- Docker Desktop aberto, com o "Engine running" ([se não estiver](#quando-algo-falha)).
- No Windows, PowerShell ou Git Bash. O `cmd.exe` não serve: nele o `cp` não existe e o comentário depois do `#` vira argumento.
- Internet no build (apt e pip) e na execução (Gemini).
- Uma chave da API Gemini, criada em <https://aistudio.google.com/apikey>. A do plano gratuito funciona com os dados
  fictícios do projeto. Para dados reais, use a paga ([por quê](licencas.md#dados-e-ia)).

## 1. Subir o ambiente Docker

O repositório tem um `Dockerfile` e um `docker-compose.yml` na raiz.

```bash
git clone https://github.com/Cabraiz/rag-local-app
cd rag-local-app
cp .env.example .env          # preencha GOOGLE_API_KEY=; a chave só vai para os serviços agent e tests-e2e
docker compose up -d --wait   # sobe ocr, rag e api
```

- **Tempo:** o 1º build, sem cache, leva de 10 a 20 minutos (por causa do Tesseract); os seguintes usam cache.
- **Saída esperada:** `ocr`, `rag` e `api` como `Healthy`.
- **Swagger:** `http://127.0.0.1:8765/docs`, ou a sua porta (ver **Porta**, abaixo).
  - Confira que a página se chama "API de agendamento de exames (fictícia)".
  - As operações, o corpo e as defesas da API: [referencia.md](referencia.md#a-api-idempotência-host-limite-cabeçalhos-e-log).
  - As capturas em [`docs/evidencias/`](evidencias/) mostram a porta 18905, a da gravação.
- **Porta:** a porta da API é publicada só em `127.0.0.1`, como `API_PORT` (padrão 8765). No Windows, o Docker pode não acusar um conflito, então confira antes se ela está livre:
  - PowerShell: `Get-NetTCPConnection -LocalPort 8765 -State Listen` (nada impresso: livre);
  - Linux e macOS: `lsof -i :8765`.

  Se estiver ocupada, ponha outra no `.env` (ex.: `API_PORT=8766`). A porta em uso sai de `docker compose port api 8000`.
- **Chave do banco:** na 1ª subida, a API cria a chave no volume `api-key`.
  - O log mostra `chave do banco criada em /keys/db.key`, nunca a chave.
  - Para usar a sua chave: [`DB_ENCRYPTION_KEY`](referencia.md#variáveis-de-ambiente), antes do 1º `up`.
- **Pasta:** rode tudo dentro de `rag-local-app`. Fora dela: `no configuration file provided: not found`.
- **`.env`:** edite o arquivo. Não use `export`, `$env:` nem `echo > .env` (este apaga o `API_PORT`).
- **Logs:** `docker compose logs -f ocr rag api`. **Parar:** `docker compose down`.
- **Parar e limpar:** `docker compose --profile cli --profile test down -v` remove containers, redes e volumes.
  - Os volumes guardam o banco, a chave e o código gerado. Faça antes o [backup](backup.md) se o banco importa.
  - Sem `--profile cli`, o volume `generated` fica.
  - `--rmi local` apaga também as imagens.

## 2. Rodar o transpilador

```bash
docker compose run --rm agent python -m cli transpile specs/agent.json   # 1º run constrói a imagem do agent (sem cache: 2 a 6 min)
docker compose run --rm agent cat generated/agent.py                     # o arquivo fica no volume "generated"
```

```text
OK: generated/agent.py gerado e importado; root_agent "clinic_scheduler" (SequentialAgent: extract -> search -> schedule)
```

A pasta `generated/` do host não recebe o `agent.py`: o arquivo vai para o volume Docker `generated`. O container roda como um
usuário sem privilégios (uid 10001), que no Linux não escreveria numa pasta do host.

**Sua própria spec, sem rebuild.** `specs/` é montada só para leitura no `agent`: salve a spec ali; uma edição vale no
próximo comando.
O exemplo parte de `listar-exames.json`, que lista os exames sem agendar. Para agendar, parta de `agent.json`.

```bash
cp specs/listar-exames.json specs/minha-spec.json                         # ou escreva a sua do zero
docker compose run --rm agent python -m cli transpile specs/minha-spec.json --output generated/minha/agent.py
docker compose run --rm agent python -m cli run --image pedido.png --spec specs/minha-spec.json --agent generated/minha/agent.py
```

Sem `--output`, o novo arquivo substitui o `generated/agent.py`. Se a spec não passar na validação ou o código não
importar, o arquivo anterior fica. Campos, erros e o código gerado comentado: [transpilador.md](transpilador.md).

## 3. Executar o agente

```bash
docker compose run --rm agent python -m cli run --image pedido.png      # chama o Gemini (gemini-3.5-flash, da spec)
```

- **`pedido.png`** está em `samples/`. Antes da saída, o Compose mostra o status dos containers (`Waiting`, `Healthy`).
- **Tempo:** de segundos a alguns minutos, quase todo à espera do Gemini (três agentes em sequência). No
  [log das evidências](evidencias/log-run-pedido.txt), de 2026-10-09, 13 s. As linhas `[extract] chamando ...` mostram que não
  travou.
- **Modelo reserva:** se o principal responde `503` ou `429`, a requisição vai ao `fallback_model` da spec. A saída
  mostra `Aviso: modelo principal indisponível; usando gemini-3.5-flash-lite`. Como funciona:
  [transpilador.md](transpilador.md#fallback_model-por-requisição).

Antes de agendar, a CLI mostra a lista e faz uma pergunta para a lista inteira. O padrão é não:

```text
Trechos removidos pelo OCR (não pareciam exame): 3
Exames para agendar:
- Hemograma completo (FICT-001)
- Glicemia de jejum (FICT-002)
- Creatinina (FICT-005)
Agendar estes 3 exames? [s/N] s
```

Com `s`, a saída segue como a do [log](evidencias/log-run-pedido.txt), gravado num terminal com essa mesma pergunta
respondida `s`. O id muda a cada execução, e `NOME x2` são o paciente e o médico.

```text
PII reconhecida e mascarada pelo OCR: NOME x2, CPF x1, EMAIL x1, TELEFONE x1
Trechos removidos pelo OCR (não pareciam exame): 3

| Exame              | Código   |
|--------------------|----------|
| Hemograma completo | FICT-001 |
| Glicemia de jejum  | FICT-002 |
| Creatinina         | FICT-005 |

Agendamento confirmado pela API: id fe64c8c6…, status scheduled
Tempo: OCR 2,1 s · busca 2,4 s · agendamento 1,6 s · total 13 s (modelo gemini-3.5-flash)
```

**Confirmação final da lista.** Nada é agendado sem ela.

- Só `s` ou `sim` agenda. Enter, ou outra resposta, é não.
- Sem terminal (`docker compose run -T`, um pipe, `CI=1`), use `--yes`.
- **`--yes`** pula a pergunta. Só as [regras](regras.md#regras-que-só-decidem-com---yes) decidem, e os exames que viriam
  com o aviso [`confira`](glossario.md#faixas-e-pisos) ficam de fora ([os limites](../README.md#limites-conhecidos)).
- Os exames que não serão agendados vêm em `Não agendados:`, com o motivo.

O fluxo da confirmação, ponto a ponto: [transpilador.md](transpilador.md#a-confirmação-da-lista-confirmação-nativa-do-adk).
Cada mensagem de erro e o que fazer: [Erros por camada](referencia.md#erros-por-camada).

**Avisos da saída.** As faixas de confiança e o que é `confira`: [glossário](glossario.md#faixas-e-pisos). Cada aviso, o
que significa, de onde vem no código e o que fazer: [referência de avisos](referencia.md#avisos-sobre-os-exames-de-um-pedido).

Todos os estados de um exame e a regra de cada um: [referencia.md](referencia.md#agendamento-conferido-em-código).
Exemplos de linhas difíceis, como negação, caixas, preparo e vários exames numa linha: [regras.md](regras.md). Os
limites conhecidos: [medicoes.md](medicoes.md#limites-conhecidos).

**A linha `Tempo:`** traz cada etapa, o total e o modelo que respondeu por último. O total pode passar bem da soma
das etapas ([como cada parte é contada](referencia.md#a-linha-tempo)).

**Outras opções:**

- Outro modelo, sem editar a spec (ou `GEMINI_MODEL=` no `.env`, para todas as execuções):
  `docker compose run --rm -e GEMINI_MODEL=<modelo> agent python -m cli run --image pedido.png`
- Logs das bibliotecas (ADK, MCP, novas tentativas do Gemini) no stderr. Numa falha inesperada, sai também o
  traceback, com a chave trocada por `[GOOGLE_API_KEY]`. Vale em `run` e `transpile`:
  `docker compose run --rm agent python -m cli run --image pedido.png --verbose`

### Testar outra imagem

Use um pedido fictício: o texto, já mascarado, vai ao Gemini. Coloque o arquivo em `samples/` (montada no container
de OCR, sem rebuild) e rode:

```bash
docker compose run --rm agent python -m cli run --image <arquivo>
```

- **Formato:** `.png`, `.jpg` ou `.jpeg`, até 5 MB e 25 megapixels. Passe só o nome, sem pastas.
- **Foto de lado ou de cabeça para baixo** é endireitada. PNG transparente é lido sobre branco.
- **Arquivo recusado:** a CLI pergunta ao OCR antes do Gemini, então o erro sai em segundos. Cada recusa, com a
  mensagem e o que fazer: [Erros por camada](referencia.md#erros-por-camada).
- **A suíte não muda:** os testes usam a lista fixa dos arquivos versionados ([`tests/versionados.py`](../tests/versionados.py), [`test_versionados.py`](../tests/test_versionados.py)).

Exemplos em `samples/`:

- **`pedido-realista.png`:** cabeçalho de clínica, CPF, telefone, CID, convênio, data, CRM e 4 exames numa fonte que
  imita letra de mão.
  - Glicemia e TSH são agendados.
  - Colesterol total e Hemoglobina glicada saem em `baixa confiança`: o OCR leu essas linhas abaixo do
    [piso de leitura](glossario.md#faixas-e-pisos) de 75, de 0 a 100. A saída mostra a confiança de cada um, de 0 a 1.
  - É o esperado: o que não foi lido com segurança vai para conferência humana.
- **`pedido-sem-exame.png`:** não tem exame, e nada é agendado.
  - É o caso (b) do [teste de alucinação](evidencias/log-alucinacao.txt). Na execução de 2026-10-09, com o
    `gemini-3.5-flash`, a saída foi
    `Erro: a busca no catálogo não foi feita (o agente tentou agendar sem buscar os exames); nada foi agendado`.
  - Gerado por `exemplos/gerar_pedido_sem_exame.py`, com semente fixa.
- **`pedido-variacao.png`:** nome sem rótulo, marcadores, sinônimo `Glicose`, telefone sem rótulo, data e CRM.
- **`ataque-injecao.png` e `ataque-exame-disfarcado.png`:** instruções escondidas; só os exames legítimos são agendados ([Segurança em detalhe](referencia.md#segurança-em-detalhe)).
- **`pedido-manuscrito.png`:** letra de mão e foto de celular, simuladas.
  - O OCR lê os 5 exames. Todos saem com `confira`, e a CLI pergunta antes de agendar.
  - É o caso (e) do [teste de alucinação](evidencias/log-alucinacao.txt), na mesma execução de 2026-10-09.
- **`pedido-manuscrito-dificil.png`:** "letra de médico", simulada.
  - O OCR quase não lê: a única busca, "TGo", sai com confiança 0,05.
  - Nada é agendado: `Erro: agendamento bloqueado antes de chamar a API: nenhum exame com confiança suficiente para
    agendar; nada foi agendado` (caso (c) do mesmo log).

## 4. Rodar com `adk run` ou `adk web`

O agente gerado também roda com as ferramentas do ADK, sem a CLI do projeto.

- O `transpile` grava `generated/agent.py` e `generated/__init__.py`.
- O `agent.py` expõe `root_agent` e um `app` retomável: a confirmação da lista pausa e retoma a mesma chamada de
  agendamento.

```bash
docker compose run --rm agent python -m cli transpile specs/agent.json   # gera de novo depois de atualizar o projeto
docker compose run --rm agent adk run --in_memory generated              # no [user]:, digite só o nome do arquivo: pedido.png
docker compose run --rm -p 127.0.0.1:8000:8000 agent python -m runtime.web --host 0.0.0.0 generated   # o adk web, com checagem de Host
```

No `adk web`, abra <http://127.0.0.1:8000>, escolha o agente `generated` e mande `pedido.png`. Com a porta 8000
ocupada, troque só o primeiro número (ex.: `127.0.0.1:8090:8000`).

- **A mensagem:** só o nome de um arquivo de `samples/`, como texto (`pedido.png` ou `agende o pedido pedido.png`).
  Outra mensagem é recusada antes do modelo, com uma linha que diz o que mandar.
- **Confirmação da lista:** a mesma da CLI, sem `--yes`
  ([como](transpilador.md#a-confirmação-da-lista-confirmação-nativa-do-adk)). No `adk run`, responda `yes` ao
  `[HITL confirm]`. No `adk web`, marque "Confirmed" e clique em "Submit".
- **Agendou** quando a última mensagem traz `Agendamento confirmado pela API: id …`. O texto do modelo
  (`[schedule]: …`) não conta.
- **Um pedido por sessão.** Para outro pedido: `exit` e `adk run` de novo. No `adk web`: New Session.
- **Reinicie o `adk run` ou o `adk web` se um `docker compose up` recriar um serviço.** Os IPs dos serviços ficam
  fixos enquanto o processo durar.
- **O `adk web` não tem login:** publique a porta só em `127.0.0.1`, como acima.
- **Fora do Docker:** o que configurar está em [Ambiente local, sem Docker](guia-dev.md#ambiente-local-sem-docker).

O que muda em relação à CLI, e por quê: [Fora da CLI](referencia.md#fora-da-cli-adk-run-e-adk-web). Os testes destes
comandos: [guia-dev.md](guia-dev.md#testes).

## Quando algo falha

**Escopo desta tabela:** falhas de ambiente (Docker, chave, portas, serviços). Os avisos sobre cada exame e os
erros de um pedido, com o lugar do código onde nascem: [referencia.md](referencia.md#mensagens-e-erros).

Erros saem como uma linha `Erro: ...`, com código 2 ([todos os códigos](referencia.md#códigos-de-saída)). Para ver o que
houve por trás, rode de novo com `--verbose`.

| Sintoma | Causa | O que fazer |
|---|---|---|
| `no configuration file provided: not found` | o comando rodou fora do clone | entre em `rag-local-app`, onde está o `docker-compose.yml` |
| `Cannot connect to the Docker daemon … Is the docker daemon running?` ou `failed to connect to the docker API` | o Docker não está rodando | abra o Docker Desktop, espere o "Engine running" e rode de novo |
| `Read timed out` do pip no 1º build | a rede até o PyPI | rode o `docker compose up -d --wait` de novo: os estágios prontos ficam no cache |
| `all predefined address pools have been fully subnetted` | redes de outros projetos ocupam as faixas do Docker | fixe duas faixas ([abaixo](#docker-sem-sub-redes-livres)) |
| `port is already allocated` ou `ports are not available` no `up` | outro programa usa a porta 8765 | troque a porta ([**Porta**](#1-subir-o-ambiente-docker)) |
| `Healthy`, mas o Swagger não se chama "API de agendamento de exames (fictícia)" | outro programa responde na porta | troque a porta ([**Porta**](#1-subir-o-ambiente-docker)) |
| `OCR ocupado com outras imagens; tente de novo em instantes.` | as 3 vagas do OCR ficaram ocupadas por mais de 20 s | tente de novo |
| Um serviço reinicia sozinho no meio de uma execução | ele passou do seu [teto de memória](referencia.md#rede-e-processos) | veja o motivo em `docker compose logs <serviço>` |
| `Erro: o OCR não devolveu o texto do pedido (serviço indisponível?); nada foi agendado` | o OCR caiu no meio da execução | veja `docker compose logs -f ocr rag api` |

As recusas do OCR e os erros do Gemini, com o que fazer: [Erros por camada](referencia.md#erros-por-camada).

### Docker sem sub-redes livres

1. **Veja as faixas em uso** (PowerShell ou Git Bash):

   ```bash
   docker network inspect $(docker network ls -q) --format "{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}"
   ```

2. **Fixe duas faixas `/24` que não estão na lista** num `docker-compose.override.yml` na raiz. O Compose o lê sozinho,
   e o `.gitignore` já o ignora:

   ```yaml
   networks:
     default:
       ipam: {config: [{subnet: 10.201.10.0/24}]}
     internal:
       ipam: {config: [{subnet: 10.201.11.0/24}]}
   ```

3. **Rode `docker compose up -d --wait` de novo.** Os três serviços devem subir como `Healthy`.

Outros casos:

- **Comandos com `-f`** (carga e robustez, em [medicoes.md](medicoes.md)) não leem o override sozinhos: acrescente `-f docker-compose.override.yml`.
- **Alternativa, com cuidado:** `docker network prune` apaga as redes sem container em uso, inclusive as de **outros projetos** parados. Confira antes com `docker network ls`.
