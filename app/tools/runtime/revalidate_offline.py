"""Fresh runtime-lane evidence without Docker, providers or journal mutations."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
CARDS = Path("D:/RAG-Local/.local/orchestration/parallel-20261002-v1/runtime/cards.json")
CASES = (
    ("sdk-parentage", ["BUG-018"], "app/tests/observability/tracing_sdk_fixture.py", []),
    ("public-metrics-route", ["BUG-019"], "app/tests/observability/telemetry_route_fixture.py", []),
    ("profile-image-ownership", ["BUG-021"], "app/tests/security/profile_image_fixture.py", []),
    ("multiline-delivery-helper", ["BUG-025"], "app/tests/shared/helper_regression.py", ["--kind", "delivery"]),
    ("qa-seed-no-docker", ["BUG-027", "BUG-088"], "app/tests/observability/observability_retry_regression.py", []),
    ("sdk-span-status", ["BUG-028"], "app/tests/observability/tracing_status_fixture.py", []),
    ("sdk-log-redaction", ["BUG-029", "BUG-030"], "app/tests/security/sdk_logging_fixture.py", []),
    ("collector-port-controls", ["BUG-031"], "app/tests/observability/collector_ports_regression.py", []),
    ("runtime-contracts", ["RAG-09", "RAG-10", "RAG-12", "RAG-13", "BUG-032", "BUG-110", "BUG-111",
                           "BUG-112", "BUG-113", "BUG-114", "BUG-115", "BUG-116", "BUG-121", "BUG-124"],
     "app/tests/resilience/runtime_offline_contracts.py", []),
)


def source_hashes():
    bases = ("app/src", "app/tests", "app/infrastructure", "app/monitoring", "app/tools/runtime",
             "docs/architecture/contracts", "docs/quality/reliability", "docs/orchestration/results/runtime")
    paths = {ROOT / "app/workspace.py", ROOT / "AGENTS.md", ROOT / "docs/orchestration/acceptance.md",
             ROOT / "docs/orchestration/workstreams.json"}
    for base in bases:
        paths.update(path for path in (ROOT / base).rglob("*")
                     if path.is_file() and not {".local", "__pycache__"}.intersection(path.parts)
                     and path.suffix in (".py", ".sql", ".json", ".md", ".yaml", ".yml", ".lock", ".in", ".ps1"))
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--output", required=True)
    cli.add_argument("--rounds", type=int, choices=(1, 2), default=2)
    args = cli.parse_args()
    folder = Path(args.output).resolve()
    if not folder.is_relative_to(ROOT / ".local") or folder.exists():
        raise ValueError("NEW_LOCAL_OUTPUT_REQUIRED")
    folder.mkdir(parents=True)
    sources = source_hashes()
    receipt = {"lane": "runtime", "at": datetime.now(timezone.utc).isoformat(), "sources_sha256": sources,
               "scope": "offline_current_worktree_only", "independent_audit": False,
               "real_docker_calls": 0, "remote_calls": 0, "rounds": [], "consecutive_passes": 0, "complete": False}
    receipt["python_executable_sha256"] = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    cards_bytes = CARDS.read_bytes()
    receipt["cards_snapshot_sha256"] = hashlib.sha256(cards_bytes).hexdigest()
    (folder / "assigned-cards.json").write_bytes(cards_bytes)
    (folder / "frozen-sources.json").write_text(json.dumps(sources, indent=2), encoding="utf8")
    # No provider credentials or user configuration are inherited by fixtures.
    private_home = folder / "private-home"
    private_home.mkdir()
    env = {"SystemRoot": os.environ.get("SystemRoot", "C:/Windows"), "USERPROFILE": str(private_home),
           "APPDATA": str(private_home), "LOCALAPPDATA": str(private_home), "TEMP": str(private_home),
           "TMP": str(private_home), "RAG_MODE": "lab", "RAG_RESILIENCE": "disabled", "RAG_DELIVERY": "disabled",
           "RAG_OBSERVABILITY": "disabled", "RAG_RETRIEVAL": "disabled", "RAG_GEMINI_RESPONSES": "disabled",
           "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    for number in range(1, args.rounds + 1):
        if source_hashes() != sources or hashlib.sha256(CARDS.read_bytes()).hexdigest() != receipt["cards_snapshot_sha256"]:
            receipt["consecutive_passes"] = 0
            receipt["error"] = "SOURCE_OR_CRITERIA_CHANGED_RESET_CLEAN_COUNT"
            (folder / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
            raise RuntimeError("SOURCE_CHANGED_RESET_CLEAN_COUNT")
        checks = []
        for name, cards, target, extra in CASES:
            output = folder / ("round-" + str(number)) / name
            command = [sys.executable, "-I", "-B", str(ROOT / "app/tools/runtime/offline_guard.py"),
                       "--target", target, "--output", str(output), "--", *extra]
            started = time.monotonic()
            try:
                result = subprocess.run(command, cwd=ROOT, env=env, shell=False, capture_output=True,
                                        encoding="utf8", timeout=120,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                proof = json.loads((output / "guard-result.json").read_text(encoding="utf8"))
                fixture_receipts = [json.loads((ROOT / path).read_text(encoding="utf8")) for path in proof["receipts"]]
                expected_receipts = name != "runtime-contracts"
                receipts_valid = (not expected_receipts or bool(fixture_receipts)) and all(
                    item["complete"] is True and item["consecutive_passes"] == 2 and
                    all(sources.get(path.replace("\\", "/")) == digest for path, digest in item["sources_sha256"].items())
                    for item in fixture_receipts)
                passed = result.returncode == 0 and proof["passed"] and not proof["denied_operations"] and receipts_valid
                summary = re.search(r"Ran (\d+) tests", (output / "stderr.log").read_text(encoding="utf8"))
                details = {"guard": str((output / "guard-result.json").relative_to(ROOT)),
                           "receipts": proof["receipts"], "denied_operations": proof["denied_operations"],
                           "receipts_match_frozen_sources": receipts_valid, "worktree_imports_only": proof["worktree_imports_only"]}
                if summary:
                    details["unittest_cases"] = int(summary.group(1))
            except Exception as error:
                passed = False
                output.mkdir(parents=True, exist_ok=True)
                (output / "runner-error.json").write_text(json.dumps({"error_type": type(error).__name__}), encoding="utf8")
                details = {"runner_error": str((output / "runner-error.json").relative_to(ROOT))}
            checks.append({"name": name, "cards": cards, "passed": passed,
                           "seconds": round(time.monotonic() - started, 3), "details": details})
            (folder / "progress.json").write_text(json.dumps({"round": number, "checks": checks}, indent=2), encoding="utf8")
            print(json.dumps({"round": number, "check": name, "passed": passed}), flush=True)
        unchanged = source_hashes() == sources and hashlib.sha256(CARDS.read_bytes()).hexdigest() == receipt["cards_snapshot_sha256"]
        passed = unchanged and all(check["passed"] for check in checks)
        receipt["rounds"].append({"number": number, "checks": checks, "sources_unchanged": unchanged, "passed": passed})
        receipt["consecutive_passes"] = receipt["consecutive_passes"] + 1 if passed else 0
        (folder / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    receipt["complete"] = receipt["consecutive_passes"] == 2 and source_hashes() == sources
    (folder / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    print(json.dumps({"receipt": str((folder / "receipt.json").relative_to(ROOT)), "complete": receipt["complete"],
                      "consecutive_passes": receipt["consecutive_passes"]}), flush=True)
    return 0 if all(round_["passed"] for round_ in receipt["rounds"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
