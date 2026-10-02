# Organização do projeto RAG

Esta alteração organiza o laboratório existente para facilitar a leitura pelo PO e a manutenção pela equipe. Não adiciona funcionalidades, não altera permissões e não publica a aplicação na internet.

Veja a [estrutura entregue e as verificações executadas](results-20261002.md).

## Critérios de aceitação

- A raiz de `app` deve conter apenas os pontos de entrada, a documentação breve e os arquivos de configuração que precisam ficar ali. Logs, imagens Docker e perfis Compose terão pastas próprias.
- Cada pasta de código, ferramentas, testes e documentação mantida por nós terá menos de 20 arquivos diretos, separados por responsabilidade. Dependências instaladas, pesos de modelos, dados privados e provas históricas são acervos, não pastas de trabalho do PO.
- Nenhum documento, segredo, pedido, banco, volume Docker ou comprovante antigo será apagado. Os movimentos terão origem, destino e SHA-256 registrados.
- Importações, caminhos relativos, builds, perfis Compose e ferramentas de cards devem funcionar nos novos endereços. O módulo público `rag_app` continuará compatível com os comandos dos containers.
- Serão executadas duas rodadas consecutivas de verificação da estrutura e dos caminhos, além de regressões e checks dos serviços afetados. Uma falha reinicia a sequência após a correção.
- A reorganização não transforma bloqueios de produção em cards concluídos nem usa comprovantes antigos como teste da nova estrutura.

## Provas necessárias

A menor prova da migração é um inventário com hashes antes e depois, nenhuma colisão ou arquivo perdido, compilação de todos os arquivos Python, imports dos módulos usados pelos containers, resolução dos perfis Compose e confirmação das dependências locais. Os serviços existentes serão conferidos por HTTP e pelos dados persistidos, sem chamadas pagas e sem apagar volumes.

## Áreas de leitura

O guia na raiz do projeto será a entrada para o PO. Código fica em `app/src`, operações em `app/tools`, testes em `app/tests`, infraestrutura em `app/infrastructure` e documentação em `docs`. Resultados antigos continuam em `eval/runs`; logs de execução ficam fora das pastas de código.

O registro da fila continua em `.local/card-execution`, independente dos arquivos que mudarem de endereço. Após a validação, a atividade volta a essa mesma fila, mantendo os bloqueios que ainda precisam de ambiente ou autorização externa.
