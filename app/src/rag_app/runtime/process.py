import asyncio
import json
import signal
import sys
import time
import os
from .config import enforce_lab
from .bootstrap import process_services
from . import resilience

stopping = False


def stop(*_):
    global stopping
    stopping = True


def main():
    enforce_lab()
    role = sys.argv[1]
    ledger, workflow = process_services(role)
    if role == 'migrate':
        ledger.migrate()
        return
    from .observability import setup, alert_transition
    from opentelemetry import trace
    setup()
    previous_alerts=None
    consumer=None
    publisher=None
    last_sql_scan=0.0
    broker_retry_at=0.0
    broker_failures=0
    sql_backlog=False
    last_heartbeat=-5.0
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    while not stopping:
        try:
            if resilience.heartbeat_due(last_heartbeat,time.monotonic()):
                ledger.heartbeat(role)
                last_heartbeat=time.monotonic()
            if role == 'control':
                ledger.recover()
                previous_alerts=alert_transition(previous_alerts)
            elif role == 'delivery':
                from .delivery import dispatch, retain
                dispatch()
                retain()
            elif role == 'relay':
                from .broker import Publisher
                if time.monotonic()>=broker_retry_at:
                    if publisher is None: publisher=Publisher()
                    publisher.publish_one()
                    broker_failures=0
            elif role == 'worker':
                tag=None
                if resilience.enabled():
                    try:
                        from .broker import Consumer
                        if consumer is None and time.monotonic()<broker_retry_at:
                            row=None
                            if sql_backlog or time.monotonic()-last_sql_scan>=1:
                                row=ledger.claim()
                                sql_backlog=row is not None
                                last_sql_scan=time.monotonic()
                        else:
                            if consumer is None: consumer=Consumer()
                            row,tag=consumer.poll()
                            broker_failures=0
                        # Stale hints cannot throttle durable SQL work to one
                        # claim every 5s. Idle fallback scans remain bounded.
                        if row is None and ((consumer is not None and consumer.had_delivery)
                                            or time.monotonic()-last_sql_scan>=1):
                            row=ledger.claim(); tag=None; last_sql_scan=time.monotonic()
                            sql_backlog=row is not None
                    except Exception:
                        if consumer: consumer.close()
                        consumer=None
                        broker_failures+=1
                        broker_retry_at=time.monotonic()+resilience.broker_reconnect_delay(broker_failures)
                        row=ledger.claim()  # SQL fallback preserves accepted work.
                        sql_backlog=row is not None
                        last_sql_scan=time.monotonic()
                else:
                    row = ledger.claim()
                if row:
                    with trace.get_tracer('rag_app.workflow').start_as_current_span('rag.request',record_exception=False,set_status_on_exception=False) as request_span:
                        try:
                            proposal = asyncio.run(asyncio.wait_for(workflow.run(row),timeout=35 if os.environ.get('RAG_GEMINI_RESPONSES')=='free_lab' else 10))
                            committed = ledger.finish(row,proposal)
                            request_span.set_status(trace.Status(trace.StatusCode.OK if committed else trace.StatusCode.ERROR))
                        except Exception as error:
                            request_span.set_status(trace.Status(trace.StatusCode.ERROR))
                            if resilience.enabled():
                                committed=ledger.fail(row,error)
                                if tag is not None:
                                    consumer.outcome(tag,row['id'])
                                continue
                            raise
                    if tag is not None:
                        consumer.outcome(tag,row['id'])
                    print(json.dumps(dict(role=role,request_id=str(row['id']),terminal_committed=committed)),flush=True)
            else: raise ValueError('UNKNOWN_ROLE')
        except Exception as error:
            if role=='relay' and publisher:
                publisher.close(); publisher=None
            if role=='relay':
                broker_failures+=1
                broker_retry_at=time.monotonic()+resilience.broker_reconnect_delay(broker_failures)
            # Error class only: never dump question, credentials or SDK candidate.
            print(json.dumps(dict(role=role,error_type=type(error).__name__)),flush=True)
        time.sleep(0.05 if resilience.enabled() and role in ('worker','relay') else 0.5)
    if consumer: consumer.close()
    if publisher: publisher.close()


if __name__ == '__main__': main()
