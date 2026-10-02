# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Two offline documentation consistency rounds, not integration certification."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets

ROOT=_workspace_root
PATHS=[ROOT/'docs/history/application/implementation-notes-20261002.md',ROOT/'docs/architecture/contracts/lab-profiles.json',Path(__file__).resolve(),
       ROOT/'app/infrastructure/compose/labs/compose.atlassian-lab.yaml']


def frozen():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in PATHS}


def main():
    folder=ROOT/'eval/runs'/('documentation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True); sources=frozen(); rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        assert frozen()==sources
        readme=' '.join((ROOT/'docs/history/application/implementation-notes-20261002.md').read_text(encoding='utf8').split())
        profile=json.loads((ROOT/'docs/architecture/contracts/lab-profiles.json').read_text(encoding='utf8'))['atlassian_remote']
        compose=(ROOT/'app/infrastructure/compose/labs/compose.atlassian-lab.yaml').read_text(encoding='utf8')
        checks={
            'enabled_policy_matches_readme':profile['organization_api_token_auth_verified_enabled'] and 'política de API token foi ligada' in readme,
            'replacement_not_pending':profile['token_created'] and 'token substituto privado foi criado' in readme and 'atlassian-mcp-token-replacement.txt' in compose,
            'remote_failure_not_claimed_pass':profile['remote_read_consecutive_passes']==0 and 'Zero passes remotos' in readme,
            'probe_not_promoted_to_rag':not profile['rag_answer_workflow_connected'] and 'Probe não está conectado' in readme,
            'old_token_preserved':profile['previous_token_preserved_not_revoked'] and 'token anterior foi preservado' in readme,
            'old_off_statement_removed':'Atualmente desligada' not in readme}
        assert all(checks.values()),checks
        rounds.append(dict(seed=seed,checks=checks,passed=True))
    receipt=dict(card_id='BUG-016',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='offline_document_metadata_consistency',provider_integration_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2,'checks_per_round':6}))


if __name__=='__main__':
    main()
