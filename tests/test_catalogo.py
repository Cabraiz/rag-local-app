"""catalogo.py is the one shared piece between the RAG search and the PII safety net: the
guardrails never import an MCP server, and the RAG server never imports the guardrails."""
import ast
from pathlib import Path

import catalogo
from guardrails import pii
from mcp_servers import rag

ROOT = Path(__file__).resolve().parents[1]


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    return {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names} | \
        {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}


def test_guardrails_and_the_rag_server_do_not_import_each_other():
    for path in (ROOT / 'guardrails').glob('*.py'):
        assert not any(name.startswith('mcp_servers') for name in imported_modules(path)), path.name
    assert not any(name.startswith('guardrails') for name in imported_modules(ROOT / 'mcp_servers' / 'rag.py'))
    assert imported_modules(ROOT / 'catalogo.py') <= {'difflib', 'functools', 'json', 'os', 're', 'unicodedata', 'pathlib'}


def test_the_search_and_the_pii_safety_net_score_against_the_same_catalog():
    assert rag.CATALOG is catalogo.CATALOG and rag.MIN_SCORE == pii.MIN_SCORE == catalogo.MIN_SCORE
    assert pii.rag_score('Hemograma completo') == 1.0 == rag.search('Hemograma completo', 1)[0]['score']


def test_only_catalogo_reads_the_catalog_file():
    """One source for the RAG and the guardrails. The API is its own service (its image has no
    catalogo.py) and reads the same file by itself."""
    code = [*ROOT.glob('*.py'), *(path for folder in ('api', 'guardrails', 'mcp_servers') for path in (ROOT / folder).glob('*.py'))]
    readers = {path.relative_to(ROOT).as_posix() for path in code if "'exams.json'" in path.read_text(encoding='utf-8')}
    assert readers == {'catalogo.py', 'api/main.py'}
