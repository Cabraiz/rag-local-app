"""Durable minimal SQL inbox. At-least-once dispatch, no broker/external-effects claims."""
import os
from . import ledger
from .domain import RequestError


def require_profile():
    if os.environ.get('RAG_DELIVERY')!='local_lab':
        raise RequestError('DELIVERY_PROFILE_DISABLED',409)


def dispatch(before_ack=None):
    require_profile()
    with ledger.connect() as db:
        rows=db.execute('SELECT id FROM outbox WHERE delivered_at IS NULL ORDER BY id LIMIT 128 FOR UPDATE SKIP LOCKED').fetchall()
        ids=[row['id'] for row in rows]
        for outbox_id in ids:
            db.execute('INSERT INTO notification_inbox(outbox_id) VALUES (%s) ON CONFLICT DO NOTHING',(outbox_id,))
    # Test hook injects a crash AFTER durable inbox commit, not a fake delivery.
    if before_ack:
        before_ack(ids)
    with ledger.connect() as db:
        db.execute('UPDATE outbox SET delivered_at=clock_timestamp() WHERE id=ANY(%s::bigint[]) AND delivered_at IS NULL',(ids,))
    return len(ids)


def retain():
    require_profile()
    with ledger.connect() as db:
        rows=db.execute("SELECT id FROM requests WHERE retention_managed AND NOT content_expired AND state IN ('SUCCEEDED','FAILED_FINAL','EXPIRED','CANCELLED') AND terminal_at<clock_timestamp()-interval '30 days' ORDER BY terminal_at LIMIT 32 FOR UPDATE SKIP LOCKED").fetchall()
        ids=[row['id'] for row in rows]
        # Receipts, keys, hashes, states, audit and outbox are deliberately retained.
        db.execute("UPDATE requests SET question='',result=NULL,content_expired=true WHERE id=ANY(%s::uuid[])",(ids,))
    return len(ids)


def read(who, after=0):
    require_profile()
    with ledger.connect() as db:
        rows=db.execute("SELECT n.outbox_id,o.request_id,o.kind,n.delivered_at FROM notification_inbox n JOIN outbox o ON o.id=n.outbox_id JOIN requests r ON r.id=o.request_id WHERE r.tenant=%s AND r.actor=%s AND n.outbox_id>%s ORDER BY n.outbox_id LIMIT 50",(who.tenant,who.actor,after)).fetchall()
    return dict(events=rows,next_after=rows[-1]['outbox_id'] if rows else after)
