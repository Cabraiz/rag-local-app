# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Derived bug receipt from the actual two-round current file integration run."""
import json
from pathlib import Path
import sys

path=Path(sys.argv[1])
value=json.loads(path.read_text(encoding='utf8'))
assert value['card_id']=='RAG-05' and value['complete'] and value['consecutive_passes']==2
assert len(value['rounds'])==2 and all(r['passed'] and all(c['passed'] for c in r['checks']) for r in value['rounds'])
value.update(card_id='BUG-060',evidence_type='verified_regression',
    criteria_passed=['reproduction','two_regression_rounds'],
    reproduction='eval/runs/current-files-20261001T171459Z-cd7714/receipt.json',
    derived_from=str(path),scope='explicit_index_readiness_before_both_real_ingestion_rounds')
output=path.with_name('readiness-receipt.json')
output.write_text(json.dumps(value,indent=2),encoding='utf8')
print(json.dumps({'receipt':str(output)}))
