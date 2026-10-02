"""Shared, bounded failure isolation. No provider URLs or exception text in SQL."""
from contextlib import contextmanager
import hashlib
import os
import random
import sys
import threading
import time
from uuid import uuid4
from .domain import RequestError

LIMITS = {'embedding':4, 'qdrant':4, 'gemini':2, 'github':1, 'jira':1}
_rate_lock=threading.Lock()
_rates={}

def admit_http(identity):
    if not enabled(): return
    now=time.monotonic(); key=(identity.tenant,identity.actor)
    with _rate_lock:
        if len(_rates)>=1024 and key not in _rates:
            for old in list(_rates):
                if now-_rates[old][1]>60: del _rates[old]
            if len(_rates)>=1024: raise RequestError('API_CAPACITY_LIMIT',429)
        tokens,stamp=_rates.get(key,(32,now))
        tokens=min(32,tokens+(now-stamp)*3)
        _rates[key]=(tokens,now)
        if tokens<1: raise RequestError('REQUEST_RATE_LIMIT',429)
        _rates[key]=(tokens-1,now)

def enabled():
    return os.environ.get('RAG_RESILIENCE') == 'local_lab'

def heartbeat_due(last,now):
    return not enabled() or now-last>=5

def bucket(tenant):
    # Non-security partitioning, identical to SQL's built-in md5 initialization.
    return int(hashlib.md5(tenant.encode(), usedforsecurity=False).hexdigest()[:8],16)%8

def delay(attempt, rng=None):
    return (rng or random.SystemRandom()).uniform(1, min(60, 2**min(attempt+1,6)))

def broker_reconnect_delay(attempt, rng=None):
    """A down broker must not impose a new connection timeout on every job."""
    ceiling=min(30, 5*2**min(max(attempt,1),3))
    return (rng or random.SystemRandom()).uniform(ceiling/2,ceiling)

def transient(error, depth=0):
    import httpx
    import psycopg
    if isinstance(error,BaseExceptionGroup):
        return depth<8 and any(transient(e,depth+1) for e in error.exceptions[:32])
    if isinstance(error,(TimeoutError,psycopg.OperationalError)): return True
    if isinstance(error,RequestError): return error.status in (429,503,504)
    # MCP 2 uses httpx2; do not require its optional dependencies in the core.
    for module in (httpx,sys.modules.get('httpx2')):
        if module is None: continue
        if isinstance(error,module.HTTPStatusError):
            return error.response.status_code==429 or 500<=error.response.status_code<600
        if isinstance(error,module.TransportError): return True
    if type(error).__module__.startswith('google.genai.'):
        code=getattr(error,'code',None)
        return isinstance(code,int) and (code==429 or 500<=code<600)
    if type(error).__module__=='rag_app.atlassian_lab' and error.args:
        return error.args[0]=='REMOTE_429'
    return False

@contextmanager
def guard(name):
    if not enabled():
        yield
        return
    if name not in LIMITS:
        raise ValueError('UNKNOWN_DEPENDENCY')
    from . import ledger
    slot = uuid4()
    rejected = None
    generation=None
    with ledger.connect() as db:
        db.execute('INSERT INTO dependency_control(name) VALUES (%s) ON CONFLICT DO NOTHING',(name,))
        state=db.execute('SELECT *,clock_timestamp() AS now FROM dependency_control WHERE name=%s FOR UPDATE',(name,)).fetchone()
        db.execute('DELETE FROM dependency_slots WHERE name=%s AND expires<=clock_timestamp()',(name,))
        if state['open_until'] and state['open_until']>state['now']:
            rejected='DEPENDENCY_CIRCUIT_OPEN'
        elif state['open_until'] and state['probe_until'] and state['probe_until']>state['now']:
            rejected='DEPENDENCY_PROBE_BUSY'
        elif db.execute('SELECT count(*) AS n FROM dependency_slots WHERE name=%s',(name,)).fetchone()['n']>=LIMITS[name]:
            rejected='DEPENDENCY_CONCURRENCY_LIMIT'
        else:
            if state['open_until']:
                state=db.execute("UPDATE dependency_control SET generation=generation+1,probe_until=clock_timestamp()+interval '60 seconds' WHERE name=%s RETURNING *",(name,)).fetchone()
            generation=state['generation']
            db.execute("INSERT INTO dependency_slots(id,name,expires,generation) VALUES (%s,%s,clock_timestamp()+interval '60 seconds',%s)",(slot,name,generation))
    if rejected:
        raise RequestError(rejected,503)
    failure = None
    try:
        yield
    except BaseException as error:
        # Cancellation counts as an uncertain dependency call, but propagates.
        failure=error
        raise
    finally:
        with ledger.connect() as db:
            state=db.execute('SELECT * FROM dependency_control WHERE name=%s FOR UPDATE',(name,)).fetchone()
            live=db.execute('DELETE FROM dependency_slots WHERE id=%s RETURNING id',(slot,)).fetchone()
            if live and state['generation']==generation:
                if failure is None:
                    db.execute('UPDATE dependency_control SET failures=0,open_until=NULL,probe_until=NULL,generation=generation+CASE WHEN open_until IS NOT NULL THEN 1 ELSE 0 END WHERE name=%s',(name,))
                elif transient(failure) or not isinstance(failure,Exception):
                    db.execute("UPDATE dependency_control SET failures=failures+1,probe_until=NULL,generation=generation+CASE WHEN failures+1>=3 OR open_until IS NOT NULL THEN 1 ELSE 0 END,open_until=CASE WHEN failures+1>=3 OR open_until IS NOT NULL THEN clock_timestamp()+interval '30 seconds' ELSE open_until END WHERE name=%s",(name,))
                else:
                    db.execute('UPDATE dependency_control SET probe_until=NULL WHERE name=%s',(name,))
