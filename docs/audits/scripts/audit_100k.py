# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Isolated durable protocol reference, NOT a RAG/HTTP/Postgres load test.

Case generator and oracle are frozen before execution. SQLite/FULL/WAL on D:
is a process-crash fixture; it is not the proposed shared runtime. No networking.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import secrets
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
TERMINALS = ('SUCCEEDED', 'FAILED_FINAL', 'EXPIRED', 'CANCELLED')
TOTAL = 100_000


def connect(path):
    db = sqlite3.connect(path, timeout=20)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('PRAGMA foreign_keys=ON')
    # Working-set cache only; durability and transaction boundaries are unchanged.
    # This fixture is not a throughput benchmark for the production database.
    db.execute('PRAGMA cache_size=-65536')
    return db


def schema(db):
    db.executescript('''
      CREATE TABLE requests(id INTEGER PRIMARY KEY, tenant INTEGER NOT NULL,
        idem TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
        fence INTEGER NOT NULL DEFAULT 0, deadline INTEGER NOT NULL DEFAULT 1000,
        lease_until INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
        next_at INTEGER NOT NULL DEFAULT 0, response_kind TEXT,
        UNIQUE(tenant,idem));
      CREATE TABLE jobs(id INTEGER PRIMARY KEY REFERENCES requests(id));
      CREATE TABLE audit(id INTEGER PRIMARY KEY REFERENCES requests(id), outcome TEXT NOT NULL);
      CREATE TABLE outbox(event TEXT PRIMARY KEY, id INTEGER NOT NULL REFERENCES requests(id), kind TEXT NOT NULL);
      CREATE TABLE inbox(event TEXT PRIMARY KEY);
      CREATE INDEX due ON requests(state,next_at,id);
      CREATE INDEX expired ON requests(state,deadline,id);
      CREATE INDEX lease ON requests(state,lease_until,id);
    ''')


def terminal(db, rid, fence, state, kind=None, enforce_fence=True):
    # This function executes INSIDE a caller transaction, with no external calls.
    if state == 'SUCCEEDED' and kind not in ('ANSWER', 'ABSTAIN'):
        return False
    where = "id=? AND state NOT IN ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED')"
    params = [state, kind, rid]
    if enforce_fence:
        where += ' AND fence=?'
        params.append(fence)
    if state in ('SUCCEEDED','FAILED_FINAL'):
        where += " AND state='RUNNING' AND deadline>20"
    row = db.execute('UPDATE requests SET state=?, response_kind=?, lease_until=NULL WHERE '+where, params)
    if row.rowcount != 1:
        return False
    db.execute('INSERT INTO audit VALUES (?,?)', (rid, state))
    db.execute('INSERT INTO outbox VALUES (?,?,?)', (f'terminal:{rid}', rid, 'terminal'))
    return True


def crash_child(path, rid, phase):
    db = connect(path)
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT fence FROM requests WHERE id=?', (rid,)).fetchone()
    assert terminal(db, rid, row[0], 'SUCCEEDED', 'ANSWER')
    if phase == 'after':
        db.commit()
    # Abrupt exit of THIS disposable child only; not user processes or services.
    os._exit(92 if phase == 'after' else 91)


