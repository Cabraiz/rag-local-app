# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Expiry oracle regression against an owned Redis, never user requests/volumes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import time
from uuid import uuid4

ROOT = _workspace_root


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('--project', required=True)
    cli.add_argument('--output', required=True)
    cli.add_argument('--baseline', action='store_true')
    args = cli.parse_args()
    project = args.project
    assert project.startswith('rag-local-resilience-qa-') and project.removeprefix('rag-local-resilience-qa-').isdigit()
    container = project + '-redis-1'
    target = Path(args.output).resolve()
    assert target.is_relative_to((ROOT / 'eval/runs').resolve()) and not target.exists()
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def docker(*parts):
        result = subprocess.run(['docker', *parts], capture_output=True, text=True, timeout=30,
                                shell=False, creationflags=flags)
        assert result.returncode == 0, result.stderr[-1000:]
        return result.stdout.strip()

    info = json.loads(docker('inspect', container))[0]
    assert info['Config']['Labels']['com.docker.compose.project'] == project
    assert info['Config']['Labels']['com.docker.compose.service'] == 'redis'
    assert info['State']['Running'] is False, 'OWNED_STOPPED_FIXTURE_REQUIRED'
    receipt = dict(card_id='BUG-124', evidence_type='verified_regression', complete=False,
                   consecutive_passes=0, criteria_passed=[], rounds=[], cloud_calls=0,
                   at=datetime.now(timezone.utc).isoformat(), project=project,
                   scope='Owned real Redis expiry controls; no user requests, no cache reset',
                   sources_sha256={})
    keys = []

    def redis(*parts):
        return json.loads(docker('exec', container, 'redis-cli', '--json', *map(str, parts)))

    def key():
        value = 'rag:v1:expiry-proof:' + str(uuid4())
        keys.append(value)
        return value

    def probe():
        live = redis('EVAL', "redis.call('SET',KEYS[1],'synthetic','PX',200); return {redis.call('TTL',KEYS[1]),redis.call('PTTL',KEYS[1])}", 1, key())
        racing = key()
        redis('SET', racing, 'synthetic', 'PX', 10000)
        seen = []; cursor = '0'
        while True:
            cursor, batch = redis('SCAN', cursor, 'MATCH', racing, 'COUNT', 1000)
            seen.extend(batch)
            if str(cursor) == '0':
                break
        redis('PEXPIRE', racing, 1)
        time.sleep(.02)
        expired = redis('PTTL', racing)
        persistent = redis('EVAL', "redis.call('SET',KEYS[1],'synthetic'); return redis.call('PTTL',KEYS[1])", 1, key())
        overlong = redis('EVAL', "redis.call('SET',KEYS[1],'synthetic','PX',301000); return redis.call('PTTL',KEYS[1])", 1, key())
        return dict(live_ttl_seconds=live[0], live_pttl_ms=live[1], scanned_before_expiry=racing in seen,
                    expired_snapshot_pttl=expired, persistent_pttl=persistent, overlong_pttl=overlong)

    try:
        docker('start', container)
        assert redis('PING') == 'PONG'
        if args.baseline:
            assert 'all(0<cache.client().ttl(k)<=300' in (ROOT / 'app/tests/resilience/resilience_fixture.py').read_text()
            observed = probe()
            receipt.update(observed=observed,
                           reproduced=observed['live_ttl_seconds'] == 0 and 0 < observed['live_pttl_ms'] <= 200
                           and observed['scanned_before_expiry'] and observed['expired_snapshot_pttl'] == -2,
                           old_oracle_rejected_live_key=not 0 < observed['live_ttl_seconds'] <= 300,
                           old_oracle_rejected_expired_snapshot=True)
        else:
            from cache_expiry_oracle import valid_pttl
            for seed in (436203, 673819):
                observed = probe()
                cases = [('live_subsecond', observed['live_pttl_ms'], True),
                         ('expired_snapshot', observed['expired_snapshot_pttl'], True),
                         ('persistent_real', observed['persistent_pttl'], False),
                         ('overlong_real', observed['overlong_pttl'], False),
                         ('zero_boundary', 0, True), ('ttl_limit', 300000, True),
                         ('too_long', 300001, False), ('persistent', -1, False),
                         ('unknown_error', -3, False), ('bool_not_ttl', True, False),
                         ('float_not_ttl', 1.5, False), ('missing_value', None, False)]
                random.Random(seed).shuffle(cases)
                checks = [dict(name=n, observed=v, expected=e, passed=valid_pttl(v) is e) for n, v, e in cases]
                checks.append(dict(name='real_scan_then_expiry', passed=observed['scanned_before_expiry']
                                   and observed['expired_snapshot_pttl'] == -2))
                checks.append(dict(name='real_ttl_zero_false_positive', passed=observed['live_ttl_seconds'] == 0
                                   and observed['live_pttl_ms'] > 0))
                receipt['rounds'].append(dict(seed=seed, observed=observed, checks=checks,
                                             passed=all(c['passed'] for c in checks)))
                if not receipt['rounds'][-1]['passed']:
                    break
            receipt['complete'] = len(receipt['rounds']) == 2 and all(r['passed'] for r in receipt['rounds'])
            if receipt['complete']:
                receipt.update(consecutive_passes=2, criteria_passed=['reproduction', 'two_regression_rounds'],
                               reproduction='eval/runs/cache-expiry-baseline-20261002.json')
    except Exception as error:
        receipt['error'] = type(error).__name__ + ':' + str(error)[:1000]
    finally:
        if keys:
            redis('DEL', *keys)  # Exact generated fixture keys only, never FLUSHDB.
        docker('stop', '--time', '30', container)
        paths = [Path(__file__), ROOT / 'app/tests/resilience/resilience_fixture.py']
        helper = ROOT / 'app/tests/resilience/cache_expiry_oracle.py'
        if helper.exists():
            paths.append(helper)
        receipt['sources_sha256'] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        target.write_text(json.dumps(receipt, indent=2))
    print(json.dumps(dict(receipt=str(target), complete=receipt['complete'], reproduced=receipt.get('reproduced'),
                          error=receipt.get('error'))))
    raise SystemExit(0 if receipt['complete'] or args.baseline and receipt.get('reproduced') else 1)


if __name__ == '__main__':
    main()
