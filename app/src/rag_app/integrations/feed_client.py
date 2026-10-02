"""Fixed MCP calls from the API; provider credentials never enter this service."""
import json
import os
import httpx
from .domain import RequestError


def call(token,refresh=False):
    if os.environ.get('RAG_REMOTE_FEED')!='enabled':raise RequestError('REMOTE_FEED_DISABLED',409)
    headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream',
             'MCP-Protocol-Version':'2025-11-25'}
    def rpc(client,method,params,rid):
        with client.stream('POST','/mcp',json={'jsonrpc':'2.0','id':rid,'method':method,'params':params}) as response:
            if response.status_code!=200:raise RequestError('MCP_GATEWAY_UNAVAILABLE',503)
            raw=b''
            for chunk in response.iter_bytes():
                raw+=chunk
                if len(raw)>2097152:raise RequestError('MCP_GATEWAY_RESPONSE_LIMIT',503)
            value=json.loads(raw)
            if value.get('id')!=rid or 'error' in value:raise RequestError('MCP_GATEWAY_FAILURE',503)
            return value['result']
    try:
        with httpx.Client(base_url='http://integration-gateway:8000',headers=headers,timeout=5,trust_env=False,follow_redirects=False) as client:
            initialized=rpc(client,'initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'rag-app','version':'0.1.0'}},1)
            headers_version=initialized.get('protocolVersion')
            if headers_version not in ('2025-11-25','2025-06-18','2025-03-26'):raise RequestError('MCP_VERSION_UNSUPPORTED',503)
            client.headers['MCP-Protocol-Version']=headers_version
            result=rpc(client,'tools/call',{'name':'refresh_integrations' if refresh else 'integration_feed','arguments':{}},2)
            if result.get('isError'):raise RequestError('MCP_GATEWAY_FAILURE',503)
            value=result.get('structuredContent')
            if value is None:value=json.loads(result['content'][0]['text'])
            if not isinstance(value,dict):raise RequestError('MCP_GATEWAY_SCHEMA',503)
            return value
    except (httpx.HTTPError,ValueError,KeyError,TypeError):raise RequestError('MCP_GATEWAY_UNAVAILABLE',503) from None
