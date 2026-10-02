"""The published schema is a contract, not merely a page with HTTP 200."""
import json
from uuid import uuid4
import httpx2
from jsonschema import Draft202012Validator

API='http://api:8080'

def test_swagger_has_no_external_runtime_assets():
    with httpx2.Client(timeout=8,trust_env=False) as client:
        html=client.get(API+'/docs').text
        assert 'https://' not in html
        assert 'validatorUrl' in html and 'null' in html
        for name in ('swagger-ui-bundle.js','swagger-ui.css','favicon-32x32.png'):
            response=client.get(API+'/static/docs/'+name)
            assert response.status_code==200 and len(response.content)>100

def test_request_schema_documents_the_actual_constraints():
    with httpx2.Client(timeout=8,trust_env=False) as client:
        schema=client.get(API+'/openapi.json').json()['components']['schemas']
    request=schema['AppointmentRequest']['properties']
    assert request['request_id']['format']=='uuid'
    assert request['exam_codes']['items']['pattern']==r'^FICT-[0-9]{3}$'
    assert request['exam_codes']['uniqueItems'] is True
    assert schema['AppointmentReceipt']['properties']['appointment_id']['format']=='uuid'

def test_published_example_can_be_executed_without_guessing_catalog_version():
    with httpx2.Client(timeout=8,trust_env=False) as client:
        schema=client.get(API+'/openapi.json').json()
        body=schema['paths']['/appointments']['post']['requestBody']['content']['application/json']['example']
        assert body['exam_codes']==['FICT-001','FICT-002','FICT-005']
        first=client.post(API+'/appointments',json=body)
        assert first.status_code in (200,201)
        second=client.post(API+'/appointments',json=body)
        assert second.status_code==200 and second.json()==first.json()

def test_documented_422_matches_both_validation_and_catalog_errors():
    with httpx2.Client(timeout=8,trust_env=False) as client:
        schema=client.get(API+'/openapi.json').json()
        actual=schema['paths']['/appointments']['post']['responses']['422']['content']['application/json']['schema']
        assert 'HTTPValidationError' not in json.dumps(actual)
        validator=Draft202012Validator({**actual,'components':schema['components']})
        example=schema['paths']['/appointments']['post']['requestBody']['content']['application/json']['example']
        for body in ({**example,'request_id':'not-a-uuid'}, {**example,'request_id':str(uuid4()),'exam_codes':['FICT-999']}):
            reply=client.post(API+'/appointments',json=body)
            assert reply.status_code==422
            validator.validate(reply.json())

def test_each_error_status_advertises_its_actual_literal_code():
    with httpx2.Client(timeout=8,trust_env=False) as client:
        schema=client.get(API+'/openapi.json').json()
    routes=schema['paths']
    expected={'400':('code','INVALID_JSON_ENVELOPE'), '408':('code','REQUEST_BODY_TIMEOUT'),
        '409':('detail','IDEMPOTENCY_CONFLICT'), '413':('code','REQUEST_SIZE_LIMIT'),
        '415':('code','JSON_REQUIRED'), '503':('detail','LEDGER_UNAVAILABLE_RETRY_SAME_KEY')}
    for status,(field,code) in expected.items():
        ref=routes['/appointments']['post']['responses'][status]['content']['application/json']['schema']['$ref']
        assert schema['components']['schemas'][ref.split('/')[-1]]['properties'][field]['const']==code
    for route in ('/appointments/{appointment_id}','/appointments/by-request/{request_id}'):
        ref=routes[route]['get']['responses']['404']['content']['application/json']['schema']['$ref']
        assert schema['components']['schemas'][ref.split('/')[-1]]['properties']['detail']['const']=='NOT_FOUND'
