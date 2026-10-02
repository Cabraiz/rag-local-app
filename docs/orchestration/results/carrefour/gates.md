# Gates pendentes da Central

Esta lane executa somente checks offline. Nada abaixo foi executado pelo executor.
O driver `carrefour-challenge/tools/verify.ps1` serializa dois gates diferentes em
cada uma das duas rodadas. O gate Docker fornece o recibo CLI real necessário ao
gate Playwright; cada um tem seus próprios resultados e artefatos. A aprovação de
CF-14 exige os dois sobre as mesmas fontes, sem failures/errors/skips.

## Driver autorizado, depois da integração

Na pasta do desafio, em terminal integrado PowerShell, a Central pode executar:

```powershell
.\tools\verify.ps1 -Evidence ..\.local\carrefour-gates\integrado-20261002 -FirstApiPort 18860 -SecondApiPort 18861
```

Pré-condições: ownership da Central, Docker disponível, portas livres e pasta de
evidência vazia. O driver adquire `.local/carrefour-gate.lock` com FileShare.None,
confere inventário vazio de dois projetos novos e registra `ownership.json`.
Usa `--project-directory` da própria pasta, arquivo de configuração sem segredos,
tags de imagens por runId e volumes dos projetos novos. Não lê `.env`, não reutiliza
o projeto da demonstração e não executa `down -v`, prune ou providers. A trava
permanece adquirida durante os gates e sua limpeza.

As portas 8840/8850 são consultadas somente para preservar o laboratório/Grafana;
nenhuma interrupção é dirigida a esses serviços. Essa preservação exige prova
real do driver; a análise de fonte da lane não a aprova.

## Gate D — Docker, OCR, MCP SSE, ADK, CLI e SQLite

Etapas próprias: `build api browser`, `up --no-build --wait api ocr rag`, `run tests`,
`tools/check_use_cases.py`, transpile/run do CLI e consultas à API. Os argumentos
Compose usam o prefixo registrado em `ownership.json`, nunca um projeto canônico.

- Executar toda a suíte pytest original, mais os oráculos offline adicionados,
  nas dependências de `requirements.lock`: Python 3.12 do container, ADK 2.10,
  MCP 2.2, pytest, Pillow e Tesseract português.
- Provar OCR nas imagens fictícias; initialize/list/call HTTP+SSE reais; negativos,
  sanitização e allowlists; execução dos dois agentes gerados; recibos idempotentes
  duráveis; contratos OpenAPI; exports repetidos e hashes do pacote.
- Provar FIFO, symlink/device e rename concorrente nativos Linux. Os adapters
  Windows não aprovam essas propriedades.
- Sob ownership dos projetos novos, executar as interrupções/reinícios já definidos
  no driver. Confirmar resultado desconhecido e reconciliação com a mesma chave,
  readiness depois de restart, ausência de booking parcial, PII em logs/ledger e
  preservação do laboratório existente.
- Segunda rodada: projeto/volumes novos, seed 330994; primeira: seed 126021.

Artefatos separados: `round-1.xml`, `round-2.xml`, `use-cases-1/2.json`, logs
`round-*-suite/cli/fault/ledger/service-state`, hashes das imagens e fontes,
`ownership.json`, checkpoints e exports. O digest da imagem Playwright deve ser
resolvido no build real; comprimento hexadecimal correto não prova sua existência.

## Gate P — Playwright e Swagger realmente construído

Etapa própria, em cada projeto já saudável e com o UUID do CLI persistido:

```text
compose <prefixo de ownership> run --rm --no-deps
  -v <diretorio-browser-da-rodada>:/evidence
  -e CF_SEED=<seed-da-rodada>
  -e CLINIC_CLI_REQUEST_ID=<UUID-real-do-CLI> browser
```

O driver executa essa etapa como `round-1-playwright`/`round-2-playwright`, antes de
limpar seus projetos. Não executar o comando isolado contra a clínica existente.

Exigir todos os 18 cenários originais: assets locais sem CDN/origem externa,
schema e exemplo executável, criação/replay/conflito, consultas pelos dois IDs,
recibo real do CLI, PII/JSON duplicado/limites/exames e catálogo inválidos,
distinção entre UUID bloqueado no formulário e HTTP 422 da API, viewport móvel.
Nenhum `route.fulfill`, resposta simulada ou ID fictício substitui esse gate.

Artefatos separados: `browser-1/2/browser-report.json`, traces e screenshots.
Exigir `complete=true`, zero failures/skips, pelo menos 18 casos, ausência de
origens externas e reconciliação com o recibo CLI real. Um `/docs` 200 em ASGI
com diretório vazio de assets não aprova este gate.

## Bloqueios desta lane

O único Python autorizado no host não tem pytest nem Pillow. Não houve instalação
no ambiente compartilhado. A suíte original, Tesseract, SSE real, containers,
assets e Chromium continuam pendentes. A suíte `unittest` é da mesma autoria,
com adapters explícitos; não é auditoria independente nem o gate integral CF-14.
Os requisitos R01–R12 foram rastreados pela matriz versionada do projeto; o PDF
original não foi reextraído nesta lane. A Central deve validar a fonte fornecida
e não converter esse limite em aprovação integral.

O check offline `tools/check_powershell_gate.ps1` analisa sintaxe e testa somente
o wrapper nativo, substituindo Docker pelo Python autorizado. Diagnóstico em
stderr com exit 0 deve passar; exit 7 deve permanecer 7. Ele não executa Compose.
No Windows com ExecutionPolicy Restricted, execute-o em processo PowerShell
temporário com `-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden
-ExecutionPolicy Bypass -File`, sem alterar a política persistente da máquina.
