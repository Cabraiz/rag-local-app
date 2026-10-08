# Como rodar: guia completo

O resumo e os 4 comandos estão no [README](../README.md#rodar-em-4-comandos). Este guia traz os
pré-requisitos, as variações e o que fazer quando algo falha. Este repositório é só o desafio, com um
`Dockerfile` e um `docker-compose.yml` na raiz.

## Pré-requisitos

- Docker com Compose ≥ 2.1.1 (`up --wait`). Não é preciso Python no host.
- Docker Desktop aberto e rodando. Se aparecer `Cannot connect to the Docker daemon … Is the docker daemon running?` (ou, em versões novas, `failed to connect to the docker API`), abra o Docker Desktop, espere o "Engine running" e rode o comando de novo.
- No Windows, PowerShell ou Git Bash. O `cmd.exe` não é suportado: nele o `cp` não existe e o comentário depois do `#` vira argumento.
- Internet no build (apt e pip) e na execução (Gemini).
- Uma chave da API Gemini, criada em <https://aistudio.google.com/apikey>. Funciona com a chave do plano gratuito; para dados reais, use a API paga: no gratuito, o Google pode usar o conteúdo para melhorar produtos ([licencas.md](licencas.md#dados-e-ia)).

## 1. Subir o ambiente Docker

```bash
git clone https://github.com/Cabraiz/rag-local-app
cd rag-local-app
cp .env.example .env          # preencha GOOGLE_API_KEY=; a chave só vai para os serviços agent e tests-e2e
docker compose up -d --wait   # sobe ocr, rag e api; na 1ª vez a api cria a chave do banco
```

- **Tempo:** o primeiro build, sem cache, leva de 10 a 20 minutos, por causa do Tesseract; os builds seguintes usam cache.
- **Chave do banco:** com `DB_ENCRYPTION_KEY` vazia no `.env`, a API cria a chave na 1ª subida e a guarda no volume `api-key`, separado do banco (o log da `api` mostra `chave do banco criada em /keys/db.key`, nunca a chave). Para usar a sua, gere uma com `docker compose run --rm --no-deps api python -m api.crypto --gerar-chave` e coloque em `DB_ENCRYPTION_KEY=` antes do 1º `up`; definida, ela tem precedência sobre o volume.
- **Pasta:** rode tudo dentro de `rag-local-app`, onde está o `docker-compose.yml`. Fora dela, o Compose responde `no configuration file provided: not found`.
- **Saída esperada:** `ocr`, `rag` e `api` como `Healthy`. Swagger em `http://127.0.0.1:<API_PORT>/docs` (padrão 8765; a porta real sai de `docker compose port api 8000`, em que 8000 é a porta interna do container). A página certa se chama "API de agendamento de exames (fictícia)". O `/openapi.json` é o mesmo que o agente consome. Operações: `create_appointment` (`POST /appointments`), `get_appointment` (`GET /appointments/{appointment_id}`) e `health` (`GET /health`). Corpo de exemplo: `{"exams": [{"code": "FICT-001"}]}` (o `name` de cada exame é opcional; a API grava e devolve o nome do catálogo). A API só aceita `Host` de `API_ALLOWED_HOSTS` (padrão `127.0.0.1`, `localhost` e `api`); outro nome recebe `400`. As capturas em [`docs/evidencias/`](evidencias/) mostram a porta 18905 da gravação.
- **Porta:** a API só escuta em `127.0.0.1`, na porta do host `API_PORT` (padrão 8765, configurável no `.env`). Antes do `up`, confira se ela está livre (`netstat -ano | findstr :8765`). Se estiver em uso, defina outra em `API_PORT` no `.env` (ex.: `API_PORT=8766`): no Windows, o Docker pode não acusar o conflito, e outro programa continua respondendo nessa porta.
- **`.env`:** para a chave e a porta, edite o arquivo `.env`. Não use `export`, `$env:` nem `echo > .env`, que sobrescreve o arquivo e apaga o `API_PORT`.
- **Logs dos serviços:** `docker compose logs -f ocr rag api`.
- **Parar:** `docker compose down`.
- **Parar e limpar:** `docker compose --profile cli --profile test down -v` remove os containers, as redes e os volumes: o banco, a chave do banco e o código gerado. Sem `--profile cli`, o volume `generated` do agent fica.
- **Imagens:** o `down -v` não apaga as imagens. Para apagá-las também, acrescente `--rmi local`.

## 2. Rodar o transpilador

```bash
docker compose run --rm agent python -m cli transpile specs/agent.json   # 1º run constrói a imagem do agent (sem cache: 2 a 6 min)
docker compose run --rm agent cat generated/agent.py                     # o arquivo fica no volume "generated"
```

```text
OK: generated/agent.py gerado e importado; root_agent "clinic_scheduler" (SequentialAgent: extract -> search -> schedule)
```

A pasta `generated/` do host fica vazia: o arquivo está no volume Docker `generated`, que só os containers do `agent` montam. O 2º comando acima o mostra. Não é uma pasta do host porque o container roda como um usuário sem privilégios (uid 10001), que no Linux não poderia escrever numa pasta do host.

**Sua própria spec, sem rebuild.** A pasta `specs/` do host é montada só para leitura no `agent`: salve a spec ali e transpile pelo caminho dela. Uma edição vale no próximo comando, sem reconstruir a imagem.

```bash
cp specs/listar-exames.json specs/minha-spec.json                         # ou escreva a sua do zero
docker compose run --rm agent python -m cli transpile specs/minha-spec.json --output generated/minha.py
docker compose run --rm agent python -m cli run --image pedido.png --spec specs/minha-spec.json --agent generated/minha.py
```

Sem `--output`, o novo arquivo substitui o `generated/agent.py`. Se a spec não passar na validação ou o código gerado não importar, o arquivo anterior fica como estava.

Campos da spec, mensagens de erro e o código gerado comentado: [transpilador.md](transpilador.md).

## 3. Executar o agente

```bash
docker compose run --rm agent python -m cli run --image pedido.png      # chama o Gemini (gemini-3.5-flash, da spec)
```

Um `run` leva de segundos a alguns minutos, conforme a fila do Gemini: quase todo o tempo é à espera dele (três agentes em sequência). Já medimos de 11 s a 381 s; no [log das evidências](evidencias/log-run-pedido.txt), 13 s. Com o Gemini lento, a busca e o agendamento crescem, porque incluem o tempo do modelo para gerar cada chamada. Não travou: as linhas `[extract] chamando ...` mostram o progresso.

**Modelo reserva, um caminho normal.** A spec traz um `fallback_model` (`gemini-3.5-flash-lite`). Se o principal responde sobrecarregado (`503`) ou sem cota (`429`), a mesma requisição vai na hora ao `fallback_model` da spec (`gemini-3.5-flash-lite`, pelo `FallbackModel` do ADK que o `agent.py` declara), sem esperar novas tentativas do principal: aparece `Aviso: modelo principal indisponível; usando gemini-3.5-flash-lite` e a execução segue; a linha `Tempo:` diz qual modelo respondeu por último. Não agenda em dobro nem pergunta de novo: o que se repete é a chamada ao modelo, e nenhuma ferramenta rodou na chamada que falhou. O reserva tem as suas 5 tentativas. É o mesmo `app` que o `adk run` roda: a CLI não muda nada no `agent.py` que importa.

`pedido.png` está em `samples/`. A saída abaixo é a desse log, sem as linhas `[extract] chamando ...`. O id muda a cada execução, e `NOME x2` são o paciente e o médico.

```text
PII mascarada pelo OCR: NOME x2, CPF x1, EMAIL x1, TELEFONE x1
Trechos removidos pelo OCR (não pareciam exame): 3

| Exame              | Código   |
|--------------------|----------|
| Hemograma completo | FICT-001 |
| Glicemia de jejum  | FICT-002 |
| Creatinina         | FICT-005 |

Agendamento confirmado pela API: id eb9a8d89…, status scheduled
Tempo: OCR 2,0 s · busca 4,4 s · agendamento 1,6 s · total 13 s (modelo gemini-3.5-flash)
```

- **Linhas antes da tabela:**
  - `PII mascarada pelo OCR` conta só dados pessoais, cada um pela regra que o reconheceu: `NOME` só quando uma regra de nome o viu (um rótulo como `Paciente:`, palavras com maiúscula ao lado de um exame, um prenome comum). Palavras comuns que a rede de segurança tira ("NAO realizar", "autoriza incluir") não contam como nome;
  - `Trechos removidos pelo OCR (não pareciam exame): N` conta o resto do que a rede de segurança tirou do texto. Não é PII pelas regras, mas pode conter um nome que elas não reconheceram (um sobrenome sem prenome comum): por isso `NOME xN` é um piso, não o total de nomes;
  - se o OCR removeu instruções escondidas, aparece também `Instruções neutralizadas no OCR: N`;
  - um item da lista de exames que o OCR leu, mas que não parece nenhum exame do catálogo ("4) Ressonância magnética de crânio"), aparece como `lido mas não reconhecido no catálogo: linha N; confira o pedido`. Só o número da linha: o texto dela não sai do OCR.
- **O que o pedido diz de cada exame:** antes de mascarar, o OCR lê cada linha como foi escrita. Só agenda sozinha uma linha que é só exame (com marcador de lista, rótulo como "Exames:" e qualificadores do exame, como "completo", "de jejum" ou "8h"); qualquer outra palavra vira pergunta:
  - "Ferritina - pedido por engano", "Vitamina B12 (laudo anexo)", "Colesterol total ?", "Hemograma completo sem plaquetas": `Li "..." → Ferritina FICT-018 (confiança 0,89); o pedido tem outras palavras além do exame. Incluir? [s/N]`. Com `--yes`: `não agendado sem confirmação: ...; o pedido tem outras palavras além do exame, confirme`;
  - numa linha que diz claramente para não fazê-lo ("NÃO realizar Ferritina", "PSA total - não repetir", "anulado") ou que ele já foi feito ("já realizado em 2025", "Resultado de Ferritina: 45"), o exame não é agendado e aparece como `não agendado: '...' → Ferritina FICT-018; o pedido diz para não realizar` (ou `que já foi realizado`). Vale também para uma caixa ou célula de tabela que diz não ("[-] TSH", "TSH | -", "não" ou célula vazia); "sim", "x" ou "✓" numa tabela é perguntado;
  - embaixo de "Não realizar:" ou "Não realizar os seguintes:", ou com "retirar o item 2", os itens são perguntados;
  - numa linha de preparo ("Preparo: jejum de 8 horas para Glicemia de jejum"), o exame não é agendado por ela e é avisado com esse motivo.
  A pergunta mostra a linha já mascarada: confira o pedido em papel. Detalhes e limites em [medicoes.md](medicoes.md#limites-conhecidos).
- **Faixas de confiança:**
  - de 0,70 a 0,90, o exame é perguntado no terminal: `Li "<linha lida>" → <exame> <código> (confiança 0,82). Incluir? [s/N]`. Só entra o que você confirmar;
  - com `--yes` (ou sem terminal interativo, como em CI), esses exames ficam de fora e aparecem como `não agendado sem confirmação`;
  - abaixo de 0,70, o exame não é agendado e aparece como `baixa confiança: '<linha lida>' → <exame> <código> (confiança 0,xx); confira o pedido`.
- **Um exame por ocorrência no pedido:**
  - cada nome do catálogo ocupa uma ocorrência própria na linha: "Exames: Hemograma completo, Creatinina e TSH" agenda 3, e "Creatinina, Clearance de creatinina" agenda 2;
  - um nome que só aparece dentro de outro ("Hemoglobina" em "Hemoglobina glicada", escrito uma vez) vale um só, como uma linha que só se parece com várias buscas;
  - um exame que repete um trecho já usado aparece como `não agendado: '<linha>' → <exame> <código>; o mesmo trecho da linha já foi usado por <outro exame>; confira o pedido`.
  - um "e" que o OCR grudou no exame não junta dois exames num trecho só: em "2) Ureiae Creatinina", a busca por "Ureia" acha o pedaço "Ureiae" (0,91) e Ureia é agendada junto com Creatinina.
- **Exame que o modelo deixou de fora:** se a busca o achou e o modelo não o incluiu, ele aparece como `não incluído pelo agente: '<linha lida>' → <exame> <código> (confiança 0,xx); confira o pedido`. Não é agendado, só avisado.
- **Vários exames numa linha** ("Colesterol total e Triglicerideos", "TSH, T4 livre"): a linha é buscada exame por exame, e cada um é agendado, perguntado ou avisado por conta própria.
  - Isso vale também quando o OCR grudou o "e" numa palavra: em "TSHe T4 livre", T4 livre é agendado e TSH, com 0,86, é perguntado.
  - Um pedaço que é parte do exame vizinho é buscado como esse exame. "Toxoplasmose IgG e IgM" agenda Toxoplasmose IgG e Toxoplasmose IgM, nunca a IgM genérica; "IgG e IgM para toxoplasmose" também.
  - "PSA total e livre" agenda PSA total e PSA livre, e "Vitamina B12 e D" agenda as duas vitaminas.
  - Em "Clearance de creatinina, urina 24h", "urina 24h" é a amostra do exame, não outro exame.
- **Conferência do pedido inteiro:** depois da execução, a CLI confere cada linha no próprio RAG, pedaço por pedaço, com os mesmos pedaços da busca.
  - Um exame escrito que o modelo nem buscou aparece como `não buscado pelo agente: '<linha lida>' → <exame> <código> (confiança 0,xx); confira o pedido`.
  - Com algum `não incluído` ou `não buscado`, a última linha fica `Agendamento confirmado pela API: id …, status scheduled; ATENÇÃO: N possível(is) exame(s) do pedido sem decisão do agente, confira os avisos acima`.
  - O código de saída continua 0, porque o agendamento existe.
  - Essa conferência leva no máximo 30 s. Se o RAG travar, a CLI mostra `Aviso: o pedido não foi conferido por inteiro` e termina.
- **Última linha, `Tempo:`:** o tempo de cada etapa (buscas em paralelo contam uma vez), o total e o modelo usado. Aparece também quando nada é agendado, antes da linha `Erro:`, e não traz nenhum dado do pedido.
  - **Cada etapa inclui o turno do modelo:** conta de quando o Gemini começa a gerar a chamada da ferramenta (o horário que o ADK grava no evento) até a resposta dela. Não conta a espera pela resposta `[s/N]`.
  - **O total é o relógio do `run` inteiro:** soma os turnos do modelo entre as chamadas, as novas tentativas do Gemini (até 5, com espera crescente) e a espera pelo `[s/N]`. Por isso pode passar bem da soma das etapas.
- **Outro modelo numa execução, sem editar a spec:**
  `docker compose run --rm -e GEMINI_MODEL=<modelo> agent python -m cli run --image pedido.png` (ou `GEMINI_MODEL=` no `.env`, para todas).
- **Ver os logs:** por padrão a saída é só a de cima, e cada falha termina numa linha `Erro: ...`. Com `--verbose` (em `run` ou `transpile`), a CLI mostra também, no stderr, os logs das bibliotecas (ADK, MCP, as novas tentativas do cliente do Gemini) e, numa falha inesperada, o traceback, com a chave da API trocada por `[GOOGLE_API_KEY]`:
  `docker compose run --rm agent python -m cli run --image pedido.png --verbose`
- Antes da saída de cada `run`, o Compose mostra o status dos containers (`Waiting`, `Healthy`).

### Testar outra imagem

Coloque o arquivo em `samples/` e rode:

```bash
docker compose run --rm agent python -m cli run --image <arquivo>
```

- **Formato:** `.png`, `.jpg` ou `.jpeg`, com até 5 MB e 25 megapixels. Informe só o nome, sem pastas.
- **PDF não é aceito.** A CLI recusa outra extensão antes de chamar o Gemini:
  `Erro: --image: "pedido.pdf" não é uma imagem aceita; use .png, .jpg ou .jpeg`.
- **PDF renomeado** para `.png` passa pela extensão, mas o OCR o reconhece: `O arquivo é um PDF, não uma imagem: exporte a página como PNG ou JPEG.`
- **Foto de lado ou de cabeça para baixo** é endireitada e lida. Se nem assim der: `imagem de lado ou de cabeça para baixo: gire e envie de novo`. Um PNG com fundo transparente é lido sobre branco.
- **Arquivo inexistente ou recusado:** antes de chamar o Gemini, a CLI pergunta ao próprio OCR se o arquivo existe e é aceito (o container do agente não enxerga `samples/`). São as mesmas checagens da leitura, sem rodar o Tesseract, então um arquivo inexistente, um PDF renomeado ou uma foto ilegível param em segundos, sem nenhum turno do modelo: `Erro: OCR recusou a imagem: Arquivo "x.png" não encontrado em /data/samples.; nada foi agendado`. O nome do arquivo vai só ao OCR; o modelo continua recebendo um apelido (`pedido-1.png`). Ver "Imagem recusada pelo OCR" em [Quando algo falha](#quando-algo-falha).
- **Sem rebuild:** não precisa reconstruir, porque `samples/` é montada no container de OCR.
- **A suíte não muda:** os testes que percorrem as imagens de `samples/` e as specs de `specs/` usam a lista fixa dos arquivos do repositório ([`tests/versionados.py`](../tests/versionados.py)); um arquivo seu ali não muda a contagem de testes nem os faz falhar ([`test_versionados.py`](../tests/test_versionados.py)).

Exemplos prontos em `samples/`:

- **`pedido-realista.png`:** cabeçalho de clínica, CPF, telefone, CID, convênio, idade, data, CRM e exames numa fonte que imita letra de mão (não é manuscrito real). Dos 4 exames, 2 são agendados (Glicemia e TSH); Colesterol total e Hemoglobina glicada são lidos certos, mas saem em `baixa confiança` para você conferir, porque a confiança de leitura do OCR nessas duas linhas é 68 e 60, abaixo do piso de 75 (o RAG acha os dois). A CLI mostra esse valor como `(confiança 0,68)`, o mesmo número e a mesma palavra da pergunta `[s/N]`: o menor entre a busca, o apoio na linha e a leitura do OCR. É o comportamento esperado, e conservador: o que o sistema não leu com segurança não é agendado sozinho, é listado para conferência humana;
- **`pedido-sem-exame.png`:** dados do paciente e nenhum exame, o caso (b) do [teste de alucinação](evidencias/log-alucinacao.txt): nada pode ser agendado (`Erro: Nenhum exame encontrado no pedido; nada foi agendado`). Gerado por `exemplos/gerar_pedido_sem_exame.py`, com semente fixa;
- **`pedido-variacao.png`:** nome sem rótulo, marcadores, sinônimo `Glicose`, telefone sem rótulo, data e CRM;
- **`ataque-injecao.png` e `ataque-exame-disfarcado.png`:** instruções escondidas no pedido. O OCR tira as ordens do texto (o que sobra sai como `[TEXTO_REMOVIDO]`) e a CLI mostra `Instruções neutralizadas no OCR: N`; só os exames legítimos são agendados (ver [Segurança em detalhe](arquitetura.md#segurança-em-detalhe)).
- **`pedido-manuscrito.png` e `pedido-manuscrito-dificil.png`:** pedidos com aparência de letra de mão e de foto de celular, ambos simulados (ver abaixo). Na primeira, o OCR lê os 5 exames; na segunda, de "letra de médico", quase nada é lido: o que o agente achar fica para a sua confirmação (`[s/N]`) ou sai em "baixa confiança", e nada é agendado sozinho.

### Pedidos manuscritos simulados

`samples/manuscritos/` tem 120 pedidos fictícios com aparência de letra de mão: 70 de letra comum e 50 de "letra de médico" (muito inclinada, abreviações, carimbo com CRM e assinatura rabiscada). **São simulados com as fontes de letra de mão do Windows (Ink Free, Segoe Print, Segoe Script), não escrita real.** 93 das 120 (77%) imitam foto ruim de celular (baixa resolução, desfoque, JPEG pesado, sombra, perspectiva, papel amassado, ruído, borda cortada); o resto, scan limpo. O `gabarito.json` diz os exames, a PII e a degradação de cada imagem, e o CPF tem o dígito verificador errado de propósito. Para gerá-las de novo (no Windows, mesma semente, mesmas imagens):

```bash
python exemplos/gerar_manuscrito.py --saida samples/manuscritos --comum 70 --medico 50
```

Para passar as 120 pelo OCR e pelo RAG, via MCP e sem Gemini, com o compose da carga (o OCR continua lendo só um nome de arquivo; as imagens vão para um volume só da carga):

```bash
docker compose -f docker-compose.yml -f tests/load/compose.carga.yml -p carga run --rm tests python -m tests.load.manuscritos
```

A saída mostra, por estilo e qualidade da foto, os exames agendados sozinhos, os que seriam perguntados (`[s/N]`), os em baixa confiança, os não lidos, os que seriam agendados errados sem confirmação e a PII que sobrou. A decisão é a regra real do agente (leitura do OCR por linha, busca no RAG e as 3 faixas), sem ninguém para responder. Resultado atual: 113 de 497 exames agendados sem perguntar (23%), 37 perguntados, **0 errados sem confirmação** e 0 PII sobrando; a letra de médico continua quase ilegível (1 de 206 exames agendado sem perguntar). Uma troca de leitura entre exames do catálogo (`TGP` lido como `TAP`) não é agendada sozinha: vira pergunta. Tabela por grupo em [medicoes.md](medicoes.md#pedidos-manuscritos-simulados). O teste da CI (`tests/test_manuscritos.py`) roda 10 das 120 e falha se algum exame for agendado errado sem confirmação ou se sobrar PII.

### Robustez: entradas faltando ou quebradas

Para mandar 602 entradas quebradas (imagens em branco, giradas, truncadas, de extensão errada; consultas vazias, enormes ou de tipo errado; corpos inválidos e 50 POSTs simultâneos na API) pelo caminho real, com o mesmo compose da carga:

```bash
docker compose -f docker-compose.yml -f tests/load/compose.carga.yml -p carga run --rm tests python -m tests.load.robustez --variantes 12
```

A saída mostra, por categoria, os casos ok, os recusados com mensagem clara e os que falharam (erro 500, traceback, PII, exame agendado fora da imagem ou mensagem pouco clara). `--caso <id>` refaz só um caso. Resultado em [medicoes.md](medicoes.md#robustez-entradas-faltando-ou-quebradas).

## 4. Rodar com `adk run` ou `adk web`

O agente gerado também roda com as ferramentas do próprio ADK, sem a CLI do projeto. O `transpile` grava `generated/agent.py` e `generated/__init__.py`, então `generated/` é uma pasta de agente do ADK: o `agent.py` expõe `root_agent` e um `app` retomável (a pergunta de confiança média pausa e retoma a mesma chamada de agendamento).

```bash
docker compose run --rm agent python -m cli transpile specs/agent.json   # gera de novo depois de atualizar o projeto
docker compose run --rm agent adk run --in_memory generated              # no [user]:, digite só o nome do arquivo: pedido.png
docker compose run --rm -p 127.0.0.1:8000:8000 agent adk web --host 0.0.0.0 --port 8000 --no-reload --no_use_local_storage generated
```

No `adk web`, abra <http://127.0.0.1:8000>, escolha o agente `generated` e mande `pedido.png`. Se a porta 8000 do host estiver ocupada, troque só o primeiro número (ex.: `127.0.0.1:8090:8000`).

- **A mensagem:** o nome de um arquivo de `samples/` (`pedido.png`, ou uma frase com ele: `agende o pedido pedido.png`), só como texto. Um texto sem nome de imagem, com pasta (`samples/pedido.png`) ou com dois arquivos é recusado com `Informe só o nome de um arquivo de pedido em samples/ …`, e uma mensagem com outra coisa além de texto (a imagem anexada, código, um resultado de código) com `Envie só o nome do arquivo do pedido, como texto…`, sem chamar o modelo.
- **Antes do 1º turno do modelo** (`start_order`, em [`runtime/callbacks.py`](../runtime/callbacks.py)), como no `cli run`:
  - o nome vira o apelido `pedido-1.png`; o modelo recebe `Arquivo do pedido: pedido-1.png` no lugar do que você digitou e nunca vê o nome real, nem uma parte da mensagem que não seja texto (`before_model`);
  - os nomes `ocr`, `rag` e `api` precisam estar em `ALLOWED_HOSTS` e não podem resolver para um endereço local ou de metadados de nuvem. A mesma conferência roda antes da 1ª conexão de cada toolset, então nada conecta antes dela (nem o grafo da interface do `adk web`). Os endereços conferidos ficam fixos enquanto o processo do `adk` durar e são os que toda conexão usa ([`runtime/rede.py`](../runtime/rede.py)); um nome que não resolveu ou foi recusado é conferido de novo na vez seguinte. Se um serviço mudar de endereço (um `docker compose up` que recria o container), reinicie o `adk run` ou o `adk web`;
  - o OCR confere a imagem (`check_image`): um arquivo inexistente ou recusado para aí, com o motivo e o apelido no lugar do nome.
- **Pergunta de confiança média:** o console do `adk run` mostra `[HITL confirm] Confirme os exames lidos com confiança média: '<linha>' → <exame> <código> (confiança 0,80)…`. `yes` inclui os exames listados nela, e só eles; qualquer outra resposta deixa todos de fora. A CLI pergunta exame a exame; o ADK, uma vez por chamada. Se o modelo mandar duas chamadas de agendamento no mesmo turno e uma agendar antes da sua resposta à outra, o exame confirmado não vai num 2º `POST`: a mensagem final diz `não agendado (você confirmou, mas o agendamento desta execução já tinha sido criado)…`. Sem terminal (`docker compose run -T`) ou com `CI=1`, nada é perguntado e esses exames ficam de fora, como no `--yes`.
- **A última mensagem** (`[clinic_scheduler]: …`) é escrita em código, com o que as ferramentas devolveram: a PII mascarada, os exames deixados de fora e o motivo, a conferência do pedido inteiro e `Agendamento confirmado pela API: id …` só com a resposta da própria API. O texto do modelo (`[schedule]: …`) não conta.
- **Um pedido por sessão:** outra mensagem na mesma sessão é recusada (`Esta sessão já tratou um pedido…`). Se a API já agendou (também quando o modelo falhou depois do `POST`), a resposta traz o agendamento e `não repita este pedido`. Para outro pedido, `exit` e `adk run` de novo; no `adk web`, New Session. A `Idempotency-Key`, as respostas e o agendamento são da sessão.
- **O estado da sessão não é confiável:** o `adk web` deixa o cliente escrever o estado (ao criar a sessão e em cada mensagem), e o `adk run` aceita `--state`. Por isso o que a política usa (a imagem, as linhas lidas, os códigos de cada busca, as respostas, a `Idempotency-Key` e a resposta da API) fica num registro do próprio runtime, por sessão, que o estado só recebe como cópia. Uma resposta "sim" ou um agendamento escritos no estado não mudam nada. Num `adk web` longo, o runtime guarda os registros dos 256 pedidos terminados mais recentes e descarta um registro sem uso por 6 horas; uma sessão cujo registro saiu continua dizendo que já tratou um pedido (até 4.096 sessões), e a `Idempotency-Key` sai da própria sessão: um pedido reenviado nela recebe da API o mesmo agendamento, nunca um segundo.
- **Por que `--in_memory` e `--no_use_local_storage`:** sem eles, o ADK grava a sessão (o texto do OCR, já mascarado, e o nome real do arquivo no estado) em `generated/.adk/`, no volume. No `adk web` do ADK 2.10, `--no_use_local_storage` põe a sessão e os artefatos em memória, e o ADK recusa combiná-lo com `--session_service_uri` ou `--artifact_service_uri`.
- **Modelo reserva, como no `cli run`:** se o modelo principal responde `503` (sobrecarregado) ou `429` (sem cota), a mesma requisição vai na hora ao `fallback_model` da spec, e o console mostra `Aviso: modelo principal indisponível; usando gemini-3.5-flash-lite`. Não agenda em dobro: é a mesma chamada ao modelo de novo, nenhuma ferramenta rodou na que falhou. Se o reserva também falhar, a etapa termina com `Gemini indisponível no momento (HTTP 503); tente novamente`, as etapas seguintes não chamam o modelo, e a última mensagem diz `…; nada foi agendado`, sem traceback.
- **Só na CLI:** a checagem de que o `agent.py` é o que a spec gera hoje, a lista de ferramentas vivas antes do 1º turno e a linha `Tempo:`. O `adk run` roda o `agent.py` que estiver na pasta. As regras do runtime valem do mesmo jeito: ferramenta sem papel recusada, só códigos buscados e presentes no pedido, nada agendado de uma linha que diz para não realizar, que diz que já foi feito ou que é preparo (o `line_intent` do OCR fica no registro do pedido, como o resto), as 3 faixas, uma `Idempotency-Key` por sessão e `ALLOWED_HOSTS`.
- **Dependências do `agent.py`:** o Google ADK e a biblioteca `runtime/` do projeto (que usa o `catalogo.py`), versionada por `API_VERSION` (hoje 5). Ela não é um pacote instalado pelo pip: o `adk run generated` na raiz do projeto põe a raiz no `sys.path`, e no container `PYTHONPATH=/app` faz o mesmo.
- **O `adk web` é a interface de desenvolvimento do ADK, sem login:** quem a abre usa o agente sem nenhuma conferência de identidade. Publique a porta só em `127.0.0.1`, como acima.
- **Telemetria do ADK desligada:** o `adk run` e o `adk web` perguntariam `Enable telemetry? [Y/n]` (Enter diz sim) a cada vez, sem conseguir gravar a resposta no container somente leitura; a imagem do `agent` já traz a resposta "não" em `~/.adk/config.json`, então nada é perguntado nem enviado. Fora do Docker: `adk telemetry disable`.
- **Credenciais do Google:** o cliente MCP do ADK 2.10 procura, a cada conexão, credenciais do Google (e o servidor de metadados de nuvem, `169.254.169.254`) para tentar mTLS. O `docker-compose.yml` desliga isso nos serviços `agent`, `tests` e `tests-e2e` (`GOOGLE_API_USE_CLIENT_CERTIFICATE=false`); fora do Docker, defina a variável do mesmo jeito.
- **Testes:** [`tests/test_adk_run.py`](../tests/test_adk_run.py) roda o próprio comando `adk run` (e o servidor do `adk web`) sobre a pasta gerada, com modelos roteirizados no lugar do Gemini e os servidores MCP e a API reais: só com `pedido-joao-silva.png` digitado, agenda os 3 exames, e nenhuma requisição ao modelo contém o nome do arquivo; um caso roda a regra real de endereços, com os nomes em endereços privados. [`tests/test_adk_seguranca.py`](../tests/test_adk_seguranca.py) faz o papel de um cliente que tenta mais: estado forjado, partes que não são texto, duas perguntas no mesmo turno, conexão antes da conferência, DNS que muda e falha depois do `POST`. [`tests/test_adk_comandos.py`](../tests/test_adk_comandos.py) roda as linhas `adk run` e `adk web` deste guia, como estão escritas, num processo `adk` de verdade: as duas sobem sem gravar nada em `generated/.adk/`, e o `adk web` lista o agente e cria uma sessão.

## Variáveis de ambiente

Todas as que o código lê. As do `.env` chegam só ao serviço que as usa; as outras já têm o valor certo dentro dos containers e só mudam fora do Docker (por exemplo, no `pytest` local).

| Variável | Padrão | Quem lê | O que faz |
|---|---|---|---|
| `GOOGLE_API_KEY` | vazia | `agent` (`cli run`) | Chave da Gemini API. Sem ela, o `run` para antes de chamar o modelo. |
| `GEMINI_MODEL` | vazia: o modelo da spec | `agent` e `tests-e2e` | Outro modelo Gemini numa execução, sem editar a spec (a mesma regra de nome da spec vale para ela). |
| `ALLOWED_HOSTS` | vazia: `ocr:8001,rag:8002,api:8000` | `agent` (`transpile`, `run`, `adk run` e `adk web`) | Hosts que os servidores de uma spec podem usar: `host` para qualquer porta, `host:porta` para uma. |
| `DB_ENCRYPTION_KEY` | vazia: a `api` cria uma no volume `api-key` | `api` | Chave AES-256 que cifra as listas de exames no banco (`python -m api.crypto --gerar-chave` cria uma). |
| `API_RATE_LIMIT_PER_MINUTE` | `1200` | `api` | Requisições por minuto por IP; acima disso, `429` com `Retry-After`. `0` desliga. |
| `API_ALLOWED_HOSTS` | vazia: `127.0.0.1,localhost,api` | `api` | Nomes que a API aceita no cabeçalho `Host`, em qualquer porta; outro nome recebe `400` (contra DNS rebinding). Para chamar a API por outro nome (um proxy, outro host), inclua-o aqui. |
| `API_IDEMPOTENCY_TTL_HOURS` | `24` | `api` | Horas que uma `Idempotency-Key` vale (guardada só como HMAC); depois, a mesma chave agenda de novo. |
| `API_PORT` | `8765` | Compose | Porta da API no host, só em `127.0.0.1`. |
| `DB_PATH` | `/state/appointments.db` | `api` | Arquivo SQLite (no volume `api-data`). |
| `DB_KEY_FILE` | `/keys/db.key` | `api` | Onde fica a chave criada na 1ª subida (no volume `api-key`). |
| `EXAMS_PATH` | `data/exams.json` | `api`, `rag`, `ocr` (`catalogo.py`) | Catálogo de exames. |
| `SAMPLES_DIR` | `/data/samples` | `ocr` | Pasta das imagens que o OCR aceita (`samples/`, só leitura; a carga monta outra). |
| `OMP_THREAD_LIMIT` | `1`, definida pelo `ocr` | Tesseract | Uma thread por leitura: com 8 leituras em paralelo, o Tesseract não disputa os núcleos. |
| `CI` | vazia | agente gerado | Não vazia (como no GitHub Actions): nenhuma pergunta `[s/N]`, e o exame que precisava de um "sim" fica de fora, como no `run --yes` (que diz isso ao agente pelo registro do pedido, não por variável). |

## Backup e restauração

O banco (SQLite) fica no volume `api-data`, e a chave que cifra as listas de exames, no volume `api-key` (ou em `DB_ENCRYPTION_KEY`, no `.env`). A cópia do banco leva os exames cifrados e nunca a chave. Guarde as duas **separadas**, a chave num cofre de senhas, por exemplo: quem tem as duas lê os dados, e é para isso que a chave fica num volume à parte. A restauração precisa das duas.

1. **A chave, uma vez** (ela não muda). Se você definiu `DB_ENCRYPTION_KEY` no `.env`, a chave é essa; senão, copie a do volume, guarde o conteúdo longe da cópia do banco e apague o arquivo:
   ```bash
   docker compose cp api:/keys/db.key ./db.key
   ```
2. **A cópia do banco, com a API no ar** (crie a pasta `backup` uma vez, com `mkdir backup`):
   ```bash
   docker compose exec api python -m api.backup --saida backup.db
   docker compose cp api:/state/backup.db ./backup/appointments.db
   docker compose exec api sh -c "rm /state/backup.db"
   ```
   - **Só o nome do arquivo:** `--saida` e `--entrada` recebem um nome, que fica na pasta do banco (`/state`). Os comandos funcionam iguais no PowerShell e no Git Bash: nenhum argumento começa com `/`, que o Git Bash reescreveria como `C:/Program Files/Git/…` (o `rm` vai entre aspas pelo mesmo motivo). Com uma pasta que não existe, o erro diz qual: `Erro: a pasta … não existe; use só um nome de arquivo (ex.: backup.db); nada foi copiado`.
   - **Consistente com a API gravando:** a cópia usa a API de backup do SQLite, que inclui os agendamentos recentes ainda no WAL (copiar só o arquivo `.db` perderia esses). Sai um arquivo só, e `--saida` nunca sobrescreve um arquivo.
   - **Por que `/state`:** os containers são somente leitura, e o volume do banco é o lugar gravável da API (o `/tmp` é um tmpfs, que o `docker compose cp` não enxerga). A cópia fica nele só até o `cp`.
   - **Saída:** `cópia gravada em /state/backup.db: 3 agendamento(s), com os exames cifrados; a chave do banco não vai na cópia (guarde-a à parte)`. O `.gitignore` já ignora `*.db` e `*.key`.
3. **A restauração, com a API parada:**
   ```bash
   docker compose cp ./backup/appointments.db api:/state/restore.db
   docker compose stop api
   docker compose run --rm --no-deps api python -m api.backup --entrada restore.db
   docker compose up -d --wait
   docker compose exec api sh -c "rm /state/restore.db"
   ```
   - **A chave primeiro:** com o volume `api-key` intacto, nada a fazer. Numa máquina nova, ponha a chave guardada em `DB_ENCRYPTION_KEY=` no `.env` antes (ela tem precedência sobre o volume). Sem chave, a restauração para e não cria outra: `Erro: chave do banco não encontrada (…): restaure primeiro a chave da gravação; nada foi restaurado`.
   - **Conferida antes de gravar:** a cópia passa pelo `integrity_check` do SQLite e cada agendamento é decifrado com a chave atual. Com a chave errada, nada muda: `Erro: não foi possível decifrar o registro: a chave não é a da gravação ou o dado foi alterado no banco; nada foi restaurado`.
   - **Banco com dados:** um banco que já tem agendamentos só é trocado com `--substituir` no fim do comando; o que entrou depois da cópia se perde.
   - **Saída:** `banco restaurado em /state/appointments.db: 3 agendamento(s), todos decifrados com a chave atual`. Testes em [`test_backup.py`](../tests/test_backup.py).

## Quando algo falha

Erros saem como uma linha `Erro: ...`, com código 2. Por exemplo: serviço fora do ar ou chave ausente. Para ver o que aconteceu por trás dela, rode de novo com `--verbose`.

- **Imagem recusada pelo OCR:** o OCR recusa com uma mensagem clara. Por exemplo: `Arquivo "x.png" não encontrado em /data/samples.`, `Imagem corrompida ou incompleta.`, `O conteúdo do arquivo não corresponde à extensão (use PNG ou JPEG).` (um GIF renomeado para `.png`) ou `Arquivo grande demais (máximo 5 MB).`. A CLI repete o motivo: `Erro: OCR recusou a imagem: <motivo>; nada foi agendado`. Ela pergunta ao OCR antes de chamar o Gemini, então essa linha sai em segundos; só uma página que o Tesseract não consegue endireitar é recusada durante a execução, com a mesma linha.
- **Fora da pasta do repositório:** `no configuration file provided: not found`. Entre em `rag-local-app`, onde está o `docker-compose.yml`.
- **Docker parado:** `Cannot connect to the Docker daemon … Is the docker daemon running?`. Abra o Docker Desktop e espere o "Engine running".
- **`Read timed out` do pip no 1º build:** é a rede até o PyPI, não o projeto. Rode o `docker compose up -d --wait` de novo: os estágios prontos ficam no cache e o build continua de onde parou.
- **Docker sem sub-redes livres:** o `up` falha com `all predefined address pools have been fully subnetted`. Causa: redes de outros projetos Docker ocupam todas as faixas.
  - **Solução recomendada:** fixe as sub-redes deste projeto num `docker-compose.override.yml` na raiz, que o Compose lê sozinho e o `.gitignore` já ignora. Use duas faixas livres na sua máquina:
    ```yaml
    networks:
      default:
        ipam: {config: [{subnet: 10.201.10.0/24}]}
      internal:
        ipam: {config: [{subnet: 10.201.11.0/24}]}
    ```
  - **Para ver as faixas em uso** (PowerShell ou Git Bash):
    ```bash
    docker network inspect $(docker network ls -q) --format "{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}"
    ```
  - **Comandos com `-f`** (a carga e a robustez, em [medicoes.md](medicoes.md)) não leem o override sozinhos: acrescente `-f docker-compose.override.yml`.
  - **Alternativa, com cuidado:** `docker network prune` apaga todas as redes sem container em uso, inclusive as de **outros projetos** que estejam parados, que depois precisam ser recriadas. Confira antes com `docker network ls`.
- **Porta ocupada no Windows:** o `up` pode ficar `Healthy` sem erro enquanto outro programa responde na porta. Se o Swagger não se chamar "API de agendamento de exames (fictícia)", troque o `API_PORT`.
- **Falhas temporárias do Gemini** (`500`) são tentadas sozinhas até 5 vezes no total, com espera crescente; `429` e `503` também, quando a spec não tem `fallback_model` ou já no modelo reserva.
- **Modelo principal indisponível** (`429`/`503`): a mesma requisição vai na hora ao `fallback_model` da spec (`gemini-3.5-flash-lite`), sem novas tentativas do principal, com o aviso `Aviso: modelo principal indisponível; usando gemini-3.5-flash-lite`. É um caminho normal, não um erro.
- **Se o reserva também falhar,** a saída é `Erro: Gemini indisponível no momento (HTTP 503); tente novamente`.
- **Pedido sem exame:** `Erro: Nenhum exame encontrado no pedido; nada foi agendado`.
- **Bloqueio antes da API:** `Erro: agendamento bloqueado antes de chamar a API: …; nada foi agendado`. Acontece quando um código não veio de uma busca no catálogo, ou quando nenhum exame atinge a confiança de 0,90 nem foi confirmado por você.
- **OCR sem texto e sem motivo** (serviço fora do ar no meio da execução): `Erro: o OCR não devolveu o texto do pedido (serviço indisponível?); nada foi agendado`.
- **Modelo descontinuado:** a saída é `Erro: o Gemini recusou a chamada (HTTP 404: ...)`. Troque-o com `-e GEMINI_MODEL=<modelo>`.

A tabela completa está em [arquitetura.md](arquitetura.md#tratamento-de-erros).

## Testes

```bash
docker compose run --rm tests pytest -q -n auto
```

- **Serviço `tests`:** usa o estágio `test` do `Dockerfile`, que é a imagem do `agent` mais pytest, ruff, mypy, o Tesseract e os testes. O `agent` leva só o que `transpile` e `run` usam. O 1º comando constrói a imagem de testes (sem cache, alguns minutos) e sobe os serviços.
- **Sem chave, sempre:** o serviço `tests` não recebe a `GOOGLE_API_KEY`, nem com ela no `.env`. Nada chama o Gemini e o teste ponta a ponta é pulado.
- **Ponta a ponta real, só quando pedido:** `docker compose run --rm tests-e2e` roda [`tests/test_e2e.py`](../tests/test_e2e.py) com a chave do `.env`: uma execução real com o Gemini (o `run` inteiro, com vários turnos do modelo) sobre `pedido.png`, que precisa agendar exatamente os 3 exames do pedido (`FICT-001`, `FICT-002` e `FICT-005`), os mesmos que o `GET` do agendamento devolve. Sem a chave no `.env`, ele falha (não é pulado), para não parecer que passou.
- **O que a suíte cobre:**
  - specs válidas e inválidas e o código gerado (compilável e importável);
  - a saída da CLI;
  - o OCR nas imagens de exemplo, com a PII mascarada;
  - a busca do RAG;
  - as duas ferramentas MCP chamadas via SSE, como o agente faz;
  - a API (criação, consulta, `404`, `422`), os cabeçalhos de segurança em cada resposta ([`test_api_headers.py`](../tests/test_api_headers.py)) e o backup e a restauração do banco ([`test_backup.py`](../tests/test_backup.py));
  - cada tipo de PII;
  - o detector de injeção ([`tests/test_injection.py`](../tests/test_injection.py)), com o corpus do próprio projeto em `tests/attacks/` (790 ataques e 1.482 linhas legítimas, 1.429 distintas);
  - a cifra do banco e a chave no volume ([`test_crypto.py`](../tests/test_crypto.py)) e a `Idempotency-Key` ([`test_idempotencia.py`](../tests/test_idempotencia.py));
  - as 3 faixas de confiança, a pergunta `[s/N]` e o piso do OCR ([`test_confianca.py`](../tests/test_confianca.py));
  - o preparo da imagem e a confiança por linha do OCR ([`test_preprocessamento.py`](../tests/test_preprocessamento.py));
  - o agente gerado conversando com os servidores MCP reais, sem Gemini ([`test_agent_mcp.py`](../tests/test_agent_mcp.py));
  - o agente gerado rodando com `adk run` e com o servidor do `adk web`, sem a CLI, até o agendamento na API real ([`test_adk_run.py`](../tests/test_adk_run.py));
  - o limiar de 0,90 sobre as 631 consultas de calibração ([`test_calibration.py`](../tests/test_calibration.py));
  - uma fração da carga (20 pedidos), dos manuscritos (10 de 120) e da robustez (1 caso por categoria): [`test_carga.py`](../tests/test_carga.py), [`test_manuscritos.py`](../tests/test_manuscritos.py), [`test_robustez.py`](../tests/test_robustez.py).
- **Resultado atual:** 548 funções de teste e 19.030 casos (`pytest --collect-only`), quase todos de corpus parametrizado (linhas legítimas, PII gerada, ataques e termos do catálogo); 19.029 passam e 1 é pulado (o ponta a ponta, sem chave). Com `-n auto` (um processo por núcleo), a suíte leva cerca de 3,5 min numa máquina de 12 núcleos, com `--cov` (medido: 200 s e 205 s); em série, de 6 a 11 min.
- **Qualidade na CI:** antes dos testes, a CI roda `ruff` (pyflakes, pycodestyle, ordem dos imports, bugbear) e `mypy` nos módulos do projeto, e os testes rodam com cobertura (relatório, sem limite que quebre o build): 98% das linhas de `api`, `catalogo`, `cli`, `guardrails`, `mcp_servers`, `runtime` e `transpiler`. O `agent.py` gerado não entra na conta. O `mypy` é uma checagem leve: sem `--strict`, com `ignore_missing_imports` (bibliotecas sem tipos não são conferidas) e com o padrão do mypy de não olhar por dentro as funções sem anotação, que são muitas. Config e exceções em [`pyproject.toml`](../pyproject.toml). Antes do build, o `pip-audit` confere `requirements.txt` e `requirements-dev.txt`, com tudo o que eles puxam: falha numa vulnerabilidade conhecida que já tem versão corrigida e só avisa quando ainda não há correção; o Dependabot abre toda semana os PRs de atualização (pip e GitHub Actions). Para rodar local:

  ```bash
  docker compose run --rm --no-deps tests ruff check .
  docker compose run --rm --no-deps tests mypy
  docker compose run --rm tests pytest -q -n auto --cov --cov-report=term
  ```
- **Avisos:** a seção `[tool.pytest.ini_options]` do `pyproject.toml` filtra o aviso de depreciação do `SequentialAgent` (ver [Decisões técnicas](arquitetura.md#decisões-técnicas-em-detalhe)) e os avisos `[EXPERIMENTAL]` do ADK sobre os recursos experimentais em uso (autenticação plugável, confirmação de ferramenta com pausa e retomada, estado do agente e o esquema JSON das funções), cada um pelo nome: com `-n auto`, cada processo os repetiria. Um recurso experimental novo, ou qualquer outro aviso, continua aparecendo.
