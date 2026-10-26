# Backup e restauração do banco

**O que tem aqui / quando ler.** Os passos para copiar o banco e a chave e para restaurar a cópia. Leia antes de
apagar volumes, trocar de máquina ou testar uma restauração. O que o banco guarda e como a lista é cifrada:
[referencia.md](referencia.md#segurança-em-detalhe).

**Índice:** [O que guardar](#o-que-guardar) · [Copiar a chave](#copiar-a-chave) · [Copiar o banco](#copiar-o-banco) ·
[Restaurar](#restaurar) · [Erros da restauração](#erros-da-restauração)

**Antes de começar:**

- O ambiente está de pé (`docker compose up -d --wait`, em [como-rodar.md](como-rodar.md#1-subir-o-ambiente-docker)).
- Os comandos rodam na raiz do clone, em PowerShell ou Git Bash.

## O que guardar

- **O banco** (SQLite) fica no volume `api-data`. A cópia leva os exames cifrados, nunca a chave.
- **A chave** que cifra as listas de exames fica no volume `api-key`, ou em `DB_ENCRYPTION_KEY` se você a definiu.
- **Guarde as duas separadas**, a chave num cofre de senhas. Quem tem as duas lê os dados.
- **A restauração precisa das duas.**

## Copiar a chave

Faça uma vez: a chave não muda. Se você definiu `DB_ENCRYPTION_KEY`, a chave é essa e não há o que copiar.

1. Copie a chave do volume:
   ```bash
   docker compose cp api:/keys/db.key ./db.key
   ```
2. Guarde o conteúdo longe da cópia do banco.
3. Apague o arquivo `db.key`.

## Copiar o banco

Com a API no ar:

1. Crie a pasta, uma vez: `mkdir backup`.
2. Gere a cópia dentro do container, traga-a para o host e apague a do container:
   ```bash
   docker compose exec api python -m api.backup --saida backup.db
   docker compose cp api:/state/backup.db ./backup/appointments.db
   docker compose exec api sh -c "rm /state/backup.db"
   ```
3. Confira a saída do primeiro comando:
   ```text
   cópia gravada em /state/backup.db: 3 agendamento(s), com os exames cifrados; a chave do banco não vai na cópia (guarde-a à parte)
   ```

Por que os comandos são assim:

- `--saida` recebe só um nome de arquivo. O arquivo vai para `/state`, o único lugar gravável da API, porque os
  containers são somente leitura.
- Um caminho começado por `/` seria reescrito pelo Git Bash. Por isso o nome vai sem pasta e o `rm` vai entre aspas.
- A cópia usa a API de backup do SQLite. Ela inclui os agendamentos ainda no WAL, que uma cópia só do `.db` perderia.
- `--saida` nunca sobrescreve um arquivo. O `.gitignore` já ignora `*.db` e `*.key`.

## Restaurar

Com a chave no lugar. Os comandos param a API antes de gravar e a sobem de novo no fim:

1. **A chave primeiro.** Com o volume `api-key` intacto, não há nada a fazer. Numa máquina nova, ponha a chave em
   `DB_ENCRYPTION_KEY=` no `.env`.
2. **Ponha a cópia no volume da API**, ainda com ela no ar:
   ```bash
   docker compose cp ./backup/appointments.db api:/state/restore.db
   ```
3. **Pare a API e restaure:**
   ```bash
   docker compose stop api
   docker compose run --rm --no-deps api python -m api.backup --entrada restore.db
   ```
4. **Suba a API e apague a cópia do volume:**
   ```bash
   docker compose up -d --wait
   docker compose exec api sh -c "rm /state/restore.db"
   ```
5. **Confira a saída** do `--entrada` (passo 3):
   ```text
   banco restaurado em /state/appointments.db: 3 agendamento(s), todos decifrados com a chave atual
   ```

Antes de gravar, a restauração roda o `integrity_check` do SQLite e decifra cada agendamento com a chave atual.

Um banco que já tem dados só é trocado com `--substituir` no fim do comando. O que entrou depois da cópia se perde,
inclusive as `Idempotency-Key`.

## Erros da restauração

Em todos, nada é restaurado.

| Mensagem | Causa | O que fazer |
|---|---|---|
| `Erro: chave do banco não encontrada (…): restaure primeiro a chave da gravação; nada foi restaurado` | Não há chave; a restauração não cria outra | Faça o passo 1 de [Restaurar](#restaurar) |
| `Erro: não foi possível decifrar o registro: a chave não é a da gravação ou o dado foi alterado no banco; nada foi restaurado` | A chave não é a da cópia | Use a chave guardada com esta cópia |
| `Erro: /state/appointments.db está em uso pela API: pare-a (docker compose stop api); nada foi restaurado` | A API está no ar e mantém o banco aberto | `docker compose stop api` |
| `use só um nome de arquivo, sem pasta nem caminho (ex.: backup.db)` | `--saida` ou `--entrada` com pasta ou caminho | Passe só o nome |

Testes: [`tests/test_backup.py`](../tests/test_backup.py).
