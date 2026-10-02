"""Disposable signed Redis values; a miss/outage never grants access."""
import hashlib
import hmac
import json
import threading
import time
from .config import secret
from .resilience import enabled

EVENTS=frozenset(('cache_miss','cache_hit','cache_unavailable_or_invalid',
    'broker_published','broker_invalid','broker_duplicate','broker_ack','broker_retry_durable_sql'))
_counts={}
_lock=threading.Lock()
_flush_lock=threading.Lock()
_started=False

def flush_events():
    """Best-effort telemetry only: request durability NEVER uses this buffer."""
    if not _flush_lock.acquire(blocking=False): return False
    batch={}
    try:
        with _lock:
            batch.update(_counts); _counts.clear()
        if not batch: return True
        from . import ledger
        with ledger.connect() as db:
            for name,count in batch.items():
                db.execute('INSERT INTO resilience_events(name,value) VALUES (%s,%s) ON CONFLICT(name) DO UPDATE SET value=resilience_events.value+EXCLUDED.value',(name,count))
        return True
    except Exception:
        with _lock:
            for name,count in batch.items(): _counts[name]=min(1000000,_counts.get(name,0)+count)
        return False
    finally:
        _flush_lock.release()

def _flush_loop():
    while True:
        time.sleep(5)
        flush_events()

def client():
    import redis
    return redis.Redis(host='redis',port=6379,socket_connect_timeout=.15,
                       socket_timeout=.15,retry_on_timeout=False)

def key(kind,scope,payload):
    raw=json.dumps([kind,scope,payload],sort_keys=True,separators=(',',':')).encode()
    return 'rag:v1:'+hashlib.sha256(raw).hexdigest()

def event(name):
    global _started
    if not enabled() or name not in EVENTS: return
    with _lock:
        _counts[name]=min(1000000,_counts.get(name,0)+1)
        if not _started:
            try:
                threading.Thread(target=_flush_loop,daemon=True,name='rag-metric-flush').start()
                _started=True
            except RuntimeError:
                pass  # A telemetry thread cannot become an availability dependency.

def get(cache_key):
    if not enabled(): return None
    try:
        raw=client().get(cache_key)
        if not raw:
            event('cache_miss'); return None
        if len(raw)>1048576: raise ValueError('CACHE_SIZE')
        envelope=json.loads(raw)
        payload=envelope['payload']
        signed=json.dumps([cache_key,payload],sort_keys=True,separators=(',',':')).encode()
        if not hmac.compare_digest(envelope['mac'],hmac.new(secret('cache_signing').encode(),signed,hashlib.sha256).hexdigest()):
            raise ValueError('CACHE_SIGNATURE')
        event('cache_hit')
        return payload
    except Exception:
        event('cache_unavailable_or_invalid')
        return None

def put(cache_key,payload):
    if not enabled(): return
    try:
        signed=json.dumps([cache_key,payload],sort_keys=True,separators=(',',':')).encode()
        raw=json.dumps(dict(payload=payload,mac=hmac.new(secret('cache_signing').encode(),signed,hashlib.sha256).hexdigest()))
        if len(raw.encode())>1048576: return
        client().set(cache_key,raw,ex=300)
    except Exception:
        event('cache_unavailable_or_invalid')
