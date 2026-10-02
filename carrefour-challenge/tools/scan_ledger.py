"""Scan only the fictional ledger inside the gate-owned API container."""
from contextlib import closing
import json
import sqlite3


def check(path='/state/appointments.sqlite3'):
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute('SELECT request_id,result FROM appointments').fetchall()
    text = json.dumps(rows).lower()
    assert not any(s in text for s in ('sentinela', 'example.invalid', '123.456.789', '90000-1234', 'ficticio qrs'))
    assert len(rows) == len({r[0] for r in rows})
    return {'pii_absent': True, 'unique_requests': len(rows)}


if __name__ == '__main__': print(json.dumps(check()))
