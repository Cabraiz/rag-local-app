# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Protocol case-insensitive header regression only, not full RAG approval."""
from datetime import datetime,timezone
from email.message import Message
import hashlib
import json
from pathlib import Path
import random
import secrets
from document_fixture import safe_headers

ROOT=_workspace_root


def main():
    paths=[Path(__file__).resolve(),ROOT/'app/tests/documents/document_fixture.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        rng=random.Random(seed)
        m=Message(); m['cache-control']='no-store'
        assert m.get('Cache-Control')=='no-store' and dict(m).get('Cache-Control') is None
        for _ in range(100):
            expected={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store','Content-Disposition':'attachment; filename="synthetic.txt"'}
            mixed={''.join(c.upper() if rng.randrange(2) else c.lower() for c in key):value for key,value in expected.items()}
            assert safe_headers(mixed)
        assert not safe_headers({'X-Content-Type-Options':'nosniff','Cache-Control':'public','Content-Disposition':'attachment; filename="synthetic.txt"'})
        assert not safe_headers({'X-Content-Type-Options':'nosniff','Cache-Control':'no-store','Content-Disposition':'inline'})
        assert not safe_headers({'Cache-Control':'no-store','Content-Disposition':'attachment;'})
        rounds.append(dict(seed=seed,header_case_variants=100,negative_controls=3,passed=True))
    folder=ROOT/'eval/runs'/('headers-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-017',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='offline_HTTP_header_case_regression',full_document_card_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))


if __name__=='__main__':
    main()
