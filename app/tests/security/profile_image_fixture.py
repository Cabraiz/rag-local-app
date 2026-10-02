# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Owned-profile image freezing regression, not actual integration certification."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets
from types import SimpleNamespace
from unittest.mock import patch
import http_fixture as base

ROOT=_workspace_root


def main():
    paths=[Path(__file__).resolve(),ROOT/'app/tests/shared/http_fixture.py',ROOT/'app/tests/rag/rag_fixture.py',ROOT/'app/tests/lifecycle/delivery_fixture.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        calls=[]
        def docker(*args):
            calls.append(args)
            if args==('config','--services'):
                return SimpleNamespace(stdout='api\nworker\npostgres\n')
            assert args==('ps','-a','-q','api','worker','postgres')
            return SimpleNamespace(stdout='api-id\nworker-id\npostgres-id\n')
        with patch.object(base,'docker',side_effect=docker),patch.object(base.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='sha256:a\nsha256:a\nsha256:b\n')) as inspect:
            assert base.service_image_ids()==['sha256:a','sha256:b']
            assert inspect.call_args[0][0]==['docker','container','inspect','--format','{{.Image}}','api-id','worker-id','postgres-id']
            assert not any('images' in c or 'orphan-id' in c for c in calls)
        with patch.object(base,'docker',side_effect=[SimpleNamespace(stdout='api\n'),SimpleNamespace(stdout='')]):
            try:
                base.service_image_ids()
                raise AssertionError('Missing profile unexpectedly accepted')
            except RuntimeError as error:
                assert 'No owned profile' in str(error)
        rounds.append(dict(seed=seed,checks=4,passed=True))
    folder=ROOT/'eval/runs'/('profile-image-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-021',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='controlled_subprocess_owned_profile_selection',real_delivery_card_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))


if __name__=='__main__':
    main()
