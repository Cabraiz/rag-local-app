"""Adversarial cases selected independently of positive fixture values."""
import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4
import pytest
from clinic_adk.catalog import Catalog
from clinic_adk.compiler import parse_spec
from clinic_adk.errors import SafeError
from clinic_adk.privacy import sanitize_ocr
from clinic_adk.runtime import Runtime, decode_tool

@pytest.mark.parametrize('version',[True,1.0,'1',None])
def test_version_is_integer_not_truthy(version):
    spec=json.loads(Path('/app/examples/agent.json').read_text())
    spec['schema_version']=version
    with pytest.raises(SafeError):
        parse_spec(json.dumps(spec).encode())

@pytest.mark.parametrize('text',['CLINICA exame desconhecido','PEDIDO desconhecido','DADOS FICTICIO desconhecido'])
def test_exam_cannot_hide_in_header_allowlist(text):
    with pytest.raises(SafeError):
        sanitize_ocr('Exame: Hemograma completo\nExame: '+text,Catalog())

@pytest.mark.parametrize('value',[{'content':None},{'content':'garbage'},{'content':[None]},
    {'content':[{'type':'text','text':None}]},{'structuredContent':{'ok':True,'value':object()}}])
def test_untrusted_result_shape_is_always_safe_error(value):
    with pytest.raises(SafeError):
        decode_tool(value)

def test_ambiguous_catalog_cannot_be_loaded(tmp_path):
    data=json.loads(Path('/app/data/exams.json').read_text())
    data['exams'][1]['aliases'].append(data['exams'][0]['name'])
    path=tmp_path/'ambiguous.json'
    path.write_text(json.dumps(data))
    with pytest.raises(SafeError) as error:
        Catalog(path)
    assert 'AMBIGUOUS' in str(error.value)

@pytest.mark.parametrize('codes',[[],['FICT-001','FICT-001'],['FICT-999'],[1],[True],'FICT-001'])
def test_api_rejects_invalid_codes_without_persistence(codes):
    from fastapi.testclient import TestClient
    from clinic_adk.api import app
    request_id=str(uuid4())
    with TestClient(app) as client:
        response=client.post('/appointments',json={'request_id':request_id,'exam_codes':codes,'catalog_version':Catalog().version})
        assert response.status_code==422
        assert client.get('/appointments/by-request/'+request_id).status_code==404

def test_api_rejects_oversized_body_before_json_processing():
    from fastapi.testclient import TestClient
    from clinic_adk.api import app
    with TestClient(app) as client:
        result=client.post('/appointments',content=b'x'*100000,headers={'content-type':'application/json'})
        assert result.status_code==413 and len(result.content)<500

def test_api_rejects_duplicate_fields():
    from fastapi.testclient import TestClient
    from clinic_adk.api import app
    with TestClient(app) as client:
        result=client.post('/appointments',content=b'{"request_id":"x","request_id":"y"}',headers={'content-type':'application/json'})
        assert result.status_code==400
        assert result.json()['code']=='INVALID_JSON_ENVELOPE'

def test_bad_receipt_and_two_timeouts_are_not_success(monkeypatch):
    import httpx2
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs):
            raise httpx2.ReadTimeout('PRIVATE_SENTINEL_NEVER_ECHO')
    monkeypatch.setattr(httpx2,'AsyncClient',lambda **kwargs:Client())
    runtime=Runtime('request.png')
    with pytest.raises(SafeError) as error:
        asyncio.run(runtime.book({'request_id':runtime.request_id,'exam_codes':['FICT-001'],'catalog_version':runtime.catalog.version}))
    assert str(error.value)=='APPOINTMENT_OUTCOME_UNKNOWN_RETRY_SAME_KEY'

