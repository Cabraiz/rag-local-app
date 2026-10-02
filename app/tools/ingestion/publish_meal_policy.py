# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Additive, restartable publication of fictional rules; no model/provider calls."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import httpx

ROOT=_workspace_root
DATA=ROOT/'eval/datasets/meal-policy-aurora-v1.json'

def main():
    data=json.loads(DATA.read_text(encoding='utf8')); docs=data['documents']
    assert len(docs)==20 and len({d['source_key'] for d in docs})==20
    assert data['baseline_limit_brl']==45
    assert all(len(d['text'])<=600 and len(d['title'])<=120 for d in docs)
    folder=ROOT/'eval/runs'/('meal-policy-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(parents=True,exist_ok=False); path=folder/'publication.json'
    result={'dataset_sha256':hashlib.sha256(DATA.read_bytes()).hexdigest(),'published':[],
            'passed':False,'scope':'synthetic additive local API publication; no Gemini calls','cloud_calls':0}
    def record():path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    try:
        with httpx.Client(base_url='http://127.0.0.1:8840',timeout=35,trust_env=False,follow_redirects=False) as client:
            def call(method,url,token=None,body=None):
                response=client.request(method,url,json=body,headers={'Authorization':'Bearer '+token} if token else {})
                if response.status_code>=400:raise RuntimeError('HTTP_'+str(response.status_code)+'_'+url)
                return response.json()
            token=call('POST','/v1/lab/session',body={'profile':'bruno'})['token']
            before=call('GET','/v1/lab/corpus',token)
            keys={d['source_key'] for d in docs}
            result['preserved_sources']={d['source_key']:d['content_hash'] for d in before['documents'] if d['source_key'] not in keys}
            result['before_generation']=before['generation'];record()
            existing={d['source_key']:d for d in before['documents']}
            projected={k:(d['title'],sum(len(c['quote'].encode()) for c in d['chunks'])) for k,d in existing.items()}
            projected.update({d['source_key']:(d['title'],len(d['text'].encode())) for d in docs})
            assert len(projected)<=32,'CORPUS_DOCUMENT_LIMIT'
            assert sum(len(title.encode())+size for title,size in projected.values())<=12000,'CORPUS_BYTES_LIMIT'
            for number,doc in enumerate(docs,1):
                head=call('GET','/v1/lab/corpus',token)
                current={d['source_key']:d for d in head['documents']}
                expected=hashlib.sha256(doc['text'].encode()).hexdigest()
                if doc['source_key'] in current and current[doc['source_key']]['content_hash']==expected and current[doc['source_key']]['title']==doc['title']:
                    outcome='already_current'
                else:
                    body={'expected_generation':head['generation'],'document':{**doc,'media_type':'text/plain','valid_until':None}}
                    # Only explicit index-unavailable 503 permits bounded retry.
                    for attempt in range(3):
                        response=client.post('/v1/lab/corpus/documents',json=body,headers={'Authorization':'Bearer '+token})
                        if response.status_code==201:break
                        if response.status_code!=503 or response.json().get('code')!='INDEX_UNAVAILABLE' or attempt==2:
                            raise RuntimeError('PUBLICATION_HTTP_'+str(response.status_code))
                        time.sleep(.5)
                    outcome='published'
                verified=call('GET','/v1/lab/corpus',token)
                by_key={d['source_key']:d for d in verified['documents']}
                assert by_key[doc['source_key']]['content_hash']==expected,'PUBLICATION_NOT_VERIFIED'
                assert all(by_key[k]['content_hash']==v for k,v in result['preserved_sources'].items()),'EXISTING_SOURCE_CHANGED'
                result['published'].append({'source_key':doc['source_key'],'outcome':outcome});record()
                print(json.dumps({'rule':number,'total':len(docs),'status':outcome}),flush=True)
            result.update(passed=True,release_id=verified['release_id'],generation=verified['generation'],document_count=len(verified['documents']))
    except Exception as error:
        result['error_type']=type(error).__name__
        # Local known reason only; no key, token or response body.
        result['error_code']=str(error) if isinstance(error,(RuntimeError,AssertionError)) else 'SANITIZED_LOCAL_FAILURE'
    record();print(json.dumps({'passed':result['passed'],'rules':len(result['published']),'receipt':str(path)}),flush=True)
    return 0 if result['passed'] else 1
if __name__=='__main__':sys.exit(main())
