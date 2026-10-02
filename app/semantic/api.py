"""Internal bounded embedding RPC. No secrets, document persistence or egress."""
import json
import logging
import threading
from pathlib import Path
from fastapi import FastAPI,HTTPException,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,field_validator
from engine import Encoder,VERSION

logging.disable(logging.CRITICAL)
encoder=Encoder(); gates=json.loads(Path('/srv/gates.json').read_text())
if gates['model_version']!=VERSION: raise RuntimeError('MODEL_GATE_MISMATCH')
slots=threading.BoundedSemaphore(2)
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

class Batch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    texts:list[str]=Field(min_length=1,max_length=32)
    @field_validator('texts')
    @classmethod
    def bounded(cls,values):
        if any(not v.strip() or len(v)>2048 or len(v.encode())>4096 for v in values) or sum(len(v.encode()) for v in values)>32768: raise ValueError('INPUT_LIMIT')
        return values

@app.exception_handler(RequestValidationError)
async def invalid(request,exception):
    return JSONResponse({'code':'INVALID_EMBEDDING_INPUT'},status_code=422)

@app.middleware('http')
async def body_limit(request:Request,call_next):
    if request.method=='POST':
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>65536: return JSONResponse({'code':'EMBEDDING_BODY_LIMIT'},status_code=413)
        request._body=bytes(raw)
    return await call_next(request)

@app.get('/health/ready')
def health(): return dict(model_version=VERSION,offline=True)

@app.post('/v1/embeddings')
def embeddings(batch:Batch):
    if not slots.acquire(blocking=False): raise HTTPException(429,'EMBEDDING_BUSY')
    try:
        return dict(vectors=encoder.embed(batch.texts),**gates)
    except Exception:
        raise HTTPException(503,'EMBEDDING_FAILED') from None
    finally:
        slots.release()
