import pytest

from proof.management_test_support import PREFIX, envelope, floor, policy


@pytest.mark.parametrize('actor', [None, 'unknown-token-that-is-at-least-thirty-two-characters'])
def test_invalid_credentials_are_rejected(management, actor):
    management.request('GET', PREFIX + '/workloads', actor=actor, status=401)


@pytest.mark.parametrize('actor', ['team-a', 'runtime', 'management'])
@pytest.mark.parametrize('method,path,body', [
    ('GET', PREFIX + '/workloads', None),
    ('POST', PREFIX + '/policies', policy()),
    ('GET', '/v1/audit?source=management', None),
    ('GET', '/openapi.json', None),
    ('GET', '/health/ready', None),
])
def test_non_actors_cannot_manage_or_inspect(management, actor, method, path, body):
    management.seed()
    management.request(method, path, body=body, actor=actor, status=403)


@pytest.mark.parametrize('actor', ['admin', 'operator', 'management', 'team-a'])
def test_snapshot_and_ack_are_runtime_capability_only(management, actor):
    api = management
    api.seed()
    api.request('GET', '/internal/v1/snapshot', actor=actor, status=403)
    api.request('POST', '/internal/v1/applied', actor=actor, body={'generation': 1, 'digest': '0' * 64}, status=403)


def test_operator_scope_role_retention_and_claimed_identity(management):
    api = management
    api.seed()
    api.request('POST', '/v1/tenants', actor='operator', body=envelope(tenant_id='other', display_name='Other'), status=403)
    api.request('POST', '/v1/tenants', body=envelope(tenant_id='other', display_name='Other'), status=201)
    other = '/v1/tenants/other'
    for path in (other + '/workloads', other + '/workloads/missing', other + '/policies', '/v1/tenants/absent/workloads'):
        api.request('GET', path, actor='operator', status=404)
    api.request('POST', other + '/policies', body=policy(tenant='other'), actor='operator', status=404)
    api.request('POST', PREFIX + '/policies', body=policy(tenant='other'), actor='operator', status=403)
    api.request('POST', PREFIX + '/policies/validate', body=policy(retain_content=True), actor='operator', status=403)
    api.request('POST', PREFIX + '/policies', body=policy('retained', retain_content=True), actor='operator', status=403)
    api.request('POST', PREFIX + '/policies', body=policy('retained', retain_content=True), status=201)
    api.request('PUT', '/v1/governance/floors/tenant/local-proof', body=floor(), actor='operator', create=True, status=403)
    api.request('PUT', '/v1/governance/floors/organization/org', body=floor(), actor='operator', create=True, status=403)
    api.request('GET', '/v1/audit?source=management', actor='operator', status=403)
    api.request('GET', '/v1/audit?source=management&tenant_id=other', actor='operator', status=404)
    own = api.request('GET', '/v1/audit?source=management&tenant_id=local-proof', actor='operator').json()
    assert own['items'] and all(item['tenant_id'] == 'local-proof' for item in own['items'])


def test_operation_and_floor_readback_are_tenant_scoped(management):
    api = management
    api.request('POST', '/v1/tenants', body=envelope(tenant_id='other', display_name='Other'), status=201)
    other = api.request('POST', '/v1/tenants/other/workloads', body=envelope(workload_id='team-b', display_name='B'), status=201).json()
    operation = '/v1/operations/' + other['operation']['operation_id']
    api.request('GET', operation, actor='operator', status=404)
    api.request('GET', operation)
    api.request('PUT', '/v1/governance/floors/tenant/other', body=floor(), create=True, status=202)
    api.request('GET', '/v1/governance/floors/tenant/other', actor='operator', status=404)


def test_content_read_requires_admin_and_is_itself_audited(management):
    api = management
    api.request('GET', '/v1/audit?source=management&tenant_id=local-proof&include_content=true', actor='operator', status=403)
    api.request('GET', '/v1/audit?source=management&include_content=true')
    events = api.request('GET', '/v1/audit?source=management').json()['items']
    reads = [item for item in events if item['event_type'] == 'audit_content_read']
    assert len(reads) == 1 and reads[0]['actor_id'] == str(api.identities[0].credential_id)
    assert all(item['content'] is None for item in events)
