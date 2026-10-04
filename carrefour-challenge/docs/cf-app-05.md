# CF-APP-05: catálogo e busca fundada

O armazenamento deste card é `data/exams.json`: snapshot sintético versionado,
schema_version inteiro 1, fictional true e notice de demonstração. Há 120 registros
distintos, códigos FICT-001 a FICT-120, sem significado clínico oficial.

Cada registro contém exatamente code, name, aliases e evidence. aliases são nomes
explicitamente cadastrados, não equivalências clínicas inferidas. evidence é a
ficha sintética do próprio código e nome, no formato `Ficha ficticia NNN: NAME;
identificador de demonstracao, sem significado clinico oficial.`. Não contém
preparo, indicação, diagnóstico, prazo, preço nem requisito clínico.

O carregador rejeita schema inválido, campos ausentes/extras, JSON com chaves
duplicadas, nomes/códigos repetidos, aliases conflitantes, menos de 100 ou mais
de 1000 registros e ficha que referencie outro código/nome. Arquivos não regulares,
links e tamanho acima de 1 MB também falham fechados. Catálogo inválido impede
o startup do RAG e portanto sua readiness.

Importação Docker em destino temporário próprio, sem usar volume compartilhado:

```powershell
docker compose run --rm --no-deps runner python -m clinic_adk.catalog_seed --destination /tmp/catalog.json
```

Para comprovar reseed no mesmo container, use pytest abaixo. A importação valida
os bytes antes da escrita, publica snapshot completo sem overwrite e devolve
created ou unchanged, count e SHA-256. Repetir os mesmos bytes mantém conteúdo,
SHA e mtime. Um destino existente diferente/inconsistente é rejeitado. A pasta
destino precisa existir e suportar hard links. O import não migra nem substitui
catálogos em uso; o RAG usa o snapshot incluído no build, somente leitura.

`lookup_exams(exam_names: list[str])` é descoberto e chamado por MCP HTTP+SSE
legado, `/sse` e `/messages/`, na rede interna do Compose. Aceita 1 a 20 nomes.
Uma busca exata após normalização Unicode/case/acentos/espaços resolve apenas
name ou alias explícito. A saída possui ok, exams (name/code/evidence),
unresolved_indices, abstentions (index/reason), catalog_version (SHA dos bytes)
e catalog_count. Não reflete texto desconhecido. Prefixo que coincida com vários
IDs resulta ambiguous; prefixo com um ID resulta low_confidence; sem coincidência
resulta not_found. Prefixos só classificam a incerteza, nunca resolvem um exame.
Se qualquer termo não resolver, ok false e exams vazio impedem resolução parcial.

Exemplos contratuais: Hemograma completo/Hemograma -> FICT-001;
Glicose em jejum -> FICT-002; TSH -> FICT-024. Colesterol abstém por ambiguidade;
Hemograma comple abstém por baixa confiança; Exame inexistente abstém por ausência.
O cliente ADK recusa ok false e o Runtime compara versão, nomes, códigos e fichas
com seu próprio snapshot antes de permitir a próxima etapa.

O contrato CF03 também protege a fronteira anterior ao SDK: `mcp_call('rag',
arguments)` só constrói o cliente após conferir uma lista de nomes/aliases
presentes no catálogo confiável e projetá-los para nomes canônicos. Ambiguidade,
baixa confiança, ausência, instrução adversarial, lista mista com item sem
referência ou referência forjada em campo extra falham localmente com
`EXAM_EVIDENCE_MISMATCH`, com zero construção, descoberta e execução MCP.
O oráculo `test_adk_client_missing_trusted_reference_rejects_before_sdk` verifica
esse código exato e spies de `McpToolset`, `get_tools`, `run_async` e `close`
sem nenhuma chamada/await. Também exige zero `send` HTTP em `httpx2.AsyncClient`
e `httpx2.Client`. Os 17 casos incluem os oito negativos anteriores e nove
exemplos sintéticos dos testes CF03: nome canário, e-mail/CPF fictícios, Unicode
fullwidth/zero-width, 21 nomes conhecidos, lista com tipo inválido e campo
`patient` extra. Todos passam por `mcp_call`; helpers de outra fronteira não
substituem essa prova. Isso é uma prova da barreira local, separada do transporte.

