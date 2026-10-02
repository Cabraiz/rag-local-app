import os
from pathlib import Path


def secret(name):
    return Path('/run/secrets/'+name).read_text().strip()


def enforce_lab():
    if os.environ.get('RAG_MODE') != 'lab':
        raise RuntimeError('Production disabled: Gate B, OIDC, HA and real RAG adapters are not complete')


def db_options(admin=False):
    return dict(host='postgres', dbname='rag', user='rag_bootstrap' if admin else 'rag_app',
                password=secret('db_admin' if admin else 'db_app'), connect_timeout=5,
                options='-c statement_timeout=5000 -c lock_timeout=2000 '
                        '-c idle_in_transaction_session_timeout=5000 -c synchronous_commit=on')
