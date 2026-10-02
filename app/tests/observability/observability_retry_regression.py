# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Current QA seed has replaced the legacy HTTP retry; no Docker or collector calls."""
import ast
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets
from unittest.mock import patch
import observability_fixture as fixture

ROOT=_workspace_root


def main():
    paths=[Path(__file__).resolve(),ROOT/'app/tests/observability/observability_fixture.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}; rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        checks=0
        for text in ('synthetic', "quote'); raise RuntimeError('injected') #", 'ação\nsegunda linha'):
            value={'documents':[{'source_key':'synthetic','title':'Synthetic','text':text}]}
            expected={'release_id':'same-candidate'}
            with patch.object(fixture.qa,'script',return_value=expected) as script, \
                    patch.object(fixture.base,'http',side_effect=AssertionError('PUBLIC_HTTP_USED')) as http, \
                    patch.object(fixture.base,'app_python',side_effect=AssertionError('REAL_DOCKER_USED')) as docker:
                status,result=fixture.ingest_traced_source('synthetic-token',value)
                assert status==201 and result is expected and script.call_count==1
                code=script.call_args.args[0]
                compile(code,'offline-qa-seed','exec')
                tree=ast.parse(code)
                ingest=tree.body[0].value.args[0].args[0]
                assert isinstance(ingest,ast.Call) and ingest.func.attr=='ingest'
                assert ast.literal_eval(ingest.args[1])==value['documents']
                assert not http.called and not docker.called
                checks+=5
        # Exceptions must propagate, not fabricate 201 or retry privileged seeds.
        with patch.object(fixture.qa,'script',side_effect=RuntimeError('SYNTHETIC_FAILURE')) as script:
            try:
                fixture.ingest_traced_source('synthetic-token',value)
                raise AssertionError('SEED_FAILURE_HIDDEN')
            except RuntimeError as error:
                assert str(error)=='SYNTHETIC_FAILURE' and script.call_count==1
                checks+=1
        rounds.append(dict(seed=seed,checks=checks,passed=True,legacy_http_retry_path_removed=True))
    folder=ROOT/'eval/runs'/('observability-retry-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-027',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='controlled_current_QA_seed_delegation_legacy_HTTP_retry_removed',full_observability_card_passed=False,
        reproduction='eval/runs/offline-bugs-20261002T023618Z/BUG-027.log')
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))


if __name__=='__main__':
    main()
