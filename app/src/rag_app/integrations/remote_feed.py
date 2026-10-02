"""Bounded real MCP source reads. Content is data, never instructions."""
import asyncio
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from .atlassian_lab import BoundedStream, McpBlocked, CLOUD_ID, EMAIL
from .github_lab import safe_error

ENDPOINTS={'github':'https://api.githubcopilot.com/mcp/x/pull_requests/readonly','jira':'https://mcp.atlassian.com/v2/mcp'}
TOOLS={'github':'list_pull_requests','jira':'searchJiraIssuesUsingJql'}
MAX_PAGES=10
PAGE_SIZE=50


def arguments(provider,page=1,cursor=None):
    if provider=='github':
        if type(page) is not int or not 1<=page<=MAX_PAGES:raise McpBlocked('ARGUMENTS_NOT_AUTHORIZED')
        return dict(owner='Cabraiz',repo='rag-mcp-lab',state='all',sort='created',direction='desc',
            perPage=PAGE_SIZE,page=page,fields=['number','title','state','draft','html_url','created_at','updated_at','merged_at'])
    if provider!='jira' or (cursor is not None and (not isinstance(cursor,str) or not 1<=len(cursor)<=2048)):
        raise McpBlocked('ARGUMENTS_NOT_AUTHORIZED')
    args=dict(cloudId=CLOUD_ID,jql='project = KAN ORDER BY created DESC, key ASC',maxResults=PAGE_SIZE,
        fields=['summary','status','project','created','updated'],searchResultMode='issues',responseContentFormat='markdown')
    if cursor:args['nextPageToken']=cursor
    return args


class FeedTransport(httpx2.AsyncBaseTransport):
    def __init__(self,provider,inner=None):
        self.provider=provider;self.inner=inner or httpx2.AsyncHTTPTransport(retries=0,trust_env=False)
        self.requests=0;self.calls=0
    async def handle_async_request(self,request):
        if str(request.url)!=ENDPOINTS[self.provider] or request.method not in {'POST','GET','DELETE'}:raise McpBlocked('DESTINATION_NOT_AUTHORIZED')
        self.requests+=1
        if self.requests>18:raise McpBlocked('REQUEST_BUDGET')
        if request.method=='POST':
            raw=await request.aread()
            if len(raw)>16384:raise McpBlocked('REQUEST_TOO_LARGE')
            value=json.loads(raw);method=value.get('method')
            if value.get('jsonrpc')!='2.0':raise McpBlocked('INVALID_RPC')
            if method=='tools/call':
                self.calls+=1
                if self.calls>MAX_PAGES:raise McpBlocked('TOOL_CALL_BUDGET')
                params=value.get('params',{});args=params.get('arguments',{})
                if params.get('name')!=TOOLS[self.provider]:raise McpBlocked('TOOL_NOT_AUTHORIZED')
                if args!=arguments(self.provider,args.get('page',1),args.get('nextPageToken')) or set(params)-{'name','arguments','_meta'} or params.get('_meta') not in (None,{}):raise McpBlocked('ARGUMENTS_NOT_AUTHORIZED')
            elif method not in {'initialize','notifications/initialized','tools/list','notifications/cancelled'}:raise McpBlocked('METHOD_NOT_AUTHORIZED')
        response=await self.inner.handle_async_request(request)
        if 300<=response.status_code<400:
            await response.aclose();raise McpBlocked('REDIRECT_DENIED')
        if response.headers.get('content-encoding','identity') not in ('identity',''):
            await response.aclose();raise McpBlocked('COMPRESSED_RESPONSE_DENIED')
        response.stream=BoundedStream(response.stream)
        return response
    async def aclose(self):await self.inner.aclose()


def payload(result):
    if result.is_error:
        # Provider content stays private; only stable error codes leave the boundary.
        text=' '.join(getattr(c,'text','') for c in result.content)
        for code in ('401','403','429'):
            if code in text:raise McpBlocked('REMOTE_'+code)
        raise McpBlocked('REMOTE_TOOL_ERROR')
    value=result.structured_content
    if value is None and len(result.content)==1 and result.content[0].type=='text':
        value=json.loads(result.content[0].text)
    # Current Atlassian MCP V2 wraps its compact result in a data envelope.
    if isinstance(value,dict) and set(value)=={'data'}:return value['data']
    return value


