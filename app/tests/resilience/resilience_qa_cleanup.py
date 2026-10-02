# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Cancel only abandoned synthetic benchmark requests in this owned QA project.

Does not delete data, waive a failed load gate, or touch the main application.
"""
import json
from datetime import datetime,timezone
from pathlib import Path
import resilience_fixture as qa
import http_fixture as base

def main():
    qa.live()
    try:
        assert not base.docker('ps','-q').stdout.strip(),'QA_PROJECT_ALREADY_RUNNING'
        base.docker('up','-d','--no-build','--wait','--wait-timeout','480','postgres')
        code='''
import json
from rag_app import ledger
from rag_app.domain import Identity
with ledger.connect() as db:
 rows=db.execute("SELECT id,tenant,actor FROM requests WHERE question LIKE 'SYNTHETIC benchmark %' AND state IN ('ACCEPTED','RUNNING','RETRY_WAIT')").fetchall()
assert len(rows)<=1000,'OWNED_FIXTURE_BOUND'
for row in rows: ledger.cancel(Identity(row['tenant'],row['actor']),str(row['id']))
print(json.dumps({'cancelled_owned_synthetic_requests':len(rows),'main_requests_modified':0,'data_deleted':False,'failed_load_waived':False}))
'''
        result=base.docker('run','--rm','--no-deps','-T','api','python','-c',code)
        proof=json.loads(result.stdout.splitlines()[-1])
        path=base.ROOT.parent/'eval'/'runs'/(qa.PROJECT+'-cleanup-'+datetime.now(timezone.utc).strftime('%H%M%S')+'.json')
        path.write_text(json.dumps(proof,indent=2))
        print(json.dumps(proof))
    finally:
        base.docker('stop',check=False); qa.live()

if __name__=='__main__': main()
