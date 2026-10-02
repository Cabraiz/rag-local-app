# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Populate only synthetic demo identities through the real local API; no cloud.

Receipts are test artifacts, never contain bearer tokens, and preserve failures.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4
import httpx

ROOT=_workspace_root
FILES=['app/src/rag_app/entrypoints/api.py','app/src/rag_app/persistence/catalog.py',
    'app/src/rag_app/integrations/integration_status.py',
    'app/tools/ingestion/demo_seed.py','app/tools/runtime/supervise-demo.ps1',
    'frontend/public/index.html','frontend/public/app.js','frontend/public/styles.css',
    'frontend/nginx.conf','app/infrastructure/compose/runtime/compose.yaml','app/infrastructure/compose/runtime/compose.retrieval.yaml','app/infrastructure/compose/runtime/compose.semantic.yaml']
QUESTIONS={
    'receipts':'Quando devo mandar as notas fiscais ao financeiro?',
    'support':'Em quais dias e horários consigo falar com a assistência?',
    'access':'Quem autoriza a entrada de um funcionário na plataforma?',
    'parking':'Em qual entrada fica a vaga de carro dos convidados?',
    'packages':'Onde deixo os pacotes e qual é o horário limite?',
    'vpn':'O que preciso conectar antes de usar os sistemas fora da empresa?',
    'repair':'Como peço reparo para a máquina de trabalho?',
    'meal':'Qual o teto de gastos com comida durante uma viagem?'}


def fingerprints():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in FILES}


