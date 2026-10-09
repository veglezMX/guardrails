"""Durable rule tests use real SQLite and pure domain services."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from guardrails.management.identity import IdentityService
from guardrails.management.models import SnapshotModel
from guardrails.storage.control import ControlRepository
from guardrails.transport import ContractProblem, parse_model, parse_policy
from proof.management_test_support import PREFIX, envelope, floor, policy


def counts(api):
    with api.repository.transaction() as connection:
        return tuple(connection.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0] for name in
                     ('tenants', 'workloads', 'credentials', 'policies', 'bindings_current', 'binding_history', 'governance_floors', 'operations', 'management_audit'))


def test_publication_replay_ordinal_and_immutability(management):
    api = management
    body = policy()
    first = api.request('POST', PREFIX + '/policies', body=body, status=201).json()
    before = counts(api)
    replay = api.request('POST', PREFIX + '/policies', body=dict(reversed(list(body.items())))).json()
    assert replay == first and counts(api) == before
    body['checks'][0]['requested_action'] = 'redact'
    api.request('POST', PREFIX + '/policies', body=body, status=409)
    assert counts(api) == before
    second = api.request('POST', PREFIX + '/policies', body=policy('older-looking-id'), status=201).json()
    assert second['policy']['publication_ordinal'] == 2
    domain = parse_policy(json.dumps(policy()).encode()).to_domain()
    with pytest.raises(FrozenInstanceError):
        domain.checks[0].requested_action = 'log_only'


@pytest.mark.parametrize('table', ['policies', 'binding_history', 'management_audit'])
@pytest.mark.parametrize('mutation', ['UPDATE {table} SET document=document', 'DELETE FROM {table}'])
def test_immutable_rows_are_database_enforced(management, table, mutation):
    management.seed()
    with sqlite3.connect(management.repository.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(mutation.format(table=table))


@pytest.mark.parametrize('failure_table', ['management_audit', 'operations', 'binding_history'])
def test_mutations_roll_back_all_resources_and_generation(management, failure_table):
    api = management
    seeded = api.seed()
    before = counts(api)
    with sqlite3.connect(api.repository.path) as connection:
        connection.execute(f"CREATE TRIGGER injected_failure BEFORE INSERT ON {failure_table} BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    response = api.request('POST', PREFIX + '/workloads/team-a/binding/state',
                           body=envelope(enforcement_state='block', reason='synthetic_promotion', evidence_kind='synthetic_local_proof', fixture_run_id='11111111-1111-4111-8111-111111111111'),
                           revision=1, status=503)
    assert counts(api) == before
    assert api.request('GET', PREFIX + '/workloads/team-a/binding').json()['desired'] == seeded['resource']
    if failure_table == 'management_audit':
        assert response.json()['code'] == 'audit_write_failed'


def test_concurrent_writes_cannot_overwrite_revision(management):
    api = management
    api.seed()
    from guardrails.management.models import BindingWrite
    body = parse_model(json.dumps(envelope(policy_id='pii', policy_version='v1', reason='policy_update')).encode(), BindingWrite)
    service = api.app.state.management.bindings
    def write():
        try:
            return service.change(api.identities[0], 'local-proof', 'team-a', body, 1, 'set')['resource']['revision']
        except ContractProblem as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: write(), range(2)))
    # The bounded SQLite writer may reject contention before it can check the
    # revision. Either rejection must leave precisely one committed revision.
    assert outcomes.count(2) == 1 and next(item for item in outcomes if item != 2) in (409, 503)
    assert write() == 409  # Once the writer is free, the stale precondition is explicit.
    assert len(api.request('GET', PREFIX + '/workloads/team-a/binding/history').json()['items']) == 2


def test_rotation_revokes_old_token_and_snapshot_has_only_active_workload_hashes(management):
    api = management
    api.seed()
    old = api.tokens['team-a']
    result = api.request('POST', PREFIX + '/workloads/team-a/credentials/rotate', body=envelope(reason='credential_rotation'), revision=1, status=201).json()
    identity = IdentityService(api.repository)
    with pytest.raises(ContractProblem) as error:
        identity.authenticate('Bearer ' + old)
    assert error.value.status == 401
    assert identity.authenticate('Bearer ' + result['token']).revision == 2
    snapshot = api.request('GET', '/internal/v1/snapshot', actor='runtime').json()
    domain = parse_model(json.dumps(snapshot).encode(), SnapshotModel).to_domain()
    assert len(domain.credential_hashes) == 1 and domain.credential_hashes[0].revision == 2
    assert all(item.role == 'workload' for item in domain.credential_hashes)
    contents = api.repository.path.read_bytes()
    assert all(token.encode() not in contents for token in (*api.tokens.values(), result['token']))
    assert not any(token in json.dumps(snapshot) for token in api.tokens.values())


@pytest.mark.parametrize('weakness', ['state', 'remove', 'type', 'stage', 'action', 'policy_id', 'failure', 'retention', 'ordinal'])
def test_floor_blocks_each_weakening_and_keeps_history(management, weakness):
    api = management
    api.seed()
    strong = floor(minimum_policy_ordinals={'pii': 1})
    api.request('PUT', '/v1/governance/floors/organization/org', body=strong, create=True, status=202)
    path = PREFIX + '/workloads/team-a/binding'
    if weakness == 'state':
        body = envelope(enforcement_state='disabled', reason='synthetic_demotion', evidence_kind='synthetic_local_proof', fixture_run_id='11111111-1111-4111-8111-111111111111')
        method, target = 'POST', path + '/state'
    elif weakness == 'remove':
        body, method, target = envelope(reason='binding_removal'), 'DELETE', path
    elif weakness == 'ordinal':
        body, method, target = floor(minimum_policy_ordinals={'pii': 2}), 'PUT', '/v1/governance/floors/organization/org'
    else:
        draft = policy('v2')
        if weakness == 'type':
            draft['checks'][0]['pii_types'] = ['email']
        elif weakness == 'stage':
            draft['checks'][0]['stages'] = ['input']
        elif weakness == 'action':
            # Raise current floor to block; log_only must then fail.
            api.request('PUT', '/v1/governance/floors/organization/org', body=floor(minimum_action='block'), revision=1, status=202)
            draft['checks'][0]['requested_action'] = 'log_only'
        elif weakness == 'policy_id':
            draft['policy_id'] = 'escape'
        elif weakness == 'failure':
            draft['failure_behavior']['transformation_failure'] = 'allow_and_log'
        elif weakness == 'retention':
            draft['retain_content'] = True
        api.request('POST', PREFIX + '/policies', body=draft, status=201)
        body = envelope(policy_id=draft['policy_id'], policy_version='v2', reason='policy_update')
        method, target = 'PUT', path
    before = counts(api)
    result = api.request(method, target, body=body, revision=1, status=409)
    assert result.json()['code'] == 'mandatory_floor_violation'
    assert counts(api) == before


def test_floor_requires_strong_stage_type_pairs_and_rejects_conflicting_ids(management):
    api = management
    draft = policy()
    draft['checks'] = [dict(check_id='strong', evaluator='local_pii', stages=['input'], pii_types=['email'], requested_action='block'),
                       dict(check_id='weak', evaluator='local_pii', stages=['output'], pii_types=['phone'], requested_action='log_only')]
    api.request('POST', PREFIX + '/workloads', body=envelope(workload_id='team-a', display_name='A'), status=201)
    api.request('POST', PREFIX + '/policies', body=draft, status=201)
    api.request('PUT', PREFIX + '/workloads/team-a/binding', body=envelope(policy_id='pii', policy_version='v1', reason='initial_binding'), create=True, status=202)
    api.request('PUT', '/v1/governance/floors/organization/org', body=floor(minimum_action='block'), create=True, status=409)
    api.request('PUT', '/v1/governance/floors/organization/org', body=floor(required_stages=[], required_pii_types=[]), create=True, status=202)
    api.request('PUT', '/v1/governance/floors/tenant/local-proof', body=floor(required_policy_id='different'), create=True, status=409)


def test_required_binding_allows_inactive_registration_but_governs_initial_binding(management):
    api = management
    api.request('PUT', '/v1/governance/floors/organization/org', body=floor(), create=True, status=202)
    result = api.seed()
    assert result['resource']['enforcement_state'] == 'observe'


def test_unknown_future_schema_is_rejected_without_rewriting(tmp_path):
    path = tmp_path / 'db/control.sqlite3'
    path.parent.mkdir()
    with sqlite3.connect(path) as connection:
        connection.executescript('CREATE TABLE schema_migrations (version INTEGER); INSERT INTO schema_migrations VALUES (2);')
    original = path.read_bytes()
    with pytest.raises(ContractProblem) as error:
        ControlRepository(path)
    assert error.value.status == 503 and path.read_bytes() == original


def test_snapshot_digest_stable_on_read_and_replay_but_advances_on_change(management):
    api = management
    api.seed()
    one = api.request('GET', '/internal/v1/snapshot', actor='runtime')
    two = api.request('GET', '/internal/v1/snapshot', actor='runtime')
    assert one.content == two.content
    api.request('GET', '/internal/v1/snapshot', actor='runtime', headers={'If-None-Match': one.headers['etag']}, status=304)
    api.request('POST', PREFIX + '/policies', body=policy())
    assert api.request('GET', '/internal/v1/snapshot', actor='runtime').content == one.content
    api.request('POST', PREFIX + '/policies', body=policy('v2'), status=201)
    three = api.request('GET', '/internal/v1/snapshot', actor='runtime').json()
    assert three['generation'] > one.json()['generation'] and three['digest'] != one.json()['digest']


def test_failed_ack_audit_cannot_mark_operations_applied(management):
    api = management
    seeded = api.seed()
    snapshot = api.request('GET', '/internal/v1/snapshot', actor='runtime').json()
    with sqlite3.connect(api.repository.path) as connection:
        connection.execute("CREATE TRIGGER fail_receipt_audit BEFORE INSERT ON management_audit BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    api.request('POST', '/internal/v1/applied', actor='runtime', body={k: snapshot[k] for k in ('generation', 'digest')}, status=503)
    op = api.request('GET', '/v1/operations/' + seeded['operation']['operation_id']).json()
    assert op['status'] == 'pending' and op['applied_generation'] == 0


def test_failed_rotation_does_not_revoke_or_advance_credential(management):
    api = management
    api.seed()
    before = counts(api)
    with sqlite3.connect(api.repository.path) as connection:
        connection.execute("CREATE TRIGGER fail_rotation_audit BEFORE INSERT ON management_audit BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    api.request('POST', PREFIX + '/workloads/team-a/credentials/rotate', body=envelope(reason='credential_rotation'), revision=1, status=503)
    assert counts(api) == before
    assert IdentityService(api.repository).authenticate('Bearer ' + api.tokens['team-a']).revision == 1


def test_case_inventory_and_skipped_checks_fail_the_gate():
    from types import SimpleNamespace
    from proof.test_profiles import GatePlugin
    plugin = GatePlugin(['mvp/tests/unit/test_policy_integrity.py'])
    with pytest.raises(pytest.UsageError):
        plugin.pytest_collection_modifyitems(None, None, [])
    plugin.collected = plugin.required_files.copy()
    plugin.pytest_runtest_logreport(SimpleNamespace(skipped=True))
    session = SimpleNamespace(exitstatus=0)
    plugin.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1
