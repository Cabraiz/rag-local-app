# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Real offered HTTP load, not 100k simultaneous accepted RAG workflows."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime,timezone
import http.client
import json
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4
import resilience_fixture as qa
import http_fixture as base

local=threading.local()

def main():
    project=sys.argv[1]; count=int(sys.argv[2]) if len(sys.argv)>2 else 100000
    if not project.startswith('rag-local-resilience-qa-') or not 100<=count<=100000:
        raise ValueError('EXCLUSIVE_QA_PROJECT_REQUIRED')
    base.COMPOSE=['docker','compose', '--project-directory', str(APP),'--ansi','never','--progress','plain','-p',project]
    for name in qa.FILES+['infrastructure/compose/qa/compose.resilience-load.yaml']: base.COMPOSE+=['-f',str(base.ROOT/name)]
    folder=base.ROOT.parent/'eval'/'runs'/(project+'-load-'+datetime.now(timezone.utc).strftime('%H%M%S'))
    folder.mkdir(exist_ok=True)
    question='SYNTHETIC benchmark '+str(uuid4())
    payload=json.dumps({'question':question}).encode()
    qa.live(); frozen=qa.hashes(); start=time.monotonic()
    receipt={'scope':'real HTTP offered load; free model disabled; two QA workers',
             'offered_target':count,'concurrency':32,'successful_100k_rag_workflows':False,
             'production_capacity_certificate':False,'source_hashes':frozen,'passed':False}
    states={}; ids=set(); unknown=[]; rejected=[]; durations=[]
    try:
        base.docker('up','-d','--no-build','--wait','--wait-timeout','480')
        ana=base.http('/v1/lab/session',body={'profile':'ana'})[1]['token']
        receipt['startup_seconds']=round(time.monotonic()-start,3)
        start=time.monotonic()
        def send(_):
            key=base.key(); started=time.monotonic()
            try:
                if not getattr(local,'conn',None): local.conn=http.client.HTTPConnection('127.0.0.1',8940,timeout=10)
                local.conn.request('POST','/v1/requests',body=payload,headers={'Content-Type':'application/json','Authorization':'Bearer '+ana,'Idempotency-Key':key})
                response=local.conn.getresponse(); code=response.status; data=json.loads(response.read())
                return code,data.get('request_id'),key,time.monotonic()-started,data.get('code')
            except Exception:
                if getattr(local,'conn',None): local.conn.close(); local.conn=None
                return 0,None,key,time.monotonic()-started,'CONNECTION_UNCONFIRMED'
        offered=0; completed=0
        with ThreadPoolExecutor(max_workers=32) as pool:
            running=set()
            while offered<count or running:
                while offered<count and len(running)<64:
                    running.add(pool.submit(send,offered)); offered+=1
                done,running=wait(running,return_when=FIRST_COMPLETED)
                for future in done:
                    code,rid,key,duration,error_code=future.result(); completed+=1
                    states[str(code)]=states.get(str(code),0)+1; durations.append(duration)
                    if code==202 and rid: ids.add(rid)
                    elif code not in (429,503) or error_code=='LEDGER_UNAVAILABLE_RETRY_SAME_KEY': unknown.append((key,code))
                    elif len(rejected)<20: rejected.append(key)
                if completed//10000>(completed-len(done))//10000:
                    print(json.dumps({'completed':completed,'http':states}),flush=True)
        elapsed=time.monotonic()-start
        if unknown: time.sleep(20)
        for key,code in unknown:
            status,value=base.http('/v1/requests/resolve',ana,{}, {'Idempotency-Key':key})
            if status==200: ids.add(value['request_id'])
            elif status!=404: raise AssertionError('UNRESOLVED_CONFIRMATION')
        def conservation():
            data=qa.inside('''
with ledger.connect() as db:
 rows=db.execute('SELECT id,state FROM requests WHERE question=%s',('''+repr(question)+''',)).fetchall()
print(json.dumps([dict(id=str(r['id']),state=r['state']) for r in rows]))
''')
            return data
        receipt.update(offered=completed,http=states,accepted_receipts=len(ids),
            confirmation_errors=len(unknown),offered_seconds=elapsed)
        try:
            qa.wait(lambda:all(r['state'] in ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED') for r in conservation()),seconds=240)
        except AssertionError:
            rows=conservation()
            receipt.update(accepted_sql=len(rows),unfinished=sum(r['state'] in ('ACCEPTED','RUNNING','RETRY_WAIT') for r in rows))
            raise
        rows=conservation(); actual={r['id'] for r in rows}
        durations.sort()
        receipt.update(offered=completed,http=states,accepted_receipts=len(ids),accepted_sql=len(actual),
                       terminal_states={state:sum(r['state']==state for r in rows) for state in sorted({r['state'] for r in rows})},
                       confirmation_errors=len(unknown),offered_seconds=elapsed,
                       latency_ms={p:round(durations[min(len(durations)-1,int(len(durations)*q))]*1000,2) for p,q in [('p50',.5),('p95',.95),('p99',.99)]},
                       rejected_sample_not_accepted=all(base.http('/v1/requests/resolve',ana,{}, {'Idempotency-Key':key})[0]==404 for key in rejected),
                       durable_ids_sha256=qa.hashlib.sha256('\n'.join(sorted(actual)).encode()).hexdigest())
        assert completed==count and ids==actual and receipt['rejected_sample_not_accepted']
        assert set(states)<={'202','429','503'},'UNEXPECTED_HTTP_OR_CONFIRMATION_ERROR'
        assert frozen==qa.hashes(),'SOURCE_CHANGED'
        receipt['images']=base.service_image_ids(); receipt['passed']=True
    except Exception as error:
        receipt['error_type']=type(error).__name__; receipt['error']=str(error)[:1500]
    finally:
        (folder/'offered-load.json').write_text(json.dumps(receipt,indent=2))
        base.docker('stop',check=False); qa.live()
    print(json.dumps({'passed':receipt['passed'],'receipt':str(folder/'offered-load.json'),'offered':receipt.get('offered'),'accepted':receipt.get('accepted_sql')}))
    if not receipt['passed']: raise SystemExit(1)

if __name__=='__main__': main()
