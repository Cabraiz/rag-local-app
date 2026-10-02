# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Two measured boots of preserved Qdrant data, no production/HA claim."""
from datetime import datetime,timezone
import hashlib
import json
import secrets
import time
import http_fixture as base

base.COMPOSE += ['-f',str(base.ROOT/'infrastructure/compose/runtime/compose.retrieval.yaml')]
CODE="""import json,httpx
try:
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=3) as client:
        response=client.get('http://qdrant:6333/collections')
        if response.status_code!=200 or len(response.content)>1048576: raise ValueError()
        collections=response.json()['result']['collections']
        print(json.dumps({'ready':isinstance(collections,list),'count':len(collections)}))
except Exception:
    print(json.dumps({'ready':False}))
"""

def main():
    if base.docker('ps','--status','running','-q').stdout.strip():
        raise SystemExit('Project already running: refuse ownership')
    paths=[base.ROOT/'infrastructure/compose/runtime/compose.retrieval.yaml',__import__('pathlib').Path(__file__).resolve()]
    sources={str(p.relative_to(base.ROOT.parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    folder=base.ROOT.parent/'eval/runs'/('qdrant-boot-real-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True); rounds=[]; error=None; expected=None
    try:
        for seed in (secrets.randbits(32),secrets.randbits(32)):
            started=time.monotonic(); base.docker('up','-d','qdrant')
            while True:
                result=base.docker('run','--rm','--no-deps','-T','api','python','-c',CODE)
                probe=json.loads(result.stdout.strip().splitlines()[-1])
                elapsed=time.monotonic()-started
                assert elapsed<=120,'Preserved collection boot exceeded unchanged 120-second lab window'
                if probe['ready']:
                    break
                time.sleep(1)
            assert probe['count']>0,'Expected preserved legacy collections'
            if expected is None: expected=probe['count']
            assert probe['count']==expected,'Lost or added collections during boot proof'
            assert all(hashlib.sha256((base.ROOT.parent/name).read_bytes()).hexdigest()==digest for name,digest in sources.items())
            rounds.append(dict(seed=seed,passed=True,elapsed_ms=round(elapsed*1000),preserved_collections=expected))
            print(json.dumps(rounds[-1]),flush=True)
            base.docker('stop','qdrant')
    except Exception as exc:
        error=str(exc)
    finally:
        base.docker('stop','qdrant',check=False)
        receipt=dict(card_id='BUG-034',evidence_type='verified_regression',complete=len(rounds)==2 and not error,
            consecutive_passes=2 if len(rounds)==2 and not error else 0,
            criteria_passed=['reproduction','two_regression_rounds'] if len(rounds)==2 and not error else [],
            sources_sha256=sources,rounds=rounds,error=error,independent_blind=False,
            scope='actual_Qdrant_preserved_collections_120_second_boot',volumes_preserved=True,production_passed=False)
        path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
        print(json.dumps({'receipt':str(path),'error':error}),flush=True)
    raise SystemExit(0 if receipt['complete'] else 1)

if __name__=='__main__':
    main()
