# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Scoped deterministic source-boundary checks; never call the providers."""
import asyncio
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import httpx2
sys.path.insert(0,str((_workspace_root / "app")/'src'))
from rag_app.remote_feed import normalize,arguments,FeedTransport,payload,jira_expired
from rag_app.atlassian_lab import McpBlocked

STAMP='2026-10-01T06:00:00Z'
PR=dict(number=7,title='New PR',html_url='https://github.com/Cabraiz/rag-mcp-lab/pull/7',state='open',draft=False,created_at=STAMP,updated_at=STAMP,merged_at=None)
count=0
def check(passed):
    global count
    assert passed;count+=1

async def run():
    check(normalize('github',[PR])[0]['id']=='7')
    check(normalize('github',[{**PR,'title':'<script>alert(1)</script>'}])[0]['title']=='<script>alert(1)</script>')
    check(normalize('github',[{**PR,'merged_at':STAMP,'state':'closed'}])[0]['status']=='Merged')
    for changed in ({'number':True},{'html_url':'https://evil.example/a'},{'state':'invented'},{'updated_at':'2026-10-01'},{'title':'\x00'}):
        try:normalize('github',[{**PR,**changed}]);raise AssertionError('INVALID_ACCEPTED')
        except (McpBlocked,ValueError):count_check=True
        check(count_check)
    issue={'key':'KAN-3','fields':{'summary':'New card','status':{'name':'To Do'},'project':{'key':'KAN'},'created':STAMP,'updated':STAMP}}
    check(normalize('jira',{'issues':[issue]})[0]['id']=='KAN-3')
    compact={'issues':[{**issue,'fields':{k:v for k,v in issue['fields'].items() if k!='updated'}}],'isLast':True}
    wrapped=payload(SimpleNamespace(is_error=False,structured_content={'data':compact},content=[]))
    check(wrapped==compact)
    check(normalize('jira',wrapped)[0]['updated_at'] is None)
    check(normalize('jira',wrapped)[0]['created_at']==STAMP.replace('Z','+00:00'))
    metadata={'email':'mateusccabr@gmail.com','expires':'2026-10-08','verified':True}
    check(not jira_expired(metadata,date(2026,10,7)))
    check(jira_expired(metadata,date(2026,10,8)))
    check(jira_expired(metadata,date(2026,10,9)))
    for changed in ({'verified':False},{'expires':'2026-02-31'},{'expires':'never'},{'email':'someone@example.com'}):
        try:jira_expired({**metadata,**changed},date(2026,10,1));raise AssertionError('BAD_METADATA_ACCEPTED')
        except McpBlocked:check(True)
    for key in ('OTHER-3','KAN-0','https://evil/KAN-3'):
        try:normalize('jira',{'issues':[{**issue,'key':key}]});raise AssertionError('SCOPE_ACCEPTED')
        except McpBlocked:check(True)
    for provider in ('jira','github'):
        requests=[]
        async def handler(request):
            requests.append(request)
            return httpx2.Response(200,json={'ok':True})
        transport=FeedTransport(provider,httpx2.MockTransport(handler))
        async with httpx2.AsyncClient(transport=transport) as client:
            from rag_app.remote_feed import ENDPOINTS,TOOLS
            base={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':TOOLS[provider],'arguments':arguments(provider)}}
            await client.post(ENDPOINTS[provider],json=base)
            check(len(requests)==1)
            if provider=='jira':
                for forbidden in ('createJiraIssue','editJiraIssue','transitionJiraIssue'):
                    try:await client.post(ENDPOINTS[provider],json={**base,'params':{'name':forbidden,'arguments':{}}});raise AssertionError('WRITE_ACCEPTED')
                    except McpBlocked:check(True)
            for value in ({**base,'method':'prompts/get'}, {**base,'params':{**base['params'],'name':'create_pull_request'}},
                {**base,'params':{**base['params'],'arguments':{**base['params']['arguments'],'owner':'Other'}}},
                {**base,'params':{**base['params'],'_meta':{'token':'never'}}}):
                try:await client.post(ENDPOINTS[provider],json=value);raise AssertionError('RPC_ACCEPTED')
                except McpBlocked:check(True)
            try:await client.post('https://evil.example/mcp',json=base);raise AssertionError('SSRF_ACCEPTED')
            except McpBlocked:check(True)
            check(len(requests)==1)

if __name__=='__main__':
    rounds=[]
    for _ in range(2):
        before=count;asyncio.run(run());rounds.append(count-before)
    print(json.dumps({'passed':True,'round_checks':rounds,'scope':'deterministic MCP read-only boundaries, live envelope parsing and expiry; not a live provider test'}))
