# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Bounded diagnostic worker for an exclusive QA project, never a load proof."""
import json
import resilience_fixture as qa
import http_fixture as base

base.COMPOSE+=['-f',str(base.ROOT/'infrastructure/compose/qa/compose.reliability-qa.yaml')]
base.docker('up','-d','--no-build','--wait','--wait-timeout','480','frontend','rabbitmq','redis','embeddings','qdrant','delivery','control','relay')
code='''
import json,time,threading,sys
from rag_app import process,broker,ledger
def measured(label,function):
 def invoke(*args,**kwargs):
  start=time.monotonic()
  try: return function(*args,**kwargs)
  finally:
   seconds=time.monotonic()-start
   print(json.dumps(dict(profile=label,seconds=round(seconds,4))),flush=True)
 return invoke
broker.Consumer.poll=measured('broker_poll',broker.Consumer.poll)
broker.Consumer.outcome=measured('broker_outcome',broker.Consumer.outcome)
ledger.claim=measured('sql_claim',ledger.claim)
ledger.finish=measured('sql_finish',ledger.finish)
timer=threading.Timer(40,lambda:setattr(process,'stopping',True)); timer.start()
sys.argv=['process','worker']
try: process.main()
finally: timer.cancel()
'''
result=base.docker('run','--rm','--no-deps','-T','worker','python','-c',code)
path=base.ROOT.parent/'eval/runs/reliability-worker-profile-20261002.log'
path.write_text(result.stdout,encoding='utf8')
values=[]
for line in result.stdout.splitlines():
    try:
        value=json.loads(line)
        if 'profile' in value: values.append(value)
    except ValueError: pass
summary={name:dict(calls=len(group),seconds=round(sum(v['seconds'] for v in group),3),max_seconds=max(v['seconds'] for v in group))
         for name in sorted({v['profile'] for v in values})
         if (group:=[v for v in values if v['profile']==name])}
print(json.dumps(dict(profile=summary,log=str(path))))
base.docker('stop')
