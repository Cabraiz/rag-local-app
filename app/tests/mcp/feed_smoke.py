# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Live app/gateway checks; a Jira auth failure remains a failed online gate."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import time
import functional_smoke as smoke

ROOT=_workspace_root
def hashes():
    names=['app/src/rag_app/'+n+'.py' for n in ('remote_feed','feed_server','feed_client','api')]
    names+=['app/infrastructure/compose/runtime/compose.integrations.yaml','frontend/public/app.js','frontend/public/index.html','frontend/public/styles.css']
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}


def run():
    checks=[]
    def check(name,value):
        assert value,name;checks.append(name)
    ana=smoke.http('/v1/lab/session',body={'profile':'ana'})[1]['token']
    bruno=smoke.http('/v1/lab/session',body={'profile':'bruno'})[1]['token']
    for path,body in [('/v1/lab/integrations/feed',None),('/v1/lab/integrations/refresh',{})]:
        check('ana_denied_'+path,smoke.http(path,ana,body)[0]==403)
        check('anonymous_denied_'+path,smoke.http(path,None,body)[0] in (401,403))
    code,data=smoke.http('/v1/lab/integrations/feed',bruno)
    check('mcp_gateway_real_response',code==200 and data['verification']=='live_remote_mcp')
    check('bounded_read_only_contract',data['max_items_per_provider']==500 and data['poll_seconds']==60)
    check('no_false_rag_or_production_claim',data['rag_answer_workflow_connected'] is False and data['production_certified'] is False)
    providers={p['id']:p for p in data['providers']}
    gh=providers['github'];jira=providers['jira']
    check('github_live_authenticated',gh['state']=='connected' and gh['last_success'] and not gh['stale'])
    check('github_scope',gh['scope']=='Cabraiz/rag-mcp-lab' and all(i['url']==f"https://github.com/Cabraiz/rag-mcp-lab/pull/{i['id']}" for i in gh['items']))
    check('jira_error_is_explicit',jira['state']=='connected' or jira['state']=='error' and jira['reason'] in ('REMOTE_401','REMOTE_403','TOKEN_LOCAL_EXPIRY'))
    check('jira_no_stale_data_on_auth_failure',jira['state']=='connected' or jira['items']==[])
    check('timestamps_real_and_current',all(p['last_attempt'] for p in providers.values()))
    check('jql_source_fixed',jira['scope']=='KAN')
    check('jira_real_cards_loaded',jira['state']!='connected' or {'KAN-1','KAN-2','KAN-3'}<={i['id'] for i in jira['items']})
    check('jira_timestamp_not_fabricated',all(i.get('updated_at') is None or datetime.fromisoformat(i['updated_at']).tzinfo is not None for i in jira['items']))
    return {'checks':checks,'github_items':len(gh['items']),'github_complete':gh['complete'],
            'jira_ids':[i['id'] for i in jira['items']],
            'source_gates':{'github':'passed','jira':'passed' if jira['state']=='connected' else 'blocked:'+jira['reason']}}


if __name__=='__main__':
    frozen=hashes();rounds=[];error=None
    try:
        for _ in range(2):rounds.append(run());assert hashes()==frozen,'SOURCE_CHANGED'
    except Exception as exc:error=str(exc)
    receipt={'at':datetime.now(timezone.utc).isoformat(),'checks_passed':error is None and len(rounds)==2,'rounds':rounds,
             'complete_online_gate':error is None and len(rounds)==2 and all(r['source_gates']['jira']=='passed' for r in rounds),
             'error':error,'source_sha256':frozen,'scope':'live MCP feed and role boundaries; not independent blind audit or production certification'}
    file=ROOT/'eval/runs/remote-feed-20261001'/('live-'+datetime.now(timezone.utc).strftime('%H%M%S')+'.json')
    file.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({**{k:receipt[k] for k in ('checks_passed','complete_online_gate','error')},'round_checks':[len(r['checks']) for r in rounds],
                     'source_gates':[r['source_gates'] for r in rounds],'receipt':str(file)}))
    raise SystemExit(0 if receipt['checks_passed'] else 1)
