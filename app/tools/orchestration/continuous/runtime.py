"""Persistent local dispatch and per-attempt supervision, without model polling."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .evidence import digest, freeze, source_manifest, verify_frozen, NO_WINDOW
from .store import Queue, now
from parallel_runner import build_command, drain_events, process_alive, write_json


def observed(queue: Queue, lane: str) -> dict:
    executor = queue.executor(lane)
    for pid in executor["previous_pids"]:
        if process_alive(pid):
            return {"safe": False, "reason": "IDLE_PREVIOUS_EXECUTOR_OR_SUPERVISOR_ALIVE"}
    sources = source_manifest(executor["worktree"])
    if sources["branch"] != executor["branch"]:
        return {"safe": False, "reason": "IDLE_BRANCH_CHANGED"}
    for row in queue.db.execute("SELECT a.snapshot_path,a.snapshot_sha FROM tasks t JOIN attempts a ON a.id=t.run_id WHERE t.lane=? AND t.state IN ('WAITING_REVIEW','INTEGRATING','REWORK','BLOCKED') AND a.snapshot_path IS NOT NULL", (lane,)):
        frozen = verify_frozen(Path(row[0]), row[1])
        if frozen["sources"] != sources:
            return {"safe": False, "reason": "IDLE_FROZEN_WORKTREE_CHANGED"}
    return {"safe": True, "sources": sources}


def runner_contract(reservation: dict, state: Path) -> tuple[list[str], str]:
    task, executor = reservation["task"], reservation["executor"]
    job = {**executor, "resume_thread": executor["thread_id"]}
    command = build_command(job, state, Path(executor["worktree"]))
    if task["mode"] == "read_only":
        command[command.index("--sandbox") + 1] = "read-only"
        index = command.index("--add-dir")
        del command[index:index + 2]
    schema = state / "output-schema.json"
    write_json(schema, {
        "type": "object", "additionalProperties": False,
        "properties": {"task_id": {"type": "string"},
                       "outcome": {"type": "string", "enum": ["completed", "blocked"]},
                       "checks": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                           "properties": {"name": {"type": "string"}, "passed": {"type": "boolean"},
                                          "detail": {"type": "string"}}, "required": ["name", "passed", "detail"]}},
                       "findings": {"type": "array", "items": {"type": "string"}},
                       "limitations": {"type": "array", "items": {"type": "string"}}},
        "required": ["task_id", "outcome", "checks", "findings", "limitations"]})
    # Global exec flags precede the resume subcommand (validated on installed CLI).
    index = command.index("resume")
    command[index:index] = ["--output-schema", str(schema)]
    prompt = f"""Continuação autorizada na MESMA tarefa {executor['thread_id']}.
