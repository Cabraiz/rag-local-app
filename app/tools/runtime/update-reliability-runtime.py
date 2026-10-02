# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Apply a proven image to the existing LOCAL lab; never enable production.

No volume deletion, billing, gateway mutation or model invocation. Schema
adoption is additive and runs only while the local core roles are stopped.
Docker remains the external supervisor. Require fresh two-round frozen proof.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT=_workspace_root
sys.path.insert(0,str(ROOT/'app/tests'))
import resilience_runtime_proof as runtime

FILES=['infrastructure/compose/runtime/compose.yaml','infrastructure/compose/runtime/compose.retrieval.yaml','infrastructure/compose/runtime/compose.semantic.yaml',
       'infrastructure/compose/runtime/compose.integrations.yaml','infrastructure/compose/labs/compose.gemini-rag.yaml','infrastructure/compose/runtime/compose.documents.yaml',
       'infrastructure/compose/runtime/compose.delivery.yaml','infrastructure/compose/observability/compose.observability.yaml','infrastructure/compose/resilience/compose.resilience.yaml',
       'infrastructure/compose/resilience/compose.resilience-integrations.yaml','infrastructure/compose/observability/compose.grafana.yaml']
COMPOSE=['docker','compose', '--project-directory', str(APP),'--ansi','never','--progress','plain','-p','rag-local-v2']
for name in FILES: COMPOSE+=['-f',str(ROOT/'app'/name)]
ROLES=['api','worker','control','delivery','relay']


