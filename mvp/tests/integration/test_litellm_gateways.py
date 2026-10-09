"""Full public lifecycle and enforcement through two pinned LiteLLM processes."""
import concurrent.futures
import json
import sqlite3
import time
from uuid import uuid4

import pytest
from proof.management_test_support import PREFIX, envelope, policy, floor

SENSITIVE = 'Contact alice@example.test or +15551234567.'
OUTPUT = 'Contact proof@example.test or +15551234567.'
SAFE = 'Contact [REDACTED] or [REDACTED].'


@pytest.mark.parametrize('state', ['disabled','observe','warn','redact','block'])
@pytest.mark.parametrize('sensitive_input,sensitive_output', [(False,False),(True,False),(False,True),(True,True)])
def test_two_gateway_state_matrix_and_every_message(gateways, state, sensitive_input, sensitive_output):
    stack = gateways
    stack.state(state)
    compared = []
    ids = []
    for gateway in ('a','b'):
        stack.scenario('sensitive' if sensitive_output else 'clean')
        texts = (SENSITIVE, SENSITIVE, SENSITIVE, 'clean user') if sensitive_input else ('clean system','clean developer','clean assistant','clean user')
        response = stack.chat(gateway, texts)
        captures = stack.mock()
        if state == 'block' and (sensitive_input or sensitive_output):
            assert response.status_code == 403
            assert captures['count'] == (0 if sensitive_input else 1)
            assert 'alice@' not in response.text and 'proof@' not in response.text
            compared.append(response.json()['error']['code'])
        else:
            assert response.status_code == 200, 'gateway state request failed'
            value = response.json()
            metadata = value['guardrails']
            action = {'disabled':'allow','observe':'log_only','warn':'log_only','redact':'redact','block':'block'}[state]
            assert metadata['input_action'] == (action if sensitive_input else 'allow')
            assert metadata['output_action'] == (action if sensitive_output else 'allow')
            assert metadata['input_source'] == metadata['output_source'] == 'runtime'
            assert bool(metadata['warning_codes']) == (state == 'warn' and (sensitive_input or sensitive_output))
            assert captures['count'] == 1
            observed = captures['calls'][0]['messages']
            assert [x['role'] for x in observed] == ['system','developer','assistant','user']
            assert [x['content'] for x in observed] == ([SAFE,SAFE,SAFE,'clean user'] if sensitive_input and state == 'redact' else list(texts))
            text = value['choices'][0]['message']['content']
            assert text == (SAFE if sensitive_output and state == 'redact' else OUTPUT if sensitive_output else 'A clean mock response.')
            ids.append(metadata['input_decision_id'])
            compared.append((metadata['input_action'], metadata['output_action'], metadata['warning_codes'], text))
    assert compared[0] == compared[1]
    if ids:
        assert ids[0] != ids[1]


@pytest.mark.parametrize('scenario', ['tool_calls','refusal','multiple','multipart','error','timeout'])
def test_invalid_upstream_and_timeout_never_deliver_unchecked_output(gateways, scenario):
    gateways.state('observe')
    gateways.scenario(scenario)
    result = gateways.chat()
    assert result.status_code == (504 if scenario == 'timeout' else 502 if scenario == 'error' else 503)
    assert 'choices' not in result.json() and 'Synthetic' not in result.text
    assert gateways.mock()['count'] == 1


@pytest.mark.parametrize('mode', ['stop','pause'])
@pytest.mark.parametrize('behavior', ['block','allow_and_log'])
def test_real_runtime_failure_fallback_and_durable_replay(gateways, mode, behavior):
    stack = gateways
    draft = policy('fault-' + uuid4().hex[:8])
    draft['failure_behavior'].update(evaluation_unavailable=behavior, evaluation_timeout=behavior, audit_failure=behavior)
    stack.bind_policy(draft)
    stack.state('observe')
    stack.scenario('sensitive')
    stack.compose(mode, 'runtime')
    try:
        for gateway in ('a','b'):
            result = stack.chat(gateway, (SENSITIVE,))
            assert result.status_code == (503 if behavior == 'block' else 200)
            if behavior == 'allow_and_log':
                body = result.json()
                assert body['choices'][0]['message']['content'] == OUTPUT
                assert body['guardrails']['input_source'] == body['guardrails']['output_source'] == 'connector'
                assert body['guardrails']['audit_status'] == 'spooled'
                assert body['guardrails']['policy_id'] is None
            path = stack.root / 'state' / ('gateway-' + gateway) / 'client-events.jsonl'
            assert path.stat().st_size > 0
            assert 'alice@' not in path.read_text() and 'proof@' not in path.read_text()
        calls = stack.mock()
        assert calls['count'] == (0 if behavior == 'block' else 2)
        if calls['count']:
            assert calls['calls'][0]['messages'][0]['content'] == SENSITIVE
    finally:
        stack.compose('unpause' if mode == 'pause' else 'start', 'runtime')
        stack.ready('runtime')
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if all((stack.root / 'state' / ('gateway-' + letter) / 'client-events.jsonl').stat().st_size == 0 for letter in ('a','b')):
            break
        time.sleep(.2)
    else:
        pytest.fail('client events were not replayed')
    records = stack.api.request('GET','/v1/audit',params={'tenant_id':'local-proof','source':'connector','limit':200}).json()
    assert records['complete'] and records['items']
    assert all(event['content'] is None and event['source'] == 'connector' for event in records['items'])
    assert len({event['event_id'] for event in records['items']}) == len(records['items'])
    stack.bind_policy(policy('closed-' + uuid4().hex[:8]))


