"""Encryption at rest of the exam lists: the cipher module and the API database."""
import importlib
import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.crypto import CryptoError, decrypt, encrypt, load_key, new_key, resolve_key

CATALOG = [{'code': 'FICT-001', 'name': 'Hemograma completo'},
           {'code': 'FICT-002', 'name': 'Glicemia de jejum'}]
ROW = '7f0c2b5e-0000-4000-8000-000000000001'


def test_round_trip():
    cipher = load_key(new_key())
    token = encrypt(cipher, 'Hemograma completo · FICT-001', ROW)
    assert 'FICT' not in token and 'Hemograma' not in token
    assert decrypt(cipher, token, ROW) == 'Hemograma completo · FICT-001'
    assert encrypt(cipher, 'x', ROW) != encrypt(cipher, 'x', ROW)  # random nonce per value


@pytest.mark.parametrize('damage', ['wrong key', 'changed byte', 'other row', 'not base64'])
def test_any_damage_fails_authentication_with_a_fixed_message(damage):
    key = new_key()
    cipher = load_key(key)
    token, row = encrypt(cipher, '[{"code": "FICT-001"}]', ROW), ROW
    if damage == 'wrong key':
        cipher = load_key(new_key())
    elif damage == 'changed byte':
        token = token[:20] + ('A' if token[20] != 'A' else 'B') + token[21:]
    elif damage == 'other row':
        row = ROW.replace('1', '2')  # a value copied to another row is rejected too
    else:
        token = '%%%'
    with pytest.raises(CryptoError) as failure:
        decrypt(cipher, token, row)
    assert str(failure.value).startswith('não foi possível decifrar o registro')
    assert key not in str(failure.value) and 'FICT' not in str(failure.value)
    assert failure.value.__cause__ is None  # no chained error with internals


@pytest.mark.parametrize('value', [None, '', 'curta', 'A' * 44 + '!', 'QUFB'])
def test_missing_or_malformed_key_explains_how_to_create_one(value):
    with pytest.raises(CryptoError) as failure:
        load_key(value)
    assert 'api.crypto --gerar-chave' in str(failure.value)
    if value:
        assert value not in str(failure.value)


def test_resolve_key_prefers_the_environment_and_touches_no_file(tmp_path):
    key = new_key()
    assert resolve_key(key, tmp_path / 'keys' / 'db.key') == key
    assert not (tmp_path / 'keys').exists()


def test_resolve_key_creates_the_file_once_and_reuses_it(tmp_path, capsys):
    key_file = tmp_path / 'keys' / 'db.key'
    first = resolve_key(None, key_file)
    load_key(first)  # a valid key
    assert key_file.read_text(encoding='ascii').strip() == first
    if os.name == 'posix':
        assert key_file.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr()
    assert f'chave do banco criada em {key_file}' in output.err and first not in output.out + output.err
    assert resolve_key('', key_file) == first  # second start: same key, nothing printed
    assert capsys.readouterr().err == ''


@pytest.mark.parametrize('content', ['curta', 'A' * 44 + '!', 'ÿþ lixo'])
def test_resolve_key_rejects_a_malformed_file_without_echoing_it(tmp_path, content):
    key_file = tmp_path / 'db.key'
    key_file.write_bytes(content.encode('latin-1'))
    with pytest.raises(CryptoError) as failure:
        resolve_key(None, key_file)
    message = str(failure.value)
    assert message.startswith(f'chave do banco inválida em {key_file}') and 'DB_ENCRYPTION_KEY' in message
    assert content not in message and failure.value.__cause__ is None


# --- The API database -----------------------------------------------------------


def start_api(tmp_path, monkeypatch, key):
    catalog = tmp_path / 'exams.json'
    catalog.write_text(json.dumps(CATALOG), encoding='utf-8')
    monkeypatch.setenv('EXAMS_PATH', str(catalog))
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'appointments.db'))
    monkeypatch.setenv('DB_KEY_FILE', str(tmp_path / 'keys' / 'db.key'))
    monkeypatch.setenv('DB_ENCRYPTION_KEY', key)
    import api.main
    return api.main  # the settings are read when the app starts, not on import


def raw_database(tmp_path):
    """Every byte of the SQLite files (database and write-ahead log)."""
    return b''.join(path.read_bytes() for path in tmp_path.glob('appointments.db*'))


def test_database_holds_no_exam_in_clear_and_get_returns_what_post_stored(tmp_path, monkeypatch):
    module = start_api(tmp_path, monkeypatch, new_key())
    with TestClient(module.app) as client:
        created = client.post('/appointments', json={'exams': [{'code': 'FICT-001', 'name': 'a'},
                                                              {'code': 'FICT-002', 'name': 'b'}]}).json()
        assert client.get(f"/appointments/{created['id']}").json() == created
    for secret in (b'FICT', b'Hemograma', b'Glicemia'):
        assert secret not in raw_database(tmp_path)
    with sqlite3.connect(tmp_path / 'appointments.db') as connection:
        stored = connection.execute('SELECT exams FROM appointments').fetchone()[0]
    assert 'FICT' not in stored and 'Hemograma' not in stored


