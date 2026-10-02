# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""One bounded synthetic selector diagnostic; never emit credentials or raw errors."""
import asyncio
import json
import logging
import re
from uuid import uuid4
from unittest.mock import patch
from rag_app import gemini_grounded as g

logging.disable(logging.CRITICAL)
original=g.call_model
async def diagnostic(key,payload):
    try:return await original(key,payload)
    except Exception as error:
        message=str(getattr(error,'message','')).replace(key,'[REDACTED]')
        message=re.sub(r'AIza[A-Za-z0-9_-]+','[REDACTED]',message)
        message=re.sub(r'https?://\S+','[URL]',message)
        code=getattr(error,'code',None)
        print(json.dumps({'event':'selector_diagnostic','type':type(error).__name__,
            'code':code if isinstance(code,int) else None,'message':message[:900]}))
        raise

row={'id':str(uuid4()),'document_id':str(uuid4()),'release_id':str(uuid4()),
     'content_hash':'synthetic','acl_epoch':1,'title':'Alimentação',
     'quote':'O limite de alimentação é de 45 reais por pessoa.'}
with patch.object(g,'call_model',diagnostic):
    proposal=asyncio.run(g.select({'id':str(uuid4()),'question':'Qual o limite de alimentação?'},[row]))
print(json.dumps({'kind':proposal.kind,'model':proposal.model}))
