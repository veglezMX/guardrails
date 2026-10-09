"""Management wire contracts, status codes and distribution semantics."""
import json
from uuid import uuid4

import pytest

from proof.management_test_support import PREFIX, envelope, floor, policy


@pytest.mark.parametrize('value', ['false', 0, 1, None])
def test_retention_is_strict_boolean(management, value):
    management.request('POST', PREFIX + '/policies', body=policy(retain_content=value), status=422)


@pytest.mark.parametrize('change', ['unknown', 'computed', 'unknown_evaluator', 'reserved_stage', 'duplicate_check', 'duplicate_stage', 'missing_failure', 'arbitrary_regex'])
def test_publication_rejects_unsupported_and_unknown_fields(management, change):
    body = policy()
    if change == 'unknown':
        body['private-field'] = 'PRIVATE-CONTENT'
    elif change == 'computed':
        body['publication_ordinal'] = 7
    elif change == 'unknown_evaluator':
        body['checks'][0]['evaluator'] = 'bedrock'
    elif change == 'reserved_stage':
        body['checks'][0]['stages'] = ['tool_call']
    elif change == 'duplicate_check':
        body['checks'] *= 2
    elif change == 'duplicate_stage':
        body['checks'][0]['stages'] = ['input', 'input']
    elif change == 'missing_failure':
        del body['failure_behavior']['audit_failure']
    else:
        body['checks'][0]['regex'] = 'PRIVATE-CONTENT'
    result = management.request('POST', PREFIX + '/policies', body=body, status=422)
    assert 'PRIVATE-CONTENT' not in result.text and 'private-field' not in result.text


@pytest.mark.parametrize('raw,status', [(b'{"schema_version":"local-proof-v1","retain_content":false,"retain_content":true}', 400),
                                       (b'{"secret":NaN}', 400), (b'{"secret":"\\ud800"}', 400), (b'{', 400)])
def test_raw_json_boundary_rejects_duplicates_nonfinite_and_invalid_unicode(management, raw, status):
    result = management.request('POST', PREFIX + '/policies', content=raw, status=status)
    assert 'secret' not in result.text


def test_body_limit_before_parsing_and_safe_correlation(management):
    correlation = str(uuid4())
    result = management.request('POST', PREFIX + '/policies', content=b'x' * (8388608 + 1), status=413,
                                headers={'X-Correlation-ID': correlation})
    assert result.json()['correlation_id'] == correlation
    result = management.request('POST', PREFIX + '/policies', body=policy(), status=422, headers={'X-Correlation-ID': 'PRIVATE-CONTENT'})
    assert 'PRIVATE-CONTENT' not in result.text


@pytest.mark.parametrize('timeout', [True, '1500', 749, 5001])
def test_binding_timeout_strict_range(management, timeout):
    management.seed()
    management.request('PUT', PREFIX + '/workloads/team-a/binding', body=envelope(policy_id='pii', policy_version='v1', reason='policy_update', input_timeout_ms=timeout), revision=1, status=422)


def test_preconditions_initial_observe_and_state_evidence(management):
    api = management
    api.request('POST', PREFIX + '/workloads', body=envelope(workload_id='team-a', display_name='A'), status=201)
    api.request('POST', PREFIX + '/policies', body=policy(), status=201)
    path = PREFIX + '/workloads/team-a/binding'
    body = envelope(policy_id='pii', policy_version='v1', reason='initial_binding')
    api.request('PUT', path, body=body, status=428)
    api.request('PUT', path, body=body | {'enforcement_state': 'block'}, create=True, status=422)
    first = api.request('PUT', path, body=body, create=True, status=202).json()
    assert first['resource']['enforcement_state'] == 'observe' and first['operation']['status'] == 'pending'
    api.request('PUT', path, body=body, create=True, status=409)
    api.request('PUT', path, body=body | {'reason': 'policy_update'}, revision=2, status=409)
    api.request('PUT', path, body=body | {'reason': 'policy_update'}, headers={'If-Match': 'true'}, status=422)
    api.request('PUT', path, body=body | {'reason': 'policy_update'}, revision=1, create=True, status=422)
    state = envelope(enforcement_state='block', reason='synthetic_promotion', evidence_kind='synthetic_local_proof', fixture_run_id=str(uuid4()))
    api.request('POST', path + '/state', body={k: v for k, v in state.items() if k != 'fixture_run_id'}, revision=1, status=422)
    api.request('POST', path + '/state', body=state | {'reason': 'PRIVATE-CONTENT'}, revision=1, status=422)
    api.request('POST', path + '/state', body=state, status=428)
    api.request('POST', path + '/state', body=state, headers={'If-Match': '"1"'}, status=202)
    api.request('PUT', path, body=body | {'reason': 'policy_update'}, revision=1, status=409)


