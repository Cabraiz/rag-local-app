# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Live local API/ADK worker regression, no remote writes or cloud generation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4
import functional_smoke as smoke

ROOT = _workspace_root
FOLDER = ROOT / 'eval/runs/unified-assistant-20261001'
NAMES = ('app/src/rag_app/domain/assistant_policy.py', 'app/src/rag_app/models/adk_workflow.py',
         'app/tests/assistant/assistant_policy_checks.py', 'app/tests/assistant/assistant_policy_smoke.py')


def frozen():
    return {n: hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in NAMES}


def run_round():
    checks, ids = [], []
    def check(name, passed):
        assert passed, name
        checks.append(name)
    code, health = smoke.http('/health/ready')
    check('healthy_lab_not_false_production', code == 200 and health['production_ready'] is False)
    check('anonymous_denied', smoke.http('/v1/requests')[0] in (401, 403))
    code, session = smoke.http('/v1/lab/session', body={'profile': 'ana'})
    check('client_role_unchanged', code == 200 and session['role'] == 'client')
    ana = session['token']
    bruno = smoke.http('/v1/lab/session', body={'profile': 'bruno'})[1]['token']
    check('client_cannot_administer_integrations', smoke.http('/v1/lab/integrations', ana)[0] == 403)
    code, catalog = smoke.http('/v1/lab/corpus', bruno)
    food = [d for d in catalog['documents'] if d['title'] == 'Alimentação']
    check('canonical_corpus_present', code == 200 and len(food) == 1)
    cases = (
        ('create', 'Crie um card no kanban', 'ABSTAIN'),
        ('move', 'Eu quero que você mova o card KAN-1', 'ABSTAIN'),
        ('catalog', 'Quero todos os preços dos alimentos', 'ABSTAIN'),
        ('knowledge', smoke.QUESTION, 'EXTRACTIVE'),
    )
    for name, question, kind in cases:
        key = str(int(time.time())) + '.' + str(uuid4())
        code, accepted = smoke.http('/v1/requests', ana, {'question': question}, {'Idempotency-Key': key})
        check(name + '_durably_accepted', code == 202)
        rid = accepted['request_id']; ids.append(rid)
        row = smoke.wait_terminal(rid, ana)
        result = row.get('result') or {}
        check(name + '_terminal_kind', row['state'] == 'SUCCEEDED' and result.get('kind') == kind)
        if kind == 'ABSTAIN':
            check(name + '_no_fake_citation', not result.get('citations'))
            expected = 'Nenhum card foi criado ou movido' if name != 'catalog' else 'Limites de reembolso não são preços'
            check(name + '_honest_limitation', expected in result.get('text', ''))
        else:
            check('knowledge_exact_evidence', result.get('text') == 'Trecho da fonte:\n' + smoke.QUOTE)
            cites = result.get('citations', [])
            check('knowledge_canonical_citation', len(cites) == 1 and cites[0]['document_id'] == food[0]['id']
                  and cites[0]['release_id'] == catalog['release_id'] and cites[0]['quote'] == smoke.QUOTE
                  and cites[0]['content_hash'] == hashlib.sha256(smoke.QUOTE.encode()).hexdigest())
        code, replay = smoke.http('/v1/requests', ana, {'question': question}, {'Idempotency-Key': key})
        check(name + '_replay_same_receipt', code == 202 and replay['request_id'] == rid)
        check(name + '_operator_cannot_read_client_receipt', smoke.http('/v1/requests/' + rid, bruno)[0] == 403)
    return {'passed': True, 'checks': checks, 'request_ids': ids}


if __name__ == '__main__':
    hashes = frozen(); rounds = []; error = None
    try:
        for _ in range(2):
            rounds.append(run_round())
            assert frozen() == hashes, 'SOURCE_CHANGED'
    except Exception as exc:
        error = type(exc).__name__ + ':' + str(exc)
    receipt = {'at': datetime.now(timezone.utc).isoformat(), 'passed': error is None and len(rounds) == 2,
        'clean_streak': len(rounds), 'rounds': rounds, 'error': error, 'source_sha256': hashes,
        'scope': 'same-executor local API/ADK worker regression; not conversational writes or independent blind audit'}
    FOLDER.mkdir(parents=True, exist_ok=True)
    path = FOLDER / ('live-' + datetime.now(timezone.utc).strftime('%H%M%S') + '.json')
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'passed': receipt['passed'], 'clean_streak': len(rounds),
        'round_checks': [len(r['checks']) for r in rounds], 'error': error, 'receipt': str(path)}))
    raise SystemExit(0 if receipt['passed'] else 1)
