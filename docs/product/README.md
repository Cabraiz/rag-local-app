# Guia do PO

O produto demonstra como consultar documentos de uma empresa com fontes verificáveis. Ana é o perfil de consulta; Bruno é o operador que administra a base e acompanha integrações. A troca de perfil é demonstrativa, não autenticação corporativa.

## Caminho de uma consulta

O frontend envia a pergunta ao backend. O PostgreSQL confirma o pedido antes do aceite. Workers executam o workflow ADK, recuperam evidências com embeddings locais e Qdrant e consultam o Gemini quando o modo gratuito autorizado está disponível. A resposta publicada usa um trecho canônico com citação; evidência insuficiente ou falha do provedor resulta em abstenção.

RabbitMQ distribui trabalho e Redis é cache opcional. O PostgreSQL continua sendo a fonte da verdade. Grafana mostra métricas e o processamento permanece rastreável após falhas de componentes nos casos já testados.

## Onde olhar

- Produto: <http://127.0.0.1:8840/>.
- Operação: <http://127.0.0.1:8850/d/rag-overview/rag-local>.
- Pendências: [fila e bloqueios](../cards/README.md).
- Desenho e limites: [arquitetura](../architecture/README.md).
- Validações: [qualidade](../../eval/README.md).

## Limites importantes

Criar ou mover cards não está liberado no workflow de respostas; as integrações MCP atuais são somente leitura. O laboratório também não comprova produção pública, 100 mil requests, identidade corporativa, recuperação em outro computador ou Vertex AI gratuito. Esses pontos continuam nos cards, sem serem escondidos pela organização das pastas.
