"""Confirmed ID-only work distribution; SQL recovery is never removed."""
import json
from uuid import UUID
from . import ledger, cache
from .config import secret
from .domain import TERMINALS

QUEUE='rag.requests.v1'

def connection():
    import pika
    return pika.BlockingConnection(pika.ConnectionParameters(
        host='rabbitmq',virtual_host='rag',credentials=pika.PlainCredentials('rag',secret('rabbitmq_password')),
        heartbeat=60,connection_attempts=1,socket_timeout=2,stack_timeout=4,
        blocked_connection_timeout=2))

def channel(conn):
    ch=conn.channel()
    ch.exchange_declare(exchange='rag.dead',exchange_type='direct',durable=True)
    ch.queue_declare(queue='rag.dead.v1',durable=True,arguments={'x-queue-type':'quorum','x-max-length':20000,'x-overflow':'reject-publish'})
    ch.queue_bind(queue='rag.dead.v1',exchange='rag.dead',routing_key='rag.dead.v1')
    ch.queue_declare(queue=QUEUE,durable=True,arguments={'x-queue-type':'quorum','x-max-length':20000,'x-overflow':'reject-publish','x-delivery-limit':5,'x-dead-letter-exchange':'rag.dead','x-dead-letter-routing-key':'rag.dead.v1','x-dead-letter-strategy':'at-least-once'})
    ch.basic_qos(prefetch_count=1)
    return ch

class Publisher:
    """Keep confirmations/channel alive; avoid reconnect/declaration per job."""
    def __init__(self):
        self.conn=connection()
        try:
            self.ch=channel(self.conn); self.ch.confirm_delivery()
        except Exception:
            self.close(); raise

    def publish_one(self):
        import pika
        self.conn.process_data_events(time_limit=0)
        with ledger.connect() as db:
            row=db.execute("SELECT r.id,r.fence FROM jobs j JOIN requests r ON r.id=j.id WHERE r.state IN ('ACCEPTED','RETRY_WAIT') AND r.next_at<=clock_timestamp() AND r.deadline>clock_timestamp() AND (j.broker_fence<r.fence OR j.broker_at<clock_timestamp()-interval '15 seconds') ORDER BY r.next_at,r.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED").fetchone()
            if not row: return False
            self.ch.basic_publish(exchange='',routing_key=QUEUE,body=str(row['id']).encode(),mandatory=True,
                properties=pika.BasicProperties(delivery_mode=2,content_type='text/plain',message_id=str(row['id'])))
            db.execute('UPDATE jobs SET broker_fence=%s,broker_at=clock_timestamp() WHERE id=%s',(row['fence'],row['id']))
        cache.event('broker_published')
        return True

    def close(self):
        try:
            if self.conn.is_open: self.conn.close()
        except Exception: pass

def publish_one():
    publisher=Publisher()
    try:
        return publisher.publish_one()
    finally:
        publisher.close()

class Consumer:
    def __init__(self):
        self.conn=connection(); self.had_delivery=False
        try:
            self.ch=channel(self.conn)
        except Exception:
            self.close(); raise

    def poll(self):
        method,_,body=self.ch.basic_get(queue=QUEUE,auto_ack=False)
        self.had_delivery=method is not None
        if method is None: return None,None
        try:
            if len(body)>36: raise ValueError('INVALID_ID')
            rid=UUID(body.decode())
        except (ValueError,UnicodeError):
            # Invalid transport data cannot create a SQL request or invoke a tool.
            self.ch.basic_reject(method.delivery_tag,requeue=False)
            cache.event('broker_invalid')
            return None,None
        row=ledger.claim(rid)
        if row is None:
            with ledger.connect() as db:
                state=db.execute('SELECT state FROM requests WHERE id=%s',(rid,)).fetchone()
            if state and state['state'] in TERMINALS:
                self.ch.basic_ack(method.delivery_tag); cache.event('broker_duplicate')
            elif state is None:
                self.ch.basic_reject(method.delivery_tag,requeue=False)
                cache.event('broker_invalid')
            else:
                # Preserve busy/not-due hints in the durable dead-letter queue.
                # No terminal ACK or hot requeue loop ahead of fresh work: the
                # SQL job, relay and fallback remain authoritative.
                self.ch.basic_nack(method.delivery_tag,requeue=False)
                cache.event('broker_retry_durable_sql')
            return None,None
        return row,method.delivery_tag

    def ack(self,tag):
        self.ch.basic_ack(tag); cache.event('broker_ack')

    def outcome(self,tag,rid):
        with ledger.connect() as db:
            state=db.execute('SELECT state FROM requests WHERE id=%s',(rid,)).fetchone()
        if state and state['state'] in TERMINALS: self.ack(tag)
        else:
            self.ch.basic_nack(tag,requeue=False)
            cache.event('broker_retry_durable_sql')

    def close(self):
        try: self.conn.close()
        except Exception: pass
