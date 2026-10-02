# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Offline selector boundaries with fake provider; no real key, no network."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from unittest.mock import MagicMock
from rag_app import ledger
from rag_app.domain import Identity, RequestError
from rag_app import gemini_grounded as g

ROW = {'id':'chunk-1','document_id':'doc-1','release_id':'release-1','content_hash':'hash',
       'acl_epoch':1,'title':'Alimentação','quote':'O limite de alimentação é de 45 reais por pessoa.'}
ENV={'RAG_MODE':'lab','RAG_GEMINI_RESPONSES':'free_lab','RAG_GEMINI_FREE_CONFIRMED':'no_billing','GOOGLE_GENAI_USE_VERTEXAI':'false'}

async def run():
    checks=[]
    def check(name,ok):
        assert ok,name
        checks.append(name)
    good=json.dumps({'answerable':True,'chunk_id':'chunk-1'})
    p=g.parse(good,[ROW])
    check('canonical_quote_not_model_text',p.text=='Trecho da fonte:\n'+ROW['quote'] and p.model==g.MODEL)
    check('canonical_citation',p.citations[0].quote==ROW['quote'] and p.citations[0].acl_epoch==1)
    current={'id':'synthetic','fence':1,'tenant':'demo-a','actor':'ana','source_snapshot':{}}
    db=MagicMock(); db.execute.return_value.fetchone.return_value=current
    def terminal(_db,_row,_state,result,**_kwargs):return result
    with patch.dict(os.environ,{**ENV,'RAG_RETRIEVAL':'extractive_lab'},clear=True), \
            patch.object(ledger,'connect') as connect, patch.object(ledger,'lock_admission'), \
            patch.object(ledger,'terminal',terminal), patch.object(ledger,'citation_valid') as authorized:
        connect.return_value.__enter__.return_value=db
        authorized.return_value=False
        revoked=ledger.finish(current,p)
        check('commit_rechecks_revoked_evidence',revoked['kind']=='ABSTAIN' and not revoked['citations'])
        check('commit_uses_backend_identity',authorized.call_args.args[1]==Identity('demo-a','ana'))
        authorized.return_value=True
        check('authorized_model_citation_commits',ledger.finish(current,p)['model']==g.MODEL)
        from dataclasses import replace
        try:ledger.finish(current,replace(p,text='Trecho da fonte:\n999'))
        except RequestError:check('commit_rejects_generated_fact',True)
        else:raise AssertionError('generated_fact_committed')
        try:ledger.finish(current,replace(p,model='paid-fallback'))
        except RequestError:check('commit_rejects_model_override',True)
        else:raise AssertionError('model_override_committed')
    check('abstention',g.parse('{"answerable":false,"chunk_id":""}',[ROW]).kind=='ABSTAIN')
    for name,text in [('fabricated_id','{"answerable":true,"chunk_id":"other"}'),
        ('string_bool','{"answerable":"true","chunk_id":"chunk-1"}'),
        ('injected_answer','{"answerable":true,"chunk_id":"chunk-1","answer":"999"}'),
        ('invalid_json','not json'),('nonempty_abstention','{"answerable":false,"chunk_id":"chunk-1"}')]:
        try:g.parse(text,[ROW])
        except (ValueError,TypeError):check(name,True)
        else:raise AssertionError(name)
    with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,ENV,clear=True), patch.object(g,'USAGE',Path(temp)/'usage.db'):
        check('first_reservation',g.reserve('one','same') is None)
        try:g.reserve('one','same')
        except g.ProbeBlocked:check('no_second_dispatch_reserved',True)
        else:raise AssertionError('repeat_dispatch')
        g.complete('one',json.loads(good))
        check('cached_result',g.reserve('one','same')==json.loads(good))
        try:g.reserve('one','changed')
        except g.ProbeBlocked:check('payload_change_denied',True)
        else:raise AssertionError('changed_payload')
        with patch.object(g,'call_model',side_effect=AssertionError('REMOTE_CALL')) as call:
            check('no_evidence_no_call',(await g.select({'id':'empty','question':'nonsense'},[])).kind=='ABSTAIN')
            check('oversize_no_call',(await g.select({'id':'big','question':'x'*25000},[ROW])).kind=='ABSTAIN')
            check('zero_unneeded_calls',call.call_count==0)
        with patch.object(g,'call_model',side_effect=TimeoutError('fake')):
            with patch.object(Path,'read_text',return_value='synthetic_test_key_not_a_credential'):
                check('timeout_safe_abstention',(await g.select({'id':'timeout','question':'pergunta'},[ROW])).kind=='ABSTAIN')
        with patch.object(g,'call_model',side_effect=AssertionError('RETRY')) as call:
            check('timeout_not_retried',(await g.select({'id':'timeout','question':'pergunta'},[ROW])).kind=='ABSTAIN')
            check('no_retry_call',call.call_count==0)
        with patch.dict(os.environ,{'GOOGLE_GENAI_USE_VERTEXAI':'true'}):
            try:g.configuration()
            except g.ProbeBlocked:check('vertex_blocked',True)
            else:raise AssertionError('vertex_allowed')
    return {'passed':True,'checks':checks}

if __name__=='__main__':
    rounds=[asyncio.run(run()),asyncio.run(run())]
    print(json.dumps({'passed':True,'scope':'offline fake-provider guards, not real inference','round_checks':[len(r['checks']) for r in rounds]}))
