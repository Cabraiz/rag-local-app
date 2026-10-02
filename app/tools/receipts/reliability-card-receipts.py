# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Generate scoped receipts only after two fresh rounds and local rollout.

Does not close cards or reinterpret blocked production/capacity work.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=_workspace_root
PREFIX='eval/runs/rag-local-resilience-qa-20261002045001-reliability-'
BASELINES={
    'BUG-110':('043925','recovery_partition_lock'),
    'BUG-111':('044620','worker_recovery_deadline'),
    'BUG-112':('050753','orphan_fk_lock'),
    'BUG-113':('051145','windows_receipt_paths'),
    'BUG-114':('051448','compose_migration_restart'),
    'BUG-115':('052602','sql_numeric_json_serialization'),
    'BUG-116':('054627','postgres_restart_restore_fixture_isolation'),
}


def read(path):
    path=Path(path).resolve()
    assert path.is_relative_to(ROOT/'eval'), 'EVIDENCE_OUTSIDE_EVAL'
    return json.loads(path.read_text()),path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--gate',required=True)
    parser.add_argument('--rollout',required=True)
    args=parser.parse_args()
    gate,gate_path=read(args.gate); rollout,rollout_path=read(args.rollout)
    assert gate['passed'] and gate['complete'] and gate['consecutive_passes']==2
    assert len(gate['rounds'])==2 and len({r['seed'] for r in gate['rounds']})==2
    assert rollout['passed'] and all(rollout['checks'].values())
    assert Path(rollout['gate']).resolve()==gate_path
    for round_ in gate['rounds']:
        assert round_['passed'] and len(round_['rag_checks'])==44
        assert all(check['passed'] for check in round_['rag_checks'])
        assert all(probe['passed'] for probe in round_['recovery_probes'])
        assert {p['phase'] for p in round_['phases']}=={
            'healthy','worker_kill','postgres_kill','broker_cache_down'}
        assert all(p['accepted']==128 and p['lost']==p['duplicate_terminals']==0
                   and p['success_fraction']>=.99 for p in round_['phases'])
        assert all(p['phase']!='postgres_kill' or (p['postgres_recovery']['passed']
                   and p['postgres_recovery']['elapsed_seconds']<60) for p in round_['phases'])
        assert all(p['phase']!='worker_kill' or p['worker_resumption']['running']==2
                   for p in round_['phases'])
        assert round_['migration_restart']['passed'] and round_['restore']['passed']
        assert round_['restore']['separate_instance']
        assert round_['confirmation_edges']['discarded_confirmation_reconciled']
        assert round_['confirmation_edges']['database_outage_not_falsely_accepted']
    sources=gate['source_hashes'].copy()
    sources[Path(__file__).relative_to(ROOT).as_posix()]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for name,digest in sources.items():
        source=(ROOT/name).resolve()
        assert source.is_relative_to(ROOT)
        assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    for card,(suffix,case) in BASELINES.items():
        baseline_path=ROOT/(PREFIX+suffix)/'receipt.json'
        baseline=json.loads(baseline_path.read_text())
        assert baseline['passed'] is False
        if card in ('BUG-110','BUG-112'):
            assert baseline['recovery_probe']['passed'] is False
            assert baseline['recovery_probe']['independent_recovered'] is False
        elif card=='BUG-111':
            assert baseline['error']=='WAIT_DEADLINE'
            accepted=json.loads((baseline_path.parent/'285534-worker_kill-accepted.json').read_text())
            assert len(accepted)==128
        elif card=='BUG-113':
            assert baseline['error_type']=='KeyError' and 'app/src/rag_app/persistence/ledger.py' in baseline['error']
        elif card=='BUG-114':
            assert baseline['error_type']=='RuntimeError' and 'migrate' in baseline['error']
        elif card=='BUG-115':
            assert baseline['error_type']=='RuntimeError' and 'Decimal' in baseline['error']
        else:
            assert baseline['error']=='WAIT_DEADLINE'
            assert len(json.loads((baseline_path.parent/'328526-postgres_kill-accepted.json').read_text()))==128
        value=dict(card_id=card,evidence_type='verified_regression',complete=True,
                   consecutive_passes=2,criteria_passed=['reproduction','two_regression_rounds'],
                   sources_sha256=sources,reproduction=dict(case=case,receipt=str(baseline_path)),
                   regression_receipt=str(gate_path),local_rollout_receipt=str(rollout_path),
                   seeds=[r['seed'] for r in gate['rounds']],production_ha=False,
                   independent_blind_audit=False,gemini_calls=0)
        path=gate_path.parent/(card.lower()+'-receipt.json')
        path.write_text(json.dumps(value,indent=2))
        print(json.dumps(dict(card=card,receipt=str(path))))


if __name__=='__main__': main()
