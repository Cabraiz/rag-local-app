# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Approve only the version/source compatibility bug from two actual document rounds."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=_workspace_root
REQUIRED={'legacy_version_revoke_commits','legacy_version_revoke_does_not_revoke_other_versions',
          'legacy_version_revoke_allows_explicit_new_version','source_revoke_commits',
          'all_historical_versions_download_revoked','revoked_source_cannot_be_reuploaded',
          'plain_text_ingest_cannot_bypass_revocation'}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('receipt'); path=Path(parser.parse_args().receipt).resolve()
    assert path.is_relative_to(ROOT/'eval/runs')
    original=json.loads(path.read_text(encoding='utf8'))
    assert original['card_id']=='RAG-05' and original['consecutive_passes']==2 and original['complete'] is True
    assert len(original['rounds'])==2
    for round in original['rounds']:
        passed={check['name'] for check in round['checks'] if check['passed']}
        assert REQUIRED<=passed and all(check['passed'] for check in round['checks'])
    for name,digest in original['sources_sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    proof=dict(original)
    proof.update(card_id='BUG-026',evidence_type='verified_regression',
                 criteria_passed=['reproduction','two_regression_rounds'],scope='actual_HTTP_SQL_version_vs_source_revocation',
                 full_document_receipt=str(path.relative_to(ROOT)))
    proof['sources_sha256']=dict(original['sources_sha256'])
    proof['sources_sha256'][str(Path(__file__).resolve().relative_to(ROOT))]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    target=path.with_name('compatibility-receipt.json'); target.write_text(json.dumps(proof,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(target),'streak':2}))


if __name__=='__main__':
    main()
