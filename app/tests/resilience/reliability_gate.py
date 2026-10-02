# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Real isolated HTTP/SQL/ADK crash gate; never a production HA certificate."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import http.client
import json
from pathlib import Path
import random
import os
import subprocess
import time
import traceback
from uuid import UUID
import resilience_fixture as qa
import http_fixture as base

base.COMPOSE += ['-f', str(base.ROOT/'infrastructure/compose/qa/compose.reliability-qa.yaml')]
FOLDER = base.ROOT.parent/'eval'/'runs'/(qa.PROJECT+'-reliability-'+datetime.now(timezone.utc).strftime('%H%M%S'))


def sources():
    result = {name.replace('\\','/'):digest for name,digest in qa.hashes().items()}
    for name in ('tests/resilience/reliability_gate.py', 'tests/shared/http_fixture.py',
                 'tests/resilience/resilience_runtime_proof.py', 'infrastructure/compose/qa/compose.reliability-qa.yaml',
                 'tools/runtime/update-reliability-runtime.py'):
        result['app/'+name] = qa.hashlib.sha256((base.ROOT/name).read_bytes()).hexdigest()
    contract=base.ROOT.parent/'docs/quality/reliability/reliability-gate-20261002.md'
    result[contract.relative_to(base.ROOT.parent).as_posix()]=qa.hashlib.sha256(contract.read_bytes()).hexdigest()
    return result


def save(name, value):
    (FOLDER/name).write_text(json.dumps(value, indent=2), encoding='utf8')


def resume_killed(service):
    # Start the exact owned container IDs after SIGKILL, rather than relying on
    # a Compose dependency/restart-state snapshot. Verify EVERY replica running.
    ids=base.docker('ps','-a','-q',service).stdout.strip().splitlines()
    assert ids, 'KILLED_SERVICE_NOT_FOUND'
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    for cid in ids:
        owner=subprocess.run(['docker','inspect',cid,'--format','{{index .Config.Labels "com.docker.compose.project"}}'],
                             capture_output=True,text=True,timeout=10,creationflags=flags)
        assert owner.returncode==0 and owner.stdout.strip()==qa.PROJECT, 'NOT_OWNED_FAULT_TARGET'
    result=subprocess.run(['docker','start',*ids],capture_output=True,text=True,timeout=30,creationflags=flags)
    assert result.returncode==0, 'FAULT_RESUME_FAILED'
    time.sleep(.5)
    state=subprocess.run(['docker','inspect','--format','{{.State.Running}}',*ids],
                         capture_output=True,text=True,timeout=10,creationflags=flags)
    assert state.returncode==0 and state.stdout.splitlines()==['true']*len(ids), 'REPLICA_NOT_RESUMED'
    return dict(container_ids=ids,running=len(ids),verified=True)


