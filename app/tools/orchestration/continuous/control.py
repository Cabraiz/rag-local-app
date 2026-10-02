"""Central-only preparation/review plus deterministic dispatcher entrypoint."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from continuous.evidence import freeze, sha, source_manifest, verify_frozen, NO_WINDOW
from continuous.runtime import process_alive, run_worker, serve, tick
from continuous.store import Queue, now


def initialize(queue: Queue, root: Path, previous: Path, limit: int):
    manifest = json.loads((root / "docs/orchestration/workstreams.json").read_text(encoding="utf8"))
    executors = []
    for lane in manifest["lanes"]:
        directory = previous / lane["id"]
        if (directory / "recovery-1/job.json").is_file():
            directory /= "recovery-1"
        job = json.loads((directory / "job.json").read_text(encoding="utf8"))
        terminal = json.loads((directory / "terminal.json").read_text(encoding="utf8"))
        progress = json.loads((directory / "progress.json").read_text(encoding="utf8"))
        pids = [progress["supervisorPid"], progress["executorPid"]]
        if any(process_alive(pid) for pid in pids) or terminal["status"] != "TURN_COMPLETED":
            raise ValueError("EXISTING_EXECUTOR_NOT_TERMINAL_AND_DEAD")
        if source_manifest(lane["worktree"])["branch"] != lane["branch"]:
            raise ValueError("EXISTING_BRANCH_CHANGED")
        executors.append({"lane": lane["id"], "branch": lane["branch"], "worktree": lane["worktree"],
                          "thread_id": terminal["thread_id"], "previous_pids": pids,
                          "allowlist": lane["allowlist"], "codex": job["codex"],
                          "model": job["model"], "reasoning_effort": job["reasoning_effort"],
                          "service_tier": job["service_tier"], "parent_thread": manifest["central_thread"],
                          "canonical_root": str(root), "previous_run": str(directory)})
    queue.initialize(manifest["central_thread"], executors, limit)


def adopt(queue: Queue, identity: str, lane: str, terminal_sha: str):
    queue.owner(identity)
    executor = queue.executor(lane)
    previous = Path(executor["previous_run"])
    terminal_path = previous / "terminal.json"
    if sha(terminal_path) != terminal_sha.lower():
        raise ValueError("ADOPTION_TERMINAL_HASH_CHANGED")
    terminal = json.loads(terminal_path.read_text(encoding="utf8"))
    receipt = Path(terminal["worker_receipt"])
    if (terminal["status"] != "TURN_COMPLETED" or terminal["thread_id"] != executor["thread_id"]
            or sha(receipt) != terminal["worker_receipt_sha256"]
            or any(process_alive(pid) for pid in executor["previous_pids"])):
        raise ValueError("ADOPTION_IDENTITY_OR_RECEIPT_INVALID")
    task_id = "previous-" + lane
    sources = source_manifest(executor["worktree"])
    spec = {"id": task_id, "lane": lane, "mode": "writer", "base_sha": sources["head"],
            "source_manifest": sources, "write_prefixes": executor["allowlist"],
            "dependencies": [], "resources": [], "prompt": "Entrega anterior; revisão da Central pendente."}
    queue.validate_spec(spec)
    # Hold the same SQLite writer lock as reserve(): no READY intermediate state,
    # and no lane dispatch can start while its previous delivery is frozen.
    with queue.transaction():
        existing = queue.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if existing:
            attempt = queue.attempt(existing["run_id"]) if existing["run_id"] else None
            if (existing["spec"] != json.dumps(spec, sort_keys=True) or not attempt
                    or existing["state"] not in ("WAITING_REVIEW", "INTEGRATING", "VERIFIED")
                    or attempt["callback_status"] != "PREVIOUS_CALLBACK_PRESERVED"):
                raise ValueError("ADOPTION_TASK_ALREADY_BOUND")
            saved = verify_frozen(Path(attempt["snapshot_path"]), attempt["snapshot_sha"])
            if (saved["binding"].get("previous_terminal_sha256") != terminal_sha.lower()
                    or saved["sources"] != sources or saved["receipt_sha256"] != sha(receipt)):
                raise ValueError("ADOPTED_HANDOFF_CHANGED")
            return
        if queue.db.execute("SELECT run_id FROM executors WHERE lane=?", (lane,)).fetchone()[0]:
            raise ValueError("ADOPTION_EXECUTOR_LEASE_ACTIVE")
        run = uuid4().hex
        directory = queue.path.parent / "frozen" / run
        snapshot = freeze(directory, Path(executor["worktree"]),
                          {"task_id": task_id, "run_id": run, "thread_id": executor["thread_id"],
                           "previous_terminal_sha256": terminal_sha.lower()}, receipt, expected=sources)
        if sha(terminal_path) != terminal_sha.lower() or sha(receipt) != terminal["worker_receipt_sha256"]:
            raise ValueError("ADOPTION_TERMINAL_OR_RECEIPT_CHANGED_DURING_FREEZE")
        if any(process_alive(pid) for pid in executor["previous_pids"]):
            raise ValueError("ADOPTION_PREVIOUS_PROCESS_ALIVE")
        queue.db.execute("INSERT INTO tasks(id,lane,state,spec,created_at,updated_at,run_id) VALUES(?,?,'WAITING_REVIEW',?,?,?,?)",
                         (task_id, lane, json.dumps(spec, sort_keys=True), now(), now(), run))
        queue.db.execute("INSERT INTO attempts(id,task_id,lane,status,created_at,snapshot_path,snapshot_sha,callback_status) VALUES(?,?,?,'TERMINAL',?,?,?,'PREVIOUS_CALLBACK_PRESERVED')",
                         (run, task_id, lane, now(), str(directory), snapshot["snapshot_sha256"]))
        queue.event("PREVIOUS_DELIVERY_ADOPTED_NOT_APPROVED", task_id, run,
                    {"snapshot_sha": snapshot["snapshot_sha256"], "previous_terminal": str(terminal_path)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--owner")
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init")
    init.add_argument("--root", type=Path, required=True)
    init.add_argument("--previous", type=Path, required=True)
    init.add_argument("--review-limit", type=int, default=6)
    submit = commands.add_parser("submit")
    submit.add_argument("--spec", type=Path, required=True)
    imported = commands.add_parser("adopt")
    imported.add_argument("--lane", required=True)
    imported.add_argument("--terminal-sha", required=True)
    review = commands.add_parser("review")
    review.add_argument("--task", required=True)
    review.add_argument("--action", dest="review_action", choices=["integrating", "verified", "rework"], required=True)
    review.add_argument("--proof", required=True)
    rework = commands.add_parser("release-rework")
    rework.add_argument("--task", required=True)
    rework.add_argument("--prompt-file", type=Path, required=True)
    recovery = commands.add_parser("recover-uncertain")
    recovery.add_argument("--run", required=True)
    recovery.add_argument("--proof", required=True)
    reconcile = commands.add_parser("reconcile-adoption")
    reconcile.add_argument("--task", required=True)
    reconcile.add_argument("--adopted-run", required=True)
    reconcile.add_argument("--proof", required=True)
    commands.add_parser("status")
    commands.add_parser("tick")
    daemon = commands.add_parser("serve")
    daemon.add_argument("--interval", type=float, default=1.0)
    worker = commands.add_parser("worker")
    worker.add_argument("--run", required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    root = Path(__file__).resolve().parents[4]
    if not database.is_relative_to(root / ".local/orchestration"):
        raise ValueError("DATABASE_OUTSIDE_PRIVATE_ORCHESTRATION")
    if args.action == "serve":
        return serve(database, args.interval)
    if args.action == "worker":
        return run_worker(database, args.run)
    database.parent.mkdir(parents=True, exist_ok=True)
    queue = Queue(database)
    try:
        if args.action == "init":
            if args.root.resolve() != root:
                raise ValueError("CANONICAL_ROOT_CHANGED")
            initialize(queue, root, args.previous, args.review_limit)
        elif args.action == "submit":
            spec = json.loads(args.spec.read_text(encoding="utf8"))
            executor = queue.executor(spec["lane"])
            sources = source_manifest(executor["worktree"])
            spec.update(source_manifest=sources, base_sha=sources["head"])
            queue.submit(args.owner, spec)
        elif args.action == "adopt":
            adopt(queue, args.owner, args.lane, args.terminal_sha)
        elif args.action == "review":
            row = queue.db.execute("SELECT a.snapshot_path,a.snapshot_sha FROM tasks t JOIN attempts a ON a.id=t.run_id WHERE t.id=?", (args.task,)).fetchone()
            if row is None:
                raise ValueError("REVIEW_HANDOFF_MISSING")
            verify_frozen(Path(row[0]), row[1])
            queue.review(args.owner, args.task, args.review_action, args.proof)
        elif args.action == "release-rework":
            lane = queue.db.execute("SELECT lane FROM tasks WHERE id=?", (args.task,)).fetchone()[0]
            queue.release_rework(args.owner, args.task, source_manifest(queue.executor(lane)["worktree"]),
                                 args.prompt_file.read_text(encoding="utf8"))
        elif args.action == "recover-uncertain":
            queue.owner(args.owner)
            attempt = queue.attempt(args.run)
            executor = queue.executor(attempt["lane"])
            # A surviving CLI can be identified by its immutable run directory
            # or exact resumed UUID. Never return prompts/command lines in output.
            if sys.platform != "win32":
                raise ValueError("LIVE_RECOVERY_REQUIRES_WINDOWS_PROCESS_INVENTORY")
            script = ("$rows=Get-CimInstance Win32_Process | Where-Object {"
                      f"($_.CommandLine -like '*{attempt['id']}*') -or "
                      f"($_.CommandLine -like '*exec*resume*{executor['thread_id']}*')"
                      "} | Where-Object {$_.Name -notin @('powershell.exe','pwsh.exe') -and $_.CommandLine -notlike '*recover-uncertain*'};"
                      "@($rows | Select-Object -ExpandProperty ProcessId) | ConvertTo-Json -Compress")
            result = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                                     "-WindowStyle", "Hidden", "-Command", script],
                                    capture_output=True, text=True, timeout=30,
                                    creationflags=NO_WINDOW, check=True)
            pids = json.loads(result.stdout) if result.stdout.strip() else []
            if isinstance(pids, int):
                pids = [pids]
            queue.recover_uncertain(args.owner, args.run, args.proof, process_alive,
                                    no_other_writer=not any(pid != os.getpid() for pid in pids))
        elif args.action == "reconcile-adoption":
            queue.owner(args.owner)
            original = queue.attempt(args.adopted_run)
            if original["task_id"] != args.task:
                raise ValueError("ADOPTION_RECONCILIATION_BINDING_CHANGED")
            executor = queue.executor(original["lane"])
            frozen = verify_frozen(Path(original["snapshot_path"]), original["snapshot_sha"])
            terminal_path = Path(executor["previous_run"]) / "terminal.json"
            terminal = json.loads(terminal_path.read_text(encoding="utf8"))
            if (frozen["binding"].get("previous_terminal_sha256") != sha(terminal_path)
                    or frozen["sources"] != source_manifest(executor["worktree"])
                    or frozen["receipt_sha256"] != sha(Path(terminal["worker_receipt"]))):
                raise ValueError("ADOPTION_ORIGINAL_SOURCE_OR_RECEIPT_CHANGED")
            if sys.platform != "win32":
                raise ValueError("LIVE_RECONCILIATION_REQUIRES_WINDOWS_PROCESS_INVENTORY")
            script = ("@(Get-CimInstance Win32_Process | Where-Object {"
                      f"$_.CommandLine -like '*exec*resume*{executor['thread_id']}*'"
                      "} | Where-Object {$_.Name -notin @('powershell.exe','pwsh.exe')} "
                      "| Select-Object -ExpandProperty ProcessId) | ConvertTo-Json -Compress")
            result = subprocess.run(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                                     "-WindowStyle", "Hidden", "-Command", script],
                                    capture_output=True, text=True, timeout=30,
                                    creationflags=NO_WINDOW, check=True)
            pids = json.loads(result.stdout) if result.stdout.strip() else []
            if isinstance(pids, int):
                pids = [pids]
            queue.reconcile_adoption(args.owner, args.task, args.adopted_run, args.proof,
                                     process_alive, no_other_writer=not pids)
        elif args.action == "tick":
            queue.owner(args.owner)
            tick(queue)
        print(json.dumps(queue.status(), ensure_ascii=True))
    finally:
        queue.close()


if __name__ == "__main__":
    main()
