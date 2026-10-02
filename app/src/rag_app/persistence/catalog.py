"""Read-only, bounded, canonical corpus view for the synthetic laboratory."""
from . import ledger
from .domain import Identity


def read(who: Identity):
    with ledger.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        head = db.execute('''SELECT r.id,r.embedding_version,h.generation FROM corpus_heads h
            JOIN corpus_releases r ON r.id=h.release_id AND r.tenant=h.tenant
            AND r.actor=h.actor WHERE h.tenant=%s AND h.actor=%s AND r.state='READY'
            ''', (who.tenant, who.actor)).fetchone()
        if not head:
            return dict(release_id=None, embedding_version=None, generation=0, documents=[])
        rows = db.execute('''SELECT d.id,d.title,d.source_key,d.content_hash,
                json_agg(json_build_object('id',c.id,'quote',c.quote)
                    ORDER BY c.ordinal) AS chunks
            FROM corpus_documents d JOIN corpus_chunks c ON c.document_id=d.id
            WHERE d.release_id=%s AND d.tenant=%s AND d.actor=%s AND NOT d.revoked
                AND (d.valid_until IS NULL OR d.valid_until>transaction_timestamp())
                AND NOT EXISTS (SELECT 1 FROM corpus_revocations v
                    WHERE v.tenant=d.tenant AND v.actor=d.actor AND v.source_key=d.source_key)
            GROUP BY d.id ORDER BY d.title,d.id LIMIT 32''',
            (head['id'], who.tenant, who.actor)).fetchall()
    return dict(release_id=str(head['id']), embedding_version=head['embedding_version'],generation=head['generation'],
        documents=[dict(id=str(row['id']),title=row['title'],source_key=row['source_key'],
            content_hash=row['content_hash'],chunks=row['chunks']) for row in rows])
