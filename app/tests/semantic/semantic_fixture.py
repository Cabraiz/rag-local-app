# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Real offline ONNX + SQL + hybrid Qdrant + ADK HTTP slice, not independent QA.

Reserved questions never select the threshold. Only owns initially stopped lab
containers; preserves all sources, volumes, prior failures and synthetic snapshots.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import random
import secrets
import time
from uuid import UUID,uuid5
import http_fixture as base
import rag_fixture as rag

base.COMPOSE += ['-f',str(base.ROOT/'infrastructure/compose/runtime/compose.semantic.yaml')]
PROJECT=base.ROOT.parent
ACTIVE_FOLDER=None
CALIBRATION=PROJECT/'eval/datasets/semantic-calibration-v1.json'
HOLDOUT=PROJECT/'eval/datasets/semantic-holdout-v1.json'
FRESH=PROJECT/'eval/datasets/semantic-holdout-v2.json'
VERSION='minilm-multilingual-faf4aa4225822f3bc6376869cb1164e8e3feedd0-384-v1'
COLLECTION='rgl_shared_neural_384_minilm_faf4aa4_v1'

def frozen():
    values=rag.frozen()
    paths=[Path(__file__),CALIBRATION,HOLDOUT,FRESH,PROJECT/'docs/architecture/contracts/semantic-contract.json',
           base.ROOT/'infrastructure/compose/runtime/compose.semantic.yaml',base.ROOT/'.dockerignore',PROJECT/'.gitignore']
    paths += [p for p in (base.ROOT/'semantic').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    for p in paths: values[str(p.relative_to(PROJECT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return values

def wait_model():
    end=time.monotonic()+60
    while time.monotonic()<end:
        try:
            result=rag.script("import httpx\nprint(json.dumps(httpx.get('http://embeddings:8000/health/ready',timeout=3,trust_env=False).json()))")
            if result==dict(model_version=VERSION,offline=True): return
        except Exception: pass
        time.sleep(.5)
    raise AssertionError('Owned offline embedding service not ready')

def run_round(seed):
    checks=[]; ids=[]; reserved=[]
    def check(name,ok):
        checks.append(dict(name=name,passed=bool(ok)))
        (ACTIVE_FOLDER/f'progress-{seed}.json').write_text(json.dumps(dict(checks=checks,reserved=reserved,ids=ids),indent=2),encoding='utf8')
        if not ok: raise AssertionError(name)
    a=base.http('/v1/lab/session',body={'tenant':'demo-a'})[1]['token']
    b=base.http('/v1/lab/session',body={'tenant':'demo-b'})[1]['token']
    marker='sem'+str(seed)+'_'
    calibration=json.loads(CALIBRATION.read_text(encoding='utf8'))
    cases=json.loads(HOLDOUT.read_text(encoding='utf8'))['cases']+json.loads(FRESH.read_text(encoding='utf8'))['cases']
    docs=[dict(d,source_key=marker+d['source_key'],media_type='text/plain',valid_until=None) for d in calibration['documents']]
    def ingest(bundle):
        for attempt in range(3):
            c,r=base.http('/v1/lab/corpus/releases',a,{'documents':bundle})
            if c!=503 or r.get('code')!='INDEX_UNAVAILABLE': return c,r
            if attempt<2: time.sleep(2)
        return c,r
    def answer(question,token=a):
        c,r=base.http('/v1/requests',token,{'question':question},{'Idempotency-Key':base.key()})
        assert c==202,(c,r)
        ids.append(r['request_id'])
        row=base.wait_state(r['request_id'],token,{'SUCCEEDED'})
        return row['result'],r['request_id']
    c,release=ingest(docs)
    check('real_neural_release_READY',c==201 and release['state']=='READY')
    settings=rag.script("print(json.dumps(corpus.release_settings("+repr(release['release_id'])+")))")
    check('neural_family_384_not_lexical_256',settings==[COLLECTION,384,True])
    schema=rag.script("r=corpus.QdrantAdapter().call('GET','/collections/"+COLLECTION+"')['result']\nprint(json.dumps(dict(params=r['config']['params'],payload_schema=r['payload_schema'])))")
    check('real_named_dense_and_sparse_hybrid_index',schema['params']['vectors']['dense']['size']==384 and 'sparse' in schema['params']['sparse_vectors'])
    check('tenant_actor_release_payload_indexes',{'tenant','actor','release_id'}<=set(schema['payload_schema']))
    # Actual inference of calibration examples, separate from reserved questions.
    diagnostic=rag.script("from rag_app import neural_client,semantic_policy\nd="+repr(calibration)+"\nv,t,m=neural_client.embed([r['title']+' '+r['text'] for r in d['documents']]+[semantic_policy.retrieval_query(r['question']) for r in d['cases']])\nresults=[]\nfor c,q in zip(d['cases'],v[8:]):\n s=sorted([(sum(a*b for a,b in zip(q,w)),r['source_key']) for r,w in zip(d['documents'],v[:8]) if semantic_policy.eligible(c['question'],r['text'])],reverse=True)\n p=s[0][1] if s and s[0][0]>=t and (len(s)<2 or s[0][0]-s[1][0]>=m) else None\n results.append(p==c['expected'])\nprint(json.dumps(dict(results=results,threshold=t,margin=m)))")
    check('calibration_conditional_and_approval_regression',all(diagnostic['results']) and len(diagnostic['results'])==12)
    order=list(range(len(cases))); random.Random(seed).shuffle(order)
    for i in order:
        case=cases[i]; result,rid=answer(case['question'])
        expected=case['expected']
        if expected is None:
            ok=result['kind']=='ABSTAIN' and result['citations']==[]
        else:
            doc=next(d for d in docs if d['source_key']==marker+expected)
            did=str(uuid5(UUID(release['release_id']),marker+expected))
            citation=dict(document_id=did,chunk_id=str(uuid5(UUID(did),'0')),release_id=release['release_id'],content_hash=hashlib.sha256(doc['text'].encode()).hexdigest(),acl_epoch=0,quote=doc['text'])
            ok=result['kind']=='EXTRACTIVE' and result['text']=='Trecho da fonte:\n'+doc['text'] and result['citations']==[citation]
        reserved.append(dict(case=i,dataset='known_regression_v1' if i<14 else 'new_authored_v2',expected_source=expected,observed_kind=result['kind'],passed=ok,request_id=rid))
        check('reserved_case_'+str(i),ok)
    check('cross_tenant_cannot_read_answer',base.http('/v1/requests/'+ids[0],b)[0]==404)
    check('canonical_actor_isolation',rag.script("r=dict(tenant='demo-a',actor='foreign-actor',source_snapshot="+repr(release['release_id'])+",question='Qual é o limite autorizado para alimentação?')\nprint(json.dumps(corpus.retrieve(r)==[]))"))
    family=rag.script("with ledger.connect() as db:\n r=db.execute(\"SELECT id FROM corpus_releases WHERE index_layout='shared_lexical_v1' AND state='READY' LIMIT 1\").fetchone()\nprint(json.dumps(corpus.release_settings(r['id']) if r else None))")
    check('prior_lexical_snapshot_keeps_256_family',family==['rgl_shared_lexical_256_v1',256,False])
    gates=rag.script("from rag_app import neural_client\nimport httpx\nx=httpx.post('http://embeddings:8000/v1/embeddings',json={'texts':['Sintético local']},trust_env=False,timeout=10).json()\nneural_client.validate(x,1)\nx.pop('vectors'); print(json.dumps(x))")
    check('pinned_calibration_and_no_holdout_training',gates['model_version']==VERSION and gates['calibration_sha256']==hashlib.sha256(CALIBRATION.read_bytes()).hexdigest() and gates['holdout_used'] is False and gates['false_accepts']==0)
    network=json.loads(base.docker('config','--format','json').stdout)
    svc=network['services']['embeddings']
    check('model_internal_network_no_host_ports_no_secrets',set(svc['networks'])=={'data'} and network['networks']['data']['internal'] is True and not svc.get('ports') and not svc.get('secrets'))
    flags=rag.script("import httpx\nb='http://embeddings:8000/v1/embeddings'\nresults=[]\nwith httpx.Client(trust_env=False,timeout=10) as c:\n for payload in ({'texts':[]},{'texts':['x']*33},{'texts':['private-'+"+repr(marker)+"],'url':'https://example.com'},{'texts':['x'*2049]}):\n  r=c.post(b,json=payload); results.append(r.status_code==422 and r.json()=={'code':'INVALID_EMBEDDING_INPUT'})\n r=c.post(b,content=b'x'*65537); results.append(r.status_code==413 and r.json()=={'code':'EMBEDDING_BODY_LIMIT'})\nprint(json.dumps(results))")
    check('bounded_RPC_rejects_extras_and_payloads_without_echo',len(flags)==5 and all(flags))
    invalid=rag.script("from rag_app import neural_client\nimport httpx,copy\nx=httpx.post('http://embeddings:8000/v1/embeddings',json={'texts':['controle sintético']},trust_env=False,timeout=10).json()\nchecks=[]\nfor key,value in [('model_version','foreign'),('calibration_sha256','bad'),('policy_sha256','bad'),('holdout_used',True),('vectors',[[0.0]*384]),('vectors',[[float('nan')]*384]),('vectors',[]),('threshold',float('inf'))]:\n bad=copy.deepcopy(x); bad[key]=value\n try: neural_client.validate(bad,1); checks.append(False)\n except RequestError as e: checks.append(e.code=='EMBEDDING_RESPONSE_INVALID' and e.status==503)\nprint(json.dumps(checks))")
    check('malformed_model_outputs_fail_closed_negative_controls',len(invalid)==8 and all(invalid))
    changed=[dict(docs[0],source_key=marker+'offline',text='Orçamento de alimentação offline controlado: 73 reais.')]
    base.docker('stop','embeddings')
    try:
        c,r=ingest(changed)
        check('real_model_outage_503_no_lexical_fallback',c==503 and r['code']=='EMBEDDING_UNAVAILABLE')
        head=rag.script("with ledger.connect() as db:\n r=db.execute(\"SELECT release_id FROM corpus_heads WHERE tenant='demo-a' AND actor='demo-user'\").fetchone()\nprint(json.dumps(str(r['release_id'])))")
        check('failed_neural_ingest_preserves_current_READY',head==release['release_id'])
    finally:
        base.docker('start','embeddings'); wait_model()
    c,recovery=ingest(changed)
    check('offline_model_recovers_same_failed_candidate',c==201 and recovery['state']=='READY')
    c,_=ingest([docs[0],dict(docs[0],source_key=marker+'conflict',text=docs[0]['text'].replace('45 reais','59 reais'))])
    result,_=answer('Qual é o limite autorizado para alimentação?')
    check('equivalent_conflicting_evidence_abstains',c==201 and result['kind']=='ABSTAIN' and result['citations']==[])
    c,final=ingest(docs)
    result,rid=answer('Qual o teto de gastos com comida durante uma viagem?')
    did=str(uuid5(UUID(final['release_id']),marker+'meal'))
    revoked=base.http('/v1/lab/documents/'+did+'/revoke-source',a,{})
    view=base.http('/v1/requests/'+rid,a)
    check('semantic_citations_revalidated_after_source_revocation',c==201 and result['kind']=='EXTRACTIVE' and revoked[0]==200 and view[0]==200 and view[1]['result']['kind']=='ABSTAIN')
    check('source_tombstone_blocks_reingest',ingest(docs)[0]==409)
    logs=base.docker('logs','--no-log-prefix','api','worker','embeddings').stdout
    check('no_synthetic_corpus_or_invalid_input_in_logs',marker not in logs and 'private-'+marker not in logs)
    return dict(seed=seed,checks=checks,reserved_cases=reserved,calibration=diagnostic,passed=True,accepted=len(ids))

def main():
    global ACTIVE_FOLDER
    if base.docker('ps','--status','running','-q').stdout.strip(): raise SystemExit('Project already running: refuse ownership')
    folder=PROJECT/'eval/runs'/('semantic-real-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True); ACTIVE_FOLDER=folder
    sources=frozen(); seeds=[secrets.randbits(32) for _ in range(2)]
    contract=dict(scope='real_offline_ONNX_CPU_SQL_Qdrant_hybrid_ADK_HTTP_authored_Portuguese',independent_blind=False,oracle_frozen_before_inputs=True,threshold_selected_from_calibration_only=True,v1_is_known_regression_after_BUG_037=True,v2_new_authored_evaluation=True,seeds=seeds,sources_sha256=sources)
    (folder/'contract.json').write_text(json.dumps(contract,indent=2),encoding='utf8')
    rounds=[]; error=None; images=None
    try:
        base.docker('up','-d','--wait','--wait-timeout','120'); rag.wait_index_ready('semantic_initial'); wait_model()
        images=rag.images(); contract['images']=images
        for seed in seeds:
            assert frozen()==sources and rag.images()==images,'Changed sources/images reset streak'
            result=run_round(seed); rounds.append(result)
            (folder/f'round-{seed}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
            print(json.dumps(dict(checks=len(result['checks']),streak=len(rounds),reserved_cases=len(result['reserved_cases']))),flush=True)
        assert frozen()==sources and rag.images()==images
    except Exception as exc: error=str(exc)
    finally:
        stopped=base.docker('stop',check=False); passed=len(rounds)==2 and error is None
        receipt=dict(card_id='RAG-06',evidence_type='real_integration',complete=passed,consecutive_passes=2 if passed else 0,
            criteria_passed=['neural_embeddings','hybrid_retrieval','portuguese_holdout','abstention'] if passed else [],sources_sha256=sources,contract=contract,rounds=rounds,error=error,containers_stopped=stopped.returncode==0,volumes_preserved=True,production_passed=False)
        path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
        print(json.dumps(dict(receipt=str(path),error=error,streak=receipt['consecutive_passes'])),flush=True)
    raise SystemExit(0 if passed else 1)

if __name__=='__main__': main()
