# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Two genuine MCP -> approved snapshot -> SQL/Qdrant/ADK/Gemini rounds.

Read-only providers; writes only operator-approved lab corpus snapshots. No
automatic access grants, full repository ingestion or assistant action writes.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
import time
from uuid import uuid4

ROOT = _workspace_root
sys.path.insert(0, str(ROOT / 'app/tools'))
import functional_smoke as api
import mcp_evidence_checks
from publish_mcp_evidence import operator_token, current_feed, publish, document, PublicationBlocked


def hashes():
    paths = list((ROOT / 'app/src').rglob('*.py')) + list((ROOT / 'app/src').rglob('*.sql'))
    paths += [Path(__file__), ROOT/'app/tests/mcp/mcp_evidence_checks.py',
              ROOT/'app/tools/ingestion/publish_mcp_evidence.py', ROOT/'app/infrastructure/compose/runtime/compose.integrations.yaml']
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def round_run(number, token, client, attempt):
    checks = mcp_evidence_checks.run()
    records = []
    attempt.update(number=number,passed=False,checks=checks,records=records)
    def check(name, value):
        assert value, name
        checks.append(name)
    for provider in secrets.SystemRandom().sample(['jira', 'github'], 2):
        feed = current_feed(token)
        current = next(p for p in feed['providers'] if p['id'] == provider)
        check(provider+'_actual_remote_mcp', current['state'] == 'connected'
              and current['complete'] and not current['stale'] and current['items'])
        # Only the pre-existing explicitly scoped test resources, no new remote items.
        item_id = 'KAN-1' if provider == 'jira' else current['items'][0]['id']
        approved = publish(provider, item_id, token)
        question = f'Qual o status registrado do MCP {provider} {item_id}?'
        code, accepted = api.http('/v1/requests', client, {'question': question},
                                  {'Idempotency-Key': str(int(time.time()))+'.'+str(uuid4())})
        check(provider+'_durable_accepted', code == 202)
        rid = accepted['request_id']
        end = time.monotonic()+60
        while time.monotonic()<end:
            code, response = api.http('/v1/requests/'+rid, client)
            if response.get('state') in ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED'):
                break
            time.sleep(.3)
        result = response.get('result') or {}
        records.append({'provider': provider, 'request_id': rid, 'publication': approved,
                        'result': result})
        check(provider+'_workflow_terminal', response['state'] == 'SUCCEEDED')
        check(provider+'_real_grounded_model', result.get('model') == 'gemini-3.5-flash-lite')
        check(provider+'_canonical_answer', result.get('kind') == 'EXTRACTIVE'
              and len(result.get('citations',[])) == 1)
        cite = result['citations'][0]
        check(provider+'_approved_source_only', cite['document_id'] == approved['document_id']
              and cite['content_hash'] == approved['content_hash']
              and cite['quote'] in approved['document']['text']
              and result['text'] == 'Trecho da fonte:\n'+cite['quote'])
        check(provider+'_snapshot_provenance', approved['document']['source_key'] in {
              f'mcp_{provider}_{item_id}'} and 'Snapshot consultado em:' in approved['document']['text'])
        check(provider+'_operator_read_denied', api.http('/v1/requests/'+rid,token)[0] == 403)
        code, catalog = api.http('/v1/lab/corpus',token)
        check(provider+'_still_indexed', code == 200 and approved['document_id'] in {
              d['id'] for d in catalog['documents']})
        check(provider+'_client_publish_denied', api.http('/v1/lab/corpus/documents',client,
              {'expected_generation': catalog['generation'], 'document': approved['document']})[0] == 403)
        print(json.dumps({'round': number, 'provider': provider, 'passed': True}), flush=True)
        time.sleep(12)
    check('operator_cannot_search', api.http('/v1/requests',token,{'question':'MCP'},
          {'Idempotency-Key':str(int(time.time()))+'.'+str(uuid4())})[0] == 403)
    attempt['passed']=True
    return attempt


def main():
    folder=ROOT/'eval/runs'/('mcp-evidence-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(exist_ok=False)
    frozen=hashes()
    result={'card_id':'RAG-04','evidence_type':'real_integration','complete':False,
            'consecutive_passes':0,'sources_sha256':frozen,'criteria_passed':[], 'rounds':[],
            'scope':'operator-approved expiring MCP snapshots in real RAG; not live assistant tools or public production',
            'remote_writes':0,'independent_blind':False}
    attempt={}
    try:
        token=operator_token()
        client=api.http('/v1/lab/session',body={'profile':'ana'})[1]['token']
        for number in (1,2):
            attempt={}
            result['rounds'].append(round_run(number,token,client,attempt))
            assert hashes()==frozen, 'SOURCE_CHANGED_RESET_STREAK'
            (folder/'progress.json').write_text(json.dumps(result,indent=2))
        expiries=[datetime.fromisoformat(record['publication']['document']['valid_until'])
                  for r in result['rounds'] for record in r['records']]
        wait=max(0,(max(expiries)-datetime.now(timezone.utc)).total_seconds()+2)
        assert wait<=182, 'UNBOUNDED_EXPIRY'
        end=time.monotonic()+wait
        while time.monotonic()<end:
            time.sleep(min(2,end-time.monotonic()))
        for r in result['rounds']:
            for record in r['records']:
                code,value=api.http('/v1/requests/'+record['request_id'],client)
                assert code==200 and value['result']['kind']=='ABSTAIN', 'EXPIRED_REMOTE_CITATION_VISIBLE'
                r['checks'].append(record['provider']+'_expiry_reauthorizes_receipt')
        assert hashes()==frozen, 'SOURCE_CHANGED_RESET_STREAK'
        result.update(complete=True,consecutive_passes=2,
                      criteria_passed=['provider_workflow','evidence_gate','authorization','bounded_execution'])
    except Exception as error:
        result['failed_round']=attempt
        result['error_type']=type(error).__name__
        result['error_code']=str(error) if isinstance(error,(AssertionError,PublicationBlocked)) else 'SANITIZED_GATE_ERROR'
    target=folder/'receipt.json'
    target.write_text(json.dumps(result,indent=2))
    print(json.dumps({'complete':result['complete'],'receipt':str(target),'error':result.get('error_code')}),flush=True)
    raise SystemExit(0 if result['complete'] else 1)


if __name__=='__main__':
    main()
