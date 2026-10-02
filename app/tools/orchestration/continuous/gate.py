"""Two frozen local rounds and private receipts; no provider calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from continuous.evidence import digest, sha, NO_WINDOW


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = list(Path(__file__).parent.rglob("*.py")) + [Path(__file__).parent.parent / "parallel_runner.py",
                 Path(__file__).parent.parent / "test_parallel_runner.py"]
    root = Path(__file__).resolve().parents[4]
    sources += [Path(__file__).parent / "install.ps1",
                root / "docs/orchestration/continuous-acceptance.md",
                root / "docs/orchestration/continuous-operation.md"]
    fingerprints = {p.relative_to(root).as_posix(): sha(p) for p in sources}
    args.output.mkdir(parents=True, exist_ok=False)
    rounds = []
    for number in (1, 2):
        checks = []
        for name, directory, pattern in (("continuous", Path(__file__).parent / "tests", "test_*.py"),
                                         ("supervisor", Path(__file__).parent.parent, "test_parallel_runner.py")):
            path = args.output / f"round-{number}-{name}.log"
            with path.open("wb") as stream:
                process = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(directory), "-p", pattern, "-v"],
                                         stdout=stream, stderr=subprocess.STDOUT, creationflags=NO_WINDOW, timeout=180)
            checks.append({"suite": name, "returncode": process.returncode, "log": str(path), "sha256": sha(path)})
        unchanged = fingerprints == {p.relative_to(root).as_posix(): sha(p) for p in sources}
        rounds.append({"round": number, "checks": checks, "frozen": unchanged,
                       "passed": unchanged and all(c["returncode"] == 0 for c in checks)})
        if not rounds[-1]["passed"]:
            break
    receipt = {"at": datetime.now(timezone.utc).isoformat(), "sources": fingerprints,
               "source_digest": digest(fingerprints), "rounds": rounds,
               "passed": len(rounds) == 2 and all(r["passed"] for r in rounds),
               "scope": "isolated deterministic queues and supervisor regressions; not live dispatch or independent blind audit"}
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf8")
    print(json.dumps({"passed": receipt["passed"], "rounds": len(rounds), "receipt": str(args.output / "receipt.json")}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
