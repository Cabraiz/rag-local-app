# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Two frozen rounds: PCA oracle, malformed/index scope guards and real API."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import secrets
from types import SimpleNamespace
from unittest.mock import patch
import functional_smoke as smoke
from rag_app import embedding_view as view
from rag_app.domain import Identity, RequestError

ROOT = _workspace_root
FILES = ['app/src/rag_app/retrieval/embedding_view.py', 'app/src/rag_app/entrypoints/api.py',
         'frontend/public/embeddings.js', 'frontend/public/app.js',
         'frontend/public/index.html', 'frontend/public/styles.css',
         'app/tests/semantic/embedding_view_checks.py', 'eval/runs/embedding-view-20261001/acceptance.json']
smoke.BASE = os.environ.get('RAG_EMBEDDING_TEST_BASE', smoke.BASE)


def frozen():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in FILES}


def round_checks(seed):
    checks=[]
    def check(name, condition):
        assert condition, name
        checks.append(name)
    def denied(name, action):
        try: action()
        except RequestError: checks.append(name)
        else: raise AssertionError(name)
    rows = [[1.,0,0,0],[-1.,0,0,0],[0,1.,0,0],[0,-1.,0,0],[0,0,1.,0],[0,0,-1.,0]]
    mean,axes,retained,ratios = view.pca(rows)
    check('rank3_retains_all_variance', abs(retained-1)<1e-7)
    for i,a in enumerate(axes):
        check('unit_axis_'+str(i), abs(view.dot(a,a)-1)<1e-7)
        for j,b in enumerate(axes[:i]): check('orthogonal_axes_'+str(i)+str(j),abs(view.dot(a,b))<1e-7)
    projected=[view.project(v,mean,axes) for v in rows]
    check('PCA_preserves_rank3_pair_distances', all(abs(sum((x-y)**2 for x,y in zip(a,b))-sum((x-y)**2 for x,y in zip(c,d)))<1e-6 for a,c in zip(rows,projected) for b,d in zip(rows,projected)))
    check('singleton_projection_is_zero',view.project(rows[0],*view.pca([rows[0]])[:2])==[0,0,0])
    check('duplicate_projection_is_zero',view.pca([rows[0]]*3)[2]==0)
    rng=random.Random(seed)
    random_rows=[view.normalized([rng.uniform(-1,1) for _ in range(12)],12) for _ in range(20)]
    check('deterministic_projection', view.pca(random_rows)==view.pca(random_rows))
    m,a,r,ratios=view.pca(random_rows)
    check('retained_variance_bounds',0<r<1 and abs(sum(ratios)-r)<1e-7)
    check('projected_variance_matches_eigenvalues',abs(sum(view.dot(view.project(x,m,a),view.project(x,m,a)) for x in random_rows)/sum(view.dot([v-mu for v,mu in zip(x,m)],[v-mu for v,mu in zip(x,m)]) for x in random_rows)-r)<1e-6)
    for name,value in [('nan',[math.nan]*4),('infinity',[math.inf]*4),('zero',[0]*4),('dimension',[1,0]),('string',['1',0,0,0]),('bool',[True,0,0,0])]:
        denied('invalid_vector_'+name, lambda:view.normalized(value,4))
    fixture={'release_id':'fixture', 'embedding_version':'fixture', 'documents':[{'id':'doc','title':'Test','source_key':'test','chunks':[{'id':'c1','quote':'synthetic'}]}]}
    point={'id':'c1','payload':{'tenant':'demo-a','actor':'demo-user','release_id':'fixture','chunk_id':'c1'},'vector':{'dense':[1.,0,0,0]}}
    class Index:
        def __init__(self, response):self.response=response;self.calls=[]
        def call(self,*args):self.calls.append(args);return self.response
    who=Identity('demo-a','demo-user')
    with patch.object(view,'require_profile'),patch.object(view.catalog,'read',return_value=fixture),patch.object(view,'release_settings',return_value=('fixed',4,False)):
        index=Index({'result':[point]});result=view.read(who,index=index)
        check('reads_exact_canonical_ids_only',index.calls==[('POST','/collections/fixed/points',{'ids':['c1'],'with_payload':True,'with_vector':['dense']})])
        check('read_does_not_send_query_to_model',result['cloud_calls']==0 and result['query'] is None)
        for field in ['tenant','actor','release_id','chunk_id']:
            bad=deepcopy(point);bad['payload'][field]='other'
            denied('deny_index_scope_'+field,lambda:view.read(who,index=Index({'result':[bad]})))
        for name,response in [('missing',{'result':[]}),('duplicate',{'result':[point,point]}),('unknown',{'result':[{**point,'id':'other'}]}),('malformed',{'result':{}})]:
            denied('deny_index_'+name,lambda:view.read(who,index=Index(response)))
        for field in ['payload','vector']:
            bad={**point,field:None}
            denied('deny_invalid_'+field,lambda:view.read(who,index=Index({'result':[bad]})))
        large=deepcopy(fixture);large['documents'][0]['chunks']=[{'id':f'c{i}','quote':'synthetic'} for i in range(65)]
        batch=[{**point,'id':f'c{i}','payload':{**point['payload'],'chunk_id':f'c{i}'}} for i in range(64)]
        with patch.object(view.catalog,'read',return_value=large):
            bounded=Index({'result':batch});sample=view.read(who,index=bounded)
            check('sample_cap_and_coverage_explicit',len(sample['points'])==64 and sample['total_chunks']==65 and sample['sampled'] is True and len(bounded.calls[0][2]['ids'])==64)
        changed=deepcopy(fixture);changed['documents']=[]
        with patch.object(view.catalog,'read',side_effect=[fixture,changed]):
            denied('canonical_revocation_during_IO_denied',lambda:view.read(who,index=Index({'result':[point]})))
    endpoint='/v1/lab/embeddings'
    check('anonymous_map_denied',smoke.http(endpoint)[0] in (401,403))
    check('anonymous_query_denied',smoke.http(endpoint+'/query',body={'question':'food'})[0] in (401,403))
    tokens={t:smoke.http('/v1/lab/session',body={'tenant':t})[1]['token'] for t in ['demo-a','demo-b']}
    maps={}
    for tenant,token in tokens.items():
        code,data=smoke.http(endpoint,token);maps[tenant]=data
        check(tenant+'_actual_index_vectors',code==200 and data['original_dimensions']==384 and len(data['points'])>0 and data['cloud_calls']==0)
        cat=smoke.http('/v1/lab/corpus',token)[1]
        own={str(c['id']):c['quote'] for d in cat['documents'] for c in d['chunks']}
        ids={p['id'] for p in data['points']}
        check(tenant+'_canonical_scope',ids<=set(own) and all(p['quote']==own[p['id']] for p in data['points']) and data['release_id']==cat['release_id'])
        check(tenant+'_finite_geometry',all(len(p['position'])==3 and all(math.isfinite(x) for x in p['position']) for p in data['points']))
        check(tenant+'_edges_authorized',all(e['source'] in ids and e['target'] in ids and -1<=e['cosine']<=1 for e in data['edges']))
        check(tenant+'_stable_geometry',data==smoke.http(endpoint,token)[1])
        code,q=smoke.http(endpoint+'/query',token,{'question':smoke.QUESTION})
        check(tenant+'_real_local_query',code==200 and q['query']['is_rag_answer'] is False and len(q['query']['matches'])==len(data['points']))
        top=q['query']['matches'][0]['id'];food=next(p for p in q['points'] if p['id']==top)
        check(tenant+'_food_query_nearest_policy',food['title']=='Alimentação' and ('45 reais' if tenant=='demo-a' else '20 reais') in food['quote'])
        check(tenant+'_query_same_basis',q['points']==data['points'])
        check(tenant+'_blank_query_denied',smoke.http(endpoint+'/query',token,{'question':'  '})[0]==422)
        check(tenant+'_scope_override_denied',smoke.http(endpoint+'/query',token,{'question':'x','tenant':'other'})[0]==422)
        check(tenant+'_oversized_query_denied',smoke.http(endpoint+'/query',token,{'question':'x'*4001})[0]==422)
        check(tenant+'_corpus_unmodified',cat==smoke.http('/v1/lab/corpus',token)[1])
    check('profiles_do_not_share_points',not ({p['id'] for p in maps['demo-a']['points']}&{p['id'] for p in maps['demo-b']['points']}))
    return {'seed':seed,'checks':checks,'passed':True,'points':{t:len(m['points']) for t,m in maps.items()}}


def main():
    folder=ROOT/'eval/runs'/('embedding-view-checks-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3));folder.mkdir(parents=True)
    sources=frozen(); rounds=[]; error=None; streak=0
    try:
        for _ in range(2):
            assert frozen()==sources,'source_changed'
            rounds.append(round_checks(secrets.randbits(32)));streak+=1
        assert frozen()==sources,'source_changed'
    except Exception as failure:
        error={'type':type(failure).__name__,'reason':str(failure)[:160]};streak=0
    receipt={'scope':'PCA deterministic guards and actual authenticated local Qdrant/query API','source_sha256':sources,'rounds':rounds,'error':error,'consecutive_passes':streak,'independent_blind':False,'production_ready':False}
    (folder/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(folder/'receipt.json'),'checks_per_round':[len(r['checks']) for r in rounds],'streak':streak,'error':error}))
    return 0 if streak==2 else 1


if __name__=='__main__':raise SystemExit(main())
