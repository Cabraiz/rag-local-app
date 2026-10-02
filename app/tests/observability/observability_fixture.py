# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Actual collector export/scrape/outage proof, no independent or production claim."""
from datetime import datetime, timezone
import hashlib
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import rag_fixture as rag
import http_fixture as base
import current_queue_fixture as qa

PROJECT='rag-local-qa-observability-20261001'
base.COMPOSE[base.COMPOSE.index('-p')+1]=PROJECT

base.COMPOSE += ['-f',str(base.ROOT/'infrastructure/compose/observability/compose.observability.yaml')]
ACTIVE_FOLDER=None


def ingest_traced_source(token,value):
    # Privileged synthetic QA seed only; never reopen public replacement APIs.
    return 201,qa.script('print(json.dumps(corpus.ingest(Identity("demo-a","demo-user"),'+repr(value['documents'])+')))')


def frozen():
    result=qa.frozen()
    for path in (base.ROOT/'tests/observability/observability_fixture.py',base.ROOT/'infrastructure/compose/observability/compose.observability.yaml',
                 base.ROOT/'monitoring/collector/otel-collector.yaml',base.ROOT.parent/'docs/architecture/contracts/observability-contract.json'):
        result[str(path.relative_to(base.ROOT.parent))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def saved(name):
    code="from pathlib import Path; p=Path('/telemetry/"+name+".json'); print(p.read_text()[-1048576:] if p.exists() else '')"
    lines=base.app_python(code).splitlines()
    result=[]
    for line in lines:
        try:
            result.append(json.loads(line))
        except ValueError:
            pass
    return result


def spans():
    return [s for obj in saved('traces') for resource in obj.get('resourceSpans',[])
            for scope in resource.get('scopeSpans',[]) for s in scope.get('spans',[])]


def poll(action, timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=action()
        if value:
            return value
        time.sleep(.5)
    raise AssertionError('Bounded telemetry wait exhausted')


def ops():
    return json.loads(base.app_python("import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/internal/ops',timeout=3).read().decode())"))['alerts']


def public_metric_is_private():
    # A SPA fallback is HTML, not JSON. Never treat an arbitrary 5xx as a passed privacy check.
    try:
        with urllib.request.urlopen(base.BASE+'/internal/metrics',timeout=5) as response:
            raw=response.read(16384)
            return response.status==200 and response.headers.get('Content-Type','').startswith('text/html') and b'rag_requests' not in raw
    except urllib.error.HTTPError as error:
        return error.code==404


def unbound_ports(bindings):
    # EXPOSE alone is not host publication. Fail closed on unexpected shapes.
    return isinstance(bindings,dict) and all(value is None or value==[] for value in bindings.values())


def collector_has_no_host_bindings():
    container=base.docker('ps','-q','collector').stdout.strip()
    if not container or '\n' in container:
        return False
    for field in ('HostConfig.PortBindings','NetworkSettings.Ports'):
        result=subprocess.run(['docker','inspect',container,'--format','{{json .'+field+'}}'],
            capture_output=True,text=True,timeout=15,check=True,shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if not unbound_ports(json.loads(result.stdout)):
            return False
    return True


def run_round(seed):
    checks=[]
    def check(name,ok):
        checks.append(dict(name=name,passed=bool(ok)))
        (ACTIVE_FOLDER/f'progress-{seed}.json').write_text(json.dumps(checks,indent=2),encoding='utf8')
        if not ok:
            raise AssertionError(name)
    a=base.http('/v1/lab/session',body={'profile':'ana'})[1]['token']
    marker='telemetry'+str(seed)
    old={s['traceId'] for s in spans()}
    doc=dict(source_key=marker,title='Synthetic policy',text=marker+' orcamento 91 reais',media_type='text/plain')
    code,_=ingest_traced_source(a,{'documents':[doc]})
    check('actual_index_ready_for_traced_workflow',code==201)
    def answer():
        code,value=base.http('/v1/requests',a,{'question':marker},{'Idempotency-Key':base.key()})
        assert code==202
        return base.wait_state(value['request_id'],a,{'SUCCEEDED'})
    result=answer()
    check('real_adk_workflow_committed',result['result']['kind']=='EXTRACTIVE')
    def new_trace():
        candidates=[s for s in spans() if s['traceId'] not in old]
        for root in candidates:
            if root['name']=='rag.request':
                children=[s for s in candidates if s.get('parentSpanId')==root['spanId'] and s['traceId']==root['traceId']]
                if {s['name'] for s in children}=={'rag.retrieve','rag.compose','rag.verify'}:
                    return [root]+children
        return None
    actual=poll(new_trace)
    check('official_collector_received_correlated_root_and_three_stages',len(actual)==4)
    check('span_payload_is_allowlisted',all(set(s)<= {'traceId','spanId','parentSpanId','name','kind','startTimeUnixNano','endTimeUnixNano','status'} for s in actual))
    metric=poll(lambda: saved('metrics'))
    names={m['name'] for obj in metric for r in obj.get('resourceMetrics',[]) for scope in r.get('scopeMetrics',[]) for m in scope.get('metrics',[])}
    check('collector_scraped_actual_sql_metrics',{'rag_requests','rag_pending_requests','rag_worker_stale'}<=names)
    check('operator_has_no_stale_worker_alert',not poll(lambda: {'alerts':ops()})['alerts'])
    # Export SDK-created spans with sensitive attributes/events; export boundary must drop them.
    base.app_python("from rag_app.observability import setup; from opentelemetry import trace; setup(); tracer=trace.get_tracer('synthetic-fixture'); exec("+repr(
        "with tracer.start_as_current_span('rag.request') as s:\n s.set_attribute('prompt',"+repr(marker)+")\n s.add_event('unsafe_event',{'token':"+repr(marker)+"})\nwith tracer.start_as_current_span('unsafe.adk.prompt') as s:\n s.set_attribute('prompt',"+repr(marker)+")\ntrace.get_tracer_provider().force_flush()")+")")
    time.sleep(1)
    traces=saved('traces'); metrics=saved('metrics')
    check('provider_spans_prompt_attributes_events_not_exported',marker not in json.dumps([traces,metrics]) and all(s['name'] in {'rag.request','rag.retrieve','rag.compose','rag.verify'} for s in spans()))
    base.docker('stop','collector')
    try:
        started=time.monotonic(); offline=answer()
        check('collector_outage_cannot_block_terminal',offline['state']=='SUCCEEDED' and offline['result']['kind']=='EXTRACTIVE' and time.monotonic()-started<20)
    finally:
        base.docker('start','collector')
    poll(lambda: saved('metrics'))
    base.docker('stop','worker')
    try:
        check('real_worker_outage_generates_stale_alert',bool(poll(lambda:'WORKER_HEARTBEAT_STALE' in ops(),timeout=25)))
        events=[]
        for line in base.docker('logs','--no-log-prefix','control').stdout.splitlines():
            try:
                value=json.loads(line)
            except ValueError:
                continue
            if value.get('event')=='operational_alerts':
                events.append(value)
        check('alert_emitted_as_structured_operator_log',any('WORKER_HEARTBEAT_STALE' in e['codes'] for e in events))
        check('unchanged_alerts_not_repeated_every_poll',all(x!=y for x,y in zip(events,events[1:])))
    finally:
        base.docker('start','worker')
    check('restarted_worker_clears_alert',bool(poll(lambda:'WORKER_HEARTBEAT_STALE' not in ops())))
    check('metrics_not_exposed_through_frontend',public_metric_is_private())
    check('collector_has_no_public_ports',collector_has_no_host_bindings())
    logs=base.docker('logs','--no-log-prefix','api','worker','control','collector').stdout
    check('uploaded_marker_absent_from_application_collector_logs',marker not in logs)
    return dict(seed=seed,checks=checks,passed=True)


def main():
    global ACTIVE_FOLDER
    qa.live()
    if base.docker('ps','--status','running','-q').stdout.strip():
        raise SystemExit('Project already running: refuse ownership')
    folder=base.ROOT.parent/'eval/runs'/('observability-real-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True); ACTIVE_FOLDER=folder
    sources=frozen(); seeds=[secrets.randbits(32) for _ in range(2)]
    contract=dict(scope='actual_local_otel_collector_SQL_HTTP_ADK_outage',independent_blind=False,
                  seeds=seeds,sources_sha256=sources,oracle_frozen_before_inputs=True,
                  isolated_project=PROJECT,endpoint=base.BASE,current_roles=True,cloud_calls=0)
    (folder/'contract.json').write_text(json.dumps(contract,indent=2),encoding='utf8')
    rounds=[]; streak=0; error=None
    try:
        base.docker('up','-d','--wait','--wait-timeout','120')
        qa.wait_index(); pinned=base.service_image_ids(); contract['images']=pinned
        for seed in seeds:
            assert frozen()==sources and base.service_image_ids()==pinned,'Changed sources/images reset streak'
            result=run_round(seed); rounds.append(result); streak+=1
            (folder/f'round-{seed}.json').write_text(json.dumps(result,indent=2),encoding='utf8')
            print(json.dumps(dict(seed=seed,checks=len(result['checks']),streak=streak)),flush=True)
        assert frozen()==sources and base.service_image_ids()==pinned,'Changed sources/images reset streak'
    except Exception as exc:
        error=str(exc); streak=0
    finally:
        stopped=base.docker('stop',check=False)
        qa.live()
        receipt=dict(card_id='RAG-09',evidence_type='real_integration',contract=contract,sources_sha256=sources,
            rounds=rounds,error=error,consecutive_passes=streak,complete=streak==2 and not error,
            criteria_passed=['collector','traces','metrics','alerts','redaction'] if streak==2 else [],
            gate_b_passed=False,containers_stopped=stopped.returncode==0,volumes_preserved=True)
        (folder/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps(dict(receipt=str(folder/'receipt.json'),error=error,streak=streak)),flush=True)
    raise SystemExit(0 if streak==2 and not error else 1)


if __name__=='__main__':
    main()
