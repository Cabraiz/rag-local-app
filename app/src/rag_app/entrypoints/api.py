from contextlib import asynccontextmanager
import os
import asyncio
import time
from uuid import UUID
from typing import Literal
import anyio
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
import jwt
import psycopg
from .bootstrap import request_service
from .config import enforce_lab, secret
from .domain import Identity, RequestError
from .lab_roles import PROFILES, resource_identity

enforce_lab()
service = request_service()
security = HTTPBearer()


@asynccontextmanager
async def lifespan(app):
    from .observability import setup
    setup()
    anyio.to_thread.current_default_thread_limiter().total_tokens = 8
    yield


app = FastAPI(title='RAG local — lifecycle slice', docs_url=None, redoc_url=None, lifespan=lifespan)


class BoundedBody:
    def __init__(self, inner): self.inner = inner
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await self.inner(scope,receive,send)
        parts, total = [], 0
        deadline=time.monotonic()+3
        while True:
            try:
                message = await asyncio.wait_for(receive(),timeout=max(.001,deadline-time.monotonic()))
            except TimeoutError:
                await send({'type':'http.response.start','status':408,'headers':[(b'content-type',b'application/json')]})
                await send({'type':'http.response.body','body':b'{"code":"BODY_READ_TIMEOUT"}'})
                return
            if message['type'] == 'http.disconnect': return
            chunk = message.get('body', b'')
            total += len(chunk)
            if total > 16384:
                await send({'type':'http.response.start','status':413,'headers':[(b'content-type',b'application/json')]})
                await send({'type':'http.response.body','body':b'{"code":"BODY_TOO_LARGE"}'})
                return
            parts.append(chunk)
            if not message.get('more_body'): break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request','body':b''.join(parts),'more_body':False}
            return await receive()
        await self.inner(scope,bounded_receive,send)


app.add_middleware(BoundedBody)


class BoundedAdmission:
    def __init__(self,inner): self.inner=inner; self.inflight=0
    async def __call__(self,scope,receive,send):
        from .resilience import enabled
        limited=enabled() and scope['type']=='http' and scope['method']=='POST' and scope['path']=='/v1/requests'
        if not limited: return await self.inner(scope,receive,send)
        if self.inflight>=16:
            await send({'type':'http.response.start','status':503,'headers':[(b'content-type',b'application/json'),(b'retry-after',b'2')]})
            await send({'type':'http.response.body','body':b'{"code":"API_INFLIGHT_LIMIT"}'})
            return
        self.inflight+=1
        try: await self.inner(scope,receive,send)
        finally: self.inflight-=1


app.add_middleware(BoundedAdmission)


