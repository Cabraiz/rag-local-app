"""SDK diagnostics are structure only. Never serialize exception text, args or traceback."""
import json
import logging
import re
import sys


class SafeSdkHandler(logging.Handler):
    def emit(self,record):
        error_type=None
        if record.exc_info and record.exc_info[0]:
            candidate=record.exc_info[0].__name__
            error_type=candidate if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}',candidate) else 'Exception'
        severity='WARNING' if record.levelno<=logging.WARNING else 'ERROR' if record.levelno<=logging.ERROR else 'CRITICAL'
        print(json.dumps({'event':'sdk_diagnostic','component':'google.adk','severity':severity,
                          'error_type':error_type}),file=sys.stderr,flush=True)


def setup():
    for namespace in ('google.adk','google_adk'):
        root=logging.getLogger(namespace)
        if not any(isinstance(h,SafeSdkHandler) for h in root.handlers):
            root.handlers=[SafeSdkHandler()]
        root.propagate=False
        root.setLevel(logging.WARNING)
        # SDK children must use the same boundary, including modules imported later.
        for name,value in logging.Logger.manager.loggerDict.items():
            if name.startswith(namespace+'.') and isinstance(value,logging.Logger):
                value.handlers=[]
                value.propagate=True
                value.setLevel(logging.NOTSET)
