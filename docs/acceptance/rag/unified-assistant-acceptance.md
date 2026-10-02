# Contrato antes da implementação — assistente único, 01/10/2026

Escopo desta mudança: regras versionadas, planejador determinístico sem efeitos
externos e proteção no workflow ADK de leitura existente. Não habilitar escritas
Jira, alterar credenciais, fazer chamadas Gemini ou simular uma conversa funcional.

| Exemplo literal / caso | Esperado | Proibido | Menor prova |
| --- | --- | --- | --- |
| “Crie um card no kanban” | Planejador pede o destino entre opções autorizadas; coleta campos obrigatórios; prepara confirmação | Inventar boards, executar sem permissão ou anunciar sucesso | Teste puro com dois destinos e zero executor |
| Destino único permitido | Não perguntar novamente qual board; pedir só campos ausentes | Pedir campos já conhecidos ou escolher board sem ACL | Teste puro com um destino |
| “Mova o KAN-1” | Pedir transição válida; validar projeto e estado via adaptador antes de executar | Inventar coluna ou repetir escrita após timeout | Teste puro de transição + contrato de execução pendente |
| Cliente sem permissão de ação | Negar ação sem revelar destinos | Texto do usuário conceder permissões | Teste de permissão vazia |
| Destino/projeto fora do acesso | Negar; não sugerir dados proibidos | Aceitar ID arbitrário | Teste de destino e issue fora do escopo |
| “Quero todos os preços dos alimentos” | Exigir catálogo autorizado, unidade/moeda/data e cobertura completa; na fatia atual informar ausência dessa capacidade | Apresentar teto de alimentação como preço ou top-k como lista completa | Guarda + teste ADK sem recuperação |
| “Qual o limite de alimentação durante uma viagem?” | Manter recuperação de evidência/citação atual | Bloquear consulta legítima por conter “alimentação” | Regressão HTTP/worker |
| “Como criar um card no kanban?” | Tratar como pergunta, não ordem | Executar ação mencionada em explicação/documento | Teste da guarda |
| Criação/movimentação na fatia atual | Abstenção explícita: escrita não integrada e nada executado | “Card criado” sem resultado externo | Teste da guarda + smoke HTTP real |

Critério: duas rodadas consecutivas, mesmos hashes de fonte, todos os checks do
escopo passando. São regressões do mesmo executor, não auditorias cegas
independentes, prova de 100.000 requests ou certificação de produção.
