# Ampliação da alimentação — contrato de laboratório

Pedido: ampliar substancialmente as regras de alimentação e popular a base local.
Fonte: eval/datasets/meal-policy-aurora-v1.json, empresa fictícia Aurora.
Preservar teto base R$45, prazo existente 12 dias úteis e todos os documentos
anteriores. Período do teto (dia/refeição) pendente de escolha explícita; não
inventar diária, número de refeições ou aprovação financeira.

Publicar 20 regras pequenas por API autenticada de Bruno, com CAS de geração,
sem apagar/revogar documentos, modificar limites da aplicação ou chamar Gemini
durante a ingestão. Corpus e embeddings locais; somente consultas sintéticas
de teste usam o Gemini Free já autorizado. Nenhum billing/Vertex/ação MCP.

Critérios: dataset válido/versionado, publicação aditiva, documentos/hash/citações
canônicos, consultas reais fundamentadas, negativos (preço sem fonte/injeção),
duas rodadas consecutivas. Fonte correta importa mais que apenas ter citação.
Quotas/falhas não contam como pass; sem fallback pago ou reset do contador.
Esses documentos instruem o RAG: não implementam pagamento, cálculo de calendário,
deduplicação financeira nem motor de aprovação automática. Não são normas legais.

Rodadas do mesmo executor são regressões adversariais delimitadas, não auditoria
cega independente. Alterações reiniciam a sequência limpa. App continua ligada.

## Entrega verificada em 01/10/2026

- Publicação aditiva: 20 novos documentos, 30 no total; os 10 anteriores foram
  preservados por hash. Base e vetores reais conferidos pela API do laboratório.
- Prova: `eval/runs/meal-policy-20261001T073758Z/publication.json`.
- Duas rodadas consecutivas finais: 22 cenários / 234 checks por rodada.
  Recibo: `eval/runs/meal-policy-20261001T073758Z/checks-081040.json`.
  API, PostgreSQL, Qdrant, grafo ADK e Gemini reais; fontes congeladas por SHA.
- Cobertura: elegibilidade, itens permitidos/proibidos, valor efetivamente pago,
  comprovantes, falta de nota, prazo, duplicidade, hotel, rateio, taxa/gorjeta,
  delivery, descontos/estornos, cartão corporativo, exceções, dieta, exemplos
  condicionais, período desconhecido, câmbio, preço ausente e injeção R$999.
  Cliente não publica; operador não lê pedidos da cliente.
- Bugs locais corrigidos: margem de abstenção usada na recuperação de candidatos
  antes do Gemini (BUG-057); conta coletiva confundida com conta bancária
  (BUG-058); procedimento de exceção exigindo valor numérico (BUG-059).
  Limiares .3/.05 e dataset de calibração preservados; regressões dos filtros,
  validação do embedding e calibração também foram repetidas.
- Tentativas interrompidas por quota, timeout e ServerError estão preservadas,
  não contam como aprovação. QA final espaçado em pelo menos 8s entre pedidos;
  isso não representa a quota oficial nem uma garantia de capacidade.
  Não houve retry automático de pedido com resultado desconhecido, reset de
  contador, aumento de timeout, alteração de faturamento ou fallback pago.
- O período dos R$45 ainda está explicitamente pendente. Não é possível
  determinar um direito individual definitivo sem período e histórico.
- Evidência visual: `eval/runs/meal-policy-20261001T073758Z/meal-expanded-ui.png`.
  Nenhuma certificação de produção, carga 100.000 ou execução financeira.

Runtime preservado com a skill persistent-local-server: supervisor Docker
existente, restart unless-stopped, projeto rag-local-v2 em D:\RAG-Local\app.
URL: http://127.0.0.1:8840/#consultar. Os cinco arquivos Compose e o comando
de inspeção estão em `docs/architecture/gemini-rag-runtime.md`.
Logs desta atualização: `build-policy.log`, `up-policy.log`, `build-process.log`
e `up-process.log` na pasta de provas acima; nunca exibir chaves ou traces SDK.
