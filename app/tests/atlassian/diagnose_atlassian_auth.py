# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Bounded, opt-in lab diagnostic. Never emits credentials or provider bodies."""
import asyncio
import base64
import json
import logging
from unittest.mock import patch
import httpx2
from rag_app import atlassian_lab as lab

Transport = lab.LabTransport
original_evidence = lab.evidence


class DiagnosticTransport(Transport):
    def __init__(self, token):
        super().__init__()
        self.expected = 'Basic ' + base64.b64encode(
            (lab.EMAIL + ':' + token).encode()).decode()

    async def handle_async_request(self, request):
        matches = request.headers.get('Authorization') == self.expected
        if not matches:
            raise lab.McpBlocked('INVALID_PRIVATE_TOKEN_FILE')
        response = await super().handle_async_request(request)
        print(json.dumps({'stage': 'transport', 'auth_header_matches': matches,
                          'provider_http_status': response.status_code}), flush=True)
        return response


def inspect_evidence(result, requested):
    if result.is_error:
        text = ' '.join(getattr(part, 'text', '') for part in result.content)[:lab.MAX_BYTES]
        lowered = text.lower()
        phrases = ('unauthorized', 'unauthorised', 'authentication required',
                   'invalid credentials', 'invalid api token', 'invalid token',
                   'token expired', 'headless', 'basic', 'oauth', 'disabled',
                   'not enabled', '401', 'forbidden', '403')
        print(json.dumps({'stage': 'read', 'remote_error': True,
                          'public_keyword_tags': [p for p in phrases if p in lowered]}), flush=True)
    return original_evidence(result, requested)


def main():
    logging.disable(logging.CRITICAL)
    token = ''
    try:
        token = lab.configuration().read_text(encoding='utf8').strip()
        # Vendor-documented read-only authentication check; metadata is NOT issue proof.
        async def check_authentication():
            async with httpx2.AsyncClient(auth=httpx2.BasicAuth(lab.EMAIL, token),
                    timeout=5, trust_env=False, follow_redirects=False) as client:
                response = await client.head(lab.ENDPOINT)
                print(json.dumps({'stage': 'authentication_head',
                                  'provider_http_status': response.status_code}), flush=True)
        asyncio.run(asyncio.wait_for(check_authentication(), timeout=6))
        with patch.object(lab, 'LabTransport', lambda: DiagnosticTransport(token)), \
             patch.object(lab, 'evidence', inspect_evidence):
            result = asyncio.run(asyncio.wait_for(lab.probe(token), timeout=45))
        print(json.dumps({'passed': result['passed']}), flush=True)
        return 0 if result['passed'] else 1
    except Exception as error:
        print(json.dumps({'passed': False, **lab.safe_error(error)}), flush=True)
        return 1
    finally:
        token = ''


if __name__ == '__main__':
    raise SystemExit(main())
