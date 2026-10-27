# Como rodar: do clone ao pedido agendado

**O que tem aqui / quando ler.** O primeiro uso, do clone ao pedido agendado:

- subir o ambiente e transpilar a spec;
- executar o agente pela CLI ou pelo `adk run`/`adk web`;
- o que fazer quando algo falha.

Leia depois do [README](../README.md#rodar-em-4-comandos), que resume tudo em 4 comandos.

**Fica em outro lugar:**

- as variáveis de ambiente e os códigos de saída: [referencia.md](referencia.md#variáveis-de-ambiente);
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
- Uma chave da API Gemini, criada em <https://aistudio.google.com/apikey>. A do plano gratuito funciona; para dados reais, use a paga, porque no gratuito o Google pode usar o conteúdo para melhorar produtos ([licencas.md](licencas.md#dados-e-ia)).

## 1. Subir o ambiente Docker

O repositório tem um `Dockerfile` e um `docker-compose.yml` na raiz.

```bash
git clone https://github.com/Cabraiz/rag-local-app
cd rag-local-app
cp .env.example .env          # preencha GOOGLE_API_KEY=; a chave só vai para os serviços agent e tests-e2e
docker compose up -d --wait   # sobe ocr, rag e api; na 1ª vez a api cria a chave do banco
```

- **Tempo:** o 1º build, sem cache, leva de 10 a 20 minutos (por causa do Tesseract); os seguintes usam cache.
- **Saída esperada:** `ocr`, `rag` e `api` como `Healthy`.
- **Swagger:** `http://127.0.0.1:<API_PORT>/docs` (padrão 8765; a porta real sai de `docker compose port api 8000`).
  - A página se chama "API de agendamento de exames (fictícia)". O `/openapi.json` dela é o contrato que o agente consome.
  - Operações: `create_appointment` (`POST /appointments`), `get_appointment` (`GET /appointments/{appointment_id}`) e `health` (`GET /health`).
  - Corpo de exemplo: `{"exams": [{"code": "FICT-001"}]}`. O `name` é opcional: a API grava e devolve o nome do catálogo.
  - Um `Host` fora dos [permitidos](referencia.md#hosts-permitidos) recebe `400`.
  - As capturas em [`docs/evidencias/`](evidencias/) mostram a porta 18905, a da gravação.
- **Porta:** a porta da API é publicada só em `127.0.0.1`, como `API_PORT` (padrão 8765). No Windows, o Docker pode não acusar um conflito, então confira antes se ela está livre:
  - PowerShell: `Get-NetTCPConnection -LocalPort 8765 -State Listen` (nada impresso: livre);
  - Linux e macOS: `lsof -i :8765`.

  Se estiver ocupada, ponha outra no `.env` (ex.: `API_PORT=8766`).
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

A pasta `generated/` do host fica vazia: o arquivo vai para o volume Docker `generated`, porque o container roda como
usuário sem privilégios (uid 10001), que no Linux não escreveria numa pasta do host.

**Sua própria spec, sem rebuild.** `specs/` é montada só para leitura no `agent`: salve a spec ali; uma edição vale no
próximo comando.

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
  [log das evidências](evidencias/log-run-pedido.txt), 13 s. As linhas `[extract] chamando ...` mostram que não
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

- Só `s` ou `sim` agenda. Outra resposta, inclusive Enter: `Erro: agendamento bloqueado antes de chamar a API: você não confirmou a lista de exames; nada foi agendado`.
- Sem terminal (`docker compose run -T`, um pipe, `CI=1`) e sem `--yes`, o `run` para antes de ler o pedido e de chamar o modelo: `Erro: sem terminal para confirmar a lista de exames: rode num terminal ou com --yes; nada foi lido nem agendado (…)`.
- **`--yes`** pula a pergunta, para automação. Só as regras decidem, e o risco é de quem opera.
  - Os exames que viriam com aviso ficam de fora (`não agendado sem confirmação`).
  - Valem por inteiro os [limites conhecidos](../README.md#limites-conhecidos).
- O pedido inteiro é conferido antes da pergunta. Os avisos vêm acima da lista. Os exames que não serão agendados
  vêm em `Não agendados:`, com o motivo.

O fluxo da confirmação, ponto a ponto: [transpilador.md](transpilador.md#a-confirmação-da-lista-confirmação-nativa-do-adk).

**Avisos da saída.** As faixas de confiança e o que é `confira` estão no [glossário](glossario.md). "Conferir" quer
dizer: olhe o pedido em papel (a saída mostra as linhas já mascaradas) e, se ele pede o exame, agende-o à parte.

Cada aviso, o que significa, de onde vem no código e o que fazer: a tabela fica num lugar só, no
[referência de avisos](referencia.md#avisos-sobre-os-exames-de-um-pedido).

Todos os estados de um exame e a regra de cada um: [referencia.md](referencia.md#agendamento-conferido-em-código).
Exemplos de linhas (negação, caixas, preparo, vários exames numa linha como "TSH, T4 livre" ou "Toxoplasmose IgG e IgM") e os limites: [regras.md](regras.md) e [medicoes.md](medicoes.md#limites-conhecidos).

**A linha `Tempo:`** traz cada etapa, o total e o modelo que respondeu por último.

- Buscas em paralelo contam uma vez.
- Ela aparece também antes de um `Erro:` e não traz dado do pedido.
- Cada etapa inclui o turno do modelo que gera a chamada da ferramenta, então cresce com o Gemini lento.
- O total é o relógio do `run` inteiro. Ele conta as tentativas do Gemini (até 5 no total) e a espera pelo `[s/N]`, e
  pode passar bem da soma das etapas.

**Outras opções:**

- Outro modelo, sem editar a spec (ou `GEMINI_MODEL=` no `.env`, para todas as execuções):
  `docker compose run --rm -e GEMINI_MODEL=<modelo> agent python -m cli run --image pedido.png`
- Logs das bibliotecas (ADK, MCP, novas tentativas do Gemini) no stderr. Numa falha inesperada, sai também o
  traceback, com a chave trocada por `[GOOGLE_API_KEY]`. Vale em `run` e `transpile`:
  `docker compose run --rm agent python -m cli run --image pedido.png --verbose`

### Testar outra imagem

Coloque o arquivo em `samples/` (montada no container de OCR, sem rebuild) e rode:

```bash
docker compose run --rm agent python -m cli run --image <arquivo>
```

- **Formato:** `.png`, `.jpg` ou `.jpeg`, até 5 MB e 25 megapixels. Passe só o nome, sem pastas.
  - Outra extensão é recusada antes do Gemini: `Erro: --image: "pedido.pdf" não é uma imagem aceita; use .png, .jpg ou .jpeg`.
  - Um PDF renomeado é recusado pelo OCR: `O arquivo é um PDF, não uma imagem: exporte a página como PNG ou JPEG.`
- **Foto de lado ou de cabeça para baixo** é endireitada. Se nem assim der: `imagem de lado ou de cabeça para baixo: gire e envie de novo`. PNG transparente é lido sobre branco.
- **Arquivo inexistente ou recusado:** a CLI pergunta ao OCR antes do Gemini, então o erro sai em segundos:
  `Erro: OCR recusou a imagem: Arquivo "x.png" não encontrado em /data/samples.; nada foi agendado`.
  - Só uma página que o Tesseract não consegue endireitar é recusada durante a execução.
  - O nome vai só ao OCR. O modelo recebe um apelido (`pedido-1.png`).
- **A suíte não muda:** os testes usam a lista fixa dos arquivos versionados ([`tests/versionados.py`](../tests/versionados.py), [`test_versionados.py`](../tests/test_versionados.py)).

Exemplos em `samples/`:

- **`pedido-realista.png`:** cabeçalho de clínica, CPF, telefone, CID, convênio, data, CRM e 4 exames numa fonte que
  imita letra de mão.
  - Glicemia e TSH são agendados.
  - Colesterol total e Hemoglobina glicada saem em `baixa confiança`: o OCR leu essas linhas com 68 e 60, abaixo do
    piso de 75. A saída mostra a confiança de cada um, como `(confiança 0,68)`.
  - É o esperado: o que não foi lido com segurança vai para conferência humana.
- **`pedido-sem-exame.png`:** não tem exame, e nada é agendado.
  - É o caso (b) do [teste de alucinação](evidencias/log-alucinacao.txt). Na execução do teste, a saída foi
    `Erro: a busca no catálogo não foi feita (o agente tentou agendar sem buscar os exames); nada foi agendado`.
  - Gerado por `exemplos/gerar_pedido_sem_exame.py`, com semente fixa.
- **`pedido-variacao.png`:** nome sem rótulo, marcadores, sinônimo `Glicose`, telefone sem rótulo, data e CRM.
- **`ataque-injecao.png` e `ataque-exame-disfarcado.png`:** instruções escondidas; só os exames legítimos são agendados ([Segurança em detalhe](referencia.md#segurança-em-detalhe)).
- **`pedido-manuscrito.png` e `pedido-manuscrito-dificil.png`:** letra de mão e foto de celular, simuladas. No primeiro, o OCR lê os 5 exames; no segundo ("letra de médico"), quase nada, e nada é agendado sem confirmação.

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

- **`python -m runtime.web`** ([`runtime/web.py`](../runtime/web.py)) é o `adk web --no_use_local_storage` com
  checagem de `Host`.
  - Ele só atende `Host` `127.0.0.1` ou `localhost`. Outro recebe 400.
  - Uma entrada que o ADK recusa, como uma confirmação forjada, recebe 400 numa linha:
    `Requisição inválida para esta sessão (ex.: confirmação forjada).`
- **`--in_memory` e `--no_use_local_storage`** mantêm a sessão em memória. Sem eles, o ADK a grava em
  `generated/.adk/`, com o texto do OCR mascarado e o nome real do arquivo.
- **A mensagem:** só o nome de um arquivo de `samples/`, como texto (`pedido.png` ou `agende o pedido pedido.png`).
  O modelo não é chamado quando ela não serve:
  - sem nome, com pasta ou com dois arquivos: `Informe só o nome de um arquivo de pedido em samples/ …`;
  - com algo além de texto: `Envie só o nome do arquivo do pedido, como texto…`.
- **Antes do 1º turno** rodam as mesmas conferências do `cli run`
  ([quais](transpilador.md#segurança-no-código-gerado)).
- **Reinicie o `adk run` ou o `adk web` se um `docker compose up` recriar um serviço.** Os endereços conferidos ficam
  fixos enquanto o processo durar ([`runtime/rede.py`](../runtime/rede.py)).
- **Confirmação da lista:** a mesma da CLI, sem `--yes`
  ([como](transpilador.md#a-confirmação-da-lista-confirmação-nativa-do-adk)). No `adk run`, responda `yes` ao
  `[HITL confirm]`. No `adk web`, marque "Confirmed" e clique em "Submit".
- **A última mensagem** (`[clinic_scheduler]: …`) é escrita em código com o que as ferramentas devolveram.
  `Agendamento confirmado pela API: id …` vem só da resposta da API. O texto do modelo (`[schedule]: …`) não conta.
- **Um pedido por sessão:** outra mensagem recebe `Esta sessão já tratou um pedido…`.
  - Se a API já agendou, a resposta traz o agendamento e `não repita este pedido`.
  - Para outro pedido: `exit` e `adk run` de novo. No `adk web`: New Session.
- **Modelo reserva e erros:** como no `cli run`. A última mensagem termina em `…; nada foi agendado` quando nada foi.
- **Só na CLI:** a checagem de que o `agent.py` é o que a spec gera hoje, a lista de ferramentas vivas antes do 1º
  turno e a linha `Tempo:`. As regras de agendamento valem igual.
- **O `adk web` não tem login:** publique a porta só em `127.0.0.1`, como acima.
- **Fora do Docker**, faça o que a imagem já faz:
  - rode os comandos na raiz do projeto: o `adk run generated` põe a raiz no `sys.path`, e o `agent.py` importa
    `runtime/` de lá ([por quê](decisoes.md#o-agentpy-usa-só-classes-do-adk-a-política-mora-num-plugin-versionado));
  - rode `adk telemetry disable`. Sem isso, o ADK pergunta sobre telemetria, e Enter responde sim;
  - defina `GOOGLE_API_USE_CLIENT_CERTIFICATE=false`, como o `docker-compose.yml` faz. Sem ela, o cliente MCP do
    ADK 2.10 procura credenciais do Google a cada conexão, inclusive em `169.254.169.254`.

Os testes destes comandos: [guia-dev.md](guia-dev.md#testes).
O porquê de cada defesa, e os limites do registro por sessão: [Segurança em detalhe](referencia.md#segurança-em-detalhe).

## Quando algo falha

**Escopo desta tabela:** falhas de ambiente (Docker, chave, portas, serviços). Os avisos sobre cada exame e os
erros de um pedido, com o lugar do código onde nascem: [referencia.md](referencia.md#mensagens-e-erros).

Erros saem como uma linha `Erro: ...`, com código 2. Para ver o que houve por trás, rode de novo com `--verbose`.

| Sintoma | O que fazer |
|---|---|
| `no configuration file provided: not found` | Entre em `rag-local-app`, onde está o `docker-compose.yml`. |
| `Cannot connect to the Docker daemon … Is the docker daemon running?` ou `failed to connect to the docker API` | Abra o Docker Desktop, espere o "Engine running" e rode de novo. |
| `Read timed out` do pip no 1º build | É a rede até o PyPI. Rode o `docker compose up -d --wait` de novo: os estágios prontos ficam no cache. |
| `all predefined address pools have been fully subnetted` | Redes de outros projetos ocupam todas as faixas do Docker. Ver abaixo. |
| `Healthy`, mas o Swagger não se chama "API de agendamento de exames (fictícia)" | Outro programa responde na porta: troque o `API_PORT`. |
| `OCR ocupado com outras imagens; tente de novo em instantes.` | O OCR lê 3 imagens por vez, e um pedido espera até 20 s por vaga: tente de novo. |
| Um serviço reinicia sozinho no meio de uma execução | Ele passou do seu teto de memória ([os tetos](referencia.md#rede-e-processos)). O motivo sai em `docker compose logs <serviço>`. |
| `Erro: o OCR não devolveu o texto do pedido (serviço indisponível?); nada foi agendado` | O OCR caiu no meio da execução: veja `docker compose logs -f ocr rag api`. |

As recusas do OCR e os erros do Gemini, com o que fazer: [Erros por camada](referencia.md#erros-por-camada).

**Docker sem sub-redes livres:**

- **Solução recomendada:** fixe duas faixas livres num `docker-compose.override.yml` na raiz (o Compose o lê sozinho; o `.gitignore` já o ignora):
  ```yaml
  networks:
    default:
      ipam: {config: [{subnet: 10.201.10.0/24}]}
    internal:
      ipam: {config: [{subnet: 10.201.11.0/24}]}
  ```
- **Faixas em uso** (PowerShell ou Git Bash):
  ```bash
  docker network inspect $(docker network ls -q) --format "{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}"
  ```
- **Comandos com `-f`** (carga e robustez, em [medicoes.md](medicoes.md)) não leem o override sozinhos: acrescente `-f docker-compose.override.yml`.
- **Alternativa, com cuidado:** `docker network prune` apaga as redes sem container em uso, inclusive as de **outros projetos** parados. Confira antes com `docker network ls`.
