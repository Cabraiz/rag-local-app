# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Structural/document consistency checks only; never certify implementation."""
from pathlib import Path
import argparse
import hashlib
import json
import random
import re

ROOT = Path(__file__).resolve().parent
files = {name: (ROOT / name).read_bytes() for name in ('application.md', 'application-eraser.txt')}
diagram = files['application-eraser.txt'].decode('utf-8')
document = files['application.md'].decode('utf-8')
defs = set(re.findall(r'^\s{2}(\w+) \[', diagram, re.M))
edges = re.findall(r'^(\w+)\s+(>|-->)\s+(\w+)(?::.*)?$', diagram, re.M)
checks = {
    'defined_endpoints': all(a in defs and b in defs for a, _, b in edges),
    'unique_nodes': len(defs) == len(re.findall(r'^\s{2}(\w+) \[', diagram, re.M)),
    'entrypoints_use_cases_only': all(b == 'UseCases' for a, _, b in edges if a in {'API','Workers','Dev'}),
    'no_agent_terminal_write': not any(a in {'ADK','Deep'} and b in {'PG','FS'} for a, _, b in edges),
    'ports_route_all_adapters': {'ADK','Deep','IO','Eval'} <= {b for a, _, b in edges if a == 'Ports'},
    'data_effects_encapsulated': all(a == 'IO' for a, _, b in edges if b in {'PG','QD','FS','Remote'}),
    'rules_owned_by_domain': ('UseCases','>','Domain') in edges,
    'automatic_layout': not re.search(r'\b(x|y|position)\s*:', diagram),
    'modular_hexagonal_not_claimed_market_rank': 'não uma afirmação de ranking' in document,
    'import_direction_defined': '`domain` usa biblioteca padrão' in document and 'Somente bootstrap' in document,
    'durable_accept_before_202': 'Só depois responde 202' in document,
    'claim_not_long_transaction': 'sem manter transação aberta durante inferência' in document,
    'fenced_terminal_and_revoke': 'sob uma transação curta' in document and 'mesma ordem de locks/epochs' in document,
    'no_raw_candidate_delivery': 'Nunca transmitir tokens brutos' in document,
    'mcp_not_local_agent_transport': 'MCP é o protocolo de ferramentas externas' in document,
    'deepagents_custom_bridge': 'a ponte TaskPort será uma integração nossa' in document,
    'web_dev_only': 'somente desenvolvimento e debugging' in document,
    'eval_offline_not_entire_suite_per_request': 'não roda o dataset inteiro por pergunta' in document,
    'local_not_load_proof': 'não são benchmarks desta aplicação' in document,
    'implementation_scope_not_overclaimed': 'primeira fatia de lifecycle implementada' in document and 'Não é o RAG completo' in document,
    'all_six_topics': all(x in document for x in ('Python','ADK','RAG','MCP','Vertex','Gemini','DeepEval','judge','DeepAgents','OpenTelemetry')),
    'no_human_response_approval': 'Não existe aprovação humana por resposta' in document,
    'cloud_opt_in': 'Cloud fica desativada por padrão' in document,
    'previous_tests_not_new_proof': 'não esta nova implementação' in document,
}
rounds = []
for seed in (7919, 104729):
    rows = list(checks.items())
    random.Random(seed).shuffle(rows)
    rounds.append({'seed': seed, 'passed': sum(ok for _, ok in rows), 'total': len(rows),
                   'findings': [name for name, ok in rows if not ok]})
assert all((ROOT / name).read_bytes() == value for name, value in files.items())
receipt = {'boundary': 'Repeated static structural and text-presence checks by same reviewer; not blind independent or runtime tests.',
           'nodes': len(defs), 'edges': len(edges), 'rounds': rounds,
           'hashes_bytes': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
           'dsl_sha256_lf_trim': hashlib.sha256(diagram.replace('\r\n','\n').strip().encode()).hexdigest()}
parser = argparse.ArgumentParser()
parser.add_argument('--receipt', default='validation-application.json')
args = parser.parse_args()
target = ROOT / args.receipt
assert not target.exists(), 'Keep receipt; choose another name before a new validation'
target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(receipt, ensure_ascii=False))
raise SystemExit(0 if all(not r['findings'] for r in rounds) else 1)
