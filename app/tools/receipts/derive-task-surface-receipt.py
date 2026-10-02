# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Derive only BUG-117 from the executed real two-round SDK task gate."""
import hashlib
import json
from pathlib import Path
import sys

root = _workspace_root
path = Path(sys.argv[1]).resolve()
assert path.is_relative_to(root / 'eval/runs')
proof = json.loads(path.read_text())
assert proof['card_id'] == 'RAG-07' and proof['complete'] and proof['consecutive_passes'] == 2
assert proof['sdk_proof']['complete'] and len(proof['sdk_proof']['rounds']) == 2
required = {'unauthorized_tool_rejected_before_model', 'actual_restricted_tool_reads',
            'real_model_task_completed', 'checkpoint_persists_after_adapter_closed',
            'real_mid_task_cancellation'}
for round_ in proof['sdk_proof']['rounds']:
    assert round_['passed'] and required <= {c['name'] for c in round_['checks'] if c['passed']}
for name, digest in proof['sources_sha256'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
baseline = root / 'eval/runs/advanced-task-20261002T114828Z/receipt.json'
original = json.loads(baseline.read_text())
assert not original['complete'] and original['sdk_proof']['error']['code'] == 'TASK_TOOL_SURFACE_NOT_AUTHORIZED'
proof.update(card_id='BUG-117', evidence_type='verified_regression',
             criteria_passed=['reproduction', 'two_regression_rounds'],
             reproduction=str(baseline), regression=str(path))
proof['sources_sha256'][Path(__file__).relative_to(root).as_posix()] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
target = path.with_name('bug-117-receipt.json')
target.write_text(json.dumps(proof, indent=2))
print(json.dumps(dict(receipt=str(target))))
