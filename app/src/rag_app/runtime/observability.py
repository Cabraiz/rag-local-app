"""Synthetic lab telemetry. Export allowlist is the privacy boundary, never SDK defaults."""
import json
import os
import httpx
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from . import ledger
from . import resilience

NAMES = frozenset(('rag.request', 'rag.retrieve', 'rag.compose', 'rag.verify'))


def sanitized(spans):
    output=[]
    for span in spans:
        if span.name not in NAMES:
            continue
        value=dict(traceId=f'{span.context.trace_id:032x}',spanId=f'{span.context.span_id:016x}',
            name=span.name,kind=1,startTimeUnixNano=str(span.start_time),endTimeUnixNano=str(span.end_time),
            status={'code':span.status.status_code.value})
        if span.parent:
            value['parentSpanId']=f'{span.parent.span_id:016x}'
        # Intentionally NO events, links, status text, SDK resources or arbitrary attributes.
        output.append(value)
    return {'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':'rag-local'}}]},
        'scopeSpans':[{'scope':{'name':'rag_app.safe_export'},'spans':output}]}]}


class SafeLocalExporter(SpanExporter):
    def export(self, spans):
        payload=sanitized(spans)
        if not payload['resourceSpans'][0]['scopeSpans'][0]['spans']:
            return SpanExportResult.SUCCESS
        try:
            with httpx.Client(timeout=0.5,trust_env=False,follow_redirects=False) as client:
                response=client.post('http://collector:4318/v1/traces',json=payload)
                return SpanExportResult.SUCCESS if response.status_code==200 else SpanExportResult.FAILURE
        except Exception:
            return SpanExportResult.FAILURE

    def shutdown(self):
        pass


def setup():
    if os.environ.get('RAG_OBSERVABILITY')!='local_lab':
        return
    provider=TracerProvider(resource=Resource({'service.name':'rag-local'}))
    provider.add_span_processor(BatchSpanProcessor(SafeLocalExporter(),max_queue_size=256,
        max_export_batch_size=32,schedule_delay_millis=200,export_timeout_millis=1000))
    trace.set_tracer_provider(provider)


def stale_after(role):
    if resilience.enabled() and role=='worker':
        return 60 if os.environ.get('RAG_GEMINI_RESPONSES')=='free_lab' else 30
    return 15

def snapshot():
    # Aggregate only. No user labels, questions, URLs, secrets or free-form errors.
    with ledger.connect() as db:
        rows=db.execute('SELECT state,count(*) AS n FROM requests GROUP BY state').fetchall()
        counts={row['state']:row['n'] for row in rows}
        workers=db.execute("SELECT role, EXTRACT(EPOCH FROM clock_timestamp()-at)::float AS age FROM worker_health").fetchall()
        ages={r['role']:r['age'] for r in workers}
        expired=db.execute("SELECT count(*) AS n FROM requests WHERE state IN ('ACCEPTED','RUNNING','RETRY_WAIT') AND deadline<=clock_timestamp()").fetchone()['n']
        if resilience.enabled():
            budget=db.execute('SELECT sum(pending) AS pending,sum(pending_bytes) AS bytes FROM admission_shards').fetchone()
            pending=budget['pending']
            queue_age=db.execute("SELECT COALESCE(max(EXTRACT(EPOCH FROM clock_timestamp()-created_at)),0)::float AS age FROM requests WHERE state IN ('ACCEPTED','RETRY_WAIT')").fetchone()['age']
            controls=db.execute('SELECT name,failures,open_until>clock_timestamp() AS opened FROM dependency_control').fetchall()
            events=db.execute('SELECT name,value FROM resilience_events').fetchall()
            durations=db.execute("SELECT EXTRACT(EPOCH FROM terminal_at-created_at)::float AS duration FROM requests WHERE terminal_at IS NOT NULL ORDER BY terminal_at DESC LIMIT 10000").fetchall()
        else:
            pending=db.execute('SELECT pending FROM admission WHERE id=1').fetchone()['pending']
    alerts=[]
    roles=('worker','control','delivery','relay') if resilience.enabled() else ('worker','control')
    for role in roles:
        if role not in ages or ages[role]>stale_after(role):
            alerts.append(role.upper()+'_HEARTBEAT_STALE')
    if expired:
        alerts.append('EXPIRED_PENDING_RECOVERY')
    lines=[f'rag_requests{{state="{state}"}} {counts.get(state,0)}' for state in
        ('ACCEPTED','RUNNING','RETRY_WAIT','SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED')]
    lines += [f'rag_pending_requests {pending}',f'rag_expired_pending_requests {expired}']
    lines += [f'rag_worker_stale{{role="{role}"}} {int(role not in ages or ages[role]>stale_after(role))}' for role in roles]
    if resilience.enabled():
        lines += [f'rag_pending_bytes {budget["bytes"]}',f'rag_queue_oldest_seconds {queue_age}',
                  f'rag_requests_total {sum(counts.values())}']
        for row in controls:
            if row['name'] in resilience.LIMITS:
                lines += [f'rag_dependency_open{{dependency="{row["name"]}"}} {int(bool(row["opened"]))}',
                          f'rag_dependency_failures{{dependency="{row["name"]}"}} {row["failures"]}']
        allowed=('cache_hit','cache_miss','cache_unavailable_or_invalid','broker_published','broker_duplicate','broker_invalid','broker_ack','broker_retry_durable_sql')
        for name in allowed:
            lines.append(f'rag_resilience_events_total{{event="{name}"}} {next((r["value"] for r in events if r["name"]==name),0)}')
        if queue_age>60: alerts.append('QUEUE_WAIT_HIGH')
        if any(row['opened'] for row in controls): alerts.append('DEPENDENCY_CIRCUIT_OPEN')
        # A bounded recent-completion summary, not an unbounded DB/label scan.
        lines += [f'rag_recent_completion_seconds_sum {sum(max(0,r["duration"]) for r in durations)}',
                  f'rag_recent_completion_seconds_count {len(durations)}']
    return '\n'.join(lines)+'\n',alerts


def alert_transition(previous):
    if os.environ.get('RAG_OBSERVABILITY')!='local_lab':
        return previous
    _,alerts=snapshot()
    value=tuple(alerts)
    if value!=previous:
        print(json.dumps({'event':'operational_alerts','codes':alerts}),flush=True)
    return value
