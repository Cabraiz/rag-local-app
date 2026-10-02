"""A MIME prefix is not the application/json media type promised by OpenAPI."""
from uuid import uuid4
import httpx2
import pytest

@pytest.mark.parametrize('media_type,accepted', [
    ('application/jsonp',False), ('application/jsonp+json',False),
    ('application/json-other',False), ('application/vnd.api+json',False),
    ('application/json; charset=utf-8',True), ('Application/JSON; Charset=utf-8',True),
])
def test_only_declared_json_media_type_is_accepted(media_type,accepted):
    with httpx2.Client(timeout=8,trust_env=False) as client:
        schema=client.get('http://api:8080/openapi.json').json()
        example=schema['paths']['/appointments']['post']['requestBody']['content']['application/json']['example']
        body={**example,'request_id':str(uuid4())}
        reply=client.post('http://api:8080/appointments',json=body,headers={'Content-Type':media_type})
        assert reply.status_code==(201 if accepted else 415)
        lookup=client.get('http://api:8080/appointments/by-request/'+body['request_id'])
        assert lookup.status_code==(200 if accepted else 404)
        if not accepted:
            assert reply.json()=={'code':'JSON_REQUIRED'}
