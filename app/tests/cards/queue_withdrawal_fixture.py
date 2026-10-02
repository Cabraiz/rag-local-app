# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Adversarial lifecycle cases on temporary SQLite, never the application DB."""
import json
import unittest
from card_queue_fixture import JournalTests, queue

class WithdrawalTests(JournalTests):
    def retired_child(self):
        bug=queue.append_bug(self.db,'C1','User pause, not a bug','Explicit user withdrawal')
        self.db.execute("UPDATE cards SET status='WITHDRAWN_USER_PAUSE' WHERE id=?",(bug,))
        return bug

    def test_withdrawn_child_does_not_block_resume(self):
        queue.claim(self.db); queue.block(self.db,'C1','Paused')
        bug=self.retired_child()
        queue.resume(self.db,'C1','User resumed; withdrawal retained')
        self.assertEqual(self.db.execute('SELECT status FROM cards WHERE id=?',(bug,)).fetchone()[0],'WITHDRAWN_USER_PAUSE')

    def test_withdrawn_child_does_not_block_completion(self):
        queue.claim(self.db); self.retired_child()
        queue.complete(self.db,'C1',self.receipt())

    def test_withdrawn_child_does_not_invalidate_approval(self):
        bug=self.retired_child()
        queue.claim(self.db); receipt=self.receipt()
        self.db.execute("UPDATE cards SET status='DONE',receipt=? WHERE id='C1'",(str(receipt),))
        self.assertEqual(queue.review(self.db),[])
        self.assertEqual(self.db.execute('SELECT status FROM cards WHERE id=?',(bug,)).fetchone()[0],'WITHDRAWN_USER_PAUSE')

    def test_withdrawn_is_resolved_not_done(self):
        self.retired_child()
        self.db.execute("UPDATE cards SET status='DONE' WHERE id IN ('C1','C2')")
        result=queue.projection(self.db)
        progress=json.loads((queue.STATE/'progress.json').read_text())
        self.assertTrue(progress['all_cards_complete'])
        self.assertEqual(result['done'],2)
        self.assertEqual(result['withdrawn'],1)
        self.assertEqual(progress['progressCurrent'],3)

if __name__=='__main__':unittest.main(verbosity=0)
