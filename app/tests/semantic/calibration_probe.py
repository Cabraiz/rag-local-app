# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Real offline calibration diagnostic. Never loads the reserved evaluation set."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess

ROOT=_workspace_root
CODE="""
import json
from engine import Encoder
import semantic_policy
x=json.load(open('/srv/calibration.json')); gates=json.load(open('/srv/gates.json'))
e=Encoder(); docs=x['documents']; queries=e.embed([semantic_policy.retrieval_query(r['question']) for r in x['cases']])
vectors=e.embed([r['title']+' '+r['text'] for r in docs]); cases=[]
for i,(case,query) in enumerate(zip(x['cases'],queries)):
 ranked=sorted([(sum(a*b for a,b in zip(query,vector)),doc['source_key']) for doc,vector in zip(docs,vectors) if semantic_policy.eligible(case['question'],doc['text'])],reverse=True)
 predicted=None
 if ranked and ranked[0][0]>=gates['threshold'] and (len(ranked)<2 or ranked[0][0]-ranked[1][0]>=gates['margin']): predicted=ranked[0][1]
 cases.append(dict(case=i,expected=case['expected'],predicted=predicted,passed=predicted==case['expected'],scores=ranked))
print(json.dumps(dict(cases=cases,gates=gates)),flush=True)
"""

def main():
    paths=[ROOT/'app/semantic/calibrate.py',ROOT/'app/src/rag_app/retrieval/semantic_policy.py',ROOT/'eval/datasets/semantic-calibration-v1.json',Path(__file__)]
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    folder=ROOT/'eval/runs'/('calibration-only-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    result=subprocess.run(['docker','run','--rm','--network','none','--read-only','--tmpfs','/tmp:size=64m','--entrypoint','python','rag-local-embedding:0.1.0','-c',CODE],capture_output=True,text=True,timeout=60,shell=False,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode: raise RuntimeError('Calibration diagnostic failed without exposing process output')
    data=json.loads(result.stdout.splitlines()[-1]); data.update(sources_sha256=sources,calibration_only=True,holdout_used=False)
    data['complete']=all(c['passed'] for c in data['cases']); path=folder/'receipt.json'
    path.write_text(json.dumps(data,indent=2),encoding='utf8')
    print(json.dumps(dict(receipt=str(path),correct=sum(c['passed'] for c in data['cases']),total=len(data['cases']),holdout_used=False)))

if __name__=='__main__': main()
