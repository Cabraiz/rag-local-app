import asyncio
import json
from uuid import UUID, uuid4
import httpx2
from .catalog import Catalog
from .errors import SafeError
from .privacy import query_safe

ENDPOINTS = {'ocr': 'http://ocr:8081/sse', 'rag': 'http://rag:8082/sse'}
TOOLS = {'ocr': 'extract_exams', 'rag': 'lookup_exams'}
API = 'http://api:8080'

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
        payload = value
    elif isinstance(value.get('structuredContent', value.get('structured_content')), dict):
        payload = value.get('structuredContent', value.get('structured_content'))
    else:
        content = value.get('content', [])
        if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
                or content[0].get('type') != 'text' or not isinstance(content[0].get('text'), str)
                or len(content[0]['text']) > 16000):
            raise SafeError('MCP_INVALID_RESULT')
        try:
            payload = json.loads(content[0]['text'], object_pairs_hook=unique_fields,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError('number')))
        except (ValueError, TypeError, RecursionError):
            raise SafeError('MCP_INVALID_RESULT') from None
    try:
        size = len(json.dumps(payload, allow_nan=False))
    except (ValueError, TypeError, RecursionError):
        raise SafeError('MCP_INVALID_RESULT') from None
    if size > 16000 or not isinstance(payload, dict) or payload.get('ok') is not True:
        raise SafeError('MCP_TOOL_FAILED')
    return payload

async def mcp_call(provider, arguments, timeout=12):
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
        except BaseException:
            pass

class Runtime:
    def __init__(self, image_ref=None, request_id=None):
        self.image_ref = image_ref
        self.request_id = request_id or str(uuid4())
        try:
            if str(UUID(self.request_id)) != self.request_id:
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
        if kind == 'ocr':
            value = await mcp_call('ocr', {'image_ref': self.image_ref})
            names = value.get('exam_names')
            if (value.get('pii_masked') is not True or type(value.get('unresolved_count')) is not int
                    or value.get('unresolved_count') != 0 or not isinstance(names, list) or not 1 <= len(names) <= 20):
                raise SafeError('OCR_PRIVACY_OR_EXTRACTION_GATE')
            if any(query_safe(name) not in self.catalog.by_name for name in names):
                raise SafeError('OCR_UNAUTHORIZED_OUTPUT')
            return {'names': names}
        if kind == 'retrieve':
            value = await mcp_call('rag', {'exam_names': node_input['names']})
            if value.get('unresolved_indices') != [] or value.get('catalog_version') != self.catalog.version:
                raise SafeError('RAG_INCOMPLETE_OR_STALE')
            return {'names': node_input['names'], 'exams': value.get('exams'), 'catalog_version': value['catalog_version']}
        if kind == 'validate':
            exams = node_input.get('exams')
            expected_codes = {self.catalog.by_name[query_safe(name)]['code'] for name in node_input['names']}
            if not isinstance(exams, list) or len(exams) != len(expected_codes):
                raise SafeError('EXAM_EVIDENCE_MISMATCH')
            seen = set()
            for row in exams:
                if (not isinstance(row, dict) or set(row) != {'name', 'code', 'evidence'}
                        or any(not isinstance(row[key], str) for key in ('name', 'code', 'evidence'))):
                    raise SafeError('EXAM_EVIDENCE_MISMATCH')
                canonical = self.catalog.by_code.get(row['code'])
                if not canonical or row['code'] in seen or any(row[k] != canonical[k] for k in ('name', 'evidence')):
                    raise SafeError('EXAM_EVIDENCE_MISMATCH')
                seen.add(row['code'])
            if seen != expected_codes:
                raise SafeError('EXAM_EVIDENCE_MISMATCH')
            return {'exams': exams, 'catalog_version': self.catalog.version, 'validated': True}
        if kind == 'schedule':
            if node_input.get('validated') is not True:
                raise SafeError('SCHEDULING_GATE')
            body = {'request_id': self.request_id, 'exam_codes': sorted(row['code'] for row in node_input['exams']),
                    'catalog_version': self.catalog.version}
            receipt = await self.book(body)
            return {'exams': node_input['exams'], 'receipt': receipt}
        return {'result': {'fictional': True, 'exams': node_input['exams'], 'receipt': node_input['receipt'],
                           'stages': self.stages, 'model_calls': self.model_calls,
                           'transport': 'legacy-http-sse'}}

    async def book(self, body):
        from .contracts import AppointmentReceipt
        async with httpx2.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            for attempt in range(2):
                try:
                    response = await client.post(API + '/appointments', json=body)
                except (httpx2.TimeoutException, httpx2.TransportError):
                    if attempt == 0:
                        # Only the same key/payload may be retried after unknown outcome.
                        await asyncio.sleep(0.1)
                        continue
                    raise SafeError('APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY') from None
                if response.status_code == 408 or 500 <= response.status_code <= 599:
                    # A server/proxy can fail after commit: only reconciliation can decide.
                    raise SafeError('APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY')
                if response.status_code not in (200, 201):
                    raise SafeError('APPOINTMENT_REJECTED')
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
