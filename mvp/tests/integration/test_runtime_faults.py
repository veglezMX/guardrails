import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from proof.management_test_support import PREFIX, envelope, integration_root, policy
from proof.runtime_test_support import RuntimeStack, change_state, request_body, publish_update


@pytest.mark.parametrize('behavior,action', [('block', 'block'), ('allow_and_log', 'log_only')])
def test_actual_stalled_worker_deadline_replacement_and_continuation(https_runtime, behavior, action):
    stack = https_runtime
    stack.seed('observe')
    draft = policy('timeout')
    draft['failure_behavior']['evaluation_timeout'] = behavior
    publish_update(stack, draft)
    stack.start_mock()
    old = set(stack.resources()['worker_pids'])
    stack.fault('stall')
    started = time.monotonic()
    value = stack.evaluate('proof@example.test')
    elapsed = time.monotonic() - started
    assert elapsed <= .6 and value['evaluation_ms'] <= 600
    assert value['action'] == action and value['operational_codes'] == ['evaluation_timeout']
    assert not value['reason_codes'] and value['audit_status'] == 'persisted'
    stack.fault()
    resources = stack.wait_recovered()
    replaced = old - set(resources['worker_pids'])
    assert len(replaced) == 1 and resources['replacements'] >= 1
    for pid in replaced:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    if value['allowed']:
        model = stack.infer('proof@example.test')
        output = stack.evaluate(model, previous=value)
        assert output['allowed'] and model == 'A clean mock response.'
    calls = stack.mock_api.request('GET', '/proof/calls', actor='proof').json()
    assert calls['count'] == (1 if behavior == 'allow_and_log' else 0)
    assert stack.evaluate()['action'] == 'allow'
    (stack.root / 'timings.json').write_text(json.dumps({'worker_timeout_seconds': elapsed, 'runtime_budget_ms': 500, 'scheduling_tolerance_ms': 100}))


def test_real_queue_saturation_queued_expiry_and_recovery():
    stack = RuntimeStack(integration_root(), config={'workers': 1, 'max_queued_jobs': 2})
    try:
        stack.start()
        stack.seed('observe')
        stack.fault('stall')
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(stack.evaluate) for _ in range(3)]
            deadline = time.monotonic() + .3
            while stack.resources()['admitted'] != 3:
                assert time.monotonic() < deadline
                time.sleep(.005)
            full = stack.api.request('POST', '/v1/evaluate', actor='team-a', content=b'not-json', status=429).json()
            assert full['code'] == 'capacity_exceeded'
            results = [future.result() for future in futures]
        assert all('evaluation_timeout' in r['operational_codes'] for r in results)
        assert all(r['evaluation_ms'] <= 600 for r in results)
        stack.fault()
        recovered = stack.wait_recovered()
        assert recovered['workers'] == 1 and recovered['admitted'] == 0
        assert stack.evaluate()['action'] == 'allow'
    finally:
        stack.stop()


@pytest.mark.parametrize('state,expected', [('block', 'block'), ('redact', 'redact'), ('observe', 'log_only')])
def test_real_sqlite_lock_is_bounded_and_does_not_weaken_decision(https_runtime, state, expected):
    stack = https_runtime
    stack.seed(state)
    draft = policy('audit-open')
    draft['failure_behavior']['audit_failure'] = 'allow_and_log'
    publish_update(stack, draft)
    lock = sqlite3.connect(stack.db)
    lock.execute('BEGIN IMMEDIATE')
    try:
        started = time.monotonic()
        result = stack.evaluate('proof@example.test')
        assert time.monotonic() - started <= .6
        assert result['action'] == expected and result['audit_status'] == 'failed' and result['audit_id'] is None
        assert 'audit_write_failed' in result['operational_codes']
        if expected == 'redact':
            assert result['transformed_content']['segments'][0]['text'] == '[REDACTED]'
        # The independent control writer remains healthy while runtime is locked.
        stack.management.api.request('POST', PREFIX + '/policies', body=policy('while-locked'), status=201)
    finally:
        lock.rollback()
        lock.close()
    assert stack.evaluate()['audit_status'] == 'persisted'


def test_combined_worker_cutoff_and_sqlite_lock_share_one_stage_deadline(https_runtime):
    stack = https_runtime
    stack.seed('observe')
    stack.fault('stall')
    lock = sqlite3.connect(stack.db)
    lock.execute('BEGIN IMMEDIATE')
    try:
        started = time.monotonic()
        result = stack.evaluate()
        elapsed = time.monotonic() - started
        assert elapsed <= .6 and result['evaluation_ms'] <= 600
        assert set(result['operational_codes']) == {'evaluation_timeout', 'audit_write_failed'}
        assert result['action'] == 'block'
        (stack.root / 'timings.json').write_text(json.dumps({'combined_deadline_seconds': elapsed, 'budget_ms': 500, 'tolerance_ms': 100}))
    finally:
        lock.rollback()
        lock.close()
        stack.fault()
    stack.wait_recovered()
    assert stack.evaluate()['audit_status'] == 'persisted'


@pytest.mark.parametrize('behavior,expected', [('block', 'block'), ('allow_and_log', 'log_only')])
def test_unsafe_worker_spans_use_explicit_failure_behavior(https_runtime, behavior, expected):
    stack = https_runtime
    stack.seed('redact')
    draft = policy('unsafe')
    draft['failure_behavior']['transformation_failure'] = behavior
    publish_update(stack, draft)
    stack.fault('invalid_span')
    value = stack.evaluate('proof@example.test')
    assert value['action'] == expected and value['operational_codes'] == ['unsafe_transformation']
    assert value['transformed_content'] is None and value['findings'] == []
    stack.fault()
    assert stack.evaluate('proof@example.test')['action'] == 'redact'


def test_live_expired_and_cross_workload_contexts_do_not_select_new_policy():
    stack = RuntimeStack(integration_root(), config={'context_ttl_s': 1})
    try:
        stack.start()
        stack.seed('observe')
        registered = stack.management.api.request('POST', PREFIX + '/workloads', body=envelope(workload_id='team-b', display_name='B'), status=201).json()
        stack.tokens['team-b'] = registered['token']
        stack.wait_applied(registered)
        first = stack.evaluate()
        stack.api.request('POST', '/v1/evaluate', actor='team-b', body=request_body(previous=first, workload='team-b'), status=422)
        time.sleep(1.05)
        assert stack.evaluate(previous=first, status=422)['code'] == 'context_expired'
        assert stack.resources()['contexts'] == 0
    finally:
        stack.stop()


def test_proof_controls_require_distinct_harness_credential(https_runtime):
    https_runtime.seed()
    for actor in ('team-a', 'management', 'admin', None):
        https_runtime.api.request('PUT', '/proof/fault', actor=actor, body={'worker': 'stall'}, status=401)
    https_runtime.api.request('PUT', '/proof/fault', actor='proof', body={'worker': 'arbitrary-code'}, status=422)
