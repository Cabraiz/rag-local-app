# Demonstração local populada — contrato

Esperado: página abre na empresa fictícia Aurora; oito documentos reais visíveis;
exemplos geram pedidos reais no Python/ADK/PostgreSQL/Qdrant; resposta literal com
fonte consultável; pergunta fora da base mostra abstenção; Horizonte tem orçamento
diferente e documentos isolados. Interface identifica dados fictícios e execução local.

Proibido: respostas estáticas como recuperação; cloud paga; segredos na interface;
exclusão de versões/histórico; sessão sintética como produção; garantia de zero bugs.

Menor prova: duas rodadas HTTP nos mesmos arquivos verificando catálogo autenticado,
escopo, oito fontes, orçamento 45/20, citação literal, abstenção e replay; inspeção
da UI e servidor persistente saudável. Mudanças reiniciam a contagem. São regressões
do autor, não auditoria cega independente. A fila maior permanece pausada.
