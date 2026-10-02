"""Bounded lab corpus adapter: canonical SQL + immutable real Qdrant collection.

Synthetic lab only. Optional pinned offline neural family never mixes with lexical
vectors. Content remains canonical in SQL; vector payload has only IDs and scope.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
import os
import re
import unicodedata
from uuid import UUID, uuid4, uuid5

import httpx

from . import ledger, neural_client, semantic_policy
from . import cache, resilience
from .domain import Citation, Identity, Proposal, RequestError

EMBEDDING='lexical-hash-256-v1'
INDEX_LAYOUT='shared_lexical_v1'
STOP=frozenset('a o as os de da do das dos e em para por com um uma que qual quais como quanto quantos sao se é'.split())


def require_profile():
    if os.environ.get('RAG_RETRIEVAL')!='extractive_lab':
        raise RequestError('RETRIEVAL_PROFILE_DISABLED',409)


def terms(text):
    normalized=''.join(c for c in unicodedata.normalize('NFKD',text.lower()) if not unicodedata.combining(c))
    return [t for t in re.findall(r'[a-z0-9]+',normalized) if t not in STOP]


def vectors(text):
    counts=Counter(terms(text)); dense=[0.0]*256; sparse={}
    for token,count in counts.items():
        h=int.from_bytes(hashlib.sha256(token.encode()).digest()[:4],'big')
        dense[h%256]+=float(count)
        sparse[h]=sparse.get(h,0.0)+float(count)
    norm=math.sqrt(sum(v*v for v in dense)) or 1.0
    return [v/norm for v in dense],{'indices':sorted(sparse),'values':[sparse[i] for i in sorted(sparse)]}


def collection_name(release, layout):
    rid=UUID(str(release))
    if layout=='legacy_release_v1':
        return 'rgl_'+rid.hex
    if layout==INDEX_LAYOUT:
        return 'rgl_shared_lexical_256_v1'
    if layout==neural_client.LAYOUT:
        return 'rgl_shared_neural_384_minilm_faf4aa4_v1'
    raise RequestError('UNKNOWN_INDEX_LAYOUT',503)


def release_settings(release):
    rid=UUID(str(release))
    with ledger.connect() as db:
        row=db.execute('SELECT index_layout,embedding_version FROM corpus_releases WHERE id=%s',(rid,)).fetchone()
    if not row:
        raise RequestError('UNKNOWN_INDEX_LAYOUT',503)
    lexical=row['embedding_version']==EMBEDDING and row['index_layout'] in ('legacy_release_v1',INDEX_LAYOUT)
    neural=row['embedding_version']==neural_client.VERSION and row['index_layout']==neural_client.LAYOUT
    if not lexical and not neural: raise RequestError('INDEX_MODEL_MISMATCH',503)
    return collection_name(rid,row['index_layout']),384 if neural else 256,neural


def collection(release):
    return release_settings(release)[0]


def new_embedding():
    mode=os.environ.get('RAG_EMBEDDING')
    if mode is None: return EMBEDDING,INDEX_LAYOUT
    if mode=='neural_lab': return neural_client.VERSION,neural_client.LAYOUT
    raise RequestError('EMBEDDING_PROFILE_INVALID',409)


class QdrantAdapter:
    def call(self, method, path, body=None):
        with resilience.guard('qdrant'):
            return self._call(method,path,body)

    def _call(self, method, path, body=None):
        # No destinations, redirects, credentials, tenant or tool names from prompts.
        try:
            with httpx.Client(base_url='http://qdrant:6333',timeout=5,trust_env=False,follow_redirects=False) as client:
                with client.stream(method,path,json=body) as response:
                    if response.status_code>=400: raise RequestError('INDEX_UNAVAILABLE',503)
                    chunks=[]; total=0
                    for chunk in response.iter_bytes():
                        total+=len(chunk)
                        if total>1048576: raise RequestError('INDEX_RESPONSE_TOO_LARGE',503)
                        chunks.append(chunk)
                    return json.loads(b''.join(chunks))
        except (httpx.HTTPError,ValueError): raise RequestError('INDEX_UNAVAILABLE',503) from None

    def index(self, release, chunks):
        name,dimension,neural=release_settings(release)
        # Qdrant PUT creation is idempotent when collection already exists.
        try:
            with resilience.guard('qdrant'), httpx.Client(base_url='http://qdrant:6333',timeout=5,trust_env=False,follow_redirects=False) as client:
                response=client.get('/collections/'+name)
                if response.status_code==404:
                    made=client.put('/collections/'+name,json={
                        'vectors':{'dense':{'size':dimension,'distance':'Cosine'}},'sparse_vectors':{'sparse':{}}})
                    if made.status_code not in (200,409): raise RequestError('INDEX_UNAVAILABLE',503)
                elif response.status_code!=200: raise RequestError('INDEX_UNAVAILABLE',503)
                metadata=client.get('/collections/'+name)
                if metadata.status_code!=200: raise RequestError('INDEX_UNAVAILABLE',503)
                params=metadata.json()['result']['config']['params']['vectors']['dense']
                if params['size']!=dimension or params['distance']!='Cosine':
                    raise RequestError('INDEX_MODEL_MISMATCH',503)
        except httpx.HTTPError: raise RequestError('INDEX_UNAVAILABLE',503) from None
        except (ValueError,KeyError,TypeError): raise RequestError('INDEX_MODEL_MISMATCH',503) from None
        if name.startswith('rgl_shared_'):
            # Re-creating existing keyword indexes forces unnecessary durable I/O
            # on every publication. Validate existing indexes; create missing only.
            payload_schema=metadata.json()['result'].get('payload_schema',{})
            for field in ('tenant','actor','release_id'):
                if field in payload_schema:
                    if payload_schema[field].get('data_type')!='keyword':
                        raise RequestError('INDEX_SCOPE_SCHEMA_MISMATCH',503)
                else:
                    self.call('PUT','/collections/'+name+'/index?wait=true',{'field_name':field,'field_schema':'keyword'})
        points=[]
        neural_vectors=neural_client.embed([c['title']+' '+c['quote'] for c in chunks],scope=(chunks[0]['tenant'],chunks[0]['actor'],str(release)))[0] if neural and chunks else None
        for ordinal,chunk in enumerate(chunks):
            dense,sparse=vectors(chunk['title']+' '+chunk['quote'])
            if neural: dense=neural_vectors[ordinal]
            points.append(dict(id=str(chunk['id']),vector=dict(dense=dense,sparse=sparse),
                payload=dict(chunk_id=str(chunk['id']),tenant=chunk['tenant'],actor=chunk['actor'],release_id=str(release))))
        self.call('PUT','/collections/'+name+'/points?wait=true',{'points':points})

    def search(self, release, who, question):
        name,dimension,neural=release_settings(release)
        cache_key=cache.key('candidates',(who.tenant,who.actor,str(release)),[name,dimension,neural,question])
        cached=cache.get(cache_key)
        if isinstance(cached,list) and len(cached)<=16:
            try: return [str(UUID(v)) for v in cached]
            except (ValueError,TypeError,AttributeError): pass
        dense,sparse=vectors(question)
        if neural: dense=neural_client.embed([semantic_policy.retrieval_query(question)],scope=(who.tenant,who.actor,str(release)))[0][0]
        if not sparse['indices']: return []
        scope={'must':[{'key':k,'match':{'value':v}} for k,v in
            (('tenant',who.tenant),('actor',who.actor),('release_id',str(release)))]}
        result=self.call('POST','/collections/'+name+'/points/query',{
            'prefetch':[{'query':dense,'using':'dense','filter':scope,'limit':16},
                        {'query':sparse,'using':'sparse','filter':scope,'limit':16}],
            'query':{'fusion':'rrf'},'filter':scope,'limit':16,'with_payload':True})
        ids=[str(UUID(str(p['id']))) for p in result['result']['points']]
        cache.put(cache_key,ids)
        return ids


def validate_bundle(documents):
    if not isinstance(documents,list) or not 1<=len(documents)<=32:
        raise RequestError('INVALID_CORPUS_BUNDLE',422)
    keys=set(); size=0
    for doc in documents:
        if set(doc)-{'source_key','title','text','media_type','valid_until'}:
            raise RequestError('INVALID_CORPUS_DOCUMENT',422)
        if doc.get('media_type')!='text/plain' or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',doc.get('source_key','')):
            raise RequestError('UNSUPPORTED_CORPUS_DOCUMENT',422)
        title=doc.get('title'); text=doc.get('text')
        if not isinstance(title,str) or not isinstance(text,str) or not 1<=len(title)<=120 or not text.strip() or len(text)>8192:
            raise RequestError('INVALID_CORPUS_DOCUMENT',422)
        if any(ord(c)<32 and c not in '\n\t\r' for c in title+text) or not terms(title+' '+text):
            raise RequestError('INVALID_CORPUS_DOCUMENT',422)
        if doc['source_key'] in keys: raise RequestError('DUPLICATE_SOURCE_KEY',422)
        keys.add(doc['source_key']); size+=len((title+text).encode('utf8'))
        if doc.get('valid_until') is not None:
            try:
                expiry=datetime.fromisoformat(doc['valid_until'].replace('Z','+00:00'))
                if expiry.tzinfo is None: raise ValueError()
            except (ValueError,TypeError,AttributeError): raise RequestError('INVALID_CORPUS_EXPIRY',422) from None
    if size>12000: raise RequestError('CORPUS_BYTES_LIMIT',413)


def ingest(who: Identity, documents, index=None, originals=None, expected_generation=None):
    require_profile(); validate_bundle(documents)
    embedding,layout=new_embedding()
    manifest=hashlib.sha256(json.dumps({'documents':documents,'originals':originals or {},'embedding':embedding,'index_layout':layout,'chunker':'chars-600-overlap80-v1'},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with ledger.connect() as db:
        db.execute('INSERT INTO corpus_heads(tenant,actor) VALUES (%s,%s) ON CONFLICT DO NOTHING',(who.tenant,who.actor))
        head=db.execute('SELECT * FROM corpus_heads WHERE tenant=%s AND actor=%s FOR UPDATE',(who.tenant,who.actor)).fetchone()
        if expected_generation is not None and head['generation'] != expected_generation:
            raise RequestError('CORPUS_PROMOTION_CONFLICT',409)
        keys=[doc['source_key'] for doc in documents]
        if db.execute('SELECT 1 FROM corpus_revocations WHERE tenant=%s AND actor=%s AND source_key=ANY(%s)',(who.tenant,who.actor,keys)).fetchone():
            raise RequestError('SOURCE_REVOKED',409)
        if head['release_id']:
            active=db.execute('SELECT manifest_hash FROM corpus_releases WHERE id=%s',(head['release_id'],)).fetchone()
            if active['manifest_hash']==manifest: return dict(release_id=str(head['release_id']),state='READY',replayed=True)
        release=db.execute("SELECT * FROM corpus_releases WHERE tenant=%s AND actor=%s AND manifest_hash=%s AND parent_generation=%s AND state='BUILDING' ORDER BY created_at LIMIT 1",(who.tenant,who.actor,manifest,head['generation'])).fetchone()
        if release: rid=release['id']; generation=release['parent_generation']
        else:
            rid=uuid4(); generation=head['generation']
            db.execute("INSERT INTO corpus_releases(id,tenant,actor,manifest_hash,parent_generation,state,embedding_version,index_layout) VALUES (%s,%s,%s,%s,%s,'BUILDING',%s,%s)",(rid,who.tenant,who.actor,manifest,generation,embedding,layout))
            for doc in documents:
                did=uuid5(rid,doc['source_key'])
                content_hash=hashlib.sha256(doc['text'].encode()).hexdigest()
                db.execute('INSERT INTO corpus_documents(id,release_id,tenant,actor,source_key,title,content_hash,valid_until) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(did,rid,who.tenant,who.actor,doc['source_key'],doc['title'],content_hash,doc.get('valid_until')))
                if originals and doc['source_key'] in originals:
                    original=originals[doc['source_key']]
                    db.execute('UPDATE corpus_documents SET original_hash=%s,original_media_type=%s,original_bytes=%s WHERE id=%s',
                        (original['original_hash'],original['original_media_type'],original['original_bytes'],did))
                for ordinal,start in enumerate(range(0,len(doc['text']),520)):
                    quote=doc['text'][start:start+600]
                    db.execute('INSERT INTO corpus_chunks VALUES (%s,%s,%s,%s)',(uuid5(did,str(ordinal)),did,ordinal,quote))
        chunks=db.execute('SELECT c.*,d.title,d.tenant,d.actor FROM corpus_chunks c JOIN corpus_documents d ON d.id=c.document_id WHERE d.release_id=%s',(rid,)).fetchall()
    (index or QdrantAdapter()).index(rid,chunks)
    with ledger.connect() as db:
        head=db.execute('SELECT * FROM corpus_heads WHERE tenant=%s AND actor=%s FOR UPDATE',(who.tenant,who.actor)).fetchone()
        if head['release_id']==rid: return dict(release_id=str(rid),state='READY',replayed=True)
        if head['generation']!=generation:
            db.execute("UPDATE corpus_releases SET state='SUPERSEDED' WHERE id=%s",(rid,))
            # Commit superseded marker even though the public result is conflict.
            db.commit(); raise RequestError('CORPUS_PROMOTION_CONFLICT',409)
        db.execute("UPDATE corpus_releases SET state='READY' WHERE id=%s",(rid,))
        db.execute('UPDATE corpus_heads SET release_id=%s,generation=generation+1 WHERE tenant=%s AND actor=%s',(rid,who.tenant,who.actor))
    return dict(release_id=str(rid),state='READY',replayed=False)


def retrieve(request, index=None):
    release=request.get('source_snapshot')
    if not release: return []
    who=Identity(request['tenant'],request['actor'])
    ids=(index or QdrantAdapter()).search(release,who,request['question'])
    if not ids: return []
    with ledger.connect() as db:
        rows=db.execute("SELECT c.*,d.title,d.content_hash,d.acl_epoch,d.release_id FROM corpus_chunks c JOIN corpus_documents d ON d.id=c.document_id JOIN corpus_releases r ON r.id=d.release_id WHERE c.id=ANY(%s::uuid[]) AND d.tenant=%s AND d.actor=%s AND d.release_id=%s AND r.state='READY' AND NOT d.revoked AND (d.valid_until IS NULL OR d.valid_until>clock_timestamp())",(ids,who.tenant,who.actor,release)).fetchall()
    query=set(terms(request['question']))
    if not query: return []
    ranked=[r for r in rows if query<=set(terms(r['title']+' '+r['quote']))]
    if release_settings(release)[2]:
        ranked=[r for r in ranked if semantic_policy.eligible(request['question'],r['quote'])]
        if not ranked and rows:
            return neural_client.rank(request['question'],rows,scope=(who.tenant,who.actor,str(release)),
                for_grounding=os.environ.get('RAG_GEMINI_RESPONSES')=='free_lab')
    ranked.sort(key=lambda r:(-len(query & set(terms(r['quote']))),r['ordinal'],str(r['id'])))
    return ranked


def compose(evidence):
    if not evidence: return Proposal('ABSTAIN','Não há evidência autorizada suficiente para responder.')
    # Different sources covering this query are conservatively treated as ambiguity.
    if len({r['document_id'] for r in evidence})>1:
        return Proposal('ABSTAIN','Há mais de uma fonte aplicável; não posso resolver o conflito com esta baseline.')
    row=evidence[0]
    citation=Citation(str(row['document_id']),str(row['id']),str(row['release_id']),row['content_hash'],row['acl_epoch'],row['quote'])
    return Proposal('EXTRACTIVE','Trecho da fonte:\n'+citation.quote,(citation,))


def revoke(who, document_id, all_versions=False):
    require_profile()
    with ledger.connect() as db:
        db.execute('SELECT 1 FROM corpus_heads WHERE tenant=%s AND actor=%s FOR UPDATE',(who.tenant,who.actor))
        row=db.execute('SELECT id,source_key FROM corpus_documents WHERE id=%s AND tenant=%s AND actor=%s',(document_id,who.tenant,who.actor)).fetchone()
        if not row: raise RequestError('NOT_FOUND',404)
        if all_versions:
            db.execute('INSERT INTO corpus_revocations(tenant,actor,source_key) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',(who.tenant,who.actor,row['source_key']))
            db.execute('UPDATE corpus_documents SET revoked=true,acl_epoch=acl_epoch+1 WHERE tenant=%s AND actor=%s AND source_key=%s AND NOT revoked',(who.tenant,who.actor,row['source_key']))
        else:
            # Preserve legacy version-only operation. Strong source revocation is explicit.
            db.execute('UPDATE corpus_documents SET revoked=true,acl_epoch=acl_epoch+1 WHERE id=%s AND NOT revoked',(row['id'],))
        db.execute('UPDATE corpus_heads SET generation=generation+1 WHERE tenant=%s AND actor=%s',(who.tenant,who.actor))
    return dict(document_id=str(row['id']),revoked=True)
