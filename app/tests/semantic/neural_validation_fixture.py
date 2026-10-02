# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Validate untrusted model responses with normal and optimized Python modes."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT=_workspace_root
CODE="""
import copy,json,sys
sys.path.insert(0,PATH)
from rag_app import neural_client,semantic_policy
from rag_app.domain import RequestError
import hashlib
from pathlib import Path
x=dict(model_version=neural_client.VERSION,calibration='office_faq_v1',holdout_used=False,calibration_sha256=neural_client.CALIBRATION_SHA256,policy_sha256=hashlib.sha256(Path(semantic_policy.__file__).read_bytes()).hexdigest(),threshold=.3,margin=.05,vectors=[[1.0]+[0.0]*383])
checks=[neural_client.validate(x,1)[0]==x['vectors']]
for key,value in [('model_version','bad'),('calibration','bad'),('holdout_used',True),('calibration_sha256','bad'),('policy_sha256','bad'),('threshold',float('nan')),('threshold',.2),('margin',.001),('vectors',[]),('vectors',[[1.0]*384]),('vectors',[[float('inf')]*384]),('vectors',[[1.0]+[0.0]*382])]:
 bad=copy.deepcopy(x); bad[key]=value
 try: neural_client.validate(bad,1); checks.append(False)
 except RequestError as e: checks.append(e.code=='EMBEDDING_RESPONSE_INVALID' and e.status==503)
print(json.dumps(dict(checks=checks,optimized=bool(sys.flags.optimize))))
""".replace('PATH',repr(str(ROOT/'app/src')))

def main():
    paths=[Path(__file__),ROOT/'app/src/rag_app/retrieval/neural_client.py',ROOT/'app/src/rag_app/retrieval/semantic_policy.py',ROOT/'app/src/rag_app/domain/domain.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}; rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        modes=[]
        for flag in ([],['-O']):
            r=subprocess.run([sys.executable,*flag,'-c',CODE],capture_output=True,text=True,timeout=15,shell=False,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            assert r.returncode==0,'Validation fixture process failed'
            modes.append(json.loads(r.stdout.strip().splitlines()[-1]))
        rounds.append(dict(seed=seed,modes=modes,passed=all(all(v['checks']) for v in modes)))
    passed=all(r['passed'] for r in rounds)
    folder=ROOT/'eval/runs'/('neural-validation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-038',evidence_type='verified_regression',complete=passed,consecutive_passes=2 if passed else 0,criteria_passed=['reproduction','two_regression_rounds'] if passed else [],sources_sha256=sources,rounds=rounds,scope='untrusted_embedding_output_validation_normal_and_optimized_Python',independent_blind=False,production_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8'); print(json.dumps(dict(receipt=str(path),streak=receipt['consecutive_passes'],optimized_accepted_invalid=sum(not c for c in rounds[0]['modes'][1]['checks']))))
    raise SystemExit(0 if passed else 1)

if __name__=='__main__': main()
