# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Adversarial real lifecycle tests, NOT full RAG/cloud/MCP/DeepAgents certification.

Fixed independent expected HTTP/SQL outcomes; random inputs generated after freeze.
Only owns a stopped Compose project; synthetic rows and volumes are preserved.
Any failure resets the consecutive pass count. Never rewrites expected outcomes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import secrets
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4

from http_fixture import ROOT, BASE, docker, http, key, app_python, wait_state, run_round

CATALOG = ROOT.parent/'docs/architecture/contracts/segment-cases.tsv'
SELF = Path(__file__).resolve()


def frozen_sources():
    paths = list((ROOT/'src').rglob('*.py')) + list((ROOT/'src').rglob('*.sql'))
    paths += [ROOT/'infrastructure/compose/runtime/compose.yaml', ROOT/'infrastructure/images/backend/Dockerfile', ROOT/'infrastructure/dependencies/backend/requirements.lock',
              SELF, SELF.with_name('http_fixture.py'), CATALOG,
              CATALOG.with_name('segment-acceptance.md')]
    paths += [p for p in (ROOT.parent/'frontend').rglob('*') if p.is_file()]
    return {str(p.relative_to(ROOT.parent)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def images():
    # docker() uses no shell and CREATE_NO_WINDOW on Windows.
    import os
    result = subprocess.run(['docker','image','inspect','rag-local-backend:0.1.0',
        'rag-local-frontend:0.1.0','--format','{{.Id}}'], check=True, capture_output=True,
        text=True, shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    return result.stdout.strip().splitlines()


def sql(code):
    return json.loads(app_python('from rag_app import ledger; from rag_app.domain import Identity, Proposal, RequestError; import json; '+code))


def extended_round(seed):
    rng = random.Random(seed)
    token = http('/v1/lab/session',body={'tenant':'demo-a'})[1]['token']
    other = http('/v1/lab/session',body={'tenant':'demo-b'})[1]['token']
    ids, observations = [], []

    def submit(label, idem=None, who=None):
        question = 'SYNTHETIC segment '+label+' '+str(seed)+' '+str(uuid4())
        idem = idem or key()
        code,row = http('/v1/requests',who or token,{'question':question},{'Idempotency-Key':idem})
        assert code==202, (label,code)
        ids.append(row['request_id'])
        return row['request_id'],question,idem

    def old_known():
        rid,question,_ = submit('old-known')
        # Emulate the key ageing while its previously committed mapping still exists.
        old = str(int(time.time())-604800-rng.randint(61,999))+'.'+str(uuid4())
        sql("db=ledger.connect(); db.execute('UPDATE requests SET idem=%s WHERE id=%s',("+repr(old)+","+repr(rid)+")); db.commit(); db.close(); print('true')")
        c,row = http('/v1/requests',token,{'question':question},{'Idempotency-Key':old})
        assert c==202 and row['request_id']==rid, f'known old key replay: expected 202 existing ID, got {c}'
        assert http('/v1/requests',token,{'question':'DIFFERENT_SYNTHETIC'},{'Idempotency-Key':old})[0]==409
        assert http('/v1/requests/resolve',token,{}, {'Idempotency-Key':old})[1]['request_id']==rid

    def expired_lease():
        rid,_,_ = submit('expired-lease')
        # Hold the synthetic row lock to prevent the independent control racing this probe.
        value=sql("db=ledger.connect(); row=db.execute(\"UPDATE requests SET state='RUNNING',fence=fence+1,attempts=1,lease_until=clock_timestamp()-interval '1 second' WHERE id=%s RETURNING *\",("+repr(rid)+",)).fetchone(); changed=ledger.terminal(db,row,'SUCCEEDED',{'kind':'ABSTAIN'},worker=True); db.commit(); db.close(); print(json.dumps(changed))")
        assert value is False, 'expired current owner committed without reclaim'

    def attempts():
        rid,_,_ = submit('attempt-limit')
        sql("db=ledger.connect(); db.execute(\"UPDATE requests SET state='RUNNING',attempts=3,lease_until=clock_timestamp()-interval '1 second' WHERE id=%s\",("+repr(rid)+",)); db.commit(); db.close(); print('true')")
        assert wait_state(rid,token,{'FAILED_FINAL'})['attempts']==3

    def orphan():
        rid,_,_ = submit('orphan')
        # The application role intentionally cannot DELETE. Use the existing
        # administrative migration role ONLY for this owned synthetic fault.
        docker('run','--rm','--no-deps','-T','migrate','python','-c',
            "from rag_app import ledger; db=ledger.connect(admin=True); db.execute('DELETE FROM jobs WHERE id=%s',("+repr(rid)+",)); db.commit(); db.close()")
        end=time.monotonic()+15
        while True:
            if sql("db=ledger.connect(); present=db.execute('SELECT EXISTS(SELECT 1 FROM jobs WHERE id=%s) AS found',("+repr(rid)+",)).fetchone()['found']; db.close(); print(json.dumps(present))"):
                break
            assert time.monotonic()<end, 'synthetic orphan job not repaired'
            time.sleep(.2)

    def actor():
        rid,_,_ = submit('actor')
        # Exact SQL row absence; not an exception class guessed by a mock.
        hidden=sql("db=ledger.connect(); n=db.execute('SELECT count(*) AS n FROM requests WHERE id=%s AND tenant=%s AND actor=%s',("+repr(rid)+",'demo-a','other-synthetic-actor')).fetchone()['n']; db.close(); print(json.dumps(n))")
        assert hidden==0
        result=app_python("from rag_app import ledger; from rag_app.domain import Identity, RequestError\ntry: ledger.read(Identity('demo-a','other-synthetic-actor'),"+repr(rid)+")\nexcept RequestError as e: print(e.status)\nelse: print('UNEXPECTED_READ')")
        assert result=='404'

    def inputs():
        before=sql("db=ledger.connect(); n=db.execute('SELECT count(*) AS n FROM requests').fetchone()['n']; db.close(); print(json.dumps(n))")
        cases=[({'question':''},{'Idempotency-Key':key()},422),
               ({'question':'SYNTHETIC'}, {},422),
               ({'question':'X'*20000},{'Idempotency-Key':key()},413),
               ({'question':'SYNTHETIC'},{'Idempotency-Key':'bad-nonce'},409),
               ({'question':'SYNTHETIC'},{'Idempotency-Key':str(int(time.time())+300)+'.'+str(uuid4())},409)]
        rng.shuffle(cases)
        for body,headers,wanted in cases:
            assert http('/v1/requests',token,body,headers)[0]==wanted
        after=sql("db=ledger.connect(); n=db.execute('SELECT count(*) AS n FROM requests').fetchone()['n']; db.close(); print(json.dumps(n))")
        assert before==after, 'invalid requests created durable rows'

    def tenant_key():
        idem=key()
        a,_,_=submit('same-key-a',idem)
        b,_,_=submit('same-key-b',idem,other)
        assert a!=b
        assert http('/v1/requests/'+a,other)[0]==404
        assert http('/v1/requests/'+b,token)[0]==404

    def invalid_proposal():
        rid,_,_=submit('unverified')
        raw=app_python("from rag_app import ledger; from rag_app.domain import Proposal, RequestError; db=ledger.connect(); row=db.execute(\"UPDATE requests SET state='RUNNING',fence=fence+1,attempts=1,lease_until=clock_timestamp()+interval '30 seconds' WHERE id=%s RETURNING *\",("+repr(rid)+",)).fetchone(); db.commit(); db.close()\ntry: ledger.finish(row,Proposal('ANSWER','SYNTHETIC_UNSUPPORTED'))\nexcept RequestError as e: print(e.code)\nelse: print('UNEXPECTED_COMMIT')\nprint(ledger.finish(row,Proposal('ABSTAIN','SYNTHETIC')))")
        assert raw.splitlines()==['UNVERIFIED_PROPOSAL','True']

    def idle_transaction():
        # Server-side timeout must terminate even if the Python holder stops
        # executing statements. This uses a real application-role connection.
        code="""from rag_app import ledger
import time,json
def probe(disable):
    db=ledger.connect()
    if disable: db.execute('SET idle_in_transaction_session_timeout=0')
    setting=db.execute('SHOW idle_in_transaction_session_timeout').fetchone()['idle_in_transaction_session_timeout']
    pid=db.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
    db.execute('SELECT pending FROM admission WHERE id=1 FOR UPDATE')
    time.sleep(6)
    observer=ledger.connect()
    alive=observer.execute('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=%s) AS alive',(pid,)).fetchone()['alive']
    observer.close()
    error=None
    try: db.execute('SELECT 1')
    except Exception as exc: error=type(exc).__name__
    finally: db.close()
    return dict(setting=setting,alive=alive,error=error)
print(json.dumps([probe(False),probe(True)]))
"""
        guarded,mutant=json.loads(app_python(code))
        assert guarded['setting']=='5s' and guarded['alive'] is False and guarded['error'] is not None, guarded
        assert mutant=={'setting':'0','alive':True,'error':None}, mutant
        assert http('/health/ready')[0]==200

    cases=[('known_old_key_replay',old_known),('expired_lease_no_commit',expired_lease),
           ('attempt_limit',attempts),('orphan_job_recovered',orphan),
           ('actor_isolation',actor),('input_boundaries',inputs),
           ('same_key_different_tenant',tenant_key),('unverified_proposal_rejected',invalid_proposal),
           ('idle_transaction_bounded',idle_transaction)]
    rng.shuffle(cases)
    docker('stop','worker')
    try:
        for name,fn in cases:
            try:
                fn(); observations.append(dict(name=name,passed=True))
            except Exception as exc:
                observations.append(dict(name=name,passed=False,error=str(exc)))
    finally:
        docker('start','worker')

    # Separate guaranteed pending receipt; worker is stopped before admission.
    docker('stop','worker')
    try:
        rid,q,idem=submit('restart-paused')
        docker('restart','postgres')
        end=time.monotonic()+35
        while True:
            try:
                if http('/health/ready')[0]==200: break
            except Exception: pass
            assert time.monotonic()<end, 'PostgreSQL did not recover'
            time.sleep(.3)
        c,restored=http('/v1/requests/'+rid,token)
        assert c==200 and restored['state']=='ACCEPTED'
        c,again=http('/v1/requests',token,{'question':q},{'Idempotency-Key':idem})
        assert c==202 and again['request_id']==rid
        observations.append(dict(name='postgres_restart',passed=True))
    finally:
        docker('start','worker')

    for handle in ids:
        wait_state(handle,token if http('/v1/requests/'+handle,token)[0]==200 else other,
                   {'SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED'})
    rid,_,_=submit('terminal')
    final=wait_state(rid,token,{'SUCCEEDED'})
    changed=http('/v1/requests/'+rid+'/cancel',token,{})[1]
    observations.append(dict(name='cancel_after_terminal',passed=changed['state']==final['state'] and changed['result']==final['result']))
    request=urllib.request.Request(BASE+'/v1/requests/'+rid,headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(request,timeout=15) as response:
        value=json.loads(response.read())
        safe=response.headers.get('Cache-Control')=='no-store' and value['result']['kind']=='ABSTAIN' and value['result']['citations']==[] and 'question' not in value and 'proposal' not in value
    observations.append(dict(name='public_result_boundary',passed=safe))
    counts=sql("db=ledger.connect(); ids="+repr(ids)+"; n=db.execute(\"SELECT count(*) AS n FROM requests WHERE id=ANY(%s::uuid[]) AND state IN ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED')\",(ids,)).fetchone()['n']; a=db.execute(\"SELECT count(*) AS n FROM audit WHERE request_id=ANY(%s::uuid[]) AND kind='TERMINAL'\",(ids,)).fetchone()['n']; o=db.execute(\"SELECT count(*) AS n FROM outbox WHERE request_id=ANY(%s::uuid[]) AND kind='TERMINAL'\",(ids,)).fetchone()['n']; db.close(); print(json.dumps([n,a,o]))")
    observations.append(dict(name='synthetic_ids_conserved',passed=counts==[len(ids)]*3))
    denied=docker('exec','-T','-e','RAG_MODE=production','api','python','-c','from rag_app.config import enforce_lab; enforce_lab()',check=False)
    observations.append(dict(name='production_refused',passed=denied.returncode!=0 and 'Production disabled' in denied.stderr))
    return dict(seed=seed,checks=observations,ids=ids,passed=all(c['passed'] for c in observations))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--rounds',type=int,default=2)
    args=parser.parse_args()
    if not 1<=args.rounds<=4: raise SystemExit('Bounded fixture: 1..4 rounds')
    if docker('ps','--status','running','-q').stdout.strip():
        raise SystemExit('Refuse ownership of an already running Compose project')
    folder=ROOT.parent/'eval/runs'/('segments-real-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True)
    frozen=frozen_sources(); image_ids=images()
    seeds=[secrets.randbits(32) for _ in range(args.rounds)]
    contract=dict(scope='real_HTTP_PostgreSQL_ADK_abstention_only',independent_blind=False,
        oracle_frozen_before_inputs=True,seeds=seeds,sha256=frozen,images=image_ids,
        full_rag_tested=False,real_mcp_tested=False,vertex_tested=False,deepagents_tested=False,
        load_100k_tested=False,gate_b_passed=False)
    (folder/'contract.json').write_text(json.dumps(contract,indent=2),encoding='utf8')
    rounds=[]; streak=0; error=None
    try:
        docker('up','-d','--wait','--wait-timeout','120')
        for seed in seeds:
            assert frozen_sources()==frozen and images()==image_ids, 'Sources/images changed: streak invalidated'
            basic=run_round(seed)
            result=extended_round(seed)
            result['basic_checks']=basic['checks']; result['basic_ids']=basic['ids']
            result['passed']=result['passed'] and all(c['pass'] for c in basic['checks'])
            streak=streak+1 if result['passed'] else 0
            result['consecutive_passes']=streak
            rounds.append(result)
            (folder/f'round-{seed}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
            print(json.dumps(dict(seed=seed,passed=result['passed'],checks=len(basic['checks'])+len(result['checks']),failures=[c for c in result['checks'] if not c['passed']],streak=streak)),flush=True)
            if not result['passed']: break
        assert frozen_sources()==frozen and images()==image_ids, 'Frozen contract changed'
    except Exception as exc:
        error=str(exc); streak=0
    finally:
        docker('unpause','worker',check=False)
        stopped=docker('stop',check=False)
        receipt=dict(contract=contract,rounds=rounds,error=error,consecutive_passes=streak,
            two_consecutive_real_slice_passes=streak>=2,all_segments_passed=False,
            containers_stopped=stopped.returncode==0,volumes_preserved=True)
        (folder/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps(dict(receipt=str(folder/'receipt.json'),error=error,streak=streak,all_segments_passed=False)),flush=True)
    raise SystemExit(0 if streak>=args.rounds and not error else 1)


if __name__=='__main__': main()
