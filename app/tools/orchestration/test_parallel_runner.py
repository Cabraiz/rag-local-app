import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from parallel_runner import dispatch_claim, owner, write_json

ROOT = Path(__file__).resolve().parents[3]


class ParallelRunnerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "docs/orchestration/workstreams.json").read_text(encoding="utf8"))

    def test_four_independent_lanes(self):
        self.assertEqual(len(self.manifest["lanes"]), 4)
        for key in ("id", "branch", "worktree"):
            self.assertEqual(len({lane[key] for lane in self.manifest["lanes"]}), 4)

    def test_exact_model_effort_fast(self):
        self.assertEqual(self.manifest["model"], "gpt-6.1-sol")
        self.assertEqual(self.manifest["reasoning_effort"], "max")
        self.assertEqual(self.manifest["service_tier"], "fast")
        self.assertTrue(self.manifest["user_confirmed_fast"])

    def test_ownership(self):
        self.assertEqual(owner(self.manifest, "RAG-05", "BUG-026"), "corpus")
        self.assertEqual(owner(self.manifest, "RAG-07", "BUG-117"), "providers")
        self.assertEqual(owner(self.manifest, "RAG-12", "BUG-124"), "runtime")
        self.assertEqual(owner(self.manifest, "CF-08", "BUG-080"), "carrefour")

    def test_central_queue_bugs_not_runtime(self):
        self.assertEqual(owner(self.manifest, "RAG-13", "BUG-020"), "central")
        self.assertEqual(owner(self.manifest, "RAG-13", "BUG-033"), "central")
        self.assertEqual(owner(self.manifest, "RAG-13", "BUG-040"), "central")

    def test_shared_files_not_allowlisted(self):
        shared = self.manifest["shared_files"]
        for lane in self.manifest["lanes"]:
            for prefix in lane["allowlist"]:
                for path in shared:
                    self.assertFalse(path.startswith(prefix))
                    self.assertFalse(prefix.startswith(path))

    def test_no_cross_lane_allowlist(self):
        lanes = self.manifest["lanes"]
        for i, lane in enumerate(lanes):
            for other in lanes[i + 1:]:
                for left in lane["allowlist"]:
                    for right in other["allowlist"]:
                        self.assertFalse(left.startswith(right) or right.startswith(left))

    def test_dispatch_cannot_duplicate(self):
        with sqlite3.connect(":memory:") as db:
            self.assertTrue(dispatch_claim(db, "corpus"))
            self.assertFalse(dispatch_claim(db, "corpus"))
            self.assertTrue(dispatch_claim(db, "providers"))
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)

    def test_atomic_progress_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sub/progress.json"
            write_json(path, {"status": "running", "completedItems": 1})
            write_json(path, {"status": "TURN_COMPLETED", "completedItems": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf8"))["completedItems"], 2)
            self.assertFalse(path.with_name("progress.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
