# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Bounded one-shot SDK gates on authorized synthetic data, never paid fallback."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = _workspace_root


def command(args, timeout=180):
    return subprocess.run(args, capture_output=True, text=True, encoding='utf8', timeout=timeout,
                          shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def sources():
    paths = list((ROOT / 'app/advanced').rglob('*.py')) + list((ROOT / 'app/advanced/evaluation/datasets').glob('*.json'))
    paths += list((ROOT / 'app/src').rglob('*.py')) + list((ROOT / 'app/src').rglob('*.sql'))
    paths += [Path(__file__), ROOT / 'app/advanced/containers/Dockerfile', ROOT / 'app/advanced/dependencies/requirements.lock',
              ROOT / 'app/infrastructure/compose/labs/compose.advanced.yaml', ROOT / 'app/infrastructure/compose/labs/compose.gemini-lab.yaml']
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument('stage', choices=['task', 'judge'])
    args = cli.parse_args()
    frozen = sources()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder = ROOT / 'eval/runs' / ('advanced-' + args.stage + '-' + stamp)
    folder.mkdir(exist_ok=False)
    name = 'rag-backlog-' + args.stage + '-' + stamp.lower()
    compose = ['docker', 'compose', '--project-directory', str(APP), '-p', 'rag-local-v2']
    for file in ('infrastructure/compose/runtime/compose.yaml', 'infrastructure/compose/runtime/compose.retrieval.yaml', 'infrastructure/compose/labs/compose.gemini-lab.yaml', 'infrastructure/compose/labs/compose.advanced.yaml'):
        compose += ['-f', str(ROOT / 'app' / file)]
    compose += ['--profile', 'advanced_lab']
    report = dict(card_id='RAG-07' if args.stage == 'task' else 'RAG-08',
                  evidence_type='real_integration', complete=False, consecutive_passes=0,
                  sources_sha256=frozen, criteria_passed=[], paid_fallback=False, independent_blind=False,
                  scope='real SDK and fixed Free model; synthetic laboratory, not public production')
    try:
        result = command(compose + ['build', 'advanced'], timeout=240)
        (folder / 'build.log').write_text(result.stdout + result.stderr)
        assert result.returncode == 0, 'SDK_IMAGE_BUILD_FAILED'
        image = command(['docker', 'image', 'inspect', 'rag-local-advanced:0.1.0', '--format', '{{.Id}}'])
        assert image.returncode == 0
        report['image'] = image.stdout.strip()
        # Compare actual embedded Python and dataset bytes, not mutable tag names.
        embedded_paths = [p for p in frozen if p.startswith(('app/src/', 'app/advanced/'))
                          and p.endswith(('.py', '.json'))]
        code = "import hashlib,json,pathlib; paths=" + repr(embedded_paths) + "; print(json.dumps({p:hashlib.sha256(pathlib.Path('/advanced/'+p[len('app/advanced/'): ] if p.startswith('app/advanced/') else '/app/src/'+p[len('app/src/'):]).read_bytes()).hexdigest() for p in paths}))"
        embedded = command(['docker', 'run', '--rm', '--network', 'none', '--read-only', report['image'],
                            'python', '-c', code])
        assert embedded.returncode == 0, 'EMBEDDED_PROOF_FAILED'
        assert all(frozen[p] == digest for p, digest in json.loads(embedded.stdout).items()), 'IMAGE_SOURCE_MISMATCH'
        result = command(compose + ['run', '--no-deps', '-T', '--name', name, 'advanced',
                                    'python', 'tasks/task_qa.py' if args.stage == 'task' else 'evaluation/judge_qa.py'], timeout=1200)
        (folder / 'sdk.log').write_text(result.stdout + result.stderr)
        outputs = []
        for line in result.stdout.splitlines():
            try:
                outputs.append(json.loads(line))
            except ValueError:
                pass
        assert outputs and outputs[-1].get('proof'), 'SDK_PROOF_MISSING'
        copied = command(['docker', 'cp', name + ':' + outputs[-1]['proof'], str(folder / 'sdk-proof.json')])
        assert copied.returncode == 0, 'SDK_PROOF_COPY_FAILED'
        proof = json.loads((folder / 'sdk-proof.json').read_text())
        report['sdk_proof'] = proof
        assert result.returncode == 0 and proof['complete'] and proof['consecutive_passes'] == 2, 'SDK_REAL_GATE_FAILED'
        assert len(proof['rounds']) == 2 and all(r['passed'] for r in proof['rounds'])
        assert frozen == sources(), 'SOURCE_CHANGED_RESET_STREAK'
        report.update(complete=True, consecutive_passes=2,
                      criteria_passed=['real_task', 'restricted_tools', 'checkpoint', 'cancellation', 'budgets']
                      if args.stage == 'task' else ['versioned_dataset', 'holdout', 'calibrated_judge', 'deterministic_tests'])
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['error_code'] = str(exc) if isinstance(exc, AssertionError) else 'SANITIZED_GATE_ERROR'
    finally:
        target = folder / 'receipt.json'
        target.write_text(json.dumps(report, indent=2))
    print(json.dumps(dict(receipt=str(target), complete=report['complete'], error=report.get('error_code'))), flush=True)
    raise SystemExit(0 if report['complete'] else 1)


if __name__ == '__main__':
    main()
