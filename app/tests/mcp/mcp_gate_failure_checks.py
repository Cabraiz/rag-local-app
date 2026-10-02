# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Proof preservation controls only, never pretend mocked providers are live."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import mcp_evidence_gate as gate


def run():
    attempt={}
    # Early failure after deterministic boundary checks must retain its attempt.
    with patch.object(gate,'current_feed',return_value={'providers':[
            {'id':'jira','state':'connected','complete':True,'stale':False,'items':[]}]}), \
            patch.object(gate.secrets.SystemRandom,'sample',return_value=['jira','github']):
        try:
            gate.round_run(1,'not-a-token','not-a-token',attempt)
        except AssertionError as error:
            assert str(error)=='jira_actual_remote_mcp'
        else:
            raise AssertionError('FAILURE_NOT_DETECTED')
    assert attempt['passed'] is False and len(attempt['checks'])==24
    assert attempt['number']==1 and attempt['records']==[]
    # A completed first case followed by a failing second one must also survive.
    approved={'document_id':'synthetic-doc','content_hash':'synthetic-hash',
              'document':{'source_key':'mcp_jira_KAN-1','text':'Snapshot consultado em: teste.'}}
    evidence={'kind':'EXTRACTIVE','model':'gemini-3.5-flash-lite',
              'text':'Trecho da fonte:\nSnapshot consultado em: teste.',
              'citations':[{'document_id':'synthetic-doc','content_hash':'synthetic-hash',
                            'quote':'Snapshot consultado em: teste.'}]}
    def simulated_api(path,token=None,body=None,headers=None):
        if path=='/v1/requests':return 202,{'request_id':'synthetic-request'}
        if path=='/v1/lab/corpus':return 200,{'generation':1,'documents':[{'id':'synthetic-doc'}]}
        if path=='/v1/lab/corpus/documents':return 403,{}
        if path=='/v1/requests/synthetic-request':
            return (403,{}) if token=='operator' else (200,{'state':'SUCCEEDED','result':evidence})
        raise AssertionError('UNEXPECTED_TEST_PATH')
    fake_feed={'providers':[
        {'id':'jira','state':'connected','complete':True,'stale':False,'items':[{'id':'KAN-1'}]},
        {'id':'github','state':'connected','complete':True,'stale':False,'items':[]}]}
    attempt={}
    with patch.object(gate,'current_feed',return_value=fake_feed), \
            patch.object(gate.secrets.SystemRandom,'sample',return_value=['jira','github']), \
            patch.object(gate,'publish',return_value=approved), \
            patch.object(gate.api,'http',side_effect=simulated_api), \
            patch.object(gate.time,'sleep'), patch('builtins.print'):
        try:
            gate.round_run(2,'operator','client',attempt)
        except AssertionError as error:
            assert str(error)=='github_actual_remote_mcp'
        else:
            raise AssertionError('LATE_FAILURE_NOT_DETECTED')
    assert attempt['passed'] is False and attempt['number']==2
    assert len(attempt['records'])==1 and attempt['records'][0]['result']==evidence
    assert attempt['records'][0]['request_id']=='synthetic-request'
    assert 'jira_canonical_answer' in attempt['checks']
    return ['early_failure_checked','unpassed_attempt_retained',
            'deterministic_checks_preserved','completed_case_preserved_after_late_failure',
            'mock_is_not_provider_approval']


if __name__=='__main__':
    root=_workspace_root
    paths=[Path(__file__),root/'app/tests/mcp/mcp_evidence_gate.py',
           root/'app/tools/ingestion/publish_mcp_evidence.py',root/'app/tests/mcp/mcp_evidence_checks.py']
    frozen={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    rounds=[run(),run()]
    assert all(hashlib.sha256((root/k).read_bytes()).hexdigest()==v for k,v in frozen.items())
    result={'card_id':'BUG-119','evidence_type':'verified_regression','complete':True,
            'consecutive_passes':2,'criteria_passed':['reproduction','two_regression_rounds'],
            'sources_sha256':frozen,'rounds':rounds,'remote_calls':0,
            'reproduction':'Jira passed in log but failed gateway receipt lacked any failed_round',
            'scope':'test harness evidence retention, not RAG integration approval'}
    target=root/'eval/runs'/('mcp-gate-regression-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
    target.write_text(json.dumps(result,indent=2))
    print(json.dumps({'complete':True,'receipt':str(target),'checks':[len(r) for r in rounds]}))
