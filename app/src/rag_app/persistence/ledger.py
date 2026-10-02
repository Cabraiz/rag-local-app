import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .config import db_options, secret
from .domain import Identity, RequestError, TERMINALS, key_timestamp
from . import resilience


def connect(admin=False):
    return psycopg.connect(**db_options(admin), row_factory=dict_row)


def migrate():
    from psycopg import sql
    schema=Path(__file__).with_name('schema.sql').read_text()
    checksum=hashlib.sha256(schema.encode()).hexdigest()
    with connect(True) as db:
        db.execute('SELECT pg_advisory_xact_lock(714998230)')
        # A successful schema is recorded atomically with its DDL. Compose can
        # rerun the one-shot service during a worker restart: never replay ALTER
        # or live counter initialization for an already-applied schema.
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations('
                   'checksum text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT clock_timestamp())')
        if db.execute('SELECT 1 FROM schema_migrations WHERE checksum=%s',(checksum,)).fetchone():
            return
        if not db.execute("SELECT 1 FROM pg_roles WHERE rolname='rag_app'").fetchone():
            db.execute(sql.SQL('CREATE ROLE rag_app LOGIN PASSWORD {}').format(sql.Literal(secret('db_app'))))
        db.execute(schema)
        db.execute('GRANT USAGE ON SCHEMA public TO rag_app')
        db.execute('GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO rag_app')
        # Only expiring semaphore leases may be deleted; ledger/content stay intact.
        db.execute('GRANT DELETE ON dependency_slots TO rag_app')
        db.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO rag_app')
        db.execute('INSERT INTO schema_migrations(checksum) VALUES (%s)',(checksum,))


def lock_admission(db, tenant=None, skip_locked=False):
    if resilience.enabled():
        if tenant is None: raise ValueError('ADMISSION_TENANT_REQUIRED')
        row=db.execute('SELECT pending FROM admission_shards WHERE id=%s FOR UPDATE'+
                       (' SKIP LOCKED' if skip_locked else ''),(resilience.bucket(tenant),)).fetchone()
        return row['pending'] if row else None
    # One short serialization point in v1; measure/partition before scale release.
    return db.execute('SELECT pending FROM admission WHERE id=1 FOR UPDATE').fetchone()['pending']


def audit(db, rid, kind):
    db.execute('INSERT INTO audit(request_id,kind) VALUES (%s,%s)', (rid, kind))
    db.execute('INSERT INTO outbox(request_id,kind) VALUES (%s,%s)', (rid, kind))


