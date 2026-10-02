"""Mechanical second pass for app-relative paths and dynamic fixture filenames."""
import ast
import json
from pathlib import Path
import re

ROOT = Path('D:/RAG-Local')
APP = ROOT / 'app'
manifest = json.loads((ROOT / 'docs/organization/relocations.json').read_text())
mapping = {v['from']: v['to'] for v in manifest['moves']}
changes = []
for base in ('app/tests', 'app/tools'):
    for path in (ROOT / base).rglob('*.py'):
        if path.parent.name == 'organization':
            continue
        original = path.read_text(encoding='utf8')
        text = original
        for old, new in mapping.items():
            if not old.startswith('app/') or not new.startswith('app/'):
                continue
            before, after = old[len('app/'):], new[len('app/'):]
            for quote in ("'", '"'):
                text = text.replace(quote + before + quote, quote + after + quote)
        # Bare Dockerfiles and backend locks in app-root-relative expressions.
        for name in ('Dockerfile', 'Dockerfile.resilience', 'Dockerfile.integrations.resilience', 'requirements.lock', 'resilience-requirements.lock', 'mcp-requirements.lock'):
            if 'app/' + name not in mapping:
                continue
            replacement = mapping['app/' + name][len('app/'):]
            for quote in ("'", '"'):
                text = text.replace(quote + name + quote, quote + replacement + quote)
        text = re.sub(r"ROOT\s*/\s*['\"]app/tests['\"]\s*/\s*(filename|name)\b", r"_named_file(ROOT / 'app/tests', \1)", text)
        text = re.sub(r"\(_workspace_root / \"app\"\)\s*/\s*['\"]tools/card_queue.py['\"]", "_named_file(_workspace_root / 'app/tools', 'card_queue.py')", text)
        # Dynamic QA helpers use a unique current path rather than assuming tests are flat.
        text = re.sub(r"ROOT\s*/\s*['\"]app/tests/['\"]\s*\+\s*name", "_named_file(ROOT / 'app/tests', name)", text)
        if text != original:
            ast.parse(text)
            path.write_text(text, encoding='utf8', newline='\n')
            changes.append(path.relative_to(ROOT).as_posix())
print(json.dumps(dict(path_reference_corrections=changes)))

for path in (APP / 'infrastructure/images').rglob('Dockerfile*'):
    original = path.read_text(encoding='utf8')
    text = original
    for old, new in mapping.items():
        if old.startswith('app/src/'):
            text = text.replace(old[len('app/'):], new[len('app/'):])
    if text != original:
        path.write_text(text, encoding='utf8', newline='\n')
