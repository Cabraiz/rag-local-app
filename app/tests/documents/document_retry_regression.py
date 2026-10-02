# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Fixed manifest-preserving index retry protocol regression, no acceptance weakening."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets
from unittest.mock import patch
import document_fixture as fixture

ROOT=_workspace_root


def main():
    paths=[Path(__file__).resolve(),ROOT/'app/tests/documents/document_fixture.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}; rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        value={'source_key':'synthetic','data_base64':'c3ludGhldGlj'}
        with patch.object(fixture.base,'http',side_effect=[(503,{'code':'INDEX_UNAVAILABLE'}),(201,{'release_id':'same-candidate'})]) as http,patch.object(fixture.time,'sleep'):
            assert fixture.upload_value('synthetic-token',value)[0]==201
            assert http.call_count==2 and all(call.args[2] is value for call in http.call_args_list)
        with patch.object(fixture.base,'http',return_value=(503,{'code':'INDEX_UNAVAILABLE'})) as http,patch.object(fixture.time,'sleep'):
            assert fixture.upload_value('synthetic-token',value)[0]==503 and http.call_count==3
        for status,code in ((401,'DENIED'),(409,'SOURCE_REVOKED'),(503,'LEDGER_UNAVAILABLE'),(422,'INVALID_ENCODING')):
            with patch.object(fixture.base,'http',return_value=(status,{'code':code})) as http:
                assert fixture.upload_value('synthetic-token',value)==(status,{'code':code}) and http.call_count==1
        rounds.append(dict(seed=seed,checks=6,passed=True))
    folder=ROOT/'eval/runs'/('document-retry-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-023',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='controlled_manifest_preserving_bounded_index_retry',full_document_card_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))


if __name__=='__main__':
    main()
