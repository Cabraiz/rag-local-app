"""Repeat available offline contracts with frozen sources and local-only artifacts.

This runner does not approve online cards, optional SDKs, Docker, or billing.
DeepAgents/DeepEval are not installed or impersonated; only exact Budget/invoke
definitions are extracted for controlled retry tests.
"""
import argparse
import ast
import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import types
from unittest.mock import patch

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'app/workspace.py').is_file())
OUT = ROOT / '.local/orchestration'
sys.path.insert(0, str(ROOT / 'app'))
from workspace import bootstrap
bootstrap()
sys.dont_write_bytecode = True
PAIR_STATE = threading.local()
ORIGINAL_SOCKETPAIR = socket.socketpair
CASES = ('gemini_controls', 'gemini_grounded_checks', 'gemini_sdk_contract', 'github_controls',
         'atlassian_controls', 'feed_checks', 'mcp_evidence_checks', 'mcp_gate_failure_checks',
         'retry_contract_extracted', 'additional_controls')
OPTIONAL = ('deepagents', 'deepeval', 'langchain-core', 'langgraph-checkpoint-sqlite', 'pip-tools', 'setuptools')


def internal_socketpair(*args, **kwargs):
    # Windows asyncio creates a private wakeup pair through a stdlib localhost
    # connect. Permit only that operation; provider and service sockets stay denied.
    PAIR_STATE.active = True
    try:
        return ORIGINAL_SOCKETPAIR(*args, **kwargs)
    finally:
        PAIR_STATE.active = False


def guard(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo') and getattr(PAIR_STATE, 'active', False):
        return
    if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'):
        raise RuntimeError('OFFLINE_EXTERNAL_EFFECT_DENIED')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path.name.startswith('.env') or '/run/secrets/' in path.as_posix() or path.name.endswith('_token'):
            raise RuntimeError('OFFLINE_PRIVATE_FILE_DENIED')
        if isinstance(args[2], int) and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            if not path.is_relative_to(OUT):
                raise RuntimeError('OFFLINE_WRITE_OUTSIDE_LOCAL')
    if event == 'sqlite3.connect' and args[0] != ':memory:':
        if not Path(args[0]).resolve().is_relative_to(OUT):
            raise RuntimeError('OFFLINE_DATABASE_OUTSIDE_LOCAL')


def extracted_model():
    path = ROOT / 'app/advanced/models/free_model.py'
    tree = ast.parse(path.read_text(encoding='utf8'), filename=str(path))
    nodes = [n for n in tree.body if not
             (isinstance(n, ast.ImportFrom) and n.module and n.module.startswith('langchain_core'))
             and not (isinstance(n, ast.ClassDef) and n.name == 'FreeChatModel')]
    module = types.ModuleType('free_model')
    module.__file__ = str(path)
    sys.modules['free_model'] = module
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), module.__dict__)
    return module


def sources():
    paths = list((ROOT / 'app').rglob('*.py')) + list((ROOT / 'app/src').rglob('*.sql'))
    paths += list((ROOT / 'app/advanced').rglob('*.json'))
    for folder in ('app/advanced/dependencies', 'app/advanced/containers'):
        paths += [p for p in (ROOT / folder).iterdir() if p.is_file()]
    paths += list((ROOT / 'app/infrastructure/compose/labs').glob('*.yaml'))
    paths += list((ROOT / 'docs/orchestration').rglob('*.md'))
    paths += [ROOT / 'AGENTS.md', ROOT / 'docs/orchestration/workstreams.json']
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(paths)) if p.is_file() and '.local' not in p.parts and '__pycache__' not in p.parts}


