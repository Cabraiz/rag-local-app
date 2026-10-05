"""Backup and restore of the API database (api/backup.py), read back through the API itself."""
import os
import shutil
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
        assert backup_module().main(['--saida', 'backup.db']) == 0  # a bare name, in DB_PATH's folder
    copy = tmp_path / 'backup.db'
    assert capsys.readouterr().out.startswith(f'cópia gravada em {copy}: 3 agendamento(s)')
    return created, copy


def restore_into(folder, copy, monkeypatch, *flags):
    """DB_PATH in `folder`, with the copy beside it, restored by its bare name."""
    folder.mkdir(exist_ok=True)
    shutil.copy(copy, folder / 'backup.db')
    monkeypatch.setenv('DB_PATH', str(folder / 'appointments.db'))
    return backup_module().main(['--entrada', 'backup.db', *flags])


def test_a_restored_backup_reads_back_decrypted_through_the_api(saved, tmp_path, monkeypatch, capsys):
    created, copy = saved
    assert not copy.with_name('backup.db-wal').exists()  # one self-contained file
    with sqlite3.connect(copy) as connection:
        stored = b''.join(str(row).encode() for row in connection.execute('SELECT * FROM appointments'))
    assert len(created) == 3 and b'FICT' not in stored and b'Hemograma' not in stored  # still encrypted
    fresh = tmp_path / 'restaurado' / 'appointments.db'  # same key (DB_ENCRYPTION_KEY), a database that does not exist
    assert restore_into(fresh.parent, copy, monkeypatch) == 0
    assert capsys.readouterr().out.startswith(f'banco restaurado em {fresh}: 3 agendamento(s)')
    with TestClient(api_app()) as client:  # the same key, the restored database
        assert client.app.state.db_path == fresh
        for appointment in created:
            assert client.get(f"/appointments/{appointment['id']}").json() == appointment
        assert client.post('/appointments', json=ORDERS[0]).status_code == 201  # and it takes new ones


def test_a_restore_with_another_key_fails_clearly_and_restores_nothing(saved, tmp_path, monkeypatch, capsys):
    _, copy = saved
    fresh = tmp_path / 'restaurado' / 'appointments.db'
    other = new_key()
    monkeypatch.setenv('DB_ENCRYPTION_KEY', other)
    assert restore_into(fresh.parent, copy, monkeypatch) == 2
    error = capsys.readouterr().err
    assert error == ('Erro: não foi possível decifrar o registro: a chave não é a da gravação ou o dado foi '
                     'alterado no banco; nada foi restaurado\n')
    assert other not in error and not fresh.exists()


def test_a_restore_without_any_key_does_not_create_one(saved, tmp_path, monkeypatch, capsys):
    _, copy = saved
    monkeypatch.setenv('DB_ENCRYPTION_KEY', '')
    monkeypatch.setenv('DB_KEY_FILE', str(tmp_path / 'keys' / 'db.key'))
    assert restore_into(tmp_path / 'restaurado', copy, monkeypatch) == 2
    assert 'restaure primeiro a chave da gravação' in capsys.readouterr().err
    assert not (tmp_path / 'keys').exists() and not (tmp_path / 'restaurado' / 'appointments.db').exists()


def test_the_key_file_of_the_volume_is_used_when_the_variable_is_empty(saved, tmp_path, monkeypatch):
    _, copy = saved
    key_file = tmp_path / 'keys' / 'db.key'
    key_file.parent.mkdir()
    key_file.write_text(os.environ['DB_ENCRYPTION_KEY'] + '\n', encoding='ascii')
    monkeypatch.setenv('DB_ENCRYPTION_KEY', '')
    monkeypatch.setenv('DB_KEY_FILE', str(key_file))
    assert restore_into(tmp_path / 'restaurado', copy, monkeypatch) == 0


def test_a_database_with_appointments_is_only_replaced_on_request(saved, tmp_path, monkeypatch, capsys):
    created, copy = saved
    with TestClient(api_app()) as client:  # DB_PATH: the original database, 3 appointments
        extra = client.post('/appointments', json=ORDERS[0]).json()
    assert backup_module().main(['--entrada', 'backup.db']) == 2  # the copy is in DB_PATH's own folder
    assert '--substituir' in capsys.readouterr().err
    with TestClient(api_app()) as client:
        assert client.get(f"/appointments/{extra['id']}").status_code == 200  # untouched
    assert backup_module().main(['--entrada', 'backup.db', '--substituir']) == 0
    with TestClient(api_app()) as client:
        assert client.get(f"/appointments/{extra['id']}").status_code == 404  # back to the copy
        assert client.get(f"/appointments/{created[0]['id']}").json() == created[0]


def test_a_restore_is_refused_while_the_api_runs_on_the_database(saved, tmp_path, monkeypatch, capsys):
    # The API keeps a connection open while it runs: the restore would replace the database under it and forget the
    # Idempotency-Keys written since the copy, so a retried POST would book twice.
    created, copy = saved
    with TestClient(api_app()) as client:
        extra = client.post('/appointments', json=ORDERS[0], headers={'Idempotency-Key': 'depois-da-copia'}).json()
        assert backup_module().main(['--entrada', 'backup.db', '--substituir']) == 2
        assert 'está em uso pela API' in capsys.readouterr().err
        assert client.post('/appointments', json=ORDERS[0], headers={'Idempotency-Key': 'depois-da-copia'}).json() == extra
    assert backup_module().main(['--entrada', 'backup.db', '--substituir']) == 0  # the API stopped: restored


def test_a_backup_never_overwrites_a_file_and_needs_a_database(saved, tmp_path, monkeypatch, capsys):
    _, copy = saved
    before = copy.read_bytes()
    assert backup_module().main(['--saida', 'backup.db']) == 2
    assert 'já existe' in capsys.readouterr().err and copy.read_bytes() == before
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'nao-existe.db'))
    assert backup_module().main(['--saida', 'outra.db']) == 2
    assert 'banco não encontrado' in capsys.readouterr().err and not (tmp_path / 'outra.db').exists()


def test_a_damaged_copy_is_refused(saved, tmp_path, monkeypatch, capsys):
    _, copy = saved
    copy.write_bytes(b'nao e um banco SQLite' * 100)
    assert restore_into(tmp_path / 'restaurado', copy, monkeypatch) == 2
    assert capsys.readouterr().err.startswith('Erro: restauração falhou:')
    assert not (tmp_path / 'restaurado' / 'appointments.db').exists()


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


@pytest.mark.parametrize('option', ['--saida', '--entrada'])
@pytest.mark.parametrize('name', ['../backup.db', '/state/backup.db', 'C:/Program Files/Git/state/backup.db',
                                  'C:\\backup.db', 'pasta/backup.db', 'pasta\\backup.db', '..', '.', ''])
def test_only_a_bare_file_name_is_accepted(tmp_path, monkeypatch, capsys, option, name):
    """A folder, "..", or an absolute path (what Git Bash makes of /state/backup.db) is refused before anything."""
    (tmp_path / 'state').mkdir()
    with TestClient(configure(tmp_path / 'state', monkeypatch)), pytest.raises(SystemExit) as refused:
        backup_module().main([option, name])
    error = capsys.readouterr().err.splitlines()[-1]
    assert refused.value.code == 2 and error.endswith('use só um nome de arquivo, sem pasta nem caminho (ex.: backup.db)')
    assert not list(tmp_path.glob('**/backup.db'))
