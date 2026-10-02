# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Actual ADK/SQL/Qdrant regressions plus bounded shared-collection growth."""
from datetime import datetime,timezone
import hashlib
import json
import secrets
from uuid import UUID
import rag_fixture as rag
import http_fixture as base

def frozen():
    values=rag.frozen()
    path=__import__('pathlib').Path(__file__).resolve()
    values[str(path.relative_to(base.ROOT.parent))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return values

def names():
    return set(rag.script("print(json.dumps([r['name'] for r in corpus.QdrantAdapter().call('GET','/collections')['result']['collections']]))"))

def main():
    if base.docker('ps','--status','running','-q').stdout.strip():
        raise SystemExit('Project already running: refuse ownership')
    folder=base.ROOT.parent/'eval/runs'/('shared-index-real-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True); rag.ACTIVE_FOLDER=folder
    sources=frozen(); seeds=[secrets.randbits(32),secrets.randbits(32)]
    contract=dict(scope='actual_ADK_SQL_Qdrant_shared_layout_regressions',independent_blind=False,seeds=seeds,sources_sha256=sources,oracle_frozen_before_inputs=True)
    (folder/'contract.json').write_text(json.dumps(contract,indent=2),encoding='utf8')
    rounds=[]; error=None
    try:
        base.docker('up','-d','--wait','--wait-timeout','120'); rag.wait_index_ready('shared_initial')
        images=rag.images(); contract['images']=images
        for seed in seeds:
            assert frozen()==sources and rag.images()==images
            before=names(); result=rag.run_round(seed); after=names()
            assert before<=after,'Historical collections were removed'
            assert after-before<={'rgl_shared_lexical_256_v1'},'New release spawned a new per-release collection'
            assert 'rgl_shared_lexical_256_v1' in after
            schema=rag.script("print(json.dumps(corpus.QdrantAdapter().call('GET','/collections/rgl_shared_lexical_256_v1')['result']['payload_schema']))")
            assert {'tenant','actor','release_id'}<=set(schema)
            mapping=rag.script("rid='11111111-1111-1111-1111-111111111111'\nprint(json.dumps([corpus.collection_name(rid,'legacy_release_v1'),corpus.collection_name(rid,'shared_lexical_v1')]))")
            assert mapping==['rgl_'+UUID('11111111-1111-1111-1111-111111111111').hex,'rgl_shared_lexical_256_v1']
            reject=rag.script("try:\n corpus.collection_name('11111111-1111-1111-1111-111111111111','unknown')\nexcept RequestError as e:\n print(json.dumps(e.code))")
            assert reject=='UNKNOWN_INDEX_LAYOUT'
            result['checks'] += [dict(name=name,passed=True) for name in ('legacy_collections_preserved','new_collection_count_bounded','scope_payload_indexes_present','legacy_layout_mapping_preserved','unknown_layout_fail_closed')]
            rounds.append(result)
            (folder/f'round-{seed}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
            print(json.dumps({'checks':len(result['checks']),'streak':len(rounds),'collections':len(after)}),flush=True)
        assert frozen()==sources and rag.images()==images
    except Exception as exc:
        error=str(exc)
    finally:
        base.docker('stop',check=False)
        passed=len(rounds)==2 and not error
        receipt=dict(card_id='BUG-035',evidence_type='verified_regression',complete=passed,consecutive_passes=2 if passed else 0,
            criteria_passed=['reproduction','two_regression_rounds'] if passed else [],sources_sha256=sources,contract=contract,
            rounds=rounds,error=error,volumes_preserved=True,production_passed=False)
        path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
        print(json.dumps({'receipt':str(path),'error':error}),flush=True)
    raise SystemExit(0 if passed else 1)

if __name__=='__main__':
    main()
