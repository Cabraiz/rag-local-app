import asyncio
import json
import pytest
from mcp.client.sse import sse_client
from mcp import ClientSession

@pytest.mark.parametrize('server,name,args', [
 ('rag','lookup_exams',{'exam_names':{'Pessoa Sentinela ZQX':'PRIVATE_SENTINEL'}}),
 ('rag','lookup_exams',{'exam_names':['TSH'],'pessoa.sentinela@example.invalid':'PRIVATE_SENTINEL'}),
 ('rag','Pessoa Sentinela ZQX',{}),
 ('ocr','extract_exams',{'image_ref':{'Pessoa Sentinela ZQX':'PRIVATE_SENTINEL'}}),
 ('ocr','extract_exams',{'image_ref':'request.png','pessoa.sentinela@example.invalid':'PRIVATE_SENTINEL'}),
])
def test_protocol_errors_cannot_reflect_pii(server,name,args):
    async def check():
        port=8081 if server=='ocr' else 8082
        async with sse_client(f'http://{server}:{port}/sse',timeout=3,sse_read_timeout=10) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                result=await session.call_tool(name,args)
                text=json.dumps(result.model_dump(by_alias=True))
                assert 'Pessoa Sentinela ZQX' not in text
                assert 'pessoa.sentinela@example.invalid' not in text
                assert 'PRIVATE_SENTINEL' not in text
                assert result.is_error or '"ok": false' in text or '\\"ok\\": false' in text
    asyncio.run(check())