def test_output_outage_uses_pinned_fail_open_and_returns_real_model_text(gateways):
    stack = gateways
    draft = policy('output-' + uuid4().hex[:8])
    draft['failure_behavior'].update(evaluation_unavailable='allow_and_log', audit_failure='allow_and_log')
    stack.bind_policy(draft)
    stack.state('observe')
    stack.scenario('delayed_sensitive')
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(stack.chat)
        deadline = time.monotonic() + 3
        while stack.mock()['count'] != 1 and time.monotonic() < deadline:
            time.sleep(.05)
        stack.compose('stop', '-t', '0', 'runtime')
        try:
            result = future.result(timeout=10)
            assert result.status_code == 200
            body = result.json()
            assert body['choices'][0]['message']['content'] == OUTPUT
            assert body['guardrails']['input_source'] == 'runtime' and body['guardrails']['output_source'] == 'connector'
        finally:
            stack.compose('start','runtime')
            stack.ready('runtime')
    stack.bind_policy(policy('closed-' + uuid4().hex[:8]))


def test_turn_keeps_old_generation_during_live_update(gateways):
    stack = gateways
    stack.state('redact')
    old = stack.binding('a')
    stack.scenario('delayed_sensitive')
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(stack.chat)
        deadline = time.monotonic() + 3
        while stack.mock()['count'] != 1 and time.monotonic() < deadline:
            time.sleep(.05)
        draft = policy('updated-' + uuid4().hex[:8])
        stack.api.request('POST', PREFIX + '/policies', body=draft, status=201)
        mutation = stack.api.request('PUT', PREFIX + '/workloads/gateway-a/binding', status=202, revision=old['revision'],
            body=envelope(policy_id='pii', policy_version=draft['version'], reason='policy_update')).json()
        stack.wait(mutation)
        assert not future.done(), 'update must apply while model inference is in flight'
        response = future.result(timeout=10)
    assert response.status_code == 200
    assert response.json()['guardrails']['policy_version'] == old['policy_version']
    assert response.json()['choices'][0]['message']['content'] == SAFE
    stack.scenario('clean')
    assert stack.chat().json()['guardrails']['policy_version'] == draft['version']
    restored = stack.api.request('POST', PREFIX + '/workloads/gateway-a/binding/rollback', status=202, revision=stack.binding('a')['revision'],
        body=envelope(target_revision=old['revision'], reason='rollback')).json()
    stack.wait(restored)
    assert stack.chat().json()['guardrails']['policy_version'] == old['policy_version']


@pytest.mark.parametrize('worker,code', [('stall','evaluation_timeout'),('crash','evaluation_unavailable'),('invalid_span','unsafe_transformation')])
@pytest.mark.parametrize('behavior', ['block','allow_and_log'])
def test_real_worker_faults_through_both_gateways(gateways, worker, code, behavior):
    stack = gateways
    draft = policy('worker-' + uuid4().hex[:8])
    draft['failure_behavior'] = dict.fromkeys(draft['failure_behavior'], behavior)
    stack.bind_policy(draft)
    stack.state('redact')
    stack.scenario('sensitive')
    stack.fault(worker)
    try:
        for letter in ('a','b'):
            response = stack.chat(letter,(SENSITIVE,))
            assert response.status_code == (503 if behavior == 'block' else 200)
            if behavior == 'allow_and_log':
                value = response.json()
                assert code in value['guardrails']['operational_codes']
                assert value['choices'][0]['message']['content'] == OUTPUT
                assert value['guardrails']['input_source'] == 'runtime'
        assert stack.mock()['count'] == (0 if behavior == 'block' else 2)
    finally:
        stack.fault()
        stack.bind_policy(policy('closed-' + uuid4().hex[:8]))