Tarefa de execução {task['id']}; não é aprovação de card. Branch {executor['branch']}.
Modo {task['mode']}. Base {task['base_sha']}. Fontes {digest(task['source_manifest'])}.
Não repita o lote anterior. A Central já integrou o corpus; as outras entregas ficam congeladas.
Esta tarefa tem seu próprio receipt gerado pelo supervisor: não altere worker-receipt.json anterior.
Somente esta tarefa: {task['prompt']}
Proibido: outra tarefa, arquivos fora do escopo {task.get('write_prefixes', [])},
alterar o journal ou D:/RAG-Local, credenciais, rede/cloud, Docker, Git push/merge,
outro chat, outro writer, instalar dependências ou alterar permissões.
Em modo read_only não edite nenhum arquivo: comandos de leitura e resultado JSON final.
Não afirme duas rodadas/ausência de bugs sem executar as verificações correspondentes.
Modelo/esforço/Fast permanecem {executor['model']}/{executor['reasoning_effort']}/{executor['service_tier']}.
Retorne somente o objeto do schema com task_id={task['id']}, checks, findings e limitações.
"""
    return command, prompt


def valid_result(path: Path, task: dict) -> dict:
    if not path.is_file() or path.stat().st_size > 256_000:
        raise ValueError("RESULT_MISSING_OR_OVERSIZED")
    def unique(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("RESULT_IDENTITY_OR_SCHEMA_INVALID")
            result[key] = item
        return result
    try:
        value = json.loads(path.read_text(encoding="utf8"), object_pairs_hook=unique)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("RESULT_IDENTITY_OR_SCHEMA_INVALID") from None
    if (not isinstance(value, dict) or set(value) != {"task_id", "outcome", "checks", "findings", "limitations"}
            or value.get("task_id") != task["id"] or value.get("outcome") not in ("completed", "blocked")
            or not isinstance(value.get("checks"), list) or not value["checks"]
            or not all(isinstance(c, dict) and set(c) == {"name", "passed", "detail"} and type(c.get("passed")) is bool
                       and isinstance(c.get("name"), str) and c["name"].strip()
                       and isinstance(c.get("detail"), str) for c in value["checks"])
            or not isinstance(value.get("findings"), list)
            or not all(isinstance(x, str) for x in value["findings"])
            or not isinstance(value.get("limitations"), list)
            or not all(isinstance(x, str) for x in value["limitations"])):
        raise ValueError("RESULT_IDENTITY_OR_SCHEMA_INVALID")
    return value


def check_writer_scope(before: dict, after: dict, task: dict):
    if before["branch"] != after["branch"]:
        raise ValueError("WRITER_BRANCH_CHANGED")
    changed = {name for name in before["files"].keys() | after["files"].keys()
               if before["files"].get(name) != after["files"].get(name)}
    for name in changed:
        if not any(name == p or (p.endswith("/") and name.startswith(p)) for p in task["write_prefixes"]):
            raise ValueError("WRITER_OUTSIDE_TASK_SCOPE")


def notify(queue: Queue, run: str, directory: Path):
    if not queue.callback_claim(run):
        return
    attempt = queue.attempt(run)
    executor = queue.executor(attempt["lane"])
    message = (f"RAG_CONTINUOUS_TERMINAL task={attempt['task_id']} lane={attempt['lane']} "
               f"run={run} receipt={directory / 'receipt.json'} snapshot={attempt['snapshot_sha']}. "
               "Entrega congelada, sem aprovação automática. Sucessor READY é despachado "
               "localmente; revise e integre em série, sem polling de modelo.")
    try:
        result = subprocess.run([executor["codex"], "queue", "--thread", executor["parent_thread"],
                                 "--message", message], capture_output=True, timeout=45,
                                creationflags=NO_WINDOW, shell=False)
        state = "QUEUED" if result.returncode == 0 else "FAILED_DELIVERY"
    except subprocess.TimeoutExpired:
        state = "UNCERTAIN_DO_NOT_RETRY"
    except OSError:
        state = "FAILED_DELIVERY"
    queue.callback_result(run, state)
    write_json(directory / "callback.json", {"status": state, "at": now()})


def run_worker(database: Path, run: str):
    queue = Queue(database)
    directory = Path(database).parent / "runs" / run
    directory.mkdir(parents=True, exist_ok=True)
    child, exit_code = None, -1
    attempt = queue.attempt(run)
    task = json.loads(queue.db.execute("SELECT spec FROM tasks WHERE id=?", (attempt["task_id"],)).fetchone()[0])
    executor = queue.executor(attempt["lane"])
    queue.bind_pid(run, os.getpid())
    baseline = task["source_manifest"]
    try:
        facts = observed(queue, attempt["lane"])
        if not facts.get("safe") or facts.get("sources") != baseline:
            raise ValueError("EXECUTION_PREFLIGHT_CHANGED")
        command, prompt = runner_contract({"task": task, "executor": executor}, directory)
        with (directory / "events.jsonl").open("ab") as output, (directory / "stderr.log").open("ab") as errors:
            child = subprocess.Popen(command, cwd=executor["worktree"], stdin=subprocess.PIPE,
                                     stdout=output, stderr=errors, text=True, encoding="utf8",
                                     creationflags=NO_WINDOW, shell=False)
            queue.bind_pid(run, child.pid, child=True)
            child.stdin.write(prompt)
            child.stdin.close()
            with (directory / "events.jsonl").open("rb") as stream:
                drain_events(stream, child, lambda event: queue.observe(run, event))
            exit_code = child.wait()
        result = valid_result(directory / "last-message.txt", task)
        after = source_manifest(executor["worktree"])
        if task["mode"] == "read_only" and after != baseline:
            raise ValueError("READ_ONLY_SOURCE_CHANGED")
        if task["mode"] == "writer":
            check_writer_scope(baseline, after, task)
        success = (result["outcome"] == "completed" and all(c["passed"] for c in result["checks"])
                   and not result["findings"])
        receipt = {"task_id": task["id"], "run_id": run, "thread_id": executor["thread_id"],
                   "lane": executor["lane"], "at": now(), "exit_code": exit_code,
                   "result": result, "source_sha256": digest(after), "approved": False}
    except Exception as exc:
        if child is not None and child.poll() is None:
            queue.uncertain(run, "SUPERVISOR_FAULT_EXECUTOR_ALIVE")
            write_json(directory / "fault.json", {"at": now(), "error_type": type(exc).__name__})
            queue.close()
            return
        if child is not None:
            exit_code = child.poll()
        success = False
        receipt = {"task_id": task["id"], "run_id": run, "thread_id": executor["thread_id"],
                   "lane": executor["lane"], "at": now(), "exit_code": exit_code,
                   "error_type": type(exc).__name__, "approved": False}
    try:
        write_json(directory / "receipt.json", receipt)
        frozen_dir = Path(database).parent / "frozen" / run
        frozen = freeze(frozen_dir, Path(executor["worktree"]),
                        {"task_id": task["id"], "run_id": run, "thread_id": executor["thread_id"]},
                        directory / "receipt.json", expected=baseline if task["mode"] == "read_only" else None)
        queue.terminal(run, snapshot_path=str(frozen_dir), snapshot_sha=frozen["snapshot_sha256"],
                       exit_code=exit_code, success=success, writer_dead=child is None or child.poll() is not None)
        # The durable terminal event releases eligible successors BEFORE callback I/O.
        notify(queue, run, directory)
    except Exception as exc:
        queue.uncertain(run, "HANDOFF_FREEZE_OR_TERMINAL_FAILED")
        write_json(directory / "fault.json", {"at": now(), "error_type": type(exc).__name__})
    finally:
        queue.close()


def reconcile(queue: Queue):
    for row in queue.db.execute("SELECT a.* FROM attempts a JOIN executors e ON e.run_id=a.id WHERE a.status IN ('STARTING','RUNNING')").fetchall():
        attempt = dict(row)
        supervisor = attempt["supervisor_pid"]
        if supervisor and process_alive(supervisor):
            continue
        if supervisor is None and attempt["launcher_pid"] and process_alive(attempt["launcher_pid"]):
            continue
        executor = attempt["executor_pid"]
        if executor and process_alive(executor):
            queue.uncertain(attempt["id"], "SUPERVISOR_DEAD_EXECUTOR_ALIVE")
        elif (datetime.now(timezone.utc) - datetime.fromisoformat(attempt["created_at"])).total_seconds() >= 30:
            queue.uncertain(attempt["id"], "MISSING_TERMINAL_OR_UNCERTAIN_SPAWN")


def tick(queue: Queue, *, launch=None) -> list[str]:
    reconcile(queue)
    started = []
    for row in queue.db.execute("SELECT lane FROM executors WHERE run_id IS NULL ORDER BY lane").fetchall():
        lane = row[0]
        if not queue.db.execute("SELECT 1 FROM tasks WHERE lane=? AND state='READY'", (lane,)).fetchone():
            queue.db.execute("UPDATE executors SET idle_reason='IDLE_NO_READY_WORK' WHERE lane=?", (lane,))
            continue
        try:
            facts = observed(queue, lane)
        except Exception:
            facts = {"safe": False, "reason": "IDLE_SOURCE_OR_FROZEN_EVIDENCE_UNCERTAIN"}
        reservation = queue.reserve(lane, facts)
        if not reservation:
            continue
        run = reservation["run_id"]
        try:
            if launch is not None:
                pid = launch(reservation)
            else:
                executable = Path(sys.executable).with_name("pythonw.exe") if os.name == "nt" else Path(sys.executable)
                directory = queue.path.parent / "runs" / run
                directory.mkdir(parents=True, exist_ok=True)
                with (directory / "startup.stdout.log").open("ab") as output, (directory / "startup.stderr.log").open("ab") as errors:
                    pid = subprocess.Popen([str(executable), "-B", str(Path(__file__).with_name("control.py")),
                                            "--database", str(queue.path), "worker", "--run", run],
                                           cwd=reservation["executor"]["worktree"], stdin=subprocess.DEVNULL,
                                           stdout=output, stderr=errors,
                                           creationflags=NO_WINDOW, shell=False).pid
            # Windows venv redirectors have a different PID from os.getpid().
            queue.bind_pid(run, pid, launcher=True)
            started.append(run)
        except Exception:
            # Reservation is intentionally NOT undone: a spawn may have succeeded.
            queue.uncertain(run, "SPAWN_OUTCOME_UNCERTAIN")
    return started


def serve(database: Path, interval=1.0):
    if not 0.1 <= interval <= 5:
        raise ValueError("INVALID_LOCAL_EVENT_INTERVAL")
    queue = Queue(database)
    queue.claim_daemon(os.getpid(), process_alive)
    started_at = now()
    last_seq = queue.db.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
    material = now()
    while True:
        try:
            tick(queue)
            status = queue.status()
            seq = queue.db.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
            if seq != last_seq:
                material, last_seq = now(), seq
            active = any(e["run_id"] for e in status["executors"])
            write_json(Path(database).parent / "status.json", {**status, "at": now(), "pid": os.getpid()})
            write_json(Path(database).parent / "progress.json", {
                "status": "running" if active else "idle", "startedAt": started_at,
                "lastProgressAt": material, "heartbeatAt": now(), "supervisorPid": os.getpid(),
                "progressFingerprint": str(last_seq), "detectorModelCalls": 0, "detectorTokenCost": 0})
            queue.db.execute("UPDATE daemon SET heartbeat_at=?,last_material_at=? WHERE id=1 AND pid=?", (now(), material, os.getpid()))
        except Exception as exc:
            write_json(Path(database).parent / "dispatcher-fault.json", {"at": now(), "error_type": type(exc).__name__})
        time.sleep(interval)
