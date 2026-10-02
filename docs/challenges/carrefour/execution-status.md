# Execucao do desafio Carrefour

## Gate Playwright concluido (02/10/2026)

Prova atual: D:/RAG-Local/eval/runs/carrefour-playwright-gate-20261002-b/receipt.json.
Duas rodadas consecutivas: 334 testes Python + 18 cenarios Playwright por rodada;
zero falhas, erros ou skips. Os 17 avisos SDK por bateria Python foram preservados.
64 arquivos congelados, seeds 126021/330994, ordens diferentes, mesma imagem;
segunda rodada em projeto/volumes novos, sem mocks de respostas HTTP.

Chromium 151.0.7922.34 em container: Swagger offline, exemplo executavel, schemas,
POST/replay/conflito, GET pelos dois IDs, recibo do CLI real, erros/PII/JSON duplicado,
corpo excessivo, catalogo/exames invalidos e viewport mobile sem overflow horizontal.
Nenhuma origem externa solicitada no browser. OCR/RAG/SSE/ADK, API persistente e
falhas reais foram comprovados separadamente, nao inferidos de screenshots.

8 cards novos resolvidos: BUG-102..109. Incluem CDN, exemplo/UUID/codigos,
schema422 divergente, seletor de teste, digest de build, schemas por status,
manifesto autorreferente e MIME aceito por prefixo. Historico de reproducoes
preservado em discovery-history. Gate A foi interrompido apos BUG-109:
seu primeiro passe historico NAO foi usado na aprovacao do codigo final.

44 cards do escopo CF: DONE, inclusive 30 bugs de CF. Nenhum bug CF aberto.
Fila global: 73 DONE, 1 retirado e 20 pendencias anteriores do RAG (9 BLOCKED,
11 NEEDS_FIX). Essas pendencias nao foram aprovadas por testes do desafio.

Limites: testes locais Docker, mesma autoria, matriz finita; nao auditoria cega
independente nem prova de ausencia de todos os bugs. Producao publica NAO foi
fornecida/implantada/testada. Nenhum commit, push, submissao, billing ou chamada
Gemini/Vertex. Swagger local: http://127.0.0.1:8860/docs.

## Historico anterior ao gate Playwright

CF-01 a CF-14: DONE. BUG-078 a BUG-086 e BUG-089 a BUG-101: DONE no gate historico abaixo.
A fila global conserva os cards anteriores; nenhum card antigo recebeu uma conclusao por inferencia.

## Busca adversarial nova concluida em 02/10/2026

Gate atual: D:/RAG-Local/eval/runs/carrefour-blind-gate-20261002-a/receipt.json.
322 testes em cada uma das duas rodadas consecutivas, zero falhas/erros/skips.
Seeds 126021 e 330994, ordem aleatoria reproduzivel, mesma imagem/fontes congeladas;
segunda rodada em projeto Docker e volumes novos. Os 17 avisos de SDK em cada
rodada foram preservados; nao sao erros nem foram ocultados para aprovar o gate.

Matriz docs/use-cases.json: oito componentes x usuario/desenvolvedor/atacante,
24 casos declarados com esperado/proibido/prova. O verificador exige execucao
aprovada de todos no JUnit real. Todos os 120 exames e seus aliases foram exercitados.
Isso nao cobre todas as entradas possiveis nem equivale a auditoria independente.

13 cards novos corrigidos, BUG-089..101 (inclui um problema do proprio teste).
Reproducoes antes das correcoes em carrefour-discovery-20261002-a e -b:
ledger GET indisponivel/handle nao fechado, recibo corrompido, JSON ambiguo,
correlacao inicial, arquivo ilimitado/FIFO, tipos de evidencia, HTTP incerto,
recibo HTTP duplicado, emissao bloqueante, PII legitima confundida com comando,
contador OCR bool/float, conexao de teste reaproveitada apos rejeicao e Medico acentuado.

OCR/RAG interrompidos: nenhum agendamento. API pausada: resultado desconhecido
preservando request_id; replay da mesma chave reconciliou recibo unico.
Reinicio preservou recibos; scans nao acharam as sentinelas PII nos logs/ledger.
RAG 8840 e Grafana 8850 permaneceram acessiveis. Sem Gemini/Vertex neste gate.

36 cards do escopo CF aprovados; fila global com 65 DONE, 1 retirado e 20 pendentes.
As pendencias antigas do RAG NAO estao aprovadas por estes testes do desafio.
Nenhum commit, push, submissao, billing ou publicacao externa foi executado.

Os gates anteriores abaixo sao historicos: suas fontes foram modificadas e suas
aprovacoes invalidadas, antes de serem substituidas pelo gate atual.

