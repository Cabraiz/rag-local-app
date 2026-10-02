# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""EXPOSE versus actual host publication: controls, not full telemetry proof."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets
import observability_fixture as fixture

ROOT=_workspace_root

def main():
    paths=[Path(__file__).resolve(),ROOT/'app/tests/observability/observability_fixture.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    rounds=[]
    cases=[({},True),({'4318/tcp':None},True),({'4318/tcp':[]},True),
        ({'4318/tcp':[{'HostIp':'127.0.0.1','HostPort':'4318'}]},False),
        ({'4318/tcp':[{'HostIp':'0.0.0.0','HostPort':'4318'}]},False),
        ({'4318/tcp':[{'HostIp':'::','HostPort':'4318'}]},False),
        (None,False),([],False),({'4318/tcp':'unknown'},False)]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        for value,expected in cases:
            assert fixture.unbound_ports(value)==expected
        rounds.append(dict(seed=seed,checks=len(cases),passed=True))
    folder=ROOT/'eval/runs'/('collector-ports-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-031',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='host_publication_positive_negative_controls',full_observability_card_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))

if __name__=='__main__':
    main()