def one_round(folder, seed):
    start = time.perf_counter()
    rng = random.Random(seed)
    order = list(range(TOTAL))
    rng.shuffle(order)
    names = [('cancel',3000), ('expire',4000), ('permanent',3000),
             ('abstain',4000), ('transient',8000), ('crashed_worker',8000)]
    categories = {}
    position = 0
    for name, count in names:
        for rid in order[position:position+count]:
            categories[rid] = name
        position += count
    expected = {rid: {'cancel':'CANCELLED','expire':'EXPIRED','permanent':'FAILED_FINAL'}
                .get(categories.get(rid), 'SUCCEEDED') for rid in range(TOTAL)}
    # Hash generator inputs BEFORE execution, oracle independent of persisted outcomes.
    scenario_hash = hashlib.sha256(json.dumps(categories, sort_keys=True).encode()).hexdigest()
    checks = []
    def check(name, ok, detail=None):
        checks.append({'name':name,'pass':bool(ok),'detail':detail})
    path = folder / f'round-{seed}.sqlite3'
    assert not path.exists()
    db = connect(path)
    schema(db)
    for offset in range(0, TOTAL, 1000):
        with db:
            rows = [(rid, rid % 100, str(rid), f'payload-{rid}', 'ACCEPTED')
                    for rid in range(offset, offset+1000)]
            db.executemany('INSERT INTO requests(id,tenant,idem,payload,state) VALUES (?,?,?,?,?)', rows)
            db.executemany('INSERT INTO jobs VALUES (?)', [(r[0],) for r in rows])
            db.executemany('INSERT INTO outbox VALUES (?,?,?)', [(f'accepted:{r[0]}',r[0],'accepted') for r in rows])
    check('100k_accepted_persisted', db.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == TOTAL)
    # Repeat 20k idempotency keys without accepting additional requests.
    with db:
        for rid in order[:20000]:
            db.execute('INSERT OR IGNORE INTO requests(id,tenant,idem,payload,state) VALUES (?,?,?,?,?)',
                       (rid+TOTAL, rid%100, str(rid), f'payload-{rid}', 'ACCEPTED'))
    check('20k_duplicate_submissions_deduplicated', db.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == TOTAL)
    conflict_count = sum(db.execute('SELECT payload FROM requests WHERE tenant=? AND idem=?',
                         (rid%100,str(rid))).fetchone()[0] != 'conflicting-payload' for rid in order[:2000])
    check('2000_conflicting_keys_detected', conflict_count == 2000)
    crashed = [rid for rid in order if categories.get(rid) == 'crashed_worker']
    with db:
        db.executemany("UPDATE requests SET state='RUNNING',fence=1,attempts=1,lease_until=5 WHERE id=?", [(rid,) for rid in crashed])
        db.executemany('UPDATE requests SET deadline=1 WHERE id=?', [(rid,) for rid,c in categories.items() if c == 'expire'])
    # Commit/rollback tested with real subprocess exits against this same 100k ledger.
    db.close()
    process_results = []
    for phase, rid in zip(('before','after'), crashed[:2]):
        p = subprocess.run([sys.executable, __file__, '--child',str(path),str(rid),phase],
                           capture_output=True, timeout=20, shell=False,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        process_results.append({'phase':phase,'returncode':p.returncode})
        assert p.returncode == (91 if phase=='before' else 92), p.stderr.decode(errors='replace')
    db = connect(path)
    check('process_crash_before_commit_rolls_back', db.execute('SELECT state FROM requests WHERE id=?',(crashed[0],)).fetchone()[0]=='RUNNING'
          and db.execute('SELECT COUNT(*) FROM audit WHERE id=?',(crashed[0],)).fetchone()[0]==0)
    check('process_crash_after_commit_retains_terminal_and_outbox',
          db.execute('SELECT state FROM requests WHERE id=?',(crashed[1],)).fetchone()[0]=='SUCCEEDED'
          and db.execute('SELECT COUNT(*) FROM outbox WHERE event=?',(f'terminal:{crashed[1]}',)).fetchone()[0]==1)
    # Injection of missing schedules. This is a synthetic corrupted control-plane state.
    missing = order[50000:50333]
    with db:
        db.executemany('DELETE FROM jobs WHERE id=?',[(rid,) for rid in missing])
    orphan_sql = "SELECT COUNT(*) FROM requests r LEFT JOIN jobs j ON r.id=j.id WHERE j.id IS NULL AND r.state NOT IN ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED')"
    orphan_before = db.execute(orphan_sql).fetchone()[0]
    check('negative_no_reconciler_detected', orphan_before > 0, orphan_before)
    with db:
        db.execute("INSERT OR IGNORE INTO jobs SELECT id FROM requests WHERE state NOT IN ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED')")
        recovered = db.execute("UPDATE requests SET state='ACCEPTED', fence=fence+1, lease_until=NULL WHERE state='RUNNING' AND lease_until<=20").rowcount
    check('missing_jobs_recreated', db.execute(orphan_sql).fetchone()[0]==0)
    check('expired_leases_recovered', recovered == 7999, recovered)
    stale_rid = crashed[2]
    # A recovered request must actually have a new RUNNING owner. Otherwise both
    # guarded and deliberately unfenced finalization reject the ACCEPTED state,
    # making the negative control incapable of detecting the missing fence.
    with db:
        db.execute("UPDATE requests SET state='RUNNING', fence=fence+1, attempts=attempts+1, lease_until=100 WHERE id=? AND state='ACCEPTED'", (stale_rid,))
    check('stale_control_has_live_new_owner', db.execute('SELECT state,fence FROM requests WHERE id=?', (stale_rid,)).fetchone() == ('RUNNING', 3))
    db.execute('BEGIN IMMEDIATE')
    safe = terminal(db, stale_rid, 1, 'SUCCEEDED', 'ANSWER')
    db.rollback()
    check('late_worker_fence_rejected', not safe)
    db.execute('BEGIN IMMEDIATE')
    unsafe = terminal(db, stale_rid, 1, 'SUCCEEDED', 'ANSWER', enforce_fence=False)
    db.rollback()
    check('negative_missing_fence_detected', unsafe)
    retries = 0
    for offset in range(0, TOTAL, 1000):
        with db:
            for rid in order[offset:offset+1000]:
                state, fence = db.execute('SELECT state,fence FROM requests WHERE id=?',(rid,)).fetchone()
                if state in TERMINALS:
                    continue
                category = categories.get(rid, 'normal')
                if category in ('expire','cancel'):
                    assert terminal(db,rid,fence,expected[rid])
                    continue
                db.execute("UPDATE requests SET state='RUNNING',fence=fence+1,attempts=attempts+1 WHERE id=? AND state IN ('ACCEPTED','RETRY_WAIT')", (rid,))
                fence = db.execute('SELECT fence FROM requests WHERE id=?',(rid,)).fetchone()[0]
                if category == 'transient':
                    db.execute("UPDATE requests SET state='RETRY_WAIT',next_at=10 WHERE id=?",(rid,))
                    db.execute('INSERT INTO outbox VALUES (?,?,?)',(f'retry:{rid}',rid,'retry'))
                    db.execute("UPDATE requests SET state='RUNNING',fence=fence+1,attempts=attempts+1 WHERE id=? AND next_at<=20",(rid,))
                    fence += 1
                    retries += 1
                outcome = 'FAILED_FINAL' if category == 'permanent' else 'SUCCEEDED'
                kind = None if outcome != 'SUCCEEDED' else ('ABSTAIN' if category == 'abstain' else 'ANSWER')
                assert terminal(db,rid,fence,outcome,kind), rid
    # Reopen to derive conservation from durable records, not in-memory counters.
    db.close()
    db = connect(path)
    actual = dict(db.execute('SELECT id,state FROM requests'))
    counts = dict(db.execute('SELECT state,COUNT(*) FROM requests GROUP BY state'))
    check('all_accepted_terminal', len(actual)==TOTAL and all(s in TERMINALS for s in actual.values()), counts)
    check('independent_outcome_oracle', actual == expected)
    check('no_nonterminal_orphans', db.execute(orphan_sql).fetchone()[0]==0)
    check('one_audit_per_terminal', db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]==TOTAL)
    check('one_terminal_outbox_per_request', db.execute("SELECT COUNT(*) FROM outbox WHERE kind='terminal'").fetchone()[0]==TOTAL)
    check('retries_durably_scheduled', retries==8000 and db.execute("SELECT COUNT(*) FROM outbox WHERE kind='retry'").fetchone()[0]==8000)
    check('abstention_not_factual_success', db.execute("SELECT COUNT(*) FROM requests WHERE response_kind='ABSTAIN'").fetchone()[0]==4000)
    check('expired_without_inference', db.execute("SELECT COUNT(*) FROM requests WHERE state='EXPIRED' AND attempts=0").fetchone()[0]==4000)
    duplicates_rejected = 0
    with db:
        for rid in order[:20000]:
            fence = db.execute('SELECT fence FROM requests WHERE id=?',(rid,)).fetchone()[0]
            duplicates_rejected += not terminal(db,rid,fence,'SUCCEEDED','ANSWER')
    check('20k_terminal_redeliveries_no_second_commit', duplicates_rejected==20000)
    # Notification duplicates + lost client notifications do not control terminal truth.
    events = [r[0] for r in db.execute("SELECT event FROM outbox WHERE kind='terminal'")]
    with db:
        db.executemany('INSERT OR IGNORE INTO inbox VALUES (?)', [(e,) for e in events+events[:5000]])
    check('5000_duplicate_notifications_idempotent', db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0]==TOTAL)
    check('2000_lost_client_notifications_pollable', all(actual[rid] in TERMINALS for rid in order[:2000]))
    db.execute('BEGIN IMMEDIATE')
    db.execute('DELETE FROM outbox WHERE event=?',(f'terminal:{order[0]}',))
    broken = db.execute("SELECT COUNT(*) FROM requests r LEFT JOIN outbox o ON o.event='terminal:'||r.id WHERE r.state IN ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED') AND o.event IS NULL").fetchone()[0]
    check('negative_missing_terminal_outbox_detected', broken==1)
    db.rollback()
    check('database_integrity', db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall())
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    db.close()
    return {'seed':seed,'requests':TOTAL,'outcomes':counts,'passed':sum(x['pass'] for x in checks),
            'total_checks':len(checks),'checks':checks,'scenario_sha256':scenario_hash,
            'subprocess_crashes':process_results,'fixture_elapsed_seconds':round(time.perf_counter()-start,3),
            'database_bytes':path.stat().st_size,'database':str(path)}


