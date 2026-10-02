"""Build the relocated Docker definitions locally, with recoverable old tags."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

ROOT = Path('D:/RAG-Local')
APP = ROOT / 'app'
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
FOLDER = ROOT / 'eval/runs' / ('organization-build-' + STAMP)
FOLDER.mkdir(parents=True, exist_ok=False)
IMAGES = (
    ('rag-local-backend:0.1.0', 'infrastructure/images/backend/Dockerfile', APP, []),
    ('rag-local-resilience:0.1.0', 'infrastructure/images/backend/Dockerfile.resilience', APP, []),
    ('rag-local-mcp-lab:0.1.0', 'infrastructure/images/integrations/Dockerfile.mcp-lab', APP, []),
    ('rag-local-github-lab:0.1.0', 'infrastructure/images/integrations/Dockerfile.github-lab', APP, []),
    ('rag-local-integrations:0.1.0', 'infrastructure/images/integrations/Dockerfile.integrations', APP, []),
    ('rag-local-integrations-resilience:0.1.0', 'infrastructure/images/integrations/Dockerfile.integrations.resilience', APP, []),
    ('rag-local-advanced:0.1.0', 'advanced/containers/Dockerfile', APP, []),
    ('rag-local-embedding:0.1.0', 'Dockerfile', APP / 'semantic',
     ['--build-context', 'policy=' + str(APP / 'src/rag_app'), '--build-context', 'dataset=' + str(ROOT / 'eval/datasets')]),
)


def execute(args, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, encoding='utf8', timeout=timeout,
                          shell=False, creationflags=subprocess.CREATE_NO_WINDOW)


report = dict(local_builds=True, remote_pushes=0, model_calls=0, builds=[])
for number, (image, dockerfile, context, extra) in enumerate(IMAGES):
    previous = execute(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'])
    prior_id = previous.stdout.strip() if previous.returncode == 0 else None
    backup = image.split(':')[0] + ':organization-before-' + STAMP.lower()
    if prior_id:
        tagged = execute(['docker', 'tag', prior_id, backup])
        assert tagged.returncode == 0, 'IMAGE_BACKUP_FAILED'
    log = FOLDER / (str(number + 1).zfill(2) + '-' + image.split(':')[0] + '.log')
    with log.open('w', encoding='utf8') as output:
        result = subprocess.run(['docker', 'build', '--progress', 'plain', '-f', str(context / dockerfile), '-t', image,
                                 *extra, str(context)], stdout=output, stderr=subprocess.STDOUT, timeout=900,
                                shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
    current = execute(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'])
    row = dict(image=image, dockerfile=str(context / dockerfile), exit_code=result.returncode,
               previous=prior_id, backup=backup if prior_id else None,
               built=current.stdout.strip() if current.returncode == 0 else None, log=str(log))
    report['builds'].append(row)
    (FOLDER / 'receipt.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(dict(image=image, passed=result.returncode == 0, receipt=str(FOLDER / 'receipt.json'))), flush=True)
    if result.returncode:
        raise SystemExit(1)
report['complete'] = True
(FOLDER / 'receipt.json').write_text(json.dumps(report, indent=2), encoding='utf8')
