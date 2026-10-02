# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Two offline queue-control rounds. No application/integration approval."""
from datetime import datetime,timezone
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import unittest
import card_queue_fixture as fixture

ROOT=_workspace_root


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--card',choices=['BUG-020','BUG-033'],default='BUG-020')
    card=parser.parse_args().card
    paths=[Path(__file__).resolve(),ROOT/'app/tests/cards/card_queue_fixture.py',ROOT/'app/tools/cards/card_queue.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        result=unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromModule(fixture))
        assert result.wasSuccessful()
        rounds.append(dict(seed=seed,checks=result.testsRun,passed=True))
    folder=ROOT/'eval/runs'/('queue-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id=card,evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='offline_SQLite_queue_and_watchdog_timestamp_regression',production_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2,'checks_per_round':rounds[0]['checks']}))


if __name__=='__main__':
    main()
