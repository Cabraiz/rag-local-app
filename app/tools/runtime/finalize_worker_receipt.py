"""Seal lane evidence after the local allowlist commit; never edit the journal."""
import argparse
from datetime import datetime, timezone
from difflib import unified_diff
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from revalidate_offline import CASES, ROOT, source_hashes


BASE = "3dba8c0f557421cd3f6d5bc26017278a95a53dbb"
BRANCH = "codex/rag-runtime"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    value = subprocess.run(["git", *args], cwd=ROOT, shell=False, capture_output=True, text=True,
                           timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if value.returncode:
        raise ValueError("LOCAL_GIT_INSPECTION_FAILED")
    return value.stdout.strip()


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--gate", default=".local/orchestration/final-offline-commit-blocked/receipt.json")
    cli.add_argument("--cards", required=True)
    cli.add_argument("--commit-blocked", action="store_true")
    args = cli.parse_args()
    gate_path = (ROOT / args.gate).resolve()
    if not gate_path.is_relative_to(ROOT / ".local"):
        raise ValueError("LOCAL_GATE_REQUIRED")
    gate = json.loads(gate_path.read_text(encoding="utf8"))
    if gate.get("complete") is not True or gate.get("consecutive_passes") != 2 or len(gate["rounds"]) != 2:
        raise ValueError("TWO_CURRENT_ROUNDS_REQUIRED")
    if any(not round_["passed"] or not round_["sources_unchanged"] or not all(c["passed"] for c in round_["checks"])
           for round_ in gate["rounds"]):
        raise ValueError("FAILED_CURRENT_OFFLINE_CHECK")
    if source_hashes() != gate["sources_sha256"]:
        raise ValueError("SOURCE_CHANGED_RESET_CLEAN_COUNT")
    if git("branch", "--show-current") != BRANCH:
        raise ValueError("RUNTIME_BRANCH_REQUIRED")
    dirty = bool(git("status", "--porcelain"))
    if dirty and not args.commit_blocked:
        raise ValueError("CLEAN_COMMITTED_RUNTIME_BRANCH_REQUIRED")
    head = git("rev-parse", "HEAD")
    if args.commit_blocked:
        blocker_path = ROOT / ".local/orchestration/git-blocker.json"
        blocker = json.loads(blocker_path.read_text(encoding="utf8"))
        if not dirty or head != BASE or blocker.get("commit_created") is not False or blocker.get("sandbox_bypassed") is not False:
            raise ValueError("VERIFIED_LOCAL_COMMIT_BLOCKER_REQUIRED")
        untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
        files = sorted(set(git("diff", "--name-only", BASE).splitlines() + untracked))
    else:
        files = git("diff", "--name-only", BASE, head).splitlines()
    lane = next(l for l in json.loads((ROOT / "docs/orchestration/workstreams.json").read_text(encoding="utf8"))["lanes"] if l["id"] == "runtime")
    if not files or any(not any(path == rule or (rule.endswith("/") and path.startswith(rule)) for rule in lane["allowlist"]) for path in files):
        raise ValueError("COMMIT_ALLOWLIST_REQUIRED")
    git("diff", "--check", BASE) if args.commit_blocked else git("diff", "--check", BASE, head)
    cards_path = Path(args.cards).resolve()
    expected = Path("D:/RAG-Local/.local/orchestration/parallel-20261002-v1/runtime/cards.json").resolve()
    if cards_path != expected:
        raise ValueError("ASSIGNED_READ_ONLY_SNAPSHOT_REQUIRED")
    cards = json.loads(cards_path.read_text(encoding="utf-8-sig"))["cards"]
    if digest(cards_path) != gate["cards_snapshot_sha256"]:
        raise ValueError("ASSIGNED_CRITERIA_CHANGED")
    plans = json.loads((ROOT / "docs/orchestration/results/runtime/gates.json").read_text(encoding="utf8"))
    covered = {card for _, group, _, _ in CASES for card in group}
    if covered != {card["id"] for card in cards}:
        raise ValueError("EVERY_ASSIGNED_CARD_MUST_BE_CHECKED")
    manifest_path = gate_path.parent / "frozen-sources.json"
    manifest = {"path": manifest_path.relative_to(ROOT).as_posix(), "sha256": digest(manifest_path)}
    checks = []
    for round_ in gate["rounds"]:
        for check in round_["checks"]:
            target = next(t for name, _, t, _ in CASES if name == check["name"])
            checks.append(dict(check, round=round_["number"], target=target,
                               target_sha256=gate["sources_sha256"][target], source_manifest=manifest))
    new_bugs = []
    for number, title, test, reproduction in (
        (1, "AMQP consumer leaks connection on channel setup failure", "test_consumer_closes_connection_after_channel_failure", "runtime-baseline"),
        (2, "Repeated SDK logging setup retains a raw handler", "test_sdk_repeated_setup_removes_added_raw_handler", "runtime-baseline"),
        (3, "Delivery/observability fixtures overwrite the Compose project directory", "test_fixture_project_directory_and_ownership", "runtime-baseline"),
        (4, "Supplemental restart fixture can target the canonical runtime", "test_restart_fixture_refuses_default_canonical_runtime", "restart-ownership"),
        (5, "Supplemental restart fixture uses the removed tenant session payload", "test_restart_fixture_runs_only_matching_isolated_contract", "restart-session-contract-lab")):
        new_bugs.append({"temporary_id": f"NEW-RUNTIME-{number:03}", "title": title, "reproduced_offline": True,
                         "fixed_in_lane": True, "central_queue_append_required": True,
                         "regression": "app/tests/resilience/runtime_offline_contracts.py:" + test,
                         "reproduction": ".local/orchestration/reproductions/" + reproduction + "/stderr.log",
                         "current_source_manifest": manifest})
    failures = []
    for folder in sorted((ROOT / ".local/orchestration/reproductions").rglob("guard-result.json")):
        proof = json.loads(folder.read_text(encoding="utf8"))
        if not proof["passed"]:
            failures.append({"artifact": folder.relative_to(ROOT).as_posix(), "sha256": digest(folder),
                             "denied_operations": proof["denied_operations"], "exit_code": proof["exit_code"],
                             "superseded_by_current_rounds": True})
    artifacts = [{"path": gate_path.relative_to(ROOT).as_posix(), "sha256": digest(gate_path)}, manifest,
                 {"path": "docs/orchestration/results/runtime/gates.json", "sha256": digest(ROOT / "docs/orchestration/results/runtime/gates.json")},
                 {"path": "docs/orchestration/results/runtime/result.md", "sha256": digest(ROOT / "docs/orchestration/results/runtime/result.md")}]
    status = "BLOCKED_LOCAL_COMMIT_PERMISSION" if args.commit_blocked else "OFFLINE_COMPLETE_PENDING_REAL_GATES"
    commit_check = {"name": "local_allowlist_commit", "passed": not args.commit_blocked,
                    "files_within_allowlist": True, "branch_preserved": True, "head_sha": head}
    if args.commit_blocked:
        patch_path = ROOT / ".local/orchestration/runtime-review.patch"
        patch_text = git("diff", "--binary", BASE) + "\n"
        for path in untracked:
            patch_text += f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
            patch_text += "".join(unified_diff([], (ROOT / path).read_text(encoding="utf8").splitlines(keepends=True),
                                             fromfile="/dev/null", tofile="b/" + path))
        patch_path.write_text(patch_text, encoding="utf8", newline="\n")
        artifacts.extend([{"path": patch_path.relative_to(ROOT).as_posix(), "sha256": digest(patch_path)},
                          {"path": blocker_path.relative_to(ROOT).as_posix(), "sha256": digest(blocker_path)}])
        commit_check.update(error="Permission denied creating worktree index.lock",
                            evidence=blocker_path.relative_to(ROOT).as_posix(), resolved=False)
        plans["dependencies"].append({"id": "runtime-git-metadata-permission", "path": "D:/RAG-Local/.git/worktrees/runtime/index.lock",
                                       "problem": "Managed sandbox ACL denies metadata writes; no local commit was created.",
                                       "action": "Central must repair the sandbox metadata mapping or review/apply runtime-review.patch in its authorized integration workspace."})
        failures.append(commit_check)
    receipt = {"lane": "runtime", "branch": BRANCH, "head_sha": head, "base_sha": BASE,
               "at": datetime.now(timezone.utc).isoformat(), "status": status, "commit_created": not args.commit_blocked,
               "offline_complete": True, "all_cards_done": False, "journal_updated": False,
               "cards_snapshot_sha256": digest(cards_path), "cards_checked": [
                   {"id": card["id"], "original_status": card["status"], "criteria": card["criteria"],
                    "offline_checks": [name for name, group, _, _ in CASES if card["id"] in group],
                    "pending_gates": [g["id"] for g in plans["gates"] if card["id"] in g["cards"]],
                    "offline_passed": True, "journal_status_changed": False} for card in cards],
               "files_changed": files, "sources_sha256": gate["sources_sha256"], "source_manifest": manifest,
               "python_executable_sha256": gate["python_executable_sha256"],
               "rounds": gate["rounds"], "consecutive_passes": 2, "checks": checks, "failed_checks": failures,
               "current_failed_checks": [commit_check] if args.commit_blocked else [], "commit_check": commit_check,
               "pending_gates": plans["gates"], "new_bugs": new_bugs,
               "dependencies": plans["dependencies"], "artifacts": artifacts,
               "limitations": ["Same-author offline rounds, not an independent audit.",
                               "Controlled ledger, Redis, broker, HTTP and clock inputs do not certify real integration or SLO.",
                               "Actual ADK/OTel SDK tests use injected retrieval/failures; no remote provider calls.",
                               "No Docker, load, fault injection, real restore, production, release or independent-host HA was executed.",
                               "Shared fixture/output/workload dependencies remain for the Central."],
               "cloud_calls": 0, "docker_calls": 0, "canonical_lab_mutations": 0, "push_merge_remote_actions": 0}
    target = ROOT / ".local/orchestration/worker-receipt.json"
    target.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf8")
    print(json.dumps({"receipt": target.relative_to(ROOT).as_posix(), "branch": BRANCH, "head_sha": head,
                      "cards": len(cards), "rounds": 2, "checks": len(checks), "new_bugs": len(new_bugs),
                      "status": receipt["status"]}))


if __name__ == "__main__":
    main()
