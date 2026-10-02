# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Two real retained-volume restore-fixture cycles; never live DB/HA approval."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = _workspace_root


def command(*args, timeout=30):
    value = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout,
                           shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if value.returncode:
        raise AssertionError('OWNED_RESTORE_COMMAND_FAILED')
    return value.stdout + value.stderr if args and args[0] == 'logs' else value.stdout


def ready():
    with urllib.request.urlopen('http://127.0.0.1:8840/health/ready', timeout=5) as response:
        assert response.status == 200, 'MAIN_LAB_NOT_READY'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project')
    args = parser.parse_args()
    project = args.project
    assert project.startswith('rag-local-resilience-qa-') and project[24:].isdigit(), 'EXCLUSIVE_QA_PROJECT_REQUIRED'
    container = project + '-restore-postgres-1'
    owned = command('ps', '--filter', 'label=com.docker.compose.project=' + project, '-q').strip()
    assert not owned, 'QA_PROJECT_MUST_BE_STOPPED'
    ready()
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
               for name in ('app/infrastructure/compose/qa/compose.reliability-qa.yaml', 'app/tests/resilience/restore_lifecycle_checks.py')}
    folder = ROOT / 'eval/runs' / ('restore-lifecycle-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(exist_ok=False)
    proof = dict(card_id='BUG-121', evidence_type='verified_regression', complete=False,
                 consecutive_passes=0, criteria_passed=[], sources_sha256=sources, rounds=[],
                 reproduction='eval/runs/rag-local-resilience-qa-20261002045001-reliability-130445/receipt.json',
                 cloud_calls=0, production_ha=False, scope='retained restore QA volume; main DB recovery SLO unchanged')

    def inspect():
        state = json.loads(command('inspect', container))[0]
        assert state['Config']['Labels']['com.docker.compose.project'] == project
        mounts = [m['Name'] for m in state['Mounts'] if m['Type'] == 'volume' and m['Destination'] == '/var/lib/postgresql']
        assert len(mounts) == 1 and mounts[0].startswith(project + '_'), 'UNOWNED_VOLUME'
        assert state['Config']['StopTimeout'] == 120
        assert state['Config']['Healthcheck']['StartPeriod'] == 300_000_000_000
        return state, mounts[0]

    def database_fingerprint():
        text = command('exec', container, 'psql', '-U', 'rag_bootstrap', '-d', 'postgres', '-At', '-c',
                       "SELECT datname FROM pg_database WHERE datname LIKE 'rag_restore_%' ORDER BY datname")
        return hashlib.sha256(text.encode()).hexdigest(), len(text.splitlines())

    baseline_volume = baseline_databases = None
    try:
        for number in (1, 2):
            state, volume = inspect()
            assert not state['State']['Running'], 'RESTORE_ALREADY_RUNNING'
            if baseline_volume is None:
                baseline_volume = volume
            assert volume == baseline_volume, 'RESTORE_VOLUME_REPLACED'
            started = time.monotonic()
            command('start', container)
            while True:
                state, volume = inspect()
                if state['State'].get('Health', {}).get('Status') == 'healthy':
                    break
                assert state['State']['Running'] and time.monotonic() - started < 480, 'RESTORE_SETUP_DEADLINE'
                time.sleep(1)
            elapsed = time.monotonic() - started
            databases = database_fingerprint()
            if baseline_databases is None:
                baseline_databases = databases
            assert databases == baseline_databases, 'RETAINED_RESTORE_DATABASES_CHANGED'
            assert databases[1] > 0, 'REAL_RESTORED_DATABASES_REQUIRED'
            durability = command('exec', container, 'psql', '-U', 'rag_bootstrap', '-d', 'postgres', '-At', '-c',
                                 'SHOW fsync; SHOW full_page_writes; SHOW synchronous_commit').splitlines()
            assert durability == ['on', 'on', 'on'], 'UNSAFE_RESTORE_DURABILITY'
            start_time = state['State']['StartedAt']
            command('stop', '--time', '120', container, timeout=150)
            state, after_volume = inspect()
            logs = command('logs', '--since', start_time, container)
            assert not state['State']['Running'] and state['State']['ExitCode'] == 0
            assert 'database system is shut down' in logs, 'UNCLEAN_RESTORE_SHUTDOWN'
            assert after_volume == baseline_volume and not state['State']['OOMKilled']
            ready()
            assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest for name, digest in sources.items())
            proof['rounds'].append(dict(number=number, passed=True, setup_seconds=round(elapsed, 3),
                                        retained_databases=databases[1], databases_sha256=databases[0],
                                        volume_preserved=True, shutdown_exit=0, clean_shutdown=True,
                                        main_lab_ready=True, live_recovery_deadline_seconds=60))
            print(json.dumps(dict(round=number, passed=True, setup_seconds=round(elapsed, 3))), flush=True)
        proof.update(complete=True, consecutive_passes=2, criteria_passed=['reproduction', 'two_regression_rounds'])
    except Exception as error:
        proof['error_type'] = type(error).__name__
        proof['error'] = str(error)[:150] if isinstance(error, AssertionError) else 'SANITIZED_LIFECYCLE_FAILURE'
    finally:
        state, _ = inspect()
        if state['State']['Running']:
            command('stop', '--time', '120', container, timeout=150)
        target = folder / 'receipt.json'
        target.write_text(json.dumps(proof, indent=2))
    print(json.dumps(dict(receipt=str(target), complete=proof['complete'])), flush=True)
    raise SystemExit(0 if proof['complete'] else 1)


if __name__ == '__main__':
    main()
