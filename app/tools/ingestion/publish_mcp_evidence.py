# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Operator-approved, short-lived MCP snapshots through the existing RAG pipeline.

Not live tool execution by the assistant. Source grants are explicit; importing
private provider content into the client corpus is never automatic.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str((_workspace_root / "app") / 'tests'))
import functional_smoke as api

SCOPES = {'jira': 'KAN', 'github': 'Cabraiz/rag-mcp-lab'}


class PublicationBlocked(RuntimeError):
    pass


def document(feed, provider, item_id, *, current=None):
    current = current or datetime.now(timezone.utc)
    if provider not in SCOPES or not isinstance(feed, dict):
        raise PublicationBlocked('PROVIDER_NOT_AUTHORIZED')
    if feed.get('verification') != 'live_remote_mcp':
        raise PublicationBlocked('REAL_MCP_ATTESTATION_REQUIRED')
    providers = feed.get('providers')
    if not isinstance(providers, list):
        raise PublicationBlocked('MCP_SNAPSHOT_SCHEMA')
    matches = [p for p in providers if isinstance(p, dict) and p.get('id') == provider]
    if len(matches) != 1:
        raise PublicationBlocked('MCP_SNAPSHOT_SCHEMA')
    value = matches[0]
    if (value.get('state') != 'connected' or value.get('stale') is not False
            or value.get('complete') is not True or value.get('read_only') is not True
            or value.get('transport') != 'remote_mcp' or value.get('scope') != SCOPES[provider]):
        raise PublicationBlocked('MCP_SOURCE_NOT_CURRENT_OR_AUTHORIZED')
    try:
        stamp = value['last_success']
        if not isinstance(stamp, str) or len(stamp) > 64:
            raise ValueError()
        fetched = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        age = (current - fetched).total_seconds()
    except (KeyError, TypeError, ValueError):
        raise PublicationBlocked('MCP_TIMESTAMP_INVALID') from None
    if fetched.tzinfo is None or not -5 <= age <= 100:
        raise PublicationBlocked('MCP_SNAPSHOT_TOO_OLD')
    pattern = r'KAN-[1-9][0-9]{0,9}' if provider == 'jira' else r'[1-9][0-9]{0,9}'
    if not isinstance(item_id, str) or not re.fullmatch(pattern, item_id):
        raise PublicationBlocked('ITEM_NOT_AUTHORIZED')
    expected_url = ('https://rag-local-lab-mateus.atlassian.net/browse/' + item_id
                    if provider == 'jira' else 'https://github.com/Cabraiz/rag-mcp-lab/pull/' + item_id)
    items = value.get('items')
    if not isinstance(items, list) or len(items) > 500:
        raise PublicationBlocked('MCP_ITEM_BUDGET')
    selected = [row for row in items if isinstance(row, dict) and row.get('id') == item_id]
    if len(selected) != 1 or selected[0].get('url') != expected_url:
        raise PublicationBlocked('ITEM_NOT_AUTHORIZED')
    row = selected[0]
    for key, maximum in (('title', 1000), ('status', 80)):
        text = row.get(key)
        if not isinstance(text, str) or not 1 <= len(text) <= maximum or any(ord(c) < 32 for c in text):
            raise PublicationBlocked('MCP_ITEM_SCHEMA')
    # Retrieved titles are untrusted quoted data, not instructions or tool names.
    # Only status metadata is attested; no body, attachments or whole repository.
    text = (f"Registro MCP {provider}: {item_id}.\nTitulo (dado da fonte): {row['title']}\n"
            f"Status registrado: {row['status']}.\nFonte: {expected_url}\n"
            f"Snapshot consultado em: {fetched.isoformat()}. "
            "Este registro e um snapshot, nao uma garantia de status em tempo real.")
    if len(text.encode()) > 8192:
        raise PublicationBlocked('MCP_DOCUMENT_BYTES')
    return {'source_key': f'mcp_{provider}_{item_id}', 'title': f'MCP {provider} {item_id}',
            'text': text, 'media_type': 'text/plain',
            'valid_until': (fetched + timedelta(seconds=180)).isoformat()}


def operator_token():
    code, result = api.http('/v1/lab/session', body={'profile': 'bruno'})
    if code != 200:
        raise PublicationBlocked('OPERATOR_SESSION_UNAVAILABLE')
    return result['token']


def current_feed(token):
    code, value = api.http('/v1/lab/integrations/feed', token)
    if code != 200:
        raise PublicationBlocked('MCP_GATEWAY_UNAVAILABLE')
    return value


def publish(provider, item_id, token):
    doc = document(current_feed(token), provider, item_id)
    code, before = api.http('/v1/lab/corpus', token)
    if code != 200:
        raise PublicationBlocked('CORPUS_UNAVAILABLE')
    preserved = {d['source_key']: d['content_hash'] for d in before['documents']
                 if d['source_key'] != doc['source_key']}
    code, promoted = api.http('/v1/lab/corpus/documents', token,
                             {'expected_generation': before['generation'], 'document': doc})
    if code != 201:
        # No silent CAS retry, dropped document, or bypass of revocation tombstones.
        raise PublicationBlocked('PUBLICATION_REJECTED')
    code, after = api.http('/v1/lab/corpus', token)
    by_key = {d['source_key']: d for d in after.get('documents', [])}
    if code != 200 or any(by_key.get(k, {}).get('content_hash') != v for k, v in preserved.items()):
        raise PublicationBlocked('ADDITIVE_PUBLICATION_INVARIANT')
    imported = by_key.get(doc['source_key'], {})
    if imported.get('content_hash') != hashlib.sha256(doc['text'].encode()).hexdigest():
        raise PublicationBlocked('MCP_CANONICAL_HASH_MISMATCH')
    return {'document': doc, 'document_id': imported['id'], 'release_id': after['release_id'],
            'content_hash': imported['content_hash'], 'preserved_sources': preserved,
            'local_publication': promoted, 'remote_writes': 0, 'automatic_provider_grant': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', choices=list(SCOPES), required=True)
    parser.add_argument('--item', required=True)
    parser.add_argument('--publish', action='store_true', help='Explicitly grant this snapshot to the Aurora client corpus.')
    args = parser.parse_args()
    token = operator_token()
    try:
        result = publish(args.provider, args.item, token) if args.publish else {
            'preview': document(current_feed(token), args.provider, args.item), 'published': False}
        print(json.dumps(result, ensure_ascii=False))
    except PublicationBlocked as error:
        print(json.dumps({'complete': False, 'code': str(error)}))
        raise SystemExit(1)
    finally:
        token = ''


if __name__ == '__main__':
    main()
