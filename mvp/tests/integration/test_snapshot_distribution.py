import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from proof.management_test_support import PREFIX, envelope, integration_root, policy
from proof.runtime_test_support import RuntimeStack, change_state, request_body, publish_update


def test_real_polling_ack_update_during_delayed_inference_and_rollback(https_runtime):
    stack = https_runtime
    stack.seed('observe')
    stack.start_mock()
    stack.mock_api.request('PUT', '/proof/scenario', actor='proof', body={'scenario': 'delayed_sensitive'})
    first = stack.evaluate('clean input')
    with ThreadPoolExecutor(max_workers=1) as pool:
        inference = pool.submit(stack.infer)
        # Wait for the real model request to arrive, then commit while it sleeps.
        deadline = time.monotonic() + 2
        while stack.mock_api.request('GET', '/proof/calls', actor='proof').json()['count'] == 0:
            assert time.monotonic() < deadline
            time.sleep(.01)
        update = publish_update(stack, policy('v2'))
        assert not inference.done(), 'The newer generation must apply while the model request is still in flight'
        stack.wait_applied(change_state(stack.management.api, 'block'))
        output = stack.evaluate(inference.result(), previous=first)
    assert output['policy_version'] == 'v1' and output['snapshot_generation'] == first['snapshot_generation']
    assert output['action'] == 'log_only' and output['binding_revision'] == first['binding_revision']
    new = stack.evaluate('proof@example.test')
    assert new['action'] == 'block' and new['policy_version'] == 'v2'
    assert new['snapshot_generation'] >= update['operation']['desired_generation']
    path = PREFIX + '/workloads/team-a/binding'
    revision = stack.management.api.request('GET', path).json()['desired']['revision']
    rollback = stack.management.api.request('POST', path + '/rollback', revision=revision, status=202,
                                           body=envelope(target_revision=1, reason='rollback')).json()
    stack.wait_applied(rollback)
    restored = stack.evaluate('proof@example.test')
    assert restored['policy_version'] == 'v1' and restored['action'] == 'log_only'
    assert restored['snapshot_generation'] > new['snapshot_generation']
    assert len(stack.management.api.request('GET', path + '/history').json()['items']) == 4


def test_management_outage_uses_snapshot_without_synchronous_dependency_and_rotation_applies(https_runtime):
    stack = https_runtime
    stack.seed('redact')
    old_token = stack.tokens['team-a']
    stack.management.stop()
    time.sleep(1.2)  # observe a failed background poll, within default 60s age
    decision = stack.evaluate('proof@example.test')
    assert decision['action'] == 'redact' and not decision['operational_codes']
    ready = stack.api.request('GET', '/health/ready', actor='management').json()
    assert ready['ready'] and ready['degraded'] and ready['cache_age_s'] > 1
    stack.api.request('POST', '/v1/evaluate', actor='unrecognized-token' * 4, body=request_body(), status=401)
    stack.management.start()
    rotated = stack.management.api.request('POST', PREFIX + '/workloads/team-a/credentials/rotate', revision=1,
                                           body=envelope(reason='credential_rotation'), status=201).json()
    stack.wait_applied(rotated)
    stack.api.request('POST', '/v1/evaluate', actor=old_token, body=request_body(), status=401)
    stack.tokens['team-a'] = rotated['token']
    assert stack.evaluate()['action'] == 'allow'


@pytest.mark.parametrize('behavior,action', [('block', 'block'), ('allow_and_log', 'log_only')])
def test_stale_and_cold_snapshots_are_explicit_and_unknown_credentials_always_reject(behavior, action):
    stack = RuntimeStack(integration_root(), config={'max_snapshot_age_s': 1})
    try:
        stack.start()
        stack.seed('observe')
        draft = policy('v2')
        draft['failure_behavior']['management_unavailable'] = behavior
        publish_update(stack, draft)
        stack.management.stop()
        time.sleep(1.2)
        value = stack.evaluate('proof@example.test')
        assert value['action'] == action and value['operational_codes'] == ['management_unavailable']
        health = stack.api.request('GET', '/health/ready', actor='management', status=503).json()
        assert health['degraded'] and not health['ready'] and health['applied_generation'] is not None
        stack.stop_runtime()
        stack.start_runtime()
        health = stack.api.request('GET', '/health/ready', actor='management', status=503).json()
        assert health['applied_generation'] is None and health['cache_age_s'] is None
        assert stack.evaluate(status=503)['code'] == 'management_unavailable'
    finally:
        stack.stop()


def test_runtime_audit_restart_and_old_context_invalidated_with_private_files(https_runtime):
    stack = https_runtime
    stack.seed('redact')
    first = stack.evaluate('proof@example.test')
    second = stack.evaluate('output@example.test', previous=first)
    before = stack.management.api.request('GET', '/v1/audit', params={'tenant_id': 'local-proof', 'source': 'runtime'}).json()
    assert len(before['items']) == 2 and before['complete']
    assert stack.db != stack.management.db
    old_pids = stack.resources()['worker_pids']
    stack.stop_runtime()
    for pid in old_pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    stack.start_runtime()
    deadline = time.monotonic() + 5
    while stack.api.client.get('/health/ready', headers={'Authorization': 'Bearer ' + stack.tokens['management']}).status_code != 200:
        assert time.monotonic() < deadline
        time.sleep(.05)
    assert stack.evaluate(previous=first, status=422)['code'] == 'context_invalid'
    after = stack.management.api.request('GET', '/v1/audit', params={'tenant_id': 'local-proof', 'source': 'runtime'}).json()
    assert after['items'] == before['items']
    with sqlite3.connect(stack.db) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    for root in (stack.runtime_root, stack.management.root):
        for path in root.rglob('*'):
            if path.is_file() and (path.suffix in ('.sqlite3', '.log') or path.name.endswith(('-wal', '-shm'))):
                data = path.read_bytes()
                assert b'proof@example.test' not in data and b'output@example.test' not in data
                assert all(token.encode() not in data for token in stack.tokens.values())
                assert path.stat().st_mode & 0o777 == 0o600


