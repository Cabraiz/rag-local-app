"""Durable one-dispatch launcher for independent persistent Codex CLI sessions.

Only supervises jobs and terminal callbacks; never edits, merges or closes cards.
Runtime state is private and outside Git. Processes run under Task Scheduler.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import traceback
from uuid import uuid4

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value, *, attempts=9, delay=0.05):
    """Atomic snapshot; short Windows sharing violations must not kill a writer."""
    if attempts < 1 or delay < 0:
        raise ValueError("INVALID_SNAPSHOT_RETRY")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")
    for attempt in range(attempts):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                # Retain this private, uniquely named snapshot for diagnosis.
                raise
            time.sleep(delay)


def record_fault(state, operation, exc):
    """No raw exception messages, commands, prompts or secrets in diagnostics."""
    value = {"at": now(), "operation": operation, "error_type": type(exc).__name__,
             "errno": getattr(exc, "errno", None), "winerror": getattr(exc, "winerror", None),
             "frames": [{"file": Path(frame.filename).name, "line": frame.lineno,
                         "function": frame.name}
                        for frame in traceback.extract_tb(exc.__traceback__)[-5:]]}
    try:
        with (state / "supervisor-faults.jsonl").open("a", encoding="utf8") as stream:
            stream.write(json.dumps(value) + "\n")
    except OSError:
        print("SUPERVISOR_DIAGNOSTIC_UNAVAILABLE", file=sys.stderr)


def snapshot(state, name, value, progress):
    try:
        write_json(state / name, value)
    except OSError as exc:
        progress["supervisorFaults"] = progress.get("supervisorFaults", 0) + 1
        record_fault(state, name, exc)
        return False
    return True


def process_alive(pid):
    """Read-only liveness, fail closed on denied access; never os.kill on Windows."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("INVALID_PREVIOUS_PID")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # no such process
                return False
            if error == 5:
                return True  # unknown ownership/liveness is never permission to resume
            raise ctypes.WinError(error)
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def owner(manifest, root_card, card_id):
    if card_id in manifest["central_only"]:
        return "central"
    for lane in manifest["lanes"]:
        if card_id in lane.get("exclude_cards", []):
            continue
        if root_card in lane.get("root_cards", []) or (
                lane.get("root_card_prefix") and root_card.startswith(lane["root_card_prefix"])):
            return lane["id"]
    return "central"