def jira_expired(metadata,current=None):
    if not isinstance(metadata,dict) or metadata.get('email')!=EMAIL or metadata.get('verified') is not True:
        raise McpBlocked('INVALID_PRIVATE_TOKEN_FILE')
    expiry=metadata.get('expires')
    if not isinstance(expiry,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',expiry):
        raise McpBlocked('INVALID_PRIVATE_TOKEN_FILE')
    try:expiry=date.fromisoformat(expiry)
    except ValueError:raise McpBlocked('INVALID_PRIVATE_TOKEN_FILE') from None
    return (current or datetime.now(timezone.utc).date())>=expiry


def short(value,limit):
    if not isinstance(value,str) or not 1<=len(value)<=limit or any(ord(c)<32 for c in value):raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
    return value


def timestamp(value):
    value=short(value,64)
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
    return parsed.astimezone(timezone.utc).isoformat()


def normalize(provider,value):
    items=value if provider=='github' else value.get('issues') if isinstance(value,dict) else None
    if not isinstance(items,list) or len(items)>PAGE_SIZE:raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
    rows=[]
    for item in items:
        if not isinstance(item,dict):raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
        if provider=='github':
            number=item.get('number')
            if type(number) is not int or not 1<=number<=2147483647:raise McpBlocked('RESPONSE_SCHEMA_CHANGED')
            url=f'https://github.com/Cabraiz/rag-mcp-lab/pull/{number}'
            if item.get('html_url')!=url or item.get('state') not in ('open','closed'):raise McpBlocked('RESPONSE_SCOPE_MISMATCH')
            rows.append(dict(id=str(number),title=short(item.get('title'),1000),url=url,
                status='Merged' if item.get('merged_at') else 'Draft' if item.get('draft') else item['state'],
                updated_at=timestamp(item.get('updated_at')),created_at=timestamp(item.get('created_at'))))
        else:
            key=item.get('key');fields=item.get('fields')
            if not isinstance(key,str) or not re.fullmatch(r'KAN-[1-9][0-9]{0,9}',key) or not isinstance(fields,dict):raise McpBlocked('RESPONSE_SCOPE_MISMATCH')
            project=fields.get('project')
            if project is not None and (not isinstance(project,dict) or project.get('key')!='KAN'):raise McpBlocked('RESPONSE_SCOPE_MISMATCH')
            status=fields.get('status');status=status.get('name') if isinstance(status,dict) else status
            rows.append(dict(id=key,title=short(fields.get('summary'),1000),status=short(status,80),
                url='https://rag-local-lab-mateus.atlassian.net/browse/'+key,
                updated_at=timestamp(fields['updated']) if fields.get('updated') is not None else None,
                created_at=timestamp(fields.get('created'))))
    return rows


async def fetch(provider):
    filename='github_mcp_token' if provider=='github' else 'atlassian_mcp_token'
    token=Path('/run/secrets/'+filename).read_text(encoding='utf-8-sig').strip()
    if provider=='github':
        if not re.fullmatch(r'github_pat_[A-Za-z0-9_]{20,240}',token):raise McpBlocked('INVALID_PRIVATE_TOKEN_FILE')
        headers={'Authorization':'Bearer '+token,'X-MCP-Readonly':'true','X-MCP-Tools':TOOLS[provider]}
        auth=None
    else:
        metadata=json.loads(Path('/run/secrets/atlassian_mcp_metadata').read_text(encoding='utf-8-sig'))
        if jira_expired(metadata):raise McpBlocked('TOKEN_LOCAL_EXPIRY')
        if not 20<=len(token)<=4096 or any(c.isspace() for c in token):raise McpBlocked('INVALID_PRIVATE_TOKEN_FILE')
        headers={};auth=httpx2.BasicAuth(EMAIL,token)
    rows={};cursor=None;seen=set();complete=False
    async with httpx2.AsyncClient(transport=FeedTransport(provider),headers={**headers,'Accept-Encoding':'identity'},auth=auth,
        timeout=httpx2.Timeout(15,connect=5),trust_env=False,follow_redirects=False) as client:
        async with streamable_http_client(ENDPOINTS[provider],http_client=client) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=15) as session:
                await session.initialize();catalog=await session.list_tools()
                tool=next((t for t in catalog.tools if t.name==TOOLS[provider]),None)
                if tool is None:raise McpBlocked('APPROVED_TOOL_UNAVAILABLE')
                from jsonschema import Draft202012Validator
                def no_refs(node):
                    if isinstance(node,dict):
                        if '$ref' in node or '$dynamicRef' in node:raise McpBlocked('SCHEMA_REFERENCE_DENIED')
                        for v in node.values():no_refs(v)
                    elif isinstance(node,list):
                        for v in node:no_refs(v)
                no_refs(tool.input_schema)
                for page in range(1,MAX_PAGES+1):
                    args=arguments(provider,page,cursor);Draft202012Validator(tool.input_schema).validate(args)
                    value=payload(await session.call_tool(TOOLS[provider],args,read_timeout_seconds=15))
                    batch=normalize(provider,value)
                    for row in batch:rows[row['id']]=row
                    if provider=='github':complete=len(batch)<PAGE_SIZE
                    else:
                        cursor=value.get('nextPageToken')
                        complete=value.get('isLast') is True or (value.get('isLast') is not False and not cursor)
                        if not complete and (not cursor or cursor in seen):raise McpBlocked('PAGINATION_INVALID')
                        if cursor:seen.add(cursor)
                    if complete:break
    return {'items':list(rows.values()),'complete':complete,'pages':page,'transport':'remote_mcp','read_only':True}


def safe_failure(error):
    if isinstance(error,BaseExceptionGroup):return safe_failure(error.exceptions[0])
    if isinstance(error,McpBlocked) and error.args and error.args[0] in ('REMOTE_401','REMOTE_403','REMOTE_429','TOKEN_LOCAL_EXPIRY','PAGINATION_INVALID','RESPONSE_SCOPE_MISMATCH'):
        return error.args[0]
    return safe_error(error).get('reason','MCP_FAILURE')
