"""Backup and restore of the API database (SQLite), with the API running for the backup.

    python -m api.backup --saida backup.db      # online copy of DB_PATH, into DB_PATH's folder (/state)
    python -m api.backup --entrada backup.db    # restore it into DB_PATH (API stopped)

The copy uses SQLite's backup API: consistent while the API writes, recent commits in the write-ahead log
included, in one file. It holds the exam lists encrypted, never the key (DB_ENCRYPTION_KEY or the api-key
volume), which is backed up apart. A restore first decrypts every row with the current key, so a wrong key
stops it with the API's own message and nothing is restored. The steps are in docs/como-rodar.md.
"""
import argparse
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from api.crypto import KEY_VARIABLE, CryptoError, decrypt, load_key
from api.main import DEFAULT_DB_PATH, DEFAULT_KEY_FILE, connect, row_fields


class BackupError(Exception):
    """A fixed message for the operator; it never contains the key or the data."""


def count_appointments(connection: sqlite3.Connection) -> int:
    return connection.execute('SELECT COUNT(*) FROM appointments').fetchone()[0]


def backup(db_path: Path, target: Path) -> int:
    """Copy the database at `db_path` to `target` (a new file); returns how many appointments it holds."""
    if not db_path.is_file():
        raise BackupError(f'banco não encontrado em {db_path}')
    if target.exists():
        raise BackupError(f'{target} já existe: escolha outro nome, a cópia nunca sobrescreve um arquivo')
    if not target.parent.is_dir():
        raise BackupError(f'a pasta {target.parent} não existe; use só um nome de arquivo (ex.: backup.db)')
    try:
        with closing(connect(db_path)) as source, closing(sqlite3.connect(target)) as copy:
            source.backup(copy)  # page by page, under SQLite's own locks: a consistent snapshot
            copy.execute('PRAGMA journal_mode = DELETE')  # one file, no -wal beside it
            if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('a cópia não passou no integrity_check do SQLite')
            appointments = count_appointments(copy)
    except BackupError:
        target.unlink(missing_ok=True)  # no half copy left behind
        raise
    except (sqlite3.Error, OSError) as error:
        target.unlink(missing_ok=True)
        raise BackupError(f'cópia falhou: {error}') from None
    return appointments


def current_key(key_file: Path) -> AESGCM:
    """The key the API would use: DB_ENCRYPTION_KEY, else the file in its volume; never a new one."""
    value = os.environ.get(KEY_VARIABLE, '')
    if not value and key_file.is_file():
        value = key_file.read_text(encoding='ascii', errors='replace').strip()
    if not value:
        raise CryptoError(f'chave do banco não encontrada ({KEY_VARIABLE} vazia e nenhum arquivo em {key_file}): '
                          'restaure primeiro a chave da gravação')
    return load_key(value)


def restore(source: Path, db_path: Path, cipher: AESGCM, replace: bool = False) -> int:
    """Copy `source` into `db_path` once every row decrypts with `cipher`; appointments there need `replace`."""
    if not source.is_file():
        raise BackupError(f'cópia não encontrada em {source}')
    try:
        with closing(sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True)) as backup_db:
            if backup_db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError(f'{source} não passou no integrity_check do SQLite')
            rows = backup_db.execute('SELECT id, status, exams, created_at FROM appointments').fetchall()
            for appointment_id, status, exams, created_at in rows:  # CryptoError: wrong key or edited row
                decrypt(cipher, exams, row_fields(appointment_id, status, created_at))
            if db_path.exists() and not replace:
                with closing(connect(db_path)) as current:
                    present = current.execute("SELECT name FROM sqlite_master WHERE name = 'appointments'").fetchone()
                    if present and count_appointments(current):
                        raise BackupError(f'{db_path} já tem agendamentos: para trocá-los pela cópia, '
                                          'pare a API e use --substituir')
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(connect(db_path)) as target:
                backup_db.backup(target)
                target.execute('PRAGMA journal_mode = WAL')  # as the API runs it (api/main.py init_db)
    except sqlite3.Error as error:
        raise BackupError(f'restauração falhou: {error}') from None
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m api.backup', description='Cópia e restauração do banco (DB_PATH).')
    action = parser.add_mutually_exclusive_group(required=True)
    # A relative name is in DB_PATH's folder: no absolute path, which Git Bash rewrites (C:/Program Files/Git/...).
    action.add_argument('--saida', type=Path, help='grava uma cópia consistente do banco neste arquivo novo')
    action.add_argument('--entrada', type=Path, help='restaura esta cópia no banco (com a API parada)')
    parser.add_argument('--substituir', action='store_true',
                        help='com --entrada: troca um banco que já tem agendamentos pela cópia')
    args = parser.parse_args(argv)
    db_path = Path(os.environ.get('DB_PATH', DEFAULT_DB_PATH))
    file = db_path.parent / (args.saida or args.entrada)
    try:
        if args.saida:
            total = backup(db_path, file)
            print(f'cópia gravada em {file}: {total} agendamento(s), com os exames cifrados; '
                  'a chave do banco não vai na cópia (guarde-a à parte)')
        else:
            cipher = current_key(Path(os.environ.get('DB_KEY_FILE', DEFAULT_KEY_FILE)))
            total = restore(file, db_path, cipher, replace=args.substituir)
            print(f'banco restaurado em {db_path}: {total} agendamento(s), todos decifrados com a chave atual')
    except (BackupError, CryptoError) as error:
        print(f'Erro: {error}; nada foi {"copiado" if args.saida else "restaurado"}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
