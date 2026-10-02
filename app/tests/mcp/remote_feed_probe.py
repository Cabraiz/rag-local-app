# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Read fixed MCP tool metadata; secret stays in process, logging disabled."""
import asyncio
from datetime import timedelta
import json
import logging
from pathlib import Path
import sys
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
sys.path.insert(0,str((_workspace_root / "app")/'src'))
from rag_app.github_lab import safe_error
from rag_app.remote_feed import payload,normalize

async def run(provider):
    folder=Path('D:/RAG-Local/.local/secrets')
    if provider=='github':
        endpoint='https://api.githubcopilot.com/mcp/x/pull_requests/readonly'
        headers={'Authorization':'Bearer '+(folder/'github-mcp-token.txt').read_text().strip(),
                 'X-MCP-Readonly':'true','X-MCP-Tools':'list_pull_requests'}
    else:
        endpoint='https://mcp.atlassian.com/v2/mcp'
        token_file='atlassian-mcp-token-pending.txt' if '--pending' in sys.argv else 'atlassian-mcp-token.txt' if '--original' in sys.argv else 'atlassian-mcp-token-replacement.txt'
        token=(folder/token_file).read_text(encoding='utf-8-sig').strip()
        import base64
        headers={'Authorization':'Basic '+base64.b64encode(('mateusccabr@gmail.com:'+token).encode()).decode()}
    async with httpx2.AsyncClient(headers={**headers,'Accept-Encoding':'identity'},trust_env=False,follow_redirects=False,timeout=15) as client:
        async with streamable_http_client(endpoint,http_client=client) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=15) as session:
                await session.initialize()
                catalog=await session.list_tools()
                names={'list_pull_requests','searchJiraIssuesUsingJql','getJiraIssue'}
                selected=[{'name':t.name,'schema':t.input_schema} for t in catalog.tools if t.name in names]
                print(json.dumps({'provider':provider,'initialized':True,'approved_metadata':selected}))
                if provider=='github':
                    result=await session.call_tool('list_pull_requests',{'owner':'Cabraiz','repo':'rag-mcp-lab','state':'all','sort':'updated','direction':'desc','perPage':20,'page':1})
                    # Only shape and HTTP-like error code; never emit the provider body.
                    print(json.dumps({'provider':provider,'tool_is_error':result.is_error,'content_types':[c.type for c in result.content],
                                      'structured_type':type(result.structured_content).__name__,
                                      'permission_denied':any('403' in getattr(c,'text','') or 'Resource not accessible' in getattr(c,'text','') for c in result.content)}))
                else:
                    resources=await session.call_tool('getAccessibleAtlassianResources',{})
                    resource_text=' '.join(getattr(c,'text','') for c in resources.content)
                    print(json.dumps({'provider':provider,'resource_discovery_is_error':resources.is_error,
                        'expected_cloud_present':'15445c1f-6463-4ece-bdb1-eecd7c4d5968' in resource_text,
                        'expected_site_present':'rag-local-lab-mateus.atlassian.net' in resource_text,
                        'http_codes':[code for code in ('401','403') if code in resource_text]}))
                    result=await session.call_tool('searchJiraIssuesUsingJql',{
                        'cloudId':'15445c1f-6463-4ece-bdb1-eecd7c4d5968',
                        'jql':'project = KAN ORDER BY updated DESC, key ASC','maxResults':50,
                        'fields':['summary','status','project','created','updated'],
                        'searchResultMode':'issues','responseContentFormat':'markdown'})
                    value=result.structured_content
                    if value is None and len(result.content)==1 and result.content[0].type=='text':
                        try:value=json.loads(result.content[0].text)
                        except ValueError:value=None
                    inner=value.get('data',value) if isinstance(value,dict) else None
                    print(json.dumps({'provider':provider,'tool_is_error':result.is_error,
                        'top_keys':list(value) if isinstance(value,dict) else [],
                        'inner_type':type(inner).__name__,'inner_keys':list(inner) if isinstance(inner,dict) else [],
                        'issues':[{ 'key':i.get('key'),'keys':list(i),'field_keys':list(i.get('fields',{})), 'status':i.get('fields',{}).get('status')} for i in inner.get('issues',[])[:5]] if isinstance(inner,dict) else [],
                        'paging':{k:inner.get(k) for k in ('isLast','nextPageToken','total','hasMore')} if isinstance(inner,dict) else {},
                        'permission_denied':any('401' in getattr(c,'text','') or '403' in getattr(c,'text','') for c in result.content),
                        'http_codes':[code for code in ('401','403','404','429') if any(code in getattr(c,'text','') for c in result.content)],
                        'auth_flags':[term for term in ('Unauthorized','invalid_token','insufficient_scope','permission','scopes','API token') if any(term in getattr(c,'text','') for c in result.content)]}))
                    if not result.is_error:
                        normalized=normalize('jira',payload(result))
                        print(json.dumps({'normalization_passed':True,'ids':[i['id'] for i in normalized],
                            'missing_updated_count':sum(i['updated_at'] is None for i in normalized)}))

if __name__=='__main__':
    logging.disable(logging.CRITICAL)
    try:asyncio.run(asyncio.wait_for(run(sys.argv[1]),timeout=40))
    except Exception as exc:print(json.dumps({'provider':sys.argv[1],'passed':False,**safe_error(exc)}));sys.exit(1)
