# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Offline adversarial checks, same-author; no remote calls or actual credentials."""
import asyncio
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import sqlite3
import tempfile
from rag_app import gemini_lab as probe


def round_checks(seed):
    rng = random.Random(seed)
    env = {'RAG_MODE':'lab','RAG_GEMINI_PROBE':'synthetic_only',
           'RAG_GEMINI_FREE_CONFIRMED':'no_billing','GOOGLE_GENAI_USE_VERTEXAI':'false'}
    names = []
    def check(name, value):
        assert value, name
        names.append(name)
    check('fixed_model_and_attempt_budget', probe.MODEL == 'gemini-3.5-flash-lite' and probe.DAILY_ATTEMPTS == 1000)
    check('private_file_not_environment', probe.configuration(env)[0] == Path('/run/secrets/gemini_api_key'))
    shuffled = list(env); rng.shuffle(shuffled)
    for field in shuffled:
        bad = dict(env); bad[field] = 'unsafe'
        try: probe.configuration(bad)
        except probe.ProbeBlocked: names.append('fail_closed_'+field)
        else: raise AssertionError(field)
    with tempfile.TemporaryDirectory(dir=_workspace_root/'tmp') as temp:
        root = Path(temp); usage = root/'counter.sqlite3'
        def reserve(_):
            try: return probe.reserve_attempt(usage, '2030-01-01')
            except probe.ProbeBlocked: return None
        probe.reserve_attempt(usage, '2029-12-31')  # Initialize schema before contention.
        # Approach the real policy ceiling in an isolated fake database, not
        # by performing 1,000 requests or changing the runtime usage volume.
        with closing(sqlite3.connect(usage)) as db, db:
            db.execute('INSERT INTO attempts VALUES (?,?)', ('2030-01-01', probe.DAILY_ATTEMPTS-8))
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(reserve, range(20)))
        check('durable_atomic_concurrent_budget', sorted(v for v in values if v)
              == list(range(probe.DAILY_ATTEMPTS-7, probe.DAILY_ATTEMPTS+1)))
        check('overflow_reservations_blocked', values.count(None) == 12)
        check('next_day_has_independent_budget', probe.reserve_attempt(usage, '2030-01-02') == 1)
        with closing(sqlite3.connect(usage)) as db:
            check('limit_persisted', db.execute('SELECT used FROM attempts WHERE day=?',('2030-01-01',)).fetchone()[0] == probe.DAILY_ATTEMPTS)
        secret = root/'fake-key'; secret.write_text('synthetic_test_key_not_a_real_credential', encoding='utf8')
        calls = []
        async def failing(_): calls.append(1); raise TimeoutError('fake')
        async def passing(_): calls.append(1); return True
        remote_usage = root/'calls.sqlite3'
        try: asyncio.run(probe.execute(secret, remote_usage, failing))
        except TimeoutError: names.append('failed_call_consumes_reservation')
        else: raise AssertionError('fake error swallowed')
        check('second_reserved_probe', asyncio.run(probe.execute(secret, remote_usage, passing))['attempt_today'] == 2)
        check('existing_two_attempts_continue_at_three', asyncio.run(probe.execute(secret, remote_usage, passing))['attempt_today'] == 3)
        with closing(sqlite3.connect(remote_usage)) as db, db:
            check('prior_attempts_not_reset', db.execute('SELECT used FROM attempts').fetchone()[0] == 3)
            db.execute('UPDATE attempts SET used=?', (probe.DAILY_ATTEMPTS,))
        try: asyncio.run(probe.execute(secret, remote_usage, passing))
        except probe.ProbeBlocked: names.append('attempt_1001_blocked_before_io')
        else: raise AssertionError('budget exceeded')
        check('no_overflow_invocation_no_retry', len(calls) == 3)
        secret.write_text('invalid whitespace key', encoding='utf8')
        try: asyncio.run(probe.execute(secret, root/'invalid.sqlite3', passing))
        except probe.ProbeBlocked: names.append('invalid_secret_blocks_before_io')
        else: raise AssertionError('invalid secret accepted')
    return {'seed':seed,'checks':len(names),'passed':True,'names':names}


if __name__ == '__main__':
    print(json.dumps({'scope':'offline_controls_not_independent_blind_audit',
        'rounds':[round_checks(84913),round_checks(62077)]}))
