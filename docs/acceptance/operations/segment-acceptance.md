# Revisão por casos de uso — contrato antes das alterações

Pedido: revisar os seis temas, criar cenários e repetir teste → correção → reteste
até duas rodadas consecutivas aprovadas, sem alterar o escopo nem acessar produção.

Esperado: catálogo com sucesso, fronteiras, falhas, abuso, concorrência e recuperação;
cada caso tem resultado esperado, comportamento proibido e menor prova. Casos reais
usam a aplicação/container existentes; casos sem implementação ficam PENDING, nunca
PASS. Uma falha ou mudança de código/contrato zera a sequência de aprovações.

Proibido: chamar modelo pago, autenticar MCP/contas reais, habilitar egress, conceder
permissões, fazer deploy, apagar dados anteriores ou alegar cobertura de todas as
possibilidades. Não criar teste que apenas copie a regra que deveria verificar.

Menor prova de correção: primeiro guardar a reprodução falhando; depois executar
duas rodadas com entradas novas, código/imagens congelados e oráculos previamente
fixados. Conservar os receipts, hashes, IDs sintéticos e resultados por caso.

## Significado de teste cego nesta execução

Não há auditor independente. O protocolo é **adversarial com entradas sorteadas após
congelar os oráculos**, não uma auditoria cega independente. Embaralhar a ordem não
cria independência. As duas rodadas exercitam entradas e falhas diferentes, mas
podem compartilhar pontos cegos do mesmo autor.

Aprovação local: todos os testes reais selecionados passam duas vezes sem alteração
intermediária. Aprovação global: TODOS os casos comportamentais do catálogo têm
evidência atual de implementação real, os gates de qualidade/carga/restore passam,
e não há PENDING. Checks de completude documental não contam como execução desses
comportamentos. Esta execução não autoriza Gate B nem produção.

## Recorte de carga

100.000 usuários, 100.000 pedidos totais e 100.000 simultâneos não são o mesmo teste.
O caso de carga exige definir RPS, concorrência, distribuição por tenant, contexto,
tokens, SLO e duração. Conferir oferecidos = aceitos únicos + rejeitados; aceitos =
terminais + pendentes duráveis, além de ausência de ACK sem commit. Simulações
históricas não certificam esse workload em HTTP/PostgreSQL/Qdrant/modelos reais.

## Fixtures e segurança

Os testes recusam um Compose já ativo; iniciam apenas o projeto conhecido, usam
identidades/demo e perguntas sintéticas, injetam falhas somente nos IDs que criaram,
e encerram os containers que iniciaram. Mantêm volumes e receipts. Não entregam
servidor permanente e não dependem de autenticação corporativa.
