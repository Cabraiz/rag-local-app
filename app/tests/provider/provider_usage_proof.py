# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Read current counters without secrets; restart only known quiescent lab workers."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import UUID

ROOT = _workspace_root
WORKERS = ('rag-local-v2-worker-1', 'rag-local-v2-worker-2')
CORE = ('gemini_lab.py', 'gemini_grounded.py')
USAGE_SOURCES = {
    'app/tests/provider/provider_card_receipt.py', 'app/tests/provider/provider_usage_checks.py',
    'app/tests/provider/provider_usage_proof.py', 'app/tests/gemini/gemini_rag_smoke.py',
    'app/src/rag_app/models/gemini_lab.py', 'app/src/rag_app/models/gemini_grounded.py',
}
RESTART_CHECKS = {
    'restart_preserved', 'shared_volume', 'same_counter', 'unchanged_image',
    'no_active_requests', 'actual_code_rejects_limit_and_duplicate',
}


def docker_command(*parts):
    value = subprocess.run(['docker', *parts], capture_output=True, text=True, timeout=60,
                           shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if value.returncode:
        raise RuntimeError('OWNED_USAGE_COMMAND_FAILED')
    return value.stdout.strip()


def worker_info(worker):
    if worker not in WORKERS:
        raise ValueError('OWNED_WORKER_REQUIRED')
    info = json.loads(docker_command('inspect', worker))[0]
    if (info['Config']['Labels']['com.docker.compose.project'] != 'rag-local-v2'
            or info['Config']['Labels']['com.docker.compose.service'] != 'worker'):
        raise ValueError('OWNED_WORKER_REQUIRED')
    return info


def snapshot_usage(worker=WORKERS[0], request_ids=()):
    info = worker_info(worker)
    ids = [str(UUID(v)) for v in request_ids]
    assert len(ids) <= 20 and len(set(ids)) == len(ids), 'BOUNDED_USAGE_REQUEST_IDS'
    mount = next(m for m in info['Mounts'] if m['Destination'] == '/usage')
    code = '''import hashlib,json,sqlite3
from datetime import datetime,timezone
from pathlib import Path
from rag_app.gemini_lab import DAILY_ATTEMPTS
day=datetime.now(timezone.utc).date().isoformat()
db=sqlite3.connect('file:/usage/gemini-probes.sqlite3?mode=ro',uri=True)
row=db.execute('SELECT used FROM attempts WHERE day=?',(day,)).fetchone()
ids=REQUEST_IDS
requests=[dict(id=v[0],state=v[1]) for rid in ids for v in db.execute('SELECT id,state FROM rag_model_calls WHERE id=?',(rid,)).fetchall()]
print(json.dumps(dict(observed_at_utc=datetime.now(timezone.utc).isoformat(),day=day,
 used=row[0] if row else 0,daily_limit=DAILY_ATTEMPTS,
 rag_reservations=db.execute('SELECT count(*) FROM rag_model_calls').fetchone()[0],model_requests=requests,
 module_sha256={'app/src/rag_app/models/'+n:hashlib.sha256(Path(__import__('importlib').import_module('rag_app.'+n[:-3]).__file__).read_bytes()).hexdigest() for n in ('gemini_lab.py','gemini_grounded.py')})))
'''.replace('REQUEST_IDS', repr(ids))
    data = json.loads(docker_command('exec', worker, 'python', '-c', code))
    env = dict(v.split('=', 1) for v in info['Config']['Env'] if '=' in v)
    data.update(worker=worker, image=info['Image'],
                volume=dict(type=mount['Type'], name=mount.get('Name'), destination=mount['Destination']),
                flags={k: env.get(k) for k in ('RAG_MODE', 'RAG_GEMINI_RESPONSES',
                                             'RAG_GEMINI_FREE_CONFIRMED', 'GOOGLE_GENAI_USE_VERTEXAI')})
    return data


def no_active_requests():
    data = docker_command('exec', 'rag-local-v2-api-1', 'python', '-c',
        "import json; from rag_app import ledger;\nwith ledger.connect() as db:\n"
        " n=db.execute(\"SELECT count(*) AS n FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT')\").fetchone()['n']\nprint(json.dumps(n==0))")
    return json.loads(data) is True


def owned_worker_restart(worker):
    worker_info(worker)
    if not no_active_requests():
        raise ValueError('ACTIVE_REQUESTS_RESTART_REFUSED')
    docker_command('restart', '--time', '45', worker)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            info = worker_info(worker)
            if info['State']['Running']:
                snapshot_usage(worker)
                return
        except RuntimeError:
            pass
        time.sleep(.5)
    raise RuntimeError('WORKER_USAGE_RESTART_DEADLINE')


def aware(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError()
    return result


def validate_usage_proof(smoke, persistence_path, *, now=None, allow_validation_fixture=False):
    before, after = smoke.get('usage_before'), smoke.get('usage_after')
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError('USAGE_SNAPSHOTS_REQUIRED')
    current = now or datetime.now(timezone.utc)
    try:
        b, s, a, f = (aware(before['observed_at_utc']), aware(smoke['started_at']),
                      aware(after['observed_at_utc']), aware(smoke['at']))
        if not (b <= s <= a <= f <= current and 0 <= (current-b).total_seconds() <= 1800):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('USAGE_TIME_INVALID') from None
    models = [item['request_id'] for r in smoke['rounds'] for item in r['results'] if item.get('model')]
    if (not models or len(set(models)) != len(models)
            or any(type(v.get(k)) is not int for v in (before, after) for k in ('used', 'daily_limit', 'rag_reservations'))
            or before['daily_limit'] != 1000 or after['daily_limit'] != 1000
            or not 0 <= before['used'] < after['used'] <= 1000
            or after['used'] - before['used'] < len(models)
            or after['rag_reservations'] - before['rag_reservations'] < len(models)
            or before.get('day') != after.get('day') or after['day'] != current.astimezone(timezone.utc).date().isoformat()):
        raise ValueError('USAGE_COUNTER_INVALID')
    if (not isinstance(after.get('volume'), dict)
            or before.get('volume') != after.get('volume') or after['volume'].get('type') != 'volume'
            or after['volume'].get('destination') != '/usage'
            or after['volume'].get('name') != 'rag-local-v2_gemini_probe_usage'):
        raise ValueError('USAGE_VOLUME_INVALID')
    if before.get('image') != after.get('image') or not str(after.get('image')).startswith('sha256:'):
        raise ValueError('USAGE_IMAGE_INVALID')
    expected_flags = {'RAG_MODE':'lab', 'RAG_GEMINI_RESPONSES':'free_lab',
                      'RAG_GEMINI_FREE_CONFIRMED':'no_billing', 'GOOGLE_GENAI_USE_VERTEXAI':'false'}
    if before.get('flags') != expected_flags or after.get('flags') != expected_flags:
        raise ValueError('USAGE_MODE_INVALID')
    embedded = {'app/src/rag_app/'+n: hashlib.sha256((ROOT/'app/src/rag_app'/n).read_bytes()).hexdigest() for n in CORE}
    if before.get('module_sha256') != embedded or after.get('module_sha256') != embedded:
        raise ValueError('USAGE_IMAGE_INVALID')
    observed = after.get('model_requests', [])
    if (not isinstance(observed, list) or any(not isinstance(r, dict) for r in observed)
            or {r.get('id') for r in observed if r.get('state') == 'DONE'} != set(models)
            or len(observed) != len(models)):
        raise ValueError('USAGE_REQUESTS_INVALID')
    if persistence_path is None:
        raise ValueError('USAGE_PERSISTENCE_REQUIRED')
    path = Path(persistence_path).resolve()
    if not path.is_relative_to((ROOT/'eval/runs').resolve()):
        raise ValueError('USAGE_PERSISTENCE_OUTSIDE_EVAL')
    try:
        raw = path.read_bytes()
        if len(raw) > 1048576: raise ValueError()
        proof = json.loads(raw)
        observed_at = aware(proof['verified_at'])
        if (proof.get('validation_fixture') is True and not allow_validation_fixture):
            raise ValueError()
        if (proof.get('card_id') != 'BUG-125' or proof.get('evidence_type') != 'verified_regression'
                or type(proof.get('cloud_calls')) is not int or proof['cloud_calls'] != 0
                or type(proof.get('counter_resets')) is not int or proof['counter_resets'] != 0
                or proof['complete'] is not True or type(proof['consecutive_passes']) is not int
                or proof['consecutive_passes'] != 2
                or proof.get('synthetic_only') is not False or len(proof['rounds']) != 2
                or not observed_at <= s or not 0 <= (current-observed_at).total_seconds() <= 1800
                or set(proof['sources_sha256']) != USAGE_SOURCES):
            raise ValueError()
        restarted = set()
        for r in proof['rounds']:
            rb, ra = r['before'], r['after']
            if (r.get('passed') is not True or set(r['real_checks']) != RESTART_CHECKS
                    or not all(v is True for v in r['real_checks'].values())
                    or not (aware(rb['observed_at_utc']) <= aware(ra['observed_at_utc']) <= observed_at)
                    or rb['worker'] != ra['worker'] or ra['worker'] not in WORKERS
                    or any(type(v[k]) is not int for v in (rb, ra) for k in ('used', 'daily_limit', 'rag_reservations'))
                    or not 0 <= rb['used'] <= 1000 or rb['daily_limit'] != 1000 or ra['daily_limit'] != 1000
                    or rb['rag_reservations'] != ra['rag_reservations']
                    or rb['day'] != ra['day'] or ra['day'] != after['day']
                    or rb['flags'] != expected_flags or ra['flags'] != expected_flags
                    or rb['module_sha256'] != embedded or ra['module_sha256'] != embedded
                    or r['before']['used'] != r['after']['used']
                    or r['before']['volume'] != r['after']['volume']
                    or r['after']['volume'] != after['volume']
                    or r['before']['image'] != r['after']['image'] or r['after']['image'] != after['image']):
                raise ValueError()
            restarted.add(ra['worker'])
        if restarted != set(WORKERS):
            raise ValueError()
        for name, digest in proof['sources_sha256'].items():
            source = (ROOT/name).resolve()
            if not source.is_relative_to(ROOT/'app') or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise ValueError()
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError('USAGE_PERSISTENCE_INVALID') from None
    return dict(before=before, after=after, physical_persistence_proof=path.relative_to(ROOT).as_posix(),
                persistence_sha256=hashlib.sha256(raw).hexdigest(), daily_limit=1000,
                counter_is_spend=False, scope='Persistent local reservations, not Google quota or monetary spend')
