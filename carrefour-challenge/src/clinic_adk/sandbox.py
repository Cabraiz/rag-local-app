"""Fail-closed, offline ADK preflight. Accepts a DSL spec, never caller Python/paths.

POSIX resource limits and audit hooks are defense in depth for exact emitter output,
not a claim that CPython safely executes arbitrary code. Run in the hardened Docker
container; network=none additionally supplies an OS boundary in the proof harness.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .compiler import emit, parse_spec
from .errors import SafeError

PREFLIGHT_TIMEOUT = 25


def check_generated(spec):
    """Construct and execute the generated ADK graph in a fresh bounded child."""
    if os.name != 'posix':
        raise SafeError('SANDBOX_POSIX_REQUIRED')
    # Revalidate before starting any child; serialization is bounded by the DSL.
    source = emit(spec)
    raw = json.dumps(parse_spec(json.dumps(spec.model_dump()).encode()).model_dump()).encode()
    worker = Path(__file__).with_name('sandbox_worker.py').resolve()
    with tempfile.TemporaryDirectory(prefix='cf-adk-') as directory:
        environment = {'PATH': os.defpath, 'HOME': directory, 'TMPDIR': directory,
                       'OTEL_SDK_DISABLED': 'true', 'ADK_SUPPRESS_EXPERIMENTAL_WARNINGS': 'true',
                       'PYTHONDONTWRITEBYTECODE': '1'}
        try:
            result = subprocess.run([sys.executable, '-I', '-B', str(worker)],
                                    input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    cwd=directory, env=environment, timeout=PREFLIGHT_TIMEOUT,
                                    shell=False, check=False)
        except subprocess.TimeoutExpired:
            raise SafeError('SANDBOX_TIMEOUT') from None
        except OSError:
            raise SafeError('SANDBOX_START_FAILED') from None
    if result.returncode or len(result.stdout) > 4096:
        raise SafeError('SANDBOX_FAILED')
    try:
        report = json.loads(result.stdout)
        import hashlib
        if (report['source_sha256'] != hashlib.sha256(source.encode()).hexdigest()
                or report['stages'] != ['ocr', 'retrieve', 'validate', 'schedule', 'format']
                or report['self_checks'] != {'network': 2, 'filesystem': 2, 'process': 1}
                or report['denied'] != {'network': 0, 'filesystem': 0, 'process': 0}
                or report['sandbox'] != 'posix-offline-adk-preflight'):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise SafeError('SANDBOX_INVALID_REPORT') from None
    return report
