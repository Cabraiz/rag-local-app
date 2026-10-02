"""Fixed internal model RPC; never uses a provider, API key or caller destination."""
import hashlib
import json
import math
from pathlib import Path
import httpx
from . import semantic_policy
from .domain import RequestError
from . import cache, resilience

VERSION='minilm-multilingual-faf4aa4225822f3bc6376869cb1164e8e3feedd0-384-v1'
LAYOUT='shared_neural_v1'
CALIBRATION_SHA256='c2945476a42d3095d73922996a8bf7f759791593e29e0ffc5540e78f5a19bfe0'

def validate(value,count):
    try:
        # Untrusted RPC data: explicit checks must survive Python -O.
        if not isinstance(value,dict): raise ValueError()
        if value['model_version']!=VERSION or value['calibration']!='office_faq_v1': raise ValueError()
        if value['holdout_used'] is not False or value['calibration_sha256']!=CALIBRATION_SHA256: raise ValueError()
        if value['policy_sha256']!=hashlib.sha256(Path(semantic_policy.__file__).read_bytes()).hexdigest(): raise ValueError()
        threshold=value['threshold']; margin=value['margin']
        if type(threshold) not in (float,int) or not math.isfinite(threshold) or not .30<=threshold<=.90 or type(margin) not in (float,int) or margin!=.05: raise ValueError()
        vectors=value['vectors']
        if not isinstance(vectors,list) or len(vectors)!=count: raise ValueError()
        for row in vectors:
            if not isinstance(row,list) or len(row)!=384: raise ValueError()
            if not all(type(v) in (float,int) and math.isfinite(v) for v in row): raise ValueError()
            if abs(sum(v*v for v in row)-1)>=.02: raise ValueError()
        return vectors,float(threshold),float(margin)
    except (KeyError,TypeError,ValueError,OverflowError):
        raise RequestError('EMBEDDING_RESPONSE_INVALID',503) from None

def embed(texts, *, scope=None):
    cache_key=cache.key('embedding',scope,[VERSION,CALIBRATION_SHA256,texts]) if scope else None
    cached=cache.get(cache_key) if cache_key else None
    if cached is not None:
        try: return validate(cached,len(texts))
        except RequestError: pass
    with resilience.guard('embedding'):
        value=_embed(texts)
    if cache_key: cache.put(cache_key,value)
    return validate(value,len(texts))

def _embed(texts):
    try:
        with httpx.Client(base_url='http://embeddings:8000',timeout=10,trust_env=False,follow_redirects=False) as client:
            with client.stream('POST','/v1/embeddings',json={'texts':texts}) as response:
                if response.status_code!=200: raise RequestError('EMBEDDING_UNAVAILABLE',503)
                raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>1048576: raise RequestError('EMBEDDING_RESPONSE_INVALID',503)
                value=json.loads(raw)
                validate(value,len(texts))
                return value
    except (httpx.HTTPError,ValueError):
        raise RequestError('EMBEDDING_UNAVAILABLE',503) from None

def rank(question,rows,*,for_grounding=False,scope=None):
    values,threshold,margin=embed([semantic_policy.retrieval_query(question)]+[r['title']+' '+r['quote'] for r in rows],scope=scope)
    scored=sorted([(sum(a*b for a,b in zip(values[0],vector)),str(row['document_id']),row) for row,vector in zip(rows,values[1:]) if semantic_policy.eligible(question,row['quote'])],key=lambda item:(-item[0],item[1],item[2]['ordinal']))
    if not scored or scored[0][0]<threshold: return []
    if for_grounding:
        # Similar authorized policies are candidates, not competing final answers.
        # Preserve eligibility/threshold; the bounded LLM selector then decides
        # answerability and canonical publication/ACL checks remain mandatory.
        return [row for score,doc,row in scored if score>=threshold][:5]
    winner=scored[0][1]
    others=[s for s in scored if s[1]!=winner]
    if others and others[0][0]>=threshold and scored[0][0]-others[0][0]<margin: return []
    return [row for score,doc,row in scored if doc==winner and score>=threshold]
