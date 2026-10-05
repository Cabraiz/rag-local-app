"""Authenticated encryption of the health data the API stores (AES-256-GCM).

Each value gets a random 12-byte nonce and is bound to the clear fields of its row
(GCM associated data): a wrong key, a changed byte, a value copied to another row or
an edited clear field fails to decrypt instead of returning wrong data.
Stored format: base64url(nonce + ciphertext + tag).

The key comes from DB_ENCRYPTION_KEY when it is set; otherwise the API creates one on its
first start and keeps it in its own volume (resolve_key). To create your own key instead
(no Python needed on the host):
    docker compose run --rm --no-deps api python -m api.crypto --gerar-chave
"""
import argparse
import base64
import binascii
import os
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_VARIABLE = 'DB_ENCRYPTION_KEY'
NONCE_BYTES = 12
HOW_TO_CREATE = ('gere uma com `docker compose run --rm --no-deps api python -m api.crypto --gerar-chave` '
                 'e coloque em DB_ENCRYPTION_KEY no .env')


class CryptoError(Exception):
    """A fixed message for the user; it never contains the key or the data."""


def new_key() -> str:
    """A random 256-bit key, base64url encoded (44 characters)."""
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode('ascii')


def key_bytes(value: str) -> bytes:
    """The 32 bytes of a base64url key, or b'' if it is malformed."""
    try:
        key = base64.b64decode(value.strip(), altchars=b'-_', validate=True)
    except (binascii.Error, ValueError):
        return b''
    return key if len(key) == 32 else b''


def load_key(value: str | None) -> AESGCM:
    """The cipher for a base64url key; a clear error if it is missing or malformed."""
    if not value:
        raise CryptoError(f'{KEY_VARIABLE} não definida: {HOW_TO_CREATE}.')
    key = key_bytes(value)
    if not key:
        raise CryptoError(f'{KEY_VARIABLE} inválida (esperada uma chave de 32 bytes em base64): {HOW_TO_CREATE}.')
    return AESGCM(key)


def resolve_key(env_value: str | None, key_file: Path) -> str:
    """DB_ENCRYPTION_KEY if set; else the key saved in `key_file`; else a new key saved there.

    The file lives in its own volume, apart from the database: a copy of the data volume
    alone cannot be decrypted. The key itself is never printed.
    """
    if env_value:
        return env_value
    try:
        if key_file.exists():
            value = key_file.read_text(encoding='ascii', errors='replace').strip()
            if not key_bytes(value):
                raise CryptoError(f'chave do banco inválida em {key_file} (esperada uma chave de 32 bytes em '
                                  f'base64): restaure a chave da gravação ou defina {KEY_VARIABLE} no .env.')
            return value
        key_file.parent.mkdir(parents=True, exist_ok=True)
        value = new_key()
        # O_EXCL never overwrites an existing key; 0o600 keeps it readable by the API user only.
        with os.fdopen(os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='ascii') as file:
            file.write(value + '\n')
    except OSError as error:
        raise CryptoError(f'não foi possível ler ou criar a chave do banco em {key_file}: '
                          f'{error.strerror}') from None
    print(f'chave do banco criada em {key_file}', file=sys.stderr)
    return value


def encrypt(cipher: AESGCM, text: str, bound_to: str) -> str:
    """Seal `text`; `bound_to` (the row's clear fields) must match again to decrypt."""
    nonce = os.urandom(NONCE_BYTES)
    sealed = cipher.encrypt(nonce, text.encode('utf-8'), bound_to.encode('utf-8'))
    return base64.urlsafe_b64encode(nonce + sealed).decode('ascii')


def decrypt(cipher: AESGCM, token: str, bound_to: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(token.encode('ascii'))
        return cipher.decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], bound_to.encode('utf-8')).decode('utf-8')
    except (InvalidTag, ValueError, binascii.Error, UnicodeError):
        raise CryptoError('não foi possível decifrar o registro: a chave não é a da gravação '
                          'ou o dado foi alterado no banco') from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='python -m api.crypto', description='Chave de cifra do banco da API.')
    parser.add_argument('--gerar-chave', action='store_true', required=True,
                        help='imprime uma chave nova (AES-256, base64url) para DB_ENCRYPTION_KEY')
    parser.parse_args()
    print(new_key())
