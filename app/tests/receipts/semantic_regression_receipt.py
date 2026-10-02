# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Approve repair-query bug only after two current full real neural slice passes."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=_workspace_root

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('receipt'); path=Path(parser.parse_args().receipt).resolve()
    assert path.is_relative_to(ROOT/'eval/runs')
    value=json.loads(path.read_text(encoding='utf8'))
    assert value['card_id']=='RAG-06' and value['complete'] is True and value['consecutive_passes']==2
    assert len(value['rounds'])==2
    for r in value['rounds']:
        assert all(c['passed'] for c in r['checks'])
        assert len(r['reserved_cases'])==28 and all(c['passed'] for c in r['reserved_cases'])
        assert any(c['case']==7 and c['expected_source']=='repair' and c['observed_kind']=='EXTRACTIVE' for c in r['reserved_cases'])
        assert any(c['case']==21 and c['expected_source']=='repair' and c['observed_kind']=='EXTRACTIVE' for c in r['reserved_cases'])
    assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in value['sources_sha256'].items())
    value.update(card_id='BUG-037',evidence_type='verified_regression',criteria_passed=['reproduction','two_regression_rounds'],full_semantic_receipt=str(path.relative_to(ROOT)))
    value['sources_sha256'][str(Path(__file__).relative_to(ROOT))]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    target=path.with_name('repair-regression-receipt.json'); target.write_text(json.dumps(value,indent=2),encoding='utf8'); print(json.dumps(dict(receipt=str(target),streak=2)))

if __name__=='__main__': main()
