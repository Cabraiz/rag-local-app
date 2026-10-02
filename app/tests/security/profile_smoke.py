# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Fresh API checks of both existing demo corpora; no reseeding or remote calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import time
from uuid import uuid4
import functional_smoke as smoke

ROOT = _workspace_root
QUOTES = {tenant: smoke.QUOTE.replace('45 reais', amount + ' reais')
          for tenant, amount in [('demo-a', '45'), ('demo-b', '20')]}


def frozen():
    names = ['frontend/public/index.html', 'frontend/public/app.js', 'frontend/public/styles.css',
             'app/tests/security/profile_smoke.py', 'app/tests/shared/functional_smoke.py',
             'eval/runs/profile-login-20261001/acceptance.json']
    return {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in names}


def run_round():
    checks, rows, catalogs = [], {}, {}
    def check(name, passed):
        assert passed, name
        checks.append(name)
    tokens = {t: smoke.http('/v1/lab/session', body={'tenant': t})[1]['token'] for t in QUOTES}
    check('anonymous_corpus_denied', smoke.http('/v1/lab/corpus')[0] in (401, 403))
    for tenant, quote in QUOTES.items():
        code, catalog = smoke.http('/v1/lab/corpus', tokens[tenant]); catalogs[tenant] = catalog
        check(tenant + '_eight_seed_documents', code == 200 and len(catalog['documents']) == 8)
        food = next(d for d in catalog['documents'] if d['title'] == 'Alimentação')
        check(tenant + '_distinct_canonical_food_policy', any(c['quote'] == quote for c in food['chunks']))
        key = str(int(time.time())) + '.' + str(uuid4())
        code, accepted = smoke.http('/v1/requests', tokens[tenant], {'question': smoke.QUESTION},
                                    {'Idempotency-Key': key})
        check(tenant + '_accepted', code == 202)
        rid = accepted['request_id']
        row = smoke.wait_terminal(rid, tokens[tenant]); rows[tenant] = row
        result = row.get('result') or {}; citations = result.get('citations', [])
        check(tenant + '_worker_returned_real_source', row['state'] == 'SUCCEEDED'
              and result.get('kind') == 'EXTRACTIVE' and result.get('text') == 'Trecho da fonte:\n' + quote
              and len(citations) == 1 and citations[0]['document_id'] == food['id']
              and citations[0]['release_id'] == catalog['release_id'])
        other = 'demo-b' if tenant == 'demo-a' else 'demo-a'
        check(tenant + '_other_profile_cannot_read', smoke.http('/v1/requests/' + rid, tokens[other])[0] == 404)
        history = smoke.http('/v1/requests', tokens[tenant])[1]
        check(tenant + '_history_own_request', any(r['id'] == rid for r in history))
    check('versions_and_documents_separate', catalogs['demo-a']['release_id'] != catalogs['demo-b']['release_id']
          and not ({d['id'] for d in catalogs['demo-a']['documents']} &
                   {d['id'] for d in catalogs['demo-b']['documents']}))
    check('same_question_different_canonical_answers', rows['demo-a']['result']['text'] != rows['demo-b']['result']['text'])
    for tenant, token in tokens.items():
        other_id = rows['demo-b' if tenant == 'demo-a' else 'demo-a']['id']
        history = smoke.http('/v1/requests', token)[1]
        check(tenant + '_no_other_profile_request_in_history', not any(r['id'] == other_id for r in history))
    return {'checks': checks, 'request_ids': {t: row['id'] for t, row in rows.items()}, 'passed': True}


def main():
    folder = ROOT / 'eval/runs' / ('profile-smoke-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                                + '-' + secrets.token_hex(3)); folder.mkdir(parents=True)
    sources, rounds, error, streak = frozen(), [], None, 0
    try:
        for _ in range(2):
            assert sources == frozen(), 'source_changed'
            rounds.append(run_round()); streak += 1
        assert sources == frozen(), 'source_changed'
    except Exception as failure:
        error = {'type': type(failure).__name__, 'reason': str(failure)[:160]}; streak = 0
    receipt = {'scope': 'real_local_API_ADK_two_existing_synthetic_companies', 'source_sha256': sources,
               'rounds': rounds, 'error': error, 'consecutive_passes': streak, 'independent_blind': False,
               'production_ready': False, 'corpus_replaced': False, 'cloud_calls': 0}
    (folder / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf8')
    print(json.dumps({'receipt': str(folder / 'receipt.json'), 'checks_per_round': [len(r['checks']) for r in rounds],
                      'streak': streak, 'error': error}))
    return 0 if streak == 2 else 1


if __name__ == '__main__':
    raise SystemExit(main())
