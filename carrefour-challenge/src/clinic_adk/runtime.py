import asyncio
import json
from uuid import UUID, uuid4
import httpx2
from .catalog import Catalog
from .errors import SafeError
from .privacy import query_safe
from .privacy_sinks import private_boundary, private_mcp_output, rag_output

ENDPOINTS = {'ocr': 'http://ocr:8081/sse', 'rag': 'http://rag:8082/sse'}
TOOLS = {'ocr': 'extract_exams', 'rag': 'lookup_exams'}
API = 'http://api:8080'

def appointment_status(status):
    if status in (200, 201):
        return
    if 400 <= status <= 499 and status != 408:
        raise SafeError('APPOINTMENT_REJECTED')
    # An unexpected success/redirect or server/proxy failure may follow commit.
    raise SafeError('APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY')


class ReceiptStream(httpx2.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > 4096:
                raise SafeError('APPOINTMENT_INVALID_RECEIPT')
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class AppointmentTransport(httpx2.AsyncBaseTransport):
    def __init__(self):
        self.transport = httpx2.AsyncHTTPTransport(trust_env=False, retries=0)

    async def handle_async_request(self, request):
        response = await self.transport.handle_async_request(request)
        try:
            appointment_status(response.status_code)
            # The fixed local API serves uncompressed JSON. Refuse decompression
            # before HTTPX buffers a body whose decoded size is attacker-controlled.
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise SafeError('APPOINTMENT_INVALID_RECEIPT')
        except SafeError:
            await response.aclose()
            raise
        response.stream = ReceiptStream(response.stream)
        return response

    async def aclose(self):
        await self.transport.aclose()

def unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate field')
        value[key] = item
    return value

def decode_tool(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(by_alias=True)
    if not isinstance(value, dict) or value.get('isError') or value.get('is_error'):
        raise SafeError('MCP_INVALID_RESULT')
    if 'ok' in value:
        if any(key in value for key in ('content', 'structuredContent', 'structured_content')):
            raise SafeError('MCP_INVALID_RESULT')
        payloads = [value]
    else:
        payloads = []
        for key in ('structuredContent', 'structured_content'):
            if key in value and value[key] is not None:
                if not isinstance(value[key], dict):
                    raise SafeError('MCP_INVALID_RESULT')
                payloads.append(value[key])
        if 'content' in value:
            content = value['content']
            if not isinstance(content, list):
                raise SafeError('MCP_INVALID_RESULT')
            if content:
                if (len(content) != 1 or not isinstance(content[0], dict)
                        or content[0].get('type') != 'text' or not isinstance(content[0].get('text'), str)
                        or len(content[0]['text']) > 16000):
                    raise SafeError('MCP_INVALID_RESULT')
                try:
                    payloads.append(json.loads(content[0]['text'], object_pairs_hook=unique_fields,
                                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('number'))))
                except (ValueError, TypeError, RecursionError):
                    raise SafeError('MCP_INVALID_RESULT') from None
    if not payloads:
        raise SafeError('MCP_INVALID_RESULT')
    canonical = []
    for payload in payloads:
        try:
            encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
        except (ValueError, TypeError, RecursionError):
            raise SafeError('MCP_INVALID_RESULT') from None
        if len(encoded) > 16000 or not isinstance(payload, dict) or payload.get('ok') is not True:
            raise SafeError('MCP_TOOL_FAILED')
        canonical.append(encoded)
    # Every supplied JSON representation is a gate, never a lower-priority fallback.
    # Canonical JSON also distinguishes booleans/numbers that compare equal in Python.
    if len(set(canonical)) != 1:
        raise SafeError('MCP_INVALID_RESULT')
    return payloads[0]

@private_boundary
async def mcp_call(provider, arguments, timeout=12):
    if provider not in TOOLS:
        raise SafeError('MCP_TOOL_MANIFEST_MISMATCH')
    if provider == 'rag':
        if not isinstance(arguments, dict) or set(arguments) != {'exam_names'}:
            raise SafeError('EXAM_EVIDENCE_MISMATCH')
        names = arguments['exam_names']
        if not isinstance(names, list) or not 1 <= len(names) <= 20:
            raise SafeError('EXAM_EVIDENCE_MISMATCH')
        catalog, canonical = Catalog(), []
        for name in names:
            try:
                row = catalog.by_name.get(query_safe(name))
            except SafeError:
                raise SafeError('EXAM_EVIDENCE_MISMATCH') from None
            if row is None:
                raise SafeError('EXAM_EVIDENCE_MISMATCH')
            canonical.append(row['name'])
        arguments = {'exam_names': canonical}
    from google.adk.tools.mcp_tool import McpToolset
    from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
    toolset = McpToolset(connection_params=SseConnectionParams(url=ENDPOINTS[provider], timeout=3, sse_read_timeout=timeout),
                        tool_filter=[TOOLS[provider]])
    try:
        async with asyncio.timeout(timeout):
            tools = await toolset.get_tools()
            if len(tools) != 1 or tools[0].name != TOOLS[provider]:
                raise SafeError('MCP_TOOL_MANIFEST_MISMATCH')
            result = await tools[0].run_async(args=arguments, tool_context=None)
            return decode_tool(result)
    except SafeError:
        raise
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        raise SafeError('MCP_UNAVAILABLE') from None
    finally:
        try:
            await asyncio.wait_for(toolset.close(), timeout=3)
        except Exception:
            pass

class Runtime:
    def __init__(self, image_ref=None, request_id=None):
        self.image_ref = image_ref
        self.request_id = str(uuid4()) if request_id is None else request_id
        try:
            if not isinstance(self.request_id, str) or str(UUID(self.request_id)) != self.request_id:
                raise ValueError()
        except (ValueError, TypeError):
            raise SafeError('INVALID_REQUEST_ID') from None
        self.catalog = Catalog()
        self.stages = []
        self.model_calls = 0

    async def step(self, kind, node_input):
        expected = ('ocr', 'retrieve', 'validate', 'schedule', 'format')
        if len(self.stages) >= 5 or kind != expected[len(self.stages)]:
            raise SafeError('WORKFLOW_STAGE_ORDER')
        self.stages.append(kind)
        if kind == 'format':
            # The linear graph cannot obtain a consent-bound receipt. Never echo
            # an arbitrary caller's exams/receipt in a completion event.
            raise SafeError('JOURNEY_CONFIRMATION_REQUIRED')
        if kind == 'schedule':
            # These local codes are fixed; no remote exception reaches this path.
            if not isinstance(node_input, dict) or node_input.get('validated') is not True:
                raise SafeError('SCHEDULING_GATE')
            raise SafeError('JOURNEY_CONFIRMATION_REQUIRED')
        return await self._private_step(kind, node_input)

    @private_boundary
    async def _private_step(self, kind, node_input):
        if kind == 'ocr':
            value = await private_mcp_output('ocr', {'image_ref': self.image_ref}, mcp_call, self.catalog)
            return {'names': value['exam_names']}
        if kind == 'retrieve':
            names = [self.catalog.by_name[query_safe(name)]['name'] for name in node_input['names']]
            value = await private_mcp_output('rag', {'exam_names': names}, mcp_call, self.catalog)
            return {'names': names, 'exams': value['exams'], 'catalog_version': value['catalog_version']}
        if kind == 'validate':
            value = rag_output({'ok': True, 'exams': node_input.get('exams'),
                                # Historical local validate inputs omit the hash;
                                # every row still must match this exact catalog.
                                # Remote retrieval always requires its supplied hash.
                                'catalog_version': node_input.get('catalog_version', self.catalog.version),
                                'unresolved_indices': []}, node_input['names'], self.catalog)
            return {'exams': value['exams'], 'catalog_version': self.catalog.version, 'validated': True}
        raise SafeError('WORKFLOW_STAGE_ORDER')

    async def book(self, body):
        from .contracts import AppointmentReceipt
        async with httpx2.AsyncClient(timeout=5, trust_env=False, follow_redirects=False,
                                      transport=AppointmentTransport()) as client:
            for attempt in range(2):
                try:
                    response = await client.post(API + '/appointments', json=body)
                except (httpx2.TimeoutException, httpx2.TransportError):
                    if attempt == 0:
                        # Only the same key/payload may be retried after unknown outcome.
                        await asyncio.sleep(0.1)
                        continue
                    raise SafeError('APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY') from None
                appointment_status(response.status_code)
                if len(response.content) > 4096:
                    raise SafeError('APPOINTMENT_INVALID_RECEIPT')
                try:
                    payload = response.json(object_pairs_hook=unique_fields,
                                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('number')))
                    value = AppointmentReceipt.model_validate(payload).model_dump()
                except (ValueError, TypeError, RecursionError):
                    raise SafeError('APPOINTMENT_INVALID_RECEIPT') from None
                if (value['request_id'] != body['request_id'] or value['exam_codes'] != body['exam_codes']
                        or value['catalog_version'] != body['catalog_version']):
                    raise SafeError('APPOINTMENT_RECEIPT_MISMATCH')
                UUID(value['appointment_id'])
                return value
        raise SafeError('APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY')
