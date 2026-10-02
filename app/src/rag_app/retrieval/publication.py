"""Add/update one synthetic source without dropping the rest of the release."""
import hashlib
from . import corpus, ledger
from .domain import RequestError


def reconstruct(chunks, content_hash):
    text=''
    for ordinal, chunk in enumerate(chunks):
        if chunk['ordinal'] != ordinal:
            raise RequestError('CORPUS_TEXT_INTEGRITY_FAILED',409)
        quote=chunk['quote']; start=ordinal*520
        overlap=len(text)-start
        if not 0<=overlap<=80 or (overlap and text[start:] != quote[:overlap]):
            raise RequestError('CORPUS_TEXT_INTEGRITY_FAILED',409)
        text+=quote[overlap:]
    if hashlib.sha256(text.encode()).hexdigest() != content_hash:
        raise RequestError('CORPUS_TEXT_INTEGRITY_FAILED',409)
    return text


def publish(who, document, expected_generation, *, original=None):
    corpus.require_profile(); corpus.validate_bundle([document])
    documents=[]; originals={}
    with ledger.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        head=db.execute('SELECT * FROM corpus_heads WHERE tenant=%s AND actor=%s',
            (who.tenant,who.actor)).fetchone()
        generation=head['generation'] if head else 0
        if generation != expected_generation:
            raise RequestError('CORPUS_PROMOTION_CONFLICT',409)
        if head and head['release_id']:
            rows=db.execute('SELECT * FROM corpus_documents WHERE release_id=%s AND tenant=%s AND actor=%s ORDER BY source_key LIMIT 33',
                (head['release_id'],who.tenant,who.actor)).fetchall()
            if len(rows)>32: raise RequestError('INVALID_CORPUS_BUNDLE',422)
            for row in rows:
                if row['revoked']:
                    # Never silently resurrect or omit a revoked document.
                    raise RequestError('REVOKED_CORPUS_REQUIRES_MAINTENANCE',409)
                chunks=db.execute('SELECT ordinal,quote FROM corpus_chunks WHERE document_id=%s ORDER BY ordinal', (row['id'],)).fetchall()
                documents.append(dict(source_key=row['source_key'],title=row['title'],
                    text=reconstruct(chunks,row['content_hash']),media_type='text/plain',
                    valid_until=row['valid_until'].isoformat() if row['valid_until'] else None))
                if row['original_hash']:
                    originals[row['source_key']]={k:row[k] for k in ('original_hash','original_media_type','original_bytes')}
    documents=[d for d in documents if d['source_key'] != document['source_key']]
    documents.append(document)
    # Edited text is no longer the old uploaded original; other originals survive.
    originals.pop(document['source_key'],None)
    if original is not None:
        originals[document['source_key']]=original
    return corpus.ingest(who,documents,originals=originals,expected_generation=generation)