def test_retention_delegation_federated_pagination_and_source_completeness(https_runtime):
    stack = https_runtime
    stack.seed('observe')
    draft = policy('retained', retain_content=True)
    stack.management.api.request('POST', PREFIX + '/policies', actor='operator', body=draft, status=403)
    publish_update(stack, draft)
    retained = 'retained@example.test'
    first = stack.evaluate(retained)
    for _ in range(4):
        stack.evaluate()
    params = {'tenant_id': 'local-proof', 'correlation_id': first['correlation_id']}
    default = stack.management.api.request('GET', '/v1/audit', params=params).json()
    assert default['complete'] and default['source_status'] == {'management': 'available', 'runtime': 'available'}
    assert all(e['content'] is None for e in default['items'])
    stack.management.api.request('GET', '/v1/audit', actor='operator', params=params | {'include_content': 'true'}, status=403)
    content = stack.management.api.request('GET', '/v1/audit', params=params | {'include_content': 'true'}).json()
    assert content['items'][0]['content']['segments'][0]['text'] == retained
    management_events = stack.management.api.request('GET', '/v1/audit', params={'tenant_id': 'local-proof', 'source': 'management'}).json()['items']
    assert any(e['event_type'] == 'audit_content_read' for e in management_events)
    assert retained not in json.dumps(management_events)
    all_ids, cursor = [], None
    while True:
        query = {'tenant_id': 'local-proof', 'limit': 2}
        if cursor:
            query['cursor'] = cursor
        page = stack.management.api.request('GET', '/v1/audit', params=query).json()
        all_ids.extend(e['event_id'] for e in page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            break
        assert len(all_ids) < 100
    full = stack.management.api.request('GET', '/v1/audit', params={'tenant_id': 'local-proof', 'limit': 200}).json()
    assert all_ids == [e['event_id'] for e in full['items']] and len(all_ids) == len(set(all_ids))
    schema = stack.management.api.request('GET', '/runtime-openapi.json').json()
    assert '/v1/evaluate' in schema['paths']
    stack.stop_runtime()
    failed = stack.management.api.request('GET', '/v1/audit', params={'tenant_id': 'local-proof'}, status=503).json()
    assert failed['source_status']['runtime'] == 'unavailable' and not failed['complete']
    partial = stack.management.api.request('GET', '/v1/audit', params={'tenant_id': 'local-proof', 'allow_partial': 'true'}).json()
    assert not partial['complete'] and partial['source_status']['management'] == 'available'
    stack.management.api.request('GET', '/runtime-openapi.json', status=503)


def test_runtime_tls_rejects_untrusted_ca(https_runtime):
    with httpx.Client(base_url=https_runtime.url, trust_env=False) as client:
        with pytest.raises(httpx.TransportError):
            client.get('/health/live')


def test_corrupt_snapshot_over_real_tls_keeps_last_good_state_and_pending_operation(https_runtime):
    import hashlib
    import signal
    from guardrails.local_contract import canonical_bytes
    stack = https_runtime
    stack.seed('observe')
    before = stack.evaluate()['snapshot_generation']
    os.kill(stack.process.pid, signal.SIGSTOP)
    original = None
    try:
        publication = stack.management.api.request('POST', PREFIX + '/policies', body=policy('newer'), status=201).json()
        with sqlite3.connect(stack.management.db) as connection:
            row = connection.execute('SELECT snapshot FROM operations WHERE operation_id=?', (publication['operation']['operation_id'],)).fetchone()
            original = row[0]
            damaged = json.loads(original)
            damaged['bindings'][0]['policy_version'] = 'absent'
            damaged['digest'] = hashlib.sha256(canonical_bytes({k: v for k, v in damaged.items() if k != 'digest'})).hexdigest()
            connection.execute('UPDATE operations SET snapshot=? WHERE operation_id=?', (canonical_bytes(damaged).decode(), publication['operation']['operation_id']))
    finally:
        os.kill(stack.process.pid, signal.SIGCONT)
    try:
        deadline = time.monotonic() + 3
        while True:
            health = stack.api.request('GET', '/health/ready', actor='management').json()
            if health['degraded']:
                break
            assert time.monotonic() < deadline
            time.sleep(.05)
        assert health['applied_generation'] == before and health['ready']
        operation = stack.management.api.request('GET', '/v1/operations/' + publication['operation']['operation_id']).json()
        assert operation['status'] == 'pending'
        assert stack.evaluate('proof@example.test')['action'] == 'log_only'
    finally:
        if original:
            with sqlite3.connect(stack.management.db) as connection:
                connection.execute('UPDATE operations SET snapshot=? WHERE operation_id=?', (original, publication['operation']['operation_id']))
    stack.wait_applied(publication)
    assert stack.evaluate()['snapshot_generation'] > before
