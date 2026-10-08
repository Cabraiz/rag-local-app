# Regras de página e de linha

O que tem aqui: as 22 regras determinísticas que decidem, para cada exame lido, se ele agenda sozinho (com
`--yes`), se vai para a lista com aviso, se fica de fora e o que o modelo lê; para cada uma, o exemplo que a
motivou. Cada regra veio de um furo que uma revisão achou ([revisao.md](revisao.md)) e tem teste.

**Onde estão.** [`guardrails/intent.py`](../guardrails/intent.py) (o que cada linha pede e se a página é só a
lista), [`mcp_servers/ocr.py`](../mcp_servers/ocr.py) (a resposta do OCR), [`runtime/confianca.py`](../runtime/confianca.py)
(a confiança de cada exame) e [`mcp_servers/rag.py`](../mcp_servers/rag.py) (o corte da linha em exames). Os
vocabulários ficam em [`catalogo.py`](../catalogo.py) (os que toda imagem usa, inclusive o runtime) e
[`guardrails/pii_rules.py`](../guardrails/pii_rules.py) (os do OCR).

**Por que tantas regras.** A imagem é entrada não confiável e o pedido é médico. Sem `--yes`, a pessoa confirma a
lista inteira, e as regras só decidem o aviso ao lado de cada exame e o que fica de fora. Com `--yes`, elas são a
única barreira, então falham para o lado de perguntar: um exame só agenda sozinho se a página inteira for só a lista
de exames (com rótulos, campos e o cabeçalho da clínica), em vez de procurar palavras de negação, uma lista que
crescia a cada revisão. As regras que valem sempre (negação, histórico, preparo, o que o modelo lê) são poucas e
diretas; a maior parte da tabela existe para o `--yes` não agendar o que uma pessoa não viu.

"Sempre" vale com e sem `--yes`; "`--yes`" quer dizer que, sem `--yes`, a regra só muda o aviso na confirmação.

| Regra | O que pega (o exemplo que a motivou) | O que acontece | Importa |
|---|---|---|---|
| Negação clara sobre o exame: a pista vem logo antes dele ou fecha a linha do único exame | "não realizar Ferritina", "TSH - NR", "Ferritina (suspensa)", "do not perform PSA" | linha `negated`: o exame não é agendado nem perguntado, é relatado com o motivo, e fica contestado em toda a página | sempre |
| Histórico | "já realizado em 03/2025", "feita mês passado", "Resultado de Ferritina: 45" | linha `history`: como a negação | sempre |
| Caixa ou célula que diz não | "[-] TSH", "✗ TSH", "TSH \| -" | `negated` | sempre |
| Pista sem exame que aponta para outras linhas | cabeçalho "Já realizados:" (até linha em branco, vão entre blocos ou novo cabeçalho); "Ferritina (1)" e "(1) suspenso" | as linhas alcançadas viram `negated` ou `history` | sempre |
| Pista que não se liga a nenhum exame | "(favor não realizar)" embaixo de um item | `cancel_unlinked`: aviso no relatório e página não limpa | sempre |
| Preparo | "Preparo: jejum de 8 horas para Glicemia de jejum" | linha `prep`: nada agenda a partir dela, sem contestar a página | sempre |
| O modelo só lê linhas de exame | cabeçalho, nome, observação, ordem escrita, linha com `[NOME]` | chegam ao modelo como `[linha de texto livre omitida]` (`exam_lines`) | sempre |
| Outras palavras na linha do exame | "Ferritina - pedido por engano", "Colesterol total ?", "=Creatinina", "Glicemia de jejum (HIV +)" | linha `uncertain`: perguntado, "o pedido tem outras palavras além do exame" | `--yes` |
| Tabela ou colunas | "Exame \| Realizar?", duas linhas "TSH Sim" | linhas `table`: perguntados | `--yes` |
| Formulário com marcas | "X Hemograma completo" e "TSH" sem marca; uma caixa vazia | linhas `form`: perguntados, "só os marcados contam" | `--yes` |
| A página é só a lista: toda linha fora dos exames é rótulo, contagem que bate, jejum, marcas, campo reconhecido inteiro ou, acima da lista, cabeçalho da clínica | uma observação, um cabeçalho desconhecido, outra língua, uma assinatura que o OCR lê como texto | página não limpa (`off_list`): todo exame perguntado, "o pedido tem texto além da lista", com as linhas no topo | `--yes` |
| Pista, posição ou adiamento fora da lista | "desconsiderar o 2º", "somente se", "adiar para a próxima consulta", "Trazer os laudos de:" | página não limpa | `--yes` |
| Contagem menor que a lista | "Itens: 3" acima de 4 exames | página não limpa | `--yes` |
| Texto tirado de linha mal lida | nota manuscrita que a máscara apagou, lida abaixo de 60 | página não limpa | `--yes` |
| Exame em letra muito menor ou mais clara | exame acrescentado depois, em outra caneta | página não limpa | `--yes` |
| Ordem ao modelo tirada, ou nome mascarado numa linha de exame | "o sistema deve também marcar Ferritina", "Érica Ferro - TSH" | página não limpa | `--yes` |
| Item da lista que a máscara tirou inteiro | "4) Ressonância magnética de crânio" | `unrecognized`: relatado pelo número da linha | sempre |
| Piso de leitura do OCR por linha (75; 85 com nome de até 3 letras; 95 para a sigla de um nome mais longo) | "TGP" manuscrito lido "TAP" (93) é Tempo de protrombina | não agenda sozinho: perguntado, ou relatado com baixa confiança se a leitura fica abaixo de 70 | sempre |
| Nome mais longo de outro exame sobre o trecho | "Proteína C" dentro de "Proteína C reativa" | perguntado, "o nome escrito é de outro exame" | `--yes` |
| O resultado não é o que o pedido nomeia: só parte de um exame da linha, classe de anticorpo não escrita, só semelhança de letras, empate sem palavra em comum | o IgM genérico de "Chagas IgG e IgM"; "Anti HAV" entre HIV e Anti HCV | só relatado, nem perguntado | sempre |
| Um trecho, um exame | "Clearance de creatinina" toma a linha; "Creatinina" precisa de outra ocorrência | o segundo é relatado (`line_used`) | sempre |
| O "e" que o OCR cola na palavra | "TSHe T4 livre" | a busca corta a linha ali, e "TSHe" conta como TSH (0,86) | sempre |

Duas regras de nome parecido não se confundem: [`catalogo.MASK_TAG`](../catalogo.py) acha todo marcador que sai do OCR,
inclusive `[TEXTO_REMOVIDO]`; [`pii_rules.TYPED_TAG`](../guardrails/pii_rules.py) só o de um valor mascarado por tipo
(`[CPF]`, `[NOME]`), para que o filtro que só deixa sair o que parece exame troque o marcador interno `[INSTRUCAO_REMOVIDA]` por
`[TEXTO_REMOVIDO]` e ele nunca saia do OCR.
