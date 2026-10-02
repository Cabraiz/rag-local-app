# Cadastro online — contrato de custo antes da ativação

Pedido em 30/09/2026: criar/configurar contas do laboratório, preferir Free e
não gastar mais de USD 10. Sem período informado, não presumir renovação mensal
nem assinatura recorrente autorizada. Estratégia inicial: gasto autorizado USD 0,
somente gratuito confirmado, sem cartão, recarga ou billing pago ativados.

Esperado: conta/projeto/site pertencente ao usuário, plano Free verificável no
provedor, dados apenas sintéticos, integrações de leitura. Vertex Express Free
somente se a conta for elegível e sem billing; Gemini Developer API Free é uma
alternativa diferente de Vertex, não uma certificação disfarçada dele.

Proibido: ativar trials pagos ou upgrades automáticos, comprar add-ons, provisionar
compute/storage cloud, criar billing pago, mudar recursos empresariais existentes,
reutilizar tokens do Codex no aplicativo, aceitar termos ou criar credenciais
persistentes sem confirmação no momento da ação. Nenhuma promessa de hard cap
baseada em orçamento com alertas ou spend cap sujeito a atraso/overage.

Menor prova: leitura do plano/billing do recurso dedicado, criação confirmada por
UI e identificação do recurso, estado de integrações, tratamento seguro de secrets
e teste pequeno autorizado. Tentar clicar não comprova criação. Registro não deve
conter API keys, cartões, senhas, OTPs ou prompts corporativos.

Pesquisa oficial atual:
- Gemini API tem Free Tier; plano pago exige billing e pode incluir prepay.
  https://ai.google.dev/gemini-api/docs/billing
- Vertex/Agent Platform Express Free: até 90 dias para novos usuários elegíveis,
  sem informação de faturamento; conta existente pode não ser elegível.
  https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start/express-mode/overview
- Spend cap Google Cloud é Preview, por projeto/serviço, com execução em curso,
  recursos persistentes e atraso que podem gerar cobranças além do cap.
  https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps
- Jira Free: até 10 usuários, sem cartão no cadastro observado. Não convidar
  pessoas, adicionar apps pagos ou converter o plano. Confluence exige verificar
  separadamente Free; exceder limite de usuários pode disparar upgrade/trial.
  https://www.atlassian.com/software/jira/pricing
  https://www.atlassian.com/licensing/cloud

Estado observado até este registro: Google AI Studio está com sessão aberta;
tentativa de criar `rag-local-lab` foi rejeitada por termos não aceitos. Não há
projeto novo confirmado. Console Express encaminhou para uma conta de faturamento
já existente. A visão geral mostrou "Conta de teste gratuito" e custo do mês
R$ 0,00; isso não comprova elegibilidade Express, saldo restante ou ausência de
custos ainda não reportados. Nenhum upgrade nem alteração dessa conta foi feito.
Jira Free está no cadastro inicial. Nenhum pagamento, modelo remoto ou credential
novo foi ativado por esta execução. Foi solicitada confirmação específica para
nome/email no Atlassian e aceite dos termos gratuitos; ainda não recebida.

O início do AI Studio mostrou o aceite pendente dos termos APIs Google/Gemini,
com aviso de que prompts e respostas podem ser usados para treinamento; deixar
marketing desmarcado e usar apenas dados sintéticos. Não aceitar sem confirmação.
Prova do ponto de parada: `proof/google-free-terms-pending.jpg`.

## Continuação após aceite pessoal do usuário

Usuário informou ter aceitado e acessado Google/Jira. Prova atual no AI Studio:
`gen-lang-client-0580698701` / Default Gemini Project / Nível gratuito /
"este projeto não tem faturamento configurado". Chave já existente no projeto;
não foi criada outra chave, nem vinculada conta de faturamento pela execução.
Provas: `proof/gemini-no-billing-confirmed.jpg` e
`proof/gemini-existing-key-masked.jpg` (segredo não revelado).

Jira: primeira tentativa falhou por serviço/reCAPTCHA; uma recarga e nova tentativa
concluíram onboarding normal, sem bypass ou resolução de desafios. Site dedicado
`rag-local-lab-mateus.atlassian.net`, espaço `RAG Local Lab`, projeto `KAN`.
Convites foram pulados. O cadastro iniciado com `edition=free` redirecionou para
confirmação com `edition=premium`: isso exige verificação na assinatura e eventual
mudança para Free; criação do site não basta para comprovar plano gratuito.

