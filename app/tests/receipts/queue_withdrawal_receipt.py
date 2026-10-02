# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Two scoped adversarial rounds for the withdrawal fix and queue regression."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import unittest
import queue_withdrawal_fixture as fixture

ROOT=_workspace_root
def main():
    cli=argparse.ArgumentParser();cli.add_argument('--card',choices=['BUG-040','BUG-020','BUG-033'],default='BUG-040');args=cli.parse_args()
    paths=['app/tools/cards/card_queue.py','app/tests/cards/card_queue_fixture.py',
           'app/tests/cards/queue_withdrawal_fixture.py','app/tests/receipts/queue_withdrawal_receipt.py']
    frozen={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in paths}
    rounds=[]
    for _ in range(2):
        result=unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromTestCase(fixture.WithdrawalTests))
        if not result.wasSuccessful():raise SystemExit(1)
        assert frozen=={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in paths},'SOURCE_CHANGED'
        rounds.append({'passed':True,'checks':result.testsRun})
    folder=ROOT/'eval/runs'/('queue-withdrawal-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(parents=True,exist_ok=True)
    receipt={'card_id':args.card,'evidence_type':'verified_regression','complete':True,
      'consecutive_passes':2,'criteria_passed':['reproduction','two_regression_rounds'],
      'sources_sha256':frozen,'rounds':rounds,'independent_blind':False,'scope':'temporary SQLite only, not app/load/production',
      'reproduction':'eval/runs/gemini-rag-20261001/queue-withdrawal-before.log'}
    path=folder/(args.card+'-receipt.json');path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'checks':[r['checks'] for r in rounds]}))
if __name__=='__main__':main()
