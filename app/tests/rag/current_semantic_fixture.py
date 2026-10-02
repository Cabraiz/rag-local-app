# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Frozen existing oracles, current clients, real offline semantic QA. No fitting."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import secrets
import time
from uuid import UUID, uuid5

import current_queue_fixture as qa
import http_fixture as base

base.COMPOSE += ['-f', str(base.ROOT/'infrastructure/compose/runtime/compose.semantic.yaml')]
DATA=base.ROOT.parent/'eval/datasets'
DATASETS=['semantic-calibration-v1.json','semantic-holdout-v1.json','semantic-holdout-v2.json']


def frozen():
    result=qa.frozen()
    for path in [Path(__file__),base.ROOT/'infrastructure/compose/runtime/compose.semantic.yaml',*[DATA/name for name in DATASETS]]:
        result[str(path.relative_to(base.ROOT.parent))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def seed(bundle):
    return qa.script('try:\n r=corpus.ingest(Identity("demo-a","demo-user"),'+repr(bundle)+')\n print(json.dumps([201,r]))\nexcept RequestError as e:\n print(json.dumps([e.status,{"code":e.code}]))')


def wait_model():
    end=time.monotonic()+120
    while time.monotonic()<end:
        ready=qa.script("import httpx\ntry:\n print(json.dumps(httpx.get('http://embeddings:8000/health/ready',trust_env=False,timeout=3).status_code==200))\nexcept httpx.HTTPError:\n print(json.dumps(False))")
        if ready:return
        time.sleep(1)
    raise AssertionError('OFFLINE_MODEL_DEADLINE')


def run_round(number,folder):
    checks=[];cases_result=[]
    def check(name,ok):
        checks.append(dict(name=name,passed=bool(ok)))
        (folder/f'progress-{number}.json').write_text(json.dumps(dict(checks=checks,cases=cases_result),indent=2),encoding='utf8')
        assert ok,name
    calibration=json.loads((DATA/DATASETS[0]).read_text(encoding='utf8'))
    cases=json.loads((DATA/DATASETS[1]).read_text(encoding='utf8'))['cases']+json.loads((DATA/DATASETS[2]).read_text(encoding='utf8'))['cases']
    marker='sem'+str(number)+'_'
    docs=[dict(d,source_key=marker+d['source_key'],media_type='text/plain',valid_until=None) for d in calibration['documents']]
    ana=base.http('/v1/lab/session',body={'profile':'ana'})[1]['token']
    bruno=base.http('/v1/lab/session',body={'profile':'bruno'})[1]['token']
    collection_names=lambda: qa.script("print(json.dumps([c['name'] for c in corpus.QdrantAdapter().call('GET','/collections')['result']['collections']]))")
    before=set(collection_names())
    status,release=seed(docs)
    check('privileged_neural_seed_READY',status==201 and release['state']=='READY')
    name,dimension,neural=qa.script('print(json.dumps(corpus.release_settings('+repr(release['release_id'])+')))')
    check('neural_family_384',dimension==384 and neural is True and name.startswith('rgl_shared_neural_'))
    schema=qa.script('print(json.dumps(corpus.QdrantAdapter().call("GET","/collections/'+name+'")["result"]))')
    check('named_dense_sparse_real_index',schema['config']['params']['vectors']['dense']['size']==384 and 'sparse' in schema['config']['params']['sparse_vectors'])
    check('tenant_actor_release_indexes',{'tenant','actor','release_id'}<=set(schema['payload_schema']))
    gates=qa.script("from rag_app import neural_client\nimport httpx\nr=httpx.post('http://embeddings:8000/v1/embeddings',json={'texts':['controle sintetico']},trust_env=False,timeout=10).json()\nneural_client.validate(r,1)\nr.pop('vectors');print(json.dumps(r))")
    check('calibration_pinned_not_holdout',gates['calibration_sha256']==hashlib.sha256((DATA/DATASETS[0]).read_bytes()).hexdigest() and gates['holdout_used'] is False and gates['threshold']==.3 and gates['margin']==.05 and gates['false_accepts']==0)
    order=list(range(len(cases)));random.Random(number).shuffle(order)
    def answer(query):
        status,value=base.http('/v1/requests',ana,{'question':query},{'Idempotency-Key':base.key()})
        assert status==202,'HTTP_ACCEPTANCE'
        return base.wait_state(value['request_id'],ana,{'SUCCEEDED'})['result'],value['request_id']
    for i in order:
        c=cases[i];result,rid=answer(c['question']);expected=c['expected']
        if expected is None:ok=result['kind']=='ABSTAIN' and result['citations']==[]
        else:
            doc=next(d for d in docs if d['source_key']==marker+expected)
            did=str(uuid5(UUID(release['release_id']),doc['source_key']))
            citation=dict(document_id=did,chunk_id=str(uuid5(UUID(did),'0')),release_id=release['release_id'],content_hash=hashlib.sha256(doc['text'].encode()).hexdigest(),acl_epoch=0,quote=doc['text'])
            ok=result['kind']=='EXTRACTIVE' and result['text']=='Trecho da fonte:\n'+doc['text'] and result['citations']==[citation]
        cases_result.append(dict(case=i,expected=expected,passed=ok,observed_kind=result['kind'],request_id=rid))
        check('frozen_reserved_case_'+str(i),ok)
    check('operator_cannot_query',base.http('/v1/requests',bruno,{'question':'teste'},{'Idempotency-Key':base.key()})[0]==403)
    for tenant,actor in [('other-tenant','demo-user'),('demo-a','other-actor')]:
        request=dict(tenant=tenant,actor=actor,source_snapshot=release['release_id'],question='Qual e o limite de alimentacao?')
        check('SQL_and_Qdrant_isolation_'+tenant+'_'+actor,qa.script('print(json.dumps(corpus.retrieve('+repr(request)+')==[]))'))
    changed=[dict(docs[0],source_key=marker+'recovery',text='Orcamento offline de alimentacao: 73 reais.')]
    base.docker('stop','embeddings')
    try:
        status,error=seed(changed)
        check('real_model_outage_fails_closed',status==503 and error['code']=='EMBEDDING_UNAVAILABLE')
        head=qa.script("with ledger.connect() as db:\n print(json.dumps(str(db.execute(\"SELECT release_id FROM corpus_heads WHERE tenant='demo-a' AND actor='demo-user'\").fetchone()['release_id'])))")
        check('outage_preserves_READY',head==release['release_id'])
    finally:
        base.docker('start','embeddings');wait_model()
    check('same_candidate_recovers',seed(changed)[0]==201)
    conflict=[docs[0],dict(docs[0],source_key=marker+'conflict',text=docs[0]['text'].replace('45 reais','59 reais'))]
    check('conflict_seed',seed(conflict)[0]==201)
    result,_=answer('Qual e o limite autorizado para alimentacao?')
    check('conflicting_evidence_abstains',result['kind']=='ABSTAIN' and result['citations']==[])
    _,final=seed(docs)
    result,rid=answer('Qual o teto de gastos com comida durante uma viagem?')
    did=str(uuid5(UUID(final['release_id']),marker+'meal'))
    check('source_revocation_commits',base.http('/v1/lab/documents/'+did+'/revoke-source',bruno,{})[0]==200)
    view=base.http('/v1/requests/'+rid,ana)
    check('citation_revalidated_after_revocation',result['kind']=='EXTRACTIVE' and view[0]==200 and view[1]['result']['kind']=='ABSTAIN')
    check('source_tombstone_blocks_seed',seed(docs)[0]==409)
    after=set(collection_names())
    check('multiple_releases_one_shared_collection',after-before<={name} and before<=after and not any(c.startswith('rgl_') and not c.startswith('rgl_shared_') for c in after))
    check('unknown_layout_fails_closed',qa.script("try:\n corpus.collection_name('00000000-0000-0000-0000-000000000001','invalid')\n print(json.dumps(False))\nexcept RequestError as e:\n print(json.dumps(e.code=='UNKNOWN_INDEX_LAYOUT'))"))
    check('no_synthetic_input_in_logs',marker not in base.docker('logs','--no-log-prefix','api','worker','embeddings').stdout)
    qa.live();check('live_lab_preserved',True)
    return dict(seed=number,checks=checks,reserved_cases=cases_result,passed=True)


def main():
    assert not base.docker('ps','--status','running','-q').stdout.strip(),'QA_RUNNING'
    qa.live()
    folder=base.ROOT.parent/'eval/runs'/('current-semantic-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3));folder.mkdir(parents=True)
    sources=frozen();seeds=[secrets.randbits(32) for _ in range(2)]
    contract=dict(independent_blind=False,oracle_unchanged=True,holdout_fitting=False,cloud_calls=0,seeds=seeds,project=qa.PROJECT,sources_sha256=sources)
    (folder/'contract.json').write_text(json.dumps(contract,indent=2),encoding='utf8')
    rounds=[];error=None;images=[]
    try:
        base.docker('up','-d','--wait','--wait-timeout','150');qa.wait_index();wait_model();images=base.service_image_ids()
        for number in seeds:
            assert frozen()==sources and base.service_image_ids()==images,'SOURCE_OR_IMAGE_CHANGED'
            result=run_round(number,folder);rounds.append(result)
            (folder/f'round-{number}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
            print(json.dumps(dict(checks=len(result['checks']),reserved=len(result['reserved_cases']),streak=len(rounds))),flush=True)
        assert frozen()==sources and base.service_image_ids()==images,'SOURCE_OR_IMAGE_CHANGED'
    except Exception as exc:error=type(exc).__name__+': '+str(exc)[:500]
    finally:
        stopped=base.docker('stop',check=False).returncode==0;qa.live()
        complete=not error and len(rounds)==2 and stopped
        receipt=dict(card_id='RAG-06',evidence_type='real_integration',complete=complete,consecutive_passes=2 if complete else 0,
            criteria_passed=['neural_embeddings','hybrid_retrieval','portuguese_holdout','abstention'] if complete else [],
            sources_sha256=sources,images=images,contract=contract,rounds=rounds,error=error,gate_b_passed=False,volumes_preserved=True)
        path=folder/'receipt.json';path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
        for card,name in [('BUG-035','shared-index-receipt.json'),('BUG-037','repair-regression-receipt.json')]:
            value=dict(receipt,card_id=card,evidence_type='verified_regression',criteria_passed=['reproduction','two_regression_rounds'] if complete else [])
            (folder/name).write_text(json.dumps(value,indent=2),encoding='utf8')
    print(json.dumps(dict(receipt=str(path),complete=complete,error=error)),flush=True)
    raise SystemExit(0 if complete else 1)


if __name__=='__main__':main()
