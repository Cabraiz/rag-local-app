# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Actual failing ADK graph with sensitive sentinel; no remote provider or SQL proof."""
import asyncio
from datetime import datetime,timezone
import hashlib
import io
import json
import logging
from pathlib import Path
import secrets
import sys
from contextlib import redirect_stdout,redirect_stderr
from unittest.mock import patch
from uuid import uuid4

ROOT=_workspace_root
sys.path.insert(0,str(ROOT/'app/src'))
from rag_app.adk_workflow import AdkRetrievalWorkflow
from rag_app.safe_logging import setup


def main():
    paths=[Path(__file__).resolve(),ROOT/'app/src/rag_app/runtime/safe_logging.py',ROOT/'app/src/rag_app/models/adk_workflow.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}; rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        marker='SYNTHETIC_PRIVATE_SENTINEL_'+str(seed)
        setup(); stdout=io.StringIO(); stderr=io.StringIO()
        with redirect_stdout(stdout),redirect_stderr(stderr):
            for namespace in ('google.adk','google_adk'):
                logger=logging.getLogger(namespace+'.synthetic')
                logger.warning('private warning %s',marker)
                try:
                    raise ValueError(marker)
                except ValueError:
                    logger.exception('private error %s',marker)
            with patch('rag_app.corpus.retrieve',side_effect=RuntimeError(marker)):
                try:
                    asyncio.run(AdkRetrievalWorkflow().run(dict(id=uuid4(),tenant='demo-a',actor='demo-user',fence=1,question='synthetic')))
                    raise AssertionError('Graph failure swallowed')
                except RuntimeError:
                    pass
        logs=stdout.getvalue()+stderr.getvalue()
        assert marker not in logs and 'Traceback' not in logs
        events=[json.loads(line) for line in stderr.getvalue().splitlines()]
        assert len(events)>=4
        assert all(set(e)=={'event','component','severity','error_type'} and e['component']=='google.adk' for e in events)
        assert any(e['error_type']=='RuntimeError' for e in events)
        assert any(e['error_type']=='ValueError' for e in events)
        stage=json.loads(stdout.getvalue().splitlines()[0])
        assert not stage['ok'] and stage['error_type']=='RuntimeError'
        rounds.append(dict(seed=seed,sensitive_warning_removed=True,exception_message_and_stack_removed=True,
                           actual_adk_failure_redacted=True,structured_class_retained=True,passed=True))
    folder=ROOT/'eval/runs'/('sdk-logging-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-029',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='actual_ADK_failure_log_redaction_with_synthetic_sentinel',full_observability_card_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    namespace=dict(receipt)
    namespace.update(card_id='BUG-030',scope='actual_ADK_dual_namespace_failure_redaction')
    namespace_path=folder/'namespace-receipt.json'; namespace_path.write_text(json.dumps(namespace,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))


if __name__=='__main__':
    main()