Contrato para próxima etapa: permitido somente Gemini Developer API gratuito,
dados sintéticos e nenhum faturamento. Proibido fallback pago, Vertex pago,
trials/conversão pagos, recarga automática e divulgação da chave. Menor prova:
segredo local restrito fora do Git, uma chamada sintética bounded, assinatura
Jira Free confirmada. Metadados de perfil não são implementação desses gates.
Foi solicitada autorização específica para salvar a chave existente em arquivo
privado local. Não copiar nem persistir o segredo antes da resposta.

Verificação final Jira: administração confirmou Premium trial/estimativa
USD 17.12/30 dias/sem método de pagamento. Alterar plano -> Selecionar Free ->
Downgrade para Free -> Ignorar pesquisa; reload e releitura mostraram Free,
1 user e informações de pagamento None. Nenhum dado, usuário ou site foi excluído;
apenas funções Premium do site recém-criado deixam de estar disponíveis.
Prova: `proof/jira-free-confirmed.jpg`. Nenhuma chamada remota Gemini ou MCP
foi executada. Testes remotos ainda pendentes; cadastro não é teste end-to-end.

## Armazenamento autorizado e interrupção de segurança

Usuário autorizou continuar com armazenamento privado da chave existente.
Diretório `D:\RAG-Local\.local\secrets` criado com herança NTFS desativada e
uma única regra Allow FullControl para o SID do usuário atual, herdável por arquivos.
Nenhuma chave foi persistida. A cópia via clipboard não retornou a chave no canal
documentado; o validador bloqueou a escrita. Clipboard anterior foi restaurado.

Ao inspecionar o diálogo visível da chave, a redação por formato legado não
reconheceu o formato novo do provedor e o valor apareceu em uma saída da ferramenta.
Não repetir o segredo neste documento ou em quaisquer logs/receipts. Tratar a
chave como exposta: não usá-la nem salvá-la. Foi solicitada autorização para criar
uma substituta somente neste projeto, persistir privadamente e excluir a anterior;
exclusão pode interromper outros consumidores e exige confirmação específica.

NÃO emitir AX, DOM, screenshot ou mensagem de erro de diálogos que possam conter
segredos, mesmo com substituição baseada em regex. Capturar em variável privada,
persistir somente o campo de UI autorizado e emitir apenas booleanos/metadados
de uma allowlist. Ao pedir confirmação, mostrar a lista mascarada, nunca o diálogo.
Prova do ponto de parada: `proof/gemini-key-replacement-pending.jpg`.
Nenhuma chamada Gemini, Vertex ou MCP foi feita nesta etapa.

## Substituição autorizada e provas subsequentes

Na resposta à confirmação específica, usuário autorizou a ação. Criada chave
`RAG Local Lab Backend` no mesmo projeto gratuito. Capturado o campo visível sem
emitir AX/DOM/segredo; arquivo privado criado com escrita exclusiva. ACL verificada:
somente SID do usuário atual, uma regra. Chave `Default Gemini API Key` excluída
com confirmação; lista releita mostrou somente a substituta. Não tentou recuperar
a antiga. Nenhum faturamento, pagamento ou outro projeto alterado.
Prova mascarada: `proof/gemini-replacement-key-masked.jpg`.

Backend recebeu somente probe sintético opt-in ADK em container, separado do
workflow extrativo. Duas chamadas de inferência retornaram HTTP 404; SDK error
bodies não registrados. Consulta somente de catálogo enumerou modelos, incluindo
o planejado; isso não certifica inferência nem identifica a causa do 404. Counter
persistente já consumiu duas tentativas do dia e não foi zerado/contornado.
Controles locais passaram 14 checks em cada uma de duas rodadas, após correção de
handle SQLite não fechado. Não são auditoria independente nem dois passes remotos.

MCP Atlassian: página de credenciais exigiu step-up com código de 8 dígitos enviado
ao e-mail. Nenhum código lido da caixa postal; nenhum token criado. Usuário precisa
verificar diretamente no navegador. Conector de Codex não entrega credenciais ao
backend. Prova: `proof/atlassian-identity-verification-pending.jpg`.

