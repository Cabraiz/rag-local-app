"""Single durable execution queue, distinct from the canonical card journal."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from uuid import UUID, uuid4

STATES = ("READY", "EXECUTING", "WAITING_REVIEW", "INTEGRATING", "VERIFIED", "REWORK", "BLOCKED")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Queue:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.db = sqlite3.connect(path, timeout=20, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        # In-place additive migration; preserve every lease, attempt and receipt.
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempts'").fetchone():
            with self.transaction():
                columns = {row[1] for row in self.db.execute("PRAGMA table_info(attempts)")}
                if "launcher_pid" not in columns:
                    self.db.execute("ALTER TABLE attempts ADD COLUMN launcher_pid INTEGER")
                self.install_reservation_guards()

    def close(self):
        self.db.close()

    def install_reservation_guards(self):
        # Durable barriers also constrain an already-running legacy dispatcher.
        # No daemon restart or worker interruption is needed to close this gap.
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS frozen_writer_reservation_guard
            BEFORE INSERT ON attempts WHEN NEW.status='STARTING'
            AND json_extract((SELECT spec FROM tasks WHERE id=NEW.task_id),'$.mode')='writer'
            AND EXISTS(SELECT 1 FROM tasks t JOIN attempts a ON a.id=t.run_id
                       WHERE t.lane=NEW.lane AND t.id<>NEW.task_id
                       AND t.state IN ('WAITING_REVIEW','INTEGRATING','REWORK','BLOCKED')
                       AND a.snapshot_path IS NOT NULL)
            BEGIN SELECT RAISE(ABORT,'FROZEN_HANDOFF_REVIEW_REQUIRED'); END""")
        self.db.execute("""CREATE TRIGGER IF NOT EXISTS review_capacity_reservation_guard
            BEFORE INSERT ON attempts WHEN NEW.status='STARTING'
            AND (SELECT count(*) FROM tasks t JOIN attempts a ON a.id=t.run_id
                 WHERE t.state IN ('WAITING_REVIEW','INTEGRATING','REWORK','BLOCKED')
                 AND a.snapshot_path IS NOT NULL)
                + (SELECT count(*) FROM executors WHERE run_id IS NOT NULL)
                >= (SELECT review_limit FROM config WHERE id=1)
            BEGIN SELECT RAISE(ABORT,'REVIEW_CAPACITY_REQUIRED'); END""")

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def initialize(self, owner: str, executors: list[dict], review_limit=6):
        UUID(owner)
        if not 1 <= len(executors) <= 4 or type(review_limit) is not int or review_limit < 1:
            raise ValueError("INVALID_CAPACITY")
        paths, threads, lanes = set(), set(), set()
        for executor in executors:
            canonical_thread = str(UUID(executor["thread_id"]))
            path = str(Path(executor["worktree"]).resolve()).casefold()
            if path in paths or canonical_thread in threads or executor["lane"] in lanes:
                raise ValueError("DUPLICATE_EXECUTOR_OR_WORKTREE")
            paths.add(path); threads.add(canonical_thread); lanes.add(executor["lane"])
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT,review_limit INTEGER);
            CREATE TABLE IF NOT EXISTS executors(lane TEXT PRIMARY KEY,spec TEXT NOT NULL,run_id TEXT,idle_reason TEXT);
            CREATE TABLE IF NOT EXISTS tasks(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,
                lane TEXT NOT NULL REFERENCES executors(lane),state TEXT NOT NULL,spec TEXT NOT NULL,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,reason TEXT,run_id TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS serial_integration ON tasks((1)) WHERE state='INTEGRATING';
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),
                lane TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,supervisor_pid INTEGER,launcher_pid INTEGER,
                executor_pid INTEGER,last_event_at TEXT,last_material_at TEXT,thread_verified INTEGER DEFAULT 0,
                terminal_event TEXT,exit_code INTEGER,snapshot_path TEXT,snapshot_sha TEXT,
                callback_status TEXT NOT NULL DEFAULT 'NOT_READY');
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE,
                at TEXT NOT NULL,task_id TEXT,run_id TEXT,kind TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS daemon(id INTEGER PRIMARY KEY CHECK(id=1),pid INTEGER,started_at TEXT,
                heartbeat_at TEXT,last_material_at TEXT);
        """)
        with self.transaction():
            self.install_reservation_guards()
            current = self.db.execute("SELECT * FROM config").fetchone()
            if current:
                if current["owner"] != owner or current["review_limit"] != review_limit:
                    raise ValueError("CONFIG_ALREADY_BOUND")
                saved = [json.loads(row[0]) for row in self.db.execute("SELECT spec FROM executors ORDER BY lane")]
                if sorted(saved, key=lambda x: x["lane"]) != sorted(executors, key=lambda x: x["lane"]):
                    raise ValueError("EXECUTORS_ALREADY_BOUND")
                return
            self.db.execute("INSERT INTO config VALUES(1,?,?)", (owner, review_limit))
            for executor in executors:
                self.db.execute("INSERT INTO executors VALUES(?,?,NULL,'IDLE_NO_READY_WORK')",
                                (executor["lane"], json.dumps(executor, sort_keys=True)))

    def owner(self, identity: str):
        central = self.db.execute("SELECT owner FROM config WHERE id=1").fetchone()[0]
        if identity == central:
            return
        # Explicit canonical technical transfer; the card journal keeps its owner.
        descriptor = self.path.parent / "technical-owner.json"
        if descriptor.is_file():
            binding = json.loads(descriptor.read_text(encoding="utf8"))
            expected = Path(binding.get("canonical_root", "")) / ".local/orchestration/continuous-v2/execution.sqlite3"
            if (binding.get("status") == "ACTIVE" and binding.get("technical_owner_thread") == identity
                    and binding.get("coordination_thread") == central
                    and binding.get("previous_technical_writer_revoked") is True
                    and self.path.resolve() == expected.resolve()):
                return
        raise ValueError("CENTRAL_CONTROLLER_ONLY")

    def event(self, kind: str, task=None, run=None, data=None, key=None):
        self.db.execute("INSERT INTO events(event_key,at,task_id,run_id,kind,data) VALUES(?,?,?,?,?,?)",
                        (key, now(), task, run, kind, json.dumps(data or {}, sort_keys=True)))

    def validate_spec(self, spec: dict):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", spec["id"]):
            raise ValueError("INVALID_TASK_ID")
        if spec["mode"] not in ("read_only", "writer") or not spec.get("prompt", "").strip():
            raise ValueError("INVALID_TASK_CONTRACT")
        executor = self.executor(spec["lane"])
        if not re.fullmatch(r"[0-9a-f]{40}", spec["base_sha"]):
            raise ValueError("INVALID_TASK_BASE")
        if spec["source_manifest"]["head"] != spec["base_sha"]:
            raise ValueError("TASK_SOURCE_BASE_MISMATCH")
        if spec["source_manifest"]["branch"] != executor["branch"]:
            raise ValueError("TASK_BRANCH_MISMATCH")
        scopes = spec.get("write_prefixes", [])
        if spec["mode"] == "read_only" and scopes:
            raise ValueError("READ_ONLY_HAS_WRITE_SCOPE")
        if spec["mode"] == "writer" and not scopes:
            raise ValueError("WRITER_SCOPE_REQUIRED")
        for scope in scopes:
            if (not isinstance(scope, str) or ".." in Path(scope).parts or Path(scope).is_absolute()
                    or "\\" in scope or not any(scope == p or (p.endswith("/") and scope.startswith(p))
                                                 for p in executor["allowlist"])):
                raise ValueError("TASK_OUTSIDE_EXECUTOR_ALLOWLIST")
        if spec["id"] in spec.get("dependencies", []):
            raise ValueError("CYCLIC_TASK_DEPENDENCY")
        for field in ("dependencies", "resources", "card_ids"):
            values = spec.get(field, [])
            if (not isinstance(values, list) or len(values) > 128
                    or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", x) for x in values)
                    or len(set(values)) != len(values)):
                raise ValueError("INVALID_TASK_REFERENCES")
        if len(spec["prompt"]) > 64_000:
            raise ValueError("TASK_PROMPT_OVERSIZED")

    def submit(self, identity: str, spec: dict):
        self.owner(identity)
        self.validate_spec(spec)
        with self.transaction():
            existing = self.db.execute("SELECT spec FROM tasks WHERE id=?", (spec["id"],)).fetchone()
            encoded = json.dumps(spec, sort_keys=True)
            if existing:
                if existing[0] != encoded:
                    raise ValueError("TASK_ID_ALREADY_BOUND")
                return False
            for dependency in spec.get("dependencies", []):
                if not self.db.execute("SELECT 1 FROM tasks WHERE id=?", (dependency,)).fetchone():
                    raise ValueError("MISSING_TASK_DEPENDENCY")
            self.db.execute("INSERT INTO tasks(id,lane,state,spec,created_at,updated_at) VALUES(?,?,'READY',?,?,?)",
                            (spec["id"], spec["lane"], encoded, now(), now()))
            self.event("AUTHORIZED_READY", spec["id"], data={"mode": spec["mode"], "cards": spec.get("card_ids", [])})
        return True

    def executor(self, lane: str) -> dict:
        row = self.db.execute("SELECT spec FROM executors WHERE lane=?", (lane,)).fetchone()
        if not row:
            raise ValueError("UNKNOWN_CANONICAL_EXECUTOR")
        return json.loads(row[0])

    def eligibility(self, task: dict, observed: dict) -> str | None:
        spec = json.loads(task["spec"])
        for dependency in spec.get("dependencies", []):
            row = self.db.execute("SELECT state FROM tasks WHERE id=?", (dependency,)).fetchone()
            if not row or row[0] != "VERIFIED":
                return "IDLE_DEPENDENCY_PENDING"
        pending = self.db.execute("SELECT count(*) FROM tasks WHERE state IN ('WAITING_REVIEW','INTEGRATING','REWORK','BLOCKED') AND run_id IN (SELECT id FROM attempts WHERE snapshot_path IS NOT NULL)").fetchone()[0]
        active = self.db.execute("SELECT count(*) FROM executors WHERE run_id IS NOT NULL").fetchone()[0]
        if pending + active >= self.db.execute("SELECT review_limit FROM config").fetchone()[0]:
            return "IDLE_REVIEW_CAPACITY"
        frozen = self.db.execute("SELECT spec FROM tasks WHERE lane=? AND state IN ('WAITING_REVIEW','INTEGRATING','REWORK','BLOCKED') AND run_id IN (SELECT id FROM attempts WHERE snapshot_path IS NOT NULL)",
                                 (task["lane"],)).fetchall()
        if spec["mode"] == "writer" and frozen:
            return "IDLE_FROZEN_WORKTREE"
        if not observed.get("safe", False):
            return observed.get("reason", "IDLE_WRITER_LIVENESS_UNCERTAIN")
        if observed.get("sources") != spec["source_manifest"]:
            return "IDLE_SOURCE_BINDING_CHANGED"
        resources = set(spec.get("resources", []))
        for row in self.db.execute("SELECT spec FROM tasks WHERE state='EXECUTING'"):
            if resources.intersection(json.loads(row[0]).get("resources", [])):
                return "IDLE_SHARED_RESOURCE_BUSY"
        return None

    def reserve(self, lane: str, observed: dict) -> dict | None:
        with self.transaction():
            executor = self.db.execute("SELECT run_id FROM executors WHERE lane=?", (lane,)).fetchone()
            if executor is None:
                raise ValueError("UNKNOWN_CANONICAL_EXECUTOR")
            if executor[0]:
                self.db.execute("UPDATE executors SET idle_reason='EXECUTOR_BOUND' WHERE lane=?", (lane,))
                return None
            ready = [dict(row) for row in self.db.execute("SELECT * FROM tasks WHERE lane=? AND state='READY' ORDER BY seq", (lane,))]
            reason = "IDLE_NO_READY_WORK"
            for task in ready:
                rejected = self.eligibility(task, observed)
                if rejected:
                    reason = rejected
                    continue
                run = uuid4().hex
                self.db.execute("INSERT INTO attempts(id,task_id,lane,status,created_at) VALUES(?,?,?,'STARTING',?)",
                                (run, task["id"], lane, now()))
                self.db.execute("UPDATE tasks SET state='EXECUTING',updated_at=?,reason=NULL,run_id=? WHERE id=?",
                                (now(), run, task["id"]))
                self.db.execute("UPDATE executors SET run_id=?,idle_reason=NULL WHERE lane=?", (run, lane))
                self.event("RESERVED_ONCE", task["id"], run, {"lease": run})
                return {"run_id": run, "task": json.loads(task["spec"]), "executor": self.executor(lane)}
            self.db.execute("UPDATE executors SET idle_reason=? WHERE lane=?", (reason, lane))
        return None

    def attempt(self, run: str) -> dict:
        row = self.db.execute("SELECT * FROM attempts WHERE id=?", (run,)).fetchone()
        if not row:
            raise ValueError("UNKNOWN_ATTEMPT")
        return dict(row)

    def bind_pid(self, run: str, pid: int, *, child=False, launcher=False):
        if type(pid) is not int or pid <= 0:
            raise ValueError("INVALID_PID")
        if child and launcher:
            raise ValueError("AMBIGUOUS_PID_ROLE")
        field = "executor_pid" if child else "launcher_pid" if launcher else "supervisor_pid"
        with self.transaction():
            attempt = self.attempt(run)
            if attempt["status"] not in ("STARTING", "RUNNING") and not (launcher and attempt["status"] == "TERMINAL"):
                raise ValueError("ATTEMPT_NOT_ACTIVE")
            if attempt[field] not in (None, pid):
                raise ValueError("PID_ALREADY_BOUND")
            if attempt[field] == pid:
                return
            state = "TERMINAL" if attempt["status"] == "TERMINAL" else "RUNNING"
            self.db.execute(f"UPDATE attempts SET {field}=?,status=? WHERE id=?", (pid, state, run))
            self.event("EXECUTOR_STARTED" if child else "LAUNCHER_STARTED" if launcher else "SUPERVISOR_STARTED", attempt["task_id"], run, {"pid": pid})

    def observe(self, run: str, event: dict):
        with self.transaction():
            attempt = self.attempt(run)
            if attempt["status"] not in ("STARTING", "RUNNING"):
                return
            kind = event.get("type")
            if kind == "thread.started":
                if event.get("thread_id") != self.executor(attempt["lane"])["thread_id"]:
                    raise ValueError("RESUMED_THREAD_ID_CHANGED")
                self.db.execute("UPDATE attempts SET thread_verified=1 WHERE id=?", (run,))
            material = kind == "item.completed" and event.get("item", {}).get("type") in (
                "command_execution", "file_change", "mcp_tool_call", "web_search")
            if material:
                at = now()
                self.db.execute("UPDATE attempts SET last_event_at=?,last_material_at=? WHERE id=?", (at, at, run))
                self.event("MATERIAL_EVENT_COMPLETED", attempt["task_id"], run)
            if kind in ("turn.completed", "turn.failed"):
                self.db.execute("UPDATE attempts SET terminal_event=?,last_event_at=? WHERE id=?", (kind, now(), run))

    def terminal(self, run: str, *, snapshot_path: str, snapshot_sha: str, exit_code: int,
                 success: bool, writer_dead: bool):
        with self.transaction():
            attempt = self.attempt(run)
            if attempt["status"] == "TERMINAL":
                if (attempt["snapshot_path"], attempt["snapshot_sha"]) != (snapshot_path, snapshot_sha):
                    raise ValueError("DUPLICATE_TERMINAL_CHANGED")
                return False
            if not writer_dead:
                raise ValueError("WRITER_STILL_ALIVE")
            if attempt["status"] not in ("STARTING", "RUNNING"):
                raise ValueError("UNCERTAIN_ATTEMPT_NEEDS_CENTRAL")
            task = self.db.execute("SELECT run_id,state FROM tasks WHERE id=?", (attempt["task_id"],)).fetchone()
            lease = self.db.execute("SELECT run_id FROM executors WHERE lane=?", (attempt["lane"],)).fetchone()
            if task["run_id"] != run or task["state"] != "EXECUTING" or lease[0] != run:
                raise ValueError("TERMINAL_ATTEMPT_BINDING_CHANGED")
            complete = success and exit_code == 0 and attempt["thread_verified"] and attempt["terminal_event"] == "turn.completed"
            state = "WAITING_REVIEW" if complete else "BLOCKED"
            self.db.execute("UPDATE attempts SET status='TERMINAL',exit_code=?,snapshot_path=?,snapshot_sha=?,callback_status='PENDING' WHERE id=?",
                            (exit_code, snapshot_path, snapshot_sha, run))
            self.db.execute("UPDATE tasks SET state=?,reason=?,updated_at=? WHERE id=?",
                            (state, None if complete else "EXECUTION_OR_RECEIPT_FAILED", now(), attempt["task_id"]))
            self.db.execute("UPDATE executors SET run_id=NULL WHERE lane=? AND run_id=?", (attempt["lane"], run))
            self.event("HANDOFF_FROZEN", attempt["task_id"], run, {"state": state, "snapshot_sha": snapshot_sha}, key="terminal:" + run)
        return True

    def uncertain(self, run: str, reason: str):
        # Keep lease: timeout/dead supervisor is not authority to spawn again.
        with self.transaction():
            attempt = self.attempt(run)
            if attempt["status"] not in ("STARTING", "RUNNING", "UNCERTAIN"):
                return
            self.db.execute("UPDATE attempts SET status='UNCERTAIN' WHERE id=?", (run,))
            changed = self.db.execute("UPDATE tasks SET state='BLOCKED',reason=?,updated_at=? WHERE id=? AND run_id=? AND state='EXECUTING'",
                                      (reason, now(), attempt["task_id"], run)).rowcount
            if not changed:
                self.event("STALE_ATTEMPT_QUARANTINED", attempt["task_id"], run, {"reason": reason})
            self.event("NO_BLIND_REDISPATCH", attempt["task_id"], run, {"reason": reason})

    def review(self, identity: str, task_id: str, action: str, proof: str):
        self.owner(identity)
        if not proof.strip():
            raise ValueError("REVIEW_PROOF_REQUIRED")
        with self.transaction():
            task = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("UNKNOWN_TASK")
            if action == "integrating" and task["state"] == "WAITING_REVIEW":
                state = "INTEGRATING"
            elif action == "verified" and task["state"] == "INTEGRATING":
                state = "VERIFIED"
            elif action == "rework" and task["state"] in ("WAITING_REVIEW", "INTEGRATING", "BLOCKED"):
                if self.db.execute("SELECT run_id FROM executors WHERE lane=?", (task["lane"],)).fetchone()[0]:
                    raise ValueError("REWORK_WRITER_OR_UNCERTAIN_LEASE_ACTIVE")
                state = "REWORK"
            else:
                raise ValueError("INVALID_REVIEW_TRANSITION")
            self.db.execute("UPDATE tasks SET state=?,reason=?,updated_at=? WHERE id=?", (state, proof, now(), task_id))
            self.event("CENTRAL_" + state, task_id, task["run_id"], {"proof": proof})

    def release_rework(self, identity: str, task_id: str, sources: dict, prompt: str):
        self.owner(identity)
        with self.transaction():
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row or row["state"] != "REWORK" or not prompt.strip():
                raise ValueError("REWORK_NOT_RELEASED")
            spec = json.loads(row["spec"])
            if sources["branch"] != self.executor(row["lane"])["branch"]:
                raise ValueError("REWORK_BRANCH_CHANGED")
            spec.update(prompt=prompt, source_manifest=sources, base_sha=sources["head"])
            self.db.execute("UPDATE tasks SET spec=?,state='READY',reason=NULL,updated_at=? WHERE id=?",
                            (json.dumps(spec, sort_keys=True), now(), task_id))
            self.event("REWORK_RELEASED_CANONICAL_EXECUTOR", task_id, data={"lane": row["lane"]})

    def callback_claim(self, run: str) -> bool:
        with self.transaction():
            changed = self.db.execute("UPDATE attempts SET callback_status='INTENT_UNCERTAIN_UNTIL_CONFIRMED' WHERE id=? AND callback_status='PENDING'", (run,)).rowcount
        return bool(changed)

    def reconcile_adoption(self, identity: str, task_id: str, adopted_run: str, proof: str,
                           alive, *, no_other_writer: bool):
        """Restore only a proved imported handoff after the legacy READY race."""
        self.owner(identity)
        if not proof.strip() or not no_other_writer:
            raise ValueError("ADOPTION_RECONCILIATION_PROOF_REQUIRED")
        with self.transaction():
            task = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or task["state"] != "BLOCKED" or task["run_id"] != adopted_run:
                raise ValueError("ADOPTION_RECONCILIATION_BINDING_CHANGED")
            original = self.attempt(adopted_run)
            if (original["task_id"] != task_id or original["status"] != "TERMINAL"
                    or original["callback_status"] != "PREVIOUS_CALLBACK_PRESERVED"
                    or not self.db.execute("SELECT 1 FROM events WHERE run_id=? AND kind='PREVIOUS_DELIVERY_ADOPTED_NOT_APPROVED'", (adopted_run,)).fetchone()):
                raise ValueError("ADOPTION_ORIGINAL_HANDOFF_NOT_PROVED")
            if self.db.execute("SELECT run_id FROM executors WHERE lane=?", (task["lane"],)).fetchone()[0]:
                raise ValueError("ADOPTION_EXECUTOR_LEASE_ACTIVE")
            attempts = list(self.db.execute("SELECT * FROM attempts WHERE task_id=?", (task_id,)))
            for attempt in attempts:
                if attempt["status"] != "TERMINAL":
                    raise ValueError("ADOPTION_ATTEMPT_NOT_TERMINAL")
                if any(attempt[field] and alive(attempt[field]) for field in ("launcher_pid", "supervisor_pid", "executor_pid")):
                    raise ValueError("ADOPTION_PROCESS_STILL_ALIVE")
            self.db.execute("UPDATE tasks SET state='WAITING_REVIEW',reason=?,updated_at=? WHERE id=?",
                            (proof, now(), task_id))
            self.event("ADOPTION_RACE_RECONCILED_NOT_APPROVED", task_id, adopted_run,
                       {"proof": proof, "preserved_attempts": [a["id"] for a in attempts]})

    def recover_uncertain(self, identity: str, run: str, proof: str, alive, *, no_other_writer: bool):
        """Explicit Central decision; never a timer or automatic lease expiry."""
        self.owner(identity)
        if not proof.strip() or not no_other_writer:
            raise ValueError("UNCERTAIN_RECOVERY_PROOF_REQUIRED")
        with self.transaction():
            attempt = self.attempt(run)
            if attempt["status"] != "UNCERTAIN":
                raise ValueError("ATTEMPT_NOT_UNCERTAIN")
            task = self.db.execute("SELECT run_id,state FROM tasks WHERE id=?", (attempt["task_id"],)).fetchone()
            lease = self.db.execute("SELECT run_id FROM executors WHERE lane=?", (attempt["lane"],)).fetchone()
            if task["run_id"] != run or task["state"] != "BLOCKED" or lease[0] != run:
                raise ValueError("UNCERTAIN_RECOVERY_BINDING_CHANGED")
            for field in ("launcher_pid", "supervisor_pid", "executor_pid"):
                if attempt[field] and alive(attempt[field]):
                    raise ValueError("PREVIOUS_PROCESS_STILL_ALIVE")
            self.db.execute("UPDATE attempts SET status='RESOLVED_BY_CENTRAL' WHERE id=?", (run,))
            self.db.execute("UPDATE tasks SET state='REWORK',reason=?,updated_at=? WHERE id=?", (proof, now(), attempt["task_id"]))
            self.db.execute("UPDATE executors SET run_id=NULL WHERE lane=? AND run_id=?", (attempt["lane"], run))
            self.event("CENTRAL_UNCERTAIN_RECOVERY_NO_REPLAY", attempt["task_id"], run, {"proof": proof})

    def callback_result(self, run: str, status: str):
        if status not in ("QUEUED", "FAILED_DELIVERY", "UNCERTAIN_DO_NOT_RETRY"):
            raise ValueError("INVALID_CALLBACK_STATUS")
        with self.transaction():
            self.db.execute("UPDATE attempts SET callback_status=? WHERE id=? AND callback_status='INTENT_UNCERTAIN_UNTIL_CONFIRMED'", (status, run))

    def claim_daemon(self, pid: int, alive):
        with self.transaction():
            row = self.db.execute("SELECT pid FROM daemon WHERE id=1").fetchone()
            if row and row[0] != pid and alive(row[0]):
                raise ValueError("DISPATCHER_ALREADY_ALIVE")
            self.db.execute("INSERT OR REPLACE INTO daemon VALUES(1,?,?,?,?)", (pid, now(), now(), now()))

    def status(self) -> dict:
        executors = []
        for row in self.db.execute("SELECT * FROM executors ORDER BY lane"):
            item = dict(row); spec = json.loads(item.pop("spec"))
            item.update(thread_id=spec["thread_id"], worktree=spec["worktree"])
            item["current"] = self.attempt(item["run_id"]) if item["run_id"] else None
            next_task = self.db.execute("SELECT id FROM tasks WHERE lane=? AND state='READY' ORDER BY seq LIMIT 1", (item["lane"],)).fetchone()
            item["next"] = next_task[0] if next_task else None
            executors.append(item)
        return {"executors": executors,
                "tasks": [dict(row) for row in self.db.execute("SELECT id,lane,state,reason,run_id,created_at,updated_at FROM tasks ORDER BY seq")],
                "review_limit": self.db.execute("SELECT review_limit FROM config").fetchone()[0],
                "detectorModelCalls": 0, "detectorTokenCost": 0}
