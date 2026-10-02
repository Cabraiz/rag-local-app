# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Real isolated containers, immutable oracles, no model/cloud calls.

Two same-author adversarial rounds, NOT independent blind audit or production HA.
Only this fresh QA project may be stopped, fault-injected or written by the test.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
import urllib.request
import urllib.error
from uuid import uuid4
import http_fixture as base
from cache_expiry_oracle import valid_pttl

PROJECT=sys.argv[1] if len(sys.argv)>1 else 'rag-local-resilience-qa-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
if not PROJECT.startswith('rag-local-resilience-qa-') or not PROJECT[len('rag-local-resilience-qa-'):].isdigit():
    raise ValueError('EXCLUSIVE_QA_PROJECT_REQUIRED')
FILES=['infrastructure/compose/runtime/compose.yaml','infrastructure/compose/runtime/compose.retrieval.yaml','infrastructure/compose/runtime/compose.semantic.yaml','infrastructure/compose/runtime/compose.documents.yaml',
       'infrastructure/compose/runtime/compose.delivery.yaml','infrastructure/compose/observability/compose.observability.yaml','infrastructure/compose/resilience/compose.resilience.yaml','infrastructure/compose/observability/compose.grafana.yaml',
       'infrastructure/compose/qa/compose.qa.yaml','infrastructure/compose/qa/compose.resilience-qa.yaml']
base.BASE='http://127.0.0.1:8940'
base.COMPOSE=['docker','compose', '--project-directory', str(APP),'--ansi','never','--progress','plain','-p',PROJECT]
for name in FILES: base.COMPOSE+=['-f',str(base.ROOT/name)]
FOLDER=base.ROOT.parent/'eval'/'runs'/(PROJECT+'-run-'+datetime.now(timezone.utc).strftime('%H%M%S'))

