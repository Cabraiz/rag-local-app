"""Stable workspace paths for standalone operations and QA scripts.

Production modules do not depend on this development helper. Historical proofs
are not rewritten: resolve_source follows only the explicit relocation manifest
and callers still compare the actual current SHA-256.
"""
from pathlib import Path
import json
import sys

APP = Path(__file__).resolve().parent
ROOT = APP.parent
MANIFEST = ROOT / 'docs/organization/relocations.json'


def bootstrap():
    sys.pycache_prefix = str(APP / '.local/python-cache')
    bases = (APP / 'tests', APP / 'tools', APP / 'advanced')
    paths = [APP, APP / 'src', APP / 'semantic']
    for base in bases:
        if base.is_dir():
            paths.extend(sorted({p.parent for p in base.rglob('*.py')
                                 if '__pycache__' not in p.parts}))
    for path in reversed(paths):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


def relocations():
    if not MANIFEST.is_file():
        return {}
    value = json.loads(MANIFEST.read_text(encoding='utf8'))
    return {item['from']: item['to'] for item in value['moves']}


def resolve_source(name, *, root=ROOT):
    """Resolve an old proof path without approving changed bytes or scope."""
    root = Path(root).resolve()
    original = (root / name).resolve()
    if not original.is_relative_to(root):
        raise ValueError('SOURCE_OUTSIDE_WORKSPACE')
    if original.is_file():
        return original
    relative = original.relative_to(root).as_posix()
    target = (root / relocations().get(relative, relative)).resolve()
    if not target.is_relative_to(root):
        raise ValueError('SOURCE_OUTSIDE_WORKSPACE')
    return target


def named_file(base, name):
    """Find a unique maintained script/config by filename, never a secret."""
    base = Path(base).resolve()
    direct = base / name
    if direct.is_file():
        return direct
    matches = [p for p in base.rglob(name) if p.is_file()
               and '__pycache__' not in p.parts and '.local' not in p.parts]
    if len(matches) != 1:
        raise ValueError('UNIQUE_WORKSPACE_FILE_REQUIRED:' + str(name))
    return matches[0]


def compose_file(name):
    candidate = (APP / name).resolve()
    if candidate.is_relative_to(APP / 'infrastructure/compose') and candidate.is_file():
        return candidate
    return named_file(APP / 'infrastructure/compose', name)


def test_file(name):
    return named_file(APP / 'tests', name)


def tool_file(name):
    return named_file(APP / 'tools', name)
