"""Run one offline fixture with egress, processes and private lab files denied.

Existing fixture assertions run unchanged. Only their eval/runs output is moved
to the caller's private .local directory. This is not a container integration.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import threading
import traceback


ROOT = Path(__file__).resolve().parents[3]


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--target", required=True)
    cli.add_argument("--output", required=True)
    cli.add_argument("args", nargs=argparse.REMAINDER)
    args = cli.parse_args()
    target = (ROOT / args.target).resolve()
    output = Path(args.output).resolve()
    if not target.is_relative_to(ROOT / "app/tests") or not target.is_file():
        raise ValueError("WORKTREE_TEST_REQUIRED")
    if not output.is_relative_to(ROOT / ".local"):
        raise ValueError("LOCAL_OUTPUT_REQUIRED")
    output.mkdir(parents=True, exist_ok=True)
    original_mkdir, original_write = Path.mkdir, Path.write_text
    evidence = ROOT / "eval/runs"

    def relocate(path):
        path = path.resolve()
        return output / "fixtures" / path.relative_to(evidence) if path.is_relative_to(evidence) else path

    def mkdir(path, *parts, **options):
        return original_mkdir(relocate(path), *parts, **options)

    def write(path, *parts, **options):
        return original_write(relocate(path), *parts, **options)

    # No application call or test assertion is replaced by this relocation.
    Path.mkdir, Path.write_text = mkdir, write
    flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    denied = []
    denied_frames = []
    blocked_capability_probes = 0
    venv = Path(sys.prefix).resolve()
    canonical = Path("D:/RAG-Local").resolve()
    ipc = threading.local()
    ipc_pairs = 0
    original_socketpair = socket.socketpair

    def socketpair(*parts, **options):
        # Windows asyncio creates a private loopback self-pipe in the stdlib.
        # Permit that exact synchronous factory, never arbitrary local services.
        nonlocal ipc_pairs
        ipc.active = True
        try:
            pair = original_socketpair(*parts, **options)
            ipc_pairs += 1
            return pair
        finally:
            ipc.active = False

    socket.socketpair = socketpair

    def guard(event, details):
        nonlocal blocked_capability_probes
        reason = None
        if event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn"):
            reason = "OFFLINE_PROCESS_DENIED"
        elif event in ("socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo", "socket.sendto"):
            address = details[1] if len(details) > 1 else None
            caller = sys._getframe(1)
            if event == "socket.bind" and address == ("::1", 0) \
                    and caller.f_globals.get("__name__") == "urllib3.util.connection" \
                    and caller.f_code.co_name == "_has_ipv6":
                # An import-time capability probe is refused as unavailable;
                # it must neither open a port nor be mistaken for cloud egress.
                blocked_capability_probes += 1
                raise OSError("OFFLINE_IPV6_CAPABILITY_PROBE_DISABLED")
            internal = event in ("socket.connect", "socket.bind") and getattr(ipc, "active", False) \
                and isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")
            if not internal:
                reason = "OFFLINE_NETWORK_DENIED"
        elif event == "open" and not isinstance(details[0], int):
            path = Path(os.fsdecode(details[0])).resolve()
            if path.name == ".env" or path.name.startswith(".env.") or "secrets" in path.parts:
                reason = "PRIVATE_INPUT_DENIED"
            elif path.is_relative_to(canonical) and not path.is_relative_to(venv):
                reason = "CANONICAL_LAB_DENIED"
            else:
                mode, open_flags = details[1:3]
                writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (open_flags & flags)
                if writing and not path.is_relative_to(ROOT / ".local"):
                    reason = "NONLOCAL_WRITE_DENIED"
        elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink", "os.chmod", "os.truncate"):
            paths = details[:2] if event in ("os.rename", "os.link", "os.symlink") else details[:1]
            if any(not isinstance(path, int) and not Path(os.fsdecode(path)).resolve().is_relative_to(ROOT / ".local")
                   for path in paths):
                reason = "NONLOCAL_WRITE_DENIED"
        if reason:
            denied.append(reason)
            denied_frames.append({"event": event, "reason": reason,
                                  "frames": [{"file": Path(frame.filename).name, "line": frame.lineno,
                                              "function": frame.name} for frame in traceback.extract_stack()[-10:-1]]})
            raise PermissionError(reason)

    sys.addaudithook(guard)
    sys.argv = [str(target)] + (args.args[1:] if args.args[:1] == ["--"] else args.args)
    stdout, stderr = io.StringIO(), io.StringIO()
    result = {"target": args.target, "passed": False, "denied_operations": denied,
              "external_network_calls": 0, "real_docker_calls": 0, "canonical_lab_access": False}
    exit_code = 0
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            runpy.run_path(str(target), run_name="__main__")
    except SystemExit as error:
        exit_code = error.code if isinstance(error.code, int) else 1
    except BaseException as error:
        exit_code = 1
        result["error_type"] = type(error).__name__
        result["frames"] = [{"file": Path(frame.filename).name, "line": frame.lineno,
                             "function": frame.name} for frame in traceback.extract_tb(error.__traceback__)]
    finally:
        # Fixture output contains only synthetic inputs; raw SDK diagnostics stay
        # private. No exception message from application code is serialized here.
        (output / "stdout.log").write_text(stdout.getvalue(), encoding="utf8")
        (output / "stderr.log").write_text(stderr.getvalue(), encoding="utf8")
        result["passed"] = exit_code == 0 and not denied
        result["exit_code"] = exit_code
        result["asyncio_internal_ipc_pairs"] = ipc_pairs
        result["denied_frames"] = denied_frames
        result["blocked_ipv6_capability_probes"] = blocked_capability_probes
        imported = {name: Path(module.__file__).resolve() for name, module in list(sys.modules.items())
                    if name.startswith("rag_app") and getattr(module, "__file__", None)}
        result["worktree_imports_only"] = all(path.is_relative_to(ROOT / "app/src") for path in imported.values())
        result["rag_app_imports"] = {name: path.relative_to(ROOT).as_posix() for name, path in imported.items()
                                     if path.is_relative_to(ROOT)}
        result["passed"] = result["passed"] and result["worktree_imports_only"]
        receipts = sorted((output / "fixtures").rglob("*receipt.json"))
        result["receipts"] = [str(path.relative_to(ROOT)) for path in receipts]
        (output / "guard-result.json").write_text(json.dumps(result, indent=2), encoding="utf8")
    print(json.dumps({key: result[key] for key in ("target", "passed", "exit_code", "denied_operations",
                                                  "asyncio_internal_ipc_pairs", "worktree_imports_only", "receipts")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
