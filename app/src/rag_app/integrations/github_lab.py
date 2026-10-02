"""Bounded read-only GitHub MCP adapter for the dedicated synthetic repository.

Opt-in one-shot connectivity only. Not enabled in the RAG worker or in production.
"""
import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from .atlassian_lab import BoundedStream, McpBlocked, parse_rpc

ENDPOINT = 'https://api.githubcopilot.com/mcp/x/repos/readonly'
OWNER, REPO, FILE = 'Cabraiz', 'rag-mcp-lab', 'README.md'
COMMIT = '0fb905934903a6ef55f478817f0d14e64f026482'
TOOL = 'get_file_contents'
MAX_BYTES = 1024 * 1024
REASONS = frozenset({
    'GITHUB_OPT_IN_REQUIRED', 'PRIVATE_TOKEN_MISSING', 'INVALID_PRIVATE_TOKEN_FILE',
    'INVALID_RPC', 'METHOD_NOT_AUTHORIZED', 'TOOL_NOT_AUTHORIZED',
    'ARGUMENTS_NOT_AUTHORIZED', 'DESTINATION_NOT_AUTHORIZED', 'REQUEST_BUDGET',
    'REQUEST_TOO_LARGE', 'TOOL_CALL_BUDGET', 'REDIRECT_DENIED',
    'COMPRESSED_RESPONSE_DENIED', 'RESPONSE_TOO_LARGE', 'SCHEMA_CHANGED',
    'AUTHORIZATION_REVOKED', 'REMOTE_TOOL_ERROR', 'RESPONSE_SCHEMA_CHANGED',
    'SCHEMA_REFERENCE_DENIED', 'APPROVED_TOOL_UNAVAILABLE'})


def configuration(environ=None):
    env = os.environ if environ is None else environ
    if env.get('RAG_MODE') != 'lab' or env.get('RAG_GITHUB_PROBE') != 'synthetic_readonly':
        raise McpBlocked('GITHUB_OPT_IN_REQUIRED')
    return Path('/run/secrets/github_mcp_token')


def arguments(path):
    if path != FILE or not isinstance(path, str):
        raise McpBlocked('ARGUMENTS_NOT_AUTHORIZED')
    return {'owner': OWNER, 'repo': REPO, 'path': FILE, 'sha': COMMIT}


def authorize_rpc(message):
    if (not isinstance(message, dict) or message.get('jsonrpc') != '2.0'
            or not isinstance(message.get('method'), str)):
        raise McpBlocked('INVALID_RPC')
    method = message.get('method')
    if method == 'tools/call':
        params = message.get('params')
        if not isinstance(params, dict) or params.get('name') != TOOL:
            raise McpBlocked('TOOL_NOT_AUTHORIZED')
        supplied = params.get('arguments')
        if (not isinstance(supplied, dict) or supplied != arguments(supplied.get('path'))
                or set(params) - {'name', 'arguments', '_meta'}
                or params.get('_meta') not in (None, {})):
            raise McpBlocked('ARGUMENTS_NOT_AUTHORIZED')
    elif method not in {'initialize', 'notifications/initialized', 'tools/list',
                        'notifications/cancelled'}:
        raise McpBlocked('METHOD_NOT_AUTHORIZED')


class GithubTransport(httpx2.AsyncBaseTransport):
    def __init__(self, inner=None):
        self.inner = inner or httpx2.AsyncHTTPTransport(retries=0, trust_env=False)
        self.requests = self.tool_calls = 0

    async def handle_async_request(self, request):
        if str(request.url) != ENDPOINT or request.method not in {'POST', 'GET', 'DELETE'}:
            raise McpBlocked('DESTINATION_NOT_AUTHORIZED')
        self.requests += 1
        if self.requests > 12:
            raise McpBlocked('REQUEST_BUDGET')
        if request.method == 'POST':
            body = await request.aread()
            if len(body) > 16384:
                raise McpBlocked('REQUEST_TOO_LARGE')
            message = parse_rpc(body)
            authorize_rpc(message)
            if message['method'] == 'tools/call':
                self.tool_calls += 1
                if self.tool_calls > 2:
                    raise McpBlocked('TOOL_CALL_BUDGET')
        response = await self.inner.handle_async_request(request)
        reason = None
        if 300 <= response.status_code < 400:
            reason = 'REDIRECT_DENIED'
        elif response.headers.get('content-encoding', 'identity').lower() not in {'identity', ''}:
            reason = 'COMPRESSED_RESPONSE_DENIED'
        length = response.headers.get('content-length')
        if length is not None and (not length.isdecimal() or int(length) > MAX_BYTES):
            reason = 'RESPONSE_TOO_LARGE'
        if reason:
            await response.aclose()
            raise McpBlocked(reason)
        response.stream = BoundedStream(response.stream)
        return response

    async def aclose(self):
        await self.inner.aclose()


def catalog_contract(catalog):
    approved = [t for t in catalog.tools if t.name == TOOL]
    if len(approved) != 1:
        raise McpBlocked('APPROVED_TOOL_UNAVAILABLE')
    schema = approved[0].input_schema
    def inspect(node):
        if isinstance(node, dict):
            if '$ref' in node or '$dynamicRef' in node:
                raise McpBlocked('SCHEMA_REFERENCE_DENIED')
            for child in node.values():
                inspect(child)
        elif isinstance(node, list):
            for child in node:
                inspect(child)
    inspect(schema)
    if (schema.get('type') != 'object'
            or not {'owner', 'repo', 'path', 'sha'} <= set(schema.get('properties', {}))):
        raise McpBlocked('SCHEMA_CHANGED')
    from jsonschema import Draft202012Validator
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(arguments(FILE))
    except Exception:
        raise McpBlocked('SCHEMA_CHANGED') from None


