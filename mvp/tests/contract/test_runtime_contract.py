import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from guardrails.runtime.app import create_app
from guardrails.transport import DecisionModel, parse_model
from proof.management_test_support import PREFIX, envelope
from proof.runtime_test_support import batch, change_state, request_body


def evaluate(api, body=None, status=200, actor='team-a'):
    return api.request('POST', '/v1/evaluate', body=body or request_body('a@b.co'), actor=actor, status=status)


def apply(api):
    api.runtime.cache.apply(api.management.request('GET', '/internal/v1/snapshot', actor='runtime').content)


def event_from(decision):
    excluded = {'correlation_id', 'context_id', 'tenant_id', 'workload_id', 'source', 'client_config', 'transformed_content'}
    metadata = {k: v for k, v in decision.items() if k not in excluded}
    metadata.update(evaluation_id=None, audit_id=None, audit_status='spooled', findings=[], reason_codes=[],
                    guardrail_category=None, evaluator_metadata=[], action='log_only', allowed=True,
                    operational_codes=['evaluation_unavailable'], would_be_action='log_only')
    return dict(event_id=str(uuid4()), source='connector', occurred_at='2026-10-09T00:00:00Z',
                tenant_id='local-proof', workload_id='team-a', actor_id=None, event_type='client_fallback',
                correlation_id=decision['correlation_id'], metadata=metadata, content=None)


def test_evaluation_client_config_capabilities_and_schema_projections(runtime_api):
    result = evaluate(runtime_api)
    decision = parse_model(result.content, DecisionModel).to_domain()
    assert decision.action == 'log_only' and decision.context_id and decision.audit_id
    config = runtime_api.request('GET', '/v1/client-config', actor='team-a').json()
    assert set(config) == {'schema_version', 'tenant_id', 'workload_id', 'generation', 'input_timeout_ms', 'output_timeout_ms', 'failure_behavior', 'retrieved_at'}
    assert config['generation'] == decision.snapshot_generation
    caps = runtime_api.request('GET', '/v1/capabilities', actor='team-a').json()
    assert caps['supported_stages'] == ['input', 'output'] and caps['evaluators'] == [dict(id='local_pii', format='pii-ascii-v1', category='GR-003')]
    runtime_api.request('GET', '/openapi.json', actor='management')
    assert create_app(schema_only=True).openapi() == json.loads(Path('mvp/proof/runtime-openapi.json').read_text())