def recovery_probe(request_locks=False, backlog=1, orphan=False):
    base.docker('stop', 'worker', 'control', 'relay', 'delivery')
    try:
        code='''
a='recovery-a'; b=next('recovery-'+str(n) for n in range(100) if resilience.bucket('recovery-'+str(n))!=resilience.bucket(a))
who_a=Identity(a,'fixture'); who_b=Identity(b,'fixture')
ids_a=[ledger.accept(who_a,'SYNTHETIC recovery A',str(int(time.time()))+'.'+str(uuid4())) for _ in range(BACKLOG)]
ra=ids_a[0]
rb=ledger.accept(who_b,'SYNTHETIC recovery B',str(int(time.time()))+'.'+str(uuid4()))
if ORPHAN:
 with ledger.connect(True) as db: db.execute('DELETE FROM jobs WHERE id=ANY(%s::uuid[])',(ids_a,))
with ledger.connect() as db:
 db.execute("UPDATE requests SET deadline=clock_timestamp()-interval '2 seconds' WHERE id=ANY(%s::uuid[])",(ids_a,))
 db.execute("UPDATE requests SET deadline=clock_timestamp()-interval '1 second' WHERE id=%s",(rb,))
error=None; started=time.monotonic()
with ledger.connect() as held:
 if REQUEST_LOCKS: held.execute('SELECT id FROM requests WHERE id=ANY(%s::uuid[]) FOR UPDATE',(ids_a,)).fetchall()
 else: ledger.lock_admission(held,a)
 try: ledger.recover()
 except Exception as e: error=type(e).__name__
 elapsed=time.monotonic()-started
 independent=ledger.read(who_b,rb)['state']=='EXPIRED'
 blocked=ledger.read(who_a,ra)['state']=='ACCEPTED'
for _ in range(3): ledger.recover()
with ledger.connect() as db:
 counts=db.execute("SELECT count(*) AS n FROM audit WHERE request_id=ANY(%s::uuid[]) AND kind='TERMINAL'",(ids_a+[rb],)).fetchone()['n']
print(json.dumps(dict(passed=independent and blocked and counts==BACKLOG+1 and error is None, independent_recovered=independent, blocked_preserved=blocked, terminal_audits=counts, blocked_backlog=BACKLOG, request_locks=REQUEST_LOCKS, orphan_fault=ORPHAN, elapsed_seconds=elapsed, error_type=error)))
'''.replace('BACKLOG',str(backlog)).replace('REQUEST_LOCKS',repr(request_locks)).replace('ORPHAN',repr(orphan))
        if orphan:
            # Only this isolated fault injector has bootstrap privileges. The
            # tested ledger operations still use their ordinary application role.
            result=base.docker('run','--rm','--no-deps','-T','migrate','python','-c',
                               'import json,time\nfrom uuid import uuid4\nfrom rag_app import ledger,resilience\nfrom rag_app.domain import Identity\n'+code)
            return json.loads(result.stdout.splitlines()[-1])
        return qa.inside(code)
    finally:
        base.docker('start', 'worker', 'control', 'relay', 'delivery')


def submit_batch(token, marker, count):
    started=time.monotonic()
    def send(n):
        key=base.key(); attempts=0; statuses=[]; first_send=time.monotonic()
        while time.monotonic()-started<180:
            attempts+=1
            try:
                code,value=base.http('/v1/requests', token, {'question':marker+' '+str(n)}, {'Idempotency-Key':key})
            except (OSError, TimeoutError):
                code,value=0,{}
            statuses.append(code)
            if code==202:
                return dict(request_id=value['request_id'], key=key, attempts=attempts,
                            http=statuses, accepted_seconds=time.monotonic()-started,
                            confirmation_seconds=time.monotonic()-first_send)
            if code not in (0,429,502,503,504):
                raise AssertionError('UNEXPECTED_ADMISSION_STATUS_'+str(code))
            # Never generate a replacement key for an unknown confirmation.
            time.sleep(.1+random.random()*.2)
        raise AssertionError('ADMISSION_DEADLINE')
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows=list(pool.map(send,range(count)))
    assert len({r['request_id'] for r in rows})==count, 'DISTINCT_KEYS_COLLAPSED'
    return rows


def migration_restart_probe():
    # Hold an ordinary request row: replayed ALTER TABLE would block/fail here.
    # Keep the default Compose dependency restart path in the chaos phases too.
    code='''
started=time.monotonic()
with ledger.connect() as held:
 held.execute('SELECT id FROM requests ORDER BY id LIMIT 1 FOR UPDATE').fetchone()
 ledger.migrate()
elapsed=time.monotonic()-started
with ledger.connect(True) as db:
 checksum=__import__('hashlib').sha256(__import__('pathlib').Path('/app/src/rag_app/persistence/schema.sql').read_text().encode()).hexdigest()
 recorded=db.execute('SELECT count(*) AS n FROM schema_migrations WHERE checksum=%s',(checksum,)).fetchone()['n']
print(json.dumps(dict(passed=recorded==1 and elapsed<2, applied_once=recorded==1, locked_request_untouched=True, elapsed_seconds=elapsed)))
'''
    value=base.docker('run','--rm','--no-deps','-T','migrate','python','-c',
                      'import json,time\nfrom rag_app import ledger\n'+code)
    return json.loads(value.stdout.splitlines()[-1])


