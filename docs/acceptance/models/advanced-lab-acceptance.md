# DeepAgents e avaliacao: contrato do laboratorio

Esperado: SDK DeepAgents real, modelo Gemini Developer API fixo e sem Vertex,
tarefa de comparacao de duas evidencias sinteticas autorizadas, ferramentas de
somente leitura explicitamente restritas, checkpoint local privado, cancelamento
e limites de chamadas, passos, bytes e tempo. Adaptador separado pelo TaskPort:
nao deve ser acionado em toda pergunta. Nao e agente de escrita Jira/GitHub.

DeepEval deve usar modelo explicito, nunca o judge OpenAI pago padrao. Dataset
versionado separa calibracao e holdout; respostas esperadas nao sao enviadas ao
judge. Resultado de judge nao concede permissoes nem substitui SQL/citacoes.

Proibido: novos tokens, billing, troca de modelo/fallback pago, ferramentas de
filesystem/shell/rede livre, exportar logs e prompts a plataformas de tracing,
alterar calibração com holdout ou tratar SDK instalado como teste aprovado.

Menor prova: duas rodadas reais por SDK com fontes, dataset, imagem e oraculo
congelados. Testes negativos verificam ferramentas, budgets, cancelamento e
persistencia. Sem recibo completo o card permanece aberto. O experimento
isolado nao certifica autenticacao de producao, Vertex, HA ou carga.
