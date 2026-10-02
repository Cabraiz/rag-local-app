# Carrefour — revalidação offline

Branch `codex/carrefour`, base `3dba8c0f557421cd3f6d5bc26017278a95a53dbb`.
Escopo: todos os **44 cards e 116 critérios** do snapshot atribuído. Nenhum estado
antigo `NEEDS_FIX` foi convertido automaticamente em bug. O executor não atualizou
o journal, não chamou providers e não executou Docker, Compose, runtime canônico,
fault injection de serviços, push ou merge. O HEAD permanece na base: a criação
de `D:/RAG-Local/.git/worktrees/carrefour/index.lock` foi negada pelo ambiente
durante `git add` (exit 128). Nenhum commit foi criado e nenhuma permissão/ACL
foi alterada. O worktree e o patch de handoff preservam todas as alterações.

Foram reproduzidos e corrigidos nove grupos de falhas. A suíte offline reúne
**33 testes**, incluindo subcasos sobre todos os 120 exames/aliases, três pares
nome/código esperados explicitamente, mutações de usuário/desenvolvedor/atacante,
compilação/importação ADK, API ASGI real e SQLite isolado. O check PowerShell verifica
sintaxe e três contratos de processo nativo. As duas rodadas finais usam seeds
126021/330994, com fontes e documentação congeladas; cada receipt contém SHA-256.
Falhas anteriores foram preservadas, e qualquer edição reiniciou a contagem limpa.

Essa aprovação é **somente do segmento offline**. A suíte pytest original permanece
inalterada e não foi executada: o Python autorizado do host não contém pytest/Pillow.
Não houve instalação no ambiente compartilhado. Não é auditoria independente,
aprovação integral CF-14 ou promessa de ausência de bugs.

## Falhas para a Central registrar no fim da fila

IDs abaixo são locais de discovery, não novos IDs oficiais do journal.

| ID local | Reprodução antes da correção | Correção / prova |
| --- | --- | --- |
| CF-NEW-01 | Dockerfile copiava requirements.lock para outro diretório, mas pip procurava /app/requirements.lock | COPY coerente com WORKDIR/RUN; oracle estático. Build real pendente |
| CF-NEW-02 | Gate fixava D:/RAG-Local/app e projeto existente; também registrava o índice errado do prefixo como projeto | Fontes próprias, dois projetos/imagens novos, ownership/trava, env-file sem segredos e identificação correta. Docker real pendente |
| CF-NEW-03 | Exame desconhecido sem rótulo, com prefixo PEDIDO/CLINICA/DADOS FICT/DEMONSTRACAO, era ignorado ao lado de exame válido | Cabeçalhos completos allowlisted; variante adjacente a BUG-082, sem aprovação parcial |
| CF-NEW-04 | JSON SQLite com status REJECTED e REQUESTED duplicados retornava sucesso nas três rotas | Parser rejeita chaves duplicadas antes de validar o recibo; extensão de BUG-090 |
| CF-NEW-05 | Catálogo com fictional=false, versão bool, código inválido ou campos mal tipados era aceito ou gerava TypeError | Leitura limitada, versão/marcador/tipos/códigos/nomes estritos, JSON sem duplicatas e erros seguros |
| CF-NEW-06 | argparse refletia nomes/valores de argumentos desconhecidos e escapava do erro JSON da CLI | Parser sanitizado retorna exit 2 e CLI_INVALID_ARGUMENTS |
| CF-NEW-07 | Exame sem rótulo contendo PII era descartado, deixando resultado parcial agendável | Apenas cabeçalhos pessoais conhecidos podem ser descartados; outras linhas com PII bloqueiam |
| CF-NEW-08 | PowerShell 5.1 com ErrorActionPreference=Stop rejeitava diagnóstico em stderr de processo exit 0 | Wrapper distingue stderr de exit code; 0 continua 0, falha 7 continua 7. Scanner de ledger virou arquivo, evitando código inline com aspas |
| CF-NEW-09 | Deadline total do workflow expirava após commit real na API em processo, mas CLI retornava WORKFLOW_FAILED_SAFE | Timeout após início do schedule informa resultado desconhecido; mesmo UUID reconcilia o recibo sem duplicar. Timeout antes de schedule informa ausência de início do agendamento |

