"""Private read-only MCP gateway, durable bounded snapshots, explicit errors."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import time
import jwt
from mcp import types
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from .config import enforce_lab, secret
from .lab_roles import resource_identity
from .remote_feed import fetch, safe_failure
from . import resilience

enforce_lab()
logging.disable(logging.CRITICAL)  # Provider exceptions/headers never reach raw logs.
PATH=Path('/syncdata/feed.sqlite')
EVENT=asyncio.Event()
NEXT={p:0.0 for p in ('jira','github')}
LAST_MANUAL=0.0


def now():return datetime.now(timezone.utc).isoformat()


def initialize():
    with sqlite3.connect(PATH) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS snapshots(provider TEXT PRIMARY KEY,body TEXT NOT NULL)')


def store(provider,body):
    raw=json.dumps(body,separators=(',',':'))
    if len(raw.encode())>1048576:raise RuntimeError('SNAPSHOT_LIMIT')
    with sqlite3.connect(PATH,timeout=3) as db:
        db.execute('INSERT INTO snapshots VALUES (?,?) ON CONFLICT(provider) DO UPDATE SET body=excluded.body',(provider,raw))


def snapshot():
    with sqlite3.connect(PATH,timeout=3) as db:
        stored={p:json.loads(raw) for p,raw in db.execute('SELECT provider,body FROM snapshots')}
    providers=[]
    for provider in ('jira','github'):
        value=stored.get(provider,{'id':provider,'state':'not_checked','items':[],'last_success':None,'last_attempt':None,'reason':None,'complete':False})
        value={**value,'scope':'KAN' if provider=='jira' else 'Cabraiz/rag-mcp-lab','transport':'remote_mcp','read_only':True}
        stamp=value.get('last_success')
        value['stale']=not stamp or (datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds()>180
        if value['state']!='connected':value['items']=[]  # No old data after access failure.
        providers.append(value)
    return {'mode':'lab','verification':'live_remote_mcp','providers':providers,'poll_seconds':60,
            'max_items_per_provider':500,'rag_answer_workflow_connected':False,'production_certified':False}


async def sync(provider):
    previous=next(v for v in snapshot()['providers'] if v['id']==provider)
    attempted=now()
    try:
        with resilience.guard(provider):
            value=await asyncio.wait_for(fetch(provider),timeout=40)
        body={'id':provider,'state':'connected','items':value['items'],'complete':value['complete'],
              'pages':value['pages'],'last_success':now(),'last_attempt':attempted,'reason':None}
        NEXT[provider]=time.monotonic()+60
    except Exception as error:
        reason=safe_failure(error)
        body={'id':provider,'state':'error','items':[],'complete':False,'last_success':previous.get('last_success'),
              'last_attempt':attempted,'reason':reason}
        NEXT[provider]=time.monotonic()+(3600 if reason in ('REMOTE_401','REMOTE_403','TOKEN_LOCAL_EXPIRY') else 120)
    store(provider,body)
    print(json.dumps({'event':'mcp_sync','provider':provider,'state':body['state'],'items':len(body['items']),
                      'complete':body['complete'],'reason':body['reason']}),flush=True)


async def poll():
    while True:
        due=[sync(provider) for provider in ('github','jira') if time.monotonic()>=NEXT[provider]]
        if due: await asyncio.gather(*due)
        EVENT.clear()
        try:await asyncio.wait_for(EVENT.wait(),timeout=2)
        except TimeoutError:pass


@asynccontextmanager
async def lifespan(server):
    initialize();task=asyncio.create_task(poll())
    try:yield {}
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):await task


server=MCPServer('RAG integration gateway',version='0.1.0',lifespan=lifespan,log_level='ERROR')


@server.tool(annotations=types.ToolAnnotations(read_only_hint=True,destructive_hint=False,idempotent_hint=True,open_world_hint=False))
def integration_feed() -> dict:
    """Read cached real Jira cards and GitHub PRs from the fixed allowed sources."""
    return snapshot()


@server.tool(annotations=types.ToolAnnotations(read_only_hint=True,destructive_hint=False,idempotent_hint=True,open_world_hint=False))
def refresh_integrations() -> dict:
    """Request a bounded read-only refresh; no remote write or LLM call."""
    global LAST_MANUAL
    current=time.monotonic()
    if current-LAST_MANUAL<15:return {'queued':False,'reason':'REFRESH_COOLDOWN','retry_after':15}
    LAST_MANUAL=current
    for provider in NEXT:NEXT[provider]=0.0
    EVENT.set()
    return {'queued':True,'read_only':True}


mcp_app=server.streamable_http_app(json_response=True,stateless_http=True,max_request_body_size=16384,
    transport_security=TransportSecuritySettings(allowed_hosts=['integration-gateway:8000','127.0.0.1:8000'],allowed_origins=[]))


class PrivateGateway:
    def __init__(self,inner):self.inner=inner
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.inner(scope,receive,send)
        if scope['path']=='/health/live':return await JSONResponse({'status':'alive'})(scope,receive,send)
        headers=dict(scope['headers']);auth=headers.get(b'authorization',b'').decode('ascii',errors='ignore')
        try:
            if not auth.startswith('Bearer '):raise ValueError()
            claims=jwt.decode(auth[7:],secret('jwt_lab'),algorithms=['HS256'],audience='rag-local',issuer='rag-local-lab',
                options={'require':['exp','iat','sub','tenant','role','aud','iss']})
            resource_identity(claims,'operator')
        except PermissionError:return await JSONResponse({'code':'ROLE_FORBIDDEN'},status_code=403)(scope,receive,send)
        except (ValueError,jwt.PyJWTError):return await JSONResponse({'code':'INVALID_TOKEN'},status_code=401)(scope,receive,send)
        return await self.inner(scope,receive,send)


app=PrivateGateway(mcp_app)
