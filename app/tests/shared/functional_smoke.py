# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Non-disruptive smoke of the running seeded lab, not load or production QA."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import time
import urllib.error
import urllib.request
from uuid import uuid4

ROOT = _workspace_root
BASE = 'http://127.0.0.1:8840'
QUESTION = 'Qual o teto de gastos com comida durante uma viagem?'
UNKNOWN = 'Qual é o orçamento aprovado para hotel?'
QUOTE = ('O orçamento aprovado para refeições é de 45 reais por pessoa. '
         'Esse valor é o limite de alimentação durante viagens.')


def http(path, token=None, body=None, headers=None):
    headers = dict(headers or {})
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(BASE + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.status, json.loads(response.read(1024 * 1024))
    except urllib.error.HTTPError as error:
        try:
            value = json.loads(error.read(16384))
        except ValueError:
            value = {'code': 'HTTP_ERROR'}
        return error.code, value


def wait_terminal(rid, token):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        code, row = http('/v1/requests/' + rid, token)
        if code == 200 and row['state'] in {'SUCCEEDED', 'FAILED_FINAL', 'EXPIRED', 'CANCELLED'}:
            return row
        time.sleep(.2)
    raise AssertionError('terminal_not_reached')


def run_round():
    checks, ids = [], []
    def check(name, passed):
        assert passed, name
        checks.append(name)
    check('health_live_and_not_false_production', http('/health/ready')[1]['production_ready'] is False)
    check('unauthenticated_request_denied', http('/v1/requests')[0] in (401, 403))
    a = http('/v1/lab/session', body={'tenant': 'demo-a'})[1]['token']
    b = http('/v1/lab/session', body={'tenant': 'demo-b'})[1]['token']
    code, catalog = http('/v1/lab/corpus', a)
    food = [d for d in catalog['documents'] if d['title'] == 'Alimentação']
    check('existing_seed_not_replaced', code == 200 and len(food) == 1
          and any(c['quote'] == QUOTE for c in food[0]['chunks']))
    idem = str(int(time.time())) + '.' + str(uuid4())
    def submit_duplicate(_):
        return http('/v1/requests', a, {'question': QUESTION}, {'Idempotency-Key': idem})
    with ThreadPoolExecutor(max_workers=3) as executor:
        responses = list(executor.map(submit_duplicate, range(3)))
    check('concurrent_submit_same_durable_receipt', all(c == 202 for c, _ in responses)
          and len({r['request_id'] for _, r in responses}) == 1)
    rid = responses[0][1]['request_id']; ids.append(rid)
    check('different_question_same_key_conflicts', http('/v1/requests', a,
          {'question': UNKNOWN}, {'Idempotency-Key': idem})[0] == 409)
    check('receipt_recovered_by_idempotency_key', http('/v1/requests/resolve', a, {},
          {'Idempotency-Key': idem})[1]['request_id'] == rid)
    check('other_tenant_cannot_read_receipt', http('/v1/requests/' + rid, b)[0] == 404)
    row = wait_terminal(rid, a)
    result = row.get('result') or {}
    check('real_worker_finished_extractive', row['state'] == 'SUCCEEDED' and result.get('kind') == 'EXTRACTIVE')
    cites = result.get('citations', [])
    check('answer_exactly_supported_by_source', result.get('text') == 'Trecho da fonte:\n' + QUOTE)
    check('canonical_version_and_hash_in_citation', len(cites) == 1
          and cites[0]['document_id'] == food[0]['id'] and cites[0]['release_id'] == catalog['release_id']
          and cites[0]['quote'] == QUOTE and cites[0]['content_hash'] == hashlib.sha256(QUOTE.encode()).hexdigest())
    check('other_tenant_cannot_revoke_source', http('/v1/lab/documents/' + food[0]['id'] + '/revoke', b, {})[0] == 404)
    code, missing = http('/v1/requests', a, {'question': UNKNOWN},
          {'Idempotency-Key': str(int(time.time())) + '.' + str(uuid4())})
    check('unsupported_question_registered', code == 202)
    unknown_id = missing['request_id']; ids.append(unknown_id)
    unknown = wait_terminal(unknown_id, a)
    check('unsupported_question_abstains_without_fake_citations', unknown['state'] == 'SUCCEEDED'
          and unknown['result']['kind'] == 'ABSTAIN' and unknown['result']['citations'] == [])
    code, history = http('/v1/requests', a)
    check('accepted_requests_remain_visible', code == 200 and set(ids) <= {r['id'] for r in history})
    integrations = http('/v1/lab/integrations', a)[1]
    check('unconnected_providers_not_misrepresented', integrations['external_calls'] == 0
          and all(p['rag_connected'] is False for p in integrations['providers']))
    return {'checks': checks, 'request_ids': ids, 'passed': True}


def main():
    folder = ROOT / 'eval/runs' / ('functional-smoke-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                                + '-' + secrets.token_hex(3))
    folder.mkdir(parents=True)
    rows, error, streak = [], None, 0
    try:
        for _ in range(2):
            rows.append(run_round()); streak += 1
    except Exception as failure:
        error = {'type': type(failure).__name__, 'reason': str(failure)[:160]}; streak = 0
    receipt = {'scope': 'existing_synthetic_lab_real_HTTP_ADK_Postgres_Qdrant', 'rounds': rows,
               'error': error, 'consecutive_passes': streak, 'independent_blind': False,
               'corpus_replaced': False, 'services_stopped': False, 'remote_providers_tested': False,
               'production_ready': False, 'load_100k_proven': False}
    (folder / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf8')
    print(json.dumps({'receipt': str(folder / 'receipt.json'), 'checks_per_round': [len(r['checks']) for r in rows],
                      'streak': streak, 'error': error}))
    return 0 if streak == 2 else 1


if __name__ == '__main__':
    raise SystemExit(main())