def adversarial_counterexamples():
    # Protocol models, not failures injected into real PostgreSQL/broker/scheduler.
    primary = set(range(TOTAL))
    standby = set(range(TOTAL-317))
    acknowledged = primary.copy()
    lost_after_async_failover = acknowledged - standby
    slots, hung = 32, 32
    expiry_task_can_run = hung < slots
    tenant_a = list(range(TOTAL))
    tenant_b = [-1]
    global_fifo_prefix = (tenant_a + tenant_b)[:1000]
    idem = {'old-key':'original-request'}
    idem.clear()  # Unsafe retention policy counterexample, not current app configuration.
    duplicate_after_cleanup = 'old-key' not in idem
    # Hypothetical admission backpressure; rejects are not counted as lost accepted requests.
    limit = 10000
    return {
      'async_failover': {'acknowledged':TOTAL,'lost_on_promoting_lagging_replica':len(lost_after_async_failover),
                        'boundary':'Abstract replication model, NOT live Postgres failover'},
      'shared_control_pool': {'ai_slots':slots,'hung_ai':hung,'expiry_can_run':expiry_task_can_run,
                             'boundary':'Abstract pool model, NOT actual worker performance'},
      'tenant_queue_delay': {'tenant_b_served_in_first_1000': -1 in global_fifo_prefix,
                            'requests_ahead_of_tenant_b': TOTAL,
                            'boundary':'Finite FIFO delay counterexample; NOT proof of infinite starvation'},
      'idempotency_cleanup': {'duplicate_created_after_key_retention':duplicate_after_cleanup,
                             'boundary':'Retention counterexample; no cleanup implemented'},
      'bounded_admission': {'offered':TOTAL,'accepted':limit,'rejected_explicit':TOTAL-limit,
                            'forgotten_accepted':0,'boundary':'Admission capacity is an ASSUMPTION, not measured'},
      'drain_time_lower_bound': [{'assumed_complete_request_rps': rate,
                                 'seconds_for_100k_without_retries': math.ceil(TOTAL/rate)} for rate in (10,100,1000)]
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--child',nargs=3)
    args = parser.parse_args()
    if args.child:
        crash_child(args.child[0],int(args.child[1]),args.child[2])
    names = ('application.md','application-eraser.txt','audit_100k.py')
    frozen = {n:(ROOT/n).read_bytes() for n in names}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder = Path('D:/RAG-Local/eval/runs') / f'admission100k-{stamp}-{secrets.token_hex(3)}'
    folder.mkdir(parents=True,exist_ok=False)
    seeds = [secrets.randbits(32),secrets.randbits(32)]
    contract = {'offered_unique':TOTAL,'duplicate_submissions':20000,'duplicate_terminal_deliveries':20000,
      'oracle':'Every accepted request exists durably with exactly one terminal audit/outbox; rejected submission is not accepted.',
      'seeds':seeds,'source_hashes':{n:hashlib.sha256(b).hexdigest() for n,b in frozen.items()},
      'not_tested':['HTTP concurrency','Postgres locks/failover','RabbitMQ confirms/ACK','LLM/GPU throughput','Power loss/disk loss','Real RAG accuracy'],
      'method':'Same-reviewer frozen adversarial protocol reference; not independent blind audit'}
    (folder/'contract.json').write_text(json.dumps(contract,indent=2)+'\n',encoding='utf-8')
    rounds = []
    for seed in seeds:
        print(json.dumps({'starting_seed':seed,'run_folder':str(folder)}),flush=True)
        result = one_round(folder,seed)
        rounds.append(result)
        (folder/f'round-{seed}.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:result[k] for k in ('seed','requests','outcomes','passed','total_checks','fixture_elapsed_seconds')}),flush=True)
    unchanged = all((ROOT/n).read_bytes()==b for n,b in frozen.items())
    reference_passed = unchanged and all(r['passed']==r['total_checks'] for r in rounds)
    receipt = {'contract':contract,'rounds':rounds,'counterexamples':adversarial_counterexamples(),
               'frozen_inputs_unchanged':unchanged,
               'verdict':('REFERENCE_PASSED_ARCHITECTURE_HAS_UNRESOLVED_GAPS' if reference_passed
                          else 'REFERENCE_FAILED_ARCHITECTURE_NOT_APPROVED'),
               'generated_at_utc':datetime.now(timezone.utc).isoformat()}
    (folder/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'receipt':str(folder/'receipt.json'),'frozen_inputs_unchanged':unchanged,
                      'reference_passed':all(r['passed']==r['total_checks'] for r in rounds)}),flush=True)
    raise SystemExit(0 if unchanged and all(r['passed']==r['total_checks'] for r in rounds) else 1)


if __name__ == '__main__':
    main()
