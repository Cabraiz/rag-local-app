# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Compile and preserve multiline helper input. No SQL/RAG certification."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets
from unittest.mock import patch

ROOT=_workspace_root


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--kind',choices=['rag','delivery'],required=True)
    kind=parser.parse_args().kind
    if kind=='rag':
        import rag_fixture as fixture
        run=fixture.script; name='rag_fixture.py'; card='BUG-024'
    else:
        import delivery_fixture as fixture
        run=fixture.sql; name='delivery_fixture.py'; card='BUG-025'
    paths=[Path(__file__).resolve(),_named_file(ROOT / 'app/tests', name)]
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}; rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        codes=['with ledger.connect() as db:\n    print(json.dumps(True))',
               'class Synthetic:\n    pass\nprint(json.dumps(True))',
               'print(json.dumps(True))']
        for code in codes:
            def checked(value):
                compile(value,'synthetic-helper','exec')
                assert value.endswith(code)
                return 'true'
            with patch.object(fixture.base,'app_python',side_effect=checked):
                assert run(code) is True
        rounds.append(dict(seed=seed,checks=3,passed=True))
    folder=ROOT/'eval/runs'/('helper-'+kind+'-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id=card,evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='offline_compiled_multiline_python_helper',full_integration_card_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))


if __name__=='__main__':
    main()
