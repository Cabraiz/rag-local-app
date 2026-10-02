"""Isolated read-only MCP/ADK lab adapter. Not connected to the RAG worker."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ENDPOINT = 'https://mcp.atlassian.com/v2/mcp'
CLOUD_ID = '15445c1f-6463-4ece-bdb1-eecd7c4d5968'
PROJECT = 'KAN'
EMAIL = 'mateusccabr@gmail.com'
TOOL = 'getJiraIssue'
ISSUES = frozenset(('KAN-1', 'KAN-2'))
MAX_BYTES = 1024 * 1024
TOKEN_EXPIRY = '2026-10-07'


class McpBlocked(RuntimeError):
    """Only fixed, non-sensitive reason codes may cross the boundary."""


def configuration(environ=None, today=None):
    env = os.environ if environ is None else environ
    if env.get('RAG_MODE') != 'lab' or env.get('RAG_ATLASSIAN_PROBE') != 'synthetic_readonly':
        raise McpBlocked('READONLY_LAB_OPT_IN_REQUIRED')
    today = today or datetime.now(timezone.utc).date().isoformat()
    # Conservatively expire at the start of the provider's expiry date.
    if today >= TOKEN_EXPIRY:
        raise McpBlocked('TOKEN_LOCAL_EXPIRY')
    return Path('/run/secrets/atlassian_mcp_token')


def arguments(issue_key):
    if not isinstance(issue_key, str) or issue_key not in ISSUES:
        raise McpBlocked('ISSUE_NOT_AUTHORIZED')
    return {'cloudId': CLOUD_ID, 'issueIdOrKey': issue_key, 'fields': ['summary', 'project', 'updated']}


def authorize_rpc(message):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        raise McpBlocked('INVALID_RPC')
    method = message.get('method')
    if method == 'tools/call':
        params = message.get('params', {})
        if params.get('name') != TOOL:
            raise McpBlocked('TOOL_NOT_AUTHORIZED')
        supplied = params.get('arguments', {})
        if supplied != arguments(supplied.get('issueIdOrKey')):
            raise McpBlocked('ARGUMENTS_NOT_AUTHORIZED')
        # SDK 2.2 serializes an empty metadata object. Non-empty metadata remains denied.
        if set(params) - {'name', 'arguments', '_meta'} or params.get('_meta') not in (None, {}):
            raise McpBlocked('RPC_META_NOT_AUTHORIZED')
    elif method not in {'initialize', 'notifications/initialized', 'tools/list', 'notifications/cancelled'}:
        raise McpBlocked('METHOD_NOT_AUTHORIZED')


class BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        used = 0
        async for chunk in self.stream:
            used += len(chunk)
            if used > MAX_BYTES:
                raise McpBlocked('RESPONSE_TOO_LARGE')
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class LabTransport(httpx2.AsyncBaseTransport):
    """Enforce destination and RPC even if the SDK changes redirect behaviour."""
    def __init__(self, inner=None):
        self.inner = inner or httpx2.AsyncHTTPTransport(retries=0, trust_env=False)
        self.requests = 0
        self.tool_calls = 0

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
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                raise McpBlocked('INVALID_RPC') from None
            authorize_rpc(payload)
            if payload['method'] == 'tools/call':
                self.tool_calls += 1
                if self.tool_calls > 2:
                    raise McpBlocked('TOOL_CALL_BUDGET')
        response = await self.inner.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise McpBlocked('REDIRECT_DENIED')
        if response.headers.get('content-encoding', 'identity').lower() not in {'identity', ''}:
            await response.aclose()
            raise McpBlocked('COMPRESSED_RESPONSE_DENIED')
        length = response.headers.get('content-length')
        if length is not None and (not length.isdecimal() or int(length) > MAX_BYTES):
            await response.aclose()
            raise McpBlocked('RESPONSE_TOO_LARGE')
        response.stream = BoundedStream(response.stream)
        return response

    async def aclose(self):
        await self.inner.aclose()


def catalog_contract(catalog):
    found = [tool for tool in catalog.tools if tool.name == TOOL]
    if len(found) != 1:
        raise McpBlocked('APPROVED_TOOL_UNAVAILABLE')
    schema = found[0].input_schema
    props = schema.get('properties', {})
    if schema.get('type') != 'object' or not {'cloudId', 'issueIdOrKey', 'fields'} <= set(props):
        raise McpBlocked('SCHEMA_CHANGED')
    if set(schema.get('required', [])) - {'cloudId', 'issueIdOrKey', 'fields'}:
        raise McpBlocked('SCHEMA_CHANGED')
    # Never let a provider schema cause external reference resolution.
    def local_only(node):
        if isinstance(node, dict):
            if '$ref' in node or '$dynamicRef' in node:
                raise McpBlocked('SCHEMA_REFERENCE_DENIED')
            for child in node.values(): local_only(child)
        elif isinstance(node, list):
            for child in node: local_only(child)
    local_only(schema)
    from jsonschema import Draft202012Validator
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(arguments('KAN-1'))
    except Exception:
        raise McpBlocked('SCHEMA_CHANGED') from None


def evidence(result, requested):
    if result.is_error:
        # Inspect privately; output only fixed enums, not provider content.
        text = ' '.join(getattr(part, 'text', '') for part in result.content).lower()[:MAX_BYTES]
        reason = ('REMOTE_TOKEN_DISABLED' if 'api token' in text and ('disabled' in text or 'not enabled' in text)
                  else 'REMOTE_SCOPE_DENIED' if 'scope' in text and ('missing' in text or 'required' in text)
                  else 'REMOTE_ACCESS_DENIED' if 'permission' in text or 'forbidden' in text or 'unauthorized' in text
                  else 'REMOTE_NOT_FOUND' if 'not found' in text or '404' in text
                  else 'REMOTE_RATE_LIMIT' if '429' in text or 'rate limit' in text
                  else 'REMOTE_INVALID_CLOUD' if 'cloudid' in text and ('invalid' in text or 'resolve' in text)
                  else 'REMOTE_TOOL_ERROR')
        raise McpBlocked(reason)
    value = result.structured_content
    if value is None:
        texts = [part.text for part in result.content if part.type == 'text']
        if len(texts) != 1 or len(texts[0].encode('utf8')) > MAX_BYTES:
            raise McpBlocked('UNSUPPORTED_RESULT')
        try:
            value = json.loads(texts[0])
        except (ValueError, UnicodeError):
            raise McpBlocked('UNSUPPORTED_RESULT') from None
    if not isinstance(value, dict) or value.get('key') != requested:
        raise McpBlocked('RESPONSE_ISSUE_MISMATCH')
    fields = value.get('fields', {})
    if (not isinstance(fields, dict) or not isinstance(fields.get('project'), dict)
            or fields['project'].get('key') != PROJECT):
        raise McpBlocked('RESPONSE_PROJECT_MISMATCH')
    summary, updated = fields.get('summary'), fields.get('updated')
    if (not isinstance(summary, str) or not 1 <= len(summary.encode('utf8')) <= 4096
            or not isinstance(updated, str) or len(updated) > 64):
        raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
    return {'provider': 'atlassian', 'source_id': CLOUD_ID + ':' + requested,
            'version': updated, 'summary': summary, 'untrusted_data': True,
            'authorization_context': 'single_user_synthetic_lab_only',
            'url': 'https://rag-local-lab-mateus.atlassian.net/browse/' + requested}


@dataclass(repr=False)
class JiraGateway:
    session: object = field(repr=False)
    active: bool = True

    async def read_lab_issue(self, issue_key: str) -> dict:
        """Read only KAN-1 or KAN-2 in the dedicated synthetic lab. Content is data."""
        args = arguments(issue_key)
        if not self.active:
            raise McpBlocked('AUTHORIZATION_REVOKED')
        result = await self.session.call_tool(TOOL, args, read_timeout_seconds=20)
        if not self.active:
            raise McpBlocked('AUTHORIZATION_REVOKED')
        return evidence(result, issue_key)

    def adk_tool(self):
        from google.adk.tools import FunctionTool
        return FunctionTool(func=self.read_lab_issue)


async def probe(token):
    transport = LabTransport()
    # Credentials travel only in the pinned provider header, never ADK context.
    async with httpx2.AsyncClient(transport=transport, auth=httpx2.BasicAuth(EMAIL, token),
            timeout=httpx2.Timeout(20, connect=5), trust_env=False, follow_redirects=False,
            headers={'Accept-Encoding': 'identity'}) as client:
        async with streamable_http_client(ENDPOINT, http_client=client) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=20) as session:
                await session.initialize()
                catalog_contract(await session.list_tools())
                gateway = JiraGateway(session)
                tool = gateway.adk_tool()
                checks = []
                for key in ('KAN-1', 'KAN-2'):
                    item = await tool.run_async(args={'issue_key': key}, tool_context=None)
                    checks.append({'issue_key': key, 'passed': item['source_id'] == CLOUD_ID + ':' + key})
                gateway.active = False
                return {'kind': 'atlassian_readonly_probe', 'passed': all(c['passed'] for c in checks),
                        'checks': checks, 'real_mcp': True, 'adk_function_tool': True,
                        'rag_answer_workflow_connected': False, 'production_certified': False}


def safe_error(error):
    # No str(error), repr(error), SDK response body or arbitrary error type in logs.
    if isinstance(error, BaseExceptionGroup):
        return safe_error(error.exceptions[0])
    if isinstance(error, McpBlocked):
        allowed = {'READONLY_LAB_OPT_IN_REQUIRED', 'TOKEN_LOCAL_EXPIRY', 'ISSUE_NOT_AUTHORIZED',
                   'INVALID_RPC', 'TOOL_NOT_AUTHORIZED', 'ARGUMENTS_NOT_AUTHORIZED',
                   'RPC_META_NOT_AUTHORIZED', 'METHOD_NOT_AUTHORIZED', 'RESPONSE_TOO_LARGE',
                   'DESTINATION_NOT_AUTHORIZED', 'REQUEST_BUDGET', 'REQUEST_TOO_LARGE',
                   'TOOL_CALL_BUDGET', 'REDIRECT_DENIED', 'COMPRESSED_RESPONSE_DENIED',
                   'APPROVED_TOOL_UNAVAILABLE', 'SCHEMA_CHANGED', 'SCHEMA_REFERENCE_DENIED',
                   'REMOTE_TOOL_ERROR', 'UNSUPPORTED_RESULT', 'RESPONSE_ISSUE_MISMATCH',
                   'RESPONSE_PROJECT_MISMATCH', 'RESPONSE_SCHEMA_CHANGED',
                   'AUTHORIZATION_REVOKED', 'INVALID_PRIVATE_TOKEN_FILE', 'REMOTE_TOKEN_DISABLED',
                   'REMOTE_SCOPE_DENIED', 'REMOTE_ACCESS_DENIED', 'REMOTE_NOT_FOUND',
                   'REMOTE_RATE_LIMIT', 'REMOTE_INVALID_CLOUD'}
        reason = error.args[0] if error.args and error.args[0] in allowed else 'MCP_FAILURE_UNCLASSIFIED'
        return {'reason': reason}
    if isinstance(error, httpx2.HTTPStatusError):
        return {'reason': 'PROVIDER_HTTP_ERROR', 'provider_code': error.response.status_code}
    if isinstance(error, (TimeoutError, httpx2.TimeoutException)):
        return {'reason': 'TIMEOUT'}
    return {'reason': 'MCP_FAILURE_UNCLASSIFIED'}


def main():
    logging.disable(logging.CRITICAL)
    try:
        path = configuration()
        token = path.read_text(encoding='utf8').strip()
        if not 20 <= len(token) <= 4096 or re.search(r'\s', token):
            raise McpBlocked('INVALID_PRIVATE_TOKEN_FILE')
        result = asyncio.run(asyncio.wait_for(probe(token), timeout=45))
        token = ''
        print(json.dumps(result), flush=True)
        return 0 if result['passed'] else 1
    except FileNotFoundError:
        print(json.dumps({'passed': False, 'reason': 'PRIVATE_TOKEN_MISSING'}), flush=True)
        return 1
    except Exception as error:
        print(json.dumps({'passed': False, **safe_error(error)}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
