"""Frozen, two-round offline gate for the supervisor; no Codex/model invocation."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
SOURCES = [
    ".gitignore",
    "app/tools/orchestration/repository_safety.py",
    "app/tools/orchestration/parallel_runner.py",
    "app/tools/orchestration/test_parallel_runner.py",
    "app/tools/orchestration/supervisor_gate.py",
    "docs/orchestration/workstreams.json",
    "docs/orchestration/results/central/supervisor-recovery.md",
]


def frozen():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def gate(card, run_id):
    if not re.fullmatch(r"BUG-\d+", card) or not re.fullmatch(r"[a-z0-9-]+", run_id):
        raise ValueError("INVALID_GATE_ID")
    state = ROOT / ".local/orchestration" / run_id
    state.mkdir()  # never overwrite a failed/older round
    sources = frozen()
    rounds, streak = [], 0
    command = [sys.executable, "-B", "-m", "unittest", "discover", "-s",
               "app/tools/orchestration", "-p", "test_parallel_runner.py", "-v"]
    for number in (1, 2):
        if frozen() != sources:
            raise RuntimeError("SOURCE_CHANGED_RESET_STREAK")
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), shell=False)
        output = result.stdout + result.stderr
        log = state / f"round-{number}.log"
        log.write_bytes(output)
        decoded = output.decode("utf8", errors="replace")
        matched = re.search(r"Ran (\d+) tests", decoded)
        passed = (result.returncode == 0 and frozen() == sources and matched is not None
                  and "skipped=" not in decoded and "ResourceWarning" not in decoded)
        streak = streak + 1 if passed else 0
        rounds.append({"round": number, "passed": passed, "exit_code": result.returncode,
                       "tests": int(matched.group(1)) if matched else None,
                       "log": str(log), "sha256": hashlib.sha256(output).hexdigest()})
        if not passed:
            break
    receipt = {"card_id": card, "evidence_type": "verified_regression",
               "at": datetime.now(timezone.utc).isoformat(), "complete": streak == 2,
               "consecutive_passes": streak, "criteria_passed":
               ["reproduction", "two_regression_rounds"] if streak == 2 else [],
               "sources_sha256": sources, "rounds": rounds,
               "scope": "Windows supervisor only; controlled subprocesses, no provider/model/cloud",
               "limitations": ["Same-author adversarial regression, not an independent blind audit",
                               "Does not approve product cards, production HA or a merge"]}
    receipt_path = ROOT / "eval/reports/orchestration" / (run_id + ".json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if receipt_path.exists():
        raise RuntimeError("RECEIPT_ALREADY_EXISTS")
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"card": card, "passed": streak == 2, "rounds": len(rounds),
                      "tests": [row["tests"] for row in rounds], "receipt": str(receipt_path)}))
    return 0 if streak == 2 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--card", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    raise SystemExit(gate(args.card, args.run_id))
