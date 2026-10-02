"""Repackage unchanged challenge sources with newly verified evidence, without Git writes."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path("D:/RAG-Local")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proof", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    proof = Path(args.proof).resolve()
    output = Path(args.output).resolve()
    if not proof.is_relative_to(ROOT / "eval/runs") or not output.is_relative_to(ROOT / "deliveries"):
        raise ValueError("INVALID_DELIVERY_PATH")
    if output.exists():
        raise ValueError("PRESERVE_PREVIOUS_DELIVERY")
    report = json.loads(proof.read_text(encoding="utf-8-sig"))
    if report.get("complete") is not True or report.get("consecutive_passes") != 2:
        raise ValueError("FULL_NEW_PROOF_REQUIRED")
    for name, digest in report["sources_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("SOURCE_CHANGED_RESET_STREAK")
    source = ROOT / "carrefour-challenge"
    excluded = {"__pycache__", ".pytest_cache", ".local", "evidence", ".git"}
    paths = sorted(p for p in source.rglob("*") if p.is_file()
                   and not excluded.intersection(p.relative_to(source).parts))
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, path.relative_to(ROOT).as_posix())
        for path in sorted(proof.parent.rglob("*")):
            if path.is_file():
                archive.write(path, "evidence/" + path.relative_to(proof.parent).as_posix())
        for name in ("cards.json", "execution-status.md"):
            path = Path(__file__).parent / name
            archive.write(path, "planning/" + name)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("INVALID_ZIP")
        expected = ("README.md", "docker-compose.yml", ".dockerignore",
                    "examples/agent.json", "examples/request.png", "examples/agent.schema.json",
                    "data/exams.json", "requirements.lock", "tests/test_integration.py")
        for name in expected:
            if "carrefour-challenge/" + name not in archive.namelist():
                raise ValueError("MISSING_DELIVERY_FILE")
        entries = len(archive.namelist())
    print(json.dumps({"output": str(output), "bytes": output.stat().st_size,
                      "entries": entries, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
