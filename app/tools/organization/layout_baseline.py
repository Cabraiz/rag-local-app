# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Capture sanitized local state before relocation; no model or provider writes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import urllib.request

ROOT = Path('D:/RAG-Local')
OUT = ROOT / '.local/organization/baseline'
OUT.mkdir(parents=True, exist_ok=False)


def docker(*args):
    value = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=30,
                           shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
    if value.returncode:
        raise RuntimeError('BASELINE_DOCKER_FAILED')
    return value.stdout


containers = json.loads(docker('inspect', *docker('ps', '--filter', 'label=com.docker.compose.project=rag-local-v2', '-q').split()))
data = dict(at=datetime.now(timezone.utc).isoformat(), cloud_calls=0, remote_writes=0,
            containers=[dict(name=c['Name'], image=c['Image'], id=c['Id'],
                             volumes=[dict(type=m['Type'], name=m.get('Name'), source=m['Source'], destination=m['Destination']) for m in c['Mounts']],
                             config_files=c['Config']['Labels'].get('com.docker.compose.project.config_files')) for c in containers])
code = "import json; from rag_app import ledger;\nwith ledger.connect() as db:\n print(json.dumps({t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('requests','jobs','corpus_versions','corpus_chunks','documents')}))"
# Record only counts. Table names vary across older schemas, so capture the catalog first.
code = "import json; from rag_app import ledger;\nwith ledger.connect() as db:\n tables=[r['tablename'] for r in db.execute(\"SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename\")]\n print(json.dumps({t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in tables}))"
data['sql_counts'] = json.loads(docker('exec', 'rag-local-v2-api-1', 'python', '-c', code))
for key, url in (('app', 'http://127.0.0.1:8840/health/ready'), ('grafana', 'http://127.0.0.1:8850/api/health'), ('challenge', 'http://127.0.0.1:8860/health')):
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            data[key] = dict(http=response.status, body=response.read(65536).decode())
    except Exception as error:
        data[key] = dict(error=type(error).__name__)
proofs = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in (ROOT / 'eval/runs').rglob('*') if p.is_file()}
data['historical_evidence'] = proofs
source = sqlite3.connect('file:' + str(ROOT / '.local/card-execution/queue.sqlite3') + '?mode=ro', uri=True)
destination = sqlite3.connect(str(OUT / 'queue.sqlite3'))
with destination:
    source.backup(destination)
data['queue_states'] = dict(source.execute('SELECT status,count(*) FROM cards GROUP BY status').fetchall())
destination.close()
source.close()
(OUT / 'snapshot.json').write_text(json.dumps(data, indent=2), encoding='utf8')
print(json.dumps(dict(baseline=str(OUT / 'snapshot.json'), containers=len(containers), sql_tables=len(data['sql_counts']), historical_files=len(proofs), queue=data['queue_states'])))
