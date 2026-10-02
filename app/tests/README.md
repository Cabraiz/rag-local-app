# Testes do RAG

Os testes são separados por área: consulta RAG, documentos, semântica, assistente, Gemini, MCPs, segurança, fila, resiliência e observabilidade. `shared` contém helpers comuns e `receipts` contém ferramentas que derivam comprovantes de testes realmente executados.

Localize ou execute por nome, sem precisar memorizar o caminho:

```powershell
& D:\RAG-Local\adk\.venv\Scripts\python.exe D:\RAG-Local\app\manage.py locate card_queue_fixture.py
& D:\RAG-Local\adk\.venv\Scripts\python.exe D:\RAG-Local\app\manage.py test card_queue_fixture.py
```

Antes de executar uma fixture, leia sua descrição: as integrações usam projetos QA exclusivos e algumas provas reais precisam de credenciais ou cota do modelo. Não executar um teste não equivale a aprovação. Resultados ficam em [eval/runs](../../eval/runs), fora do código.