## Revisão do modelo sem contornar limites

A documentação oficial atual limita a família 2.5 a usuários com utilização
prévia e recomenda 3.5 Flash-Lite para projetos novos. Fonte:
https://ai.google.dev/gemini-api/docs/deprecations. É uma explicação possível
para o 404, ainda não confirmada pela resposta específica do provedor.

Antes de mudar runtime, concluídas as fixtures: 31/31 RAG e 34/34 lifecycle,
duas rodadas cada. Depois, perfil sintético migrado para `gemini-3.5-flash-lite`
Standard gratuito e thinking minimal. Controles 14/14 x2; construção SDK/ADK
18/18 x2, no host e no container sem rede/sem segredo. Falha inicial do harness
por importação sob mock preservada, corrigida sem mudar o resultado esperado.
Nova imagem exige novas regressões. Provas offline não viram inferência real.

Leitura readonly do volume confirmou duas reservas em 30/09/2026 UTC. Sem reset,
troca de projeto ou nova inferência nessa janela. Chave/egress seguem separados
do worker; ninguém deve ativar billing para tentar corrigir a disponibilidade.

## Janela UTC seguinte: falha remota permanece

Em 01/10/2026 UTC, durante as regressões finais necessárias, a janela diária
seguinte abriu naturalmente. Sem espera artificial/reset, executadas duas
tentativas sintéticas na mesma revisão 3.5. Ambas retornaram HTTP 400; a segunda
confirmou somente status `INVALID_ARGUMENT`. Diagnóstico em allowlist não
identificou causa específica; não prova que credencial ou parâmetros estejam
corretos. Corpo bruto do SDK e segredo não foram registrados.
Receipt: `eval/runs/gemini-35-remote-20261001/receipt.json`.
Limite da nova janela consumido. Não fazer terceiro probe, aumentar quota, mudar
projeto ou ativar billing. Integração Gemini de respostas RAG segue desligada.

## Fechamento local e confirmação pendente do MCP

Imagem final: RAG 31/31 x2 e lifecycle 34/34 x2, com fontes/imagens congeladas.
Falha Qdrant de 30 segundos preservada; protocolo LAB agora distingue processo
iniciado de API pronta e registra duração. Não certifica RTO 30s ou produção.
Fixtures encerraram seus próprios containers e preservaram volumes.
Resumo: `eval/runs/setup-final-20261001/receipt.json`.

Conferência privada mostrou token visível Gemini igual ao arquivo salvo; não
emitido seu conteúdo. Busca do valor literal em 197 artefatos textuais de
app/docs/eval: zero ocorrências, sem arquivos omitidos/ilegíveis nesse escopo.
Isso não prova autenticação remota válida nem ausência universal de exfiltração.

Página Atlassian passou da identidade para gerenciamento de tokens. Nenhum
token existente; preparado apenas rascunho `RAG Local Lab MCP Read Only`,
Atlassian MCP V2, expiração 07/10/2026. UI marcava 25 escopos por padrão,
incluindo escrita: desmarcados, selecionados somente read:jira:agent-interface,
search:jira:agent-interface e read:me. Revisão final confirma esses três.
Criação e armazenamento privado requerem confirmação específica solicitada ao
usuário; botão Criar token NÃO clicado. API token não é limitado a cloudId: app
precisa validar site/projeto/tools do laboratório antes de I/O. Não ampliar para
Confluence/TWG ou escrita como fallback. Prova:
`proof/atlassian-mcp-readonly-review-pending.jpg`.

## Token aprovado, persistido e bloqueio de política MCP

Usuário autorizou explicitamente a criação e armazenamento. Primeira tentativa foi
recusada por step-up expirado; usuário concluiu a verificação no navegador. Refeito
o rascunho, desmarcados os 25 escopos padrão, conferidos exatamente os três já
aprovados. Token criado, capturado do campo visível sem emitir segredo e escrito
com `flag=wx` em `.local/secrets/atlassian-mcp-token.txt`. Campo fechado, variável
privada limpa e lista mostra o token. ACL: uma regra, Allow, apenas SID atual.
Prova: `proof/atlassian-readonly-token-created.jpg`.

