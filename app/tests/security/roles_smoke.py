# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Two scoped adversarial rounds against the running lab; never print tokens."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4
import functional_smoke as smoke

ROOT=_workspace_root
FOLDER=ROOT/'eval/runs/roles-20261001'
DOC={'source_key':'estacionamento-roles-demo','title':'Estacionamento',
     'text':'O limite de estacionamento da Aurora é de 18 reais por dia. Guarde o comprovante para solicitar o reembolso.',
     'media_type':'text/plain','valid_until':None}


def frozen():
    names=['app/src/rag_app/'+n+'.py' for n in ('api','lab_roles','publication','corpus','catalog','integration_status')]
    names+=['frontend/public/'+n for n in ('index.html','app.js','styles.css','embeddings.js')]
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}


def run_round():
    checks=[]
    def check(name, passed):
        assert passed,name
        checks.append(name)
    sessions={p:smoke.http('/v1/lab/session',body={'profile':p}) for p in ('ana','bruno')}
    check('server_fixed_roles', all(c==200 and b['tenant']=='demo-a' and b['role']==r
        for p,r in [('ana','client'),('bruno','operator')] for c,b in [sessions[p]]))
    ana=sessions['ana'][1]['token']; bruno=sessions['bruno'][1]['token']
    check('anonymous_denied',smoke.http('/v1/lab/corpus')[0] in (401,403))
    for name,body in [('legacy_tenant',{'tenant':'demo-a'}),('role_injection',{'profile':'ana','role':'operator'}),
                      ('tenant_injection',{'profile':'bruno','tenant':'demo-b'}),('unknown_profile',{'profile':'admin'})]:
        check(name,smoke.http('/v1/lab/session',body=body)[0]==422)
    stub={'source_key':'no-write','title':'Forbidden','text':'No write allowed','media_type':'text/plain'}
    for path,body in [('/v1/lab/corpus',None),('/v1/lab/integrations',None),('/v1/lab/embeddings',None),
        ('/v1/lab/corpus/documents',{'document':stub,'expected_generation':0}),
        ('/v1/lab/corpus/releases',{'documents':[stub]}),
        ('/v1/lab/files',{'source_key':'no-write','title':'No write','media_type':'text/plain','data_base64':'YQ=='}),
        ('/v1/lab/documents/'+str(uuid4())+'/original',None),
        ('/v1/lab/documents/'+str(uuid4())+'/revoke',{}),
        ('/v1/lab/documents/'+str(uuid4())+'/revoke-source',{}),
        ('/v1/lab/embeddings/query',{'question':'estacionamento'})]:
        check('ana_denied_'+path,smoke.http(path,ana,body)[0]==403)
    for path,body in [('/v1/requests',None),('/v1/events',None),('/v1/requests/'+str(uuid4()),None),
        ('/v1/requests',{'question':'alimentacao'}),('/v1/requests/resolve',{}),
        ('/v1/requests/'+str(uuid4())+'/cancel',{}),('/v1/lab/embeddings/query',{'question':'alimentacao'})]:
        check('bruno_denied_'+path,smoke.http(path,bruno,body,{'Idempotency-Key':str(int(time.time()))+'.'+str(uuid4())})[0]==403)
    a,b,c=ana.split('.')
    check('tampered_token_denied',smoke.http('/v1/requests',a+'.'+b+'.'+('A' if c[0]!='A' else 'B')+c[1:])[0]==401)
    code,before=smoke.http('/v1/lab/corpus',bruno)
    check('operator_catalog',code==200 and len(before['documents'])>=8)
    code,published=smoke.http('/v1/lab/corpus/documents',bruno,{'document':DOC,'expected_generation':before['generation']})
    check('operator_publication',code==201 and published['state']=='READY')
    after=smoke.http('/v1/lab/corpus',bruno)[1]
    previous={d['source_key']:d['content_hash'] for d in before['documents'] if d['source_key']!=DOC['source_key']}
    current={d['source_key']:d['content_hash'] for d in after['documents']}
    check('other_sources_preserved',all(current.get(k)==v for k,v in previous.items()))
    check('published_source_canonical',current.get(DOC['source_key'])==hashlib.sha256(DOC['text'].encode()).hexdigest())
    code,_=smoke.http('/v1/lab/corpus/documents',bruno,{'document':{**DOC,'source_key':'stale-write'},'expected_generation':max(0,after['generation']-1)})
    check('stale_publication_conflict',code==409)
    check('legacy_replacement_blocked',smoke.http('/v1/lab/corpus/releases',bruno,{'documents':[stub]})[0]==409)
    check('legacy_upload_blocked',smoke.http('/v1/lab/files',bruno,{'source_key':'no-write','title':'No write','media_type':'text/plain','data_base64':'YQ=='})[0]==409)
    check('operator_integrations_honest',smoke.http('/v1/lab/integrations',bruno)[1]['external_calls']==0)
    check('operator_real_vector_diagnostics',smoke.http('/v1/lab/embeddings',bruno)[0]==200)
    key=str(int(time.time()))+'.'+str(uuid4())
    code,accepted=smoke.http('/v1/requests',ana,{'question':'Qual o limite de estacionamento da Aurora?'},{'Idempotency-Key':key})
    check('client_request_accepted',code==202)
    row=smoke.wait_terminal(accepted['request_id'],ana)
    result=row.get('result') or {}
    check('published_text_retrieved_by_ana',row['state']=='SUCCEEDED' and result.get('kind')=='EXTRACTIVE' and DOC['text'] in result.get('text',''))
    check('citation_current_release',len(result.get('citations',[]))==1 and result['citations'][0]['release_id']==after['release_id'])
    code,replay=smoke.http('/v1/requests',ana,{'question':'Qual o limite de estacionamento da Aurora?'},{'Idempotency-Key':key})
    check('idempotent_request',code==202 and replay['request_id']==accepted['request_id'])
    check('operator_cannot_read_client_receipt',smoke.http('/v1/requests/'+accepted['request_id'],bruno)[0]==403)
    return {'passed':True,'checks':checks,'request_id':accepted['request_id'],'release_id':after['release_id']}


def main():
    hashes=frozen();rounds=[];error=None
    try:
        for _ in range(2):
            rounds.append(run_round())
            assert frozen()==hashes,'SOURCE_CHANGED'
    except Exception as exc: error=str(exc)
    receipt={'at':datetime.now(timezone.utc).isoformat(),'scope':'role/API/local worker checks; not independent audit or production certification',
             'passed':error is None and len(rounds)==2,'clean_streak':len(rounds),'rounds':rounds,'error':error,'source_sha256':hashes}
    path=FOLDER/('receipt-'+datetime.now(timezone.utc).strftime('%H%M%S')+'.json')
    path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'passed':receipt['passed'],'clean_streak':len(rounds),'checks':[len(r['checks']) for r in rounds],'error':error,'receipt':str(path)}))
    raise SystemExit(0 if receipt['passed'] else 1)


if __name__=='__main__':main()