## Revalidacao de 02/10/2026 UTC (01/10 no horario local)

O PDF foi relido integralmente; SHA-256
8d65f159d05aa388e5456b0397505220e76268c5d0bb86646104ccb2ef3c1a3b
permanece igual. Nenhum PDF mais novo foi anexado nesta retomada.

Novo gate real: D:/RAG-Local/eval/runs/carrefour-recheck-20261002-a/receipt.json.
Novamente 125/125 testes em cada rodada, sem falhas, erros ou skips; segunda
rodada em projeto/volumes novos. Falhas reais, replay, privacidade e persistencia
foram reexecutados. As 48 fontes do desafio continuam iguais ao gate anterior.

Os 14 cards CF e os nove bugs CF permanecem aprovados. O novo pacote de entrega
e D:/RAG-Local/deliveries/carrefour-challenge-20261002.zip. Nao houve commit,
push ou envio ao avaliador; a submissao no repositorio ainda e uma operacao
separada, nao executada.

## Fila antiga: nao confundir com requisitos deste PDF

- BUG-062/063/087: imagem SDK isolada corrigida, pins/hashes completos, pip check
  e imports reais de DeepAgents/DeepEval aprovados em duas rodadas offline.
- BUG-018/019/021/025/027/028/031/038/057 revalidados com fontes atuais.
- BUG-088: teste de observabilidade antigo chamava Docker real apesar de se
  declarar offline; corrigido para o seed QA atual, com prova em duas rodadas.
- RAG-01/03: leitura MCP de Jira/GitHub e negacoes de acesso passaram duas vezes.
  Jira retornou KAN-1/2/3. GitHub retornou lista de PRs vazia: nao prova um novo PR.
  Feed administrativo nao e ingestao dessas fontes no workflow RAG.
- RAG-16: sete consultas reais passaram; a seguinte recebeu ServerError do Gemini
  e abstencao segura. Nao foi aprovada, nao houve retry em massa nem fallback pago.
- 22 aprovacoes antigas tinham hashes vencidos e foram reabertas; parte foi
  revalidada nesta retomada. Recibos historicos nao foram apagados.
- Restam cards antigos de integracao/avaliacao, identidade, carga, recuperacao e
  Vertex. Nenhum recebeu DONE para aparentar "100%". O estado vivo e o journal.

Vertex, DeepAgents, login empresarial, HA e 100 mil requests nao sao exigencias
do PDF Carrefour. A entrega do desafio nao certifica esses segmentos do RAG.

## Provas do gate anterior (preservadas)

- Rodada 1: 125 testes, zero falhas/erros/skips; seed 481516.
- Rodada 2: 125 testes, zero falhas/erros/skips; seed 271828, em projeto e volumes novos.
- Mesmos hashes de fonte e mesma imagem Docker nas duas rodadas.
- Interrupcao real de OCR/RAG: erro seguro e nenhum agendamento.
- API pausada: timeout real, resultado desconhecido com request_id; replay com a mesma chave reconcilia um recibo unico.
- Reinicio da API: recibo persistido preservado.
- Sentinelas ficticias ausentes dos logs de aplicacao e do ledger nas verificacoes.
- RAG em 8840 e Grafana em 8850 continuaram disponiveis.
- Sem chamadas ao Gemini/Vertex neste desafio; 120 exames com codigos ficticios.

As rodadas sao regressao adversarial da mesma autoria, nao auditoria cega independente nem garantia de inexistencia de qualquer bug. Avisos experimentais/deprecacao do SDK foram registrados; nao sao falhas de teste. Nao foi demonstrada escala de 100 mil requests ou producao publica.

## Arquivos e acesso

- Projeto: D:/RAG-Local/carrefour-challenge/README.md
- Recibo geral: D:/RAG-Local/eval/runs/carrefour-final-20261001-c/receipt.json
- Recibos individuais: CF-01.json ... CF-14.json e BUG-078.json ... BUG-086.json na mesma pasta.
- Swagger: http://127.0.0.1:8860/docs
- Journal canonico: D:/RAG-Local/.local/card-execution/progress.json

Os containers permanentes api/ocr/rag ficam sob o Docker daemon, com restart unless-stopped. A tentativa de registrar um supervisor adicional Windows foi bloqueada pela politica do ambiente; nao foi declarado um Scheduled Task inexistente. PC/daemon desligados interrompem o laboratorio.

O segundo projeto de avaliacao foi removido depois do teste; seus volumes ficticios foram preservados. Nao houve commit, push, publicacao externa, pagamento ou mutacao de credenciais.
