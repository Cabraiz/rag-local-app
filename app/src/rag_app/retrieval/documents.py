"""Opt-in original-file CAS with canonical SQL authorization, not a public file server."""
import base64
import binascii
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from . import corpus, ledger
from .domain import RequestError

ROOT = Path('/objects')
RAW_LIMIT = 8192
STORE_LIMIT = 20971520


def require_profile():
    corpus.require_profile()
    if os.environ.get('RAG_DOCUMENTS') != 'synthetic_lab':
        raise RequestError('DOCUMENT_PROFILE_DISABLED', 409)


def parse(raw, media_type):
    if not raw or len(raw) > RAW_LIMIT:
        raise RequestError('DOCUMENT_BYTES_LIMIT', 413)
    try:
        result = subprocess.run([sys.executable, '-m', 'rag_app.document_parser', media_type],
            input=raw, capture_output=True, timeout=4, check=False, shell=False)
        if result.returncode or len(result.stdout) > 65536:
            raise ValueError()
        return json.loads(result.stdout)['text']
    except (ValueError, KeyError, subprocess.SubprocessError, OSError):
        raise RequestError('DOCUMENT_PARSE_REJECTED', 422) from None


def store(raw):
    digest = hashlib.sha256(raw).hexdigest()
    # Private fixed mount, no caller-supplied pathname. Serialize quota and publication.
    try:
        with (ROOT / '.lock').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            target = ROOT / digest
            if target.exists():
                if target.is_symlink() or target.read_bytes() != raw:
                    raise RequestError('DOCUMENT_INTEGRITY_FAILURE', 503)
                return digest
            used = sum(p.stat().st_size for p in ROOT.iterdir() if p.is_file())
            if used + len(raw) > STORE_LIMIT:
                raise RequestError('OBJECT_STORE_FULL', 507)
            fd, name = tempfile.mkstemp(prefix='.pending-', dir=ROOT)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, target)
                directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return digest
    except OSError:
        raise RequestError('OBJECT_STORE_UNAVAILABLE', 503) from None


def upload(who, value):
    require_profile()
    try:
        raw = base64.b64decode(value['data_base64'], validate=True)
    except (ValueError, binascii.Error):
        raise RequestError('INVALID_DOCUMENT_ENCODING', 422) from None
    text = parse(raw, value['media_type'])
    doc = {k: value[k] for k in ('source_key', 'title', 'valid_until')}
    doc.update(text=text, media_type='text/plain')
    corpus.validate_bundle([doc])
    digest = store(raw)
    original = {doc['source_key']: dict(original_hash=digest,
        original_media_type=value['media_type'], original_bytes=len(raw))}
    return corpus.ingest(who, [doc], originals=original)


def upload_additive(who, value, expected_generation):
    """Publish parsed bytes without replacing other sources or trusting file metadata."""
    from .publication import publish
    require_profile()
    try:
        raw = base64.b64decode(value['data_base64'], validate=True)
    except (ValueError, binascii.Error):
        raise RequestError('INVALID_DOCUMENT_ENCODING', 422) from None
    text = parse(raw, value['media_type'])
    doc = {k: value[k] for k in ('source_key', 'title', 'valid_until')}
    doc.update(text=text, media_type='text/plain')
    corpus.validate_bundle([doc])
    digest = store(raw)
    return publish(who, doc, expected_generation, original=dict(
        original_hash=digest, original_media_type=value['media_type'],
        original_bytes=len(raw)))


def download(who, document_id):
    require_profile()
    # Hold the same row lock used by citation validation until bytes pass integrity checks.
    with ledger.connect() as db:
        row = db.execute("SELECT d.* FROM corpus_documents d JOIN corpus_releases r ON r.id=d.release_id "
            "WHERE d.id=%s AND d.tenant=%s AND d.actor=%s AND r.state='READY' AND NOT d.revoked "
            "AND (d.valid_until IS NULL OR d.valid_until>clock_timestamp()) FOR SHARE OF d",
            (document_id, who.tenant, who.actor)).fetchone()
        if not row or not row['original_hash']:
            raise RequestError('NOT_FOUND', 404)
        digest = row['original_hash']
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise RequestError('DOCUMENT_INTEGRITY_FAILURE', 503)
        try:
            path = ROOT / digest
            if path.is_symlink() or path.stat().st_size > RAW_LIMIT:
                raise RequestError('DOCUMENT_INTEGRITY_FAILURE', 503)
            raw = path.read_bytes()
        except OSError:
            raise RequestError('DOCUMENT_INTEGRITY_FAILURE', 503) from None
        if len(raw) != row['original_bytes'] or hashlib.sha256(raw).hexdigest() != digest:
            raise RequestError('DOCUMENT_INTEGRITY_FAILURE', 503)
        if row['original_media_type'] not in ('text/plain', 'text/markdown', 'application/pdf'):
            raise RequestError('DOCUMENT_INTEGRITY_FAILURE', 503)
        return raw, row['original_media_type']