Baseline de produto: `.local/orchestration/baseline-02/` (17 failures, dois errors,
agrupados nas seis primeiras falhas). Caso adjacente: `extended-baseline/` (duas
falhas para CF-NEW-07). CF-NEW-08: `native-stderr-reproduction.json`; CF-NEW-09:
`total-timeout-probe.json`, com solicitação persistida e erro genérico observado.
As tentativas
`baseline-01` e `after-fix-01` também foram mantidas; seus erros de adapter Windows
são diferenciados de bugs do produto. Passes intermediários não contam como gate final.
As rodadas `offline-round-1/2` passaram antes da documentação deste bloqueio;
as primeiras `offline-final-1/2` também passaram com 32 testes, antes de descobrir
CF-NEW-09. O receipt final usa `offline-release-1/2` com 33 testes, preservando
todas as sequências anteriores e vinculando o resultado às fontes atuais.

## Cobertura dos requisitos

Todos os critérios individuais e vínculos de teste ficam em
`.local/orchestration/card-audit.json`, com hash do snapshot e dos receipts.
As linhas abaixo não alteram estados dos cards no journal.

| Card | Prova offline atual | Limite / próximo gate |
| --- | --- | --- |
| CF-01 | Schema versionado, positivos/negativos, campos seguros, DAG limitado/ordenado | Segmento de contrato revalidado |
| CF-02 | Emissão determinística dos dois specs, imports/tipo Workflow ADK reais, compile, rejeição de injeção e integridade de bytes | CLI em container pendente |
| CF-03 | Sanitização, cabeçalhos legítimos, nomes/documentos/contatos, logs seguros e falha fechada | OCR real, estado do transporte e scan de logs/ledger do container pendentes |
| CF-04 | Fronteira de sanitização/argumentos e limite de arquivo regular | Imagem/Tesseract, SSE initialize/list/call, symlink/device/FIFO nativos pendentes |
| CF-05 | 120 nomes/códigos, versão/hash/marcador fictício, todos os aliases e três pares de oracle explícitos | Mesma autoria; não auditoria independente |
| CF-06 | Evidência canônica, desconhecidos, stale/ambiguidade e JSON inválido abstêm-se; mock lexical documentado | Transporte SSE real pendente |
| CF-07 | OpenAPI vs exemplo/respostas ASGI reais; SQLite, replay/conflito e 12 posts concorrentes com um recibo | Restart/persistência e HTTP publicados do Docker pendentes |
| CF-08 | Grafo/Runner ADK reais, cinco etapas e API ASGI/SQLite reais, com adapters sanitizados de ferramentas; gates de evidência/cleanup/retry | OCR/SSE e HTTP de serviços reais pendentes; teste de corrida substitui step explicitamente |
| CF-09 | Erros JSON seguros, correlação UUID e integridade do artefato; execute retorna recibo consultável da API em processo | Transpile/run como processos CLI e imagem real pendentes |
| CF-10 | Compose único, serviços/limites/redes declarados e gate isolado corrigido | Build, porta publicada, ambiente novo e preservação real do laboratório pendentes |
| CF-11 | Contratos/adversariais offline, PII/prompt injection, timeout/5xx/retry sem chave nova | pytest original, contratos SSE e interrupções reais pendentes |
| CF-12 | Fontes/specs/imagens presentes, manifesto válido e export repetido; README/IA/referências revisados | Pacote em container e evidências CLI/Swagger reais pendentes |
| CF-13 | Defesa técnica revisada: compilador/runtime, SSE/PII, simplicidade, mocks/cloud/produção | Demonstração real com CLI/Swagger continua no gate de entrega |
| CF-14 | Critérios R01–R12 rastreados pela matriz versionada; receipts honestos e fontes congeladas | Duas rodadas full E2E, PDF original, Docker e Playwright pendentes; não DONE |

## Regressões atribuídas

“Offline” significa o predicate equivalente sobre fontes atuais, nas duas rodadas
do runner, sem alegar execução da suíte pytest original. “Parcial” exige o gate indicado.

