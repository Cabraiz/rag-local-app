"""Adversarial deterministic checks; isolated queues, no Codex/provider calls."""
from __future__ import annotations

import json
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from continuous import control, evidence, runtime
from continuous.store import Queue

OWNER = "01a0eed2-991f-7220-9c09-f2d131682f39"
HEAD = "a" * 40


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.database = self.directory / ".local/orchestration/execution.sqlite3"
        self.database.parent.mkdir(parents=True)
        self.queue = Queue(self.database)
        self.executors = [{"lane": f"lane{i}", "thread_id": str(uuid4()),
                           "worktree": str(self.directory / f"worktree{i}"), "branch": f"codex/lane{i}",
                           "allowlist": [f"lane{i}/"], "previous_pids": [12301 + i, 23401 + i],
                           "codex": "codex", "canonical_root": str(self.directory), "parent_thread": OWNER,
                           "model": "gpt-6.1-sol", "reasoning_effort": "max", "service_tier": "fast"}
                          for i in range(4)]
        self.queue.initialize(OWNER, self.executors)

    def tearDown(self):
        self.queue.close()
        self.tmp.cleanup()

    def source(self, lane):
        return {"head": HEAD, "branch": f"codex/{lane}", "files": {f"{lane}/module.py": "b" * 64}}

    def submit(self, name="B", lane="lane0", **extras):
        spec = {"id": name, "lane": lane, "mode": "read_only", "base_sha": HEAD,
                "source_manifest": self.source(lane), "write_prefixes": [],
                "dependencies": [], "resources": [], "prompt": "Real authorized scoped verification.", **extras}
        self.queue.submit(OWNER, spec)
        return spec

    def reserve(self, lane="lane0", **extras):
        return self.queue.reserve(lane, {"safe": True, "sources": self.source(lane), **extras})

    def complete(self, reservation, **extras):
        run = reservation["run_id"]
        self.queue.bind_pid(run, 4321)
        self.queue.bind_pid(run, 5432, child=True)
        self.queue.observe(run, {"type": "thread.started", "thread_id": reservation["executor"]["thread_id"]})
        self.queue.observe(run, {"type": "turn.completed"})
        return self.queue.terminal(run, snapshot_path="private/frozen/" + run, snapshot_sha="d" * 64,
                                   exit_code=0, success=True, writer_dead=True, **extras)

    def state(self, task):
        return self.queue.db.execute("SELECT state FROM tasks WHERE id=?", (task,)).fetchone()[0]

    def test_three_ready_not_four_artificial(self):
        for i in range(3):
            self.submit(f"T{i}", f"lane{i}")
        self.assertEqual(sum(bool(self.reserve(f"lane{i}")) for i in range(4)), 3)
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 3)

    def test_duplicate_task_and_dispatch_cas(self):
        spec = self.submit()
        self.assertFalse(self.queue.submit(OWNER, spec))
        self.assertIsNotNone(self.reserve())
        competitor = Queue(self.queue.path)
        try:
            self.assertIsNone(competitor.reserve("lane0", {"safe": True, "sources": self.source("lane0")}))
        finally:
            competitor.close()
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)

    def test_same_id_changed_contract_rejected(self):
        spec = self.submit()
        with self.assertRaisesRegex(ValueError, "ALREADY_BOUND"):
            self.queue.submit(OWNER, {**spec, "prompt": "Changed"})

    def test_a_review_b_terminal_c_once_without_central_planning(self):
        self.submit("A", "lane1")
        self.complete(self.reserve("lane1"))
        self.queue.review(OWNER, "A", "integrating", "Current frozen review")
        self.submit("B")
        self.submit("C")
        b = self.reserve()
        self.complete(b)
        c = self.reserve()
        self.assertEqual(c["task"]["id"], "C")
        self.assertEqual(self.state("A"), "INTEGRATING")
        self.assertEqual(self.state("B"), "WAITING_REVIEW")
        self.assertIsNone(self.reserve())

    def test_duplicate_terminal_deduplicated(self):
        self.submit(); b = self.reserve(); self.complete(b)
        self.assertFalse(self.queue.terminal(b["run_id"], snapshot_path="private/frozen/" + b["run_id"],
                         snapshot_sha="d" * 64, exit_code=0, success=True, writer_dead=True))
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM events WHERE kind='HANDOFF_FROZEN'").fetchone()[0], 1)

    def test_changed_duplicate_terminal_rejected(self):
        self.submit(); b = self.reserve(); self.complete(b)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_TERMINAL_CHANGED"):
            self.queue.terminal(b["run_id"], snapshot_path="other", snapshot_sha="e" * 64,
                                exit_code=0, success=True, writer_dead=True)

    def test_callback_uncertain_not_retried_or_redispatched(self):
        self.submit(); b = self.reserve(); self.complete(b)
        self.assertTrue(self.queue.callback_claim(b["run_id"]))
        self.queue.callback_result(b["run_id"], "UNCERTAIN_DO_NOT_RETRY")
        self.assertFalse(self.queue.callback_claim(b["run_id"]))
        self.assertIsNone(self.reserve())

    def test_callback_crash_after_intent_not_retried(self):
        self.submit(); b = self.reserve(); self.complete(b)
        self.assertTrue(self.queue.callback_claim(b["run_id"]))
        self.assertFalse(self.queue.callback_claim(b["run_id"]))

    def test_spawn_uncertain_preserves_lease(self):
        self.submit(); b = self.reserve()
        self.queue.uncertain(b["run_id"], "SPAWN_OUTCOME_UNCERTAIN")
        self.submit("C")
        self.assertIsNone(self.reserve())
        self.assertEqual(self.state("B"), "BLOCKED")
        with self.assertRaisesRegex(ValueError, "LEASE_ACTIVE"):
            self.queue.review(OWNER, "B", "rework", "Cannot infer no writer")

    def test_supervisor_dead_executor_alive_no_second_writer(self):
        self.submit(); b = self.reserve()
        self.queue.bind_pid(b["run_id"], 123)
        self.queue.bind_pid(b["run_id"], 456, child=True)
        with patch.object(runtime, "process_alive", side_effect=lambda pid: pid == 456):
            runtime.reconcile(self.queue)
        self.assertEqual(self.queue.attempt(b["run_id"])["status"], "UNCERTAIN")
        self.submit("C"); self.assertIsNone(self.reserve())

    def test_dead_pid_without_terminal_not_success(self):
        self.submit(); b = self.reserve()
        self.queue.db.execute("UPDATE attempts SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?", (b["run_id"],))
        with patch.object(runtime, "process_alive", return_value=False):
            runtime.reconcile(self.queue)
        self.assertEqual(self.state("B"), "BLOCKED")

    def test_no_ready_work_idle_reason(self):
        self.assertIsNone(self.reserve())
        self.assertEqual(self.queue.status()["executors"][0]["idle_reason"], "IDLE_NO_READY_WORK")

    def test_dependency_pending_then_verified_releases(self):
        self.submit("A", "lane1"); self.submit("C", dependencies=["A"])
        self.assertIsNone(self.reserve())
        self.complete(self.reserve("lane1"))
        self.assertIsNone(self.reserve())
        self.queue.review(OWNER, "A", "integrating", "Frozen audit")
        self.queue.review(OWNER, "A", "verified", "Two current rounds")
        self.assertIsNotNone(self.reserve())

    def test_review_limit_reserved_slots_and_release(self):
        self.queue.db.execute("UPDATE config SET review_limit=1")
        self.submit("B"); self.submit("C", "lane1")
        b = self.reserve(); self.assertIsNone(self.reserve("lane1"))
        self.complete(b); self.assertIsNone(self.reserve("lane1"))
        self.queue.review(OWNER, "B", "integrating", "Proof")
        self.queue.review(OWNER, "B", "verified", "Two rounds")
        self.assertIsNotNone(self.reserve("lane1"))

    def test_writer_frozen_until_verified(self):
        self.submit("B", mode="writer", write_prefixes=["lane0/"])
        self.complete(self.reserve())
        self.submit("C", mode="writer", write_prefixes=["lane0/"])
        self.assertIsNone(self.reserve())
        self.assertEqual(self.queue.status()["executors"][0]["idle_reason"], "IDLE_FROZEN_WORKTREE")

    def test_rework_same_identity_new_attempt_old_preserved(self):
        self.submit(); old = self.reserve(); self.complete(old)
        self.queue.review(OWNER, "B", "rework", "Finding requires repair")
        self.assertIsNone(self.reserve())
        self.queue.release_rework(OWNER, "B", self.source("lane0"), "Repair exact finding")
        new = self.reserve()
        self.assertNotEqual(old["run_id"], new["run_id"])
        self.assertEqual(old["executor"]["thread_id"], new["executor"]["thread_id"])
        self.assertEqual(self.queue.attempt(old["run_id"])["snapshot_sha"], "d" * 64)

    def test_only_one_integrating(self):
        self.submit("A"); self.submit("B", "lane1")
        self.complete(self.reserve()); self.complete(self.reserve("lane1"))
        self.queue.review(OWNER, "A", "integrating", "Proof")
        with self.assertRaises(sqlite3.IntegrityError):
            self.queue.review(OWNER, "B", "integrating", "Proof")
        self.assertEqual(self.state("B"), "WAITING_REVIEW")

    def test_only_central_mutates_contract_or_review(self):
        spec = self.submit()
        with self.assertRaisesRegex(ValueError, "CENTRAL_CONTROLLER_ONLY"):
            self.queue.submit(str(uuid4()), {**spec, "id": "C"})

    def test_writer_alive_rejects_terminal(self):
        self.submit(); b = self.reserve()
        with self.assertRaisesRegex(ValueError, "WRITER_STILL_ALIVE"):
            self.queue.terminal(b["run_id"], snapshot_path="p", snapshot_sha="s", exit_code=0,
                                success=True, writer_dead=False)

    def test_missing_thread_or_terminal_event_blocks(self):
        self.submit(); b = self.reserve()
        self.queue.terminal(b["run_id"], snapshot_path="p", snapshot_sha="s", exit_code=0,
                            success=True, writer_dead=True)
        self.assertEqual(self.state("B"), "BLOCKED")

    def test_wrong_thread_rejected(self):
        self.submit(); b = self.reserve()
        with self.assertRaisesRegex(ValueError, "THREAD_ID_CHANGED"):
            self.queue.observe(b["run_id"], {"type": "thread.started", "thread_id": str(uuid4())})

    def test_no_progress_from_heartbeat_or_reasoning(self):
        self.submit(); b = self.reserve()
        for event in ({"type": "heartbeat"}, {"type": "item.completed", "item": {"type": "reasoning"}}):
            self.queue.observe(b["run_id"], event)
        self.assertIsNone(self.queue.attempt(b["run_id"])["last_material_at"])
        self.queue.observe(b["run_id"], {"type": "item.completed", "item": {"type": "command_execution"}})
        self.assertIsNotNone(self.queue.attempt(b["run_id"])["last_material_at"])

    def test_pid_binding_idempotent_and_not_replaceable(self):
        self.submit(); b = self.reserve()
        self.queue.bind_pid(b["run_id"], 123)
        self.queue.bind_pid(b["run_id"], 123)
        with self.assertRaisesRegex(ValueError, "PID_ALREADY_BOUND"):
            self.queue.bind_pid(b["run_id"], 456)
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM events WHERE kind='SUPERVISOR_STARTED'").fetchone()[0], 1)

    def test_windows_redirector_pid_distinct_from_actual_supervisor(self):
        self.submit(); b = self.reserve()
        self.queue.bind_pid(b["run_id"], 123, launcher=True)
        self.queue.bind_pid(b["run_id"], 456)
        self.assertEqual(self.queue.attempt(b["run_id"])["launcher_pid"], 123)
        self.assertEqual(self.queue.attempt(b["run_id"])["supervisor_pid"], 456)
        with patch.object(runtime, "process_alive", side_effect=lambda pid: pid == 456):
            runtime.reconcile(self.queue)
        self.assertEqual(self.queue.attempt(b["run_id"])["status"], "RUNNING")

    def test_central_uncertain_recovery_dead_process_proof_required(self):
        self.submit(); b = self.reserve()
        self.queue.bind_pid(b["run_id"], 123, launcher=True)
        self.queue.uncertain(b["run_id"], "uncertain")
        with self.assertRaisesRegex(ValueError, "PROOF_REQUIRED"):
            self.queue.recover_uncertain(OWNER, b["run_id"], "proof", lambda _: False, no_other_writer=False)
        with self.assertRaisesRegex(ValueError, "STILL_ALIVE"):
            self.queue.recover_uncertain(OWNER, b["run_id"], "proof", lambda _: True, no_other_writer=True)
        self.queue.recover_uncertain(OWNER, b["run_id"], "Exact dead process inventory", lambda _: False, no_other_writer=True)
        self.assertEqual(self.state("B"), "REWORK")
        self.assertEqual(self.queue.attempt(b["run_id"])["status"], "RESOLVED_BY_CENTRAL")
        self.assertIsNone(self.reserve())

    def test_alive_redirector_during_startup_not_misread_as_dead_supervisor(self):
        self.submit(); b = self.reserve()
        self.queue.bind_pid(b["run_id"], 123, launcher=True)
        self.queue.db.execute("UPDATE attempts SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?", (b["run_id"],))
        with patch.object(runtime, "process_alive", return_value=True):
            runtime.reconcile(self.queue)
        self.assertEqual(self.queue.attempt(b["run_id"])["status"], "RUNNING")

    def test_source_binding_changed_prevents_dispatch(self):
        self.submit()
        self.assertIsNone(self.reserve(sources={**self.source("lane0"), "head": "b" * 40}))
        self.assertEqual(self.queue.status()["executors"][0]["idle_reason"], "IDLE_SOURCE_BINDING_CHANGED")

    def test_alive_previous_pid_blocks_even_if_lease_expired(self):
        self.submit()
        self.assertIsNone(self.reserve(safe=False, reason="IDLE_PREVIOUS_EXECUTOR_OR_SUPERVISOR_ALIVE"))

    def test_shared_resource_not_independent(self):
        self.submit("A", resources=["exclusive-qa"])
        self.submit("B", "lane1", resources=["exclusive-qa"])
        self.reserve()
        self.assertIsNone(self.reserve("lane1"))

    def test_restart_retains_active_lease(self):
        self.submit(); old = self.reserve()
        self.queue.close(); self.queue = Queue(self.database)
        self.assertIsNone(self.reserve())
        self.assertEqual(self.queue.status()["executors"][0]["run_id"], old["run_id"])

    def test_daemon_singleton_and_proven_dead_recovery(self):
        self.queue.claim_daemon(111, lambda _: False)
        with self.assertRaisesRegex(ValueError, "DISPATCHER_ALREADY_ALIVE"):
            self.queue.claim_daemon(222, lambda _: True)
        self.queue.claim_daemon(222, lambda _: False)

    def test_tasks_reject_invalid_scope_and_unknown_dependency(self):
        for scope in ("../secret", "other/", "D:/secret", "lane0/../secret"):
            with self.assertRaises(ValueError):
                self.submit(mode="writer", write_prefixes=[scope])
        with self.assertRaisesRegex(ValueError, "MISSING_TASK_DEPENDENCY"):
            self.submit(dependencies=["missing"])

    def test_distinct_worktree_and_thread_bindings(self):
        candidate = Queue(self.directory / "bad.sqlite3")
        try:
            with self.assertRaisesRegex(ValueError, "DUPLICATE_EXECUTOR"):
                candidate.initialize(OWNER, [self.executors[0], self.executors[0]])
        finally:
            candidate.close()

    def test_tick_failed_spawn_no_retry(self):
        self.submit()
        with patch.object(runtime, "observed", return_value={"safe": True, "sources": self.source("lane0")}):
            self.assertEqual(runtime.tick(self.queue, launch=lambda _: (_ for _ in ()).throw(OSError())), [])
            self.assertEqual(runtime.tick(self.queue, launch=lambda _: 123), [])
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)

    def test_cli_read_only_contract_preserves_settings(self):
        task = self.submit()
        command, prompt = runtime.runner_contract({"task": task, "executor": self.executors[0]}, self.directory)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--add-dir", command)
        self.assertIn(self.executors[0]["thread_id"], command)
        self.assertIn('model_reasoning_effort="max"', command)
        self.assertIn('service_tier="fast"', command)
        self.assertLess(command.index("--output-schema"), command.index("resume"))
        self.assertIn("não é aprovação de card", prompt)

    def test_cli_review_action_does_not_replace_subcommand(self):
        self.submit("A"); self.complete(self.reserve())
        arguments = ["control.py", "--database", str(self.database), "--owner", OWNER,
                     "review", "--task", "A", "--action", "integrating", "--proof", "Exact current audit"]
        fake_file = self.directory / "app/tools/orchestration/continuous/control.py"
        with patch.object(control, "__file__", str(fake_file)), patch.object(sys, "argv", arguments), \
                patch.object(control, "verify_frozen", return_value={}), patch.object(sys, "stdout", io.StringIO()):
            control.main()
        self.assertEqual(self.state("A"), "INTEGRATING")

    def test_result_schema_checks_missing_and_wrong_identity(self):
        path = self.directory / "result.json"
        with self.assertRaisesRegex(ValueError, "MISSING"):
            runtime.valid_result(path, {"id": "B"})
        path.write_text(json.dumps({"task_id": "Other", "outcome": "completed", "checks": [], "findings": [], "limitations": []}))
        with self.assertRaisesRegex(ValueError, "SCHEMA_INVALID"):
            runtime.valid_result(path, {"id": "B"})

    def adoption_fixture(self):
        previous = self.directory / "previous"; previous.mkdir()
        receipt = previous / "receipt.json"; receipt.write_text('{"approved": false}')
        terminal = previous / "terminal.json"
        terminal.write_text(json.dumps({"status": "TURN_COMPLETED", "thread_id": self.executors[0]["thread_id"],
                                        "worker_receipt": str(receipt), "worker_receipt_sha256": evidence.sha(receipt)}))
        executor = {**self.executors[0], "previous_run": str(previous)}
        self.queue.db.execute("UPDATE executors SET spec=? WHERE lane='lane0'", (json.dumps(executor),))
        return terminal

    def test_adoption_never_exposes_ready_to_concurrent_reservation(self):
        terminal = self.adoption_fixture()
        connected, freezing, requested, finished = [threading.Event() for _ in range(4)]
        results, errors = [], []
        def competitor():
            queue = Queue(self.database)
            try:
                connected.set()
                if not freezing.wait(3):
                    raise AssertionError("freeze did not begin")
                requested.set()
                results.append(queue.reserve("lane0", {"safe": True, "sources": self.source("lane0")}))
            except BaseException as exc:
                errors.append(exc)
            finally:
                queue.close(); finished.set()
        thread = threading.Thread(target=competitor); thread.start()
        self.assertTrue(connected.wait(3))
        def freezer(*args, **kwargs):
            freezing.set(); self.assertTrue(requested.wait(3))
            self.assertFalse(finished.wait(.05))
            reader = sqlite3.connect(self.database)
            try:
                self.assertEqual(reader.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
            finally:
                reader.close()
            return {"snapshot_sha256": "d" * 64}
        try:
            with patch.object(control, "process_alive", return_value=False), \
                    patch.object(control, "source_manifest", return_value=self.source("lane0")), \
                    patch.object(control, "freeze", side_effect=freezer):
                control.adopt(self.queue, OWNER, "lane0", evidence.sha(terminal))
        finally:
            freezing.set(); thread.join(5)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(results, [None]); self.assertEqual(self.state("previous-lane0"), "WAITING_REVIEW")
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM events WHERE kind='AUTHORIZED_READY'").fetchone()[0], 0)

    def test_failed_adoption_freeze_publishes_no_task_or_attempt(self):
        terminal = self.adoption_fixture()
        with patch.object(control, "process_alive", return_value=False), \
                patch.object(control, "source_manifest", return_value=self.source("lane0")), \
                patch.object(control, "freeze", side_effect=ValueError("FREEZE_FAILED")):
            with self.assertRaisesRegex(ValueError, "FREEZE_FAILED"):
                control.adopt(self.queue, OWNER, "lane0", evidence.sha(terminal))
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)

    def test_adoption_rejects_active_lane_before_freeze(self):
        terminal = self.adoption_fixture(); self.submit(); self.reserve()
        with patch.object(control, "process_alive", return_value=False), \
                patch.object(control, "source_manifest", return_value=self.source("lane0")), \
                patch.object(control, "freeze") as freezer:
            with self.assertRaisesRegex(ValueError, "LEASE_ACTIVE"):
                control.adopt(self.queue, OWNER, "lane0", evidence.sha(terminal))
        freezer.assert_not_called()

    def test_stale_terminal_cannot_mutate_rebound_task_or_release_lease(self):
        self.submit(); reservation = self.reserve(); run = reservation["run_id"]
        self.queue.db.execute("UPDATE tasks SET run_id='different',state='WAITING_REVIEW' WHERE id='B'")
        with self.assertRaisesRegex(ValueError, "TERMINAL_ATTEMPT_BINDING_CHANGED"):
            self.queue.terminal(run, snapshot_path="p", snapshot_sha="d"*64,
                                exit_code=0, success=False, writer_dead=True)
        self.assertEqual(self.state("B"), "WAITING_REVIEW")
        self.assertEqual(self.queue.attempt(run)["status"], "STARTING")
        self.assertEqual(self.queue.db.execute("SELECT run_id FROM executors WHERE lane='lane0'").fetchone()[0], run)

    def test_adoption_duplicate_verifies_existing_handoff_without_refreeze(self):
        terminal = self.adoption_fixture()
        with patch.object(control, "process_alive", return_value=False), \
                patch.object(control, "source_manifest", return_value=self.source("lane0")), \
                patch.object(control, "freeze", return_value={"snapshot_sha256": "d"*64}) as freezer:
            control.adopt(self.queue, OWNER, "lane0", evidence.sha(terminal))
            frozen = {"binding": {"previous_terminal_sha256": evidence.sha(terminal)},
                      "sources": self.source("lane0"), "receipt_sha256": evidence.sha(terminal.parent/'receipt.json')}
            with patch.object(control, "verify_frozen", return_value=frozen):
                control.adopt(self.queue, OWNER, "lane0", evidence.sha(terminal))
        self.assertEqual(freezer.call_count, 1)
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)

    def test_stale_worker_fault_cannot_block_another_delivery(self):
        self.submit(); reservation = self.reserve(); run = reservation["run_id"]
        self.queue.db.execute("UPDATE tasks SET run_id='different',state='WAITING_REVIEW' WHERE id='B'")
        self.queue.uncertain(run, "HANDOFF_FREEZE_OR_TERMINAL_FAILED")
        self.assertEqual(self.state("B"), "WAITING_REVIEW")
        self.assertEqual(self.queue.attempt(run)["status"], "UNCERTAIN")
        self.assertEqual(self.queue.db.execute("SELECT run_id FROM executors WHERE lane='lane0'").fetchone()[0], run)

    def reconciliation_fixture(self):
        self.submit("previous-lane0"); reservation = self.reserve(); self.complete(reservation)
        run = reservation["run_id"]
        self.queue.db.execute("UPDATE attempts SET callback_status='PREVIOUS_CALLBACK_PRESERVED' WHERE id=?", (run,))
        self.queue.db.execute("UPDATE tasks SET state='BLOCKED' WHERE id='previous-lane0'")
        self.queue.event("PREVIOUS_DELIVERY_ADOPTED_NOT_APPROVED", "previous-lane0", run)
        return run

    def test_reconciliation_restores_original_without_approval_or_new_attempt(self):
        run = self.reconciliation_fixture()
        self.queue.reconcile_adoption(OWNER, "previous-lane0", run, "Current inventory and hashes", lambda _: False, no_other_writer=True)
        self.assertEqual(self.state("previous-lane0"), "WAITING_REVIEW")
        self.assertEqual(self.queue.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1)
        self.assertIsNone(self.reserve())

    def test_reconciliation_rejects_live_process_or_unverified_inventory(self):
        run = self.reconciliation_fixture()
        with self.assertRaisesRegex(ValueError, "STILL_ALIVE"):
            self.queue.reconcile_adoption(OWNER, "previous-lane0", run, "Proof", lambda _: True, no_other_writer=True)
        with self.assertRaisesRegex(ValueError, "PROOF_REQUIRED"):
            self.queue.reconcile_adoption(OWNER, "previous-lane0", run, "Proof", lambda _: False, no_other_writer=False)
        self.assertEqual(self.state("previous-lane0"), "BLOCKED")

    def test_technical_transfer_requires_active_matching_canonical_binding(self):
        technical = str(uuid4())
        with self.assertRaisesRegex(ValueError, "CONTROLLER_ONLY"):
            self.queue.owner(technical)
        root = self.directory / "canonical"
        directory = root / ".local/orchestration/continuous-v2"; directory.mkdir(parents=True)
        queue = Queue(directory / "execution.sqlite3"); queue.initialize(OWNER, self.executors)
        descriptor = {"status": "TRANSFER_PENDING", "technical_owner_thread": technical,
                      "canonical_root": str(root), "coordination_thread": OWNER,
                      "previous_technical_writer_revoked": True}
        try:
            path = directory / "technical-owner.json"; path.write_text(json.dumps(descriptor))
            with self.assertRaisesRegex(ValueError, "CONTROLLER_ONLY"):
                queue.owner(technical)
            descriptor["status"] = "ACTIVE"; path.write_text(json.dumps(descriptor))
            queue.owner(technical)
            descriptor["coordination_thread"] = str(uuid4()); path.write_text(json.dumps(descriptor))
            with self.assertRaisesRegex(ValueError, "CONTROLLER_ONLY"):
                queue.owner(technical)
        finally:
            queue.close()

    def test_blocked_writer_freeze_blocks_preexisting_writer_successor(self):
        self.submit("B", mode="writer", write_prefixes=["lane0/"])
        self.submit("C", mode="writer", write_prefixes=["lane0/"])
        run = self.reserve()["run_id"]
        self.queue.terminal(run, snapshot_path="private/frozen/B", snapshot_sha="d"*64,
                            exit_code=0, success=False, writer_dead=True)
        self.assertEqual(self.state("B"), "BLOCKED")
        self.assertIsNone(self.reserve())
        self.assertEqual(self.queue.db.execute("SELECT idle_reason FROM executors WHERE lane='lane0'").fetchone()[0], "IDLE_FROZEN_WORKTREE")

    def test_blocked_delivery_still_counts_against_review_capacity(self):
        self.queue.db.execute("UPDATE config SET review_limit=1")
        self.submit("B"); run = self.reserve()["run_id"]
        self.queue.terminal(run, snapshot_path="p", snapshot_sha="d"*64, exit_code=0, success=False, writer_dead=True)
        self.submit("C", "lane1")
        self.assertIsNone(self.reserve("lane1"))

    def test_live_sql_barrier_blocks_legacy_writer_reservation(self):
        self.submit("B", mode="writer", write_prefixes=["lane0/"])
        self.submit("C", mode="writer", write_prefixes=["lane0/"])
        run = self.reserve()["run_id"]
        self.queue.terminal(run,snapshot_path="p",snapshot_sha="d"*64,exit_code=0,success=False,writer_dead=True)
        with self.assertRaisesRegex(sqlite3.IntegrityError,"FROZEN_HANDOFF_REVIEW_REQUIRED"):
            self.queue.db.execute("INSERT INTO attempts(id,task_id,lane,status,created_at) VALUES('legacy','C','lane0','STARTING','now')")
        self.assertEqual(self.state("C"),"READY")
        self.assertIsNone(self.queue.db.execute("SELECT id FROM attempts WHERE id='legacy'").fetchone())

    def test_live_sql_barrier_keeps_blocked_handoff_in_capacity(self):
        self.queue.db.execute("UPDATE config SET review_limit=1")
        self.submit("B"); run=self.reserve()["run_id"]
        self.queue.terminal(run,snapshot_path="p",snapshot_sha="d"*64,exit_code=0,success=False,writer_dead=True)
        self.submit("C","lane1")
        with self.assertRaisesRegex(sqlite3.IntegrityError,"REVIEW_CAPACITY_REQUIRED"):
            self.queue.db.execute("INSERT INTO attempts(id,task_id,lane,status,created_at) VALUES('legacy','C','lane1','STARTING','now')")

    def test_explicit_rework_can_edit_same_task_after_blocked_freeze(self):
        self.submit("B", mode="writer", write_prefixes=["lane0/"])
        run = self.reserve()["run_id"]
        self.queue.terminal(run, snapshot_path="p", snapshot_sha="d"*64, exit_code=0, success=False, writer_dead=True)
        self.queue.review(OWNER, "B", "rework", "Current finding")
        self.queue.release_rework(OWNER, "B", self.source("lane0"), "Fix exact finding")
        self.assertIsNotNone(self.reserve())

    def test_duplicate_thread_uuid_different_case_is_same_executor(self):
        executors = [dict(e) for e in self.executors]
        executors[1]["thread_id"] = executors[0]["thread_id"].upper()
        other = Queue(self.directory / "other.sqlite3")
        try:
            with self.assertRaisesRegex(ValueError, "DUPLICATE_EXECUTOR_OR_WORKTREE"):
                other.initialize(OWNER, executors)
        finally:
            other.close()

    def test_result_rejects_extra_fields_and_duplicate_keys(self):
        path = self.directory / "result.json"
        valid = {"task_id": "B", "outcome": "completed", "checks": [{"name":"check","passed":True,"detail":"Proof"}], "findings":[], "limitations":[]}
        path.write_text(json.dumps(valid)); self.assertEqual(runtime.valid_result(path, {"id":"B"}), valid)
        for value in ({**valid,"extra":"not allowed"}, {**valid,"checks":[{**valid["checks"][0],"extra":True}]}):
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,"SCHEMA_INVALID"):
                runtime.valid_result(path,{"id":"B"})
        path.write_text(json.dumps(valid)[:-1]+',"task_id":"B"}')
        with self.assertRaisesRegex(ValueError,"SCHEMA_INVALID"):
            runtime.valid_result(path,{"id":"B"})

    def test_old_uncertain_recovery_cannot_release_new_attempt(self):
        self.submit(); old = self.reserve()["run_id"]
        self.queue.uncertain(old,"SPAWN_UNCERTAIN")
        # Simulate a legacy superseded pointer, retaining the old UNCERTAIN row.
        self.queue.db.execute("UPDATE tasks SET state='REWORK' WHERE id='B'")
        self.queue.db.execute("UPDATE executors SET run_id=NULL WHERE lane='lane0'")
        self.queue.release_rework(OWNER,"B",self.source("lane0"),"Authorized new attempt")
        current = self.reserve()["run_id"]
        with self.assertRaisesRegex(ValueError,"RECOVERY_BINDING_CHANGED"):
            self.queue.recover_uncertain(OWNER,old,"PIDs dead",lambda _:False,no_other_writer=True)
        self.assertEqual(self.state("B"),"EXECUTING")
        self.assertEqual(self.queue.db.execute("SELECT run_id FROM executors WHERE lane='lane0'").fetchone()[0],current)
class FrozenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.worktree = self.root / "repo"; self.worktree.mkdir()
        subprocess.run(["git", "init", "-q", str(self.worktree)], check=True, creationflags=evidence.NO_WINDOW)
        (self.worktree / "module.py").write_text("VALUE = 1\n")
        subprocess.run(["git", "-C", str(self.worktree), "add", "module.py"], check=True, creationflags=evidence.NO_WINDOW)
        subprocess.run(["git", "-C", str(self.worktree), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "Isolated fixture"], check=True, creationflags=evidence.NO_WINDOW)
        self.receipt = self.root / "receipt.json"; self.receipt.write_text('{"approved": false}')
        self.directory = self.root / "frozen"
        self.binding = {"task_id": "B", "run_id": "unique"}

    def tearDown(self):
        self.tmp.cleanup()

    def frozen(self):
        return evidence.freeze(self.directory, self.worktree, self.binding, self.receipt)

    def test_immutable_snapshot_survives_later_original_change(self):
        value = self.frozen()
        (self.worktree / "module.py").write_text("VALUE = 2\n")
        evidence.verify_frozen(self.directory, value["snapshot_sha256"])
        self.assertNotEqual(evidence.source_manifest(self.worktree), value["sources"])

    def test_unchanged_duplicate_freeze_idempotent(self):
        first = self.frozen()
        self.assertEqual(first, self.frozen())

    def test_frozen_source_tamper_detected(self):
        value = self.frozen()
        (self.directory / "code/module.py").write_text("tamper")
        with self.assertRaisesRegex(ValueError, "FROZEN_SOURCE_CHANGED"):
            evidence.verify_frozen(self.directory, value["snapshot_sha256"])

    def test_frozen_receipt_tamper_detected(self):
        value = self.frozen()
        (self.directory / "receipt.json").write_text("tamper")
        with self.assertRaisesRegex(ValueError, "FROZEN_RECEIPT_CHANGED"):
            evidence.verify_frozen(self.directory, value["snapshot_sha256"])

    def test_read_only_sources_must_match(self):
        before = evidence.source_manifest(self.worktree)
        (self.worktree / "module.py").write_text("changed")
        with self.assertRaisesRegex(ValueError, "READ_ONLY_SOURCE_CHANGED"):
            evidence.freeze(self.directory, self.worktree, self.binding, self.receipt, expected=before)

    def test_existing_snapshot_still_enforces_expected_sources(self):
        frozen = self.frozen()
        expected = {**frozen["sources"], "head": "f"*40}
        with self.assertRaisesRegex(ValueError,"READ_ONLY_SOURCE_CHANGED"):
            evidence.freeze(self.directory,self.worktree,self.binding,self.receipt,expected=expected)
        evidence.verify_frozen(self.directory,frozen["snapshot_sha256"])

    def test_nested_private_env_never_enters_source_manifest(self):
        directory = self.worktree / "config"; directory.mkdir()
        for name in (".env", ".ENV.local"):
            path = directory / name; path.write_text("SYNTHETIC=NOT_A_SECRET")
            try:
                with self.assertRaisesRegex(ValueError,"PRIVATE_PATH_IN_SOURCE_MANIFEST"):
                    evidence.source_manifest(self.worktree)
            finally:
                path.unlink()

    def test_new_frozen_file_is_not_silently_ignored(self):
        value = self.frozen()
        (self.directory / "code/extra.py").write_text("extra")
        with self.assertRaisesRegex(ValueError, "FROZEN_FILE_SET_CHANGED"):
            evidence.verify_frozen(self.directory, value["snapshot_sha256"])

    def test_private_resolved_alias_is_rejected_before_hash(self):
        root = self.worktree.resolve()
        alias = root / "alias/config"
        original_resolve = Path.resolve
        for target in (".local/config", ".git/config", "app/.ENV.local", ".local./config"):
            private_target = root / (".local/config" if target == ".local./config" else target)
            def resolve(path, *args, **kwargs):
                return private_target if path == alias else original_resolve(path, *args, **kwargs)
            with self.subTest(target=target), patch.object(evidence, "git", return_value="alias/config"), \
                    patch.object(Path, "resolve", resolve), patch.object(evidence, "sha") as hashing:
                with self.assertRaisesRegex(ValueError, "PRIVATE_PATH_IN_SOURCE_MANIFEST"):
                    evidence.source_manifest(root)
                hashing.assert_not_called()

    def test_regular_leaf_below_symlink_or_junction_is_rejected(self):
        root = self.worktree.resolve()
        parent = root / "alias"
        for kind in ("is_symlink", "is_junction"):
            with self.subTest(kind=kind), patch.object(evidence, "git", return_value="alias/config"), \
                    patch.object(Path, kind, lambda path: path == parent, create=True), \
                    patch.object(evidence, "sha") as hashing:
                with self.assertRaisesRegex(ValueError, "UNSAFE_SOURCE_PATH"):
                    evidence.source_manifest(root)
                hashing.assert_not_called()

    def test_writer_scope_change_checked(self):
        before = evidence.source_manifest(self.worktree)
        after = {**before, "files": {"module.py": "changed"}}
        runtime.check_writer_scope(before, after, {"write_prefixes": ["module.py"]})
        with self.assertRaisesRegex(ValueError, "OUTSIDE_TASK_SCOPE"):
            runtime.check_writer_scope(before, after, {"write_prefixes": ["another.py"]})


if __name__ == "__main__":
    unittest.main()