def test_acknowledgement_requires_exact_existing_digest_and_never_regresses(management):
    api = management
    seeded = api.seed()
    operation = seeded['operation']
    old = api.request('GET', '/internal/v1/snapshot', actor='runtime').json()
    api.request('POST', PREFIX + '/policies', body=policy('v2'), status=201)
    newest = api.request('GET', '/internal/v1/snapshot', actor='runtime').json()
    for body in ({'generation': newest['generation'] + 1, 'digest': newest['digest']},
                 {'generation': newest['generation'], 'digest': '0' * 64},
                 {'generation': old['generation'], 'digest': newest['digest']}):
        api.request('POST', '/internal/v1/applied', actor='runtime', body=body, status=409)
    assert api.request('GET', '/v1/operations/' + operation['operation_id']).json()['status'] == 'pending'
    receipt = {k: newest[k] for k in ('generation', 'digest')}
    api.request('POST', '/internal/v1/applied', actor='runtime', body=receipt)
    api.request('POST', '/internal/v1/applied', actor='runtime', body=receipt)
    api.request('POST', '/internal/v1/applied', actor='runtime', body={k: old[k] for k in ('generation', 'digest')}, status=409)
    applied = api.request('GET', '/v1/operations/' + operation['operation_id']).json()
    assert applied['status'] == 'applied' and applied['applied_at'] is not None
    binding = api.request('GET', PREFIX + '/workloads/team-a/binding').json()
    assert binding['applied_revision'] == 1 and binding['cache_age_s'] is None


def test_audit_partial_sources_never_masquerade_as_complete(management):
    api = management
    api.seed()
    failed = api.request('GET', '/v1/audit', status=503).json()
    assert failed['complete'] is False and failed['source_status'] == {'management': 'available', 'runtime': 'unavailable'}
    partial = api.request('GET', '/v1/audit?allow_partial=true').json()
    assert partial['items'] and partial['complete'] is False
    complete = api.request('GET', '/v1/audit?source=management').json()
    assert complete['complete'] is True and complete['source_status'] == {'management': 'available'}
    runtime = api.request('GET', '/v1/audit?source=connector&allow_partial=true').json()
    assert runtime['items'] == [] and runtime['complete'] is False
    api.request('GET', '/runtime-openapi.json', status=503)


def test_pagination_order_scope_filters_and_policy_summaries(management):
    api = management
    api.seed()
    for version in ('v2', 'v3'):
        api.request('POST', PREFIX + '/policies', body=policy(version), status=201)
    result, versions = None, []
    url = PREFIX + '/policies?limit=1'
    for _ in range(3):
        result = api.request('GET', url).json()
        assert len(result['items']) == 1 and 'checks' not in result['items'][0] and 'failure_behavior' not in result['items'][0]
        versions.append(result['items'][0]['version'])
        cursor = result['next_cursor']
        if cursor:
            api.request('GET', PREFIX + '/workloads?cursor=' + cursor, status=422)
            url = PREFIX + '/policies?limit=1&cursor=' + cursor
    assert versions == ['v1', 'v2', 'v3'] and result['next_cursor'] is None
    correlation = str(uuid4())
    api.request('POST', PREFIX + '/policies', body=policy('v4'), status=201, headers={'X-Correlation-ID': correlation})
    events = api.request('GET', '/v1/audit?source=management&correlation_id=' + correlation).json()['items']
    assert len(events) == 1 and events[0]['event_type'] == 'policy_published'


