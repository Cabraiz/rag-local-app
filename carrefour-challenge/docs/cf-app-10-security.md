# CF-APP-10: contrato de seguranca

**Perfil opt-in. O caminho padrao ainda nao esta protegido por estes controles.**
O overlay nao e ativado automaticamente pelo Compose principal nem pela CLI
original. Aprovar este segmento nao significa que o app global esta seguro.

## Reconciliacao CF07 antes das novas rodadas

Snapshot `81fb3f0` revisado pela Central e preservado, com seus receipts e bundle
local. Nova base verificada: `origin/main` em
`9b80d2133a9a855cf7cb11616ca36d753c0725aa` (CF07 helpers, PR #3).
Rebase somente da candidata local, mesma branch/worktree, sem conflitos.

Esperado: conservar os controles opt-in e os 70 oraculos originais, aceitar as
regras NFKC/cabecalho completo do owner e provar regressao de seus helpers.
Proibido: enfraquecer oraculos, tocar fontes/worktrees dos owners, instalar
implicitamente helpers nos consumidores ou alegar aceite global/CF07 completo.
Menor prova: duas rodadas novas com fontes/config/seed congeladas, os 80 testes
CF10 e regressao da base mais `test_pii_guardrails`, `test_cf07_privacy_adapters`
e `test_cf07_mcp_egress`. Sem skip, sem reutilizar prova da base anterior.
Os oraculos consumidores pendentes do CF07 e seu schema externo CF06 nao sao
este segmento; constam como limites e nao sao declarados verdes. PR/merge
permanecem sob revisao e execucao da Central.

## Contrato da retomada antes das alteracoes

Base requerida e verificada: `origin/main` em
`4de574ff62a62381eeda180c7c9567db11eec1e1`. Worktree proprio de integracao:
`D:/RAG-Worktrees/rag-exec-10-integration`, branch
`codex/carrefour-cf-app-10-integration`. O checkout original e os worktrees
OCR/RAG sao preservados. O patch antigo e a prova de 3ed9592 sao historicos,
nao aprovacao vigente sobre esta base.

Esperado: manter os 70 oraculos adversariais, validar o novo campo RAG
`abstentions`, manter o schema publico dos owners e o comportamento do guard
padrao, com rigor adicional ativado somente pelo adapter opt-in. Proibido:
ativar o overlay implicitamente, reintroduzir arquivos removidos pelos owners,
editar seus worktrees, alegar seguranca global ou aproveitar receipts antigos.
Menor prova adicional: processos novos distinguem caminho padrao e perfil;
SSE valido de OCR/RAG e catalogo from_bytes permanecem compativeis, respostas
contraditorias falham, os ataques mantem zero efeitos/reservas. Regressao usa
os testes presentes nesta base, incluindo os contratos CF02/CF05, sem SKIP.

Qualquer alteracao relevante reinicia o par congelado. Commit local candidato
esta autorizado nesta retomada; PR e merge aguardam nova revisao da Central.

Contrato registrado antes da implementacao, em 2026-10-03. Base:
`3ed9592309fe91b70221432944dc5111636a548f`, branch `codex/carrefour-exec-10`.
Somente `carrefour-challenge/`; nenhuma mudanca no laboratorio RAG ou fila.

## Esperado e proibido

Usuario/spec, imagem/OCR, catalogo e mensagens MCP/SSE sao dados nao confiaveis.
Somente a DSL fixa pode criar o workflow e somente tools declaradas e argumentos
tipados podem atravessar o servidor. Nenhum texto pode conceder ferramentas,
trocar identidade, revelar prompt/segredos, ler caminhos arbitrarios, controlar
endpoints ou causar reservas. Uma entrada malformada deve produzir erro fixo,
sem reflexao do payload; a proxima chamada valida deve continuar funcionando.

A protecao estrutural e primaria: o workflow nao usa LLM, nao interpreta comandos
dos textos e nao executa codigo submetido. Filtros de texto sao defesa adicional,
nao uma promessa de reconhecer toda instrucao em linguagem natural.

## Matriz e menor prova

| Caso | Entrada | Oraculo definido antes do candidato |
| --- | --- | --- |
| Direto | spec com prompt, identidade, tool, URL, import/codigo | rejeicao antes de tool, filesystem ou egress |
| OCR indireto | exame valido mais instrucao de segredo/tool/identidade | rejeicao; nenhum RAG/agendamento; payload ausente na saida |
| Catalogo | instrucao/URL/segredo de fixture no evidence/notice | catalogo recusado antes de uso; nenhuma resposta ecoa o texto |
| Tool forgery | tool desconhecida, argumentos extras, metadata/autoridade forjada | servidor rejeita antes do handler; contadores zero |
| Path | absoluto, traversal, URL, link simbolico | nenhum acesso ao alvo fora de samples; nenhum OCR/subprocesso |
| SSE | chaves duplicadas, metodo/ID/params/meta invalidos ou autoridade extra | HTTP/MCP seguro no transporte real de ambos servidores; recuperacao |
| Exfiltracao | resposta OCR/RAG com campos extras, texto/payload divergente | runtime rejeita antes da API; nao ecoa canario |
| Controle | tools declaradas com fixture valida | resposta fundada; evidencia de que os spies podem detectar chamadas |

Harness: spies de acesso a arquivo, subprocesso, tool e saida de rede com canarios
inteiramente ficticios; requests MCP via SSE real; API FastAPI com SQLite privado
e consulta por request_id, mais contagem final igual a zero nos casos de ataque.
Rede de teste isolada e endpoints loopback conhecidos nao sao exfiltracao.
Logs completos ficam em `.local/cf-app-10/`, fora do indice Git.

Duas rodadas consecutivas devem usar a mesma imagem, fontes, configuracao, seed e
harness. Qualquer mudanca relevante reinicia as duas. Revisao independente e merge
ficam com a Central; nenhum receipt isolado significa integracao concluida.

## Limites conhecidos antes da mudanca

Esta base possui recibos REQUESTED e nao implementa slots/consentimento interativo;
esse contrato pertence a CF-APP-03/06. Esta lane prova zero recibos para ataques,
sem substituir esses cards. Testes de fronteira com adapters sao identificados como
tal; testes SSE usam servidores reais. Nao prova sandbox de Python arbitrario,
producao, modelo online, nem ausencia total de bugs.

## Decisao de integracao e ownership

O contrato compartilhado v1 foi recebido durante a implementacao. Os hooks
rascunhados em runtime.py, catalog.py e rag_server.py foram retirados integralmente.
Os controles usam o helper dedicado security.py, o guard de seguranca mcp_guard.py
e adapters explicitos security_profile.py/secure_cli.py. Os arquivos dos owners
CF03/04/05/06/08 e o Compose principal permanecem intactos.

Para ativar as fronteiras completas, usar o overlay aditivo:

```powershell
docker compose -f docker-compose.yml -f security.compose.yml up -d --wait api ocr rag
docker compose -f docker-compose.yml -f security.compose.yml run --rm runner python -m clinic_adk.secure_cli transpile
docker compose -f docker-compose.yml -f security.compose.yml run --rm runner python -m clinic_adk.secure_cli run --image request.png
```

O overlay e os adapters sao parte obrigatoria desta entrega de seguranca.
A execucao direta pelos entrypoints originais conserva as lacunas de schema
de resposta e de envelope RAG; a Central deve adotar este perfil no pacote
integrado. O gate nao afirma que os entrypoints originais foram corrigidos.

## Repetir o gate isolado

```powershell
.\tools\verify_cf_app_10.ps1 -Evidence D:\RAG-Worktrees\rag-exec-10-integration\.local\cf-app-10\nova-prova
```

O script exige imagem runtime ja existente, congela as fontes em uma pasta nova,
fixa o ID da imagem e verifica todas as versoes de requirements.lock. Executa
duas rodadas, com seed 1010, rede Docker `none`, filesystem somente leitura,
tmpfs privados para /tmp e /artifacts, limite de CPU/memoria/processos e sem volumes persistentes.
MCPs e API rodam via HTTP/SSE real no loopback do container de teste.

A suite especifica conserva os 70 casos de ataque da primeira candidata e
acrescenta compatibilidade com CF02/CF05, ativacao em processo novo e rejeicao
de representacoes contraditorias. Inclui startup/recuperacao e consulta HTTP mais
contagem SQL do ledger real. Fixtures maliciosas SSE sao servidores reais e
intencionalmente hostis; dois casos de resposta estruturada usam adapters,
identificados no harness. A suite de regressao roda em outro processo para
nao herdar os bindings do perfil. Endpoints api/ocr/rag dos testes antigos sao
processos reais privados do mesmo container, com hostnames fixos no loopback.
Nenhum caso de hostname e excluido ou convertido em mock. O namespace desse
ledger de controles positivos e separado do ledger zero-reservas dos ataques.
Nenhum SKIP conta como aprovacao.

Os spies distinguem startup de ataque. Startup carrega dependencias e faz
handshakes conhecidos. Durante os ataques, arquivos autorizados sao as fontes
/app, imagens de fixture e o diretorio SQLite privado. `/dev/null` e somente
o sink fixo de stderr do Tesseract; nao e entrada arbitraria de um usuario.
Conexoes autorizadas sao apenas loopback. Tentativas capturadas por excecoes
do servidor continuam reprovando a contagem final dos spies.

O build convencional sem rede pode depender de cache de pacotes de sistema;
a alternativa declarada usa o runtime local com dependencias conferidas e
fontes congeladas montadas somente leitura. Essa alternativa nao comprova
rebuild de dependencias em um host limpo, que pertence a CF-APP-01/12.

O spy encontrou uma tentativa real de ADC do SDK contra `169.254.169.254:80`,
inclusive no controle positivo sem chamada de modelo. O perfil offline passa a
recusar `google.auth.default` com a excecao documentada de credenciais ausentes,
antes de ler credenciais ou consultar metadados. Isso e uma politica do adapter
de producao local, nao um mock do harness nem uma credencial ficticia. Os spies
permanecem ativos e o endereco continua proibido.
