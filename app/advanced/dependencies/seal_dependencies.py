"""Seal only dependency bugs; SDK installation never closes parent integration cards."""
import argparse
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'app/workspace.py').is_file())
sys.path.insert(0, str(ROOT / 'app'))
from workspace import bootstrap
bootstrap()
spec = importlib.util.spec_from_file_location("queue_control", ROOT / "app/tools/cards/card_queue.py")
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--proof", required=True)
    args = cli.parse_args()
    path = Path(args.proof).resolve()
    if not path.is_relative_to(ROOT / "eval"):
        raise ValueError("PROOF_OUTSIDE_EVAL")
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    if report.get("complete") is not True or report.get("consecutive_passes") != 2:
        raise ValueError("TWO_ROUNDS_REQUIRED")
    if len(report.get("rounds", [])) != 2 or any(
            not r["sdk"]["passed"] or r["sdk"]["model_calls"] != 0
            or r["compiler_exit_code"] != 0 for r in report["rounds"]):
        raise ValueError("REAL_CHECKS_REQUIRED")
    completed = []
    for card in ("BUG-062", "BUG-063", "BUG-087"):
        with closing(queue.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM cards WHERE id=?", (card,)).fetchone()
            if row["status"] == "DONE":
                raise ValueError("DO_NOT_REWRITE_APPROVAL")
            if row["status"] == "QUEUED":
                active = db.execute("SELECT id FROM cards WHERE status='RUNNING'").fetchone()
                if active:
                    raise ValueError("OTHER_WRITER_ACTIVE")
                db.execute("UPDATE cards SET status='RUNNING',reason=NULL WHERE id=?", (card,))
                queue.event(db, card, "START", "Current user requested all queued/blocked cards; offline dependency proof")
            elif row["status"] in ("BLOCKED", "NEEDS_FIX"):
                queue.resume(db, card, "Dependency regression passed twice with frozen sources")
            receipt = {**report, "card_id": card, "evidence_type": row["evidence_type"],
                       "criteria_passed": json.loads(row["criteria"]),
                       "reproduction": row["reason"] if row["reason"] else
                       "eval/advanced-lock-20261001.log: pip-tools import stdlib_pkgs failed"}
            target = path.parent / (card + ".json")
            target.write_text(json.dumps(receipt, indent=2), encoding="utf8")
            queue.complete(db, card, target)
            completed.append(card)
    with closing(queue.connect()) as db:
        status = queue.projection(db)
    print(json.dumps({"completed": completed, "journal": status,
                      "parent_cards_completed": False}))


if __name__ == "__main__":
    main()
