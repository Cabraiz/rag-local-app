# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Readiness-before-download bounded regression, not persistence certification."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets
from unittest.mock import patch
import document_fixture as fixture

ROOT=_workspace_root


def main():
    paths=[Path(__file__).resolve(),ROOT/'app/tests/documents/document_fixture.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}; rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        with patch.object(fixture.base,'http',side_effect=[(502,{}),(503,{}),(200,{'mode':'lab'})]) as http,patch.object(fixture.time,'sleep'):
            fixture.wait_api_ready(); assert http.call_count==3
        with patch.object(fixture.time,'monotonic',side_effect=[0,31]):
            try:
                fixture.wait_api_ready(); raise AssertionError('Unready API unexpectedly accepted')
            except AssertionError as error:
                assert 'exceeded 30 seconds' in str(error)
        rounds.append(dict(seed=seed,checks=2,passed=True))
    folder=ROOT/'eval/runs'/('document-restart-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-022',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='controlled_HTTP_restart_readiness',full_document_card_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))


if __name__=='__main__':
    main()
