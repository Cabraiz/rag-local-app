"""Fresh layout gate. Static/current runtime checks, never old receipt promotion."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import secrets
import sqlite3
import subprocess
import sys
import urllib.request

ROOT = Path('D:/RAG-Local').resolve()
APP = ROOT / 'app'
sys.path.insert(0, str(APP))
from workspace import bootstrap, compose_file, resolve_source
bootstrap()
from manage import RUNTIME_PROFILES


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(args, timeout=90):
    return subprocess.run(args, capture_output=True, text=True, encoding='utf8', timeout=timeout,
                          shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def sources():
    paths = []
    for base in ('app', 'docs', 'frontend', 'carrefour-challenge'):
        for path in (ROOT / base).rglob('*'):
            if not path.is_file() or any(v in path.parts for v in ('.local', '.venv', '__pycache__', 'model', 'data', 'history', 'diagrams')):
                continue
            if path.suffix in ('.py', '.ps1', '.md', '.yaml', '.yml', '.toml', '.js', '.json', '.sql', '.lock', '.in', '.html', '.css') or path.name.startswith('Dockerfile'):
                paths.append(path)
    paths.append(ROOT / 'README.md')
    return paths


def one_round(seed, folder, live=False):
    checks = []
    def check(name, ok, details=None):
        checks.append(dict(name=name, passed=bool(ok), details=details))
    manifest = json.loads((ROOT / 'docs/organization/relocations.json').read_text())
    moved = manifest['moves']
    check('all_destinations_exist', all((ROOT / v['to']).is_file() for v in moved), len(moved))
    check('no_collisions', len({v['to'] for v in moved}) == len(moved))
    originals = [v for v in moved if v['to'].startswith(('eval/', 'docs/history/', 'docs/diagrams/')) or v['to'].endswith('.sql')]
    check('archived_artifacts_and_schema_byte_preserved', all(sha(ROOT / v['to']) == v['sha256'] for v in originals), len(originals))
    modules = [v for v in moved if v['to'].startswith('app/src/rag_app/') and v['to'].endswith('.py')]
    check('business_module_bytes_unchanged', all(sha(ROOT / v['to']) == v['sha256'] for v in modules), len(modules))
    check('old_paths_resolve_to_exact_current_destination', all(resolve_source(v['from']) == (ROOT / v['to']).resolve() for v in moved if v['from'] != 'app/README.md'))
    errors = []
    python = [p for p in sources() if p.suffix == '.py']
    random.Random(seed).shuffle(python)
    for path in python:
        try:
            compile(path.read_text(encoding='utf-8-sig'), str(path), 'exec')
        except Exception as error:
            errors.append(dict(path=path.relative_to(ROOT).as_posix(), error=type(error).__name__))
    check('all_python_sources_compile', not errors, dict(files=len(python), errors=errors))
    missing = []
    for path in python:
        if path.parent.name == 'organization':
            continue
        text = path.read_text(encoding='utf-8-sig')
        for item in re.findall(r"['\"]((?:app|docs)/[^'\"\n]+\.(?:py|sql|lock|in|yaml|yml|ps1|json|md|txt))['\"]", text):
            if not (ROOT / item).is_file() and not item.startswith('docs/history/'):
                missing.append(dict(script=path.relative_to(ROOT).as_posix(), target=item))
    check('literal_source_config_paths_exist', not missing, missing)
    too_many = []
    for base in ('app', 'docs', 'frontend', 'carrefour-challenge'):
        for path in (ROOT / base).rglob('*'):
            if not path.is_dir() or any(v in path.parts for v in ('.local', '.venv', '__pycache__', 'model', 'data')):
                continue
            number = sum(1 for p in path.iterdir() if p.is_file())
            if number >= 20:
                too_many.append(dict(folder=path.relative_to(ROOT).as_posix(), files=number))
    check('maintained_folders_under_20_direct_files', not too_many, too_many)
    root_files = sorted(p.name for p in APP.iterdir() if p.is_file())
    check('app_root_only_entrypoints_and_configuration', root_files == ['.dockerignore', '.gitignore', 'README.md', 'manage.py', 'pyproject.toml', 'workspace.py'], root_files)
    links = []
    for path in [p for p in sources() if p.suffix == '.md']:
        for target in re.findall(r'\[[^\]]+\]\(([^\)]+)\)', path.read_text(encoding='utf-8-sig')):
            target = target.strip('<>').split('#')[0]
            if not target or target.startswith(('http:', 'https:', 'mailto:', 'D:', 'C:')):
                continue
            if not (path.parent / target).exists():
                links.append(dict(document=path.relative_to(ROOT).as_posix(), target=target))
    check('current_documentation_links_resolve', not links, links)
    combos = [list(RUNTIME_PROFILES), ['compose.yaml', 'compose.retrieval.yaml', 'compose.atlassian-lab.yaml'],
              ['compose.yaml', 'compose.retrieval.yaml', 'compose.github-lab.yaml'],
              ['compose.yaml', 'compose.retrieval.yaml', 'compose.gemini-lab.yaml', 'compose.advanced.yaml'],
              [n for n in RUNTIME_PROFILES if 'integrations' not in n and 'gemini' not in n] + ['compose.qa.yaml', 'compose.resilience-qa.yaml', 'compose.reliability-qa.yaml']]
    profile_errors = []
    for number, profiles in enumerate(combos):
        args = ['docker', 'compose', '--project-directory', str(APP), '-p', 'rag-local-layout-config']
        for profile in profiles:
            args += ['-f', str(compose_file(profile))]
        args += ['--profile', 'advanced_lab', '--profile', 'mcp_lab', '--profile', 'gemini_lab', 'config', '--format', 'json']
        result = command(args)
        if result.returncode:
            profile_errors.append(dict(profile=number, error=result.stderr[-1200:]))
            continue
        value = json.loads(result.stdout)
        for name, service in value['services'].items():
            build = service.get('build')
            if build:
                context = Path(build['context'])
                if not context.is_dir() or not (context / build.get('dockerfile', 'Dockerfile')).is_file():
                    profile_errors.append(dict(profile=number, service=name, error='BUILD_PATH_MISSING'))
            for mount in service.get('volumes', []):
                if mount['type'] == 'bind' and not Path(mount['source']).exists():
                    profile_errors.append(dict(profile=number, service=name, error='BIND_SOURCE_MISSING', source=mount['source']))
        for name, secret in value.get('secrets', {}).items():
            if 'file' in secret and not Path(secret['file']).is_file():
                profile_errors.append(dict(profile=number, secret=name, error='SECRET_FILE_MISSING'))
    check('all_five_compose_profiles_and_bindings_resolve', not profile_errors, profile_errors)
    js_results = []
    for path in (ROOT / 'frontend').rglob('*.js'):
        result = command(['node', '--check', str(path)])
        js_results.append(dict(path=path.relative_to(ROOT).as_posix(), exit=result.returncode))
    check('frontend_javascript_syntax', all(v['exit'] == 0 for v in js_results), js_results)
    unit_results = []
    for test, extra in [('card_queue_fixture.py', []), ('assistant_policy_checks.py', []), ('helper_regression.py', ['--kind', 'rag']), ('helper_regression.py', ['--kind', 'delivery'])]:
        result = command([sys.executable, str(APP / 'manage.py'), 'test', test, *extra], timeout=120)
        log = folder / ('round-' + str(seed) + '-' + test.removesuffix('.py') + (('-' + extra[-1]) if extra else '') + '.log')
        log.write_text(result.stdout + '\n' + result.stderr, encoding='utf8')
        unit_results.append(dict(test=test, args=extra, exit=result.returncode, log=log.relative_to(ROOT).as_posix()))
    check('regressions_of_queue_guard_and_cross_folder_helpers', all(v['exit'] == 0 for v in unit_results), unit_results)
    if live:
        import provider_usage_proof
        usage = provider_usage_proof.snapshot_usage()
        check('shared_persistent_usage_counter', usage['volume']['name'] == 'rag-local-v2_gemini_probe_usage', dict(used=usage['used'], volume=usage['volume']['name']))
        for name, url in [('app', 'http://127.0.0.1:8840/health/ready'), ('grafana', 'http://127.0.0.1:8850/api/health'), ('challenge', 'http://127.0.0.1:8860/docs')]:
            try:
                with urllib.request.urlopen(url, timeout=8) as response:
                    check(name + '_actual_http', response.status == 200)
            except Exception as error:
                check(name + '_actual_http', False, type(error).__name__)
    return dict(seed=seed, checks=checks, passed=all(v['passed'] for v in checks))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--rounds', type=int, default=2, choices=[1, 2])
    args = parser.parse_args()
    folder = ROOT / 'eval/runs' / ('organization-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(parents=True, exist_ok=False)
    frozen = {p.relative_to(ROOT).as_posix(): sha(p) for p in sources()}
    rounds = []
    for number in range(args.rounds):
        value = one_round(secrets.randbits(32), folder, args.live)
        rounds.append(value)
        print(json.dumps(dict(round=number + 1, passed=value['passed'], failures=[v for v in value['checks'] if not v['passed']])), flush=True)
        if not value['passed']:
            break
    unchanged = {p.relative_to(ROOT).as_posix(): sha(p) for p in sources()} == frozen
    complete = len(rounds) == 2 and all(v['passed'] for v in rounds) and unchanged
    receipt = dict(complete=complete, consecutive_passes=2 if complete else 0, rounds=rounds,
                   sources_sha256=frozen, sources_unchanged=unchanged, cloud_calls=0,
                   remote_writes=0, independent_blind=False,
                   scope='folder layout, paths, parsing, local regression and optional HTTP; not production capacity or historical card renewal')
    path = folder / 'receipt.json'
    path.write_text(json.dumps(receipt, indent=2), encoding='utf8')
    print(json.dumps(dict(receipt=str(path), complete=complete)))
    return 0 if all(v['passed'] for v in rounds) and unchanged else 1


if __name__ == '__main__':
    raise SystemExit(main())
