"""Direct lifecycle through an actual TLS server and durable control database."""
import json
import ssl
from uuid import uuid4

import httpx
import pytest

from proof.management_test_support import PREFIX, envelope, floor, policy


def test_real_https_lifecycle_history_update_rollback_and_pending_operations(https_management):
    api = https_management.api
    api.request('POST', '/v1/tenants', body=envelope(tenant_id='team-tenant', display_name='Synthetic tenant'), status=201)
    validated = api.request('POST', PREFIX + '/policies/validate', body=policy(), actor='operator').json()
    assert validated['valid'] is True
    seeded = api.seed()
    binding_path = PREFIX + '/workloads/team-a/binding'
    assert seeded['operation']['status'] == 'pending'
    assert seeded['resource']['enforcement_state'] == 'observe'
    assert api.request('GET', PREFIX + '/workloads', actor='operator').json()['items'][0]['workload_id'] == 'team-a'
    published = api.request('POST', PREFIX + '/policies', body=policy()).json()
    assert published['policy']['digest'] == validated['canonical_digest']
    changed = policy(retain_content=True)
    api.request('POST', PREFIX + '/policies', body=changed, status=409)
    revision = 1
    for state in ('warn', 'redact', 'block', 'disabled', 'observe'):
        reason = 'synthetic_demotion' if state == 'disabled' else 'synthetic_promotion'
        result = api.request('POST', binding_path + '/state', body=envelope(enforcement_state=state, reason=reason,
                             evidence_kind='synthetic_local_proof', fixture_run_id=str(uuid4())), revision=revision, status=202).json()
        revision += 1
        assert result['resource']['enforcement_state'] == state and result['resource']['revision'] == revision
    new = api.request('POST', PREFIX + '/policies', body=policy('v2'), actor='operator', status=201).json()
    assert new['policy']['publication_ordinal'] == 2
    updated = api.request('PUT', binding_path, body=envelope(policy_id='pii', policy_version='v2', reason='policy_update', input_timeout_ms=1700, output_timeout_ms=1800), revision=revision, status=202).json()
    assert updated['resource']['enforcement_state'] == 'observe'
    rollback = api.request('POST', binding_path + '/rollback', body=envelope(target_revision=1, reason='rollback'), revision=revision + 1, status=202).json()
    assert rollback['resource']['policy_version'] == 'v1' and rollback['resource']['input_timeout_ms'] == 1500
    assert rollback['resource']['generation'] > updated['resource']['generation']
    history = api.request('GET', binding_path + '/history').json()['items']
    assert [item['revision'] for item in history] == list(range(1, revision + 3))
    assert all(item['updated_by'] for item in history)
    audit = api.request('GET', '/v1/audit?source=management&tenant_id=local-proof&workload_id=team-a').json()
    assert audit['complete'] and any(item['event_type'] == 'binding_rollback' for item in audit['items'])
    op = api.request('GET', '/v1/operations/' + rollback['operation']['operation_id']).json()
    assert op['status'] == 'pending' and op['applied_generation'] == 0
    assert api.request('GET', binding_path).json()['applied_revision'] is None


def test_real_snapshot_etag_authenticated_test_receipt_and_later_pending_write(https_management):
    api = https_management.api
    binding = api.seed()
    snapshot = api.request('GET', '/internal/v1/snapshot', actor='runtime')
    value = snapshot.json()
    api.request('GET', '/internal/v1/snapshot', actor='runtime', headers={'If-None-Match': snapshot.headers['etag']}, status=304)
    receipt = {k: value[k] for k in ('generation', 'digest')}
    api.request('POST', '/internal/v1/applied', actor='admin', body=receipt, status=403)
    api.request('POST', '/internal/v1/applied', actor='runtime', body=receipt | {'digest': '0' * 64}, status=409)
    api.request('POST', '/internal/v1/applied', actor='runtime', body=receipt)
    assert api.request('GET', '/v1/operations/' + binding['operation']['operation_id']).json()['status'] == 'applied'
    result = api.request('POST', PREFIX + '/policies', body=policy('v2'), status=201).json()
    assert result['operation']['status'] == 'pending'
    assert result['operation']['applied_generation'] == value['generation']


def test_real_floor_blocks_rollback_below_current_minimum(https_management):
    api = https_management.api
    api.seed()
    path = PREFIX + '/workloads/team-a/binding'
    api.request('POST', PREFIX + '/policies', body=policy('v2'), status=201)
    api.request('PUT', path, body=envelope(policy_id='pii', policy_version='v2', reason='policy_update'), revision=1, status=202)
    api.request('PUT', '/v1/governance/floors/organization/org', body=floor(minimum_policy_ordinals={'pii': 2}), create=True, status=202)
    api.request('POST', path + '/rollback', body=envelope(target_revision=1, reason='rollback'), revision=2, status=409)
    api.request('DELETE', path, body=envelope(reason='binding_removal'), revision=2, status=409)
    assert len(api.request('GET', path + '/history').json()['items']) == 2
    assert api.request('GET', path).json()['desired']['policy_version'] == 'v2'


def test_real_rotation_lost_issuance_recovery_scope_and_privacy(https_management):
    api = https_management.api
    api.seed()
    old_token = api.tokens['team-a']
    revision = api.request('GET', PREFIX + '/workloads/team-a').json()['credential_revision']
    rotated = api.request('POST', PREFIX + '/workloads/team-a/credentials/rotate', body=envelope(reason='credential_rotation'), revision=revision, status=201).json()
    api.request('GET', PREFIX + '/workloads', actor=old_token, status=401)
    api.request('GET', PREFIX + '/workloads', actor=rotated['token'], status=403)
    api.request('GET', '/v1/tenants/other/workloads', actor='operator', status=404)
    snapshot = api.request('GET', '/internal/v1/snapshot', actor='runtime').json()
    assert snapshot['credential_hashes'][0]['revision'] == 2
    audit = api.request('GET', '/v1/audit?source=management').json()
    assert all(item['content'] is None for item in audit['items'])
    for path in https_management.root.rglob('*'):
        if path.is_file():
            data = path.read_bytes()
            assert all(token.encode() not in data for token in (*api.tokens.values(), rotated['token']))


def test_real_tls_rejects_untrusted_peer(https_management):
    with httpx.Client(verify=ssl.create_default_context(), trust_env=False) as client:
        with pytest.raises(httpx.ConnectError) as rejected:
            client.get(https_management.url + '/health/live')
        assert isinstance(rejected.value, httpx.ConnectError)


def test_real_binding_removal_recreation_preserves_history_and_applied_view(https_management):
    api = https_management.api
    api.seed()
    path = PREFIX + '/workloads/team-a/binding'
    snapshot = api.request('GET', '/internal/v1/snapshot', actor='runtime').json()
    api.request('POST', '/internal/v1/applied', actor='runtime', body={k: snapshot[k] for k in ('generation', 'digest')})
    api.request('DELETE', path, body=envelope(reason='binding_removal'), revision=1, status=202)
    api.request('GET', path, status=404)
    created = api.request('PUT', path, body=envelope(policy_id='pii', policy_version='v1', reason='initial_binding'), create=True, status=202).json()
    assert created['resource']['revision'] == 2
    api.request('PUT', path, body=envelope(policy_id='pii', policy_version='v1', reason='policy_update'), revision=1, status=409)
    assert api.request('GET', path).json()['applied_revision'] == 1
    assert len(api.request('GET', path + '/history').json()['items']) == 2
