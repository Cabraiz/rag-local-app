# Perfis de laboratório e conexão online

Atualização de limite local em 01/10/2026: após autorização explícita, o probe
sintético admite até 1.000 tentativas por dia UTC. Contador preservado; nenhuma
chamada remota, mudança de plano, billing ou integração ao RAG nesta alteração.
As referências a duas tentativas abaixo são histórico das execuções anteriores.
Contrato atual: [gemini-limit-increase-20261001.md](../architecture/decisions/gemini-limit-increase-20261001.md).

Não existe conta empresarial compartilhada de teste para Vertex ou Atlassian.
`lab-profiles.json` documenta escolhas; não habilita integrações no runtime.
O default executável continua local, com dados sintéticos e sem chamadas pagas.

## Atualização atual: chave substituída e probe remoto ainda reprovado

- Após autorização específica do usuário, a chave `Default Gemini API Key`,
  exposta numa saída da ferramenta, foi excluída. A substituta
  `RAG Local Lab Backend` foi salva em `.local/secrets/gemini-api-key.txt`,
  fora do versionamento, com ACL NTFS restrita ao usuário atual.
- O projeto continua Free, sem faturamento. O container opt-in
  `infrastructure/compose/labs/compose.gemini-lab.yaml` executa somente um probe sintético ADK, separado
  do RAG padrão. Duas tentativas reais retornaram HTTP 404: integração remota
  NÃO aprovada; causa ainda não confirmada. O limite persistente de duas
  tentativas por dia UTC não será resetado para contornar a falha.
- Após conferir a política oficial de acesso à família 2.5 para projetos novos,
  o modelo fixo do probe foi atualizado para `gemini-3.5-flash-lite` Standard
  gratuito com thinking minimal. A janela diária UTC seguinte abriu durante as
  regressões locais; sem reset ou espera artificial, duas tentativas na revisão
  3.5 retornaram HTTP 400 `INVALID_ARGUMENT`. Causa específica não confirmada;
  zero passes remotos. Logs não contêm mensagens brutas/segredos do SDK.
  Preservar falhas 2.5/3.5 e não certificar conexão por testes offline.
- Controles offline: 14 checks em cada uma de duas rodadas passaram. Isso
  não substitui inferência real nem auditoria cega independente. O diário
  `gemini-lab-acceptance.md` registra esperado, proibido e provas.
- Atlassian: site Free confirmado e nova verificação de identidade concluída pelo usuário.
  Token MCP V2 criado após confirmação específica, válido até 07/10/2026, apenas
  `read:jira:agent-interface`, `search:jira:agent-interface` e `read:me`.
  O padrão da UI selecionava 25 escopos, inclusive escrita; foram desmarcados.
  Salvo privadamente em `.local/secrets/atlassian-mcp-token.txt`; ACL verificada,
  apenas usuário atual. Nenhuma credencial do Codex exportada. Token não é restrito
  a cloudId; gateway Python executável fixa KAN-1/KAN-2, cloudId e ferramenta.
  SDK MCP 2.2.0 + FunctionTool ADK em imagem separada: 54 checks x2 no host e
  container sem rede/sem segredo real. Sessão remota e catálogo passaram, mas
  leitura de issue falhou; diagnóstico bounded identificou 401 no erro da ferramenta.
  Depois de confirmação específica do usuário, API token foi habilitado somente
  na organização dedicada; reload confirmou persistência. Enterprise managed auth
  continua desligado. Permissões MCP de leitura já estavam 15/15 permitidas;
  os três escopos do token foram relidos e conferidos, sem mudança.
  Leituras posteriores continuam retornando erro MCP com tag 401. Diagnóstico
  verifica privadamente o header Basic em cada request; somente booleano e status
  HTTP são emitidos. HTTP 200 do transporte NÃO é sucesso da ferramenta.
  HEAD do procedimento documentado pelo provedor retornou 400, sem certificar auth.
  Causa restante não confirmada; não ampliar escopos ou usar outros sites/tokens.
  Zero rodadas de leitura real aprovadas; conexão ao workflow RAG permanece desligada.
  Após confirmação específica, criado substituto `RAG Local Lab MCP Read Only Replacement`
  com os mesmos três escopos/validade, em arquivo novo privado; antigo não revogado.
  Compose monta somente o substituto. Dois controles offline x54 passaram novamente
  no host e container na revisão final. Leitura real ainda apresentou 401, inclusive
  diagnóstico iniciado 79s após salvar o novo token (além do minuto indicado pela UI).
  Header Basic esperado verificado privadamente. Troca não resolveu a falha;
  não há evidência suficiente para atribuí-la à chave anterior ou certificar MCP.
  Não criar sucessivas credenciais, ampliar escopos ou trocar organização às cegas.
- GitHub MCP e Vertex continuam desligados. Nenhuma ativação de faturamento,
  cartão, upgrade ou fallback pago foi feita para resolver a falha.

Provas atuais: `proof/gemini-replacement-key-masked.jpg`,
`proof/atlassian-readonly-token-created.jpg`,
`proof/atlassian-mcp-api-token-disabled.jpg` (histórico),
`proof/atlassian-mcp-api-token-enabled.jpg` e logs redigidos em `app/`.
Substituto: `proof/atlassian-replacement-token-created.jpg` e
`proof/atlassian-replacement-token-scopes.jpg`. Resumo:
`eval/runs/atlassian-replacement-20261001/receipt.json`.
Não abrir nem copiar o arquivo de segredo para logs, prompts ou evidências.

## Histórico: cadastro após aceite pessoal em 30/09/2026

