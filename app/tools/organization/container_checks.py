"""Two offline container rounds on the relocated code; network and secrets absent."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path('D:/RAG-Local')
APP = ROOT / 'app'
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
FOLDER = ROOT / 'eval/runs' / ('organization-sdk-' + STAMP)
FOLDER.mkdir(parents=True, exist_ok=False)
CASES = (
    ('rag-local-resilience:0.1.0', 'gemini/gemini_controls.py'),
    ('rag-local-resilience:0.1.0', 'gemini/gemini_grounded_checks.py'),
    ('rag-local-resilience:0.1.0', 'gemini/gemini_sdk_contract.py'),
    ('rag-local-resilience:0.1.0', 'security/sdk_logging_fixture.py'),
    ('rag-local-resilience:0.1.0', 'observability/tracing_sdk_fixture.py'),
    ('rag-local-resilience:0.1.0', 'observability/tracing_status_fixture.py'),
    ('rag-local-resilience:0.1.0', 'observability/telemetry_route_fixture.py'),
    ('rag-local-resilience:0.1.0', 'semantic/neural_validation_fixture.py'),
    ('rag-local-resilience:0.1.0', 'rag/grounding_recall_fixture.py'),
    ('rag-local-integrations-resilience:0.1.0', 'atlassian/atlassian_controls.py'),
    ('rag-local-integrations-resilience:0.1.0', 'github/github_controls.py'),
    ('rag-local-integrations-resilience:0.1.0', 'mcp/feed_checks.py'),
    ('rag-local-integrations-resilience:0.1.0', 'mcp/mcp_evidence_checks.py'),
    ('rag-local-integrations-resilience:0.1.0', 'mcp/mcp_gate_failure_checks.py'),
)
ADVANCED = ('dependency_checks', 'retry_checks', 'task_report_checks')


def hashes():
    files = [p for base in ('src', 'tests', 'tools', 'advanced', 'infrastructure')
             for p in (APP / base).rglob('*') if p.is_file() and '__pycache__' not in p.parts
             and p.suffix in ('.py', '.sql', '.json', '.yaml', '.lock')]
    files += [APP / 'workspace.py', Path(__file__)]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def run_case(round_, image, command, filename, mounted):
    image_info = subprocess.run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'], capture_output=True, text=True,
                                timeout=30, shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
    assert image_info.returncode == 0, 'QA_IMAGE_REQUIRED'
    name = 'rag-layout-sdk-' + STAMP.lower() + '-' + str(round_) + '-' + Path(filename).stem.replace('_', '-')
    args = ['docker', 'run', '--rm', '--name', name, '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--memory', '1g', '--cpus', '1', '--pids-limit', '128',
            '--tmpfs', '/tmp:size=128m,mode=1777', '--tmpfs', '/workspace/tmp:size=64m,mode=1777',
            '--tmpfs', '/workspace/eval:size=64m,mode=1777', '--tmpfs', '/home/rag:size=32m,mode=1777',
            '--tmpfs', '/workspace/eval/runs:size=64m,mode=1777',
            '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'DEEPEVAL_TELEMETRY_OPT_OUT=YES', '-e', 'LANGSMITH_TRACING=false']
    if mounted:
        for source, target in [(APP / 'workspace.py', '/workspace/app/workspace.py'),
                               (APP / 'tests', '/workspace/app/tests'), (APP / 'tools', '/workspace/app/tools'),
                               (APP / 'src', '/workspace/app/src'), (APP / 'infrastructure', '/workspace/app/infrastructure'),
                               (APP / 'semantic', '/workspace/app/semantic'), (ROOT / 'docs', '/workspace/docs'),
                               (ROOT / 'frontend', '/workspace/frontend')]:
            args += ['--mount', 'type=bind,source=' + str(source) + ',target=' + target + ',readonly']
        contract = ROOT / 'eval/runs/functional-priority-20261001/acceptance.json'
        args += ['--mount', 'type=bind,source=' + str(contract) + ',target=/workspace/eval/runs/functional-priority-20261001/acceptance.json,readonly']
    log = FOLDER / ('round-' + str(round_) + '-' + Path(filename).stem + '.log')
    with log.open('w', encoding='utf8') as output:
        result = subprocess.run(args + [image, *command], stdout=output, stderr=subprocess.STDOUT, timeout=150,
                                shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
    return dict(test=filename, image=image_info.stdout.strip(), exit_code=result.returncode, passed=result.returncode == 0,
                log=log.relative_to(ROOT).as_posix(), network='none', credentials_mounted=False)


if len(sys.argv) == 2:
    matches = [(image, path) for image, path in CASES if path == sys.argv[1]]
    assert len(matches) == 1, 'ONE_KNOWN_DEBUG_CASE_REQUIRED'
    image, path = matches[0]
    result = run_case('debug', image, ['python', '/workspace/app/tests/' + path], path, True)
    (FOLDER / 'debug.json').write_text(json.dumps(dict(result=result, full_gate=False), indent=2))
    print(json.dumps(result))
    raise SystemExit(0 if result['passed'] else 1)

frozen = hashes()
report = dict(rounds=[], complete=False, consecutive_passes=0, cloud_calls=0, remote_writes=0, sources_sha256=frozen,
              scope='real installed SDKs and controlled inputs in offline Linux containers; not live model/MCP task gates')
for round_ in (1, 2):
    results = []
    for image, script in CASES:
        result = run_case(round_, image, ['python', '/workspace/app/tests/' + script], script, True)
        results.append(result)
        print(json.dumps(dict(round=round_, test=script, passed=result['passed'])), flush=True)
        if not result['passed']:
            break
    if all(v['passed'] for v in results):
        for module in ADVANCED:
            result = run_case(round_, 'rag-local-advanced:0.1.0', ['python', '-m', module], module + '.py', False)
            results.append(result)
            print(json.dumps(dict(round=round_, test=module, passed=result['passed'])), flush=True)
            if not result['passed']:
                break
    passed = len(results) == len(CASES) + len(ADVANCED) and all(v['passed'] for v in results) and hashes() == frozen
    report['rounds'].append(dict(number=round_, results=results, passed=passed))
    (FOLDER / 'receipt.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    if not passed:
        break
report.update(complete=len(report['rounds']) == 2 and all(v['passed'] for v in report['rounds']),
              sources_unchanged=hashes() == frozen)
report['consecutive_passes'] = 2 if report['complete'] else 0
(FOLDER / 'receipt.json').write_text(json.dumps(report, indent=2), encoding='utf8')
print(json.dumps(dict(receipt=str(FOLDER / 'receipt.json'), complete=report['complete'])))
raise SystemExit(0 if report['complete'] else 1)