| Card | Check atual | Limite |
| --- | --- | --- |
| BUG-078 | Diagnóstico de campo desconhecido sem refletir nome/valor | Offline |
| BUG-079 | Compilação e importação da API; endpoints em ASGI | Offline |
| BUG-080 | Workflow BaseNode executado por Runner(node=...), ADK 2.10 real | Ferramentas/HTTP de rede substituídos |
| BUG-081 | ToolEnvelopeGuard rejeita extras/tipos e não reflete PII | SSE/SDK de rede pendentes |
| BUG-082 | Queries/cabeçalhos inválidos e resultados MCP sanitizados | Contrato SSE real pendente; caso CF-NEW-03 corrigido |
| BUG-083 | Parser PowerShell sem erros | Driver pesado pendente |
| BUG-084 | API declara rede edge/porta loopback, demais serviços internos | Porta host Docker pendente |
| BUG-085 | Troca do artefato após validação não executa novos bytes | Grafo ADK real; step substituído nesse oracle; arquivos especiais Linux pendentes |
| BUG-086 | Readiness --wait depois de restart preservado no driver | Restart/health reais pendentes |
| BUG-089 | GET/POST indisponíveis retornam 503 e inicialização parcial fecha conexão | Erros de conexão substituídos; SQLite local real |
| BUG-090 | Recibos corrompidos não retornam sucesso nas três rotas | Offline, mais CF-NEW-04 |
| BUG-091 | JSON de ferramenta duplicado/não finito rejeitado | Offline |
| BUG-092 | Falha de spec preserva UUID válido | read_spec substituído por falha tipada |
| BUG-093 | Limite de arquivo regular no descritor | Spec/FIFO nativos Linux pendentes |
| BUG-094 | code/name/evidence não escalares geram erro seguro | Offline |
| BUG-095 | 408/5xx conservam resultado desconhecido | Respostas HTTP substituídas; falha real pendente |
| BUG-096 | JSON duplicado no recibo HTTP é rejeitado | Resposta HTTP substituída |
| BUG-097 | Escrita atômica em arquivo regular | FIFO de saída e concorrência POSIX pendentes |
| BUG-098 | Nome/contato com substring de instrução em cabeçalho é descartado | Offline |
| BUG-099 | bool/float não são contador OCR inteiro zero | Resultado da ferramenta substituído |
| BUG-100 | Três clientes separados preservados na fonte do teste | Keepalive/rebinding reais pendentes |
| BUG-101 | Médico/MÉDICO/Médica aceitos em cabeçalhos legítimos | Offline |
| BUG-102 | HTML Swagger só referencia assets locais | Assets construídos/Chromium pendentes |
| BUG-103 | UUID/códigos/version reais no schema/exemplo, POST/replay ASGI | Swagger UI real pendente |
| BUG-104 | Schema 422 validado contra ambos os erros ASGI | Offline |
| BUG-105 | Assert UUID bloqueado no formulário preservado | Playwright pendente |
| BUG-106 | Digest Playwright é hexadecimal de 64 caracteres; lockfile preservado | Resolução/pull/build do digest pendentes |
| BUG-107 | Literais de erro OpenAPI coerentes com 400/408/409/413/415/503 e 404 | UI/rede reais pendentes |
| BUG-108 | Duas reexportações não incluem manifesto/unrelated e todos os hashes conferem | API em processo; export do container pendente |
| BUG-109 | Media types exatos e não persistência nos seis casos ASGI | HTTP de rede pendente |

## Provas e handoff

- `.local/orchestration/offline-release-1/2/{receipt.json,sources.json,tests.log}`:
  resultados atuais, fontes/documentação SHA-256 e ordens diferentes.
- `.local/orchestration/powershell-release-1/2/receipt.json`: parser e wrapper nativo,
  com Docker substituído pelo único Python autorizado; nenhuma chamada Docker.
- `.local/orchestration/card-audit.json`: 44 cards/116 critérios, fontes e limites.
- `.local/orchestration/worker-receipt.json`: branch/SHA, bloqueio do commit, arquivos, checks,
  falhas históricas, bugs novos, dependências e gates pendentes.
- [Gates Docker e Playwright separados](gates.md): execução serial pela Central,
  com ownership e dois projetos novos. Não fechar CF-14 por este segmento offline.
- `.local/orchestration/carrefour-handoff.patch`: todas as 14 alterações da
  allowlist, incluindo novos arquivos, prontas para revisão da Central.
- `.local/orchestration/commit-blocker.json`: erro de permissão observado;
  nenhum `index.lock` foi removido e nenhuma escalada foi tentada.

Nenhum ajuste de código fora da allowlist foi necessário. A Central adiciona as
novas falhas ao fim da fila, realiza o commit autorizado das alterações após revisão,
integra e executa os gates restantes. Commit bloqueado não é commit entregue.
O PDF não foi reextraído; o rastreamento usa o contrato versionado já fornecido.
