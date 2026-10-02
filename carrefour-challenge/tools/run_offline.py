"""Run scoped stdlib oracles with source fingerprints and a hard network fence."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]


def fingerprint():
    excluded = {'.local', '__pycache__', '.pytest_cache', 'node_modules', 'evidence', 'generated'}
    results = PROJECT.parent / 'docs/orchestration/results/carrefour'
    paths = [p for root in (PROJECT, results) for p in root.rglob('*') if p.is_file()]
    paths += [PROJECT.parent / name for name in ('AGENTS.md', 'docs/orchestration/acceptance.md',
              'docs/orchestration/workstreams.json', 'docs/challenges/carrefour/cards.json')]
    return {p.relative_to(PROJECT.parent).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths) if not (set(p.relative_to(PROJECT.parent).parts) & excluded) and p.name != '.env'}


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite): yield from flatten(test)
        else: yield test


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--seed', type=int, default=126021)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    allowed = PROJECT.parent / '.local'
    if not output.is_relative_to(allowed.resolve()): raise SystemExit('OUTPUT_MUST_BE_WORKTREE_LOCAL')
    output.mkdir(parents=True, exist_ok=False)
    os.environ['CF_SEED'] = str(args.seed)
    os.environ['OTEL_SDK_DISABLED'] = 'true'
    os.environ['ADK_SUPPRESS_EXPERIMENTAL_WARNINGS'] = 'true'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(PROJECT / 'src'))
    sys.path.insert(0, str(PROJECT / 'tests'))
    before = fingerprint()
    (output / 'sources.json').write_text(json.dumps(before, sort_keys=True, indent=2), encoding='utf8')
    started = datetime.now(timezone.utc).isoformat()
    tests = list(flatten(unittest.defaultTestLoader.loadTestsFromName('test_offline_lane')))
    random.Random(args.seed).shuffle(tests)
    with (output / 'tests.log').open('w', encoding='utf8') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.TestSuite(tests))
    after = fingerprint()
    frozen = before == after
    report = {
        'scope': 'same-author offline boundary tests; not original pytest/full E2E or independent audit',
        'seed': args.seed, 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'python': sys.version, 'source_root': str(PROJECT), 'sources_sha256': before,
        'sources_manifest_sha256': hashlib.sha256((output / 'sources.json').read_bytes()).hexdigest(),
        'sources_unchanged': frozen, 'tests': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'skipped': len(result.skipped),
        'failed_tests': [test.id() for test, _ in result.failures + result.errors],
        'passed': result.wasSuccessful() and frozen and not result.skipped,
        'test_log_sha256': hashlib.sha256((output / 'tests.log').read_bytes()).hexdigest(),
        'dependencies': {name: importlib.metadata.version(name) for name in ('google-adk', 'mcp', 'fastapi', 'httpx2', 'pydantic')},
        'unavailable': [name for name in ('pytest', 'PIL') if importlib.util.find_spec(name) is None],
        'network_policy': 'external socket.connect and create_connection forbidden; loopback socketpair for Windows asyncio only',
        'adapters': ['catalog default selects identical worktree fixture', 'Swagger mount uses empty temporary directory; assets unproved',
                     'Windows regular-file flags and serial atomic writes only; POSIX FIFO/symlink/devices/concurrent rename unproved'],
        'pending_gates': ['original pytest suite with frozen container dependencies', 'real Tesseract and MCP SSE',
                          'Docker build/fresh environment/readiness/isolation/faults', 'actual Swagger assets and Playwright desktop/mobile',
                          'original full two-round CF-14 gate'],
    }
    (output / 'receipt.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps({k: report[k] for k in ('tests', 'failures', 'errors', 'skipped', 'passed', 'sources_unchanged')}))
    print(json.dumps({'receipt': str(output / 'receipt.json'), 'failed_tests': report['failed_tests']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