@pytest.mark.parametrize('change,status,code', [
    ({'tenant_id': 'foreign'}, 403, 'identity_mismatch'),
    ({'workload_id': 'team-b'}, 403, 'identity_mismatch'),
    ({'schema_version': 'old'}, 422, 'invalid_request'),
    ({'policy_id': 'override'}, 422, 'invalid_request'),
    ({'stage': 'retrieval'}, 422, 'unsupported_surface'),
    ({'stage': 'tool_call'}, 422, 'unsupported_surface'),
    ({'stage': 'tool_result'}, 422, 'unsupported_surface'),
    ({'stage': 'memory_write'}, 422, 'unsupported_surface'),
    ({'stage': 'final_response'}, 422, 'unsupported_surface'),
    ({'context_id': 'unexpected'}, 422, 'invalid_request'),
    ({'stage': 'output', 'context_id': None}, 422, 'invalid_request'),
    ({'stage': 'output', 'context_id': 'unknown'}, 422, 'context_invalid'),
    ({'correlation_id': 'secret-value'}, 422, 'invalid_request'),
    ({'content': {'segments': [{'segment_id': '0', 'text': False}]}}, 422, 'invalid_request'),
    ({'content': {'segments': [{'segment_id': 'user-supplied-name', 'text': 'secret-value'}]}}, 422, 'invalid_request'),
    ({'content': {'segments': []}}, 422, 'invalid_request'),
])
def test_invalid_payloads_return_sanitized_problem(runtime_api, change, status, code):
    response = evaluate(runtime_api, request_body('secret-value') | change, status)
    assert response.json()['code'] == code and 'secret-value' not in response.text


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{', b'"\xff"'])
def test_malformed_json_is_400_without_input_echo(runtime_api, raw):
    runtime_api.request('POST', '/v1/evaluate', actor='team-a', content=raw, status=400)


@pytest.mark.parametrize('path', ['/internal/v1/audit?tenant_id=local-proof', '/health/ready', '/openapi.json'])
def test_workload_cannot_read_service_endpoints(runtime_api, path):
    runtime_api.request('GET', path, actor='team-a', status=403)


@pytest.mark.parametrize('path', ['/v1/client-config', '/v1/evaluate', '/v1/client-events'])
def test_service_cannot_impersonate_workload(runtime_api, path):
    runtime_api.request('GET' if path.endswith('config') else 'POST', path, actor='management', status=403)


def test_context_one_use_identity_correlation_and_policy_generation(runtime_api):
    first = evaluate(runtime_api).json()
    wrong = request_body(previous=first) | {'correlation_id': str(uuid4())}
    evaluate(runtime_api, wrong, 422)
    change_state(runtime_api.management, 'block')
    apply(runtime_api)
    output = evaluate(runtime_api, request_body('a@b.co', previous=first)).json()
    assert output['snapshot_generation'] == first['snapshot_generation'] and output['action'] == 'log_only'
    evaluate(runtime_api, request_body(previous=first), 422)
    current = evaluate(runtime_api).json()
    assert current['action'] == 'block' and current['snapshot_generation'] > first['snapshot_generation']
    assert current['context_id'] not in runtime_api.runtime.contexts.contexts


def test_scoped_content_free_idempotent_events_and_collision(runtime_api):
    decision = evaluate(runtime_api).json()
    event = event_from(decision)
    one = runtime_api.request('POST', '/v1/client-events', actor='team-a', body=[event]).json()
    two = runtime_api.request('POST', '/v1/client-events', actor='team-a', body=[event]).json()
    assert one == two == {'accepted_event_ids': [event['event_id']]}
    changed = json.loads(json.dumps(event))
    changed['metadata']['evaluation_ms'] += 1
    runtime_api.request('POST', '/v1/client-events', actor='team-a', body=[changed], status=409)
    result = runtime_api.request('GET', '/internal/v1/audit?tenant_id=local-proof&source=connector', actor='management').json()
    assert len(result['items']) == 1 and result['complete']


@pytest.mark.parametrize('field,value,status', [('workload_id', 'team-b', 403), ('tenant_id', 'foreign', 403),
                                              ('source', 'runtime', 422), ('content', {'segments': [{'segment_id': '0', 'text': 'secret-value'}]}, 422),
                                              ('event_type', 'secret-value', 422)])
def test_client_events_reject_foreign_identity_content_and_arbitrary_strings(runtime_api, field, value, status):
    event = event_from(evaluate(runtime_api).json())
    event[field] = value
    response = runtime_api.request('POST', '/v1/client-events', actor='team-a', body=[event], status=status)
    assert 'secret-value' not in response.text


def test_event_metadata_allowlist_and_batch_atomicity(runtime_api):
    one = event_from(evaluate(runtime_api).json())
    bad = json.loads(json.dumps(one))
    bad['event_id'] = str(uuid4())
    bad['metadata']['raw_text'] = 'secret-value'
    runtime_api.request('POST', '/v1/client-events', actor='team-a', body=[one, bad], status=422)
    page = runtime_api.request('GET', '/internal/v1/audit?tenant_id=local-proof&source=connector', actor='management').json()
    assert not page['items']
    runtime_api.request('POST', '/v1/client-events', actor='team-a', body=[one] * 101, status=422)


def test_audit_delegation_and_cursor_scope(runtime_api):
    for _ in range(3):
        evaluate(runtime_api)
    runtime_api.request('GET', '/internal/v1/audit', actor='management', status=403)
    runtime_api.request('GET', '/internal/v1/audit?tenant_id=local-proof&include_content=true', actor='management', status=403)
    runtime_api.request('GET', '/internal/v1/audit?tenant_id=local-proof&include_content=true', actor='management',
                        headers={'X-Audit-Actor': str(uuid4()), 'X-Audit-Role': 'tenant_operator'}, status=403)
    page = runtime_api.request('GET', '/internal/v1/audit?tenant_id=local-proof&limit=1', actor='management').json()
    next_page = runtime_api.request('GET', '/internal/v1/audit', actor='management', params={'tenant_id': 'local-proof', 'limit': 1, 'cursor': page['next_cursor']}).json()
    assert next_page['items'][0]['event_id'] != page['items'][0]['event_id']
    runtime_api.request('GET', '/internal/v1/audit', actor='management', params={'tenant_id': 'foreign', 'cursor': page['next_cursor']}, status=422)
    assert all(item['content'] is None for item in page['items'])


def test_capacity_is_rejected_before_body_read(runtime_api):
    from starlette.testclient import TestClient
    runtime_api.runtime.executor.admitted = 33  # one worker plus 32 queue slots
    response = runtime_api.request('POST', '/v1/evaluate', actor='team-a', content=b'{invalid-body', status=429)
    assert response.json()['code'] == 'capacity_exceeded'
    runtime_api.runtime.executor.admitted = 0


def test_unbound_floor_policy_and_stale_snapshot_failures(runtime_api):
    runtime_api.runtime.cache.last_contact -= 61
    decision = evaluate(runtime_api).json()
    assert decision['action'] == 'block' and decision['operational_codes'] == ['management_unavailable']
    runtime_api.request('POST', '/v1/evaluate', actor='unknown-token' * 5, body=request_body(), status=401)
    runtime_api.runtime.cache.current = None
    runtime_api.request('POST', '/v1/evaluate', actor='team-a', body=request_body(), status=503)


def test_limits_and_unknown_query_fields(runtime_api):
    runtime_api.request('GET', '/v1/client-config?workload_id=team-b', actor='team-a', status=422)
    body = request_body()
    body['content']['segments'] *= 2
    evaluate(runtime_api, body, 422)
    runtime_api.runtime.config = __import__('guardrails.local_contract', fromlist=['ProofConfig']).ProofConfig(max_http_bytes=32)
    evaluate(runtime_api, request_body(), 413)
