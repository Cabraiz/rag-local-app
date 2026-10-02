"""Inspect only the Git index; never echo credentials or upload local runtime records."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=90,
                            shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError('GIT_INDEX_INSPECTION_FAILED')
    return result.stdout


def scan(root):
    names = git(root, 'ls-files', '-z').decode('utf8').split('\0')
    names = [v for v in names if v]
    patterns = {
        'google_api_key': re.compile(rb'AIza[0-9A-Za-z_-]{35}'),
        'github_token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})'),
        'atlassian_token': re.compile(rb'ATATT[A-Za-z0-9_=-]{25,}'),
        'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
        'aws_access_id': re.compile(rb'AKIA[0-9A-Z]{16}'),
    }
    rejected = []
    inventory = []
    for name in names:
        parts = Path(name).parts
        if (any(p in ('.local', '.venv', '__pycache__', 'node_modules') for p in parts)
                or name.startswith(('eval/runs/', 'eval/history/', 'eval/receipts/', 'eval/logs/', 'eval/reports/orchestration/', 'deliveries/', 'cache/', 'tmp/'))
                or (Path(name).name.startswith('.env') and Path(name).name != '.env.example')
                or Path(name).suffix.lower() in ('.sqlite3', '.db', '.log', '.pem', '.key', '.pfx', '.p12')):
            rejected.append(dict(path=name, reason='PRIVATE_RUNTIME_PATH'))
        data = git(root, 'show', ':' + name)
        inventory.append(dict(path=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        if len(data) > 10_000_000:
            rejected.append(dict(path=name, reason='LARGE_ARTIFACT_REQUIRES_REVIEW'))
        for kind, pattern in patterns.items():
            for match in pattern.finditer(data):
                rejected.append(dict(path=name, reason=kind, line=data[:match.start()].count(b'\n') + 1,
                                     fingerprint=hashlib.sha256(match.group()).hexdigest()[:16]))
    return dict(at=datetime.now(timezone.utc).isoformat(), passed=not rejected, files=len(names),
                bytes=sum(v['bytes'] for v in inventory), rejected=rejected, inventory=inventory,
                scope='known credential patterns and private-path denylist; not a universal secret detector')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    result = scan(args.root.resolve())
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps(dict(passed=result['passed'], files=result['files'], bytes=result['bytes'],
                         findings=result['rejected'], receipt=str(args.receipt))))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
