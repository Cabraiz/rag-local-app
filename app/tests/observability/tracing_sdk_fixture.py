# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Actual ADK graph and OTel SDK correlation regression; retrieval injected, not full RAG."""
import asyncio
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
from unittest.mock import patch
from uuid import uuid4

ROOT=_workspace_root
sys.path.insert(0,str(ROOT/'app/src'))
from rag_app.adk_workflow import AdkRetrievalWorkflow
from rag_app.observability import sanitized
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def main():
    paths=[Path(__file__).resolve(),ROOT/'app/src/rag_app/models/adk_workflow.py',ROOT/'app/src/rag_app/runtime/observability.py']
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    exporter=InMemorySpanExporter(); provider=TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter)); trace.set_tracer_provider(provider)
    tracer=trace.get_tracer('synthetic-fixture'); rounds=[]
    for seed in (secrets.randbits(32),secrets.randbits(32)):
        exporter.clear(); marker='sensitive'+str(seed)
        request=dict(id=uuid4(),tenant='demo-a',actor='demo-user',fence=1,question=marker)
        evidence=dict(document_id=uuid4(),id=uuid4(),release_id=uuid4(),content_hash='a'*64,acl_epoch=0,quote='synthetic quote',title='synthetic',ordinal=0)
        with tracer.start_as_current_span('rag.request') as root:
            root.set_attribute('prompt',marker); root.add_event('private-event',{'token':marker})
            with patch('rag_app.corpus.retrieve',return_value=[evidence]):
                proposal=asyncio.run(AdkRetrievalWorkflow().run(request))
        assert proposal.kind=='EXTRACTIVE'
        spans=exporter.get_finished_spans()
        payload=sanitized(spans); values=payload['resourceSpans'][0]['scopeSpans'][0]['spans']
        assert {s['name'] for s in values}=={'rag.request','rag.retrieve','rag.compose','rag.verify'}
        assert len(values)==4
        root=next(s for s in values if s['name']=='rag.request')
        assert all(s['traceId']==root['traceId'] and s.get('parentSpanId')==root['spanId'] for s in values if s!=root)
        assert marker not in json.dumps(payload)
        assert all('attributes' not in s and 'events' not in s and set(s['status'])=={'code'} for s in values)
        rounds.append(dict(seed=seed,actual_adk_graph=True,root_plus_stages=4,parentage_passed=True,redaction_passed=True))
    folder=ROOT/'eval/runs'/('tracing-sdk-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True)
    receipt=dict(card_id='BUG-018',evidence_type='verified_regression',complete=True,consecutive_passes=2,
        criteria_passed=['reproduction','two_regression_rounds'],sources_sha256=sources,rounds=rounds,
        independent_blind=False,scope='actual_ADK_OTel_SDK_with_injected_retrieval',full_observability_card_passed=False)
    path=folder/'receipt.json'; path.write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps({'receipt':str(path),'streak':2}))


if __name__=='__main__':
    main()
