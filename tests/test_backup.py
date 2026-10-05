"""Backup and restore of the API database (api/backup.py), read back through the API itself."""
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.crypto import new_key
from tests.test_api import configure

ORDERS = [{'exams': [{'code': 'FICT-001', 'name': 'hemograma'}]},
          {'exams': [{'code': 'FICT-001', 'name': 'a'}, {'code': 'FICT-002', 'name': 'b'}]},
          {'exams': [{'code': 'FICT-002', 'name': 'glicemia'}]}]


def backup_module():
    import api.backup
    return api.backup


@pytest.fixture
def saved(tmp_path, monkeypatch, capsys):
    """Three appointments and an online backup of their database, taken while the API runs."""
    app = configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        created = [client.post('/appointments', json=order).json() for order in ORDERS]
        # The API keeps a connection open, so the commits are still in the write-ahead log:
        # a copy of the .db file alone would not have them.
        assert (tmp_path / 'appointments.db-wal').stat().st_size > 0
        copy = tmp_path / 'copia' / 'backup.db'
        copy.parent.mkdir()
        assert backup_module().main(['--saida', str(copy)]) == 0
    assert capsys.readouterr().out.startswith(f'cópia gravada em {copy}: 3 agendamento(s)')
    return created, copy


def test_a_restored_backup_reads_back_decrypted_through_the_api(saved, tmp_path, monkeypatch, capsys):
    created, copy = saved
    assert not copy.with_name('backup.db-wal').exists()  # one self-contained file
    with sqlite3.connect(copy) as connection:
        stored = b''.join(str(row).encode() for row in connection.execute('SELECT * FROM appointments'))
    assert len(created) == 3 and b'FICT' not in stored and b'Hemograma' not in stored  # still encrypted
    fresh = tmp_path / 'restaurado' / 'appointments.db'
    monkeypatch.setenv('DB_PATH', str(fresh))  # same key (DB_ENCRYPTION_KEY), a database that does not exist
    assert backup_module().main(['--entrada', str(copy)]) == 0
    assert capsys.readouterr().out.startswith(f'banco restaurado em {fresh}: 3 agendamento(s)')
    with TestClient(api_app()) as client:  # the same key, the restored database
        assert client.app.state.db_path == fresh
        for appointment in created:
            assert client.get(f"/appointments/{appointment['id']}").json() == appointment
        assert client.post('/appointments', json=ORDERS[0]).status_code == 201  # and it takes new ones


def test_a_restore_with_another_key_fails_clearly_and_restores_nothing(saved, tmp_path, monkeypatch, capsys):
    _, copy = saved
    fresh = tmp_path / 'restaurado' / 'appointments.db'
    monkeypatch.setenv('DB_PATH', str(fresh))
    other = new_key()
    monkeypatch.setenv('DB_ENCRYPTION_KEY', other)
    assert backup_module().main(['--entrada', str(copy)]) == 2
    error = capsys.readouterr().err
    assert error == ('Erro: não foi possível decifrar o registro: a chave não é a da gravação ou o dado foi '
                     'alterado no banco; nada foi restaurado\n')
    assert other not in error and not fresh.exists()


def test_a_restore_without_any_key_does_not_create_one(saved, tmp_path, monkeypatch, capsys):
    _, copy = saved
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'restaurado' / 'appointments.db'))
    monkeypatch.setenv('DB_ENCRYPTION_KEY', '')
    monkeypatch.setenv('DB_KEY_FILE', str(tmp_path / 'keys' / 'db.key'))
    assert backup_module().main(['--entrada', str(copy)]) == 2
    assert 'restaure primeiro a chave da gravação' in capsys.readouterr().err
    assert not (tmp_path / 'keys').exists() and not (tmp_path / 'restaurado').exists()


def test_the_key_file_of_the_volume_is_used_when_the_variable_is_empty(saved, tmp_path, monkeypatch):
    _, copy = saved
    key_file = tmp_path / 'keys' / 'db.key'
    key_file.parent.mkdir()
    key_file.write_text(os.environ['DB_ENCRYPTION_KEY'] + '\n', encoding='ascii')
    monkeypatch.setenv('DB_ENCRYPTION_KEY', '')
    monkeypatch.setenv('DB_KEY_FILE', str(key_file))
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'restaurado' / 'appointments.db'))
    assert backup_module().main(['--entrada', str(copy)]) == 0


def test_a_database_with_appointments_is_only_replaced_on_request(saved, tmp_path, monkeypatch, capsys):
    created, copy = saved
    with TestClient(api_app()) as client:  # DB_PATH: the original database, 3 appointments
        extra = client.post('/appointments', json=ORDERS[0]).json()
    assert backup_module().main(['--entrada', str(copy)]) == 2
    assert '--substituir' in capsys.readouterr().err
    with TestClient(api_app()) as client:
        assert client.get(f"/appointments/{extra['id']}").status_code == 200  # untouched
    assert backup_module().main(['--entrada', str(copy), '--substituir']) == 0
    with TestClient(api_app()) as client:
        assert client.get(f"/appointments/{extra['id']}").status_code == 404  # back to the copy
        assert client.get(f"/appointments/{created[0]['id']}").json() == created[0]


def test_a_backup_never_overwrites_a_file_and_needs_a_database(saved, tmp_path, monkeypatch, capsys):
    _, copy = saved
    before = copy.read_bytes()
    assert backup_module().main(['--saida', str(copy)]) == 2
    assert 'já existe' in capsys.readouterr().err and copy.read_bytes() == before
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'nao-existe.db'))
    assert backup_module().main(['--saida', str(tmp_path / 'outra.db')]) == 2
    assert 'banco não encontrado' in capsys.readouterr().err and not (tmp_path / 'outra.db').exists()


def test_a_damaged_copy_is_refused(saved, tmp_path, monkeypatch, capsys):
    _, copy = saved
    copy.write_bytes(b'nao e um banco SQLite' * 100)
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'restaurado' / 'appointments.db'))
    assert backup_module().main(['--entrada', str(copy)]) == 2
    assert capsys.readouterr().err.startswith('Erro: restauração falhou:')
    assert not (tmp_path / 'restaurado').exists()


def api_app():
    import api.main
    return api.main.app


def test_a_bare_file_name_is_in_the_database_folder_both_ways(tmp_path, monkeypatch, capsys):
    """The guide's commands name only a file (backup.db): no absolute path for Git Bash to rewrite."""
    app = configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        created = client.post('/appointments', json=ORDERS[0]).json()
        assert backup_module().main(['--saida', 'backup.db']) == 0
    assert capsys.readouterr().out.startswith(f"cópia gravada em {tmp_path / 'backup.db'}: 1 agendamento(s)")
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'appointments.db'))
    (tmp_path / 'appointments.db').unlink()
    assert backup_module().main(['--entrada', 'backup.db']) == 0
    with TestClient(api_app()) as client:
        assert client.get(f"/appointments/{created['id']}").json() == created


def test_a_folder_that_does_not_exist_is_named_instead_of_sqlites_error(tmp_path, monkeypatch, capsys):
    """What Git Bash makes of /state/backup.db: the message names the folder, not "unable to open database"."""
    app = configure(tmp_path, monkeypatch)
    with TestClient(app):
        assert backup_module().main(['--saida', 'C:/Program Files/Git/state/backup.db']) == 2
    error = capsys.readouterr().err
    assert error == (f"Erro: a pasta {tmp_path / 'C:/Program Files/Git/state'} não existe; use só um nome de arquivo "
                     '(ex.: backup.db); nada foi copiado\n')
    assert 'unable to open' not in error and not list(tmp_path.glob('**/backup.db'))
