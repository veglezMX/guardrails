import json
import pytest
from connector.gateway_app import validate_request, BoundaryError

@pytest.mark.parametrize('field,value,status', [
    ('stream',True,400), ('n',2,400), ('n',True,422), ('temperature',1,400), ('max_tokens',257,400),
    ('tools',[],400), ('functions',[],400), ('response_format',{},400), ('guardrails',[],403),
    ('metadata',{},403), ('api_key','synthetic',403), ('policy_id','other',403), ('workload_id','other',403),
    ('model','openai/direct',400), ('messages',[{'role':'tool','content':'x'}],400),
    ('messages',[{'role':'user','content':[]}],400), ('messages',[{'role':'user','content':'x','name':'x'}],400),
])
def test_unsupported_or_override_surface_rejects(field, value, status):
    body = {'model':'proof-a','messages':[{'role':'user','content':'hello'}],field:value}
    with pytest.raises(BoundaryError) as error:
        validate_request(json.dumps(body).encode(), 'proof-a')
    assert error.value.status == status


def test_every_role_and_empty_text_keeps_exact_mapping():
    messages = [{'role':role,'content':text} for role,text in zip(('system','developer','assistant','user'), ('system@example.test','+15551234567','','user@example.test'))]
    body = {'model':'proof-a','messages':messages,'n':1,'stream':False,'max_tokens':256,'temperature':0}
    assert validate_request(json.dumps(body).encode(), 'proof-a')['messages'] == messages
