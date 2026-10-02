# Conectar Gemini ao RAG — contrato de 01/10/2026

Esperado: frontend -> API/ledger -> worker ADK -> recuperação local -> evidências
autorizadas -> Gemini Developer API Free -> verificação -> resposta/citações ou
abstenção. Não anunciar conexão sem uma resposta real atravessar esse caminho.

Antes de qualquer chamada: projeto gen-lang-client-0580698701 confirmado Free no
AI Studio e sem conta vinculada no Console Cloud. Custo autorizado R$0. Nenhuma
alteração de billing, Vertex, plano, grounding pago ou fallback. Somente corpus
sintético autorizado neste laboratório; segredos somente no backend privado.

Antes de modificar a geração: investigar erro remoto 400 com diagnóstico
sanitizado. Usar contador persistente existente, até 1.000 tentativas por dia UTC,
sem reset. Uma chamada por execução, sem retry automático. Não abrir endpoint
externo livre nem retornar erro bruto do provedor ao navegador.

Casos mínimos: consulta de alimentação com 45 reais e citação; pergunta sem
evidência; preço de abobrinha não pode virar estacionamento; instrução para inventar
999 não pode substituir 45; conteúdo retornado por modelo não autoriza ferramentas;
espaços rejeitados; fonte/ACL revogada no commit deve causar abstenção; timeout/
quota deve produzir resposta segura e não loop de chamadas. Tests determinísticos
e smoke live separados. Dois passes não certificam ausência de alucinação/produção.
