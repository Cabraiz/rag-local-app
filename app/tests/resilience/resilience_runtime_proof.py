# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Read-only proof of local rollout, without exposing document text or secrets."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT=_workspace_root
FOLDER=ROOT/'eval'/'resilience-runtime'

def sql(query):
    result=subprocess.run(['docker','exec','rag-local-v2-postgres-1','psql',
        '-U','rag_bootstrap','-d','rag','-At','-v','ON_ERROR_STOP=1','-c',query],
        capture_output=True,text=True,timeout=30,shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode: raise RuntimeError('READ_ONLY_SQL_PROOF_FAILED')
    return json.loads(result.stdout)

def digest(table):
    value=sql(f"SELECT COALESCE(json_agg(row_to_json(x) ORDER BY row_to_json(x)::text),'[]'::json) FROM {table} x")
    return {'count':len(value),'sha256':hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()}

def http(path,port=8840):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=8) as r:
        return json.load(r)

def container_json(container,code):
    result=subprocess.run(['docker','exec',container,'python','-c',code],
        capture_output=True,text=True,timeout=30,shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode: raise RuntimeError('LOCAL_SMOKE_COMMAND_FAILED')
    return json.loads(result.stdout.splitlines()[-1])

def smoke():
    # A separate synthetic tenant has NO corpus: Gemini.select returns before
    # reading its API key or reserving/invoking a model. Do not query real data.
    usage_code="import sqlite3,json; db=sqlite3.connect('file:/usage/gemini-probes.sqlite3?mode=ro',uri=True); print(json.dumps(db.execute('SELECT day,used FROM attempts ORDER BY day').fetchall()))"
    before=container_json('rag-local-v2-worker-1',usage_code)
    path=FOLDER/'main-smoke.json'
    if path.exists(): rid=json.loads(path.read_text())['request_id']
    else:
        rid=container_json('rag-local-v2-api-1',"import json,time; from uuid import uuid4; from rag_app import ledger; from rag_app.domain import Identity; who=Identity('resilience-smoke','proof'); print(json.dumps(ledger.accept(who,'SYNTHETIC resilience runtime smoke',str(int(time.time()))+'.'+str(uuid4()))))")
        path.write_text(json.dumps({'request_id':rid,'passed':False},indent=2))
    end=time.monotonic()+120
    while time.monotonic()<end:
        value=container_json('rag-local-v2-api-1',"import json; from rag_app import ledger; from rag_app.domain import Identity; r=ledger.read(Identity('resilience-smoke','proof'),"+repr(rid)+"); print(json.dumps({'state':r['state'],'kind':r['result']['kind'] if r['result'] else None}))")
        if value['state'] in ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED'): break
        time.sleep(1)
    after=container_json('rag-local-v2-worker-1',usage_code)
    result={'request_id':rid,**value,'no_model_calls':before==after,
        'passed':value['state']=='SUCCEEDED' and value['kind']=='ABSTAIN' and before==after}
    path.write_text(json.dumps(result,indent=2))
    return result

def main():
    FOLDER.mkdir(exist_ok=True)
    main_smoke=smoke() if sys.argv[1]=='after' else None
    current={'documents':digest('corpus_documents'),'heads':digest('corpus_heads'),
        'releases':digest('corpus_releases'),'chunks':digest('corpus_chunks'),
        'request_ids':sql("SELECT COALESCE(json_agg(id ORDER BY id),'[]'::json) FROM requests"),
        'ready':http('/health/ready')}
    if sys.argv[1]=='before':
        path=FOLDER/'before.json'
        if path.exists(): raise ValueError('BASELINE_ALREADY_EXISTS')
        path.write_text(json.dumps(current,indent=2))
        print(json.dumps({'baseline':str(path),'documents':current['documents']['count'],
            'requests':len(current['request_ids'])}))
        return
    before=json.loads((FOLDER/'before.json').read_text())
    checks={name:current[name]==before[name] for name in ('documents','heads','releases','chunks')}
    checks['old_requests_retained']=set(before['request_ids'])<=set(current['request_ids'])
    checks['actual_main_workflow_smoke_no_model']=main_smoke['passed']
    checks['partitioned_runtime']=current['ready'].get('admission_policy')=='partitioned_count_bytes_v3'
    grafana=http('/api/health',8850)
    dashboard=http('/api/dashboards/uid/rag-overview',8850)
    checks['grafana_healthy']=grafana['database']=='ok'
    checks['grafana_ten_panels_readonly']=len(dashboard['dashboard']['panels'])==10 and not dashboard['meta']['canEdit']
    data=http('/api/datasources/proxy/uid/rag-prometheus/api/v1/query?query=rag_requests',8850)
    checks['grafana_real_app_metrics']=data['status']=='success' and bool(data['data']['result'])
    checks['queue_budget_conserved']=sql("SELECT to_json((SELECT count(*) FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT'))=(SELECT sum(pending) FROM admission_shards))")
    checks['queue_bytes_conserved']=sql("SELECT to_json((SELECT COALESCE(sum(octet_length(question)),0) FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT'))=(SELECT sum(pending_bytes) FROM admission_shards))")
    receipt={'passed':all(checks.values()),'checks':checks,'main_smoke':main_smoke,'ready':current['ready'],
        'documents':current['documents']['count'],'previous_requests':len(before['request_ids']),
        'current_requests':len(current['request_ids']),'grafana':'http://127.0.0.1:8850/d/rag-overview/rag-local'}
    path=FOLDER/'after.json'; path.write_text(json.dumps(receipt,indent=2))
    print(json.dumps({'passed':receipt['passed'],'checks':checks,'receipt':str(path)}))
    if not receipt['passed']: raise SystemExit(1)

if __name__=='__main__': main()
