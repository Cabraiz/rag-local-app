# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Two actual offline ONNX calibration rounds for BUG-036, not retrieval approval."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import calibration_probe as fixture

ROOT=fixture.ROOT

def main():
    paths=[Path(__file__),Path(fixture.__file__),ROOT/'app/semantic/calibrate.py',ROOT/'app/src/rag_app/retrieval/semantic_policy.py',ROOT/'eval/datasets/semantic-calibration-v1.json']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    def image():
        return subprocess.check_output(['docker','image','inspect','rag-local-embedding:0.1.0','--format','{{.Id}}'],text=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0).strip()
    pinned=image(); rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        result=subprocess.run(['docker','run','--rm','--network','none','--read-only','--tmpfs','/tmp:size=64m','--entrypoint','python','rag-local-embedding:0.1.0','-c',fixture.CODE],capture_output=True,text=True,timeout=60,shell=False,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        assert result.returncode==0,'Offline model diagnostic failed'
        data=json.loads(result.stdout.splitlines()[-1]); assert len(data['cases'])==12 and all(c['passed'] for c in data['cases'])
        assert image()==pinned and all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in sources.items())
        rounds.append(dict(seed=seed,checks=data['cases'],gates=data['gates'],passed=True))
    folder=ROOT/'eval/runs'/('calibration-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-036',evidence_type='verified_regression',complete=True,consecutive_passes=2,criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,image=pinned,scope='real_OFFLINE_ONNX_calibration_conditional_and_approval',independent_blind=False,production_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8'); print(json.dumps(dict(receipt=str(path),checks_per_round=12,streak=2)))

if __name__=='__main__': main()
