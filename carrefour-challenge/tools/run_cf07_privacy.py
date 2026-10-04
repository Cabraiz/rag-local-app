"""Two frozen CF-APP-07 rounds in owned Docker resources, never cloud providers.

Keeps logs private under worktree/.local. Records red consumer gates honestly;
neither skips them nor installs the adapters into another owner's component.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
BASE_IMAGE = 'carrefour-adk-challenge:1.0.0'
BASE_IMAGE_ID = 'sha256:3ac0685898aa7ac27b471ec24e6636440d2abfac9e59cc067cc052c1da1d7272'
SEED = '707'


def fingerprint():
    excluded = {'.local', 'evidence', 'node_modules', '__pycache__', '.pytest_cache', 'generated'}
    return {p.relative_to(PROJECT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(PROJECT.rglob('*')) if p.is_file()
            and not set(p.relative_to(PROJECT).parts) & excluded}


def run(arguments, output, timeout=300):
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with output.open('w', encoding='utf8') as stream:
        result = subprocess.run(arguments, shell=False, creationflags=flags,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=timeout,
                                cwd=PROJECT, check=False)
    return result.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--port', type=int, default=18867)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    allowed = (PROJECT.parent / '.local').resolve()
    if not output.is_relative_to(allowed):
        raise SystemExit('OUTPUT_MUST_BE_WORKTREE_LOCAL')
    output.mkdir(parents=True, exist_ok=False)
    sources = fingerprint()
    encoded = json.dumps(sources, sort_keys=True, separators=(',', ':')).encode()
    source_hash = hashlib.sha256(encoded).hexdigest()
    (output / 'sources.json').write_bytes(encoded)
    image = 'cf07-privacy:' + source_hash[:16]
    project_name = 'cf07-' + source_hash[:12]
    dockerfile = output / 'Dockerfile'
    dockerfile.write_text('FROM ' + BASE_IMAGE + '\nCOPY --chown=10001:10001 . /app\n')
    # Reusing this namespace is forbidden, including stale gate-owned resources.
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    existing = subprocess.run(['docker', 'ps', '-aq', '--filter',
                              'label=com.docker.compose.project=' + project_name],
                             shell=False, creationflags=flags, capture_output=True, check=True)
    if existing.stdout.strip():
        raise SystemExit('PROJECT_NAMESPACE_ALREADY_EXISTS')
    inspect = subprocess.run(['docker', 'image', 'inspect', BASE_IMAGE, '--format', '{{.Id}}'],
                             shell=False, creationflags=flags, capture_output=True, check=True)
    if inspect.stdout.decode().strip() != BASE_IMAGE_ID:
        raise SystemExit('LOCAL_BASE_IMAGE_CHANGED')
    report = {'head': subprocess.run(['git', 'rev-parse', 'HEAD'], shell=False,
                                    creationflags=flags, capture_output=True, check=True).stdout.decode().strip(),
              'source_sha256': source_hash, 'base_image': BASE_IMAGE_ID,
              'project': project_name, 'seed': SEED, 'rounds': [],
              'scope': 'PII privacy only; existing booking domain is REQUESTED, not slot/consent flow',
              'external_network': 'disabled for build; internal Compose network for services',
              'independent_review': 'pending Central', 'integrated': False}
    try:
        report['build_exit'] = run(['docker', 'build', '--network', 'none', '--pull=false',
                                   '-f', str(dockerfile), '-t', image, '.'], output / 'build.log')
    except subprocess.TimeoutExpired:
        report['build_exit'] = 'timeout'
    if report['build_exit'] != 0:
        (output / 'receipt.json').write_text(json.dumps(report, indent=2))
        return 1
    image_id = subprocess.run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'],
                              shell=False, creationflags=flags, capture_output=True, check=True)
    report['image_id'] = image_id.stdout.decode().strip()
    override = output / 'compose.override.json'
    services = {name: {'image': report['image_id'], 'restart': 'no'}
                for name in ('api', 'ocr', 'rag', 'runner', 'tests')}
    services['api']['ports'] = ['127.0.0.1:' + str(args.port) + ':8080']
    services['tests']['environment'] = {'CF_SEED': SEED}
    override.write_text(json.dumps({'services': services}, indent=2))
    # COMPOSE port list merges need an override with !override, so use a generated
    # independent Compose file rather than touching the architecture owner's file.
    import yaml
    config = yaml.safe_load((PROJECT / 'docker-compose.yml').read_text())
    config.pop('name', None)
    config.pop('x-runtime', None)
    for name in ('api', 'ocr', 'rag', 'runner', 'tests'):
        config['services'][name].pop('build', None)
        config['services'][name].update(services[name])
        if name == 'tests':
            config['services'][name]['environment'].update({
                'PYTHONPATH': '/app/src:/app', 'OTEL_SDK_DISABLED': 'true',
                'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'})
    config['services']['ocr']['volumes'] = [str(PROJECT / 'examples') + ':/samples:ro']
    config['services'].pop('browser')
    # Shared Docker default pools may be exhausted by other lanes. Inspect only,
    # then choose two unused explicit subnets; never prune shared networks.
    identifiers = subprocess.run(['docker', 'network', 'ls', '-q'], shell=False,
                                 creationflags=flags, capture_output=True, check=True).stdout.decode().split()
    occupied = []
    if identifiers:
        metadata = subprocess.run(['docker', 'network', 'inspect', *identifiers], shell=False,
                                  creationflags=flags, capture_output=True, check=True)
        for network in json.loads(metadata.stdout):
            for row in network.get('IPAM', {}).get('Config', []) or []:
                if row.get('Subnet'):
                    occupied.append(ipaddress.ip_network(row['Subnet']))
    selected = []
    for octet in range(207, 240):
        candidate = ipaddress.ip_network(f'10.{octet}.7.0/24')
        if not any(candidate.version == old.version and candidate.overlaps(old) for old in occupied):
            selected.append(str(candidate))
            if len(selected) == 2:
                break
    if len(selected) != 2:
        raise SystemExit('NO_SAFE_UNUSED_TEST_SUBNET')
    for name, subnet in zip(('clinic', 'edge'), selected):
        config['networks'][name]['ipam'] = {'config': [{'subnet': subnet}]}
    report['explicit_unused_subnets'] = selected
    compose = output / 'compose.json'
    compose.write_text(json.dumps(config, indent=2))
    prefix = ['docker', 'compose', '-p', project_name, '-f', str(compose)]
    try:
        report['up_exit'] = run(prefix + ['up', '-d', '--no-build', '--pull', 'never',
                                        '--wait', '--wait-timeout', '100', 'api', 'ocr', 'rag'],
                                output / 'up.log', timeout=160)
        if report['up_exit'] == 0:
            for number in (1, 2):
                folder = output / ('round-' + str(number))
                folder.mkdir()
                item = {'number': number, 'seed': SEED, 'image_id': report['image_id']}
                common = prefix + ['run', '--rm', '--no-deps', 'tests', 'python', '-m', 'pytest',
                                   '-q', '-p', 'no:cacheprovider']
                item['helper_and_ocr_exit'] = run(common + ['tests/test_cf07_privacy_adapters.py',
                    'tests/test_pii_guardrails.py'], folder / 'helpers.log')
                item['consumer_gate_exit'] = run(common + ['tests/test_cf07_privacy_sinks.py'],
                                                 folder / 'consumers.log')
                item['real_sse_adk_api_exit'] = run(common + ['tests/test_pii_live_boundary.py'],
                                                   folder / 'live.log')
                item['real_mcp_envelope_exit'] = run(common + ['tests/test_mcp_security.py',
                    '-k', 'protocol_errors_cannot_reflect_pii'], folder / 'mcp-envelope.log')
                item['ledger_scan_exit'] = run(prefix + ['exec', '-T', 'api', 'python',
                                                       'tools/scan_ledger.py'], folder / 'ledger.log')
                item['sources_unchanged'] = sources == fingerprint()
                report['rounds'].append(item)
                print(json.dumps(item), flush=True)
    finally:
        report['logs_exit'] = run(prefix + ['logs', '--no-color', 'api', 'ocr', 'rag'],
                                  output / 'services.log')
        from scan_cf07_sinks import scan
        report['service_log_scan'] = scan((output / 'services.log').read_text(encoding='utf8'))
        # Only this newly created project; preserve volumes, images and unrelated services.
        report['cleanup_exit'] = run(prefix + ['down', '--timeout', '8'], output / 'cleanup.log')
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report['sources_unchanged'] = sources == fingerprint()
        report['passed'] = (len(report['rounds']) == 2 and report['sources_unchanged']
                            and report['service_log_scan']['pii_absent']
                            and all(all(row.get(key) == 0 for key in (
                                'helper_and_ocr_exit', 'consumer_gate_exit',
                                'real_sse_adk_api_exit', 'real_mcp_envelope_exit',
                                'ledger_scan_exit')) for row in report['rounds']))
        report['log_hashes'] = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in output.rglob('*.log')}
        (output / 'receipt.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'passed': report['passed'], 'receipt': str(output / 'receipt.json')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
