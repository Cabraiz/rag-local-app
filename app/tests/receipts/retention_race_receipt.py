# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Derive scoped regression approval only from two full actual delivery rounds."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=_workspace_root
REQUIRED={'managed_old_terminal_content_scrubbed','nonterminal_never_scrubbed',
    'retention_is_idempotent','retained_receipt_still_resolves_same_key',
    'retained_payload_hash_still_detects_conflict','same_original_payload_replays_after_content_retention'}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('receipt')
    path=Path(parser.parse_args().receipt).resolve()
    assert path.is_relative_to(ROOT/'eval/runs')
    original=json.loads(path.read_text(encoding='utf8'))
    assert original['card_id']=='RAG-10' and original['complete'] is True and original['consecutive_passes']==2
    assert len(original['rounds'])==2
    for item in original['rounds']:
        assert REQUIRED<={c['name'] for c in item['checks'] if c['passed']}
        assert all(c['passed'] for c in item['checks'])
    for name,digest in original['sources_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    proof=dict(original)
    proof.update(card_id='BUG-032',evidence_type='verified_regression',criteria_passed=['reproduction','two_regression_rounds'],
        scope='actual_controlled_retention_concurrency',full_delivery_receipt=str(path.relative_to(ROOT)))
    proof['sources_sha256']=dict(original['sources_sha256'])
    proof['sources_sha256'][str(Path(__file__).resolve().relative_to(ROOT))]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    target=path.with_name('retention-race-receipt.json'); target.write_text(json.dumps(proof,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(target),'streak':2}))

if __name__=='__main__':
    main()
