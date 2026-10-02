# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Temporary container integration fixture. Own project only; no persistent server.

Preserves volumes/results. Stops only containers this fixture started, in finally.
Does not approve Gate B or simulate 100k concurrent HTTP requests.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import secrets
import subprocess
import sys
import time
import urllib.request
import urllib.error
from uuid import uuid4

ROOT = (_workspace_root / "app")
BASE = 'http://127.0.0.1:8840'
COMPOSE = ['docker','compose', '--project-directory', str(APP),'-f',str(ROOT/'infrastructure/compose/runtime/compose.yaml')]


def docker(*args, check=True):
    result = subprocess.run(COMPOSE+list(args),capture_output=True,text=True,timeout=180,
        shell=False,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if check and result.returncode:
        # Compose errors contain no generated secret values; keep output short.
        raise RuntimeError(result.stderr[-1800:])
    return result


def http(path, token=None, body=None, headers=None):
    headers = dict(headers or {})
    if token: headers['Authorization'] = 'Bearer '+token
    if body is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(body).encode()
    request = urllib.request.Request(BASE+path,data=body,headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=15) as response:
            return response.status,json.loads(response.read())
    except urllib.error.HTTPError as error:
        payload = error.read()
        try: value = json.loads(payload)
        except ValueError: value = {'code':'PROXY_HTTP_'+str(error.code)}
        return error.code,value


def key(): return str(int(time.time()))+'.'+str(uuid4())


def service_image_ids():
    # Only services declared in THIS composed profile. Stopped orphan profiles may
    # reference an old image that Desktop has already removed; they are not this run.
    services=docker('config','--services').stdout.strip().splitlines()
    ids=docker('ps','-a','-q',*services).stdout.strip().splitlines()
    if not ids:
        raise RuntimeError('No owned profile containers to freeze')
    result=subprocess.run(['docker','container','inspect','--format','{{.Image}}',*ids],
        capture_output=True,text=True,timeout=30,shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode:
        raise RuntimeError('Owned profile image inspection failed')
    return sorted(set(result.stdout.strip().splitlines()))


def source_hashes():
    paths = [ROOT/'infrastructure/compose/runtime/compose.yaml', ROOT/'infrastructure/images/backend/Dockerfile', ROOT/'infrastructure/dependencies/backend/requirements.lock']
    paths += list((ROOT/'src').rglob('*.py')) + list((ROOT/'src').rglob('*.sql'))
    paths += [ROOT/'tests/shared/http_fixture.py']
    paths += [p for p in (ROOT.parent/'frontend').rglob('*') if p.is_file()]
    return {str(p.relative_to(ROOT.parent)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def wait_state(rid, token, wanted):
    end = time.monotonic()+35
    while time.monotonic()<end:
        code,row = http('/v1/requests/'+rid,token)
        if code==200 and row['state'] in wanted: return row
        time.sleep(0.2)
    raise AssertionError('Terminal/expected state not reached: '+rid)


def app_python(code):
    return docker('exec','-T','api','python','-c',code).stdout.strip()


def run_round(seed):
    rng = random.Random(seed)
    checks,ids = [],[]
    def check(name, ok):
        checks.append({'name':name,'pass':bool(ok)})
        if not ok: raise AssertionError(name)
    a = http('/v1/lab/session',body={'tenant':'demo-a'})[1]['token']
    b = http('/v1/lab/session',body={'tenant':'demo-b'})[1]['token']
    check('unauthenticated_status_denied',http('/v1/requests')[0] in (401,403))
    check('bad_token_denied',http('/v1/requests','not.a.token')[0]==401)
    idem = key()
    question = 'SYNTHETIC fixture '+str(seed)
    def duplicate(_): return http('/v1/requests',a,{'question':question},{'Idempotency-Key':idem})
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(duplicate,range(16)))
    check('16_concurrent_duplicates_same_committed_receipt',all(c==202 for c,_ in results) and len({r['request_id'] for _,r in results})==1)
    rid = results[0][1]['request_id']; ids.append(rid)
    check('different_payload_same_key_conflicts',http('/v1/requests',a,{'question':'DIFFERENT_SYNTHETIC'},{'Idempotency-Key':idem})[0]==409)
    check('cross_tenant_read_denied',http('/v1/requests/'+rid,b)[0]==404)
    check('cross_tenant_cancel_denied',http('/v1/requests/'+rid+'/cancel',b,{})[0]==404)
    check('resolve_after_lost_confirmation',http('/v1/requests/resolve',a,{}, {'Idempotency-Key':idem})[1]['request_id']==rid)
    check('extra_tenant_field_rejected',http('/v1/requests',a,{'question':question,'tenant':'demo-b'},{'Idempotency-Key':key()})[0]==422)
    old = str(int(time.time())-604900)+'.'+str(uuid4())
    check('old_unmapped_key_rejected',http('/v1/requests',a,{'question':question},{'Idempotency-Key':old})[0]==409)
    check('oversized_body_rejected',http('/v1/requests',a,{'question':'X'*20000},{'Idempotency-Key':key()})[0]==413)
    row = wait_state(rid,a,{'SUCCEEDED'})
    check('adk_returns_explicit_abstention_no_fake_citations',row['result']['kind']=='ABSTAIN' and row['result']['citations']==[])
    # Controlled unavailability avoids SIGSTOP freezing an arbitrary SQL lock.
    # Frozen transactions are exercised separately in segment_fixture.py.
    docker('stop','worker')
    try:
        def submit(label):
            code,row = http('/v1/requests',a,{'question':'SYNTHETIC '+label},{'Idempotency-Key':key()})
            check(label+'_accepted',code==202)
            ids.append(row['request_id'])
            return row['request_id']
        cancel_id = submit('cancel_'+str(seed))
        check('cancel_idempotent_with_inference_unavailable',http('/v1/requests/'+cancel_id+'/cancel',a,{})[1]['state']=='CANCELLED' and http('/v1/requests/'+cancel_id+'/cancel',a,{})[1]['state']=='CANCELLED')
        expire_id = submit('expire_'+str(seed))
        app_python("from rag_app import ledger; db=ledger.connect(); db.execute(\"UPDATE requests SET deadline=clock_timestamp()-interval '1 second' WHERE id=%s\",('"+expire_id+"',)); db.commit(); db.close()")
        check('control_expires_while_worker_unavailable',wait_state(expire_id,a,{'EXPIRED'})['attempts']==0)
        lease_id = submit('lease_'+str(seed))
        app_python("from rag_app import ledger; db=ledger.connect(); db.execute(\"UPDATE requests SET state='RUNNING',fence=1,attempts=1,lease_until=clock_timestamp()-interval '1 second' WHERE id=%s\",('"+lease_id+"',)); db.commit(); db.close()")
        wait_state(lease_id,a,{'RETRY_WAIT'})
        check('lease_recovered_without_original_worker',True)
        raw = app_python("from rag_app import ledger; from rag_app.domain import Proposal; db=ledger.connect(); row=db.execute(\"UPDATE requests SET state='RUNNING',fence=fence+1,lease_until=clock_timestamp()+interval '30 seconds' WHERE id=%s RETURNING *\",('"+lease_id+"',)).fetchone(); db.commit(); db.close(); old=dict(row,fence=1); print(ledger.finish(old,Proposal('ABSTAIN','SYNTHETIC'))); print(ledger.finish(row,Proposal('ABSTAIN','SYNTHETIC')))")
        check('real_sql_stale_fence_rejected_new_owner_commits',raw.splitlines()==['False','True'])
    finally:
        docker('start','worker')
    # Verify SQL conservation for ONLY these synthetic IDs, including atomic audit/outbox.
    code = "from rag_app import ledger; import json; db=ledger.connect(); ids="+repr(ids)+"; rows=db.execute(\"SELECT id::text,state FROM requests WHERE id=ANY(%s::uuid[])\",(ids,)).fetchall(); audits=db.execute(\"SELECT COUNT(*) AS n FROM audit WHERE request_id=ANY(%s::uuid[]) AND kind='TERMINAL'\",(ids,)).fetchone()['n']; events=db.execute(\"SELECT COUNT(*) AS n FROM outbox WHERE request_id=ANY(%s::uuid[]) AND kind='TERMINAL'\",(ids,)).fetchone()['n']; print(json.dumps(dict(rows=rows,audits=audits,events=events))); db.close()"
    durable = json.loads(app_python(code))
    check('all_accepted_ids_terminal_and_one_audit_outbox_each',len(durable['rows'])==len(ids) and all(r['state'] in ('SUCCEEDED','CANCELLED','EXPIRED','FAILED_FINAL') for r in durable['rows']) and durable['audits']==durable['events']==len(ids))
    random_delay = rng.random()/10
    time.sleep(random_delay)
    check('poll_remains_authorized_after_terminal',http('/v1/requests/'+rid,a)[0]==200 and http('/v1/requests/'+rid,b)[0]==404)
    return dict(seed=seed,checks=checks,passed=len(checks),unique_accepted=len(ids),ids=ids)


def main():
    active = docker('ps','--status','running','-q').stdout.strip()
    if active: raise RuntimeError('Refuse fixture ownership: project is already running')
    folder = Path('D:/RAG-Local/eval/runs') / ('http-slice-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True)
    seeds=[secrets.randbits(32),secrets.randbits(32)]
    frozen = source_hashes()
    image_result = subprocess.run(['docker','image','inspect','rag-local-backend:0.1.0','rag-local-frontend:0.1.0','--format','{{.Id}}'],capture_output=True,text=True,check=True,shell=False,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    images = image_result.stdout.strip().splitlines()
    contract=dict(seeds=seeds,source_sha256=frozen,images=images,scope='Real HTTP/Postgres/ADK small lifecycle fixture, NOT load/HA/RAG quality',production_ready=False)
    (folder/'contract.json').write_text(json.dumps(contract,indent=2),encoding='utf8')
    rounds=[]; error=None
    try:
        docker('up','-d','--wait','--wait-timeout','120')
        for seed in seeds:
            result=run_round(seed); rounds.append(result)
            (folder/f'round-{seed}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
            print(json.dumps(dict(seed=seed,passed=result['passed'],unique_accepted=result['unique_accepted'])),flush=True)
        code,health=http('/health/ready')
        assert code==200 and health['production_ready'] is False
        no_sdk=app_python("from rag_app.bootstrap import process_services; import sys; process_services('control'); print('google.adk' in sys.modules)")
        assert no_sdk=='False'
        denied=docker('exec','-T','-e','RAG_MODE=production','api','python','-c','from rag_app.config import enforce_lab; enforce_lab()',check=False)
        assert denied.returncode!=0 and 'Production disabled' in denied.stderr
        assert source_hashes()==frozen, 'Source changed during test rounds'
        if '--visual-handoff' in sys.argv:
            print(json.dumps(dict(visual_fixture_ready=True,stop_marker=str(folder/'visual-complete'))),flush=True)
            until=time.monotonic()+300
            while not (folder/'visual-complete').exists() and time.monotonic()<until:
                time.sleep(1)
            assert (folder/'visual-complete').exists(), 'Visual fixture timed out; cleanup enforced'
            assert source_hashes()==frozen, 'Source changed during visual fixture'
    except Exception as exc:
        error=str(exc)
    finally:
        docker('unpause','worker',check=False)
        stopped=docker('stop',check=False)
        (folder/'receipt.json').write_text(json.dumps(dict(contract=contract,rounds=rounds,error=error,containers_stopped=stopped.returncode==0,volumes_preserved=True,gate_b_passed=False),indent=2),encoding='utf8')
    print(json.dumps(dict(receipt=str(folder/'receipt.json'),error=error,rounds=len(rounds),persistent_server_started=False)),flush=True)
    raise SystemExit(1 if error else 0)


if __name__=='__main__': main()
