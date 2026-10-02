# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Actual OTel/ADK/process status regression with injected ledger/model failures."""
import asyncio
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import sys
from contextlib import redirect_stdout
from unittest.mock import patch
from uuid import uuid4

ROOT=_workspace_root
sys.path.insert(0,str(ROOT/'app/src'))
from rag_app import process
from rag_app.adk_workflow import AdkRetrievalWorkflow
from rag_app.domain import RequestError,Proposal
from rag_app.observability import sanitized
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def main():
    paths=[Path(__file__).resolve(),ROOT/'app/src/rag_app/runtime/process.py',ROOT/'app/src/rag_app/models/adk_workflow.py',ROOT/'app/src/rag_app/runtime/observability.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    exporter=InMemorySpanExporter(); provider=TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter)); trace.set_tracer_provider(provider)
    tracer=trace.get_tracer('synthetic-status-fixture'); rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        exporter.clear(); marker='private-error'+str(seed)
        for status in (trace.StatusCode.UNSET,trace.StatusCode.OK,trace.StatusCode.ERROR):
            with tracer.start_as_current_span('rag.request') as span:
                span.set_status(trace.Status(status,marker if status==trace.StatusCode.ERROR else None))
        payload=sanitized(exporter.get_finished_spans())
        assert [s['status']['code'] for s in payload['resourceSpans'][0]['scopeSpans'][0]['spans']]==[0,1,2]
        assert marker not in json.dumps(payload)
        request=dict(id=uuid4(),tenant='demo-a',actor='demo-user',fence=1,question='synthetic')
        exporter.clear()
        with patch('rag_app.corpus.retrieve',side_effect=RequestError('INDEX_UNAVAILABLE',503)),redirect_stdout(io.StringIO()):
            try:
                asyncio.run(AdkRetrievalWorkflow().run(request))
                raise AssertionError('Failed retrieval unexpectedly succeeded')
            except RequestError:
                pass
        stage=next(s for s in exporter.get_finished_spans() if s.name=='rag.retrieve')
        assert stage.status.status_code==trace.StatusCode.ERROR
        for fail,commit in ((True,False),(False,False),(False,True)):
            exporter.clear(); logs=io.StringIO()
            class Workflow:
                async def run(self,row):
                    if fail:
                        raise RuntimeError(marker)
                    return Proposal('ABSTAIN','synthetic')
            class Ledger:
                def heartbeat(self,role): pass
                def claim(self): return request
                def finish(self,*args): return commit
            def stop_after_iteration(_):
                process.stopping=True
            with patch.dict(os.environ,{'RAG_MODE':'lab','RAG_OBSERVABILITY':'disabled'}),patch.object(process,'stopping',False),patch.object(process.sys,'argv',['process','worker']),patch.object(process,'process_services',return_value=(Ledger(),Workflow())),patch.object(process.signal,'signal'),patch.object(process.time,'sleep',side_effect=stop_after_iteration),redirect_stdout(logs):
                process.main()
            root=next(s for s in exporter.get_finished_spans() if s.name=='rag.request')
            assert root.status.status_code==(trace.StatusCode.OK if not fail and commit else trace.StatusCode.ERROR)
            assert marker not in logs.getvalue() and marker not in json.dumps(sanitized(exporter.get_finished_spans()))
        rounds.append(dict(seed=seed,enum_mapping=True,actual_adk_error_status=True,process_failure_and_stale_fence=True,success_status=True,redaction=True,passed=True))
    folder=ROOT/'eval/runs'/('tracing-status-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3)); folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-028',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='actual_ADK_OTel_process_with_injected_failures',full_observability_card_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))


if __name__=='__main__':
    main()
