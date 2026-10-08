# Como rodar: guia completo

**O que tem aqui:** os comandos para subir o projeto, transpilar a spec e executar o agente (pela CLI ou pelo
`adk run`/`adk web`), o que cada aviso da saída quer dizer e o que fazer quando algo falha; no fim, variáveis de
ambiente, backup do banco e testes. Leia depois do [README](../README.md#rodar-em-4-comandos), que resume tudo em 4
comandos. O porquê de cada regra está em [arquitetura.md](arquitetura.md); os números, em [medicoes.md](medicoes.md).

## Pré-requisitos

- Docker com Compose ≥ 2.1.1 (`up --wait`). Não é preciso Python no host.
- Docker Desktop aberto e rodando. Se aparecer `Cannot connect to the Docker daemon … Is the docker daemon running?` (ou, em versões novas, `failed to connect to the docker API`), abra o Docker Desktop, espere o "Engine running" e rode de novo.
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
- **Swagger:** `http://127.0.0.1:<API_PORT>/docs` (padrão 8765; a porta real sai de `docker compose port api 8000`). A página certa se chama "API de agendamento de exames (fictícia)"; o `/openapi.json` dela é o que o agente consome. Operações: `create_appointment` (`POST /appointments`), `get_appointment` (`GET /appointments/{appointment_id}`) e `health` (`GET /health`). Corpo de exemplo: `{"exams": [{"code": "FICT-001"}]}` (o `name` é opcional; a API grava e devolve o nome do catálogo). Outro `Host` que não `127.0.0.1`, `localhost` ou `api` recebe `400`. As capturas em [`docs/evidencias/`](evidencias/) mostram a porta 18905, a da gravação.
- **Porta:** a API escuta só em `127.0.0.1`, na porta `API_PORT`. Confira antes se está livre (`netstat -ano | findstr :8765`); se não, ponha outra no `.env` (ex.: `API_PORT=8766`). No Windows, o Docker pode não acusar o conflito.
- **Chave do banco:** com `DB_ENCRYPTION_KEY` vazia, a API cria a chave na 1ª subida, no volume `api-key` (o log mostra `chave do banco criada em /keys/db.key`, nunca a chave). Para usar a sua, gere com `docker compose run --rm --no-deps api python -m api.crypto --gerar-chave` e ponha em `DB_ENCRYPTION_KEY=` antes do 1º `up`; definida, ela vale no lugar da do volume.
- **Pasta:** rode tudo dentro de `rag-local-app`. Fora dela: `no configuration file provided: not found`.
- **`.env`:** edite o arquivo. Não use `export`, `$env:` nem `echo > .env` (este apaga o `API_PORT`).
- **Logs:** `docker compose logs -f ocr rag api`. **Parar:** `docker compose down`.
- **Parar e limpar:** `docker compose --profile cli --profile test down -v` remove containers, redes e volumes (banco, chave e código gerado; sem `--profile cli`, o volume `generated` fica). `--rmi local` apaga também as imagens.

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
docker compose run --rm agent python -m cli transpile specs/minha-spec.json --output generated/minha.py
docker compose run --rm agent python -m cli run --image pedido.png --spec specs/minha-spec.json --agent generated/minha.py
```

Sem `--output`, o novo arquivo substitui o `generated/agent.py`. Se a spec não passar na validação ou o código não
importar, o arquivo anterior fica. Campos, erros e o código gerado comentado: [transpilador.md](transpilador.md).

## 3. Executar o agente

```bash
docker compose run --rm agent python -m cli run --image pedido.png      # chama o Gemini (gemini-3.5-flash, da spec)
```

- **`pedido.png`** está em `samples/`. Antes da saída, o Compose mostra o status dos containers (`Waiting`, `Healthy`).
- **Tempo:** de segundos a alguns minutos, quase todo à espera do Gemini (três agentes em sequência). Já medimos de 11 s a 381 s; no [log das evidências](evidencias/log-run-pedido.txt), 13 s. As linhas `[extract] chamando ...` mostram que não travou.
- **Modelo reserva:** se o principal responde `503` (sobrecarregado) ou `429` (sem cota), a mesma requisição vai na hora ao `fallback_model` da spec (`gemini-3.5-flash-lite`, pelo `FallbackModel` do ADK), com `Aviso: modelo principal indisponível; usando gemini-3.5-flash-lite`. Nenhuma ferramenta rodou na chamada que falhou, então nada é agendado em dobro. O reserva tem as suas 5 tentativas.

Antes de agendar, a CLI mostra a lista e faz uma pergunta para a lista inteira. O padrão é não:

```text
Trechos removidos pelo OCR (não pareciam exame): 3
Exames para agendar:
- Hemograma completo (FICT-001)
- Glicemia de jejum (FICT-002)
- Creatinina (FICT-005)
Agendar estes 3 exames? [s/N] s
```

Com `s`, a saída segue como a do log abaixo (sem as linhas `[extract] chamando ...`). O log foi gravado com `-T` (sem
terminal), antes de existir a pergunta; hoje esse comando precisa de `--yes`. O id muda a cada execução, e `NOME x2`
são o paciente e o médico.

```text
PII reconhecida e mascarada pelo OCR: NOME x2, CPF x1, EMAIL x1, TELEFONE x1
Trechos removidos pelo OCR (não pareciam exame): 3

| Exame              | Código   |
|--------------------|----------|
| Hemograma completo | FICT-001 |
| Glicemia de jejum  | FICT-002 |
| Creatinina         | FICT-005 |

Agendamento confirmado pela API: id eb9a8d89…, status scheduled
Tempo: OCR 2,0 s · busca 4,4 s · agendamento 1,6 s · total 13 s (modelo gemini-3.5-flash)
```

**Confirmação final da lista.** Nada é agendado sem ela.

- Só `s` ou `sim` agenda. Outra resposta, inclusive Enter: `Erro: agendamento bloqueado antes de chamar a API: você não confirmou a lista de exames; nada foi agendado`.
- Sem terminal (`docker compose run -T`, um pipe, `CI=1`) e sem `--yes`, o `run` para antes de ler o pedido e de chamar o modelo: `Erro: sem terminal para confirmar a lista de exames: rode num terminal ou com --yes; nada foi lido nem agendado (…)`.
- **`--yes`** pula a pergunta, para automação, por conta e risco de quem opera: só as regras decidem, e os exames que viriam com aviso ficam de fora (`não agendado sem confirmação`). Valem por inteiro os [limites conhecidos](../README.md#limites-conhecidos).
- O pedido inteiro é conferido antes da pergunta: os avisos vêm acima da lista; os exames que não serão agendados, em `Não agendados:`, com o motivo.

**Avisos da saída.** A "confiança" de um exame é o menor valor entre a nota da busca no catálogo, o quanto a busca
bate com a linha e a leitura que o OCR deu à linha: de 0,90 para cima, o exame entra na lista; de 0,70 a 0,90, entra
com aviso; abaixo, não é agendado. "Conferir" quer dizer: olhe o pedido em papel (a saída mostra as linhas já
mascaradas) e, se ele pede o exame, agende-o à parte.

| Aviso | O que significa | O que fazer |
|---|---|---|
| `PII reconhecida e mascarada pelo OCR: …` | Dados pessoais mascarados, por tipo. É um piso: `nenhuma` não quer dizer que não sobrou nenhum (um nome feito de palavras de exame, como "Albina Ferro", passa). | Nada. |
| `Trechos removidos pelo OCR (não pareciam exame): N` | O que o filtro do OCR tirou do texto; pode conter um nome que as regras não reconheceram. | Nada. |
| `Instruções neutralizadas no OCR: N` | Ordens escondidas no pedido, tiradas do texto (viram `[TEXTO_REMOVIDO]`). | Conferir. |
| `Atenção: o pedido tem texto além da lista de exames: linha 1 "[TEXTO_REMOVIDO]"; confira o papel` | A página não é só a lista de exames; o aviso vem uma vez, no topo, com as linhas que o causam. | Conferir antes de responder. |
| `- IgA (FICT-079): lido "- GA", confiança 0,80; confira` | Confiança de 0,70 a 0,90. Com `--yes`, sai como `não agendado sem confirmação`. | Conferir a linha. |
| `…; o pedido tem outras palavras além do exame; confira` | A linha tem algo além do exame ("Ferritina - pedido por engano"); confiança de no máximo 0,89. Com `--yes`: `não agendado sem confirmação: ...; o pedido tem outras palavras além do exame, confirme`. | Conferir a linha. |
| `não agendado: '...' → Ferritina FICT-018; o pedido diz para não realizar` (ou `que já foi realizado`) | A linha diz para não fazer ("NÃO realizar Ferritina"), que já foi feito ("Resultado de Ferritina: 45") ou marca "não" numa caixa ou tabela. | Conferir. |
| `não agendado: ...; a linha é uma orientação de preparo, não um pedido` | O exame só aparece numa linha de preparo ("jejum de 8 horas para Glicemia"). | Nada. |
| `baixa confiança: '<linha lida>' → <exame> <código> (confiança 0,xx); confira o pedido` | Confiança abaixo de 0,70. | Conferir. |
| `não agendado: '<linha>' → <exame> <código>; o mesmo trecho da linha já foi usado por <outro exame>; confira o pedido` | Cada exame precisa de um trecho próprio: "Hemoglobina" dentro de "Hemoglobina glicada", escrito uma vez, vale um só. | Conferir. |
| `lido mas não reconhecido no catálogo: linha N; confira o pedido` | Item da lista que não parece exame do catálogo; só o número da linha sai do OCR. | Conferir. |
| `não incluído pelo agente: '<linha lida>' → <exame> <código> (confiança 0,xx); confira o pedido` | A busca achou o exame e o modelo o deixou de fora. | Conferir. |
| `não buscado pelo agente: ...; confira o pedido` | A conferência do pedido inteiro, feita em código no RAG depois da execução, achou um exame que o modelo nem buscou. | Conferir. |
| `…, status scheduled; ATENÇÃO: N possível(is) exame(s) do pedido sem decisão do agente, confira os avisos acima` | Há `não incluído` ou `não buscado`. O código de saída continua 0: o agendamento existe. | Ler os avisos acima. |
| `Aviso: o pedido não foi conferido por inteiro` | A conferência do pedido inteiro passou de 30 s (o RAG travou). | Conferir o pedido todo. |

Todos os estados de um exame e a regra de cada um: [arquitetura.md](arquitetura.md#agendamento-conferido-em-código).
Exemplos de linhas (negação, caixas, preparo, vários exames numa linha como "TSH, T4 livre" ou "Toxoplasmose IgG e
IgM") e os limites: [regras.md](regras.md) e [medicoes.md](medicoes.md#limites-conhecidos).

**A linha `Tempo:`** traz cada etapa (buscas em paralelo contam uma vez), o total e o modelo que respondeu por último;
aparece também antes de um `Erro:` e não traz dado do pedido. Cada etapa inclui o turno do modelo que gera a chamada
da ferramenta, então cresce com o Gemini lento. O total é o relógio do `run` inteiro, com as novas tentativas do
Gemini (até 5) e a espera pelo `[s/N]`: pode passar bem da soma das etapas.

**Outras opções:**

- Outro modelo, sem editar a spec (ou `GEMINI_MODEL=` no `.env`, para todas as execuções):
  `docker compose run --rm -e GEMINI_MODEL=<modelo> agent python -m cli run --image pedido.png`
- Logs das bibliotecas (ADK, MCP, novas tentativas do Gemini) no stderr e, numa falha inesperada, o traceback, com a chave trocada por `[GOOGLE_API_KEY]` (vale em `run` e `transpile`):
  `docker compose run --rm agent python -m cli run --image pedido.png --verbose`

### Testar outra imagem

Coloque o arquivo em `samples/` (montada no container de OCR, sem rebuild) e rode:

```bash
docker compose run --rm agent python -m cli run --image <arquivo>
```

- **Formato:** `.png`, `.jpg` ou `.jpeg`, até 5 MB e 25 megapixels. Só o nome, sem pastas. Outra extensão é recusada antes do Gemini (`Erro: --image: "pedido.pdf" não é uma imagem aceita; use .png, .jpg ou .jpeg`); um PDF renomeado, pelo OCR (`O arquivo é um PDF, não uma imagem: exporte a página como PNG ou JPEG.`).
- **Foto de lado ou de cabeça para baixo** é endireitada. Se nem assim der: `imagem de lado ou de cabeça para baixo: gire e envie de novo`. PNG transparente é lido sobre branco.
- **Arquivo inexistente ou recusado:** a CLI pergunta ao OCR antes do Gemini, então o erro sai em segundos: `Erro: OCR recusou a imagem: Arquivo "x.png" não encontrado em /data/samples.; nada foi agendado`. Só uma página que o Tesseract não consegue endireitar é recusada durante a execução. O nome vai só ao OCR; o modelo recebe um apelido (`pedido-1.png`).
- **A suíte não muda:** os testes usam a lista fixa dos arquivos versionados ([`tests/versionados.py`](../tests/versionados.py), [`test_versionados.py`](../tests/test_versionados.py)).

Exemplos em `samples/`:

- **`pedido-realista.png`:** cabeçalho de clínica, CPF, telefone, CID, convênio, data, CRM e 4 exames numa fonte que imita letra de mão. Glicemia e TSH são agendados; Colesterol total e Hemoglobina glicada saem em `baixa confiança` (`(confiança 0,68)`), porque o OCR leu essas linhas com 68 e 60, abaixo do piso de 75. É o esperado: o que não foi lido com segurança vai para conferência humana.
- **`pedido-sem-exame.png`:** nenhum exame, o caso (b) do [teste de alucinação](evidencias/log-alucinacao.txt): `Erro: Nenhum exame encontrado no pedido; nada foi agendado`. Gerado por `exemplos/gerar_pedido_sem_exame.py`, com semente fixa.
- **`pedido-variacao.png`:** nome sem rótulo, marcadores, sinônimo `Glicose`, telefone sem rótulo, data e CRM.
- **`ataque-injecao.png` e `ataque-exame-disfarcado.png`:** instruções escondidas; só os exames legítimos são agendados ([Segurança em detalhe](arquitetura.md#segurança-em-detalhe)).
- **`pedido-manuscrito.png` e `pedido-manuscrito-dificil.png`:** letra de mão e foto de celular, simuladas. No primeiro, o OCR lê os 5 exames; no segundo ("letra de médico"), quase nada, e nada é agendado sem confirmação.

### Pedidos manuscritos simulados

`samples/manuscritos/` tem 120 pedidos fictícios com aparência de letra de mão: 70 de letra comum e 50 de "letra de
médico" (inclinada, abreviada, com carimbo e assinatura). **São simulados com fontes de letra de mão do Windows (Ink
Free, Segoe Print, Segoe Script), não escrita real.** 93 das 120 (77%) imitam foto ruim de celular; o resto, scan
limpo. O `gabarito.json` diz os exames, a PII e a degradação de cada imagem (o CPF tem o dígito verificador errado de
propósito). Para gerá-las de novo (no Windows, mesma semente):

```bash
python exemplos/gerar_manuscrito.py --saida samples/manuscritos --comum 70 --medico 50
```

Para passar as 120 pelo OCR e pelo RAG, sem Gemini, com a regra real do agente:

```bash
docker compose -f docker-compose.yml -f tests/load/compose.carga.yml -p carga run --rm tests python -m tests.load.manuscritos
```

- **Resultado atual:** 113 de 497 exames agendados sem perguntar (23%), 37 perguntados, **0 errados sem confirmação** e 0 PII sobrando. Letra de médico: 1 de 206 exames agendado sem perguntar.
- Uma troca entre exames do catálogo (`TGP` lido como `TAP`) vira pergunta.
- Tabela por grupo em [medicoes.md](medicoes.md#pedidos-manuscritos-simulados). Na CI, `tests/test_manuscritos.py` roda 10 das 120 e falha com um exame agendado errado sem confirmação ou PII sobrando.

### Robustez: entradas faltando ou quebradas

602 entradas quebradas pelo caminho real (imagens em branco, giradas, truncadas, de extensão errada; consultas vazias,
enormes ou de tipo errado; corpos inválidos e 50 POSTs simultâneos na API):

```bash
docker compose -f docker-compose.yml -f tests/load/compose.carga.yml -p carga run --rm tests python -m tests.load.robustez --variantes 12
```

A saída mostra, por categoria, os casos ok, os recusados com mensagem clara e os que falharam (erro 500, traceback,
PII, exame agendado fora da imagem, mensagem pouco clara). `--caso <id>` refaz um caso. Resultado em
[medicoes.md](medicoes.md#robustez-entradas-faltando-ou-quebradas).

## 4. Rodar com `adk run` ou `adk web`

O agente gerado também roda com as ferramentas do ADK, sem a CLI do projeto: o `transpile` grava `generated/agent.py`
e `generated/__init__.py`, e o `agent.py` expõe `root_agent` e um `app` retomável (a confirmação da lista pausa e
retoma a mesma chamada de agendamento).

```bash
docker compose run --rm agent python -m cli transpile specs/agent.json   # gera de novo depois de atualizar o projeto
docker compose run --rm agent adk run --in_memory generated              # no [user]:, digite só o nome do arquivo: pedido.png
docker compose run --rm -p 127.0.0.1:8000:8000 agent python -m runtime.web --host 0.0.0.0 generated   # o adk web, com checagem de Host
```

No `adk web`, abra <http://127.0.0.1:8000>, escolha o agente `generated` e mande `pedido.png`. Com a porta 8000
ocupada, troque só o primeiro número (ex.: `127.0.0.1:8090:8000`).

- **`python -m runtime.web`** ([`runtime/web.py`](../runtime/web.py)) é o `adk web --no_use_local_storage` que só atende `Host` `127.0.0.1` ou `localhost` (outro recebe 400). Uma entrada que o ADK recusa, como uma confirmação forjada, recebe 400 numa linha: `Requisição inválida para esta sessão (ex.: confirmação forjada).`
- **`--in_memory` e `--no_use_local_storage`** mantêm a sessão em memória, em vez de gravá-la em `generated/.adk/` (com o texto do OCR mascarado e o nome real do arquivo). No ADK 2.10, `--no_use_local_storage` não combina com `--session_service_uri` nem `--artifact_service_uri`.
- **A mensagem:** só o nome de um arquivo de `samples/`, como texto (`pedido.png` ou `agende o pedido pedido.png`). Sem nome, com pasta ou com dois arquivos: `Informe só o nome de um arquivo de pedido em samples/ …`; com algo além de texto: `Envie só o nome do arquivo do pedido, como texto…`. O modelo não é chamado.
- **Antes do 1º turno** (`start_order`, em [`runtime/callbacks.py`](../runtime/callbacks.py)), como no `cli run`: o modelo recebe só o apelido (`Arquivo do pedido: pedido-1.png`), os hosts de `ALLOWED_HOSTS` são conferidos e o OCR confere a imagem (`check_image`). Os endereços conferidos ficam fixos enquanto o `adk` durar ([`runtime/rede.py`](../runtime/rede.py)): se um `docker compose up` recriar um serviço, reinicie o `adk run` ou o `adk web`.
- **Confirmação da lista:** a mesma da CLI, pela confirmação nativa do ADK, sem `--yes`. O `adk run` mostra `[HITL confirm]` com os avisos, a lista e `Agendar estes N exames?`; `yes` agenda, outra resposta não. O `adk web` mostra a lista, uma caixa "Confirmed" e "Submit": marcada, agenda. Um "sim" que chega depois do agendamento não gera um 2º `POST` (`não agendado (você confirmou, mas o agendamento desta execução já tinha sido criado)…`).
- **A última mensagem** (`[clinic_scheduler]: …`) é escrita em código com o que as ferramentas devolveram; `Agendamento confirmado pela API: id …` vem só da resposta da API. O texto do modelo (`[schedule]: …`) não conta.
- **Um pedido por sessão:** outra mensagem recebe `Esta sessão já tratou um pedido…`; se a API já agendou, a resposta traz o agendamento e `não repita este pedido`. Para outro pedido: `exit` e `adk run` de novo; no `adk web`, New Session. O estado da sessão, que o cliente pode escrever (`adk web`, `adk run --state`), não decide nada: o runtime guarda o pedido num registro próprio por sessão.
- **Modelo reserva e erros:** como no `cli run`; a última mensagem termina em `…; nada foi agendado` quando nada foi.
- **Só na CLI:** a checagem de que o `agent.py` é o que a spec gera hoje, a lista de ferramentas vivas antes do 1º turno e a linha `Tempo:`. As regras de agendamento valem igual.
- **Dependências do `agent.py`:** o Google ADK e a biblioteca `runtime/` do projeto (com `catalogo.py` e `leitura.py`), versionada por `API_VERSION` (hoje 6). Não é instalada pelo pip: o `adk run generated` na raiz põe a raiz no `sys.path`; no container, `PYTHONPATH=/app`.
- **O `adk web` não tem login:** publique a porta só em `127.0.0.1`, como acima.
- **Fora do Docker:** rode `adk telemetry disable` (a imagem já responde "não" à pergunta de telemetria, em que Enter diria sim) e defina `GOOGLE_API_USE_CLIENT_CERTIFICATE=false`, como o `docker-compose.yml` faz em `agent`, `tests` e `tests-e2e`: sem ela, o cliente MCP do ADK 2.10 procura credenciais do Google a cada conexão (inclusive em `169.254.169.254`).
- **Testes:** [`tests/test_adk_run.py`](../tests/test_adk_run.py) (o `adk run` e o `adk web` com modelos roteirizados, MCP e API reais), [`tests/test_adk_seguranca.py`](../tests/test_adk_seguranca.py) (um cliente que tenta forjar estado e confirmação) e [`tests/test_adk_comandos.py`](../tests/test_adk_comandos.py) (as linhas deste guia, como estão escritas).

O porquê de cada defesa, e os limites do registro por sessão: [Segurança em detalhe](arquitetura.md#segurança-em-detalhe).

## Variáveis de ambiente

Todas as que o código lê. As do `.env` chegam só ao serviço que as usa; as outras já têm o valor certo nos containers e só mudam fora do Docker (por exemplo, no `pytest` local).

| Variável | Padrão | Quem lê | O que faz |
|---|---|---|---|
| `GOOGLE_API_KEY` | vazia | `agent` (`cli run`) | Chave da Gemini API. Sem ela, o `run` para antes de chamar o modelo. |
| `GEMINI_MODEL` | vazia: o modelo da spec | `agent` e `tests-e2e` | Outro modelo Gemini numa execução, sem editar a spec (a mesma regra de nome da spec vale para ela). |
| `ALLOWED_HOSTS` | vazia: `ocr:8001,rag:8002,api:8000` | `agent` (`transpile`, `run`, `adk run` e `adk web`) | Hosts que os servidores de uma spec podem usar: `host` para qualquer porta, `host:porta` para uma. |
| `DB_ENCRYPTION_KEY` | vazia: a `api` cria uma no volume `api-key` | `api` | Chave AES-256 que cifra as listas de exames no banco (`python -m api.crypto --gerar-chave` cria uma). |
| `API_RATE_LIMIT_PER_MINUTE` | `1200` | `api` | Requisições por minuto por IP; acima disso, `429` com `Retry-After`. `0` desliga. |
| `API_ALLOWED_HOSTS` | vazia: `127.0.0.1,localhost,api` | `api` | Nomes aceitos no cabeçalho `Host`, em qualquer porta; outro recebe `400` (contra DNS rebinding). Para chamar a API por outro nome (um proxy), inclua-o aqui. |
| `API_IDEMPOTENCY_TTL_HOURS` | `24` | `api` | Horas que uma `Idempotency-Key` vale (guardada só como HMAC); depois, a mesma chave agenda de novo. |
| `API_PORT` | `8765` | Compose | Porta da API no host, só em `127.0.0.1`. |
| `DB_PATH` | `/state/appointments.db` | `api` | Arquivo SQLite (no volume `api-data`). |
| `DB_KEY_FILE` | `/keys/db.key` | `api` | Onde fica a chave criada na 1ª subida (no volume `api-key`). |
| `EXAMS_PATH` | `data/exams.json` | `api`, `rag`, `ocr` (`catalogo.py`) | Catálogo de exames. |
| `SAMPLES_DIR` | `/data/samples` | `ocr` | Pasta das imagens que o OCR aceita (`samples/`, só leitura; a carga monta outra). |
| `OMP_THREAD_LIMIT` | `1`, definida pelo `ocr` | Tesseract | Uma thread por leitura: com 8 leituras em paralelo, o Tesseract não disputa os núcleos. |
| `CI` | vazia | `cli run` | Não vazia (como no GitHub Actions): nenhuma pergunta, como sem terminal; sem `--yes`, nada é agendado. O `--yes` chega ao agente pelo registro do pedido, não por variável. |

## Backup e restauração

O banco (SQLite) fica no volume `api-data`; a chave que cifra as listas de exames, no volume `api-key` (ou em
`DB_ENCRYPTION_KEY`). A cópia do banco leva os exames cifrados, nunca a chave. Guarde as duas **separadas** (a chave
num cofre de senhas): quem tem as duas lê os dados, e a restauração precisa das duas.

1. **A chave, uma vez** (ela não muda). Se você definiu `DB_ENCRYPTION_KEY`, é essa; senão, copie a do volume, guarde o conteúdo longe da cópia do banco e apague o arquivo:
   ```bash
   docker compose cp api:/keys/db.key ./db.key
   ```
2. **A cópia do banco, com a API no ar** (crie a pasta uma vez, com `mkdir backup`):
   ```bash
   docker compose exec api python -m api.backup --saida backup.db
   docker compose cp api:/state/backup.db ./backup/appointments.db
   docker compose exec api sh -c "rm /state/backup.db"
   ```
   - `--saida` e `--entrada` recebem só um nome de arquivo, gravado em `/state`, o lugar gravável da API (os containers são somente leitura). Assim os comandos valem no PowerShell e no Git Bash, que reescreveria um argumento começado por `/` (por isso o `rm` vai entre aspas). Pasta ou caminho: `use só um nome de arquivo, sem pasta nem caminho (ex.: backup.db)`.
   - A cópia usa a API de backup do SQLite, que inclui os agendamentos ainda no WAL; copiar só o `.db` os perderia. `--saida` nunca sobrescreve um arquivo.
   - **Saída:** `cópia gravada em /state/backup.db: 3 agendamento(s), com os exames cifrados; a chave do banco não vai na cópia (guarde-a à parte)`. O `.gitignore` já ignora `*.db` e `*.key`.
3. **A restauração, com a API parada:**
   ```bash
   docker compose cp ./backup/appointments.db api:/state/restore.db
   docker compose stop api
   docker compose run --rm --no-deps api python -m api.backup --entrada restore.db
   docker compose up -d --wait
   docker compose exec api sh -c "rm /state/restore.db"
   ```
   - **A chave primeiro:** com o volume `api-key` intacto, nada a fazer; numa máquina nova, ponha a chave em `DB_ENCRYPTION_KEY=` antes. Sem chave, nada é restaurado (nem criada outra): `Erro: chave do banco não encontrada (…): restaure primeiro a chave da gravação; nada foi restaurado`.
   - **Conferida antes de gravar:** `integrity_check` do SQLite e cada agendamento decifrado com a chave atual. Chave errada: `Erro: não foi possível decifrar o registro: a chave não é a da gravação ou o dado foi alterado no banco; nada foi restaurado`.
   - **Banco com dados:** só é trocado com `--substituir` no fim do comando; o que entrou depois da cópia se perde, inclusive as `Idempotency-Key`.
   - **API no ar:** recusada, porque a API mantém o banco aberto: `Erro: /state/appointments.db está em uso pela API: pare-a (docker compose stop api); nada foi restaurado`.
   - **Saída:** `banco restaurado em /state/appointments.db: 3 agendamento(s), todos decifrados com a chave atual`. Testes em [`test_backup.py`](../tests/test_backup.py).

## Quando algo falha

Erros saem como uma linha `Erro: ...`, com código 2. Para ver o que houve por trás, rode de novo com `--verbose`.
Tabela completa em [arquitetura.md](arquitetura.md#tratamento-de-erros).

| Sintoma | O que fazer |
|---|---|
| `no configuration file provided: not found` | Entre em `rag-local-app`, onde está o `docker-compose.yml`. |
| `Cannot connect to the Docker daemon … Is the docker daemon running?` | Abra o Docker Desktop e espere o "Engine running". |
| `Read timed out` do pip no 1º build | É a rede até o PyPI. Rode o `docker compose up -d --wait` de novo: os estágios prontos ficam no cache. |
| `all predefined address pools have been fully subnetted` | Redes de outros projetos ocupam todas as faixas do Docker. Ver abaixo. |
| `Healthy`, mas o Swagger não se chama "API de agendamento de exames (fictícia)" | Outro programa responde na porta: troque o `API_PORT`. |
| `Erro: OCR recusou a imagem: <motivo>; nada foi agendado` | Corrija o arquivo conforme o motivo: `Arquivo "x.png" não encontrado em /data/samples.`, `Imagem corrompida ou incompleta.`, `O conteúdo do arquivo não corresponde à extensão (use PNG ou JPEG).` ou `Arquivo grande demais (máximo 5 MB).`. |
| `OCR ocupado com outras imagens; tente de novo em instantes.` | O OCR lê 3 imagens por vez, e um pedido espera até 20 s por vaga: tente de novo. Cada serviço tem teto de memória, CPU e processos (`mem_limit`, `cpus`, `pids_limit`; o OCR, 1,5 GiB); passar dele reinicia só aquele container. |
| `Aviso: modelo principal indisponível; usando gemini-3.5-flash-lite` | Não é erro: o principal deu `429`/`503` e a requisição foi ao `fallback_model`. |
| `Erro: Gemini indisponível no momento (HTTP 503); tente novamente` | O reserva também falhou (as falhas temporárias, como `500`, já foram tentadas até 5 vezes, com espera crescente). Tente mais tarde. |
| `Erro: o Gemini recusou a chamada (HTTP 404: ...)` | Modelo descontinuado: troque com `-e GEMINI_MODEL=<modelo>`. |
| `Erro: Nenhum exame encontrado no pedido; nada foi agendado` | A imagem não tem exame reconhecível. |
| `Erro: agendamento bloqueado antes de chamar a API: …; nada foi agendado` | Um código não veio de uma busca no catálogo, nenhum exame entrou na lista ou você não confirmou a lista. |
| `Erro: o OCR não devolveu o texto do pedido (serviço indisponível?); nada foi agendado` | O OCR caiu no meio da execução: veja `docker compose logs -f ocr rag api`. |

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

## Testes

```bash
docker compose run --rm tests pytest -q -n auto
```

- **Serviço `tests`:** a imagem do `agent` mais pytest, ruff, mypy, o Tesseract e os testes (estágio `test` do `Dockerfile`). Nunca recebe a `GOOGLE_API_KEY`: nada chama o Gemini, e o teste ponta a ponta é pulado.
- **Ponta a ponta real, só quando pedido:** `docker compose run --rm tests-e2e` roda [`tests/test_e2e.py`](../tests/test_e2e.py) com a chave: o `run` inteiro com o Gemini sobre `pedido.png` precisa agendar exatamente `FICT-001`, `FICT-002` e `FICT-005`, e o `GET` do agendamento precisa devolver os mesmos. Sem a chave, falha (não é pulado).
- **Resultado atual:** 607 funções de teste e 19.366 casos (`pytest --collect-only`), quase todos de corpus parametrizado (linhas legítimas, PII gerada, ataques, termos do catálogo); 19.365 passam e 1 é pulado (o ponta a ponta). Com `-n auto` e `--cov`, cerca de 3,5 min numa máquina de 12 núcleos (medido: 200 s e 205 s); em série, de 6 a 11 min.
- **O que cobre:** specs e código gerado; a saída da CLI; o OCR e cada tipo de PII; a busca do RAG; as ferramentas MCP via SSE; a API ([`test_api_headers.py`](../tests/test_api_headers.py), [`test_crypto.py`](../tests/test_crypto.py), [`test_idempotencia.py`](../tests/test_idempotencia.py), [`test_backup.py`](../tests/test_backup.py)); o detector de injeção ([`tests/test_injection.py`](../tests/test_injection.py)) com o corpus de `tests/attacks/` (790 ataques e 1.482 linhas legítimas, 1.429 distintas); as faixas de confiança e a pergunta `[s/N]` ([`test_confianca.py`](../tests/test_confianca.py)); o preparo da imagem ([`test_preprocessamento.py`](../tests/test_preprocessamento.py)); o agente gerado com MCP real ([`test_agent_mcp.py`](../tests/test_agent_mcp.py)) e com `adk run`/`adk web` ([`test_adk_run.py`](../tests/test_adk_run.py)); o limiar de 0,90 sobre as 631 consultas de calibração ([`test_calibration.py`](../tests/test_calibration.py)); e uma fração da carga (20 pedidos), dos manuscritos (10 de 120) e da robustez (1 caso por categoria): [`test_carga.py`](../tests/test_carga.py), [`test_manuscritos.py`](../tests/test_manuscritos.py), [`test_robustez.py`](../tests/test_robustez.py).
- **Qualidade na CI:** `ruff` (pyflakes, pycodestyle, ordem dos imports, bugbear) e `mypy` (sem `--strict`, com `ignore_missing_imports` e `check_untyped_defs`; config em [`pyproject.toml`](../pyproject.toml)); cobertura como relatório, sem limite que quebre o build: 98% das linhas de `api`, `catalogo`, `cli`, `guardrails`, `mcp_servers`, `runtime` e `transpiler` (sem o `agent.py` gerado). O `pip-audit` confere `requirements.txt`, `requirements-dev.txt` e `constraints.txt` e falha numa vulnerabilidade que já tem correção; o Dependabot abre os PRs de atualização toda semana. Para rodar local:

  ```bash
  docker compose run --rm --no-deps tests ruff check .
  docker compose run --rm --no-deps tests mypy
  docker compose run --rm tests pytest -q -n auto --cov --cov-report=term
  ```
- **Avisos filtrados:** o `pyproject.toml` filtra, cada um pelo nome, a depreciação do `SequentialAgent` ([Decisões técnicas](arquitetura.md#decisões-técnicas-em-detalhe)) e os avisos `[EXPERIMENTAL]` do ADK sobre os recursos em uso, que cada processo do `-n auto` repetiria. Um aviso novo continua aparecendo.
