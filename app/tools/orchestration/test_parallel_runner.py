import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import parallel_runner as runner
from parallel_runner import dispatch_claim, owner, write_json

ROOT = Path(__file__).resolve().parents[3]


class ParallelRunnerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "docs/orchestration/workstreams.json").read_text(encoding="utf8"))

    def test_four_independent_lanes(self):
        self.assertEqual(len(self.manifest["lanes"]), 4)
        for key in ("id", "branch", "worktree"):
            self.assertEqual(len({lane[key] for lane in self.manifest["lanes"]}), 4)

    def test_supervisor_receipts_are_private_and_git_ignored(self):
        result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "eval/reports/orchestration/proof.json"],
                                capture_output=True, shell=False, creationflags=runner.NO_WINDOW)
        self.assertEqual(result.returncode, 0)

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
        self.assertEqual(owner(self.manifest, "RAG-13", "BUG-126"), "central")

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
        with closing(sqlite3.connect(":memory:")) as db:
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

    def test_transient_sharing_violation_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            replace = os.replace
            count = 0
            def locked(source, target):
                nonlocal count
                count += 1
                if count <= 2:
                    raise PermissionError(13, "held reader")
                replace(source, target)
            with patch.object(runner.os, "replace", side_effect=locked):
                write_json(path, {"status": "running"}, delay=0)
            self.assertEqual(count, 3)
            self.assertEqual(json.loads(path.read_text())["status"], "running")

    def test_permanent_sharing_failure_is_bounded_and_preserves_old_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            write_json(path, {"status": "original"})
            with patch.object(runner.os, "replace", side_effect=PermissionError(13, "denied")) as replace:
                with self.assertRaises(PermissionError):
                    write_json(path, {"status": "new"}, attempts=4, delay=0)
            self.assertEqual(replace.call_count, 4)
            self.assertEqual(json.loads(path.read_text())["status"], "original")
            self.assertEqual(len(list(path.parent.glob("progress.json.*.tmp"))), 1)

    @unittest.skipUnless(os.name == "nt", "real Windows sharing semantics")
    def test_real_windows_reader_reproduces_old_failure_then_retry_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            write_json(path, {"status": "original"})
            reader = path.open("rb")
            old_temporary = path.with_name("legacy.tmp")
            old_temporary.write_text('{"status":"new"}')
            try:
                with self.assertRaises(PermissionError):
                    os.replace(old_temporary, path)
                release = threading.Timer(0.1, reader.close)
                release.start()
                try:
                    write_json(path, {"status": "recovered"})
                finally:
                    release.join(timeout=2)
            finally:
                reader.close()
            self.assertEqual(json.loads(path.read_text())["status"], "recovered")

    def test_snapshot_fault_does_not_escape_or_print_raw_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            progress = {}
            with patch.object(runner, "write_json", side_effect=PermissionError("secret-not-for-logs")):
                self.assertFalse(runner.snapshot(state, "progress.json", progress, progress))
            self.assertEqual(progress["supervisorFaults"], 1)
            diagnostic = (state / "supervisor-faults.jsonl").read_text()
            self.assertNotIn("secret-not-for-logs", diagnostic)
            self.assertEqual(json.loads(diagnostic)["error_type"], "PermissionError")

    def test_resume_cas_preserves_previous_identity_and_rejects_duplicate(self):
        with closing(sqlite3.connect(":memory:")) as db:
            self.assertTrue(dispatch_claim(db, "runtime"))
            db.execute("UPDATE jobs SET status='FAILED',thread_id='same-thread',callback_status='QUEUED'")
            db.commit()
            self.assertFalse(dispatch_claim(db, "runtime", "wrong-thread"))
            self.assertTrue(dispatch_claim(db, "runtime", "same-thread"))
            self.assertFalse(dispatch_claim(db, "runtime", "same-thread"))
            self.assertEqual(db.execute("SELECT status,thread_id FROM jobs").fetchone(),
                             ("STARTING", "same-thread"))
            self.assertEqual(db.execute("SELECT previous_status,thread_id,previous_callback_status "
                                        "FROM recovery_history").fetchone(),
                             ("FAILED", "same-thread", "QUEUED"))

    def test_running_or_unknown_lane_cannot_be_resumed(self):
        with closing(sqlite3.connect(":memory:")) as db:
            self.assertFalse(dispatch_claim(db, "runtime", "same-thread"))
            self.assertTrue(dispatch_claim(db, "runtime"))
            db.execute("UPDATE jobs SET status='RUNNING',thread_id='same-thread'")
            db.commit()
            self.assertFalse(dispatch_claim(db, "runtime", "same-thread"))
            self.assertEqual(db.execute("SELECT count(*) FROM recovery_history").fetchone()[0], 0)

    def recovery_job(self, directory):
        terminal = Path(directory) / "terminal.json"
        write_json(terminal, {"status": "FAILED", "lane": "runtime", "thread_id": "same-thread"})
        return {"resume_from": str(terminal), "resume_terminal_sha256": hashlib.sha256(terminal.read_bytes()).hexdigest(),
                "resume_thread": "same-thread", "lane": "runtime", "previous_pids": [101, 102],
                "worktree": directory, "branch": "codex/rag-runtime"}

    def test_resume_rejects_live_previous_writer_before_git_or_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.recovery_job(directory)
            with patch.object(runner, "process_alive", return_value=True), \
                    patch.object(runner.subprocess, "check_output") as git:
                with self.assertRaisesRegex(RuntimeError, "STILL_ALIVE"):
                    runner.validate_recovery(job)
                git.assert_not_called()

    def test_resume_rejects_changed_terminal_sha(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.recovery_job(directory)
            write_json(job["resume_from"], {"status": "TURN_COMPLETED"})
            with patch.object(runner, "process_alive") as alive:
                with self.assertRaisesRegex(RuntimeError, "TERMINAL_CHANGED"):
                    runner.validate_recovery(job)
                alive.assert_not_called()

    def test_resume_rejects_wrong_terminal_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.recovery_job(directory)
            job["resume_thread"] = "another-thread"
            with self.assertRaisesRegex(RuntimeError, "IDENTITY_MISMATCH"):
                runner.validate_recovery(job)

    def test_resume_requires_dead_pids_and_original_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            job = self.recovery_job(directory)
            with patch.object(runner, "process_alive", return_value=False) as alive, \
                    patch.object(runner.subprocess, "check_output", return_value="codex/rag-runtime\n"):
                runner.validate_recovery(job)
                self.assertEqual(alive.call_count, 2)
            with patch.object(runner, "process_alive", return_value=False), \
                    patch.object(runner.subprocess, "check_output", return_value="main\n"):
                with self.assertRaisesRegex(RuntimeError, "BRANCH_CHANGED"):
                    runner.validate_recovery(job)

    def test_read_only_process_probe_recognizes_current_process(self):
        self.assertTrue(runner.process_alive(os.getpid()))
        for pid in (0, -1, None, True):
            with self.assertRaises(ValueError):
                runner.process_alive(pid)

    def test_resume_command_names_existing_session_no_new_worktree(self):
        job = {"codex": "codex", "model": "gpt-6.1-sol", "reasoning_effort": "max",
               "service_tier": "fast", "canonical_root": "D:/RAG-Local", "resume_thread": "same-thread"}
        command = runner.build_command(job, Path("state"), Path("worktree"))
        self.assertEqual(command[-3:], ["resume", "same-thread", "-"])
        self.assertNotIn("--worktree", command)
        self.assertNotIn("--last", command)
        self.assertIn('model_reasoning_effort="max"', command)
        self.assertIn('service_tier="fast"', command)

    def test_partial_utf8_last_json_without_newline_and_invalid_output(self):
        child = unittest.mock.Mock()
        child.poll.return_value = 0
        output = io.BytesIO(b'not-json\n[]\n' + json.dumps({"type": "turn.completed", "text": "ação"},
                                                       ensure_ascii=False).encode("utf8"))
        events = []
        runner.drain_events(output, child, events.append)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"], "ação")

    def test_reasoning_events_are_not_material_progress_or_json_writes(self):
        with closing(sqlite3.connect(":memory:")) as db, patch.object(runner, "snapshot") as snapshot:
            progress = {"completedItems": 0, "lastProgressAt": "unchanged"}
            runner.accept_event({"type": "item.completed", "item": {"type": "reasoning"}},
                                {}, Path("state"), db, progress)
            snapshot.assert_not_called()
            self.assertEqual(progress["completedItems"], 0)
            self.assertEqual(progress["lastProgressAt"], "unchanged")

    def fake_job(self, directory):
        state = Path(directory) / "run/lane"
        state.mkdir(parents=True)
        worktree = Path(directory) / "worktree"
        worktree.mkdir()
        job = {"lane": "runtime", "run_dir": str(state), "worktree": str(worktree)}
        write_json(state / "job.json", job)
        (state / "prompt.txt").write_text("test input only", encoding="utf8")
        return state

    def test_real_child_keeps_stdout_and_finishes_despite_every_progress_write_failing(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.fake_job(directory)
            script = ("import sys,json;sys.stdin.read();"
                      "print(json.dumps({'type':'thread.started','thread_id':'same-thread'}),flush=True);"
                      "[print(json.dumps({'type':'item.completed','item':{'type':'command_execution'},'i':i}),flush=True) for i in range(250)];"
                      "print(json.dumps({'type':'turn.completed'}),flush=True)")
            write = runner.write_json
            def failing_progress(path, value, **kwargs):
                if Path(path).name == "progress.json":
                    raise PermissionError(13, "persistent reader")
                write(path, value, **kwargs)
            with patch.object(runner, "build_command", return_value=[sys.executable, "-u", "-c", script]), \
                    patch.object(runner, "write_json", side_effect=failing_progress), \
                    patch.object(runner, "notify_terminal", return_value="QUEUED") as notify, \
                    patch.object(runner.subprocess, "Popen", wraps=subprocess.Popen) as popen:
                receipt = runner.run(state / "job.json")
            self.assertEqual(receipt["status"], "TURN_COMPLETED")
            self.assertEqual(receipt["exit_code"], 0)
            self.assertFalse(receipt["executor_alive"])
            self.assertFalse(receipt["merge_approved"])
            self.assertFalse(receipt["worker_receipt_exists"])
            self.assertGreater(receipt["supervisor_faults"], 0)
            lines = (state / "events.jsonl").read_text(encoding="utf8").splitlines()
            self.assertEqual(len(lines), 252)
            self.assertEqual(json.loads(lines[-1])["type"], "turn.completed")
            self.assertNotEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
            self.assertEqual(notify.call_count, 1)

    def test_nonzero_child_exit_cannot_be_approved_from_turn_completed_event(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.fake_job(directory)
            script = ("import sys,json;sys.stdin.read();"
                      "print(json.dumps({'type':'thread.started','thread_id':'same-thread'}));"
                      "print(json.dumps({'type':'turn.completed'}));sys.exit(7)")
            with patch.object(runner, "build_command", return_value=[sys.executable, "-u", "-c", script]), \
                    patch.object(runner, "notify_terminal", return_value="QUEUED"):
                receipt = runner.run(state / "job.json")
            self.assertEqual(receipt["status"], "FAILED")
            self.assertEqual(receipt["exit_code"], 7)
            self.assertFalse(receipt["merge_approved"])

    def test_fatal_supervisor_error_does_not_claim_live_child_finished(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.fake_job(directory)
            child = unittest.mock.Mock(pid=12345)
            child.poll.return_value = None
            with patch.object(runner, "build_command", return_value=["unused"]), \
                    patch.object(runner.subprocess, "Popen", return_value=child), \
                    patch.object(runner, "drain_events", side_effect=RuntimeError("broken consumer")), \
                    patch.object(runner, "notify_terminal", return_value="QUEUED"):
                receipt = runner.run(state / "job.json")
            self.assertEqual(receipt["status"], "SUPERVISOR_FAILED_EXECUTOR_RUNNING")
            self.assertTrue(receipt["executor_alive"])
            self.assertIsNone(receipt["exit_code"])
            self.assertFalse(receipt["merge_approved"])
            child.kill.assert_not_called()
            child.terminate.assert_not_called()

    def test_command_is_dispatched_once_even_if_repeat_run_is_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.fake_job(directory)
            with closing(sqlite3.connect(state.parent / "dispatch.sqlite3")) as db:
                self.assertTrue(dispatch_claim(db, "runtime"))
            with patch.object(runner.subprocess, "Popen") as popen, patch("sys.stdout", new=io.StringIO()):
                self.assertIsNone(runner.run(state / "job.json"))
                popen.assert_not_called()

    def test_two_concurrent_resume_claims_admit_exactly_one_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dispatch.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                dispatch_claim(db, "runtime")
                with db:
                    db.execute("UPDATE jobs SET status='FAILED',thread_id='same-thread'")
            barrier, results, errors = threading.Barrier(2), [], []
            def claim():
                try:
                    with closing(sqlite3.connect(path, timeout=5)) as db:
                        barrier.wait(timeout=5)
                        results.append(dispatch_claim(db, "runtime", "same-thread"))
                except Exception as exc:
                    errors.append(type(exc).__name__)
            threads = [threading.Thread(target=claim) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=6)
                self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(sorted(results), [False, True])
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM recovery_history").fetchone()[0], 1)

    def test_incomplete_utf8_line_is_held_until_real_child_finishes_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.fake_job(directory)
            script = ("import sys,time,json;sys.stdin.read();"
                      "print(json.dumps({'type':'thread.started','thread_id':'same-thread'}),flush=True);"
                      "data=json.dumps({'type':'item.completed','item':{'type':'file_change'},'text':'ação'},ensure_ascii=False).encode('utf8');"
                      "cut=data.index('ç'.encode('utf8'))+1;"
                      "sys.stdout.buffer.write(data[:cut]);sys.stdout.buffer.flush();time.sleep(0.15);"
                      "sys.stdout.buffer.write(data[cut:]+b'\\n');sys.stdout.buffer.flush();"
                      "print(json.dumps({'type':'turn.completed'}),flush=True)")
            with patch.object(runner, "build_command", return_value=[sys.executable, "-u", "-c", script]), \
                    patch.object(runner, "notify_terminal", return_value="QUEUED"):
                receipt = runner.run(state / "job.json")
            self.assertEqual(receipt["status"], "TURN_COMPLETED")
            progress = json.loads((state / "progress.json").read_text(encoding="utf8"))
            self.assertEqual(progress["completedItems"], 1)


if __name__ == "__main__":
    unittest.main()
from contextlib import closing
