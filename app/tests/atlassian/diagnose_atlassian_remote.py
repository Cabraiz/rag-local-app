# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Opt-in synthetic provider diagnostic. No raw remote content/headers/errors in output."""
import asyncio
import json
import logging
from unittest.mock import patch
from rag_app import atlassian_lab as lab

original_evidence = lab.evidence
original_catalog = lab.catalog_contract


def inspect_catalog(catalog):
    approved = [tool for tool in catalog.tools if tool.name == lab.TOOL]
    print(json.dumps({'stage': 'catalog', 'approved_tool_count': len(approved),
        'approved_schema_properties': sorted(approved[0].input_schema.get('properties', {})) if approved else []}), flush=True)
    original_catalog(catalog)


def inspect_evidence(result, requested):
    if result.is_error:
        text = ' '.join(getattr(part, 'text', '') for part in result.content).lower()[:lab.MAX_BYTES]
        words = ['token', 'auth', 'scope', 'permission', 'denied', 'allowed', 'expired', 'cloud',
                 'jira', 'issue', 'project', 'field', 'format', 'parse', 'argument', 'invalid',
                 'organization', 'premium', 'license', 'internal', 'unknown', '400', '401', '403', '404', '429']
        print(json.dumps({'stage': 'read', 'remote_error': True,
            'content_blocks': len(result.content), 'has_structured_content': result.structured_content is not None,
            'public_keyword_tags': [word for word in words if word in text]}), flush=True)
    return original_evidence(result, requested)


def main():
    logging.disable(logging.CRITICAL)
    try:
        path = lab.configuration()
        token = path.read_text(encoding='utf8').strip()
        with patch.object(lab, 'evidence', inspect_evidence), patch.object(lab, 'catalog_contract', inspect_catalog):
            result = asyncio.run(asyncio.wait_for(lab.probe(token), timeout=45))
        token = ''
        print(json.dumps({'passed': result['passed']}), flush=True)
        return 0 if result['passed'] else 1
    except Exception as error:
        print(json.dumps({'passed': False, **lab.safe_error(error)}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