def owned_docker(*args,check=True):
    result=subprocess.run(base.COMPOSE+list(args),capture_output=True,text=True,
        timeout=600 if args and args[0]=='up' else 180,shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if check and result.returncode: raise RuntimeError(result.stderr[-1800:])
    return result

base.docker=owned_docker

def inside(code):
    text=base.app_python('import json,time\nfrom uuid import uuid4\nfrom rag_app import ledger,corpus,resilience,cache,broker\nfrom rag_app.domain import Identity,RequestError,Proposal\n'+code)
    return json.loads(text.splitlines()[-1])

def broker_inside(code):
    # The API deliberately has NO broker secret. Use an owned one-shot worker.
    prefix='import json,time\nfrom uuid import uuid4\nfrom rag_app import ledger,broker\nfrom rag_app.domain import Identity,Proposal\n'
    text=base.docker('run','--rm','--no-deps','-T','worker','python','-c',prefix+code).stdout
    return json.loads(text.splitlines()[-1])

def wait(check,seconds=60):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if check(): return
        time.sleep(.5)
    raise AssertionError('WAIT_DEADLINE')

def live():
    with urllib.request.urlopen('http://127.0.0.1:8840/health/ready',timeout=5) as r:
        assert r.status==200

def hashes():
    paths=list((base.ROOT/'src').rglob('*.py'))+list((base.ROOT/'src').rglob('*.sql'))
    paths += [base.ROOT/f for f in FILES]+[Path(__file__),base.ROOT/'tests/resilience/cache_expiry_oracle.py',base.ROOT/'infrastructure/images/backend/Dockerfile.resilience',base.ROOT/'infrastructure/dependencies/backend/resilience-requirements.lock']
    paths += [p for p in (base.ROOT/'monitoring').rglob('*') if p.is_file()]
    paths += [base.ROOT/f for f in ('infrastructure/compose/runtime/compose.integrations.yaml','infrastructure/compose/labs/compose.gemini-rag.yaml',
        'infrastructure/compose/resilience/compose.resilience-integrations.yaml','infrastructure/compose/qa/compose.resilience-load.yaml',
        'infrastructure/images/integrations/Dockerfile.integrations.resilience','infrastructure/dependencies/integrations/mcp-requirements.lock','infrastructure/images/backend/Dockerfile','infrastructure/dependencies/backend/requirements.lock')]
    return {str(p.relative_to(base.ROOT.parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

def round_(seed):
    checks=[]
    def check(name,ok):
        checks.append({'name':name,'passed':bool(ok)})
        (FOLDER/f'round-{seed}.json').write_text(json.dumps(checks,indent=2))
        if not ok: raise AssertionError(name)
    ana=base.http('/v1/lab/session',body={'profile':'ana'})[1]['token']
    bruno=base.http('/v1/lab/session',body={'profile':'bruno'})[1]['token']
    question='Qual o limite de alimentação durante viagens?'
    key='policy'+str(seed)
    # A cold durable collection may exceed a single 5s RPC budget. Reuse the SAME
    # manifest after explicit 503; never promote or claim acceptance on timeout.
    documents=[dict(source_key=key,title='Alimentação em viagem',media_type='text/plain',text='O orçamento aprovado para refeições é de 45 reais por pessoa. Esse valor é o limite de alimentação durante viagens.')]
    release=inside('documents='+repr(documents)+'''\n
for attempt in range(3):
 try:
  release=corpus.ingest(Identity("demo-a","demo-user"),documents)
  release['fixture_attempts']=attempt+1
  print(json.dumps(release)); break
 except RequestError as error:
  if error.status!=503 or attempt==2: raise
  time.sleep(resilience.delay(attempt+1))
''')
    check('real_neural_ingestion',release['state']=='READY')
    check('bruno_cannot_query',base.http('/v1/requests',bruno,{'question':question},{'Idempotency-Key':base.key()})[0]==403)
    check('ana_cannot_edit_corpus',base.http('/v1/lab/corpus/documents',ana,{'source_key':'x','title':'x','text':'x'})[0]==403)
    check('unauthenticated_receipts_denied',base.http('/v1/requests')[0] in (401,403))
    idem=base.key()
    with ThreadPoolExecutor(max_workers=8) as pool:
        values=list(pool.map(lambda _:base.http('/v1/requests',ana,{'question':question},{'Idempotency-Key':idem}),range(16)))
    check('concurrent_duplicates_one_receipt',all(c==202 for c,_ in values) and len({r['request_id'] for _,r in values})==1)
    rid=values[0][1]['request_id']
    check('resolve_lost_confirmation',base.http('/v1/requests/resolve',ana,{}, {'Idempotency-Key':idem})[1]['request_id']==rid)
    check('different_payload_same_key_conflict',base.http('/v1/requests',ana,{'question':'pergunta diferente'},{'Idempotency-Key':idem})[0]==409)
    result=base.wait_state(rid,ana,{'SUCCEEDED'})['result']
    check('real_adk_canonical_citation',result['kind']=='EXTRACTIVE' and '45 reais' in result['citations'][0]['quote'] and result.get('model') is None)
    check('foreign_identity_cannot_read',inside('try:\n ledger.read(Identity("foreign","foreign"),'+repr(rid)+')\n print(json.dumps(False))\nexcept RequestError as e:\n print(json.dumps(e.status==404))'))
    check('real_cache_populated',inside('print(json.dumps(cache.client().dbsize()>0))'))
    # Whole-second TTL can be zero while a live key still has 200ms left.
    # A key returned by SCAN can also expire before PTTL (-2). Neither grants
    # access: canonical SQL permission checks still follow every cache hit.
    expiry_values=inside('r=cache.client()\nprint(json.dumps([r.pttl(k) for k in r.scan_iter("rag:v1:*")]))')
    (FOLDER/f'cache-expiry-{seed}.json').write_text(json.dumps(dict(
        pttl_ms=expiry_values, persistent_or_overlong=[v for v in expiry_values if not valid_pttl(v)]),indent=2))
    check('cache_has_expiry',all(valid_pttl(v) for v in expiry_values))
    scoped=inside('k=cache.key("proof",["demo-a","demo-user"],["value"])\ncache.put(k,{"n":45})\nother=cache.key("proof",["other","demo-user"],["value"])\ngood=cache.get(k)=={"n":45} and cache.get(other) is None\ncache.client().set(k,b"not signed")\nprint(json.dumps(good and cache.get(k) is None))')
    check('cache_scope_and_tamper_rejected',scoped)
    base.docker('stop','redis')
    try:
        check('redis_outage_miss_not_request_loss',inside('print(json.dumps(cache.get("anything") is None))'))
        code,request=base.http('/v1/requests',ana,{'question':question},{'Idempotency-Key':base.key()})
        check('request_accepted_without_redis',code==202)
        check('canonical_answer_without_redis',base.wait_state(request['request_id'],ana,{'SUCCEEDED'})['result']['kind']=='EXTRACTIVE')
    finally: base.docker('start','redis')
    # Cache contains IDs; canonical permission checks remain after EVERY hit.
    document=result['citations'][0]['document_id']
    check('revocation_actual_sql',inside('print(json.dumps(corpus.revoke(Identity("demo-a","demo-user"),'+repr(document)+') is not None))'))
    check('cached_result_revalidated_on_read',base.http('/v1/requests/'+rid,ana)[1]['result']['kind']=='ABSTAIN')
    code,request=base.http('/v1/requests',ana,{'question':question},{'Idempotency-Key':base.key()})
    check('cached_candidate_cannot_bypass_revocation',code==202 and base.wait_state(request['request_id'],ana,{'SUCCEEDED'})['result']['kind']=='ABSTAIN')
    base.docker('stop','worker','relay','control')
    try:
        check('backoff_randomized_and_bounded',inside('import random\nvalues=[resilience.delay(2,random.Random(n)) for n in range(32)]\nprint(json.dumps(all(1<=v<=8 for v in values) and len(set(values))>16))'))
        check('permanent_error_no_retry',inside('rid=ledger.accept(Identity("demo-a","demo-user"),"SYNTHETIC permanent",str(int(time.time()))+"."+str(uuid4()))\nrow=ledger.claim(rid)\nledger.fail(row,RequestError("INVALID_PROPOSAL",422))\nprint(json.dumps(ledger.read(Identity("demo-a","demo-user"),rid)["state"]=="FAILED_FINAL"))'))
        check('transient_error_backoff_and_three_attempt_bound',inside('''
rid=ledger.accept(Identity("demo-a","demo-user"),"SYNTHETIC transient",str(int(time.time()))+"."+str(uuid4()))
ok=True
for attempt in range(1,4):
 row=ledger.claim(rid)
 ledger.fail(row,RequestError("INDEX_UNAVAILABLE",503))
 with ledger.connect() as db:
  current=db.execute('SELECT *,EXTRACT(EPOCH FROM next_at-clock_timestamp())::float AS wait FROM requests WHERE id=%s',(rid,)).fetchone()
  ok=ok and current['attempts']==attempt and current['state']==('RETRY_WAIT' if attempt<3 else 'FAILED_FINAL')
  if attempt<3:
   ok=ok and .5<current['wait']<=2**(attempt+1)
   db.execute('UPDATE requests SET next_at=clock_timestamp() WHERE id=%s',(rid,))
print(json.dumps(ok))
'''))
        check('lease_recovery_and_stale_fencing',inside('''
rid=ledger.accept(Identity("demo-a","demo-user"),"SYNTHETIC stale",str(int(time.time()))+"."+str(uuid4()))
row=ledger.claim(rid)
with ledger.connect() as db: db.execute("UPDATE requests SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(rid,))
ledger.recover()
ok=ledger.read(Identity("demo-a","demo-user"),rid)['state']=='RETRY_WAIT' and not ledger.finish(row,Proposal('ABSTAIN','safe'))
ledger.cancel(Identity("demo-a","demo-user"),rid)
print(json.dumps(ok))
'''))
        check('explicit_deadline_terminal',inside('''
rid=ledger.accept(Identity("demo-a","demo-user"),"SYNTHETIC expired",str(int(time.time()))+"."+str(uuid4()))
with ledger.connect() as db: db.execute("UPDATE requests SET deadline=clock_timestamp()-interval '1 second' WHERE id=%s",(rid,))
ledger.recover()
print(json.dumps(ledger.read(Identity("demo-a","demo-user"),rid)['state']=='EXPIRED'))
'''))
        check('other_partition_not_blocked_by_held_admission',inside('''
import threading
a='partition-a'; b=next('partition-'+str(n) for n in range(100) if resilience.bucket('partition-'+str(n))!=resilience.bucket(a))
done=threading.Event(); result=[]
def submit():
 rid=ledger.accept(Identity(b,'fixture'),"SYNTHETIC partition",str(int(time.time()))+"."+str(uuid4())); result.append(rid); done.set()
with ledger.connect() as db:
 ledger.lock_admission(db,a)
 t=threading.Thread(target=submit); t.start(); ok=done.wait(2)
t.join(10)
if result: ledger.cancel(Identity(b,'fixture'),result[0])
print(json.dumps(ok))
'''))
        check('circuit_three_failures_opens_and_half_open_recovers',inside('''
for n in range(3):
 try:
  with resilience.guard('qdrant'): raise RequestError('INDEX_UNAVAILABLE',503)
 except RequestError: pass
blocked=False
try:
 with resilience.guard('qdrant'): pass
except RequestError as e: blocked=e.code=='DEPENDENCY_CIRCUIT_OPEN'
with ledger.connect() as db: db.execute("UPDATE dependency_control SET open_until=clock_timestamp()-interval '1 second' WHERE name='qdrant'")
with resilience.guard('qdrant'):
 probe=False
 try:
  with resilience.guard('qdrant'): pass
 except RequestError as e: probe=e.code=='DEPENDENCY_PROBE_BUSY'
with ledger.connect() as db: closed=db.execute("SELECT open_until IS NULL AS closed FROM dependency_control WHERE name='qdrant'").fetchone()['closed']
print(json.dumps(blocked and probe and closed))
'''))
        check('shared_dependency_concurrency_budget',inside('''
from contextlib import ExitStack
with ExitStack() as stack:
 for _ in range(4): stack.enter_context(resilience.guard('embedding'))
 blocked=False
 try:
  with resilience.guard('embedding'): pass
 except RequestError as e: blocked=e.code=='DEPENDENCY_CONCURRENCY_LIMIT'
print(json.dumps(blocked))
'''))
        check('nonessential_metrics_do_not_write_on_calling_thread',inside('''
import threading
original=ledger.connect; calls=[]; caller=threading.get_ident()
def blocked(*args,**kwargs):
 calls.append(threading.get_ident()); raise TimeoutError('synthetic')
ledger.connect=blocked
try:
 for n in range(32): cache.event('cache_hit')
 ok=caller not in calls
finally: ledger.connect=original
ok=ok and cache.flush_events()
print(json.dumps(ok))
'''))
        check('heartbeat_cadence_bounded_without_disabling_liveness',inside('''
last=-5; writes=0
for tick in range(61):
 now=tick/10
 if resilience.heartbeat_due(last,now): last=now; writes+=1
print(json.dumps(writes==2 and not resilience.heartbeat_due(0,4.9) and resilience.heartbeat_due(0,5)))
'''))
        check('sdk_and_wrapped_transient_failures_open_circuit',inside('''
import httpx
from google.genai import errors
ok=resilience.transient(ExceptionGroup('synthetic',[TimeoutError()])) and resilience.transient(errors.APIError(429,{'error':{'message':'synthetic'}})) and not resilience.transient(errors.APIError(401,{'error':{'message':'synthetic'}}))
with ledger.connect() as db: db.execute("UPDATE dependency_control SET failures=0,open_until=NULL,probe_until=NULL,generation=generation+1 WHERE name='gemini'")
for n in range(3):
 try:
  with resilience.guard('gemini'): raise errors.ServerError(503,{'error':{'message':'synthetic'}})
 except errors.APIError: pass
try:
 with resilience.guard('gemini'): ok=False
except RequestError as e: ok=ok and e.code=='DEPENDENCY_CIRCUIT_OPEN'
with ledger.connect() as db: db.execute("UPDATE dependency_control SET failures=0,open_until=NULL,probe_until=NULL,generation=generation+1 WHERE name='gemini'")
print(json.dumps(ok))
'''))
        check('publisher_reuses_confirmed_connection',broker_inside('''
conn=broker.connection(); ch=broker.channel(conn); ch.queue_purge(broker.QUEUE); conn.close()
p=broker.Publisher(); connection_id=id(p.conn); ok=True
try:
 for n in range(2):
  rid=ledger.accept(Identity('demo-a','demo-user'),'SYNTHETIC publisher reuse',str(int(time.time()))+'.'+str(uuid4()))
  ok=ok and p.publish_one() and id(p.conn)==connection_id
  c=broker.Consumer(); row,tag=c.poll()
  ok=ok and row is not None and str(row['id'])==rid and ledger.finish(row,Proposal('ABSTAIN','safe'))
  c.outcome(tag,rid); c.close()
finally: p.close()
print(json.dumps(ok))
'''))
        check('committed_result_before_ack_and_redelivery_dedup',broker_inside('''
rid=ledger.accept(Identity('demo-a','demo-user'),'SYNTHETIC broker crash',str(int(time.time()))+'.'+str(uuid4()))
conn=broker.connection(); ch=broker.channel(conn); ch.queue_purge(broker.QUEUE); conn.close()
broker.publish_one()
c=broker.Consumer(); row,tag=c.poll()
ok=row is not None and str(row['id'])==rid and ledger.finish(row,Proposal('ABSTAIN','safe'))
c.close()
c=broker.Consumer(); row,tag=c.poll(); c.close()
with ledger.connect() as db: count=db.execute("SELECT count(*) AS n FROM audit WHERE request_id=%s AND kind='TERMINAL'",(rid,)).fetchone()['n']
print(json.dumps(ok and row is None and count==1))
'''))
        check('terminal_audit_and_counts_conserved',inside('''
with ledger.connect() as db:
 actual=db.execute("SELECT count(*) AS n,COALESCE(sum(octet_length(question)),0) AS bytes FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT')").fetchone()
 budget=db.execute('SELECT sum(pending) AS n,sum(pending_bytes) AS bytes FROM admission_shards').fetchone()
 dupe=db.execute("SELECT count(*) AS n FROM (SELECT request_id FROM audit WHERE kind='TERMINAL' GROUP BY request_id HAVING count(*)>1) q").fetchone()['n']
print(json.dumps(actual==budget and dupe==0))
'''))
        check('cancelled_claim_releases_transport_delivery',broker_inside('''
rid=ledger.accept(Identity('demo-a','demo-user'),'SYNTHETIC broker cancel',str(int(time.time()))+'.'+str(uuid4()))
conn=broker.connection(); ch=broker.channel(conn); ch.queue_purge(broker.QUEUE); conn.close()
broker.publish_one()
c=broker.Consumer(); row,tag=c.poll()
ok=row is not None and str(row['id'])==rid
ledger.cancel(Identity('demo-a','demo-user'),rid)
ok=ok and not ledger.finish(row,Proposal('ABSTAIN','safe'))
c.outcome(tag,rid); c.close()
print(json.dumps(ok and ledger.read(Identity('demo-a','demo-user'),rid)['state']=='CANCELLED'))
'''))
    finally: base.docker('start','control','relay','worker')
    # Actual broker down: SQL jobs, not volatile transport, survive.
    base.docker('stop','rabbitmq')
    try:
        code,request=base.http('/v1/requests',ana,{'question':'SYNTHETIC fora da base'},{'Idempotency-Key':base.key()})
        check('broker_outage_acceptance_durable',code==202)
        check('broker_outage_sql_fallback_terminal',base.wait_state(request['request_id'],ana,{'SUCCEEDED'})['result']['kind']=='ABSTAIN')
    finally: base.docker('start','rabbitmq')
    base.docker('stop','relay')
    try:
        code,request=base.http('/v1/requests',ana,{'question':'SYNTHETIC relay indisponivel'},{'Idempotency-Key':base.key()})
        check('healthy_broker_dead_relay_sql_fallback',code==202 and base.wait_state(request['request_id'],ana,{'SUCCEEDED'})['result']['kind']=='ABSTAIN')
    finally: base.docker('start','relay')
    wait(lambda:inside("import httpx\nprint(json.dumps(httpx.get('http://prometheus:9090/api/v1/query',params={'query':'rag_pending_requests'},timeout=3).json()['data']['result']!=[]))"))
    check('prometheus_real_sql_metrics',True)
    with urllib.request.urlopen('http://127.0.0.1:8950/api/health',timeout=5) as response:
        check('grafana_database_healthy',json.load(response)['database']=='ok')
    with urllib.request.urlopen('http://127.0.0.1:8950/api/dashboards/uid/rag-overview',timeout=5) as response:
        dashboard=json.load(response)
        check('grafana_dashboard_provisioned_ten_panels',len(dashboard['dashboard']['panels'])==10 and not dashboard['meta']['canEdit'])
    update=urllib.request.Request('http://127.0.0.1:8950/api/dashboards/db',
        data=json.dumps({'dashboard':dashboard['dashboard'],'overwrite':True}).encode(),
        headers={'Content-Type':'application/json'})
    try:
        urllib.request.urlopen(update,timeout=5).close()
        denied=False
    except urllib.error.HTTPError as error:
        denied=error.code in (401,403)
    check('grafana_anonymous_cannot_update_dashboard',denied)
    check('grafana_query_has_real_data',inside("import httpx\nv=httpx.get('http://grafana:3000/api/datasources/proxy/uid/rag-prometheus/api/v1/query',params={'query':'rag_requests'},timeout=5).json()\nprint(json.dumps(v['status']=='success' and bool(v['data']['result'])))"))
    check('metrics_no_sensitive_content',inside("from rag_app.observability import snapshot\nv=snapshot()[0]\nprint(json.dumps(all(x not in v for x in ('45 reais','demo-user','Authorization','api_key','tenant='))))"))
    base.docker('restart','api')
    wait(lambda:base.http('/health/ready')[0]==200)
    check('receipt_survives_api_restart',base.http('/v1/requests/'+rid,ana)[0]==200)
    live(); check('main_lab_untouched',True)
    return {'seed':seed,'passed':True,'checks':checks}

def main():
    FOLDER.mkdir(parents=True,exist_ok=False); before=hashes(); live()
    receipt={'scope':'isolated real SQL/Qdrant/MiniLM/ADK/Redis/RabbitMQ/Prometheus/Grafana',
             'independent_blind_audit':False,'production_ha':False,'gemini_calls':0,'source_hashes':before,'rounds':[],'passed':False}
    try:
        assert not base.docker('ps','-q').stdout.strip(),'QA_PROJECT_ALREADY_RUNNING'
        base.docker('up','-d','--no-build','--wait','--wait-timeout','480')
        wait(lambda:inside("import httpx\nprint(json.dumps(httpx.get('http://qdrant:6333/readyz',timeout=3).status_code==200))"))
        for _ in range(2):
            seed=random.SystemRandom().randrange(100000,999999)
            receipt['rounds'].append(round_(seed))
            assert before==hashes(),'SOURCE_CHANGED_RESET_CLEAN_COUNT'
        receipt['images']=base.service_image_ids(); receipt['passed']=True
    except Exception as error:
        receipt['error_type']=type(error).__name__; receipt['error']=str(error)[:1800]
    finally:
        (FOLDER/'receipt.json').write_text(json.dumps(receipt,indent=2))
        base.docker('stop',check=False); live()
    print(json.dumps({'passed':receipt['passed'],'rounds':len(receipt['rounds']),'receipt':str(FOLDER/'receipt.json')}))
    if not receipt['passed']: raise SystemExit(1)

if __name__=='__main__': main()