@pytest.mark.parametrize('state', ['block','redact'])
def test_audit_lock_never_weakens_completed_restriction(gateways, state):
    stack = gateways
    draft = policy('audit-' + uuid4().hex[:8])
    draft['failure_behavior']['audit_failure'] = 'allow_and_log'
    stack.bind_policy(draft)
    stack.state(state)
    stack.scenario('sensitive')
    connection = sqlite3.connect(stack.root / 'state/live-runtime/audit.sqlite3')
    connection.execute('BEGIN IMMEDIATE')
    try:
        response = stack.chat(texts=(SENSITIVE,))
        assert response.status_code == (503 if state == 'block' else 200)
        if state == 'redact':
            value = response.json()
            assert value['choices'][0]['message']['content'] == SAFE
            assert value['guardrails']['input_action'] == value['guardrails']['output_action'] == 'redact'
            assert value['guardrails']['audit_status'] == 'spooled'
        assert 'alice@' not in response.text and 'proof@' not in response.text
    finally:
        connection.rollback()
        connection.close()
    stack.bind_policy(policy('closed-' + uuid4().hex[:8]))


def test_cli_lifecycle_wait_rotation_governance_and_audit(gateways):
    s = gateways
    tenant, workload = 'cli-' + uuid4().hex[:8], 'team'
    s.cli('tenants','register','--id',tenant,'--name','CLI tenant')
    token = s.root / 'secrets/cli-workload.token'
    issued = s.cli('workloads','register','--tenant',tenant,'--id',workload,'--name','CLI workload','--credential-out',token)
    assert issued['operation']['status'] == 'applied'
    draft = s.root / 'cli-policy.json'
    draft.write_text(json.dumps(policy(tenant=tenant)))
    s.cli('policies','validate','--tenant',tenant,'--file',draft)
    s.cli('policies','publish','--tenant',tenant,'--file',draft)
    s.cli('bindings','set','--tenant',tenant,'--workload',workload,'--policy','pii','--version','v1','--reason','initial_binding')
    promoted = s.cli('bindings','state','--tenant',tenant,'--workload',workload,'--state','redact','--revision','1','--reason','synthetic_promotion','--fixture-run',s.run_id)
    assert promoted['operation']['status'] == 'applied'
    draft.write_text(json.dumps(policy('v2',tenant=tenant)))
    s.cli('policies','publish','--tenant',tenant,'--file',draft)
    s.cli('bindings','set','--tenant',tenant,'--workload',workload,'--policy','pii','--version','v2','--revision','2','--reason','policy_update')
    rolled = s.cli('bindings','rollback','--tenant',tenant,'--workload',workload,'--to-revision','1','--revision','3','--reason','rollback')
    assert rolled['resource']['enforcement_state'] == 'observe'
    history = s.cli('bindings','history','--tenant',tenant,'--workload',workload)
    assert len(history['items']) == 4
    constraint = s.root / 'cli-floor.json'
    constraint.write_text(json.dumps(floor()))
    s.cli('floors','set','--scope','tenant','--scope-id',tenant,'--file',constraint)
    s.cli('bindings','remove','--tenant',tenant,'--workload',workload,'--revision','4','--reason','binding_removal',status=4)
    s.cli('bindings','state','--tenant',tenant,'--workload',workload,'--state','disabled','--revision','4','--reason','synthetic_demotion','--fixture-run',s.run_id,status=4)
    s.cli('workloads','get','--tenant',tenant,'--id',workload,actor='operator',status=4)
    new = s.root / 'secrets/cli-rotated.token'
    s.cli('workloads','rotate-credential','--tenant',tenant,'--id',workload,'--revision','1','--reason','credential_rotation','--credential-out',new)
    assert new.read_text() != token.read_text()
    assert s.cli('operations','get','--id',promoted['operation']['operation_id'])['status'] == 'applied'
    assert len(s.cli('policies','list','--tenant',tenant)['items']) == 2
    assert s.cli('policies','get','--tenant',tenant,'--id','pii','--version','v1')['version'] == 'v1'
    assert s.cli('floors','get','--scope','tenant','--scope-id',tenant)
    assert 'local_pii' in {item['id'] for item in s.cli('capabilities',actor='cli-rotated')['evaluators']}
    s.cli('capabilities',actor='cli-workload',status=3)
    from proof.runtime_test_support import request_body
    diagnostic = s.root / 'cli-evaluation.json'
    diagnostic.write_text(json.dumps(request_body('Synthetic CLI diagnostic input.') | {'tenant_id':tenant,'workload_id':workload}))
    evaluated = s.cli('evaluate','--file',diagnostic,actor='cli-rotated')
    assert evaluated['source'] == 'runtime' and evaluated['policy_version'] == 'v1'
    s.fixture_content.add('Synthetic CLI diagnostic input.')
    readback = s.cli('audit','list','--tenant',tenant)
    assert readback['complete'] and readback['items']
    draft.write_text(json.dumps(policy('v3',tenant=tenant)))
    s.compose('stop','runtime')
    try:
        pending = s.cli('policies','publish','--tenant',tenant,'--file',draft,status=6,extra=('--wait-timeout','.2'))
        assert pending['committed_operation']['status'] == 'pending'
    finally:
        s.compose('start','runtime')
        s.ready('runtime')