Cliente MCP oficial 2.2.0 instalado como dependência adicional em imagem separada;
imagem base e serviços RAG padrão não foram reconstruídos/alterados. Python ADK
FunctionTool oferece só leitura de KAN-1/2. Falhas offline preservadas: comparação
de Path Windows no harness e `_meta={}` do SDK, depois corrigidas. Oráculos ampliados
antes de novas entradas: 54 verificações x2 no host e container sem rede, mesma fonte
final; não são auditoria independente nem passes remotos.

Sessão real initialize/tools-list funcionou; leituras falharam. Diagnóstico sem
corpo bruto revelou apenas tag 401. GET público do tenant_info do site confirmou
cloudId fixado. Administração da organização `rag-local-lab-mateus`, Rovo MCP ->
Autenticação, confirmou checkbox "Permitir autenticação por token de API" desligado.
Isso é compatível com a falha; não prova ausência de outros problemas após ativação.
Política NÃO alterada: solicitada confirmação específica para habilitar somente nessa
organização. A liberação vale para tokens válidos da organização, não só este token.
Não mudar IP allowlist, planos, billing, escopos, outras organizações ou métodos de
autenticação como atalho. Prova: `proof/atlassian-mcp-api-token-disabled.jpg`.

Busca do token literal em 228 artefatos textuais de app/docs/eval: zero ocorrências,
zero arquivos ilegíveis nesse escopo. Não é garantia universal de não exfiltração.
Gemini continua reprovado e sem nova inferência nesta janela UTC; contador preservado.

## Política MCP autorizada e ativada; leitura real ainda bloqueada

Usuário confirmou no momento da ação a ativação de autenticação por token
somente em `rag-local-lab-mateus`. Checkbox ficou ativo e persistiu após reload;
enterprise managed authentication permaneceu desligado. Sem alterações de domínio,
IP allowlist, escopos, outras organizações, plano ou billing. Prova nova preservada:
`proof/atlassian-mcp-api-token-enabled.jpg`.

Probe real na mesma imagem continuou falhando no primeiro getJiraIssue: tag 401.
Diagnósticos adicionais, separados do adaptador, provaram header Basic esperado
presente em cada request, transporte 200/202, mas resultado MCP de erro 401.
HEAD sugerido na documentação oficial retornou 400; não é autenticação aprovada.
UI confirmou os mesmos três escopos e token existente. Causa restante não isolada;
nenhuma rotação/criação de token ou mudança de método feita como tentativa cega.

Controles offline repetidos nesta execução: 54 checks x2 no host e 54 x2 no
container sem rede/segredo real, oráculos e adaptador inalterados. Não equivalem
a dois testes remotos ou auditoria independente. Resumo atualizado:
`eval/runs/atlassian-policy-enabled-20261001/receipt.json`.
Gemini: zero novas inferências; contador diário preservado. Projeto completo e
produção continuam reprovados; workflow de respostas ainda não ligado ao MCP.

## Substituto autorizado, sem ampliação ou revogação; falha persiste

Usuário respondeu afirmativamente à proposta concreta de substituir apenas o token
com os mesmos três escopos e validade, preservando o anterior. Rascunho MCP V2
voltou a selecionar 25 escopos padrão; desmarcados todos e escolhidos exatamente
os três aprovados. Review final conferido antes de criar. Novo segredo capturado
do campo visível sem emissão, arquivo novo `atlassian-mcp-token-replacement.txt`
criado com `wx` na pasta privada; original intacto. ACL: uma regra somente usuário
atual, herdada da pasta protegida. Campos secretos fechados, variáveis limpas.
Lista da UI mostra ambos os tokens; modal do substituto confirma scopes.

Configuração opt-in passou a montar somente o substituto, sob alias existente.
Sequência zerada e testes offline repetidos: 54 x2 host e 54 x2 container, mesma
revisão congelada. Um probe regular falhou; diagnóstico separado começou 79s após
persistência do token, verificou Basic esperado em cada envio e também retornou
erro MCP 401. HEAD documentado retornou 400, não prova autenticação válida.
Nenhuma leitura real completa aprovada; troca de chave não solucionou o problema.
Não repetir criações de token como loop sem nova evidência ou ampliar permissões.
Gemini sem chamadas novas, limites/volume preservados; nenhuma ação paga.
Resumo: `eval/runs/atlassian-replacement-20261001/receipt.json`.
