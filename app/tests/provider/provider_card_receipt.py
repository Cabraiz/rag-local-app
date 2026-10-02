# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Bind executed scoped provider evidence to queue criteria; never invent passes."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from provider_usage_proof import validate_usage_proof

ROOT=_workspace_root

def validate_billing_proof(path, smoke_at, *, now=None):
    """Bind a recent, manually observed Console record, not a spending guarantee."""
    if path is None:
        raise ValueError('BILLING_PROOF_REQUIRED')
    path = Path(path).resolve()
    if not path.is_relative_to((ROOT / 'eval/runs').resolve()):
        raise ValueError('BILLING_PROOF_OUTSIDE_EVAL')
    try:
        raw = path.read_bytes()
        if len(raw) > 32768:
            raise ValueError()
        proof = json.loads(raw)
        if not isinstance(proof, dict):
            raise ValueError()
    except (OSError, ValueError, UnicodeError):
        raise ValueError('BILLING_PROOF_INVALID') from None
    project = 'gen-lang-client-0580698701'
    url = 'https://console.cloud.google.com/billing/linkedaccount?project=' + project
    if proof.get('project_id') != project or proof.get('url') != url:
        raise ValueError('BILLING_PROOF_PROJECT')
    if (proof.get('source') != 'authenticated Google Cloud Console visible UI'
            or any(type(proof.get(k)) is not int or proof[k] != 0
                   for k in ('billing_changes', 'credential_changes', 'inference_calls'))):
        raise ValueError('BILLING_PROOF_SOURCE')
    if proof.get('no_billing_account_at_observation') is not True:
        raise ValueError('BILLING_PROOF_NOT_FREE')
    try:
        observed = datetime.fromisoformat(proof['observed_at_utc'])
        started = datetime.fromisoformat(smoke_at)
        current = now or datetime.now(timezone.utc)
        if any(t.tzinfo is None or t.utcoffset() is None for t in (observed, started, current)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError('BILLING_PROOF_TIME') from None
    if not (0 <= (current - observed).total_seconds() <= 1800 and observed <= started <= current):
        raise ValueError('BILLING_PROOF_NOT_CURRENT')
    return dict(project_id=project, observed_at_utc=observed.isoformat(), url=url,
                source=proof['source'], sha256=hashlib.sha256(raw).hexdigest(),
                path=path.relative_to(ROOT).as_posix(), no_billing_account_at_observation=True,
                scope='Current project linkage observed before this batch; not a lifetime spending guarantee')

def main():
    cli=argparse.ArgumentParser(); cli.add_argument('--card',choices=['RAG-01','RAG-02','RAG-03'],required=True)
    cli.add_argument('--proof',required=True); cli.add_argument('--billing-proof'); cli.add_argument('--usage-persistence-proof'); args=cli.parse_args()
    path=Path(args.proof).resolve(); assert path.is_relative_to(ROOT/'eval/runs')
    value=json.loads(path.read_text(encoding='utf8')); rounds=value['rounds']; assert len(rounds)==2
    billing = None
    usage = None
    if args.card=='RAG-02':
        billing = validate_billing_proof(args.billing_proof, value.get('started_at'))
        if value.get('fixture') is True:
            raise ValueError('SIMULATED_PROVIDER_PROOF')
        usage = validate_usage_proof(value, args.usage_persistence_proof)
        assert value['passed'] is True
        required={'real_grounded_graph_enabled','food_real_model_reported','food_canonical_evidence','food_replay_same_receipt','blank_rejected_'+repr('   ')}
        assert all(required<=set(r['checks']) and r['passed'] for r in rounds)
        criteria=['real_inference','no_billing','persistent_daily_limit']
    else:
        assert value['checks_passed'] and value['complete_online_gate']
        assert all(r['source_gates']=={'github':'passed','jira':'passed'} for r in rounds)
        assert all({'KAN-1','KAN-2'}<=set(r['jira_ids']) for r in rounds)
        required={'github_live_authenticated','github_scope','mcp_gateway_real_response','ana_denied_/v1/lab/integrations/feed'}
        assert all(required<=set(r['checks']) for r in rounds)
        criteria=['KAN-1','KAN-2','authorized_read_only'] if args.card=='RAG-01' else ['repository_allowlist','real_read','negative_authorization']
    sources=dict(value['source_sha256'])
    if billing:
        sources[billing['path']] = billing['sha256']
        sources[usage['physical_persistence_proof']] = usage['persistence_sha256']
    sources[str(Path(__file__).resolve().relative_to(ROOT))]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for name,digest in sources.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,'STALE_PROOF'
    receipt={'card_id':args.card,'evidence_type':'real_provider','complete':True,'consecutive_passes':2,
       'criteria_passed':criteria,'sources_sha256':sources,'rounds':rounds,'source_proof':str(path.relative_to(ROOT)),
       'verified_at':datetime.now(timezone.utc).isoformat(),'independent_blind':False,
       'scope':'existing synthetic Free lab; live provider read/selection only, not writes or production'}
    if billing:
        receipt['billing_observation'] = billing
        receipt['usage_observation'] = usage
    target=path.with_name(args.card+'-'+path.stem+'-receipt.json')
    target.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(target)}))
if __name__=='__main__':main()