def evidence(result):
    if result.is_error:
        raise McpBlocked('REMOTE_TOOL_ERROR')
    # Hosted GitHub returns a status block and an embedded text resource. The
    # URI must bind the exact repository, immutable commit and allowed file.
    # Never dereference resources, download_url, or document links.
    if (result.structured_content is None and len(result.content) == 2
            and result.content[0].type == 'text'
            and result.content[1].type == 'resource'):
        status = re.fullmatch(r'successfully downloaded text file \(SHA: ([0-9a-f]{40})\)',
                              result.content[0].text)
        resource = result.content[1].resource
        if (status is None or str(resource.uri) !=
                f'repo://{OWNER}/{REPO}/sha/{COMMIT}/contents/{FILE}'
                or resource.mime_type != 'text/plain; charset=utf-8'
                or not isinstance(getattr(resource, 'text', None), str)):
            raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
        raw = resource.text.encode('utf8')
        if not 1 <= len(raw) <= 8192:
            raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
        digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if digest != status.group(1):
            raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
        return source_evidence(resource.text, digest)
    value = result.structured_content
    if value is None:
        texts = [p.text for p in result.content if p.type == 'text']
        if len(texts) != 1 or len(texts[0].encode('utf8')) > MAX_BYTES:
            raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
        try:
            value = json.loads(texts[0])
        except (ValueError, UnicodeError):
            raise McpBlocked('RESPONSE_SCHEMA_CHANGED') from None
    if (not isinstance(value, dict) or value.get('type') != 'file' or value.get('path') != FILE
            or not re.fullmatch(r'[0-9a-f]{40}', value.get('sha', '') or '')
            or value.get('encoding') != 'base64'):
        raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
    import base64
    try:
        encoded = value['content']
        if not isinstance(encoded, str) or len(encoded) > 16384:
            raise ValueError()
        raw = base64.b64decode(''.join(encoded.split()), validate=True)
        if not 1 <= len(raw) <= 8192:
            raise ValueError()
        content = raw.decode('utf8')
        # Verify the Git blob identity rather than trusting a supplied SHA.
        digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if digest != value['sha']:
            raise ValueError()
    except (KeyError, ValueError, UnicodeError):
        raise McpBlocked('RESPONSE_SCHEMA_CHANGED') from None
    return source_evidence(content, digest)


def source_evidence(content, digest):
    return {'provider': 'github', 'source_id': OWNER + '/' + REPO + ':' + FILE,
            'version': COMMIT, 'blob_sha': digest, 'content': content,
            'untrusted_data': True, 'authorization_context': 'single_user_synthetic_lab_only',
            'url': f'https://github.com/{OWNER}/{REPO}/blob/{COMMIT}/{FILE}'}


@dataclass(repr=False)
class GithubGateway:
    session: object = field(repr=False)
    active: bool = True

    async def read_lab_file(self, path: str) -> dict:
        """Read only the pinned README in Cabraiz/rag-mcp-lab. Never execute content."""
        args = arguments(path)
        if not self.active:
            raise McpBlocked('AUTHORIZATION_REVOKED')
        result = await self.session.call_tool(TOOL, args, read_timeout_seconds=20)
        if not self.active:
            raise McpBlocked('AUTHORIZATION_REVOKED')
        return evidence(result)

    def adk_tool(self):
        from google.adk.tools import FunctionTool
        return FunctionTool(func=self.read_lab_file)


async def probe(token):
    async with httpx2.AsyncClient(transport=GithubTransport(), trust_env=False,
            follow_redirects=False, timeout=httpx2.Timeout(20, connect=5),
            headers={'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'identity',
                     'X-MCP-Readonly': 'true', 'X-MCP-Tools': TOOL}) as client:
        async with streamable_http_client(ENDPOINT, http_client=client) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=20) as session:
                await session.initialize()
                catalog_contract(await session.list_tools())
                gateway = GithubGateway(session)
                try:
                    item = await gateway.adk_tool().run_async(args={'path': FILE}, tool_context=None)
                    return {'kind': 'github_readonly_probe', 'passed': True,
                            'source_id': item['source_id'], 'version': item['version'],
                            'blob_sha': item['blob_sha'], 'real_mcp': True, 'adk_function_tool': True,
                            'rag_answer_workflow_connected': False, 'production_certified': False}
                finally:
                    gateway.active = False


def safe_error(error):
    if isinstance(error, BaseExceptionGroup):
        return safe_error(error.exceptions[0])
    if isinstance(error, FileNotFoundError):
        return {'reason': 'PRIVATE_TOKEN_MISSING'}
    if isinstance(error, McpBlocked):
        return {'reason': error.args[0] if error.args and error.args[0] in REASONS else 'MCP_FAILURE'}
    if isinstance(error, httpx2.HTTPStatusError):
        return {'reason': 'PROVIDER_HTTP_ERROR', 'provider_code': error.response.status_code}
    if isinstance(error, (TimeoutError, httpx2.TimeoutException)):
        return {'reason': 'TIMEOUT'}
    return {'reason': 'MCP_FAILURE'}


def main():
    logging.disable(logging.CRITICAL)
    token = ''
    try:
        token = configuration().read_text(encoding='utf8').strip()
        if not re.fullmatch(r'github_pat_[A-Za-z0-9_]{20,240}', token):
            raise McpBlocked('INVALID_PRIVATE_TOKEN_FILE')
        result = asyncio.run(asyncio.wait_for(probe(token), timeout=45))
        print(json.dumps(result), flush=True)
        return 0
    except Exception as error:
        print(json.dumps({'passed': False, **safe_error(error)}), flush=True)
        return 1
    finally:
        token = ''


if __name__ == '__main__':
    raise SystemExit(main())