@pytest.mark.parametrize('query', ['limit=0', 'limit=true', 'limit=201', 'limit=1&limit=2', 'unknown=private', 'allow_partial=1', 'source=unknown', 'correlation_id=private', 'cursor=garbage'])
def test_query_validation(management, query):
    management.request('GET', '/v1/audit?' + query + ('&source=management' if 'source=' not in query else ''), status=422)


def test_management_schema_projects_real_models_and_protects_documentation(management):
    from guardrails.management.app import ROUTES, create_app
    api = management
    actual = api.request('GET', '/openapi.json').json()
    assert actual == create_app(schema_only=True).openapi()
    for method, path, _, model, _, _ in ROUTES:
        operation = actual['paths'][path][method.lower()]
        assert operation['security'] == [{'bearerAuth': []}]
        if model:
            assert actual['components']['schemas'][model.__name__]['additionalProperties'] is False
    api.request('GET', '/docs', status=404)
    api.request('GET', '/redoc', status=404)


def test_floor_and_rotation_preconditions(management):
    api = management
    api.seed()
    api.request('POST', PREFIX + '/workloads/team-a/credentials/rotate', body=envelope(reason='credential_rotation'), status=428)
    api.request('POST', PREFIX + '/workloads/team-a/credentials/rotate', body=envelope(reason='credential_rotation'), revision=2, status=409)
    api.request('PUT', '/v1/governance/floors/organization/org', body=floor(), status=428)
    api.request('PUT', '/v1/governance/floors/organization/org', body=floor(minimum_state='block'), create=True, status=422)
    api.request('PUT', '/v1/governance/floors/organization/org', body=floor(), create=True, status=202)
    api.request('PUT', '/v1/governance/floors/organization/org', body=floor(), revision=2, status=409)


def test_management_audit_read_failure_identifies_its_unavailable_source(management):
    import sqlite3
    with sqlite3.connect(management.repository.path) as connection:
        connection.execute('DROP TABLE management_audit')
    result = management.request('GET', '/v1/audit?source=management', status=503).json()
    assert result['complete'] is False and result['source_status'] == {'management': 'unavailable'}
    partial = management.request('GET', '/v1/audit?source=management&allow_partial=true').json()
    assert partial['items'] == [] and partial['complete'] is False


def test_rollback_restores_historical_state_timeouts_and_records_previous_and_new(management):
    api = management
    api.seed()
    path = PREFIX + '/workloads/team-a/binding'
    state = envelope(enforcement_state='block', reason='synthetic_promotion', evidence_kind='synthetic_local_proof', fixture_run_id=str(uuid4()))
    api.request('POST', path + '/state', body=state, revision=1, status=202)
    api.request('PUT', path, body=envelope(policy_id='pii', policy_version='v1', reason='policy_update', input_timeout_ms=1800, output_timeout_ms=1900), revision=2, status=202)
    api.request('POST', path + '/state', body=state | {'enforcement_state': 'observe', 'reason': 'synthetic_demotion'}, revision=3, status=202)
    result = api.request('POST', path + '/rollback', body=envelope(target_revision=3, reason='rollback'), revision=4, status=202).json()
    assert result['resource']['enforcement_state'] == 'block'
    assert result['resource']['input_timeout_ms'] == 1800 and result['resource']['output_timeout_ms'] == 1900
    events = api.request('GET', '/v1/audit?source=management&workload_id=team-a').json()['items']
    event = next(item for item in events if item['event_type'] == 'binding_rollback')
    assert event['metadata']['previous']['state'] == 'observe' and event['metadata']['new']['state'] == 'block'
    assert event['metadata']['reason'] == 'rollback'
