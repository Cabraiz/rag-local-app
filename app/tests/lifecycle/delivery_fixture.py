# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Frozen real SQL inbox/HTTP/recovery/managed-retention synthetic lab proof."""
from datetime import datetime,timezone
import hashlib
import json
import secrets
import time
import http_fixture as base
import current_queue_fixture as qa

base.COMPOSE[3]='rag-local-qa-delivery-20261001'

base.COMPOSE += ['-f',str(base.ROOT/'infrastructure/compose/runtime/compose.delivery.yaml')]
ACTIVE_FOLDER=None


def frozen():
    result=qa.frozen()
    for path in (base.ROOT/'tests/lifecycle/delivery_fixture.py',base.ROOT/'infrastructure/compose/runtime/compose.delivery.yaml',
                 base.ROOT.parent/'docs/architecture/contracts/delivery-contract.json'):
        result[str(path.relative_to(base.ROOT.parent))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def sql(code):
    return json.loads(base.app_python('import json\nfrom rag_app import ledger,delivery\n'+code).splitlines()[-1])


def reconcile():
    return sql("with ledger.connect() as db:\n a=db.execute('SELECT pending,pending_bytes FROM admission WHERE id=1').fetchone()\n r=db.execute(\"SELECT count(*) AS pending,COALESCE(sum(octet_length(question)),0) AS pending_bytes FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT')\").fetchone()\n q=db.execute(\"SELECT count(*) AS n FROM tenant_queue q WHERE q.pending!=(SELECT count(*) FROM requests r WHERE r.tenant=q.tenant AND r.state IN ('ACCEPTED','RUNNING','RETRY_WAIT')) OR q.pending_bytes!=(SELECT COALESCE(sum(octet_length(question)),0) FROM requests r WHERE r.tenant=q.tenant AND r.state IN ('ACCEPTED','RUNNING','RETRY_WAIT'))\").fetchone()['n']\n print(json.dumps(a==r and q==0))")


def run_round(seed):
    checks=[]
    def check(name,ok):
        checks.append(dict(name=name,passed=bool(ok)))
        (ACTIVE_FOLDER/f'progress-{seed}.json').write_text(json.dumps(checks,indent=2),encoding='utf8')
        if not ok:
            raise AssertionError(name)
    a=base.http('/v1/lab/session',body={'profile':'ana'})[1]['token']
    b=base.http('/v1/lab/session',body={'profile':'bruno'})[1]['token']
    marker='delivery'+str(seed)
    def submit(text=marker,key=None):
        return base.http('/v1/requests',a,{'question':text},{'Idempotency-Key':key or base.key()})
    check('initial_exact_count_and_byte_reconciliation',reconcile())
    # Only this fixture-owned project's delivery process is stopped.
    base.docker('stop','delivery')
    try:
        idem=base.key(); code,value=submit(marker+' café',idem)
        check('real_request_accepted',code==202)
        rid=value['request_id']; row=base.wait_state(rid,a,{'SUCCEEDED'})
        check('terminal_independent_of_dispatcher',row['result']['kind']=='ABSTAIN')
        check('duplicate_mapping_preserved',submit(marker+' café',idem)[1]['request_id']==rid)
        proof=sql("rid="+repr(rid)+"\nwith ledger.connect() as db:\n ids=[r['id'] for r in db.execute('SELECT id FROM outbox WHERE request_id=%s',(rid,)).fetchall()]\n print(json.dumps({'outbox':len(ids),'delivered':db.execute('SELECT count(*) AS n FROM notification_inbox WHERE outbox_id=ANY(%s::bigint[])',(ids,)).fetchone()['n']}))")
        check('outbox_durable_while_dispatcher_stopped',proof=={'outbox':2,'delivered':0})
        crash=sql("exec("+repr("def crash(ids):\n raise RuntimeError('SYNTHETIC_CRASH_AFTER_DURABLE_INBOX')\ntry:\n delivery.dispatch(crash)\nexcept RuntimeError:\n pass\nwith ledger.connect() as db:\n n=db.execute('SELECT count(*) AS n FROM notification_inbox n JOIN outbox o ON o.id=n.outbox_id WHERE o.request_id=%s',("+repr(rid)+",)).fetchone()['n']\n p=db.execute('SELECT count(*) AS n FROM outbox WHERE request_id=%s AND delivered_at IS NULL',("+repr(rid)+",)).fetchone()['n']\n print(json.dumps([n,p]))")+")")
        check('crash_after_inbox_commit_before_ack_is_durable',crash==[2,2])
        replay=sql("delivery.dispatch(); delivery.dispatch();\nwith ledger.connect() as db:\n n=db.execute('SELECT count(*) AS n FROM notification_inbox n JOIN outbox o ON o.id=n.outbox_id WHERE o.request_id=%s',("+repr(rid)+",)).fetchone()['n']\n p=db.execute('SELECT count(*) AS n FROM outbox WHERE request_id=%s AND delivered_at IS NULL',("+repr(rid)+",)).fetchone()['n']\n print(json.dumps([n,p]))")
        check('replay_deduplicates_then_acknowledges',replay==[2,0])
        ids=sql("with ledger.connect() as db:\n print(json.dumps([r['id'] for r in db.execute('SELECT id FROM outbox WHERE request_id=%s ORDER BY id',("+repr(rid)+",)).fetchall()]))")
        events=base.http('/v1/events?after='+str(ids[0]-1),a)
        check('authenticated_owner_gets_minimal_events',events[0]==200 and [e['kind'] for e in events[1]['events'] if e['request_id']==rid]==['ACCEPTED','TERMINAL'] and marker not in json.dumps(events))
        foreign=base.http('/v1/events?after='+str(ids[0]-1),b)
        check('operator_cannot_poll_client_events',foreign[0]==403)
        for tenant,actor in [('foreign-tenant','demo-user'),('demo-a','foreign-actor')]:
            isolated=sql('from rag_app.domain import Identity\nprint(json.dumps(delivery.read(Identity('+repr(tenant)+','+repr(actor)+'),'+str(ids[0]-1)+')["events"]))')
            check('canonical_inbox_isolation_'+tenant+'_'+actor,isolated==[])
        check('unauthenticated_poll_denied',base.http('/v1/events')[0] in (401,403))
        check('negative_event_cursor_rejected',base.http('/v1/events?after=-1',a)[0]==422)
    finally:
        base.docker('start','delivery')
    check('byte_reservations_released_after_terminal',reconcile())
    # Negative quota controls inflate counters temporarily, restore exact current SQL sums in finally.
    base.docker('stop','worker')
    # Freeze only the fixture-owned background retainer before aging its records.
    base.docker('stop','delivery')
    try:
        for table,quota,name in (('admission',33554432,'global_byte_limit'),('tenant_queue',8388608,'tenant_byte_limit')):
            clause="id=1" if table=='admission' else "tenant='demo-a'"
            try:
                sql("with ledger.connect() as db:\n db.execute("+repr('UPDATE '+table+' SET pending_bytes=%s WHERE '+clause)+",("+str(quota)+",))\n print(json.dumps(True))")
                code,error=submit(marker+name)
                check(name+'_never_202',code==429 and error['code']=='ADMISSION_BYTES_LIMIT')
            finally:
                sql("with ledger.connect() as db:\n db.execute(\"UPDATE admission SET pending_bytes=(SELECT COALESCE(sum(octet_length(question)),0) FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT')) WHERE id=1\")\n db.execute(\"UPDATE tenant_queue q SET pending_bytes=(SELECT COALESCE(sum(octet_length(question)),0) FROM requests r WHERE r.tenant=q.tenant AND r.state IN ('ACCEPTED','RUNNING','RETRY_WAIT'))\")\n print(json.dumps(True))")
        accepted=submit(marker+' pending managed')[1]['request_id']
        check('pending_request_reserves_utf8_bytes',reconcile())
        # Retention can only scrub fixture-owned, managed terminal content. Never existing records.
        sql("with ledger.connect() as db:\n db.execute(\"UPDATE requests SET terminal_at=clock_timestamp()-interval '31 days' WHERE id=%s\",("+repr(rid)+",))\n db.execute(\"UPDATE requests SET terminal_at=clock_timestamp()-interval '31 days' WHERE id=%s\",("+repr(accepted)+",))\n print(json.dumps(True))")
        count=sql("print(json.dumps(delivery.retain()))")
        retained=base.http('/v1/requests/'+rid,a)[1]
        pending=base.http('/v1/requests/'+accepted,a)[1]
        check('managed_old_terminal_content_scrubbed',count>=1 and retained['content_expired'] and retained['result'] is None and retained['state']=='SUCCEEDED')
        check('nonterminal_never_scrubbed',pending['state']=='ACCEPTED' and not pending['content_expired'])
        check('retained_receipt_still_resolves_same_key',base.http('/v1/requests/resolve',a,{}, {'Idempotency-Key':idem})[1]['request_id']==rid)
        check('retained_payload_hash_still_detects_conflict',submit(marker+' DIFFERENT',idem)[0]==409)
        check('same_original_payload_replays_after_content_retention',submit(marker+' café',idem)[1]['request_id']==rid)
        check('retention_is_idempotent',sql('print(json.dumps(delivery.retain()))')==0)
        base.http('/v1/requests/'+accepted+'/cancel',a,{})
        check('cancel_releases_bytes_exactly_once',reconcile())
        base.http('/v1/requests/'+accepted+'/cancel',a,{})
        check('duplicate_cancel_cannot_underflow',reconcile())
    finally:
        base.docker('start','delivery')
        base.docker('start','worker')
    base.docker('restart','delivery')
    check('dispatcher_restart_preserves_pollable_receipt',base.http('/v1/requests/'+rid,a)[0]==200)
    check('final_global_tenant_count_and_bytes_conservation',reconcile())
    check('managed_retention_does_not_touch_unmanaged',sql("with ledger.connect() as db:\n print(json.dumps(db.execute('SELECT count(*) AS n FROM requests WHERE NOT retention_managed AND content_expired').fetchone()['n']==0))"))
    logs=base.docker('logs','--no-log-prefix','delivery','worker','api').stdout
    check('content_absent_from_delivery_logs',marker not in logs)
    return dict(seed=seed,checks=checks,passed=True)


def main():
    global ACTIVE_FOLDER
    qa.live()
    if base.docker('ps','--status','running','-q').stdout.strip():
        raise SystemExit('Project already running: refuse ownership')
    folder=base.ROOT.parent/'eval/runs'/('delivery-real-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True); ACTIVE_FOLDER=folder
    sources=frozen(); seeds=[secrets.randbits(32) for _ in range(2)]
    contract=dict(scope='actual_HTTP_SQL_inbox_request_retention_byte_quota_process_restart',independent_blind=False,
                  seeds=seeds,sources_sha256=sources,oracle_frozen_before_inputs=True,external_broker=False,
                  isolated_project=base.COMPOSE[3],endpoint=base.BASE,current_roles=True,cloud_calls=0)
    (folder/'contract.json').write_text(json.dumps(contract,indent=2),encoding='utf8')
    rounds=[]; streak=0; error=None
    try:
        base.docker('up','-d','--wait','--wait-timeout','120')
        pinned=base.service_image_ids(); contract['images']=pinned
        for seed in seeds:
            assert frozen()==sources,'Changed sources reset streak'
            result=run_round(seed); rounds.append(result); streak+=1
            (folder/f'round-{seed}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
            print(json.dumps(dict(seed=seed,checks=len(result['checks']),streak=streak)),flush=True)
        assert frozen()==sources and base.service_image_ids()==pinned,'Changed sources/images reset streak'
    except Exception as exc:
        error=str(exc); streak=0
    finally:
        stopped=base.docker('stop',check=False)
        qa.live()
        receipt=dict(card_id='RAG-10',evidence_type='real_integration',contract=contract,sources_sha256=sources,
            rounds=rounds,error=error,consecutive_passes=streak,complete=streak==2 and not error,
            criteria_passed=['outbox_dispatch','retention','byte_limits','recovery'] if streak==2 else [],
            gate_b_passed=False,containers_stopped=stopped.returncode==0,volumes_preserved=True)
        (folder/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps(dict(receipt=str(folder/'receipt.json'),error=error,streak=streak)),flush=True)
    raise SystemExit(0 if streak==2 and not error else 1)


if __name__=='__main__':
    main()
