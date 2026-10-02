# Fila de cards

O registro canônico está em `.local/card-execution/queue.sqlite3`, com projeção legível em `.local/card-execution/progress.json`. A reorganização não apaga cards, muda conclusões sem prova nem converte bloqueios em sucesso.

Abra [resultados e bloqueios](backlog-results-20261002.md) para a última consolidação anterior à organização. Os números desse relatório são datados; o estado atual deve ser consultado pela ferramenta:

```powershell
& D:\RAG-Local\adk\.venv\Scripts\python.exe D:\RAG-Local\app\manage.py cards status
```

O [plano de resolução](backlog-resolution-20261002.json) registra a sequência e os critérios. [cards.json](cards.json) é a definição inicial, não a fila viva. As provas antigas continuam preservadas; se um arquivo de validação mudar, a revisão deve registrar a necessidade de nova validação, não alterar o comprovante antigo.

A [reorganização de outubro](../organization/results-20261002.md) exige revalidar aprovações cujas fontes de suporte mudaram. `NEEDS_FIX` nessa revisão significa prova desatualizada, não necessariamente um bug novo: os novos testes devem distinguir as duas situações antes de fechar o card.
