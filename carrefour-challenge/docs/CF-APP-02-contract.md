# CF-APP-02: contrato e oráculos anteriores à implementação

Base: `3ed9592309fe91b70221432944dc5111636a548f`, branch
`codex/carrefour-exec-02`. Escopo: compilador, preflight isolado, fixtures e
verificações deste card. A especificação canônica permanece somente leitura.

Esperado: DSL versão inteira 1; somente google-adk/SSE/offline, cinco etapas
tipadas na ordem OCR, recuperação, validação, agendamento e formatação. O grafo
linear não admite referências, tools configuráveis nem ciclos. Nomes são
identificadores ASCII limitados, não palavras reservadas ou símbolos do emitter.
A mesma entrada semântica gera exatamente os mesmos bytes e SHA-256. A emissão
revalida a IR, inclusive após mutação/construção sem validação.

Proibido: campos extras, coerção de tipos, chaves duplicadas, referências e tools
desconhecidas, ciclos, payloads Python/shell, imports/URLs/caminhos controlados
pela entrada. Nenhuma entrada seleciona código, imports ou nomes de arquivo para
execução. Não executar Python arbitrário. Sem rede externa, dados reais,
credenciais, serviços pagos, alteração de volumes ou fila compartilhada.

| Casos fixados | Resultado e menor prova |
| --- | --- |
| Dois JSON existentes; ordem de chaves/whitespace diferente | compile, Workflow do ADK instalado, cinco etapas executadas; bytes/hash iguais na entrada equivalente e distintos na variante |
| Versão bool/float/2, timeout bool/string/fora dos limites, raiz inválida | SafeError sem refletir valores |
| Extras na raiz/etapa, tool ausente, next/back edge/self loop | rejeição antes de qualquer execução |
| Nome duplicado, reservado, Unicode, longo, Python e traversal | rejeição; zero rede/escrita indevida |
| JSON duplicado, inválido, NaN, encoding não UTF-8, excessivo/profundo | falha fechada e diagnósticos limitados |
| IR mutada ou model_construct com payload | emissão recusa antes do compile |
| Artefato adulterado e paths relativos/absolutos | igualdade de bytes obrigatória; entrada não controla path do preflight |
| Preflight do agente real | subprocesso, cwd temporário, timeout, limites POSIX e audit hook; Docker sem rede, root somente leitura, cap_drop e memória/PIDs limitados |
| Spies negativos | socket/DNS, leitura fora da allowlist, escrita fora do temporário e subprocessos bloqueados; contadores e arquivos canário inalterados |

O preflight executa o grafo ADK real com uma implementação local de etapas que
retorna apenas seus nomes. Não prova OCR, MCP SSE, API, reserva ou diálogo. A CLI
clínica continua precisando da rede interna fixa para seus serviços; o preflight
sem rede é uma interface própria de compilador disponível para integração pelos
owners CF03/08. CF02 não altera a CLI nem o runtime clínico.

Reprodução: testes Docker sobre mount somente leitura deste worktree, sem
serviços ou volumes compartilhados. Duas rodadas consecutivas usam o mesmo
image ID, hashes, fixtures, seed e comandos. Revisão independente após a mudança;
mudança relevante reinicia as duas rodadas. Receipt local não fecha o card.

Interface de compilador: `clinic_adk.sandbox.check_generated(spec)` aceita apenas
`AgentSpec`, revalida o documento e devolve hash, versão instalada de ADK, etapas,
spies negativos e contadores do grafo. Executa sondas fixas dentro do mesmo worker
antes do grafo: sockets IP/DNS, leitura/escrita fora das raízes e subprocesso. Cada
bloqueio deve vir do audit hook; uma negação do filesystem somente leitura não
substitui essa prova. Nenhum path ou Python fornecido pela entrada é executado.

Reprodução na raiz deste checkout:

```powershell
& .\carrefour-challenge\tools\verify_compiler.ps1 -Evidence .local\cf-app-02\nova-prova
```

O harness fixa image ID local, seed=1234, fontes e corpus nas duas rodadas. Ele
executa também as suítes originais `test_unit.py` e `test_artifact_integrity.py`.
O gate não instala dependências, não inicia serviços e não usa volumes existentes.
Não é build limpo nem prova da execução clínica da CLI, cujo owner é CF08.
Slots e consentimento são estado de interação de CF03/08, não definições de código
na DSL; a versão 1 e a assinatura `build_agent(runtime)` continuam compatíveis.
