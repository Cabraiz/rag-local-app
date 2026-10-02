# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Executable architecture countermodels. NOT tests of the production backend.

Same reviewer, randomized decks frozen before execution, deliberate mutants.
Output authorizes construction only. Does not generate a release attestation.
"""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import secrets

ROOT = Path(__file__).resolve().parent


def deck(policy, seed):
    rng = random.Random(seed)
    checks = []
    def check(name, passed, scope='countermodel'):
        checks.append(dict(name=name, passed=bool(passed), scope=scope))
    n = policy['workload']['offered_unique']
    lag = rng.randint(1, 900)
    primary = set(range(n))
    delayed = set(range(n-lag))
    # Synchronous acknowledgement set is contained in every eligible promoted copy.
    acknowledged_sync = primary & delayed
    check('eligible_sync_copy_conserves_confirmed', not (acknowledged_sync-delayed))
    check('negative_async_ack_loses_confirmed_detected', bool(primary-delayed))
    check('failover_contract_has_flush_eligibility_fencing', all(policy['durability'].values()), 'policy')
    def may_ack(committed, remote_flush, primary_fenced=True, sync_required=None):
        sync_required = policy['durability']['production_sync_flush'] if sync_required is None else sync_required
        return committed and primary_fenced and (remote_flush or not sync_required)
    check('no_quorum_no_confirm', not may_ack(True, False))
    check('negative_disabled_sync_detected', may_ack(True, False, sync_required=False))
    check('no_ack_before_commit_or_with_unfenced_primary', not may_ack(False, True) and not may_ack(True, True, False))
    # Owner generation, SQL clock, expiry and ACL are independent publication guards.
    now = rng.randint(1000, 9000)
    current_fence = rng.randint(2, 500)
    def can_publish(fence, lease, deadline, authorized, ignore_fence=False):
        return (ignore_fence or fence == current_fence) and lease > now and deadline > now and authorized
    check('current_live_owner_can_publish', can_publish(current_fence, now+10, now+20, True))
    check('obsolete_owner_cannot_publish', not can_publish(current_fence-1, now+10, now+20, True))
    check('negative_unfenced_worker_detected', can_publish(current_fence-1, now+10, now+20, True, True))
    check('expired_lease_even_before_reclaim_cannot_publish', not can_publish(current_fence, now-1, now+20, True))
    check('deadline_or_revocation_blocks_publish', not can_publish(current_fence, now+20, now-1, True) and not can_publish(current_fence, now+20, now+30, False))
    check('all_publication_guards_required', all(policy['publication'].values()), 'policy')
    c = policy['control']
    def control_slots(config, ai_available):
        if not config['reserved_cpu'] or config['runs_llm']:
            return 0
        return config['reserved_sql_pool'] if config['separate_process'] else ai_available
    check('control_progress_with_all_ai_slots_busy', control_slots(c, 0) > 0)
    shared_mutant = dict(c, separate_process=False)
    check('negative_shared_saturated_pool_detected', control_slots(shared_mutant, 0) == 0)
    # Admission model creates exactly n unique scoped handles without treating rejects as accepted.
    pending, by_tenant = {}, {}
    offered = list(range(n)); rng.shuffle(offered)
    for rid in offered:
        tenant = rid % policy['workload']['tenants']
        if len(pending) < policy['workload']['global_pending'] and by_tenant.get(tenant,0) < policy['workload']['tenant_pending']:
            pending[(tenant,rid)] = rid
            by_tenant[tenant] = by_tenant.get(tenant,0)+1
    check('100k_model_admissions_conserved', len(pending) == n)
    before = len(pending)
    returned = [pending.setdefault((rid % policy['workload']['tenants'],rid), n+rid) for rid in offered[:2000]]
    check('duplicate_model_submission_returns_same_handle', returned == offered[:2000] and len(pending)==before)
    check('one_more_at_full_capacity_is_rejected', not (len(pending) < policy['workload']['global_pending']))
    check('atomic_count_bytes_headroom_required', all(policy['admission'].values()), 'policy')
    # Round robin is compared with FIFO using a small tenant behind a large flood.
    queues = {'flood':deque(range(n)), 'small':deque([-1])}
    ring = deque(queues)
    prefix = []
    for _ in range(10):
        tenant = ring.popleft()
        if queues[tenant]:
            prefix.append(queues[tenant].popleft())
            if queues[tenant]: ring.append(tenant)
    check('small_tenant_progress_under_flood', -1 in prefix)
    fifo_mutant = deque(list(range(n))+[-1])
    check('negative_fifo_delay_detected', -1 not in [fifo_mutant.popleft() for _ in range(10)])
    r = policy['retention']
    def key_valid(ts):
        return ts <= now+r['future_skew_seconds'] and now-ts <= r['key_age_seconds']
    check('within_window_key_valid', key_valid(now-rng.randint(0,100)))
    old_ts = now-r['key_age_seconds']-rng.randint(1,100)
    check('old_unmapped_key_cannot_be_resurrected', r['reject_old_unmapped_key'] and not key_valid(old_ts))
    check('future_key_rejected', not key_valid(now+r['future_skew_seconds']+1))
    check('retention_covers_retry_and_active_is_not_collected', r['terminal_key_days']*86400 > r['key_age_seconds'] and r['receipt_days'] >= r['terminal_key_days'] and not r['gc_nonterminal'])
    def unmapped_submission(ts, reject_old):
        return 'REJECTED' if reject_old and not key_valid(ts) else 'NEW'
    check('negative_deleted_unaged_key_detected', unmapped_submission(old_ts, True)=='REJECTED' and unmapped_submission(old_ts, False)=='NEW')
    p = policy['production']
    check('production_has_no_fixture_auth_model_or_web', not any(p[k] for k in ('fixture_auth','fixture_model','public_adk_web')), 'policy')
    check('container_and_release_requirements_present', all(v for k,v in p.items() if k not in ('fixture_auth','fixture_model','public_adk_web')), 'policy')
    check('design_gate_does_not_approve_release', policy['gate_a']=='design_only_start_local_implementation' and 'real_integration' in policy['gate_b'], 'policy')
    return {'seed':seed,'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),'offered_model':n,'accepted_model':len(pending),'failover_lag_assumed':lag}


def main():
    files = ['production-policy.json','production-contracts.md','application.md','application-eraser.txt','review_production.py']
    frozen = {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in files}
    policy = json.loads((ROOT/'production-policy.json').read_text(encoding='utf8'))
    seeds = [secrets.randbits(32),secrets.randbits(32)]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder = Path('D:/RAG-Local/eval/runs') / f'production-design-{stamp}-{secrets.token_hex(3)}'
    folder.mkdir(parents=True)
    contract = {'scope':'Gate A design/countermodels only, same reviewer, not independent blind audit','source_hashes':frozen,'seeds':seeds,'production_release':False}
    (folder/'contract.json').write_text(json.dumps(contract,indent=2)+'\n',encoding='utf8')
    rounds = [deck(policy,seed) for seed in seeds]
    unchanged = all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in frozen.items())
    passed = unchanged and all(r['passed']==r['total'] for r in rounds)
    receipt = dict(contract=contract, rounds=rounds, frozen_unchanged=unchanged, gate_a_passed=passed, gate_b_passed=False)
    (folder/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'receipt':str(folder/'receipt.json'),'rounds':[{k:r[k] for k in ('seed','passed','total','accepted_model')} for r in rounds],'gate_a_passed':passed,'gate_b_passed':False}))
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