def prepare(root, state, codex):
    root, state = Path(root).resolve(), Path(state).resolve()
    if state.exists():
        raise RuntimeError("RUN_ALREADY_PREPARED")
    if not state.is_relative_to(root / ".local" / "orchestration"):
        raise RuntimeError("STATE_OUTSIDE_PRIVATE_ORCHESTRATION")
    manifest = json.loads((root / "docs/orchestration/workstreams.json").read_text(encoding="utf8"))
    database = root / ".local/card-execution/queue.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        cards = [dict(row) for row in db.execute(
            "SELECT seq,id,parent,title,status,evidence_type,criteria FROM cards ORDER BY seq")]
    by_id = {card["id"]: card for card in cards}
    for card in cards:
        current, visited = card, set()
        while current.get("parent"):
            if current["id"] in visited or current["parent"] not in by_id:
                raise RuntimeError("INVALID_CARD_PARENT_GRAPH")
            visited.add(current["id"])
            current = by_id[current["parent"]]
        card["root_card"] = current["id"]
        card["owner"] = owner(manifest, current["id"], card["id"])
        card["criteria"] = json.loads(card["criteria"])
    state.mkdir(parents=True)
    write_json(state / "queue-snapshot.json", {"at": now(), "cards": cards})
    base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                   creationflags=NO_WINDOW, text=True).strip()
    for lane in manifest["lanes"]:
        directory = state / lane["id"]
        assigned = [card for card in cards if card["owner"] == lane["id"]]
        write_json(directory / "cards.json", {"at": now(), "cards": assigned})
        prompt = f"""Você é o executor da frente {lane['id']} de um trabalho paralelo explicitamente autorizado pelo usuário.
O usuário pediu: criar worktrees e novos chats, coordená-los, corrigir os cards e integrar sem bugs, usando GPT-6.1 Sol máximo. Confirmou Fast e o consumo maior. A Central integra; você não faz merge ou push.
Seu único workspace de escrita: {lane['worktree']}. Branch: {lane['branch']}. Base: {base}.
Leia AGENTS.md, docs/orchestration/acceptance.md, docs/orchestration/workstreams.json e os critérios da sua lista read-only em {directory / 'cards.json'}.
Objetivo: {lane['objective']}
Allowlist de escrita versionada: {json.dumps(lane['allowlist'], ensure_ascii=False)}.
Se um ajuste precisa de outro arquivo, não o edite: registre a dependência para a Central. Não leia segredos, bases ou .env do laboratório canônico. Nunca altere D:/RAG-Local, seus containers, imagens, dados ou fila. Nunca execute manage.py runtime, Docker builds, compose ou fault injection nesta rodada; a Central serializa os gates pesados. Não chame modelos/providers remotos ou escreva em GitHub/Jira.
Reutilize apenas o executável Python D:/RAG-Local/adk/.venv/Scripts/python.exe, sem instalar no ambiente compartilhado. Use imports e fontes do SEU worktree. O snapshot externo é somente leitura. Logs e receipts novos ficam em .local/ no seu worktree.
Não confunda 95 revalidações de fonte antiga com 95 bugs confirmados. Examine os critérios e regressões de TODOS os cards atribuídos, preserve os que já passam e corrija falhas reproduzíveis. Casos adversariais devem testar usuário, erro de desenvolvimento e abuso; não relaxe assertions para passar. Cada mudança reinicia a contagem; rode duas rodadas consecutivas com fontes congeladas dos testes offline disponíveis. Preserve falhas e informe os gates online/Docker ainda não executados. Não chame testes da mesma autoria de auditoria independente. BLOCKED não equivale a DONE.
Faça os commits locais necessários SOMENTE na sua branch, com arquivos da allowlist. Sem push, PR, merge, reset --hard, checkout destrutivo ou exclusão de dados. Use apply_patch; exec_command tty:true/login:false e processos ocultos.
Ao encerrar, escreva .local/orchestration/worker-receipt.json contendo: lane, branch, head_sha, status, cards_checked, files_changed, rounds, checks, failed_checks, pending_gates, new_bugs, dependencies, artifacts. Vincule checks às fontes por SHA-256. Liste bugs novos reproduzidos para a Central adicionar ao fim da fila; não edite o journal. Inclua um resumo legível em docs/orchestration/results/{lane['id']}/result.md, sem segredos nem evidência privada.
O supervisor entrega um callback terminal à Central automaticamente. Não mande mensagens paralelas ou repetidas. Trabalhe até concluir o escopo offline, ou até uma limitação real impedir progresso. Na resposta final use português curto, branch/SHA, checks e limitações.
"""
        (directory / "prompt.txt").write_text(prompt, encoding="utf8")
        write_json(directory / "job.json", {
            "lane": lane["id"], "title": lane["title"], "worktree": lane["worktree"],
            "branch": lane["branch"], "base_sha": base, "codex": str(Path(codex).resolve()),
            "parent_thread": manifest["central_thread"], "run_dir": str(directory),
            "canonical_root": str(root),
            "model": manifest["model"], "reasoning_effort": manifest["reasoning_effort"],
            "service_tier": manifest["service_tier"], "assigned_cards": len(assigned)})
    write_json(state / "progress.json", {"status": "waiting_executors", "startedAt": now(),
                                         "lastProgressAt": now(), "base_sha": base})
    print(json.dumps({"prepared": True, "cards": len(cards), "state": str(state),
                      "owners": {key: sum(c["owner"] == key for c in cards)
                                 for key in ["central"] + [x["id"] for x in manifest["lanes"]]}}))