A seleção qualificada une integralmente as seleções anteriores CF05/main e
CF06, acrescentando o arquivo CF03 `test_journey_privacy.py` completo. Os 12
arquivos são executados sem filtros; o manifesto de coleta e o JUnit devem
conter todos os casos anteriores e os 17 casos com spies, sem skips, erros ou
deselects. Perfis externos de OCR, produtor/CLI live, descoberta, empacotamento
e certificação independente de privacidade continuam sendo gates dos seus
owners; não se declara aprovação desses perfis por este segmento CF05.

O teste SSE real continua enviando os três termos incertos diretamente à tool
do servidor e exige `ok=false`, `exams=[]` e o motivo de abstenção correto. Só a
decodificação dessas respostas remotas produz `MCP_TOOL_FAILED`. O teste positivo
ADK real mantém IDs/fichas persistidos e rejeição de `FICT-999` no gate do agente.
Não se aceita um conjunto de códigos alternativos para esconder a fronteira da
falha, nem se autoriza construir o SDK para cumprir um oráculo antigo.

O SDK instalado pode transportar esse dicionário em JSON TextContent sem
structuredContent. A integração aceita o JSON estruturado validado dessa forma;
se ambas as representações vierem, o cliente exige igualdade. Não interpreta
texto livre como exame nem transforma uma resposta ok false em resolução.

Prova reproduzível em projeto Compose isolado, com imagem deste checkout:

```powershell
docker compose build rag
docker compose up -d --wait rag
docker compose run --rm --no-deps tests python -m pytest -q -p no:cacheprovider tests/test_catalog_rag_card.py
```

Não execute esses comandos sobre o projeto compartilhado de demonstração. Use
`-p` e `--env-file` próprios; override do serviço tests pode remover seu volume
de artifacts, pois esta bateria só grava em /tmp. Repita duas vezes com a mesma
imagem/configuração/fontes/seeds/harness. A bateria conta/resemeia, testa conflito
e concorrência de importação, catálogo vazio/inconsistente/referência inválida,
percorre os 120 exames/aliases pelo SSE real e passa a saída real pelo cliente ADK
e gate de evidências do agente, sem criar reservas.

Limites: recuperação lexical exata, sem embeddings ou inferência clínica. Esta
bateria não comprova OCR, reserva, UI, todos os ataques ou prontidão integral.
Revisão independente e integração são gates da Central separados da regressão.

## Gate leve por Compose, com SSE real e sem serviços compartilhados

`compose.rag-proof.yaml` usa a mesma imagem construída pelo Dockerfile do desafio,
mas só inicia um container efêmero, sem rede externa, porta publicada ou volume
persistente. Dentro dele `tools/verify_catalog.py` inicia o aplicativo RAG real,
aguarda sua saúde, executa os clientes MCP e ADK reais por HTTP+SSE, e encerra
somente seu subprocesso. `extra_hosts` liga rag a 127.0.0.1 no próprio namespace
do container, e o harness verifica esse endereço antes de iniciar. Esse gate não
depende de API/OCR e não demonstra a topologia integrada entre três serviços.

Na pasta do desafio, com duas pastas absolutas de evidência novas:

```powershell
New-Item -ItemType Directory -Force evidence/cf05-round-1,evidence/cf05-round-2 | Out-Null
"CF05_EVIDENCE=$((Resolve-Path evidence/cf05-round-1).Path)" | Set-Content evidence/cf05-round-1/config.env
"CF05_EVIDENCE=$((Resolve-Path evidence/cf05-round-2).Path)" | Set-Content evidence/cf05-round-2/config.env
docker compose -p cf05-proof --env-file evidence/cf05-round-1/config.env -f compose.rag-proof.yaml build proof
docker compose -p cf05-proof --env-file evidence/cf05-round-1/config.env -f compose.rag-proof.yaml run --rm --no-deps proof
docker compose -p cf05-proof --env-file evidence/cf05-round-2/config.env -f compose.rag-proof.yaml run --rm --no-deps proof
```

No ambiente compartilhado da Central, builds/gates pesados ainda exigem a lease
do projeto. Para regressão leve enquanto a lease está ocupada, pode-se reutilizar
uma imagem de dependências pelo ID imutável e montar src/tests/tools/data deste
checkout somente leitura, registrando hashes de todos esses arquivos. Isso
comprova o código montado e o SSE real, mas não um novo build Dockerfile completo.
O receipt deve distinguir essas provas, nunca marcar build bloqueado como aprovado.

O parâmetro --source do importador seleciona um snapshot sintético a validar;
imutabilidade significa não substituir um destino publicado. A opção não é uma
operação MCP, não atualiza o RAG em execução e não certifica equivalência clínica
de aliases de uma origem arbitrária. O serviço usa a fixture versionada do build.