def command(args,check=True):
    result=subprocess.run(args,capture_output=True,text=True,timeout=180,shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if check and result.returncode: raise RuntimeError('LOCAL_RUNTIME_COMMAND_FAILED')
    return result.stdout.strip()


def baseline():
    return dict(corpus={t:runtime.digest(t) for t in ('corpus_documents','corpus_heads','corpus_releases','corpus_chunks')},
                ids=runtime.sql("SELECT COALESCE(json_agg(id ORDER BY id),'[]'::json) FROM requests"))


def model_usage():
    return runtime.container_json('rag-local-v2-worker-1',
        "import sqlite3,json; db=sqlite3.connect('file:/usage/gemini-probes.sqlite3?mode=ro',uri=True); print(json.dumps(db.execute('SELECT day,used FROM attempts ORDER BY day').fetchall()))")


def wait_ready():
    end=time.monotonic()+90
    while time.monotonic()<end:
        try:
            ready=runtime.http('/health/ready')
            if ready['production_ready'] is False and ready['admission_policy']=='partitioned_count_bytes_v3': return ready
        except Exception: pass
        time.sleep(1)
    raise RuntimeError('LOCAL_READY_DEADLINE')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--receipt',required=True)
    parser.add_argument('--apply-local',action='store_true',required=True)
    args=parser.parse_args()
    receipt_path=Path(args.receipt).resolve()
    assert receipt_path.is_relative_to(ROOT/'eval'), 'RECEIPT_OUTSIDE_EVAL'
    proof=json.loads(receipt_path.read_text())
    assert proof['passed'] and proof['complete'] and proof['consecutive_passes']==2 and len(proof['rounds'])==2, 'TWO_CLEAN_ROUNDS_REQUIRED'
    assert all(r['passed'] and len(r['rag_checks'])==44 and all(c['passed'] for c in r['rag_checks'])
               and all(p['lost']==0 and p['duplicate_terminals']==0 and p['success_fraction']>=.99
                       and (p['phase']!='postgres_kill' or p['postgres_recovery']['passed'])
                       for p in r['phases'])
               and r['restore']['passed'] and r['migration_restart']['passed']
               for r in proof['rounds']), 'INCOMPLETE_RELIABILITY_PROOF'
    for name,digest in proof['source_hashes'].items():
        path=(ROOT/name).resolve()
        assert path.is_relative_to(ROOT) and hashlib.sha256(path.read_bytes()).hexdigest()==digest, 'SOURCE_CHANGED_RESET_STREAK'
    candidate=command(['docker','image','inspect','rag-local-reliability-qa:20261002','--format','{{.Id}}'])
    assert candidate in proof['images'], 'UNTESTED_IMAGE'
    # Verify embedded source, not just a mutable Docker tag's name.
    embedded=json.loads(command(['docker','run','--rm','--network','none','--read-only',candidate,'python','-c',
        "import hashlib,json,pathlib,importlib; print(json.dumps({n:hashlib.sha256(pathlib.Path(importlib.import_module('rag_app.'+n[:-3]).__file__).read_bytes()).hexdigest() for n in ('ledger.py','process.py','broker.py','resilience.py')}))"]))
    layout={'ledger.py':'persistence','process.py':'runtime','broker.py':'runtime','resilience.py':'runtime'}
    assert all(embedded[n]==proof['source_hashes']['app/src/rag_app/'+layout[n]+'/'+n] for n in embedded), 'IMAGE_SOURCE_MISMATCH'
    folder=ROOT/'eval/runs'/('reliability-local-rollout-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(exist_ok=False)
    before=baseline(); usage_before=model_usage(); ready_before=runtime.http('/health/ready')
    old=command(['docker','inspect','rag-local-v2-api-1','--format','{{.Image}}'])
    report=dict(passed=False,at_utc=datetime.now(timezone.utc).isoformat(),gate=str(receipt_path),
                candidate_image=candidate,previous_image=old,production_enabled=False,gemini_calls=0)
    try:
        command(COMPOSE+['stop','--timeout','45',*ROLES])
        command(['docker','tag',candidate,'rag-local-resilience:0.1.0'])
        # First adoption of migration checksums is performed with core roles
        # quiescent. Subsequent dependency restarts are no-ops, proven in QA.
        command(COMPOSE+['run','--rm','--no-deps','-T','migrate'])
        command(COMPOSE+['up','-d','--no-build','--no-deps',*ROLES])
        command(COMPOSE+['restart','--no-deps','frontend'])
        ready=wait_ready()
        rid=runtime.container_json('rag-local-v2-api-1',
            "import json,time; from uuid import uuid4; from rag_app import ledger; from rag_app.domain import Identity; who=Identity('reliability-runtime','proof'); print(json.dumps(ledger.accept(who,'SYNTHETIC reliability runtime smoke',str(int(time.time()))+'.'+str(uuid4()))))")
        end=time.monotonic()+90; result={}
        while time.monotonic()<end:
            result=runtime.container_json('rag-local-v2-api-1',
                "import json; from rag_app import ledger; from rag_app.domain import Identity; r=ledger.read(Identity('reliability-runtime','proof'),"+repr(rid)+"); print(json.dumps({'state':r['state'],'kind':r['result']['kind'] if r['result'] else None}))")
            if result['state']=='SUCCEEDED': break
            time.sleep(1)
        after=baseline()
        images={name:command(['docker','inspect','rag-local-v2-'+name,'--format','{{.Image}}'])
                for name in ('api-1','worker-1','worker-2','control-1','delivery-1','relay-1')}
        checks=dict(corpus_conserved=before['corpus']==after['corpus'],
                    all_previous_receipts_retained=set(before['ids'])<=set(after['ids']),
                    exact_tested_image=all(v==candidate for v in images.values()),
                    still_lab=ready['production_ready'] is False,
                    workflow_preserved=ready['workflow']==ready_before['workflow'],
                    synthetic_smoke=result=={'state':'SUCCEEDED','kind':'ABSTAIN'},
                    no_model_calls=model_usage()==usage_before,
                    queue_conserved=runtime.sql("SELECT to_json((SELECT count(*) FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT'))=(SELECT sum(pending) FROM admission_shards))"))
        with urllib.request.urlopen('http://127.0.0.1:8850/api/health',timeout=5) as response:
            checks['grafana_healthy']=json.load(response)['database']=='ok'
        report.update(checks=checks,images=images,old_receipts=len(before['ids']),corpus=after['corpus'],smoke_request_id=rid)
        assert all(checks.values()), 'LOCAL_POST_ROLLOUT_CHECK_FAILED'
        report['passed']=True
    except Exception as error:
        report['error_type']=type(error).__name__
        report['error_code']=str(error) if isinstance(error,AssertionError) else 'LOCAL_ROLLOUT_FAILED'
        # Same prior immutable image: preserve availability, never remove data.
        command(['docker','tag',old,'rag-local-resilience:0.1.0'],check=False)
        command(COMPOSE+['up','-d','--no-build','--no-deps',*ROLES],check=False)
        command(COMPOSE+['restart','--no-deps','frontend'],check=False)
        report['rollback_attempted']=True
    finally:
        path=folder/'receipt.json'; path.write_text(json.dumps(report,indent=2))
    print(json.dumps(dict(passed=report['passed'],receipt=str(path))))
    if not report['passed']: raise SystemExit(1)


if __name__=='__main__': main()
