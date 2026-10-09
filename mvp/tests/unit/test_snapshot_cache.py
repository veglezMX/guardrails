import asyncio
import copy
import hashlib

import httpx
import pytest

from guardrails.local_contract import ProofConfig, canonical_bytes
from guardrails.runtime.snapshots import SnapshotCache, SnapshotPoller
from guardrails.transport import ContractProblem
from proof.management_test_support import PREFIX, envelope


def redigest(value):
    value['digest'] = hashlib.sha256(canonical_bytes({k: v for k, v in value.items() if k != 'digest'})).hexdigest()
    return canonical_bytes(value)


@pytest.mark.parametrize('defect', ['digest', 'lower', 'equal_changed', 'duplicate_workload', 'credential_ref', 'credential_revision',
                                  'revoked', 'binding_ref', 'binding_generation', 'policy_capability', 'policy_digest', 'duplicate_policy', 'floor'])
def test_bad_candidates_preserve_last_good_generation_and_contact(management, defect):
    management.seed()
    raw = management.request('GET', '/internal/v1/snapshot', actor='runtime').content
    clock = [10.0]
    cache = SnapshotCache(clock=lambda: clock[0])
    original = cache.apply(raw)
    value = copy.deepcopy(original.to_wire())
    clock[0] = 20.0
    if defect == 'digest':
        value['digest'] = '0' * 64
    elif defect == 'lower':
        value['generation'] -= 1
    elif defect == 'equal_changed':
        value['workloads'][0]['display_name'] = 'Changed'
    elif defect == 'duplicate_workload':
        value['generation'] += 1
        value['workloads'] *= 2
    elif defect == 'credential_ref':
        value['credential_hashes'][0]['workload_id'] = 'missing'
    elif defect == 'credential_revision':
        value['credential_hashes'][0]['revision'] += 1
    elif defect == 'revoked':
        value['credential_hashes'][0]['revoked_at'] = value['generated_at']
    elif defect == 'binding_ref':
        value['bindings'][0]['policy_version'] = 'missing'
    elif defect == 'binding_generation':
        value['bindings'][0]['generation'] = value['generation'] + 1
    elif defect == 'policy_capability':
        value['policies'][0]['checks'][0]['evaluator'] = 'arbitrary_code'
    elif defect == 'policy_digest':
        value['policies'][0]['retain_content'] = True
    elif defect == 'duplicate_policy':
        value['policies'] *= 2
    elif defect == 'floor':
        from proof.management_test_support import floor
        value['floors'] = [dict(**{k: v for k, v in floor(required_policy_id='different').items() if k != 'schema_version'}, scope='organization', tenant_id=None, revision=1)]
    candidate = canonical_bytes(value) if defect == 'digest' else redigest(value)
    with pytest.raises(ContractProblem):
        cache.apply(candidate)
    assert cache.current is original and cache.last_contact == 10.0 and cache.refresh_failed


def test_cold_age_heartbeat_and_known_identity_revocation_lag(management):
    management.seed()
    clock = [100.0]
    cache = SnapshotCache(ProofConfig(max_snapshot_age_s=2), clock=lambda: clock[0])
    assert cache.health() == dict(ready=False, degraded=True, applied_generation=None, cache_age_s=None)
    with pytest.raises(ContractProblem) as error:
        cache.authenticate('Bearer ' + management.tokens['team-a'])
    assert error.value.status == 503
    snapshot = cache.apply(management.request('GET', '/internal/v1/snapshot', actor='runtime').content)
    clock[0] += 3
    assert cache.stale and not cache.health()['ready']
    assert cache.authenticate('Bearer ' + management.tokens['team-a']).workload_id == 'team-a'
    with pytest.raises(ContractProblem) as error:
        cache.authenticate('Bearer ' + 'unknown' * 8)
    assert error.value.status == 401
    cache.heartbeat('"' + snapshot.digest + '"')
    assert cache.age == 0 and cache.health()['ready']
    clock[0] -= 1
    assert cache.age == 0
    with pytest.raises(ContractProblem):
        cache.heartbeat('"wrong"')


def test_unbound_required_floor_overrides_deployment_fail_open(management):
    from proof.management_test_support import floor
    management.request('POST', PREFIX + '/workloads', body=envelope(workload_id='team-a', display_name='A'), status=201)
    management.request('PUT', '/v1/governance/floors/organization/org', body=floor(), create=True, status=202)
    cache = SnapshotCache(ProofConfig(unresolved_policy_behavior='allow_and_log'))
    cache.apply(management.request('GET', '/internal/v1/snapshot', actor='runtime').content)
    binding, policy, client = cache.resolve(('local-proof', 'team-a'))
    assert binding is policy is None and client.failure_behavior.policy_unresolved == 'block'


def test_acknowledgement_is_after_swap_and_retried_after_failed_receipt(management):
    management.seed()
    raw = management.request('GET', '/internal/v1/snapshot', actor='runtime').content
    cache, calls = SnapshotCache(), []
    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == 'GET':
            if cache.current:
                return httpx.Response(304, headers={'ETag': '"' + cache.current.digest + '"'})
            return httpx.Response(200, content=raw)
        assert cache.current is not None
        if len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={'applied_generation': cache.current.generation})
    async def run():
        async with httpx.AsyncClient(base_url='https://management', transport=httpx.MockTransport(handle)) as client:
            poller = SnapshotPoller(cache, client)
            await poller.refresh()
            assert cache.current and cache.acknowledged_generation is None and cache.refresh_failed
            await poller.refresh()
            assert cache.acknowledged_generation == cache.current.generation and not cache.refresh_failed
    asyncio.run(run())


def test_new_generation_cannot_rewrite_an_already_known_immutable_version(management):
    from guardrails.transport import parse_policy
    management.seed()
    cache = SnapshotCache()
    before = cache.apply(management.request('GET', '/internal/v1/snapshot', actor='runtime').content)
    candidate = before.to_wire()
    candidate['generation'] += 1
    candidate['policies'][0]['retain_content'] = True
    draft = {k: v for k, v in candidate['policies'][0].items() if k not in ('publication_ordinal', 'digest', 'published_at', 'published_by')}
    candidate['policies'][0]['digest'] = parse_policy(canonical_bytes(draft)).to_domain().content_digest()
    with pytest.raises(ContractProblem):
        cache.apply(redigest(candidate))
    assert cache.current is before