def dispatch_claim(db, lane, resume_thread=None):
    db.execute("CREATE TABLE IF NOT EXISTS jobs(lane TEXT PRIMARY KEY, started_at TEXT, "
               "status TEXT, thread_id TEXT, callback_status TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS recovery_history(id INTEGER PRIMARY KEY, "
               "lane TEXT, at TEXT, previous_started_at TEXT, previous_status TEXT, "
               "thread_id TEXT, previous_callback_status TEXT)")
    if resume_thread:
        with db:
            previous = db.execute("SELECT started_at,status,thread_id,callback_status FROM jobs "
                                  "WHERE lane=?", (lane,)).fetchone()
            changed = db.execute("UPDATE jobs SET started_at=?,status='STARTING',"
                                 "callback_status='NOT_READY' WHERE lane=? AND status='FAILED' "
                                 "AND thread_id=?", (now(), lane, resume_thread)).rowcount
            if changed != 1:
                return False
            db.execute("INSERT INTO recovery_history(lane,at,previous_started_at,"
                       "previous_status,thread_id,previous_callback_status) VALUES (?,?,?,?,?,?)",
                       (lane, now(), *previous))
        return True
    try:
        with db:
            db.execute("INSERT INTO jobs VALUES (?,?,'STARTING',NULL,'NOT_READY')", (lane, now()))
        return True
    except sqlite3.IntegrityError:
        return False


def validate_recovery(job):
    terminal = Path(job["resume_from"])
    if hashlib.sha256(terminal.read_bytes()).hexdigest() != job["resume_terminal_sha256"]:
        raise RuntimeError("RECOVERY_TERMINAL_CHANGED")
    value = json.loads(terminal.read_text(encoding="utf8"))
    if (value.get("status") != "FAILED" or value.get("lane") != job["lane"]
            or value.get("thread_id") != job["resume_thread"]):
        raise RuntimeError("RECOVERY_IDENTITY_MISMATCH")
    if len(job.get("previous_pids", [])) != 2:
        raise RuntimeError("RECOVERY_PREVIOUS_PIDS_REQUIRED")
    if any(process_alive(pid) for pid in job["previous_pids"]):
        raise RuntimeError("PREVIOUS_WRITER_OR_SUPERVISOR_STILL_ALIVE")
    branch = subprocess.check_output(["git", "-C", job["worktree"], "branch", "--show-current"],
                                     creationflags=NO_WINDOW, text=True).strip()
    if branch != job["branch"]:
        raise RuntimeError("RECOVERY_BRANCH_CHANGED")