def execute(case):
    if case == 'gemini_controls':
        module = importlib.import_module(case)
        with tempfile.TemporaryDirectory(dir=OUT / 'temp') as temporary:
            fake = Path(temporary)
            (fake / 'tmp').mkdir()
            with patch.object(module, '_workspace_root', fake):
                return module.round_checks(84913)
    if case == 'gemini_grounded_checks':
        return asyncio.run(importlib.import_module(case).run())
    if case == 'gemini_sdk_contract':
        return importlib.import_module(case).run_round(93572)
    if case == 'github_controls':
        return asyncio.run(importlib.import_module(case).run_round(470119))
    if case == 'atlassian_controls':
        return asyncio.run(importlib.import_module(case).round_checks(620771))
    if case in ('feed_checks', 'mcp_evidence_checks', 'mcp_gate_failure_checks'):
        module = importlib.import_module(case)
        value = module.run()
        if asyncio.iscoroutine(value):
            value = asyncio.run(value)
        return {'passed': True, 'checks': value if value is not None else module.count}
    if case == 'retry_contract_extracted':
        extracted_model()
        try:
            importlib.import_module('retry_checks').main()
        except SystemExit as error:
            if error.code:
                raise
        return {'passed': True, 'boundary': 'exact Budget/invoke definitions; optional SDKs not imported'}
    if case == 'additional_controls':
        return importlib.import_module('providers_offline_checks').run(OUT, extracted_model)
    raise ValueError('UNKNOWN_OFFLINE_CASE')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', required=True)
    parser.add_argument('--case', choices=CASES)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.phase):
        raise ValueError('LOCAL_PHASE_NAME_REQUIRED')
    if args.case:
        socket.socketpair = internal_socketpair
        sys.addaudithook(guard)
        result = execute(args.case)
        passed = not isinstance(result, dict) or result.get('passed', True) is True
        print(json.dumps({'passed': passed, 'result': result}, default=str))
        raise SystemExit(0 if passed else 1)
    folder = OUT / args.phase
    folder.mkdir(parents=True, exist_ok=False)
    (OUT / 'temp').mkdir(exist_ok=True)
    frozen = sources()
    (folder / 'sources.json').write_text(json.dumps({'sources_sha256': frozen}, indent=2), encoding='utf8')
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {'SYSTEMROOT', 'WINDIR', 'PATH', 'COMSPEC', 'PATHEXT', 'PROCESSOR_ARCHITECTURE'}}
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', TEMP=str(OUT / 'temp'), TMP=str(OUT / 'temp'),
               DEEPEVAL_TELEMETRY_OPT_OUT='YES', LANGSMITH_TRACING='false', OTEL_SDK_DISABLED='true')
    rounds = []
    for number in (1, 2):
        checks = []
        before = sources() == frozen
        for case in CASES:
            path = folder / f'round-{number}-{case}.log'
            with path.open('w', encoding='utf8') as log:
                try:
                    run = subprocess.run([sys.executable, '-B', str(Path(__file__)), '--phase', args.phase, '--case', case],
                                         cwd=ROOT, env=env, stdout=log, stderr=log, shell=False, timeout=120,
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    code = run.returncode
                except subprocess.TimeoutExpired:
                    code = 'TIMEOUT'
            row = dict(name=case, passed=code == 0, exit_code=code, log=path.relative_to(ROOT).as_posix())
            checks.append(row)
            print(json.dumps({'round': number, **row}), flush=True)
        unchanged = before and sources() == frozen
        rounds.append(dict(number=number, checks=checks, sources_unchanged=unchanged,
                           passed=unchanged and all(c['passed'] for c in checks)))
        (folder / 'rounds.json').write_text(json.dumps(rounds, indent=2), encoding='utf8')
    versions = {}
    for name in ('google-adk', 'google-genai', 'mcp', 'click', 'pip', *OPTIONAL):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    passed = all(r['passed'] for r in rounds)
    summary = dict(complete=passed, consecutive_passes=2 if passed else 0, rounds=rounds,
                   sources_sha256=frozen, versions=versions, cloud_calls=0, docker_calls=0, independent_blind=False,
                   pending_optional_sdk_imports=[name for name in OPTIONAL if versions[name] is None],
                   scope='available offline contracts only; optional SDK, Docker and provider gates pending')
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf8')
    print(json.dumps({'complete': passed, 'artifact': str(folder / 'summary.json')}))
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