@pytest.mark.parametrize('stage', ['input','output'])
@pytest.mark.parametrize('mode,code', [('malformed','evaluation_unavailable'),('invalid_transformation','unsafe_transformation'),('delay','evaluation_timeout')])
@pytest.mark.parametrize('behavior', ['block','allow_and_log'])
def test_real_response_faults_obey_pinned_behavior(gateways, stage, mode, code, behavior):
    s = gateways
    draft = policy('protocol-' + uuid4().hex[:8])
    draft['failure_behavior'] = dict.fromkeys(draft['failure_behavior'], behavior)
    s.bind_policy(draft)
    s.state('observe')
    s.scenario('sensitive')
    response = s.client.put(s.url('runtime') + '/proof/fault', headers=s.headers('control'), json={'worker':None,'response':mode,'stage':stage})
    assert response.status_code == 200
    try:
        result = s.chat(texts=(SENSITIVE,))
        assert result.status_code == (503 if behavior == 'block' else 200)
        if behavior == 'allow_and_log':
            value = result.json()
            assert value['choices'][0]['message']['content'] == OUTPUT
            assert value['guardrails'][stage + '_source'] == 'connector'
            assert code in value['guardrails']['operational_codes']
        assert s.mock()['count'] == (0 if stage == 'input' and behavior == 'block' else 1)
    finally:
        s.fault()
        s.bind_policy(policy('closed-' + uuid4().hex[:8]))


def test_never_configured_gateway_refuses_then_recovers(gateways):
    s = gateways
    s.scenario()
    s.compose('stop','runtime')
    try:
        s.compose('restart','gateway-a')
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                result = s.client.get(s.url('gateway-a') + '/health/live')
                if result.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(.2)
        health = s.client.get(s.url('gateway-a') + '/health/ready',headers=s.headers('gateway-a.client'))
        assert health.status_code == 503 and health.json()['client_generation'] is None
        assert s.chat().status_code == 503
        assert s.mock()['count'] == 0
    finally:
        s.compose('start','runtime')
        s.ready('runtime')
        s.ready('gateway-a')
    assert s.chat().status_code == 200


def test_management_outage_keeps_valid_cached_evaluation(gateways):
    s = gateways
    s.state('redact')
    s.scenario('sensitive')
    s.compose('stop','management')
    try:
        for letter in ('a','b'):
            response = s.chat(letter,(SENSITIVE,))
            assert response.status_code == 200
            assert response.json()['choices'][0]['message']['content'] == SAFE
            assert response.json()['guardrails']['input_source'] == 'runtime'
    finally:
        s.compose('start','management')
        s.ready('management')


def test_expired_context_never_selects_new_policy_or_loses_fail_open_output(gateways):
    s = gateways
    path = s.root / 'live-runtime.json'
    original = path.read_text()
    values = json.loads(original)
    values['proof_config']['context_ttl_s'] = 1
    path.write_text(json.dumps(values))
    s.compose('restart','runtime')
    s.ready('runtime')
    s.wait_initial_receipt()
    draft = policy('expiry-' + uuid4().hex[:8])
    draft['failure_behavior']['evaluation_unavailable'] = 'allow_and_log'
    s.bind_policy(draft)
    s.state('observe')
    s.scenario('delayed_sensitive')
    try:
        result = s.chat()
        assert result.status_code == 200
        value = result.json()
        assert value['choices'][0]['message']['content'] == OUTPUT
        assert value['guardrails']['output_source'] == 'connector'
        assert value['guardrails']['policy_version'] is None
    finally:
        path.write_text(original)
        s.compose('restart','runtime')
        s.ready('runtime')
        s.wait_initial_receipt()
        s.bind_policy(policy('closed-' + uuid4().hex[:8]))