def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:6]
    folder=ROOT/'eval'/'runs'/('populated-demo-'+stamp);folder.mkdir(parents=True)
    receipt=dict(schema=1,scope='synthetic_populated_local_demo',cloud_calls=0,
        independent_blind=False,production_ready=False,source_hashes=fingerprints(),
        seeds={},rounds=[],passed=False)
    def record():
        (folder/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    try:
        with httpx.Client(base_url='http://127.0.0.1:8840',timeout=25,trust_env=False,follow_redirects=False) as client:
            def call(method,path,tenant=None,body=None,key=None):
                headers={} if tenant is None else {'Authorization':'Bearer '+tokens[tenant]}
                if key:headers['Idempotency-Key']=key
                response=client.request(method,path,json=body,headers=headers)
                if response.status_code>=400:raise RuntimeError(f'{method} {path}: HTTP {response.status_code}')
                return response.json()
            tokens={tenant:call('POST','/v1/lab/session',body={'tenant':tenant})['token'] for tenant in ('demo-a','demo-b')}
            dataset=json.loads((ROOT/'eval/datasets/semantic-calibration-v1.json').read_text(encoding='utf8'))
            corpus={}
            for tenant in tokens:
                docs=[dict(doc,source_key='visible_demo_v1_'+doc['source_key'],media_type='text/plain',valid_until=None) for doc in dataset['documents']]
                if tenant=='demo-b':docs[0]['text']=docs[0]['text'].replace('45 reais','20 reais')
                release=call('POST','/v1/lab/corpus/releases',tenant,{'documents':docs})
                corpus[tenant]=call('GET','/v1/lab/corpus',tenant)
                receipt['seeds'][tenant]=dict(release=release,documents=corpus[tenant]['documents'])
            for number in (1,2):
                checks=[];round_receipt=dict(number=number,checks=checks,queries=[],passed=False);receipt['rounds'].append(round_receipt)
                def check(name,condition):
                    checks.append(dict(name=name,passed=bool(condition)));record()
                    if not condition:raise AssertionError(name)
                check('catalog_requires_identity',client.get('/v1/lab/corpus').status_code in (401,403))
                check('integration_requires_identity',client.get('/v1/lab/integrations').status_code in (401,403))
                integrations=call('GET','/v1/lab/integrations','demo-a')
                providers={p['id']:p for p in integrations['providers']}
                check('integration_local_metadata_only',integrations['verification']=='local_wiring_only' and integrations['external_calls']==0)
                check('jira_adapter_isolated',providers['jira']['adapter_present'] and not providers['jira']['rag_connected'])
                check('github_adapter_not_implemented',not providers['github']['adapter_present'] and not providers['github']['rag_connected'])
                check('online_health_not_claimed',all(p['online_health']=='not_checked' for p in providers.values()))
                check('integration_no_store',client.get('/v1/lab/integrations',headers={'Authorization':'Bearer '+tokens['demo-a']}).headers.get('cache-control')=='no-store')
                check('catalog_no_store',client.get('/v1/lab/corpus',headers={'Authorization':'Bearer '+tokens['demo-a']}).headers.get('cache-control')=='no-store')
                check('distinct_release_and_document_ids',corpus['demo-a']['release_id']!=corpus['demo-b']['release_id'] and not ({d['id'] for d in corpus['demo-a']['documents']}&{d['id'] for d in corpus['demo-b']['documents']}))
                last_a=None
                for tenant in tokens:
                    check(tenant+'_eight_actual_documents',len(corpus[tenant]['documents'])==8)
                    check(tenant+'_offline_neural_index',corpus[tenant]['embedding_version'].startswith('minilm-'))
                    bykey={d['source_key'].removeprefix('visible_demo_v1_'):d for d in corpus[tenant]['documents']}
                    cases=[(None,'Qual é o orçamento aprovado para hotel?'),(None,'Qual é a senha da VPN?')]+list(QUESTIONS.items())
                    for source,question in cases:
                        key=f'{int(time.time())}.{uuid4()}'
                        accepted=call('POST','/v1/requests',tenant,{'question':question},key)
                        rid=accepted['request_id'];deadline=time.monotonic()+90
                        while True:
                            row=call('GET','/v1/requests/'+rid,tenant)
                            if row['state'] in ('SUCCEEDED','FAILED_FINAL','CANCELLED','EXPIRED'):break
                            if time.monotonic()>deadline:raise TimeoutError('Worker deadline '+rid)
                            time.sleep(.5)
                        round_receipt['queries'].append(dict(tenant=tenant,question=question,expected_source=source,row=row));record()
                        check(tenant+'_'+(source or question)+'_terminal',row['state']=='SUCCEEDED')
                        if source is None:
                            check(tenant+'_'+question+'_abstain',row['result']['kind']=='ABSTAIN' and not row['result']['citations'])
                        else:
                            cites=row['result']['citations'];doc=bykey[source]
                            check(tenant+'_'+source+'_canonical_citation',row['result']['kind']=='EXTRACTIVE' and len(cites)==1 and cites[0]['document_id']==doc['id'] and cites[0]['release_id']==corpus[tenant]['release_id'] and cites[0]['quote'] in [c['quote'] for c in doc['chunks']] and row['result']['text']=='Trecho da fonte:\n'+cites[0]['quote'])
                        replay=call('POST','/v1/requests',tenant,{'question':question},key)
                        check(tenant+'_'+(source or question)+'_idempotent',replay['request_id']==rid)
                        if tenant=='demo-a':last_a=rid
                    meal=round_receipt['queries'][-1]['row']['result']['text']
                    check(tenant+'_distinct_budget',('45 reais' if tenant=='demo-a' else '20 reais') in meal)
                    history=call('GET','/v1/requests',tenant)
                    check(tenant+'_history_includes_latest_receipt',any(r['id']==rid for r in history))
                    check(tenant+'_private_request_fields_absent',all('question' not in r and 'source_snapshot' not in r for r in history))
                check('cross_tenant_receipt_denied',client.get('/v1/requests/'+last_a,headers={'Authorization':'Bearer '+tokens['demo-b']}).status_code==404)
                check('files_unchanged',fingerprints()==receipt['source_hashes'])
                round_receipt['passed']=True;record()
                print(f'Rodada {number}: {len(checks)} checks, 20 consultas reais, aprovada.',flush=True)
            receipt['passed']=True;record()
    except Exception as error:
        receipt['error']=str(error);record();print('FALHOU: '+str(error),flush=True);raise SystemExit(1)
    finally:print('Receipt: '+str(folder/'receipt.json'),flush=True)


if __name__=='__main__':main()