def test_wrong_key_or_tampered_row_gives_a_clear_500_without_the_key(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    first_key, second_key = new_key(), new_key()
    module = start_api(tmp_path, monkeypatch, first_key)
    with TestClient(module.app) as client:
        kept = client.post('/appointments', json={'exams': [{'code': 'FICT-001', 'name': 'a'}]}).json()
        changed = client.post('/appointments', json={'exams': [{'code': 'FICT-002', 'name': 'b'}]}).json()
    with sqlite3.connect(tmp_path / 'appointments.db') as connection:
        token = connection.execute('SELECT exams FROM appointments WHERE id = ?', (changed['id'],)).fetchone()[0]
        connection.execute('UPDATE appointments SET exams = ? WHERE id = ?',
                           (token[:30] + ('A' if token[30] != 'A' else 'B') + token[31:], changed['id']))
    replies = []
    with TestClient(start_api(tmp_path, monkeypatch, first_key).app) as client:
        replies.append(client.get(f"/appointments/{changed['id']}"))  # tampered row
        assert client.get(f"/appointments/{kept['id']}").json() == kept  # the others still work
    with TestClient(start_api(tmp_path, monkeypatch, second_key).app) as client:
        replies.append(client.get(f"/appointments/{kept['id']}"))  # wrong key
    for reply in replies:
        assert reply.status_code == 500
        assert reply.json()['detail'].startswith('não foi possível decifrar o registro')
        assert 'Traceback' not in reply.text
    for key in (first_key, second_key):
        assert all(key not in reply.text for reply in replies) and key not in caplog.text


@pytest.mark.parametrize('edit', ['exams moved from another row', 'created_at changed'])
def test_a_value_moved_or_a_clear_field_edited_in_the_database_gives_a_fixed_500(tmp_path, monkeypatch, edit):
    module = start_api(tmp_path, monkeypatch, new_key())
    with TestClient(module.app) as client:
        source = client.post('/appointments', json={'exams': [{'code': 'FICT-001', 'name': 'a'}]}).json()
        target = client.post('/appointments', json={'exams': [{'code': 'FICT-002', 'name': 'b'}]}).json()
        with sqlite3.connect(tmp_path / 'appointments.db') as connection:
            if edit == 'created_at changed':
                connection.execute('UPDATE appointments SET created_at = ? WHERE id = ?',
                                   ('2020-01-01T00:00:00+00:00', target['id']))
            else:
                connection.execute('UPDATE appointments SET exams = (SELECT exams FROM appointments WHERE id = ?) '
                                   'WHERE id = ?', (source['id'], target['id']))
        reply = client.get(f"/appointments/{target['id']}")
        assert reply.status_code == 500
        assert reply.json() == {'detail': 'não foi possível decifrar o registro: a chave não é a da gravação '
                                          'ou o dado foi alterado no banco'}
        assert client.get(f"/appointments/{source['id']}").json() == source


@pytest.mark.parametrize('key', ['chave-invalida', 'QUFB'])
def test_api_does_not_start_with_an_invalid_key(tmp_path, monkeypatch, key):
    module = start_api(tmp_path, monkeypatch, key)
    with pytest.raises(CryptoError) as stop, TestClient(module.app):
        pass
    message = str(stop.value)
    assert 'DB_ENCRYPTION_KEY inválida' in message and 'api.crypto --gerar-chave' in message
    assert key not in message
    assert not list(tmp_path.glob('appointments.db*'))  # stopped before the database was opened


def test_api_without_a_key_creates_one_and_reads_back_after_a_restart(tmp_path, monkeypatch):
    with TestClient(start_api(tmp_path, monkeypatch, '').app) as client:
        created = client.post('/appointments', json={'exams': [{'code': 'FICT-001', 'name': 'a'}]}).json()
    assert (tmp_path / 'keys' / 'db.key').exists()
    with TestClient(start_api(tmp_path, monkeypatch, '').app) as client:
        assert client.get(f"/appointments/{created['id']}").json() == created


def test_api_does_not_start_with_a_malformed_key_file(tmp_path, monkeypatch):
    (tmp_path / 'keys').mkdir()
    (tmp_path / 'keys' / 'db.key').write_text('estragada', encoding='ascii')
    with pytest.raises(CryptoError) as stop, TestClient(start_api(tmp_path, monkeypatch, '').app):
        pass
    message = str(stop.value)
    assert message.startswith('chave do banco inválida em') and 'estragada' not in message


def test_importing_the_api_reads_no_setting_and_touches_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'state' / 'appointments.db'))
    monkeypatch.setenv('DB_KEY_FILE', str(tmp_path / 'keys' / 'db.key'))
    monkeypatch.setenv('DB_ENCRYPTION_KEY', 'chave-invalida')
    monkeypatch.setenv('EXAMS_PATH', str(tmp_path / 'nao-existe.json'))
    import api.main
    importlib.reload(api.main)  # no exit, no key, no database, no catalog
    assert list(tmp_path.iterdir()) == []


def test_uvicorn_refuses_an_invalid_key_with_one_clear_line(tmp_path):
    """The real server command (Dockerfile): one line that says why, no traceback, no key."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    key = 'chave-invalida-sentinela'
    env = {**os.environ, 'DB_ENCRYPTION_KEY': key, 'DB_PATH': str(tmp_path / 'appointments.db'),
           'DB_KEY_FILE': str(tmp_path / 'keys' / 'db.key')}
    server = subprocess.run([sys.executable, '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', str(port)],
                            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True,
                            timeout=60, shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = server.stdout + server.stderr
    assert server.returncode != 0
    assert [line for line in output.splitlines() if 'DB_ENCRYPTION_KEY' in line] == \
        [line for line in output.splitlines() if 'A API não subiu: DB_ENCRYPTION_KEY inválida' in line] != []
    assert len([line for line in output.splitlines() if 'DB_ENCRYPTION_KEY' in line]) == 1
    assert 'Traceback' not in output and key not in output
    assert not list(tmp_path.glob('appointments.db*'))
