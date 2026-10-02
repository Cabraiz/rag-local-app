"""Private immutable handoffs and exact source bindings (no secrets discovery)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def git(worktree: str | Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(worktree), *args],
                                   text=True, encoding="utf8", errors="strict",
                                   creationflags=NO_WINDOW, timeout=30).strip()


def source_manifest(worktree: str | Path) -> dict:
    root = Path(worktree).resolve()
    names = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    files = {}
    checked_paths = set()

    def private(parts):
        return any(part.casefold() in (".local", ".git") or
                   part.casefold().startswith(".env") for part in parts)

    for name in sorted(set(names.split("\0")) - {""}):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("UNSAFE_SOURCE_PATH")
        if private(relative.parts):
            raise ValueError("PRIVATE_PATH_IN_SOURCE_MANIFEST")
        path = root / relative
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("UNSAFE_SOURCE_PATH")
        if private(resolved.relative_to(root).parts):
            raise ValueError("PRIVATE_PATH_IN_SOURCE_MANIFEST")
        # A regular leaf can still sit below a symlink or Windows junction.
        # Cache shared ancestors; validate every entry before reading its bytes.
        candidate = path
        while candidate != root:
            if candidate not in checked_paths:
                if (candidate.is_symlink() or
                        getattr(candidate, "is_junction", lambda: False)()):
                    raise ValueError("UNSAFE_SOURCE_PATH")
                checked_paths.add(candidate)
            candidate = candidate.parent
        files[name] = sha(path) if path.is_file() else None
    return {"head": git(root, "rev-parse", "HEAD"),
            "branch": git(root, "branch", "--show-current"), "files": files}


def freeze(directory: Path, worktree: Path, binding: dict, receipt: Path,
           *, expected: dict | None = None) -> dict:
    """Exclusive snapshot. Partial/crashed snapshots require explicit review."""
    directory = Path(directory)
    if directory.exists():
        saved = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
        verify_frozen(directory, saved["snapshot_sha256"])
        if saved["binding"] != binding or sha(receipt) != saved["receipt_sha256"]:
            raise ValueError("FROZEN_HANDOFF_IDENTITY_CHANGED")
        if expected is not None and saved["sources"] != expected:
            raise ValueError("READ_ONLY_SOURCE_CHANGED")
        return saved
    sources = source_manifest(worktree)
    if expected is not None and sources != expected:
        raise ValueError("READ_ONLY_SOURCE_CHANGED")
    directory.mkdir(parents=True, exist_ok=False)
    for name, checksum in sources["files"].items():
        if checksum is None:
            continue
        destination = directory / "code" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(worktree) / name, destination)
        if sha(destination) != checksum:
            raise ValueError("SOURCE_CHANGED_DURING_FREEZE")
    shutil.copyfile(receipt, directory / "receipt.json")
    if source_manifest(worktree) != sources:
        raise ValueError("SOURCE_CHANGED_DURING_FREEZE")
    manifest = {"binding": binding, "sources": sources,
                "receipt_sha256": sha(directory / "receipt.json")}
    manifest["snapshot_sha256"] = digest(manifest)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    verify_frozen(directory, manifest["snapshot_sha256"])
    return manifest


def verify_frozen(directory: Path, expected_sha: str) -> dict:
    value = json.loads((directory / "manifest.json").read_text(encoding="utf8"))
    data = {key: item for key, item in value.items() if key != "snapshot_sha256"}
    if value.get("snapshot_sha256") != expected_sha or digest(data) != expected_sha:
        raise ValueError("FROZEN_MANIFEST_CHANGED")
    if sha(directory / "receipt.json") != value["receipt_sha256"]:
        raise ValueError("FROZEN_RECEIPT_CHANGED")
    expected_files = {name for name, checksum in value["sources"]["files"].items()
                      if checksum is not None}
    actual_files = {p.relative_to(directory / "code").as_posix()
                    for p in (directory / "code").rglob("*") if p.is_file()}
    if actual_files != expected_files:
        raise ValueError("FROZEN_FILE_SET_CHANGED")
    for name in expected_files:
        if sha(directory / "code" / name) != value["sources"]["files"][name]:
            raise ValueError("FROZEN_SOURCE_CHANGED")
    return value
