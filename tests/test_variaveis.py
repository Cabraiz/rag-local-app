"""Every environment variable the product code reads is in the table of docs/como-rodar.md and in .env.example."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = [*ROOT.glob('*.py'), *(path for folder in ('api', 'runtime', 'transpiler', 'mcp_servers', 'guardrails')
                              for path in (ROOT / folder).glob('*.py'))]
# os.environ.get('X'), os.environ['X'], os.environ.setdefault('X', ...), os.getenv('X'), and a
# name kept in a constant first (KEY_VARIABLE = 'DB_ENCRYPTION_KEY').
READ = re.compile(r"""environ(?:\.get|\.setdefault)?\(\s*['"]([A-Z_]+)['"]|environ\[\s*['"]([A-Z_]+)['"]\]"""
                  r"""|getenv\(\s*['"]([A-Z_]+)['"]|_VARIABLE\s*=\s*['"]([A-Z_]+)['"]""")


def read_by_the_code():
    return {name for path in CODE for groups in READ.findall(path.read_text(encoding='utf-8')) for name in groups if name}


def test_every_variable_the_code_reads_is_documented_and_in_the_env_example():
    names = read_by_the_code()
    assert {'GOOGLE_API_KEY', 'DB_ENCRYPTION_KEY', 'API_RATE_LIMIT_PER_MINUTE', 'EXAMS_PATH'} <= names  # the regex works
    guide = (ROOT / 'docs' / 'como-rodar.md').read_text(encoding='utf-8')
    table = set(re.findall(r'^\| `([A-Z_]+)` \|', guide.split('## Variáveis de ambiente', 1)[1], re.M))
    example = (ROOT / '.env.example').read_text(encoding='utf-8')
    assert names - table == set(), 'missing from the table in docs/como-rodar.md'
    assert {name for name in names if not re.search(rf'\b{name}\b', example)} == set(), 'missing from .env.example'
