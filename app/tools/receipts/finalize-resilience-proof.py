# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Summarize fresh local checks; never collect credentials or document text."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT=_workspace_root
sys.path.insert(0,str(ROOT/'app'/'tests'))
import resilience_runtime_proof as runtime

QA=ROOT/'eval/runs/rag-local-resilience-qa-20261001181053-run-191638/receipt.json'
LOAD=ROOT/'eval/runs/rag-local-resilience-qa-20261001181053-load-192309/offered-load.json'

def docker(*args):
    r=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=30,
        shell=False,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if r.returncode: raise RuntimeError('LOCAL_CHECK_COMMAND_FAILED')
    return r.stdout.strip()

def main():
    qa=json.loads(QA.read_text()); load=json.loads(LOAD.read_text())
    actual=json.loads((ROOT/'eval/resilience-runtime/after.json').read_text())
    checks={'two_clean_frozen_rounds':qa['passed'] and len(qa['rounds'])==2,
        'offered_load_conserved':load['passed'] and load['offered']==100000,
        'live_data_and_workflow_proof':actual['passed']}
    for tag,receipt in (('qa',qa),('load',load)):
        checks[tag+'_source_unchanged']=all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==h
            for p,h in receipt['source_hashes'].items())
    names=['api','worker-1','worker-2','relay','control','delivery','frontend',
        'postgres','qdrant','embeddings','integration-gateway','collector',
        'redis','rabbitmq','prometheus','grafana']
    containers=[]
    for name in names:
        full='rag-local-v2-'+name if name.startswith('worker-') else 'rag-local-v2-'+name+'-1'
        fields=docker('inspect',full,'--format','{{.Name}}|{{.Image}}|{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}').split('|')
        containers.append(dict(name=fields[0],image=fields[1],running=fields[2]=='true',restart=fields[3],health=fields[4]))
    checks['containers_running_and_externally_supervised']=all(c['running'] and c['restart']=='unless-stopped' and c['health'] in ('healthy','none') for c in containers)
    checks['tested_backend_deployed']=all(c['image'] in qa['images'] and c['image'] in load['images']
        for c in containers if c['name'].split('-v2-')[1] in ('api-1','worker-1','worker-2','relay-1','control-1','delivery-1'))
    providers=json.loads(docker('exec','rag-local-v2-integration-gateway-1','python','-c',
        "import json; from rag_app.feed_server import snapshot; print(json.dumps([{'provider':p['id'],'state':p['state'],'stale':p['stale'],'reason':p['reason']} for p in snapshot()['providers']]))"))
    checks['existing_remote_read_integrations_healthy']=all(p['state']=='connected' and not p['stale'] for p in providers)
    up=runtime.http('/api/datasources/proxy/uid/rag-prometheus/api/v1/query?query=up',8850)
    targets=[dict(job=r['metric']['job'],up=r['value'][1]=='1') for r in up['data']['result']]
    checks['prometheus_targets_up']=bool(targets) and all(t['up'] for t in targets)
    rid=actual['main_smoke']['request_id']
    from uuid import UUID
    rid=str(UUID(rid))
    smoke_transport=runtime.sql("SELECT json_build_object('broker_confirmed',j.broker_fence>=0 AND j.broker_at IS NOT NULL,'terminal_audits',(SELECT count(*) FROM audit a WHERE a.request_id=r.id AND a.kind='TERMINAL'),'terminal_notification_delivered',EXISTS(SELECT 1 FROM outbox o JOIN notification_inbox i ON i.outbox_id=o.id WHERE o.request_id=r.id AND o.kind='TERMINAL')) FROM requests r JOIN jobs j ON j.id=r.id WHERE r.id='"+rid+"'")
    checks['main_terminal_audit_and_outbox_delivery']=smoke_transport['terminal_audits']==1 and smoke_transport['terminal_notification_delivered']
    with urllib.request.urlopen('http://127.0.0.1:8840/',timeout=5) as r:
        page=r.read().decode()
    checks['frontend_contains_grafana_link']='http://127.0.0.1:8850/d/rag-overview/rag-local' in page
    receipt={'passed':all(checks.values()),'at_utc':datetime.now(timezone.utc).isoformat(),
        'checks':checks,'containers':containers,'providers':providers,'prometheus_targets':targets,
        'qa_receipt':str(QA),'rounds':[dict(seed=r['seed'],checks=len(r['checks']),passed=r['passed']) for r in qa['rounds']],
        'load_receipt':str(LOAD),'offered':load['offered'],'accepted_terminal':load['accepted_sql'],
        'explicitly_rejected':sum(load['http'].get(k,0) for k in ('429','503')),
        'http_latency_ms':load['latency_ms'],'main_data_proof':str(ROOT/'eval/resilience-runtime/after.json'),
        'main_smoke_transport':smoke_transport,'documents_retained':actual['documents'],
        'old_requests_retained':actual['previous_requests'],'qa_gemini_calls':0,
        'main_smoke_no_model_calls':actual['main_smoke']['no_model_calls'],
        'limits':{'production_ha':False,'independent_blind_audit':False,'100000_simultaneous_successful_rag_workflows':False,
            'paid_fallback_enabled':False,'remote_alert_notifications_configured':False,'grafana_log_or_trace_datasource_added':False},
        'grafana':'http://127.0.0.1:8850/d/rag-overview/rag-local',
        'app':'http://127.0.0.1:8840/'}
    path=ROOT/'eval/resilience-final-receipt.json'
    path.write_text(json.dumps(receipt,indent=2))
    print(json.dumps({'passed':receipt['passed'],'checks':checks,'receipt':str(path)}))
    if not receipt['passed']: raise SystemExit(1)

if __name__=='__main__': main()
