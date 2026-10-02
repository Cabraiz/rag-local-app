# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Read-only sanitized blockers/runtime snapshot; no provider writes or LLM calls."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0,str((_workspace_root / "app")/'tests'))
import functional_smoke as api

root=_workspace_root
queue=json.loads((root/'.local/card-execution/progress.json').read_text())
report={'at':datetime.now(timezone.utc).isoformat(),
        'completed_cards':queue['completedCards'],'total':queue['progressTotal'],
        'pending':[{'id':c['id'],'status':c['status'],'reason':c['reason']} for c in queue['cards']
                   if c['status'] not in ('DONE','WITHDRAWN_USER_PAUSE')],
        'cloud_calls':0,'remote_writes':0}
code,ready=api.http('/health/ready')
report['ready']={'http':code,'mode':ready.get('mode'),'workflow':ready.get('workflow'),
                 'production_ready':ready.get('production_ready')}
code,session=api.http('/v1/lab/session',body={'profile':'bruno'})
if code==200:
    token=session['token']
    code,feed=api.http('/v1/lab/integrations/feed',token)
    report['feed_http']=code
    report['providers']=[{key:p.get(key) for key in ('id','state','complete','stale','reason','last_success')}
                         | {'items':len(p.get('items',[]))} for p in feed.get('providers',[])]
    token=''
target=root/'eval/runs'/('backlog-status-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
target.write_text(json.dumps(report,indent=2))
print(json.dumps({'completed_cards':report['completed_cards'],'total':report['total'],
                  'pending':[c['id'] for c in report['pending']], 'ready':report['ready'],
                  'providers':report.get('providers',[]),'receipt':str(target)}))
