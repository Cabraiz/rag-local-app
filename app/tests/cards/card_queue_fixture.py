# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Offline journal controls only. Does not complete application cards."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

PATH=_named_file(_workspace_root / "app/tools", 'card_queue.py')
spec=importlib.util.spec_from_file_location('card_queue',PATH)
queue=importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.original_root,self.original_state=queue.ROOT,queue.STATE
        queue.ROOT=self.root; queue.STATE=self.root/'.local/card-execution'
        self.db=queue.connect(self.root/'test.sqlite3')
        self.db.executemany('INSERT INTO cards(id,title,evidence_type,criteria) VALUES (?,?,?,?)',
            [('C1','One','real_integration','["check"]'),('C2','Two','real_integration','["check"]')])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        queue.ROOT,queue.STATE=self.original_root,self.original_state
        self.temp.cleanup()

    def receipt(self,card='C1',**changes):
        source=self.root/'source.py'; source.write_text('safe\n')
        receipt={'card_id':card,'evidence_type':'real_integration','complete':True,
                 'consecutive_passes':2,'criteria_passed':['check'],
                 'sources_sha256':{'source.py':hashlib.sha256(source.read_bytes()).hexdigest()}}
        receipt.update(changes)
        target=self.root/'eval/receipt.json'; target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(receipt)); return target

    def test_fifo_single_writer(self):
        self.assertEqual(queue.claim(self.db),'C1')
        with self.assertRaisesRegex(ValueError,'WRITER_ALREADY_ACTIVE'):
            queue.claim(self.db)

    def test_database_enforces_one_writer(self):
        queue.claim(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE cards SET status='RUNNING' WHERE id='C2'")

    def test_block_is_not_complete(self):
        queue.claim(self.db); queue.block(self.db,'C1','Missing identity')
        self.assertEqual(queue.claim(self.db),'C2')
        value=queue.projection(self.db)
        self.assertEqual((value['done'],value['blocked']),(0,1))

    def test_bug_appended_last_deduplicated(self):
        bug=queue.append_bug(self.db,'C1','Bug','Reproduction')
        self.assertEqual(queue.append_bug(self.db,'C1','Bug','Reproduction'),bug)
        self.assertEqual([r[0] for r in self.db.execute('SELECT id FROM cards ORDER BY seq')],['C1','C2',bug])

    def test_pending_bug_blocks_parent_approval(self):
        queue.claim(self.db); queue.append_bug(self.db,'C1','Bug','Proof')
        with self.assertRaisesRegex(ValueError,'OPEN_CHILD_BUG'):
            queue.complete(self.db,'C1',self.receipt())

    def test_one_round_cannot_complete(self):
        queue.claim(self.db)
        with self.assertRaisesRegex(ValueError,'SCOPED_TWO_ROUND_PROOF_REQUIRED'):
            queue.complete(self.db,'C1',self.receipt(consecutive_passes=1))

    def test_offline_proof_cannot_complete_real_card(self):
        queue.claim(self.db)
        with self.assertRaisesRegex(ValueError,'SCOPED_TWO_ROUND_PROOF_REQUIRED'):
            queue.complete(self.db,'C1',self.receipt(evidence_type='offline'))

    def test_changed_source_invalidates_receipt(self):
        queue.claim(self.db); receipt=self.receipt()
        (self.root/'source.py').write_text('changed\n')
        with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED_RESET_STREAK'):
            queue.complete(self.db,'C1',receipt)

    def test_same_frozen_sources_complete(self):
        queue.claim(self.db); queue.complete(self.db,'C1',self.receipt())
        self.assertEqual(self.db.execute("SELECT status FROM cards WHERE id='C1'").fetchone()[0],'DONE')

    def test_later_bug_invalidates_prior_approval(self):
        queue.claim(self.db); queue.complete(self.db,'C1',self.receipt())
        queue.append_bug(self.db,'C1','Later bug','Proof')
        self.assertEqual(self.db.execute("SELECT status FROM cards WHERE id='C1'").fetchone()[0],'NEEDS_FIX')

    def test_started_at_is_immutable_across_cards(self):
        with patch.object(queue,'now',return_value='2026-01-01T00:00:00+00:00'):
            queue.claim(self.db)
        with patch.object(queue,'now',return_value='2026-01-01T00:05:00+00:00'):
            queue.block(self.db,'C1','Blocked')
            queue.claim(self.db)
        queue.projection(self.db)
        value=json.loads((queue.STATE/'progress.json').read_text())
        self.assertEqual(value['startedAt'],'2026-01-01T00:00:00+00:00')
        self.assertEqual(value['lastProgressAt'],'2026-01-01T00:05:00+00:00')

    def test_resume_only_after_child_bug_resolved(self):
        queue.claim(self.db); queue.block(self.db,'C1','Blocked')
        bug=queue.append_bug(self.db,'C1','Bug','Proof')
        with self.assertRaisesRegex(ValueError,'OPEN_CHILD_BUG'):
            queue.resume(self.db,'C1','Fix completed')
        self.db.execute("UPDATE cards SET status='DONE' WHERE id=?",(bug,))
        queue.resume(self.db,'C1','Specific bug approved')
        self.assertEqual(self.db.execute("SELECT status FROM cards WHERE id='C1'").fetchone()[0],'RUNNING')

    def test_later_source_changes_invalidate_completed_card(self):
        queue.claim(self.db); queue.complete(self.db,'C1',self.receipt())
        (self.root/'source.py').write_text('changed after approval\n')
        self.assertEqual(queue.review(self.db),['C1'])
        self.assertEqual(queue.review(self.db),[])
        self.assertEqual(self.db.execute("SELECT status FROM cards WHERE id='C1'").fetchone()[0],'NEEDS_FIX')

    def test_child_source_change_invalidates_all_approved_ancestors(self):
        bug=queue.append_bug(self.db,'C2','Child regression','Proof')
        self.db.execute("UPDATE cards SET parent='C1' WHERE id='C2'")
        parent=self.root/'parent.py'; parent.write_text('unchanged\n')
        child=self.root/'child.py'; child.write_text('original\n')
        folder=self.root/'eval'; folder.mkdir()
        for card,source in (('C1',parent),('C2',parent),(bug,child)):
            receipt=folder/(card+'.json')
            receipt.write_text(json.dumps({'sources_sha256':{source.name:hashlib.sha256(source.read_bytes()).hexdigest()}}))
            self.db.execute("UPDATE cards SET status='DONE',receipt=? WHERE id=?",(str(receipt),card))
        self.assertEqual(queue.review(self.db),[])
        child.write_text('changed\n')
        self.assertEqual(set(queue.review(self.db)),{'C1','C2',bug})
        self.assertEqual(queue.review(self.db),[])
        self.assertEqual(self.db.execute("SELECT count(*) FROM cards WHERE status='DONE'").fetchone()[0],0)


if __name__=='__main__':
    unittest.main(verbosity=1)
