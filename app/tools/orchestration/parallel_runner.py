"""Durable one-dispatch launcher for independent persistent Codex CLI sessions.

Only supervises jobs and terminal callbacks; never edits, merges or closes cards.
Runtime state is private and outside Git. Processes run under Task Scheduler.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")
    os.replace(temporary, path)


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
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
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
            "model": manifest["model"], "reasoning_effort": manifest["reasoning_effort"],
            "service_tier": manifest["service_tier"], "assigned_cards": len(assigned)})
    write_json(state / "progress.json", {"status": "waiting_executors", "startedAt": now(),
                                         "lastProgressAt": now(), "base_sha": base})
    print(json.dumps({"prepared": True, "cards": len(cards), "state": str(state),
                      "owners": {key: sum(c["owner"] == key for c in cards)
                                 for key in ["central"] + [x["id"] for x in manifest["lanes"]]}}))


def dispatch_claim(db, lane):
    db.execute("CREATE TABLE IF NOT EXISTS jobs(lane TEXT PRIMARY KEY, started_at TEXT, "
               "status TEXT, thread_id TEXT, callback_status TEXT)")
    try:
        with db:
            db.execute("INSERT INTO jobs VALUES (?,?,'STARTING',NULL,'NOT_READY')", (lane, now()))
        return True
    except sqlite3.IntegrityError:
        return False


def run(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf8"))
    state, worktree = Path(job["run_dir"]), Path(job["worktree"])
    db = sqlite3.connect(state.parent / "dispatch.sqlite3", timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    if not dispatch_claim(db, job["lane"]):
        print("ALREADY_DISPATCHED_NO_SECOND_WRITER")
        return
    progress = {"status": "running", "startedAt": now(), "lastProgressAt": now(),
                "lane": job["lane"], "supervisorPid": os.getpid(), "completedItems": 0}
    write_json(state / "progress.json", progress)
    command = [job["codex"], "exec", "--json", "--color", "never", "--model", job["model"],
               "-c", f'model_reasoning_effort="{job["reasoning_effort"]}"',
               "-c", f'service_tier="{job["service_tier"]}"', "--enable", "fast_mode",
               "-c", 'approval_policy="never"', "--sandbox", "workspace-write",
               "-c", "sandbox_workspace_write.network_access=false", "--cd", str(worktree),
               "--output-last-message", str(state / "last-message.txt"), "-"]
    terminal_event, exit_code, error_type = None, None, None
    try:
        with (state / "stderr.log").open("a", encoding="utf8") as errors, \
                (state / "events.jsonl").open("a", encoding="utf8") as events:
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=errors, text=True, encoding="utf8", errors="replace",
                                     creationflags=NO_WINDOW, shell=False, cwd=worktree)
            progress["executorPid"] = child.pid
            write_json(state / "progress.json", progress)
            child.stdin.write((state / "prompt.txt").read_text(encoding="utf8"))
            child.stdin.close()
            for line in child.stdout:
                events.write(line)
                events.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "thread.started":
                    progress["threadId"] = event["thread_id"]
                    with db:
                        db.execute("UPDATE jobs SET status='RUNNING',thread_id=? WHERE lane=?",
                                   (event["thread_id"], job["lane"]))
                    write_json(state / "identity.json", {**job, "thread_id": event["thread_id"]})
                if event.get("type") == "item.completed" and event.get("item", {}).get("type") in (
                        "command_execution", "file_change", "mcp_tool_call", "web_search"):
                    progress["completedItems"] += 1
                    progress["lastProgressAt"] = now()
                    progress["progressFingerprint"] = str(progress["completedItems"])
                if event.get("type") in ("turn.completed", "turn.failed"):
                    terminal_event = event["type"]
                write_json(state / "progress.json", progress)
            exit_code = child.wait()
    except Exception as exc:
        error_type = type(exc).__name__
    status = "TURN_COMPLETED" if exit_code == 0 and terminal_event == "turn.completed" else "FAILED"
    progress.update(status=status, endedAt=now(), lastProgressAt=now())
    write_json(state / "progress.json", progress)
    worker_receipt = worktree / ".local/orchestration/worker-receipt.json"
    receipt = {"lane": job["lane"], "status": status, "thread_id": progress.get("threadId"),
               "exit_code": exit_code, "error_type": error_type, "ended_at": now(),
               "worker_receipt": str(worker_receipt), "worker_receipt_exists": worker_receipt.is_file(),
               "worker_receipt_sha256": hashlib.sha256(worker_receipt.read_bytes()).hexdigest()
               if worker_receipt.is_file() else None, "merge_approved": False}
    write_json(state / "terminal.json", receipt)
    with db:
        db.execute("UPDATE jobs SET status=?,callback_status='PENDING' WHERE lane=?", (status, job["lane"]))
    message = (f"RAG_EXECUTOR_TERMINAL lane={job['lane']} status={status} "
               f"thread={progress.get('threadId')} receipt={state / 'terminal.json'}. "
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
    with db:
        db.execute("UPDATE jobs SET callback_status=? WHERE lane=?", (delivery, job["lane"]))
    write_json(state / "callback.json", {"status": delivery, "at": now(),
                                         "parent_thread": job["parent_thread"], "message": message})
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="action", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--root", required=True)
    prep.add_argument("--state", required=True)
    prep.add_argument("--codex", required=True)
    worker = commands.add_parser("run")
    worker.add_argument("--job", required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.root, args.state, args.codex)
    else:
        run(args.job)