def accept(identity: Identity, question, key):
    digest = hashlib.sha256(json.dumps({'question':question},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with connect() as db:
        now = db.execute('SELECT extract(epoch FROM clock_timestamp()) AS now').fetchone()['now']
        pending = lock_admission(db, identity.tenant)
        existing = db.execute('SELECT id,payload_hash FROM requests WHERE tenant=%s AND actor=%s AND idem=%s', (identity.tenant,identity.actor,key)).fetchone()
        if existing:
            if existing['payload_hash'] != digest: raise RequestError('IDEMPOTENCY_CONFLICT',409)
            return str(existing['id'])
        # Age limits prevent NEW admission after receipt retention; they must not
        # invalidate an existing authorized mapping while that receipt is retained.
        # Payload conflict is checked even for an old known key.
        key_timestamp(key, float(now))
        db.execute('INSERT INTO tenant_queue(tenant) VALUES (%s) ON CONFLICT DO NOTHING',(identity.tenant,))
        tenant_pending = db.execute('SELECT pending FROM tenant_queue WHERE tenant=%s FOR UPDATE',(identity.tenant,)).fetchone()['pending']
        if pending >= (12500 if resilience.enabled() else 100000) or tenant_pending >= 1024: raise RequestError('ADMISSION_LIMIT',429)
        byte_count=len(question.encode('utf8'))
        if os.environ.get('RAG_DELIVERY')=='local_lab':
            total_bytes=(db.execute('SELECT pending_bytes FROM admission_shards WHERE id=%s',(resilience.bucket(identity.tenant),)).fetchone()['pending_bytes'] if resilience.enabled() else db.execute('SELECT pending_bytes FROM admission WHERE id=1').fetchone()['pending_bytes'])
            tenant_bytes=db.execute('SELECT pending_bytes FROM tenant_queue WHERE tenant=%s',(identity.tenant,)).fetchone()['pending_bytes']
            if total_bytes+byte_count>(4194304 if resilience.enabled() else 33554432) or tenant_bytes+byte_count>8388608:
                raise RequestError('ADMISSION_BYTES_LIMIT',429)
            size=db.execute('SELECT pg_database_size(current_database()) AS size').fetchone()['size']
            if size>268435456:
                raise RequestError('LEDGER_STORAGE_ADMISSION_CLOSED',503)
        rid = uuid4()
        snapshot = None
        if os.environ.get('RAG_RETRIEVAL') == 'extractive_lab':
            head = db.execute('SELECT release_id FROM corpus_heads WHERE tenant=%s AND actor=%s', (identity.tenant,identity.actor)).fetchone()
            if head: snapshot = head['release_id']
        db.execute("INSERT INTO requests(id,tenant,actor,idem,payload_hash,question,state,deadline,source_snapshot) VALUES (%s,%s,%s,%s,%s,%s,'ACCEPTED',clock_timestamp()+interval '900 seconds',%s)",(rid,identity.tenant,identity.actor,key,digest,question,snapshot))
        db.execute('INSERT INTO jobs(id) VALUES (%s)',(rid,))
        if os.environ.get('RAG_DELIVERY')=='local_lab':
            db.execute('UPDATE requests SET retention_managed=true WHERE id=%s',(rid,))
        if resilience.enabled():
            db.execute('UPDATE admission_shards SET pending=pending+1,pending_bytes=pending_bytes+%s WHERE id=%s',(byte_count,resilience.bucket(identity.tenant)))
        else:
            db.execute('UPDATE admission SET pending=pending+1,pending_bytes=pending_bytes+%s WHERE id=1',(byte_count,))
        db.execute('UPDATE tenant_queue SET pending=pending+1,pending_bytes=pending_bytes+%s WHERE tenant=%s',(byte_count,identity.tenant))
        audit(db,rid,'ACCEPTED')
    return str(rid)


def read(identity, rid=None):
    with connect() as db:
        select = 'SELECT id,state,created_at,deadline,attempts,result,source_snapshot,content_expired FROM requests WHERE tenant=%s AND actor=%s'
        if rid:
            row = db.execute(select+' AND id=%s',(identity.tenant,identity.actor,rid)).fetchone()
            if not row: raise RequestError('NOT_FOUND',404)
            return delivery(db, identity, row)
        return [delivery(db, identity, row) for row in db.execute(select+' ORDER BY created_at DESC,id DESC LIMIT 50',(identity.tenant,identity.actor)).fetchall()]


def abstention():
    return dict(kind='ABSTAIN',text='Não há evidência autorizada suficiente para entregar uma resposta.',citations=[])


def citation_valid(db, who, snapshot, value):
    # Share lock serializes the authorization check with revocation writes.
    try:
        from uuid import UUID
        did, cid, release = (UUID(value[k]) for k in ('document_id','chunk_id','release_id'))
    except (ValueError,TypeError,KeyError): return False
    if str(release) != str(snapshot): return False
    row = db.execute("SELECT c.quote,d.content_hash,d.acl_epoch FROM corpus_chunks c JOIN corpus_documents d ON d.id=c.document_id JOIN corpus_releases r ON r.id=d.release_id WHERE c.id=%s AND d.id=%s AND d.release_id=%s AND d.tenant=%s AND d.actor=%s AND r.state='READY' AND NOT d.revoked AND (d.valid_until IS NULL OR d.valid_until>clock_timestamp()) FOR SHARE OF d",(cid,did,release,who.tenant,who.actor)).fetchone()
    return bool(row and all(value.get(k)==row[k] for k in ('quote','content_hash','acl_epoch')))


def delivery(db, who, row):
    snapshot = row.pop('source_snapshot')
    result = row['result']
    if result and result.get('kind') == 'EXTRACTIVE':
        cites = result.get('citations',[])
        if len(cites)!=1 or not citation_valid(db,who,snapshot,cites[0]): row['result']=abstention()
    return row


def resolve(identity, key):
    with connect() as db:
        row = db.execute('SELECT id FROM requests WHERE tenant=%s AND actor=%s AND idem=%s',(identity.tenant,identity.actor,key)).fetchone()
        if not row: raise RequestError('NOT_FOUND',404)
        return dict(request_id=str(row['id']))


def terminal(db, row, state, result=None, worker=False):
    params = [state,Jsonb(result) if result else None,row['id']]
    where = "id=%s AND state NOT IN ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED')"
    if worker:
        where += " AND state='RUNNING' AND fence=%s AND lease_until>clock_timestamp() AND deadline>clock_timestamp()"
        params.append(row['fence'])
    changed = db.execute('UPDATE requests SET state=%s,result=%s,lease_until=NULL,terminal_at=clock_timestamp() WHERE '+where+' RETURNING id,octet_length(question) AS bytes',params).fetchone()
    if not changed: return False
    audit(db,row['id'],'TERMINAL')
    if resilience.enabled():
        db.execute('UPDATE admission_shards SET pending=pending-1,pending_bytes=pending_bytes-%s WHERE id=%s',(changed['bytes'],resilience.bucket(row['tenant'])))
    else:
        db.execute('UPDATE admission SET pending=pending-1,pending_bytes=pending_bytes-%s WHERE id=1',(changed['bytes'],))
    db.execute('UPDATE tenant_queue SET pending=pending-1,pending_bytes=pending_bytes-%s WHERE tenant=%s',(changed['bytes'],row['tenant']))
    return True


def cancel(identity, rid):
    with connect() as db:
        lock_admission(db, identity.tenant)
        row = db.execute('SELECT * FROM requests WHERE id=%s AND tenant=%s AND actor=%s FOR UPDATE',(rid,identity.tenant,identity.actor)).fetchone()
        if not row: raise RequestError('NOT_FOUND',404)
        if row['state'] not in TERMINALS: terminal(db,row,'CANCELLED')
    return read(identity,rid)


def claim(rid=None):
    with connect() as db:
        if rid is not None:
            row=db.execute("SELECT id FROM requests WHERE id=%s AND state IN ('ACCEPTED','RETRY_WAIT') AND next_at<=clock_timestamp() AND deadline>clock_timestamp() FOR UPDATE SKIP LOCKED",(rid,)).fetchone()
            if not row: return None
            lease=60 if os.environ.get('RAG_GEMINI_RESPONSES')=='free_lab' else 30
            return db.execute("UPDATE requests SET state='RUNNING',fence=fence+1,attempts=attempts+1,lease_until=clock_timestamp()+%s*interval '1 second' WHERE id=%s RETURNING *",(lease,rid)).fetchone()
        tenant = db.execute("SELECT q.tenant FROM tenant_queue q WHERE EXISTS (SELECT 1 FROM requests r JOIN jobs j ON j.id=r.id WHERE r.tenant=q.tenant AND r.state IN ('ACCEPTED','RETRY_WAIT') AND r.next_at<=clock_timestamp() AND r.deadline>clock_timestamp()) ORDER BY q.last_claim,q.tenant LIMIT 1 FOR UPDATE OF q SKIP LOCKED").fetchone()
        if not tenant: return None
        row = db.execute("SELECT r.id FROM requests r JOIN jobs j ON j.id=r.id WHERE r.tenant=%s AND r.state IN ('ACCEPTED','RETRY_WAIT') AND r.next_at<=clock_timestamp() AND r.deadline>clock_timestamp() ORDER BY r.created_at,r.id LIMIT 1 FOR UPDATE OF r SKIP LOCKED",(tenant['tenant'],)).fetchone()
        if not row: return None
        db.execute('UPDATE tenant_queue SET last_claim=clock_timestamp() WHERE tenant=%s',(tenant['tenant'],))
        lease=60 if os.environ.get('RAG_GEMINI_RESPONSES')=='free_lab' else 30
        return db.execute("UPDATE requests SET state='RUNNING',fence=fence+1,attempts=attempts+1,lease_until=clock_timestamp()+%s*interval '1 second' WHERE id=%s RETURNING *",(lease,row['id'])).fetchone()


def finish(row, proposal):
    with connect() as db:
        lock_admission(db, row['tenant'])
        current=db.execute('SELECT * FROM requests WHERE id=%s FOR UPDATE',(row['id'],)).fetchone()
        if not current or current['fence']!=row['fence']: return False
        if proposal.kind == 'ABSTAIN' and not proposal.citations:
            result=dict(kind='ABSTAIN',text=proposal.text,citations=[])
        elif proposal.kind=='EXTRACTIVE' and os.environ.get('RAG_RETRIEVAL')=='extractive_lab':
            if len(proposal.citations)!=1 or proposal.text!='Trecho da fonte:\n'+proposal.citations[0].quote:
                raise RequestError('UNVERIFIED_PROPOSAL',422)
            cites=[asdict(c) for c in proposal.citations]
            who=Identity(current['tenant'],current['actor'])
            result=dict(kind=proposal.kind,text=proposal.text,citations=cites) if citation_valid(db,who,current['source_snapshot'],cites[0]) else abstention()
        else: raise RequestError('UNVERIFIED_PROPOSAL',422)
        if proposal.model is not None:
            from .gemini_lab import MODEL
            if os.environ.get('RAG_GEMINI_RESPONSES')!='free_lab' or proposal.model!=MODEL:
                raise RequestError('UNVERIFIED_MODEL',422)
            result['model']=proposal.model
        return terminal(db,current,'SUCCEEDED',result,worker=True)


def recover():
    if resilience.enabled():
        with connect() as db:
            # Repairing one orphan must not wait on its FK/locked request and
            # abort deadline/lease recovery for every other tenant.
            db.execute("INSERT INTO jobs(id) SELECT id FROM requests r WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT') AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.id=r.id) LIMIT 128 FOR KEY SHARE OF r SKIP LOCKED ON CONFLICT DO NOTHING")
            # Exclude busy partitions BEFORE LIMIT: a backlog of 128 locked
            # candidates must not hide expired work belonging to another tenant.
            rows=db.execute("SELECT r.id,r.tenant FROM requests r JOIN admission_shards s ON s.id=(('x'||substr(md5(r.tenant),1,8))::bit(32)::bigint % 8) WHERE r.state IN ('ACCEPTED','RUNNING','RETRY_WAIT') AND (r.deadline<=clock_timestamp() OR (r.state='RUNNING' AND r.lease_until<=clock_timestamp())) ORDER BY r.deadline,r.id LIMIT 128 FOR UPDATE OF s,r SKIP LOCKED").fetchall()
        for candidate in rows:
            with connect() as db:
                if lock_admission(db,candidate['tenant'],skip_locked=True) is None:
                    continue
                row=db.execute('SELECT *,clock_timestamp() AS now FROM requests WHERE id=%s FOR UPDATE SKIP LOCKED',(candidate['id'],)).fetchone()
                if not row or row['state'] in TERMINALS: continue
                if row['deadline']<=row['now']: terminal(db,row,'EXPIRED')
                elif row['state']=='RUNNING' and row['lease_until']<=row['now']:
                    if row['attempts']>=3: terminal(db,row,'FAILED_FINAL')
                    else: retry_in(db,row,'WORKER_LEASE_EXPIRED')
        return
    with connect() as db:
        lock_admission(db)
        db.execute("INSERT INTO jobs(id) SELECT id FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT') ON CONFLICT DO NOTHING")
        rows = db.execute("SELECT * FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT') AND (deadline<=clock_timestamp() OR (state='RUNNING' AND lease_until<=clock_timestamp())) ORDER BY deadline LIMIT 128 FOR UPDATE SKIP LOCKED").fetchall()
        for row in rows:
            now = db.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            if row['deadline'] <= now: terminal(db,row,'EXPIRED')
            elif row['attempts'] >= 3: terminal(db,row,'FAILED_FINAL')
            else:
                db.execute("UPDATE requests SET state='RETRY_WAIT',fence=fence+1,lease_until=NULL,next_at=clock_timestamp()+interval '2 seconds' WHERE id=%s",(row['id'],))
                audit(db,row['id'],'RETRY_'+str(row['attempts']))


def heartbeat(role):
    with connect() as db:
        db.execute('INSERT INTO worker_health VALUES (%s,clock_timestamp()) ON CONFLICT(role) DO UPDATE SET at=excluded.at',(role,))


def retry_in(db,row,code):
    wait=resilience.delay(row['attempts'])
    db.execute("UPDATE requests SET state='RETRY_WAIT',fence=fence+1,lease_until=NULL,failure_code=%s,next_at=clock_timestamp()+%s*interval '1 second' WHERE id=%s",(code,wait,row['id']))
    audit(db,row['id'],'RETRY_'+str(row['attempts']))


def fail(row,error):
    # Only safe error codes are persisted. No SDK/SQL exception text.
    with connect() as db:
        lock_admission(db,row['tenant'])
        current=db.execute('SELECT *,clock_timestamp() AS now FROM requests WHERE id=%s FOR UPDATE',(row['id'],)).fetchone()
        if current['state']!='RUNNING' or current['fence']!=row['fence'] or current['lease_until']<=current['now']: return False
        code=error.code if isinstance(error,RequestError) else 'WORKFLOW_TIMEOUT' if isinstance(error,TimeoutError) else 'WORKFLOW_FAILURE'
        db.execute('UPDATE requests SET failure_code=%s WHERE id=%s',(code,row['id']))
        if current['deadline']<=current['now']: return terminal(db,current,'EXPIRED')
        if resilience.transient(error) and current['attempts']<3:
            retry_in(db,current,code)
            return True
        return terminal(db,current,'FAILED_FINAL',worker=True)


def health():
    with connect() as db:
        version = db.execute('SELECT version FROM schema_version').fetchone()['version']
        return dict(schema=version, mode='lab', production_ready=False, workflow='adk_gemini_grounded_graph' if os.environ.get('RAG_GEMINI_RESPONSES')=='free_lab' else 'adk_extractive_graph' if os.environ.get('RAG_RETRIEVAL')=='extractive_lab' else 'adk_abstention_fixture',
                    admission_policy='partitioned_count_bytes_v3' if resilience.enabled() else 'count_and_bytes_lab_v2' if os.environ.get('RAG_DELIVERY')=='local_lab' else 'count_only_v1', workers=db.execute('SELECT role,at FROM worker_health').fetchall())
