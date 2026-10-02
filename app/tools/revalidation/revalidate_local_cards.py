# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Sequential scoped card receipts. No cloud, billing, deletion or deployment.

Run only after semantic_fixture completed and no other writer owns the project.
Any failing test stops this chain, records the failure last, and never approves it.
"""
from datetime import datetime,timezone
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT=_workspace_root
QUEUE=ROOT/'app/tools/cards/card_queue.py'
LOG_FOLDER=None

def run(script,*args):
    result=subprocess.run([sys.executable,str(ROOT/script),*map(str,args)],cwd=ROOT,capture_output=True,text=True,encoding='utf8',timeout=1500,shell=False,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    path=LOG_FOLDER/(Path(script).stem+'-'+secrets.token_hex(2)+'.log')
    path.write_text(result.stdout+'\n'+result.stderr,encoding='utf8')
    values=[]
    for line in result.stdout.splitlines():
        try: values.append(json.loads(line))
        except ValueError: pass
    if result.returncode:
        raise RuntimeError('LOCAL_CHECK_FAILED:'+str(path.relative_to(ROOT)))
    return values[-1] if values else {}

def queue(command,card=None,**kwargs):
    args=[command]
    if card: args+=['--card',card]
    for key,value in kwargs.items(): args+=['--'+key,str(value)]
    value=run('app/tools/cards/card_queue.py',*args)
    print(json.dumps(dict(stage=command,card=card,state=value)),flush=True)
    return value

def approve(card,receipt):
    queue('complete',card,receipt=receipt)

def resume(card):
    queue('resume',card,reason='Revalidacao real de duas rodadas com fontes atuais')

def main():
    global LOG_FOLDER
    parser=argparse.ArgumentParser(); parser.add_argument('semantic_receipt'); args=parser.parse_args()
    path=Path(args.semantic_receipt).resolve()
    if not path.is_relative_to(ROOT/'eval/runs'): raise SystemExit('Receipt outside evaluation folder')
    value=json.loads(path.read_text(encoding='utf8'))
    if value.get('complete') is not True or value.get('consecutive_passes')!=2: raise SystemExit('Semantic slice not passed')
    LOG_FOLDER=ROOT/'eval/runs'/('local-revalidation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); LOG_FOLDER.mkdir(parents=True)
    current='BUG-037'; completed=[]
    try:
        proof=run('app/tests/receipts/semantic_regression_receipt.py',path)['receipt']; approve(current,proof); completed.append(current)
        current='BUG-035'; resume(current)
        proof=run('app/tests/semantic/shared_index_fixture.py')['receipt']; approve(current,proof); completed.append(current)
        current='RAG-06'; resume(current); approve(current,path); completed.append(current)
        current='BUG-026'; resume(current)
        documents=run('app/tests/documents/document_fixture.py')['receipt']
        proof=run('app/tests/receipts/revocation_compat_receipt.py',documents)['receipt']; approve(current,proof); completed.append(current)
        current='RAG-05'; resume(current); approve(current,documents); completed.append(current)
        current='RAG-09'; resume(current)
        proof=run('app/tests/observability/observability_fixture.py')['receipt']; approve(current,proof); completed.append(current)
        current='BUG-032'; resume(current)
        delivery=run('app/tests/lifecycle/delivery_fixture.py')['receipt']
        proof=run('app/tests/receipts/retention_race_receipt.py',delivery)['receipt']; approve(current,proof); completed.append(current)
        current='RAG-10'; resume(current); approve(current,delivery); completed.append(current)
        current='RAG-15'; run('app/tests/cards/segment_fixture.py','--rounds','2')
        queue('review'); state=queue('status')
        (LOG_FOLDER/'receipt.json').write_text(json.dumps(dict(completed=completed,state=state,full_project_complete=False,production_passed=False),indent=2),encoding='utf8')
        print(json.dumps(dict(receipt=str(LOG_FOLDER/'receipt.json'),completed=completed)),flush=True)
    except Exception as exc:
        error=str(exc); failure=LOG_FOLDER/'failure.json'
        failure.write_text(json.dumps(dict(card=current,error=error,completed=completed),indent=2),encoding='utf8')
        queue('bug',current,title='Falha verificada na revalidacao local de '+current,proof=str(failure.relative_to(ROOT)))
        # RAG-15 is blocked already: do not pretend to own its final full-project gate.
        if current!='RAG-15': queue('block',current,reason=error)
        print(json.dumps(dict(failure=str(failure),completed=completed)),flush=True)
        raise SystemExit(1)

if __name__=='__main__': main()
