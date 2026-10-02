# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Bounded real probe; no secret or raw provider error output."""
import asyncio
import json
import logging
import re
from rag_app import gemini_lab as probe

logging.disable(logging.CRITICAL)

async def invoke(key):
    try:
        return await probe.run_adk(key)
    except Exception as error:
        # Only a sanitised diagnostic from a fixed synthetic request. Never
        # expose request URLs, headers, keys or arbitrary SDK repr/tracebacks.
        message = str(getattr(error, 'message', '')).replace(key, '[REDACTED]')
        message = re.sub(r'AIza[A-Za-z0-9_-]+', '[REDACTED]', message)
        message = re.sub(r'https?://\S+', '[URL]', message)
        code = getattr(error, 'code', None)
        print(json.dumps({'event':'provider_diagnostic', 'error_type':type(error).__name__,
            'code':code if isinstance(code,int) else None, 'message':message[:700]}), flush=True)
        raise

try:
    secret, usage = probe.configuration()
    result = asyncio.run(probe.execute(secret, usage, invoke))
    print(json.dumps(result))
except Exception as error:
    print(json.dumps({'passed':False,'error_type':type(error).__name__}))
    raise SystemExit(1)
