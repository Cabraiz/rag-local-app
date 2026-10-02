# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Counter validation and real owned-worker restart; never reset global usage."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys

ROOT = _workspace_root
SCRIPT = ROOT / 'app/tests/provider/provider_card_receipt.py'


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--output', required=True)
    cli.add_argument('--baseline', action='store_true')
    cli.add_argument('--smoke')
    cli.add_argument('--billing-proof')
    args = cli.parse_args()
    target = Path(args.output).resolve()
    assert target.is_relative_to(ROOT / 'eval/runs') and not target.exists()
    folder = target.with_suffix('')
    folder.mkdir(exist_ok=False)
    proof = dict(card_id='BUG-125', evidence_type='verified_regression', complete=False,
                 consecutive_passes=0, criteria_passed=[], rounds=[], cloud_calls=0,
                 counter_resets=0, synthetic_only=False,
                 scope='Current SQLite usage, bounded controls and owned local worker restarts; no model calls')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    if args.baseline:
        source = Path(args.smoke).resolve()
        assert source.is_relative_to(ROOT / 'eval/runs')
        value = json.loads(source.read_text(encoding='utf8'))
        assert value['passed'] is True and len(value['rounds']) == 2
        fixture = folder / 'BASELINE-existing-real-smoke.json'
        value['baseline_only'] = True
        fixture.write_text(json.dumps(value))
        run = subprocess.run([sys.executable, str(SCRIPT), '--card', 'RAG-02', '--proof', str(fixture),
                              '--billing-proof', args.billing_proof], capture_output=True, text=True,
                             shell=False, timeout=20, creationflags=flags)
        proof.update(reproduced=run.returncode == 0,
                     accepted_without_current_counter_snapshot=run.returncode == 0,
                     reused_real_inference=str(source.relative_to(ROOT)),
                     child_receipt_scope='BASELINE ONLY, never use for card approval')
    else:
        from provider_usage_proof import (snapshot_usage, validate_usage_proof, owned_worker_restart,
                                          no_active_requests, docker_command)
        for seed, worker in ((802733, 'rag-local-v2-worker-1'), (317946, 'rag-local-v2-worker-2')):
            assert no_active_requests(), 'ACTIVE_REQUESTS_RESTART_REFUSED'
            before = snapshot_usage(worker)
            owned_worker_restart(worker)
            after = snapshot_usage(worker)
            other = snapshot_usage('rag-local-v2-worker-2' if worker.endswith('-1') else 'rag-local-v2-worker-1')
            real_checks = dict(restart_preserved=before['used'] == after['used']
                               and before['rag_reservations'] == after['rag_reservations'],
                               shared_volume=before['volume'] == after['volume'] == other['volume'],
                               same_counter=after['used'] == other['used'],
                               unchanged_image=before['image'] == after['image'] == other['image'],
                               no_active_requests=no_active_requests())
            # Isolated tmpfs control: the application's actual reservation code
            # rejects 1001 and replay without mutating the real shared counter.
            code = '''import json,sqlite3,tempfile
from pathlib import Path
from datetime import datetime,timezone
from rag_app import gemini_grounded as m
from rag_app.gemini_lab import ProbeBlocked,DAILY_ATTEMPTS
m.USAGE=Path(tempfile.mkdtemp())/'CONTROL-ONLY.sqlite3'
with sqlite3.connect(m.USAGE) as db:
 db.execute('CREATE TABLE attempts(day TEXT PRIMARY KEY,used INTEGER NOT NULL)')
 db.execute('INSERT INTO attempts VALUES (?,?)',(datetime.now(timezone.utc).date().isoformat(),DAILY_ATTEMPTS-1))
m.reserve('synthetic-cap-1','digest')
blocked=False
try: m.reserve('synthetic-cap-2','digest')
except ProbeBlocked as e: blocked=str(e)=='DAILY_MODEL_LIMIT'
duplicate=False
try: m.reserve('synthetic-cap-1','digest')
except ProbeBlocked as e: duplicate=str(e)=='MODEL_ATTEMPT_ALREADY_RESERVED'
with sqlite3.connect(m.USAGE) as db:
 used=db.execute('SELECT used FROM attempts').fetchone()[0]
 n=db.execute('SELECT count(*) FROM rag_model_calls').fetchone()[0]
print(json.dumps(dict(passed=blocked and duplicate and used==1000 and n==1,scope='SIMULATED TMPFS LIMIT CONTROL ONLY',cloud_calls=0)))
'''
            limit_control = json.loads(docker_command('run', '--rm', '--network', 'none', '--read-only',
                '--tmpfs', '/tmp:size=16m,mode=1777', after['image'], 'python', '-c', code))
            real_checks['actual_code_rejects_limit_and_duplicate'] = limit_control['passed']
            now = datetime.now(timezone.utc)
            # Synthetic snapshots below exercise only the validator, not inference.
            b = deepcopy(after); a = deepcopy(after)
            b.update(observed_at_utc=(now - timedelta(seconds=20)).isoformat(), used=10, rag_reservations=100)
            a.update(observed_at_utc=(now - timedelta(seconds=5)).isoformat(), used=12, rag_reservations=102,
                     model_requests=[dict(id='case-a', state='DONE'), dict(id='case-b', state='DONE')])
            smoke = dict(started_at=(now - timedelta(seconds=15)).isoformat(),
                         at=(now - timedelta(seconds=2)).isoformat(), usage_before=b, usage_after=a,
                         rounds=[dict(results=[dict(request_id='case-a', model='gemini-3.5-flash-lite')]),
                                 dict(results=[dict(request_id='case-b', model='gemini-3.5-flash-lite')])])
            control_rounds = []
            for index, control_worker in enumerate(('rag-local-v2-worker-1', 'rag-local-v2-worker-2')):
                rb = deepcopy(before); ra = deepcopy(after)
                rb.update(worker=control_worker, observed_at_utc=(now - timedelta(seconds=60-index*10)).isoformat())
                ra.update(worker=control_worker, observed_at_utc=(now - timedelta(seconds=55-index*10)).isoformat())
                control_rounds.append(dict(real_checks=real_checks, before=rb, after=ra, passed=True))
            persistence = dict(card_id='BUG-125', evidence_type='verified_regression',
                               cloud_calls=0, counter_resets=0, validation_fixture=True,
                               synthetic_only=False, complete=True, consecutive_passes=2,
                               verified_at=(now - timedelta(seconds=30)).isoformat(),
                               rounds=control_rounds)
            paths = (SCRIPT, Path(__file__), ROOT / 'app/tests/provider/provider_usage_proof.py',
                     ROOT / 'app/tests/gemini/gemini_rag_smoke.py', ROOT / 'app/src/rag_app/models/gemini_lab.py',
                     ROOT / 'app/src/rag_app/models/gemini_grounded.py')
            persistence['sources_sha256'] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            control = folder / f'{seed}-SYNTHETIC-validator-persistence.json'
            control.write_text(json.dumps(persistence))
            variants = [('fresh', None, None), ('missing_snapshots', 'missing', 'USAGE_SNAPSHOTS_REQUIRED'),
                        ('counter_regressed', 'regressed', 'USAGE_COUNTER_INVALID'),
                        ('counter_unchanged', 'unchanged', 'USAGE_COUNTER_INVALID'),
                        ('wrong_limit', 'limit', 'USAGE_COUNTER_INVALID'),
                        ('over_limit', 'over', 'USAGE_COUNTER_INVALID'),
                        ('boolean_counter', 'boolean', 'USAGE_COUNTER_INVALID'),
                        ('different_day', 'day', 'USAGE_COUNTER_INVALID'),
                        ('ephemeral_volume', 'ephemeral', 'USAGE_VOLUME_INVALID'),
                        ('different_volume', 'volume', 'USAGE_VOLUME_INVALID'),
                        ('changed_image', 'image', 'USAGE_IMAGE_INVALID'),
                        ('missing_reservation', 'reservation', 'USAGE_REQUESTS_INVALID'),
                        ('reserved_not_done', 'reserved', 'USAGE_REQUESTS_INVALID'),
                        ('stale_snapshot', 'time', 'USAGE_TIME_INVALID'),
                        ('missing_persistence', 'no_proof', 'USAGE_PERSISTENCE_REQUIRED'),
                        ('outside_eval', 'outside', 'USAGE_PERSISTENCE_OUTSIDE_EVAL'),
                        ('stale_persistence', 'stale_proof', 'USAGE_PERSISTENCE_INVALID'),
                        ('missing_source_binding', 'sources', 'USAGE_PERSISTENCE_INVALID'),
                        ('duplicated_restart', 'duplicate_restart', 'USAGE_PERSISTENCE_INVALID'),
                        ('future_persistence', 'future_proof', 'USAGE_PERSISTENCE_INVALID'),
                        ('changed_persistence_volume', 'proof_volume', 'USAGE_PERSISTENCE_INVALID'),
                        ('counter_reset_claim', 'reset', 'USAGE_PERSISTENCE_INVALID'),
                        ('validator_fixture_cannot_approve', 'fixture', 'USAGE_PERSISTENCE_INVALID')]
            random.Random(seed).shuffle(variants)
            checks = []
            for name, mutation, expected in variants:
                v = deepcopy(smoke); p = control
                persistence_variant = deepcopy(persistence)
                if mutation == 'missing': v.pop('usage_after')
                elif mutation == 'regressed': v['usage_after']['used'] = 9
                elif mutation == 'unchanged': v['usage_after']['used'] = 10
                elif mutation == 'limit': v['usage_after']['daily_limit'] = 100000
                elif mutation == 'over': v['usage_after']['used'] = 1001
                elif mutation == 'boolean': v['usage_before']['used'] = True
                elif mutation == 'day': v['usage_after']['day'] = '1900-01-01'
                elif mutation == 'ephemeral': v['usage_after']['volume']['type'] = 'bind'
                elif mutation == 'volume': v['usage_after']['volume']['name'] = 'another-volume'
                elif mutation == 'image': v['usage_after']['image'] = 'sha256:' + '0' * 64
                elif mutation == 'reservation': v['usage_after']['model_requests'] = []
                elif mutation == 'reserved': v['usage_after']['model_requests'][0]['state'] = 'RESERVED'
                elif mutation == 'time': v['usage_before']['observed_at_utc'] = (now - timedelta(hours=1)).isoformat()
                elif mutation == 'no_proof': p = None
                elif mutation == 'outside': p = SCRIPT
                elif mutation == 'stale_proof': persistence_variant['verified_at'] = (now - timedelta(hours=1)).isoformat()
                elif mutation == 'sources': persistence_variant['sources_sha256'] = {}
                elif mutation == 'duplicate_restart': persistence_variant['rounds'][1] = deepcopy(persistence_variant['rounds'][0])
                elif mutation == 'future_proof': persistence_variant['verified_at'] = (now + timedelta(seconds=1)).isoformat()
                elif mutation == 'proof_volume': persistence_variant['rounds'][0]['before']['volume']['name'] = 'other'
                elif mutation == 'reset': persistence_variant['counter_resets'] = 1
                if mutation in {'stale_proof', 'sources', 'duplicate_restart', 'future_proof', 'proof_volume', 'reset'}:
                    p = folder / f'{seed}-{name}-SYNTHETIC.json'
                    p.write_text(json.dumps(persistence_variant))
                observed = None
                try: validate_usage_proof(v, p, now=now, allow_validation_fixture=mutation != 'fixture')
                except ValueError as error: observed = str(error)
                checks.append(dict(name=name, expected=expected, observed=observed, passed=observed == expected))
            passed = all(real_checks.values()) and all(c['passed'] for c in checks)
            proof['rounds'].append(dict(seed=seed, before=before, after=after, real_checks=real_checks,
                                       checks=checks, limit_control=limit_control, passed=passed))
            if not passed: break
        proof['complete'] = len(proof['rounds']) == 2 and all(r['passed'] for r in proof['rounds'])
        if proof['complete']:
            proof.update(consecutive_passes=2, criteria_passed=['reproduction', 'two_regression_rounds'],
                         reproduction='eval/runs/provider-usage-baseline-20261002.json')
    paths = [SCRIPT, Path(__file__)]
    if not args.baseline:
        paths += [ROOT / 'app/tests/provider/provider_usage_proof.py', ROOT / 'app/tests/gemini/gemini_rag_smoke.py',
                  ROOT / 'app/src/rag_app/models/gemini_lab.py', ROOT / 'app/src/rag_app/models/gemini_grounded.py']
    proof['sources_sha256'] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    proof['verified_at'] = datetime.now(timezone.utc).isoformat()
    target.write_text(json.dumps(proof, indent=2))
    print(json.dumps(dict(receipt=str(target), complete=proof['complete'], reproduced=proof.get('reproduced'))))
    raise SystemExit(0 if proof['complete'] or args.baseline and proof.get('reproduced') else 1)


if __name__ == '__main__': main()