def accounting(marker):
    return qa.inside('''
with ledger.connect() as db:
 rows=db.execute("SELECT r.id,r.state,r.attempts,EXTRACT(EPOCH FROM r.terminal_at-r.created_at)::float AS seconds,(SELECT count(*) FROM audit a WHERE a.request_id=r.id AND a.kind='TERMINAL') AS terminals,EXISTS(SELECT 1 FROM jobs j WHERE j.id=r.id) AS job,(SELECT count(*) FROM outbox o JOIN notification_inbox i ON i.outbox_id=o.id WHERE o.request_id=r.id AND o.kind='TERMINAL') AS delivered FROM requests r WHERE question LIKE %s",('''+repr(marker+'%')+''',)).fetchall()
print(json.dumps([dict(r,id=str(r['id'])) for r in rows]))
''')


def drain(token, marker, rows, seconds=180):
    wanted={r['request_id'] for r in rows}
    qa.wait(lambda: len(accounting(marker))==len(rows) and all(
        r['state'] in ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED') and r['delivered']==1
        for r in accounting(marker)), seconds=seconds)
    actual=accounting(marker)
    assert {r['id'] for r in actual}==wanted, 'ACCEPTED_ID_MISSING_OR_EXTRA'
    assert all(r['job'] and r['terminals']==1 and r['delivered']==1 for r in actual), 'AUDIT_JOB_OR_DELIVERY_INVARIANT'
    successes=sum(r['state']=='SUCCEEDED' for r in actual)
    assert successes/len(rows)>=.99, 'SUCCESS_SLO_BELOW_99_PERCENT'
    # HTTP receipts stay authorized and resolvable, not merely internal SQL rows.
    for row in rows[::max(1,len(rows)//16)]:
        assert base.http('/v1/requests/'+row['request_id'], token)[0]==200
        assert base.http('/v1/requests/resolve',token,{}, {'Idempotency-Key':row['key']})[1]['request_id']==row['request_id']
    durations=sorted(r['seconds'] for r in actual if r['seconds'] is not None)
    return dict(accepted=len(rows), lost=0, duplicate_terminals=0, delivered=len(actual),
                succeeded=successes, success_fraction=successes/len(rows),
                terminal_states={s:sum(r['state']==s for r in actual) for s in sorted({r['state'] for r in actual})},
                completion_seconds={p:round(durations[min(len(durations)-1,int(len(durations)*q))],3)
                                    for p,q in [('p50',.5),('p95',.95),('p99',.99)]})


def crash_round(seed):
    token=base.http('/v1/lab/session',body={'profile':'ana'})[1]['token']
    reports=[]
    probes=[recovery_probe(backlog=129), recovery_probe(request_locks=True,backlog=129),
            recovery_probe(request_locks=True,orphan=True)]
    save(str(seed)+'-recovery-probes.json',probes)
    assert all(p['passed'] for p in probes), 'RECOVERY_PARTITION_OR_ROW_STARVATION'
    assert qa.inside('import random\nvalues=[resilience.broker_reconnect_delay(n,random.Random(n)) for n in range(1,20)]\nprint(json.dumps(all(5<=v<=30 for v in values) and len(set(values))>10))'), 'BROKER_RECONNECT_BACKOFF_INVALID'
    # Respect the real profile's 3 requests/s + 32 burst admission policy.
    # These are accepted workloads, not a 100k rejection-dominated burst.
    phases=random.Random(seed).sample(['worker_kill','broker_cache_down','postgres_kill','healthy'],4)
    for phase in phases:
        count=128
        marker='SYNTHETIC reliability '+str(seed)+' '+phase
        started=time.monotonic()
        if phase in ('worker_kill','postgres_kill'):
            base.docker('stop','worker','relay','control')
        if phase=='broker_cache_down':
            base.docker('stop','rabbitmq','redis')
        try:
            rows=submit_batch(token,marker,count)
            save(str(seed)+'-'+phase+'-accepted.json',rows)
            if phase=='worker_kill':
                # An abandoned real SQL claim plus actual SIGKILL of workers.
                claimed=qa.inside('row=ledger.claim('+repr(rows[0]['request_id'])+')\nprint(json.dumps(dict(id=str(row["id"]),fence=row["fence"])))')
                # Inject a missing transport job, never delete its SQL receipt.
                missing=str(UUID(rows[1]['request_id']))
                removed=base.docker('exec','-T','postgres','psql','-U','rag_bootstrap','-d','rag','-Atqc',
                                    "DELETE FROM jobs WHERE id='"+missing+"'::uuid RETURNING id").stdout.strip()
                assert removed==missing, 'ORPHAN_FAULT_NOT_INJECTED'
                base.docker('start','worker','relay','control')
                base.docker('kill','-s','SIGKILL','worker')
                resumed=resume_killed('worker')
                assert resumed['running']==2, 'WORKER_REPLICA_MISSING_AFTER_KILL'
                # Preserve real lease duration; control must actually recover it.
                assert claimed['id']==rows[0]['request_id']
            elif phase=='postgres_kill':
                save('progress.json',dict(stage='postgres_ready_after_kill',seed=seed,phase=phase))
                recovery=postgres_restart_probe()
                base.docker('start','worker','relay','control')
            save('progress.json',dict(stage='phase_drain',seed=seed,phase=phase,accepted=len(rows)))
            report=drain(token,marker,rows,seconds=120 if phase=='broker_cache_down' else 180)
            if phase=='postgres_kill': report['postgres_recovery']=recovery
            if phase=='worker_kill':
                stale=qa.inside('print(json.dumps(not ledger.finish('+repr(dict(id=claimed['id'],tenant='demo-a',fence=claimed['fence']))+',Proposal("ABSTAIN","stale"))))')
                assert stale, 'STALE_WORKER_COMMITTED_AFTER_RECOVERY'
                report.update(missing_job_repaired=True, stale_completion_rejected=True)
                report['worker_resumption']=resumed
            report.update(phase=phase,elapsed_seconds=round(time.monotonic()-started,3),
                          admission_attempts=sum(r['attempts'] for r in rows),
                          http_statuses={str(s):sum(r['http'].count(s) for r in rows) for s in sorted({s for r in rows for s in r['http']})})
            confirmations=sorted(r['confirmation_seconds'] for r in rows)
            report['confirmation_seconds']={p:round(confirmations[min(len(confirmations)-1,int(len(confirmations)*q))],3)
                                            for p,q in [('p50',.5),('p95',.95),('p99',.99)]}
            reports.append(report); save(str(seed)+'-phases.json',reports)
            print(json.dumps(dict(seed=seed,**report)),flush=True)
        finally:
            if phase=='broker_cache_down': base.docker('start','rabbitmq','redis')
            base.docker('start','worker','relay','control')
    return dict(seed=seed, passed=True, phases=reports, recovery_probes=probes)


def confirmation_edges(seed):
    token=base.http('/v1/lab/session',body={'profile':'ana'})[1]['token']
    marker='SYNTHETIC reliability edges '+str(seed)
    key=base.key(); payload=json.dumps({'question':marker+' discarded'}).encode()
    connection=http.client.HTTPConnection('127.0.0.1',8940,timeout=10)
    try:
        connection.request('POST','/v1/requests',body=payload,headers={
            'Content-Type':'application/json','Authorization':'Bearer '+token,'Idempotency-Key':key})
        # Observe the committed key by a different connection, then discard the
        # original HTTP response WITHOUT ever reading it. No mock response.
        qa.wait(lambda: base.http('/v1/requests/resolve',token,{}, {'Idempotency-Key':key})[0]==200)
    finally:
        connection.close()
    code,row=base.http('/v1/requests',token,{'question':marker+' discarded'},{'Idempotency-Key':key})
    assert code==202
    accepted=[dict(request_id=row['request_id'],key=key)]
    retry_key=base.key()
    base.docker('stop','-t','0','postgres')
    try:
        status,value=base.http('/v1/requests',token,{'question':marker+' unavailable'},{'Idempotency-Key':retry_key})
        assert status==503 and value['code']=='LEDGER_UNAVAILABLE_RETRY_SAME_KEY', 'FALSE_ACCEPT_DURING_DB_OUTAGE'
    finally:
        resume_killed('postgres')
        qa.wait(lambda: base.http('/health/ready')[0]==200)
    assert base.http('/v1/requests/resolve',token,{}, {'Idempotency-Key':retry_key})[0]==404, 'REJECTED_WAS_COMMITTED'
    code,row=base.http('/v1/requests',token,{'question':marker+' unavailable'},{'Idempotency-Key':retry_key})
    assert code==202
    accepted.append(dict(request_id=row['request_id'],key=retry_key))
    save(str(seed)+'-edges-accepted.json',accepted)
    proof=drain(token,marker,accepted)
    proof.update(discarded_confirmation_reconciled=True, database_outage_not_falsely_accepted=True)
    return proof


def restore_probe(seed):
    """Restore a real pg_dump to a separate owned instance/volume, not a mock."""
    name='rag_restore_'+str(seed)
    base.docker('exec','-T','restore-postgres','psql','-U','rag_bootstrap','-d','postgres','-v','ON_ERROR_STOP=1','-c',
        "DO $body$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='rag_app') THEN CREATE ROLE rag_app; END IF; END $body$;")
    base.docker('exec','-T','restore-postgres','psql','-U','rag_bootstrap','-d','postgres','-v','ON_ERROR_STOP=1','-c','CREATE DATABASE '+name)
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    dumped=subprocess.run(base.COMPOSE+['exec','-T','postgres','pg_dump','-U','rag_bootstrap','-Fc','rag'],
                          capture_output=True,timeout=60,creationflags=flags,shell=False)
    assert dumped.returncode==0 and dumped.stdout.startswith(b'PGDMP'), 'BACKUP_FAILED'
    backup=FOLDER/('backup-'+str(seed)+'.dump'); backup.write_bytes(dumped.stdout)
    restored=subprocess.run(base.COMPOSE+['exec','-T','restore-postgres','pg_restore','-U','rag_bootstrap','--exit-on-error','--dbname='+name],
                            input=dumped.stdout,capture_output=True,timeout=60,creationflags=flags,shell=False)
    assert restored.returncode==0, 'RESTORE_FAILED'
    sql="""SELECT json_build_object(
    'requests',(SELECT json_agg(json_build_array(id,state,payload_hash,idem,tenant,actor) ORDER BY id) FROM requests),
    'audit',(SELECT json_agg(json_build_array(request_id,kind) ORDER BY request_id,kind) FROM audit),
    'jobs',(SELECT json_agg(id ORDER BY id) FROM jobs),
    'outbox',(SELECT json_agg(json_build_array(id,request_id,kind,delivered_at IS NOT NULL) ORDER BY id) FROM outbox),
    'inbox',(SELECT json_agg(outbox_id ORDER BY outbox_id) FROM notification_inbox))"""
    actual=[]
    for service,database in (('postgres','rag'),('restore-postgres',name)):
        result=base.docker('exec','-T',service,'psql','-U','rag_bootstrap','-d',database,'-Atqc',sql)
        value=json.loads(result.stdout)
        actual.append(qa.hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    assert actual[0]==actual[1], 'RESTORED_LEDGER_DIFFERS'
    return dict(passed=True,backup=str(backup),sha256=qa.hashlib.sha256(dumped.stdout).hexdigest(),
                restored_ledger_sha256=actual[1],separate_instance=True,independent_failure_domain=False)


def postgres_restart_probe():
    started=time.monotonic(); statuses=[]
    base.docker('kill','-s','SIGKILL','postgres')
    resumed=resume_killed('postgres')
    def ready():
        code,value=base.http('/health/ready'); statuses.append(code)
        return code==200
    proof=dict(passed=False,resumption=resumed)
    try:
        qa.wait(ready,seconds=max(.1,60-(time.monotonic()-started)))
        elapsed=time.monotonic()-started
        assert elapsed<60, 'POSTGRES_RECOVERY_RTO_EXCEEDED'
        proof['passed']=True
        return proof
    finally:
        proof.update(elapsed_seconds=time.monotonic()-started,http_statuses=statuses)
        save('postgres-recovery-last.json',proof)


def global_invariants():
    value=qa.inside('''
with ledger.connect() as db:
 row=db.execute("""WITH active AS MATERIALIZED (
 SELECT tenant,octet_length(question) AS bytes FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT'))
 SELECT (SELECT count(*) FROM active) AS active,
 (SELECT COALESCE(sum(pending),0) FROM admission_shards) AS shard_pending,
 (SELECT COALESCE(sum(bytes),0) FROM active) AS active_bytes,
 (SELECT COALESCE(sum(pending_bytes),0) FROM admission_shards) AS shard_bytes,
 (SELECT count(*) FROM tenant_queue q WHERE q.pending<>(SELECT count(*) FROM active a WHERE a.tenant=q.tenant)
 OR q.pending_bytes<>(SELECT COALESCE(sum(bytes),0) FROM active a WHERE a.tenant=q.tenant)) AS tenant_mismatches,
 (SELECT count(*) FROM requests r WHERE NOT EXISTS(SELECT 1 FROM jobs j WHERE j.id=r.id)) AS missing_jobs,
 (SELECT count(*) FROM requests r WHERE NOT EXISTS(SELECT 1 FROM audit a WHERE a.request_id=r.id AND kind='ACCEPTED')) AS missing_accept_audits,
 (SELECT count(*) FROM requests r WHERE r.state IN ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED') AND NOT EXISTS(SELECT 1 FROM audit a WHERE a.request_id=r.id AND kind='TERMINAL')) AS missing_terminal_audits,
 (SELECT count(*) FROM outbox WHERE delivered_at IS NULL) AS undelivered,
 (SELECT count(*) FROM requests) AS all_receipts""").fetchone()
print(json.dumps({name:int(value) for name,value in row.items()}))
''')
    assert value['active']==value['shard_pending']==0 and value['active_bytes']==value['shard_bytes']==0, 'QUEUE_COUNTER_DRIFT'
    assert all(value[k]==0 for k in ('tenant_mismatches','missing_jobs','missing_accept_audits','missing_terminal_audits','undelivered')), 'GLOBAL_LEDGER_INVARIANT_FAILED'
    return value


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('project')
    parser.add_argument('--probe-only',action='store_true')
    parser.add_argument('--orphan',action='store_true')
    parser.add_argument('--postflight-only',action='store_true')
    args=parser.parse_args()
    FOLDER.mkdir(parents=True,exist_ok=False)
    frozen=sources(); qa.live()
    receipt=dict(passed=False,complete=False,consecutive_passes=0,source_hashes=frozen,
                 production_ha=False,independent_blind_audit=False,gemini_calls=0,rounds=[])
    try:
        assert not base.docker('ps','-q').stdout.strip(), 'QA_PROJECT_ALREADY_RUNNING'
        base.docker('up','-d','--no-build','--wait','--wait-timeout','480')
        receipt['images']=base.service_image_ids()
        receipt['embedded_sources']=qa.inside("import hashlib,importlib\nfrom pathlib import Path\nprint(json.dumps({str(Path(importlib.import_module('rag_app.'+n[:-3]).__file__).relative_to('/app/src')):hashlib.sha256(Path(importlib.import_module('rag_app.'+n[:-3]).__file__).read_bytes()).hexdigest() for n in ('ledger.py','process.py','broker.py','resilience.py')}))")
        assert all(frozen['app/src/'+n]==h for n,h in receipt['embedded_sources'].items()), 'UNTESTED_EMBEDDED_SOURCE'
        if args.probe_only:
            receipt['recovery_probe']=recovery_probe(request_locks=args.orphan,orphan=args.orphan)
            receipt['passed']=receipt['recovery_probe']['passed']
        elif args.postflight_only:
            # Diagnose late harness stages cheaply. Never counts as a full round
            # or yields complete/two-pass approval, even when all probes pass.
            seed=random.SystemRandom().randrange(1000000,9999999)
            qa.wait(lambda:qa.inside("with ledger.connect() as db:\n n=db.execute(\"SELECT count(*) AS n FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT')\").fetchone()['n']\nprint(json.dumps(n==0))"),seconds=180)
            receipt['postflight']=dict(postgres_restart=postgres_restart_probe(),migration_restart=migration_restart_probe(),
                confirmation_edges=confirmation_edges(seed),invariants=global_invariants(),
                restore=restore_probe(seed))
            receipt['passed']=True
        else:
            qa.FOLDER=FOLDER
            # Account for retained requests from a previous failed attempt;
            # never delete/cancel them just to make a new gate appear clean.
            qa.wait(lambda: qa.inside("with ledger.connect() as db:\n n=db.execute(\"SELECT count(*) AS n FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT')\").fetchone()['n']\nprint(json.dumps(n==0))"),seconds=180)
            receipt['database_durability']=qa.inside("with ledger.connect() as db:\n v={n:db.execute('SHOW '+n).fetchone()[n] for n in ('fsync','full_page_writes','synchronous_commit')}\nprint(json.dumps(v))")
            assert all(v=='on' for v in receipt['database_durability'].values()), 'UNSAFE_DATABASE_DURABILITY'
            for _ in range(2):
                seed=random.SystemRandom().randrange(100000,999999)
                save('progress.json',dict(stage='rag_regression',seed=seed,clean_rounds=receipt['consecutive_passes']))
                print(json.dumps(dict(stage='rag_regression',seed=seed,clean_rounds=receipt['consecutive_passes'])),flush=True)
                original=qa.round_(seed)
                save('progress.json',dict(stage='accepted_fault_load',seed=seed,rag_checks=len(original['checks'])))
                result=crash_round(seed); result['rag_checks']=original['checks']
                result['migration_restart']=migration_restart_probe()
                assert result['migration_restart']['passed'], 'MIGRATION_RESTART_REPLAYED_DDL'
                result['confirmation_edges']=confirmation_edges(seed)
                result['invariants']=global_invariants()
                result['restore']=restore_probe(seed)
                receipt['rounds'].append(result)
                assert frozen==sources(), 'SOURCE_CHANGED_RESET_CLEAN_COUNT'
                receipt['consecutive_passes']+=1
                save('receipt.json',receipt)
            receipt['passed']=receipt['complete']=True
    except Exception as error:
        receipt.update(error_type=type(error).__name__, error=str(error)[:1800])
        receipt['failure_stack']=[dict(file=Path(frame.filename).name,line=frame.lineno,function=frame.name)
                                  for frame in traceback.extract_tb(error.__traceback__)]
    finally:
        save('receipt.json',receipt)
        base.docker('stop',check=False); qa.live()
    print(json.dumps(dict(passed=receipt['passed'],rounds=len(receipt['rounds']),receipt=str(FOLDER/'receipt.json'))),flush=True)
    if not receipt['passed']: raise SystemExit(1)


if __name__=='__main__': main()
