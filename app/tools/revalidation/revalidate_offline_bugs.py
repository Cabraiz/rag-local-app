# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""One writer, fresh receipts for narrowly scoped offline regressions only."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import card_queue as queue

ROOT = _workspace_root
CASES = (
    ("BUG-018", "tracing_sdk_fixture.py", []),
    ("BUG-019", "telemetry_route_fixture.py", []),
    ("BUG-021", "profile_image_fixture.py", []),
    ("BUG-025", "helper_regression.py", ["--kind", "delivery"]),
    ("BUG-027", "observability_retry_regression.py", []),
    ("BUG-028", "tracing_status_fixture.py", []),
    ("BUG-031", "collector_ports_regression.py", []),
    ("BUG-038", "neural_validation_fixture.py", []),
    ("BUG-057", "grounding_recall_fixture.py", []),
)


def main():
    folder = ROOT / "eval/runs" / ("offline-bugs-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    for card, filename, extra in CASES:
        with closing(queue.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM cards WHERE id=?", (card,)).fetchone()
            if row["status"] == "DONE":
                continue
            queue.resume(db, card, "User requested revalidation; current-source offline regression, not parent integration gate")
        log = folder / (card + ".log")
        try:
            result = subprocess.run([sys.executable, str(_named_file(ROOT / 'app/tests', filename)), *extra],
                                    cwd=ROOT, capture_output=True, encoding="utf8", timeout=120,
                                    shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            log.write_text(result.stdout + "\n" + result.stderr, encoding="utf8")
            if result.returncode:
                raise ValueError("REGRESSION_PROCESS_FAILED")
            payloads = []
            for line in result.stdout.splitlines():
                try:
                    payloads.append(json.loads(line))
                except ValueError:
                    pass
            proof = Path(payloads[-1]["receipt"])
            report = json.loads(proof.read_text(encoding="utf-8-sig"))
            if report["card_id"] != card or report["complete"] is not True:
                raise ValueError("SCOPED_REGRESSION_FAILED")
            with closing(queue.connect()) as db, db:
                queue.complete(db, card, proof)
                queue.projection(db)
            results.append({"card": card, "passed": True, "receipt": str(proof)})
            print(json.dumps(results[-1]), flush=True)
        except Exception as error:
            if not log.exists():
                log.write_text(type(error).__name__ + ": local regression did not complete", encoding="utf8")
            with closing(queue.connect()) as db, db:
                queue.block(db, card, type(error).__name__ + ": " + str(log.relative_to(ROOT)))
                queue.projection(db)
            results.append({"card": card, "passed": False, "log": str(log)})
            print(json.dumps(results[-1]), flush=True)
            break
    (folder / "receipt.json").write_text(json.dumps({"results": results, "parent_cards_completed": False}, indent=2), encoding="utf8")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
