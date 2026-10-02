# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Read-only diagnostic of the synthetic IDs in one stopped fixture receipt."""
import json
from pathlib import Path
import sys
import http_fixture as base
base.COMPOSE+=['-f',str(base.ROOT/'infrastructure/compose/runtime/compose.retrieval.yaml')]
folder=Path(sys.argv[1]); records=[]
for path in folder.glob('progress-*.json'):
    value=json.loads(path.read_text()); records.append(value)
failed=next(r for r in records if r['checks'][-1]['passed'] is False)
rid=failed['ids'][-1]
if base.docker('ps','--status','running','-q').stdout.strip(): raise SystemExit('Project already running')
try:
    base.docker('up','-d','--wait','--wait-timeout','120','api','frontend')
    token=base.http('/v1/lab/session',body={'tenant':'demo-a'})[1]['token']
    public=base.http('/v1/requests/'+rid,token)[1]
    output=base.app_python("import json; from rag_app import ledger,corpus; rid="+repr(rid)+"\nwith ledger.connect() as db:\n r=db.execute('SELECT * FROM requests WHERE id=%s',(rid,)).fetchone(); head=db.execute(\"SELECT release_id FROM corpus_heads WHERE tenant='demo-a' AND actor='demo-user'\").fetchone(); docs=db.execute('SELECT id,revoked,valid_until,title FROM corpus_documents WHERE release_id=%s',(r['source_snapshot'],)).fetchall()\nevidence=corpus.retrieve(r); print(json.dumps({'request_id':rid,'state':r['state'],'raw_kind':r['result']['kind'],'snapshot':str(r['source_snapshot']),'head':str(head['release_id']),'docs':docs,'retrieved_ids':[str(c['id']) for c in evidence],'raw_result':r['result']},default=str))")
    report=dict(public=public,canonical=json.loads(output.splitlines()[-1]))
    (folder/'diagnostic.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(dict(diagnostic=str(folder/'diagnostic.json'),public_kind=public['result']['kind'],raw_kind=report['canonical']['raw_kind'],same_snapshot=report['canonical']['snapshot']==report['canonical']['head'],retrieved=len(report['canonical']['retrieved_ids']))))
finally: base.docker('stop')
