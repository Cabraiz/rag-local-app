"""The fixed lists of tests/versionados.py: they match the repository, and a user's extra file changes nothing."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

from tests.versionados import EXAMPLE_SPECS, SAMPLE_IMAGES

ROOT = Path(__file__).resolve().parents[1]
# The modules parametrized over the shipped samples and specs.
MODULES = ['tests/test_qualidade.py', 'tests/test_runtime.py', 'tests/test_spec_generica.py']


def tracked(pattern):
    """The files git tracks for a pattern, or None outside a git checkout (the Docker image has no .git)."""
    if shutil.which('git') is None:
        return None
    done = subprocess.run(['git', 'ls-files', pattern], cwd=ROOT, capture_output=True, text=True)
    return sorted(Path(line).name for line in done.stdout.splitlines()) if done.returncode == 0 else None


def test_every_listed_file_ships_with_the_repository():
    assert all((ROOT / 'samples' / name).is_file() for name in SAMPLE_IMAGES)
    assert all((ROOT / 'specs' / name).is_file() for name in EXAMPLE_SPECS)


def test_the_lists_are_the_files_git_tracks():
    """Where git runs (CI, a clone): exactly the tracked files. The Docker image has no .git, and there
    the test above (each listed file exists) is the check."""
    for pattern, listed in (('samples/*.png', SAMPLE_IMAGES), ('specs/*.json', EXAMPLE_SPECS)):
        files = tracked(pattern)
        assert files is None or files == sorted(listed), pattern


def collected(folder):
    out = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider', *MODULES],
                         cwd=folder, capture_output=True, text=True, timeout=300).stdout
    return int(re.search(r'(\d+) tests? collected', out).group(1))


def test_an_extra_image_or_spec_of_the_user_changes_no_count(tmp_path):
    """What the guides suggest (a new image in samples/, a new spec in specs/), on a copy of the project."""
    copy = tmp_path / 'projeto'
    for name in ('api', 'guardrails', 'mcp_servers', 'runtime', 'transpiler', 'tests', 'specs', 'data'):
        shutil.copytree(ROOT / name, copy / name, ignore=shutil.ignore_patterns('__pycache__'))
    for path in [*ROOT.glob('*.py'), ROOT / 'pyproject.toml', *(ROOT / 'samples').glob('*.*')]:
        (copy / path.relative_to(ROOT)).parent.mkdir(exist_ok=True)
        shutil.copy(path, copy / path.relative_to(ROOT))
    before = collected(copy)
    shutil.copy(copy / 'samples' / 'pedido.png', copy / 'samples' / 'minha-foto.png')
    (copy / 'samples' / 'quebrada.png').write_bytes(b'not a png')
    (copy / 'specs' / 'minha-spec.json').write_text('{"name": "rascunho"}', encoding='utf-8')
    assert collected(copy) == before > 0
