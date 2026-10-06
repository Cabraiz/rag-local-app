"""The two calls the runtime itself makes to the spec's MCP servers, outside any model turn: the
reading server's check of an image before the first model turn, and the catalog search of every line
read, for the check of the whole order after the run (runtime/reconcilia.py). No model sees either."""
import asyncio

from mcp import ClientSession
from mcp.client.sse import sse_client

from .reconcilia import order_lines, unreported

IMAGE_CHECK = 'check_image'  # the OCR server's check of a file without the OCR; no spec, so no agent, has it
SEARCHES_AT_ONCE, CHECK_SECONDS = 8, 30  # the check of the whole order: searches in flight, and its limit


async def image_problem(url, filename):
    """Why the reading server refuses the file (missing, too large, not the format its name says,
    corrupt, a photo it would barely read), or None. A server without IMAGE_CHECK (another spec's
    reader) leaves the file to the run. Raises when the server does not answer."""
    async with sse_client(url, timeout=5, sse_read_timeout=CHECK_SECONDS) as streams, ClientSession(*streams) as session:
        await session.initialize()
        if IMAGE_CHECK not in [tool.name for tool in (await session.list_tools()).tools]:
            return None
        result = await session.call_tool(IMAGE_CHECK, {'filename': filename})
    if not result.is_error:
        return None
    texts = ' '.join(item.text for item in result.content if getattr(item, 'text', None))
    return texts.removeprefix(f'Error executing tool {IMAGE_CHECK}: ')[:300]


async def search_lines(url, tool, texts, top_k):
    """text -> the catalog search's hits (MCP over SSE): the same search the agent uses, which cuts a
    line into its exams and tags each hit with its piece. The searches share one session and run at
    once, a few at a time."""
    limit = asyncio.Semaphore(SEARCHES_AT_ONCE)
    async with sse_client(url, timeout=5, sse_read_timeout=CHECK_SECONDS) as streams, ClientSession(*streams) as session:
        await session.initialize()

        async def search(text):
            async with limit:
                result = await session.call_tool(tool, {'query': text[:200], 'top_k': top_k})
            payload = None if result.is_error else (result.structured_content or {}).get('result')
            return text, payload if isinstance(payload, list) else []
        return dict(await asyncio.gather(*map(search, texts)))


async def unreported_exams(order, url, tool, policy):
    """(the exams of the order the run left in no reported state, True if the search did not answer):
    every line read is searched on the spec's catalog server (runtime/reconcilia.py)."""
    texts = list(dict.fromkeys(text for _, text, _ in order_lines(order.get('ocr_read', []))))
    if not texts or not (url and tool):
        return [], False
    try:
        hits = await asyncio.wait_for(search_lines(url, tool, texts, policy.top_k), CHECK_SECONDS)
    except Exception:  # the search went down after the run: say the order was not checked
        return [], True
    appointment = order.get('booked_appointment') if isinstance(order.get('booked_appointment'), dict) else {}
    settled = {exam.get('code') for exam in appointment.get('exams') or [] if isinstance(exam, dict)}
    settled |= {item['code'] for item in [*order.get('low_confidence', []), *order.get('confirmed', [])]}
    return unreported(order, hits.get, policy, settled), False
