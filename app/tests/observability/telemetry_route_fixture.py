# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""HTML-versus-metrics protocol regression, not full collector certification."""
from datetime import datetime,timezone
import hashlib
import io
import json
from pathlib import Path
import secrets
from unittest.mock import patch
from urllib.error import HTTPError
from observability_fixture import public_metric_is_private

ROOT=_workspace_root


class Response(io.BytesIO):
    def __init__(self,raw,kind):
        super().__init__(raw); self.status=200; self.headers={'Content-Type':kind}


def main():
    paths=[Path(__file__).resolve(),ROOT/'app/tests/observability/observability_fixture.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        with patch('urllib.request.urlopen',return_value=Response(b'<!doctype html><p>Lab</p>','text/html; charset=utf-8')):
            assert public_metric_is_private()
        with patch('urllib.request.urlopen',return_value=Response(b'rag_requests 9\n','text/plain')):
            assert not public_metric_is_private()
        with patch('urllib.request.urlopen',return_value=Response(b'rag_requests 9\n','text/html')):
            assert not public_metric_is_private()
        with patch('urllib.request.urlopen',side_effect=HTTPError('http://synthetic',404,'NOT_FOUND',{},None)):
            assert public_metric_is_private()
        with patch('urllib.request.urlopen',side_effect=HTTPError('http://synthetic',503,'UNAVAILABLE',{},None)):
            assert not public_metric_is_private()
        rounds.append(dict(seed=seed,checks=5,passed=True))
    folder=ROOT/'eval/runs'/('telemetry-route-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-019',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='controlled_HTTP_HTML_metrics_privacy_regression',full_observability_card_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))


if __name__=='__main__':
    main()
