# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Inspect only MCP response shape; never log credentials or document text."""
import asyncio
import json
import logging
import re
from rag_app import github_lab as lab


def shape(result):
    blocks = []
    for part in result.content:
        row = {'type': part.type}
        if part.type == 'text':
            row['bytes'] = len(part.text.encode('utf8'))
            row['git_sha_candidates'] = re.findall(r'\b[0-9a-f]{40}\b', part.text)
            try:
                value = json.loads(part.text)
                row['json_type'] = type(value).__name__
                if isinstance(value, dict):
                    row['known_keys'] = sorted(set(value) & {
                        'name', 'path', 'sha', 'type', 'encoding', 'content',
                        'url', 'download_url', 'size', 'html_url'})
            except ValueError:
                row['json_type'] = None
        elif part.type == 'resource':
            resource = part.resource
            row['resource_type'] = type(resource).__name__
            uri = str(resource.uri)
            if len(uri) < 250 and 'Cabraiz/rag-mcp-lab/' in uri and not re.search(r'github_pat_|[?@#]', uri):
                row['synthetic_resource_uri'] = uri
            row['uri_matches_commit'] = str(resource.uri) == (
                f'repo://{lab.OWNER}/{lab.REPO}/{lab.COMMIT}/{lab.FILE}')
            row['uri_matches_ref'] = str(resource.uri) == (
                f'repo://{lab.OWNER}/{lab.REPO}/refs/heads/{lab.COMMIT}/{lab.FILE}')
            row['mime_type'] = resource.mime_type
            row['text_bytes'] = len(getattr(resource, 'text', '').encode('utf8'))
        blocks.append(row)
    print(json.dumps({'is_error': result.is_error,
                      'structured_type': type(result.structured_content).__name__,
                      'blocks': blocks}), flush=True)
    raise lab.McpBlocked('RESPONSE_SCHEMA_CHANGED')


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    lab.evidence = shape
    raise SystemExit(lab.main())