@app.middleware('http')
async def no_sensitive_cache(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    return response


def authenticated_claims(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        claims = jwt.decode(credentials.credentials,secret('jwt_lab'),algorithms=['HS256'],
            audience='rag-local',issuer='rag-local-lab',options={'require':['exp','iat','sub','tenant','role','aud','iss']})
        resource_identity(claims)
        return claims
    except (jwt.PyJWTError,ValueError): raise HTTPException(401,'INVALID_TOKEN') from None


def client_identity(claims=Depends(authenticated_claims)):
    try: return resource_identity(claims, 'client')
    except PermissionError: raise HTTPException(403, 'ROLE_FORBIDDEN') from None


def operator_identity(claims=Depends(authenticated_claims)):
    try: return resource_identity(claims, 'operator')
    except PermissionError: raise HTTPException(403, 'ROLE_FORBIDDEN') from None


@app.exception_handler(RequestError)
async def request_error(_, error):
    return JSONResponse({'code':error.code},status_code=error.status,
        headers={'Retry-After':'2'} if error.status in (429,503) else None)


@app.exception_handler(psycopg.Error)
async def db_error(_, error):
    return JSONResponse({'code':'LEDGER_UNAVAILABLE_RETRY_SAME_KEY'},status_code=503,headers={'Retry-After':'2'})


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1,max_length=4000,pattern=r'\S')


class LabIdentity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile: Literal['ana','bruno']


class LabDocument(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_key: str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    title: str=Field(min_length=1,max_length=120)
    text: str=Field(min_length=1,max_length=8192)
    media_type: Literal['text/plain']='text/plain'
    valid_until: AwareDatetime | None=None


class LabBundle(BaseModel):
    model_config=ConfigDict(extra='forbid')
    documents: list[LabDocument]=Field(min_length=1,max_length=8)


class LabPublication(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_generation: int=Field(ge=0)
    document: LabDocument


class LabFile(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_key: str=Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    title: str=Field(min_length=1,max_length=120)
    media_type: Literal['text/plain','text/markdown','application/pdf']
    data_base64: str=Field(min_length=1,max_length=10924)
    valid_until: AwareDatetime | None=None


class LabFilePublication(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_generation: int=Field(ge=0)
    document: LabFile


@app.post('/v1/lab/corpus/files',status_code=201)
def publish_file(body: LabFilePublication,who: Identity=Depends(operator_identity)):
    from .documents import upload_additive
    return upload_additive(who,body.document.model_dump(mode='json'),body.expected_generation)


@app.post('/v1/lab/files',status_code=201)
def upload_file(body: LabFile,who: Identity=Depends(operator_identity)):
    # Legacy upload replaces the full release. Do not expose that footgun here.
    raise HTTPException(409, 'USE_ADDITIVE_DOCUMENT_PUBLICATION')


@app.get('/v1/lab/documents/{document_id}/original')
def original_file(document_id: UUID,who: Identity=Depends(operator_identity)):
    from .documents import download
    raw,media_type=download(who,document_id)
    extension={'text/plain':'txt','text/markdown':'md','application/pdf':'pdf'}[media_type]
    return Response(raw,media_type=media_type,headers={
        'Content-Disposition':f'attachment; filename="{document_id}.{extension}"',
        'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})


@app.post('/v1/lab/corpus/releases',status_code=201)
def ingest_bundle(body: LabBundle,who: Identity=Depends(operator_identity)):
    raise HTTPException(409, 'USE_ADDITIVE_DOCUMENT_PUBLICATION')


@app.post('/v1/lab/corpus/documents',status_code=201)
def publish_document(body: LabPublication,who: Identity=Depends(operator_identity)):
    from .publication import publish
    return publish(who,body.document.model_dump(mode='json'),body.expected_generation)


@app.get('/v1/lab/corpus')
def visible_corpus(who: Identity=Depends(operator_identity)):
    from .catalog import read
    return read(who)


@app.get('/v1/lab/integrations')
def integration_metadata(who: Identity=Depends(operator_identity)):
    from .integration_status import read
    return read()


@app.get('/v1/lab/integrations/feed')
def integration_feed(who: Identity=Depends(operator_identity),credentials: HTTPAuthorizationCredentials=Depends(security)):
    from .feed_client import call
    return call(credentials.credentials)


@app.post('/v1/lab/integrations/refresh',status_code=202)
def refresh_integrations(who: Identity=Depends(operator_identity),credentials: HTTPAuthorizationCredentials=Depends(security)):
    from .feed_client import call
    return call(credentials.credentials,refresh=True)


@app.get('/v1/lab/embeddings')
def embedding_map(who: Identity=Depends(operator_identity)):
    from .embedding_view import read
    return read(who)


@app.post('/v1/lab/embeddings/query')
def embedding_query(body: Question, claims=Depends(authenticated_claims)):
    # Operator diagnostics are read-only; client search uses the request ledger.
    raise HTTPException(403, 'ROLE_FORBIDDEN')


@app.post('/v1/lab/documents/{document_id}/revoke')
def revoke_document(document_id: UUID,who: Identity=Depends(operator_identity)):
    from .corpus import revoke
    return revoke(who,document_id)


@app.post('/v1/lab/documents/{document_id}/revoke-source')
def revoke_source(document_id: UUID,who: Identity=Depends(operator_identity)):
    from .corpus import revoke
    return revoke(who,document_id,all_versions=True)


@app.post('/v1/lab/session')
def lab_session(body: LabIdentity):
    # Intentionally fixture-only. Production startup is blocked, not just this route.
    now = int(time.time())
    profile=PROFILES[body.profile]
    token = jwt.encode(dict(iss='rag-local-lab',aud='rag-local',sub=body.profile,
        **profile,iat=now,exp=now+3600),secret('jwt_lab'),algorithm='HS256')
    return dict(token=token,profile=body.profile,**profile,mode='LAB_SYNTHETIC_ONLY')


@app.get('/health/live')
def live(): return dict(status='alive')


@app.get('/health/ready')
def ready(): return service.health()


@app.get('/internal/metrics',include_in_schema=False)
def metrics():
    if os.environ.get('RAG_OBSERVABILITY')!='local_lab':
        raise HTTPException(404,'NOT_FOUND')
    from .observability import snapshot
    return Response(snapshot()[0],media_type='text/plain; version=0.0.4')


@app.get('/internal/ops',include_in_schema=False)
def operational():
    if os.environ.get('RAG_OBSERVABILITY')!='local_lab':
        raise HTTPException(404,'NOT_FOUND')
    from .observability import snapshot
    return {'alerts':snapshot()[1]}


@app.get('/v1/events')
def events(after: int=Query(default=0,ge=0,le=9223372036854775807),who: Identity=Depends(client_identity)):
    from .delivery import read
    return read(who,after)


@app.post('/v1/requests',status_code=202)
def submit(body: Question, idempotency_key: str = Header(max_length=80), who: Identity = Depends(client_identity)):
    return service.submit(who,body.question,idempotency_key)


@app.get('/v1/requests')
def recent(who: Identity = Depends(client_identity)): return service.status(who)


@app.post('/v1/requests/resolve')
def resolve(idempotency_key: str = Header(max_length=80), who: Identity = Depends(client_identity)):
    return service.resolve(who,idempotency_key)


@app.get('/v1/requests/{rid}')
def status(rid: UUID, who: Identity = Depends(client_identity)): return service.status(who,rid)


@app.post('/v1/requests/{rid}/cancel')
def cancel(rid: UUID, who: Identity = Depends(client_identity)): return service.cancel(who,rid)