def prepare_recovery(job_path, terminal_sha, attempt):
    if not attempt.startswith("recovery-") or not attempt[len("recovery-"):].isdigit():
        raise ValueError("INVALID_RECOVERY_ATTEMPT_NAME")
    original = json.loads(Path(job_path).read_text(encoding="utf8"))
    previous_state = Path(original["run_dir"]).resolve()
    if not previous_state.is_relative_to(Path(original["canonical_root"]).resolve() / ".local/orchestration"):
        raise RuntimeError("RECOVERY_OUTSIDE_PRIVATE_STATE")
    previous = json.loads((previous_state / "progress.json").read_text(encoding="utf8"))
    terminal = json.loads((previous_state / "terminal.json").read_text(encoding="utf8"))
    state = previous_state / attempt
    job = {**original, "run_dir": str(state),
           "dispatch_db": str(previous_state.parent / "dispatch.sqlite3"),
           "resume_thread": terminal["thread_id"], "resume_from": str(previous_state / "terminal.json"),
           "resume_terminal_sha256": terminal_sha,
           "previous_pids": [previous["executorPid"], previous["supervisorPid"]]}
    validate_recovery(job)
    with closing(sqlite3.connect(Path(job["dispatch_db"]).as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT status,thread_id FROM jobs WHERE lane=?", (job["lane"],)).fetchone()
    if row != ("FAILED", job["resume_thread"]):
        raise RuntimeError("RECOVERY_LANE_NOT_FAILED_OR_ALREADY_DISPATCHED")
    state.mkdir()  # exclusive; never replace a previous attempt or its receipts
    prompt = f"""Retome ESTA sessão {job['resume_thread']} no MESMO worktree {job['worktree']} e branch {job['branch']}.
A supervisão anterior falhou com PermissionError e fechou o pipe stdout. A Central confirmou os PIDs mortos e corrigiu o supervisor; o histórico e suas alterações locais foram preservados.
Continue do último checkpoint. Não crie outro executor nem recomece os cards já verificados. Confira os arquivos que ficaram incompletos pela interrupção.
Permanecem o manifesto, a allowlist, os critérios originais e o snapshot de cards da primeira execução. Não escreva na Central, journal, credenciais ou containers; nada de cloud, instalações no Python compartilhado, Docker ou ações remotas.
Finalize as correções reproduzíveis e duas rodadas consecutivas com fontes congeladas. Preserve falhas e não conte rodadas anteriores a uma mudança como aprovação atual.
Gates online/Docker/SDK indisponíveis continuam pendentes, não solucionados por inferência. Faça commit local só da allowlist.
Ao terminar, entregue .local/orchestration/worker-receipt.json com branch/SHA, fontes SHA-256, rodadas, cards verificados, bugs novos, dependências e limitações; resumo em docs/orchestration/results/{job['lane']}/result.md. O supervisor entrega a conclusão à Central uma única vez.
"""
    (state / "prompt.txt").write_text(prompt, encoding="utf8")
    write_json(state / "job.json", job)
    print(json.dumps({"prepared_recovery": True, "lane": job["lane"],
                      "thread_id": job["resume_thread"], "job": str(state / "job.json")}))


def build_command(job, state, worktree):
    command = [job["codex"], "exec", "--json", "--color", "never", "--model", job["model"],
               "-c", f'model_reasoning_effort="{job["reasoning_effort"]}"',
               "-c", f'service_tier="{job["service_tier"]}"', "--enable", "fast_mode",
               "-c", 'approval_policy="never"', "--sandbox", "workspace-write",
               "-c", "sandbox_workspace_write.network_access=false", "--cd", str(worktree),
               "--add-dir", str(Path(job["canonical_root"]) / ".git"),
               "--output-last-message", str(state / "last-message.txt")]
    return command + (["resume", job["resume_thread"], "-"] if job.get("resume_thread") else ["-"])


def accept_event(event, job, state, db, progress):
    kind, changed = event.get("type"), False
    if kind == "thread.started":
        if job.get("resume_thread") and event["thread_id"] != job["resume_thread"]:
            raise RuntimeError("RESUMED_THREAD_ID_CHANGED")
        progress["threadId"], progress["identityVerified"] = event["thread_id"], True
        with db:
            db.execute("UPDATE jobs SET status='RUNNING',thread_id=? WHERE lane=?",
                       (event["thread_id"], job["lane"]))
        snapshot(state, "identity.json", {**job, "thread_id": event["thread_id"]}, progress)
        changed = True
    if kind == "item.completed" and event.get("item", {}).get("type") in (
            "command_execution", "file_change", "mcp_tool_call", "web_search"):
        progress["completedItems"] += 1
        progress["lastProgressAt"] = now()
        progress["progressFingerprint"] = str(progress["completedItems"])
        changed = True
    if kind in ("turn.completed", "turn.failed"):
        progress["terminalEvent"] = kind
        changed = True
    if changed:
        snapshot(state, "progress.json", progress, progress)


def drain_events(stream, child, consume):
    """Tail durable stdout, retaining partial UTF-8/JSON until newline or exit."""
    while True:
        offset = stream.tell()
        line = stream.readline()
        alive = child.poll() is None
        if line and (line.endswith(b"\n") or not alive):
            try:
                event = json.loads(line.decode("utf8"))
            except (ValueError, UnicodeError):
                continue
            if isinstance(event, dict):
                consume(event)
        elif alive:
            stream.seek(offset)
            time.sleep(0.05)
        elif not line:
            break


def notify_terminal(job, state, worktree, receipt):
    status = receipt["status"]
    message = (f"RAG_EXECUTOR_TERMINAL lane={job['lane']} status={status} "
               f"thread={receipt.get('thread_id')} receipt={state / 'terminal.json'}. "
               "Revise a allowlist, o SHA e as duas rodadas antes de integrar; não há aprovação automática. "
               "Retome a integração serial das frentes autorizadas pelo usuário, sem polling.")
    try:
        callback = subprocess.run([job["codex"], "queue", "--thread", job["parent_thread"],
                                   "--message", message], cwd=worktree, capture_output=True,
                                  text=True, encoding="utf8", errors="replace", timeout=45,
                                  creationflags=NO_WINDOW, shell=False)
        delivery = "QUEUED" if callback.returncode == 0 else "FAILED_DELIVERY"
    except subprocess.TimeoutExpired:
        delivery = "UNCERTAIN_DO_NOT_RETRY"
    except OSError:
        delivery = "FAILED_DELIVERY"
    write_json(state / "callback.json", {"status": delivery, "at": now(),
                                         "parent_thread": job["parent_thread"], "message": message})
    return delivery


def run(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf8"))
    state, worktree = Path(job["run_dir"]), Path(job["worktree"])
    if job.get("resume_thread"):
        validate_recovery(job)  # repeated immediately before atomic claim, not just preparation
    database = Path(job.get("dispatch_db", state.parent / "dispatch.sqlite3"))
    with closing(sqlite3.connect(database, timeout=20)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        if not dispatch_claim(db, job["lane"], job.get("resume_thread")):
            print("ALREADY_DISPATCHED_NO_SECOND_WRITER")
            return
        progress = {"status": "running", "startedAt": now(), "lastProgressAt": now(),
                    "lane": job["lane"], "supervisorPid": os.getpid(), "completedItems": 0,
                    "supervisorFaults": 0, "identityVerified": False}
        if job.get("resume_thread"):
            progress["threadId"] = job["resume_thread"]
        snapshot(state, "progress.json", progress, progress)
        child, exit_code, error_type = None, None, None
        try:
            # Child owns a real file handle: a supervisor/progress failure cannot close its stdout pipe.
            with (state / "stderr.log").open("ab") as errors, (state / "events.jsonl").open("ab") as events:
                child = subprocess.Popen(build_command(job, state, worktree), stdin=subprocess.PIPE,
                                         stdout=events, stderr=errors, text=True, encoding="utf8",
                                         creationflags=NO_WINDOW, shell=False, cwd=worktree)
                progress["executorPid"] = child.pid
                snapshot(state, "progress.json", progress, progress)
                try:
                    child.stdin.write((state / "prompt.txt").read_text(encoding="utf8"))
                    child.stdin.close()
                except BrokenPipeError as exc:
                    record_fault(state, "stdin", exc)
                with (state / "events.jsonl").open("rb") as source:
                    drain_events(source, child, lambda event: accept_event(event, job, state, db, progress))
                exit_code = child.wait()
        except Exception as exc:
            error_type = type(exc).__name__
            record_fault(state, "execute", exc)
            if child is not None:
                exit_code = child.poll()
        alive = child is not None and exit_code is None
        if alive:
            # Fail closed: a lost supervisor is NOT permission to launch a second writer.
            status = "SUPERVISOR_FAILED_EXECUTOR_RUNNING"
        else:
            status = "TURN_COMPLETED" if (exit_code == 0 and progress.get("terminalEvent") ==
                      "turn.completed" and progress["identityVerified"] and not error_type) else "FAILED"
        progress.update(status=status, endedAt=now(), lastProgressAt=now())
        snapshot(state, "progress.json", progress, progress)
        worker_receipt = worktree / ".local/orchestration/worker-receipt.json"
        receipt = {"lane": job["lane"], "status": status, "thread_id": progress.get("threadId"),
                   "exit_code": exit_code, "error_type": error_type, "ended_at": now(),
                   "executor_alive": alive, "executor_pid": progress.get("executorPid"),
                   "supervisor_faults": progress["supervisorFaults"],
                   "worker_receipt": str(worker_receipt), "worker_receipt_exists": worker_receipt.is_file(),
                   "worker_receipt_sha256": hashlib.sha256(worker_receipt.read_bytes()).hexdigest()
                   if worker_receipt.is_file() else None, "merge_approved": False}
        write_json(state / "terminal.json", receipt)
        with db:
            db.execute("UPDATE jobs SET status=?,callback_status='PENDING' WHERE lane=?", (status, job["lane"]))
        delivery = notify_terminal(job, state, worktree, receipt)
        with db:
            db.execute("UPDATE jobs SET callback_status=? WHERE lane=?", (delivery, job["lane"]))
        return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--root", required=True)
    prep.add_argument("--state", required=True)
    prep.add_argument("--codex", required=True)
    worker = commands.add_parser("run")
    worker.add_argument("--job", required=True)
    recovery = commands.add_parser("prepare-recovery")
    recovery.add_argument("--job", required=True)
    recovery.add_argument("--terminal-sha", required=True)
    recovery.add_argument("--attempt", required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.root, args.state, args.codex)
    elif args.action == "prepare-recovery":
        prepare_recovery(args.job, args.terminal_sha, args.attempt)
    else:
        run(args.job)
