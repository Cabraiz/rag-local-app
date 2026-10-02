# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Supplemental restart check inside an already-owned temporary HTTP fixture.
Never starts a server. Requires --inside-owned-fixture and the active fixture run.
"""
import json
from pathlib import Path
import re
import sys
import time
import http_fixture as base
from http_fixture import docker, http, key, wait_state, app_python

if len(sys.argv)!=3 or sys.argv[1]!='--inside-owned-fixture':
    raise SystemExit('Refuse execution without explicit temporary fixture context')
folder=Path(sys.argv[2]).resolve()
if '-p' not in base.COMPOSE or base.COMPOSE.index('-p')+1>=len(base.COMPOSE):
    raise SystemExit('Refuse restart without an isolated QA project')
project=base.COMPOSE[base.COMPOSE.index('-p')+1]
if not re.fullmatch(r'rag-local-(?:qa|resilience-qa)-[a-z0-9-]+',project) or base.BASE!='http://127.0.0.1:8940':
    raise SystemExit('Refuse restart outside the isolated QA runtime')
if not folder.is_relative_to(_workspace_root/'.local') or not (folder/'contract.json').is_file() or (folder/'receipt.json').exists():
    raise SystemExit('Refuse unknown or already finished fixture')
contract=json.loads((folder/'contract.json').read_text(encoding='utf8'))
if contract.get('isolated_project')!=project or contract.get('endpoint')!=base.BASE:
    raise SystemExit('Refuse restart when fixture ownership does not match')
rounds=[]
try:
    a=http('/v1/lab/session',body={'profile':'ana'})[1]['token']
    for number in (1,2):
        docker('stop','worker')
        try:
            idem=key()
            status,row=http('/v1/requests',a,{'question':'SYNTHETIC PostgreSQL restart '+str(number)},{'Idempotency-Key':idem})
            assert status==202
            rid=row['request_id']
            docker('restart','postgres')
            end=time.monotonic()+30
            while True:
                try:
                    if http('/health/ready')[0]==200: break
                except Exception: pass
                if time.monotonic()>end: raise AssertionError('PostgreSQL readiness did not recover')
                time.sleep(.2)
            code,restored=http('/v1/requests/'+rid,a)
            assert code==200 and restored['state']=='ACCEPTED'
            code,again=http('/v1/requests',a,{'question':'SYNTHETIC PostgreSQL restart '+str(number)},{'Idempotency-Key':idem})
            assert code==202 and again['request_id']==rid
        finally:
            docker('start','worker')
        final=wait_state(rid,a,{'SUCCEEDED'})
        assert final['result']['kind']=='ABSTAIN'
        raw=app_python("from rag_app import ledger; import json; db=ledger.connect(); counts=db.execute(\"SELECT (SELECT COUNT(*) FROM audit WHERE request_id=%s AND kind='TERMINAL') AS a,(SELECT COUNT(*) FROM outbox WHERE request_id=%s AND kind='TERMINAL') AS o\",('"+rid+"','"+rid+"')).fetchone(); print(json.dumps(counts)); db.close()")
        assert json.loads(raw)=={'a':1,'o':1}
        rounds.append(dict(round=number,request_id=rid,checks=5,passed=True))
    error=None
except Exception as exc:
    error=str(exc)
finally:
    docker('unpause','worker',check=False)
    (folder/'restart-checks.json').write_text(json.dumps(dict(rounds=rounds,error=error,scope='Real PostgreSQL container restart, NOT host disk loss/HA/PITR'),indent=2),encoding='utf8')
print(json.dumps(dict(rounds=len(rounds),error=error)),flush=True)
raise SystemExit(1 if error else 0)