- Gemini Developer API: projeto `gen-lang-client-0580698701`, Free Tier,
  sem faturamento configurado. Chave padrão já existente, não copiada/persistida
  pela execução: autorização de armazenamento privado local ainda pendente.
- Jira: https://rag-local-lab-mateus.atlassian.net, espaço `RAG Local Lab`, `KAN`.
  Site criado pelo fluxo normal. O cadastro Free disparou trial Premium de 30 dias
  com estimativa USD 17.12; executado downgrade e relida assinatura após reload:
  **Free, 1 user, payment None**. Nenhum cartão ou pagamento adicionado.
- `cloudId` observado na administração do site:
  `15445c1f-6463-4ece-bdb1-eecd7c4d5968`. Não equivale a autorização OAuth da app.
- Conector legado Atlassian do Codex ainda retornou `[]`; sessão de navegador
  autenticada NÃO foi exportada como credencial para Python. MCP da app pendente.
- `gemini-2.5-flash-lite` tem Standard gratuito na tabela oficial consultada;
  modelo apenas planejado, nenhuma chamada de inferência/ADK remoto executada.
  Gemini Developer API não é Vertex AI. Vertex pago continua desativado.
- Metadados em `lab-profiles.json` não ligam adaptadores nem implementam gate de
  orçamento. Gitignore criado para `.local` e envs privados antes de qualquer
  futura gravação de chave. Nenhum segredo foi salvo nesta etapa.

Provas: `proof/jira-free-confirmed.jpg`, `proof/jira-lab-board-created.jpg`,
`proof/gemini-no-billing-confirmed.jpg`, `proof/gemini-existing-key-masked.jpg`.
Não há garantia sobre custos de outros projetos preexistentes ou alterações que
o usuário venha a fazer nos provedores. Estratégia deste laboratório: USD 0,
sem billing, sem upgrade ou fallback pago, somente dados sintéticos.

Os registros de descoberta abaixo são históricos anteriores a esse cadastro.

Consulta de descoberta em 30/09/2026: o conector Atlassian disponível no Codex
retornou lista vazia de recursos acessíveis. Não foram lidos tickets/documentos.
Não foi encontrada ferramenta GitHub conectada nesta sessão. Isso não prova que
o usuário não tenha contas; só que não temos aqui um site/repo autenticado de
laboratório. Credenciais de conectores do Codex não são exportadas ao aplicativo.

Descoberta Google Cloud no host: `gcloud` não encontrado; ADC e configuração
default não encontrados em `%APPDATA%\gcloud`. Só foram verificadas presença de
CLI/arquivos, sem ler ou imprimir credenciais. Isso não exclui uma conta existente
no navegador ou outra configuração customizada; ainda precisamos identificá-la.

## GitHub

Em 01/10/2026 foi criado `Cabraiz/rag-mcp-lab` como repositório **privado**,
id `1399100401`, branch `main`, inicializado com README de laboratório.
Visibilidade confirmada na página GitHub e pelo metadata do conector Codex.
Nenhum código local, segredo ou dado pessoal foi enviado. Sem convite de
colaboradores ou ativação de serviços pagos.

Esse é agora o destino do laboratório. `google/adk-python` era apenas referência
pública histórica, não a fonte privada que será conectada ao RAG. A tela local
foi atualizada para abrir o repositório criado sem fingir que o MCP está ativo.

O MCP hospedado permanece desligado até credencial de leitura própria do
backend; login no navegador e metadata lido pelo Codex não certificam o MCP
da aplicação. PAT fine-grained planejado: somente este repositório entre os
privados, Contents/Metadata read, 30 dias. A concessão precisa de confirmação
específica. Tokens fine-grained também permitem leitura de repositórios públicos
por padrão; o gateway da app deve restringir o destino a Cabraiz/rag-mcp-lab.
Endpoint planejado: `https://api.githubcopilot.com/mcp/x/repos/readonly`;
tools allowlist mínima `get_file_contents`, owner/repo fixos, timeouts e logs
sem credenciais. Token, leitura MCP autenticada e ingestão ainda pendentes.

Não gerar token amplo, não colocar credenciais em prompts/logs/arquivos versionados
e não cadastrar uma conta pelo usuário. Gateway precisa restringir repo e tools,
além do header de leitura do servidor.

Descoberta atual: conector GitHub disponível no Codex. Isso não entrega credenciais
ao backend nem comprova conexão ao servidor MCP remoto da aplicação.

## Atlassian

Informar o site real, por exemplo o domínio do seu laboratório em `atlassian.net`,
e completar o fluxo de autenticação na própria conta. O domínio de exemplo NÃO é
uma conta existente nem uma configuração válida. Usar somente Jira/Confluence
sintéticos e tools de leitura; OAuth respeita permissões já existentes, mas não
substitui o gateway e a reautorização do nosso aplicativo.

Guia oficial: https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/

## Vertex/Gemini

Precisamos de projeto pertencente ao usuário, faturamento/configuração aprovada,
API habilitada, identidade com permissão mínima e um modelo/região compatíveis.
Não há project ID default que possa ser inventado. Região e modelo ficam sem
default por enquanto: dados, disponibilidade do modelo e custo precisam orientar
a seleção. Não usar endpoint global se o contrato exigir localização regional.
O orçamento, teto de tokens e autorização de chamadas devem preceder qualquer
teste pago; alertas de orçamento não equivalem a um hard cap do aplicativo.

Localizações oficiais: https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/locations

## Critério de prova

Dois passes locais de contratos não viram dois passes remotos. Sem essas
configurações, testes de credenciais reais, escopos, revogação, 429 e custos
permanecem pendentes. Não tentar contornar autenticação por backdoor.
