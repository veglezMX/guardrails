"""Authored release corpus through the actual isolated gateways and APIs."""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
from uuid import uuid4

import pytest

from proof.acceptance_stack import rpc
from proof.fixtures import load_fixtures, PiiFixture, LifecycleFixture, FailureFixture
from proof.management_test_support import PREFIX, envelope, policy
from proof.reports import write_safe

FIXTURES = load_fixtures(Path(__file__).parents[1] / 'fixtures', '00000000-0000-0000-0000-000000000001')


def observe(gateways, scenario, **metadata):
    gateways.observations.append(dict(scenario_id=scenario, **metadata))


def audit_turn(stack, correlation):
    value = stack.api.request('GET', '/v1/audit', params=dict(tenant_id='local-proof', correlation_id=correlation, source='runtime', limit=200)).json()
    assert value['complete'] and value['next_cursor'] is None
    return {event['metadata']['stage']: event for event in value['items']}


def decision_evidence(stack, response, stage, letter):
    """Correlate an actual decision with its persisted or pending durable event."""
    body = response.json()
    public = body['guardrails'] if response.status_code == 200 else body['error']
    correlation = public['correlation_id']
    decision_id = public[stage + '_decision_id'] if response.status_code == 200 else public['decision_id']
    source = public[stage + '_source'] if response.status_code == 200 else public['source']
    codes = public['operational_codes']
    pending = stack.root/'state'/('gateway-'+letter)/'client-events.jsonl'
    candidates = [json.loads(line) for line in pending.read_text().splitlines()] if pending.exists() else []
    result = stack.api.client.get('/v1/audit',headers=stack.headers('admin'),params=dict(
        tenant_id='local-proof',correlation_id=correlation,allow_partial='true',limit=200))
    assert result.status_code == 200
    candidates += result.json()['items']
    candidates = [event for event in candidates if event['correlation_id'] == correlation
                  and event['metadata'].get('stage') == stage]
    matches = [event for event in candidates if event['metadata'].get('decision_id') == decision_id]
    if not matches and 'audit_write_failed' in codes:
        # Runtime audit failure is forwarded durably under a distinct connector ID.
        matches = [event for event in candidates if event['source']=='connector'
                   and 'audit_write_failed' in event['metadata'].get('operational_codes',[])]
    assert matches, 'decision_evidence_missing'
    event=matches[0]
    assert event['content'] is None and event['workload_id']=='gateway-'+letter
    return dict(action=event['metadata']['action'], source=source, operational_codes=codes,
                evidence_source=event['source'], audit_status=public['audit_status'])


def test_offline_prepared_services_and_runner(gateways):
    stack = gateways
    value = rpc('network')
    artifacts=json.loads((Path(os.environ['PROOF_ACCEPTANCE_REPORT'])/'manifest.json').read_text())['artifacts']
    assert value['internal']
    assert len({s['container_id'] for s in value['services'].values()}) == 5
    for service, state in value['services'].items():
        assert state['running'] and state['networks'] == [stack.project + '_proof']
        assert state['image_id']==artifacts['images']['gateway' if service.startswith('gateway-') else 'owned']['image_id']
        assert rpc('probe', service=service)['denied']
    assert value['runner']['networks']==[stack.project+'_proof'] and value['runner']['running']
    assert value['runner']['image_id']==artifacts['images']['owned']['image_id']
    with pytest.raises(OSError):
        socket.create_connection((value['sentinel_address'], 8787), .5)
    assert not Path('/var/run/docker.sock').exists()
    for name in ('gateway-a', 'gateway-b'):
        assert stack.ready(name)['external_connect_attempts'] == 0
    config = json.loads(stack.config.read_text())
    assert all(s['pull_policy'] == 'never' for s in config['services'].values())
    assert all(not s.get('ports') for s in config['services'].values())
    observe(stack, 'offline-boundary', internal_network=True, runner_egress_denied=True,
            service_egress_denied=True, service_identities=value['services'],runner_identity=value['runner'])


def test_hostname_trust_is_required(gateways):
    import ssl
    from proof.gateway_test_support import PREPARED
    context=ssl.create_default_context(cafile=str(PREPARED/'pki/ca.crt'))
    with socket.create_connection(('gateway-a',4000)) as connection:
        with pytest.raises(ssl.SSLCertVerificationError) as rejected:
            context.wrap_socket(connection,server_hostname='not-a-proof-service')
        assert isinstance(rejected.value,ssl.SSLCertVerificationError)


@pytest.mark.parametrize('fixture', [f for f in FIXTURES if isinstance(f, PiiFixture)], ids=lambda f:f.fixture_id)
def test_pii_corpus_both_gateways(gateways, fixture):
    s = gateways
    s.state(fixture.enforcement_state)
    compared, identities = [], []
    texts = tuple(segment.text for segment in fixture.input_segments.segments)
    expected = tuple(segment.text for segment in (fixture.expected_transformed or fixture.input_segments).segments)
    if fixture.label == 'invalid_span':
        s.fault('invalid_span')
    try:
        for letter in ('a', 'b'):
            s.scenario(fixture.mock_scenario)
            response = s.chat(letter, texts if fixture.stage == 'input' else ('Synthetic clean corpus input.',))
            calls = s.mock()
            assert calls['count'] == int(fixture.expect_model_call)
            if fixture.expected_action == 'block':
                assert response.status_code == 503 and 'choices' not in response.json()
                evidence=decision_evidence(s,response,fixture.stage,letter)
                assert evidence['action']=='block' and evidence['source']=='runtime'
                assert evidence['operational_codes']==list(fixture.expected_operational_codes)
                event=audit_turn(s,response.json()['error']['correlation_id'])[fixture.stage]['metadata']
                assert event['findings']==[] and event['reason_codes']==list(fixture.expected_reason_codes)
                compared.append((evidence['action'],evidence['operational_codes']))
                observe(s,fixture.fixture_id,gateway=letter,stage=fixture.stage,model_calls=calls['count'],**evidence)
                continue
            assert response.status_code == 200
            body = response.json()
            metadata = body['guardrails']
            assert metadata[fixture.stage + '_action'] == fixture.expected_action
            assert metadata[fixture.stage + '_source'] == 'runtime'
            if fixture.stage == 'input':
                assert tuple(m['content'] for m in calls['calls'][0]['messages']) == expected
            else:
                assert body['choices'][0]['message']['content'] == expected[0]
            events = audit_turn(s, metadata['correlation_id'])
            event = events[fixture.stage]
            actual = event['metadata']
            assert actual['findings'] == [f.model_dump(mode='json') for f in fixture.expected_findings]
            assert actual['reason_codes'] == list(fixture.expected_reason_codes)
            assert actual['operational_codes'] == list(fixture.expected_operational_codes)
            assert event['content'] is None and event['workload_id'] == 'gateway-' + letter
            assert actual['policy_digest'] and actual['binding_revision'] >= 1 and actual['snapshot_generation'] >= 1
            assert actual['evaluation_ms'] >= 0
            compared.append((actual['action'], actual['reason_codes'], actual['findings'], expected))
            identities.append((metadata['correlation_id'], metadata[fixture.stage + '_decision_id'], event['event_id']))
            observe(s, fixture.fixture_id, gateway=letter, stage=fixture.stage, action=actual['action'],
                    reason_codes=actual['reason_codes'], finding_count=len(actual['findings']),
                    evaluation_ms=actual['evaluation_ms'], snapshot_generation=actual['snapshot_generation'],
                    binding_revision=actual['binding_revision'], model_calls=calls['count'])
        assert compared[0] == compared[1]
        if identities:
            assert all(a != b for a, b in zip(*identities))
    finally:
        s.fault()


@pytest.mark.parametrize('fixture', [f for f in FIXTURES if isinstance(f, LifecycleFixture)], ids=lambda f:f.fixture_id)
def test_authored_lifecycle(gateways, fixture):
    s = gateways
    workload = fixture.fixture_id
    revision = 0
    binding = PREFIX + '/workloads/' + workload + '/binding'
    for step in fixture.steps:
        body = step.validate_request(s.run_id)
        op = step.operation
        assert op in ('register','publish','bind','inspect','state','update','rollback'), 'unhandled_lifecycle_step'
        if op == 'register':
            body['workload_id'] = workload
        if op == 'publish':
            body['version'] = fixture.interface + '-' + body['version']
        if op in ('bind', 'update'):
            body['policy_version'] = fixture.interface + '-' + body['policy_version']
        if fixture.interface == 'api':
            if op == 'inspect':
                value = s.api.request('GET', binding).json()
                assert value['desired']['revision'] == revision
                assert value['applied_revision'] == revision
                continue
            if op == 'publish':
                s.api.request('POST', PREFIX + '/policies/validate', body=body)
            path = PREFIX + '/workloads' if op == 'register' else PREFIX + '/policies' if op == 'publish' else binding
            if op in ('state', 'rollback'):
                path += '/' + op
            value = s.api.request('PUT' if op in ('bind','update') else 'POST', path, body=body,
                                  status=step.expected_status, revision=revision if op in ('update','state','rollback') else None,
                                  create=op == 'bind').json()
            s.wait(value)
        else:
            common = ['--tenant', 'local-proof', '--workload', workload]
            if op == 'register':
                value = s.cli('workloads','register','--tenant','local-proof','--id',workload,'--name',body['display_name'],
                              '--credential-out', s.root / 'secrets' / (workload + '.token'))
            elif op == 'publish':
                path = s.root / (workload + '-policy.json')
                path.write_text(json.dumps(body))
                s.cli('policies','validate','--tenant','local-proof','--file',path)
                value = s.cli('policies','publish','--tenant','local-proof','--file',path)
            elif op in ('bind','update'):
                value = s.cli('bindings','set',*common,'--policy',body['policy_id'],'--version',body['policy_version'],
                              '--reason',body['reason'], *(['--revision',revision] if revision else []))
            elif op == 'state':
                value = s.cli('bindings','state',*common,'--state',body['enforcement_state'],'--revision',revision,
                              '--reason',body['reason'],'--fixture-run',s.run_id)
            elif op == 'rollback':
                value = s.cli('bindings','rollback',*common,'--to-revision',body['target_revision'],'--revision',revision,'--reason','rollback')
            else:
                value = s.cli('bindings','get',*common)
                assert value['desired']['revision'] == revision and value['applied_revision'] == revision
                continue
            assert value['operation']['status'] == 'applied'
        if op in ('bind','update','state','rollback'):
            revision += 1
            assert value['resource']['revision'] == revision
            if op in ('bind','rollback'):
                assert value['resource']['enforcement_state'] == 'observe'
    history = s.api.request('GET', binding + '/history').json()['items']
    assert len(history) == 4
    assert len({v['generation'] for v in history}) == 4
    assert all(v['updated_by'] for v in history)
    observe(s, fixture.fixture_id, revisions=len(history), applied_generation=max(v['generation'] for v in history))


@pytest.mark.parametrize('invalid', ['retention-string','unsupported-capability','changed-version','retention-authority'])
def test_real_api_cli_rejections_match(gateways, invalid):
    s = gateways
    draft = policy('rejection-' + uuid4().hex[:8])
    actor, status, cli_status = 'admin', 422, 2
    if invalid == 'changed-version':
        s.wait(s.api.request('POST', PREFIX+'/policies',body=draft,status=201).json())
        draft['checks'][0]['requested_action'] = 'redact'
        status, cli_status = 409, 4
    elif invalid == 'retention-string':
        draft['retain_content'] = 'false'
    elif invalid == 'unsupported-capability':
        draft['checks'][0]['evaluator'] = 'unsupported'
    else:
        draft['retain_content'] = True
        actor, status, cli_status = 'operator', 403, 3
    path = s.root / ('negative-'+invalid+'.json')
    path.write_text(json.dumps(draft))
    direct = s.api.request('POST', PREFIX+'/policies',body=draft,status=status,actor=actor).json()
    via_cli = s.cli('policies','publish','--tenant','local-proof','--file',path,status=cli_status,actor=actor)
    assert direct['code'] == via_cli['code']
    assert 'false' not in json.dumps(direct) if invalid == 'retention-string' else True


def test_real_identity_and_management_scope(gateways):
    from proof.runtime_test_support import request_body
    s = gateways
    for letter in ('a','b'):
        name = 'gateway-'+letter
        value = request_body('Synthetic identity fixture.',workload=name)
        response = s.client.post(s.url('runtime')+'/v1/evaluate',headers=s.headers(name),json=value)
        assert response.status_code == 200 and response.json()['workload_id'] == name
        value['workload_id'] = 'gateway-'+('b' if letter=='a' else 'a')
        assert s.client.post(s.url('runtime')+'/v1/evaluate',headers=s.headers(name),json=value).status_code == 403
        s.api.request('GET',PREFIX+'/workloads',actor=name,status=403)
    s.api.request('GET','/v1/tenants/foreign/workloads',actor='operator',status=404)
    s.fixture_content.add('Synthetic identity fixture.')


@pytest.mark.parametrize('fixture', [f for f in FIXTURES if isinstance(f, FailureFixture)], ids=lambda f:f.fixture_id)
def test_authored_failure_boundaries(gateways, fixture):
    s = gateways
    behavior = 'block' if fixture.policy_alias == 'closed' else 'allow_and_log'
    draft = policy('failure-' + uuid4().hex[:12])
    draft['failure_behavior'] = dict.fromkeys(draft['failure_behavior'], behavior)
    s.bind_policy(draft)
    s.state(fixture.enforcement_state)
    for letter in ('a', 'b'):
        s.scenario('delayed_sensitive' if fixture.stage == 'output' else 'sensitive')
        lock = None
        stopped = False
        def activate():
            nonlocal lock, stopped
            if fixture.failure_location == 'runtime_down':
                s.compose('stop', '-t', '0', 'runtime')
                stopped = True
            elif fixture.failure_location == 'audit_write':
                lock = sqlite3.connect(s.root / 'state/live-runtime/audit.sqlite3')
                lock.execute('BEGIN IMMEDIATE')
            elif fixture.failure_location == 'malformed_response':
                result = s.client.put(s.url('runtime') + '/proof/fault', headers=s.headers('control'),
                    json={'worker':None,'response':'malformed','stage':fixture.stage})
                assert result.status_code == 200
            else:
                s.fault('stall' if fixture.failure_location == 'worker_timeout' else 'invalid_span')
        started = time.monotonic()
        try:
            if fixture.stage == 'input':
                activate()
                result = s.chat(letter, ('Synthetic fault input.',))
            else:
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pending = pool.submit(s.chat, letter, ('Synthetic fault input.',))
                    deadline = time.monotonic() + 1.5
                    while s.mock()['count'] == 0:
                        assert time.monotonic() < deadline
                        time.sleep(.02)
                    activate()
                    assert not pending.done()
                    result = pending.result(timeout=10)
            assert s.mock()['count'] == int(fixture.expect_model_call)
            assert result.status_code == (200 if fixture.expect_delivery else 503)
            if fixture.expect_delivery:
                value = result.json()
                assert value['choices'][0]['message']['content'] == 'Contact proof@example.test or +15551234567.'
                meta = value['guardrails']
                assert meta[fixture.stage + '_action'] == fixture.expected_action
                assert meta[fixture.stage + '_source'] == fixture.expected_source
                assert fixture.expected_operational_code in meta['operational_codes']
            else:
                assert 'choices' not in result.json()
                assert 'proof@example.test' not in result.text
            evidence=decision_evidence(s,result,fixture.stage,letter)
            assert evidence['action']==fixture.expected_action and evidence['source']==fixture.expected_source
            assert fixture.expected_operational_code in evidence['operational_codes']
            observe(s, fixture.fixture_id, gateway=letter, elapsed_s=time.monotonic()-started,
                    model_calls=s.mock()['count'],delivered='choices' in result.json(),**evidence)
        finally:
            if lock:
                lock.rollback()
                lock.close()
            if stopped:
                s.compose('start','runtime')
                s.ready('runtime')
                s.wait_initial_receipt()
            s.fault()
    s.bind_policy(policy('restored-' + uuid4().hex[:12]))


def test_owner_cannot_weaken_mandatory_floor_through_api_or_cli(gateways):
    from proof.management_test_support import floor
    s=gateways
    constraint=floor(minimum_action='block')
    s.wait(s.api.request('PUT','/v1/governance/floors/tenant/local-proof',body=constraint,create=True,status=202).json())
    path=PREFIX+'/workloads/gateway-a/binding'
    before=s.binding('a')
    body=envelope(enforcement_state='disabled',reason='synthetic_demotion',evidence_kind='synthetic_local_proof',fixture_run_id=s.run_id)
    direct=s.api.request('POST',path+'/state',body=body,revision=before['revision'],actor='operator',status=409).json()
    via_cli=s.cli('bindings','state','--tenant','local-proof','--workload','gateway-a','--state','disabled',
        '--revision',before['revision'],'--reason','synthetic_demotion','--fixture-run',s.run_id,actor='operator',status=4)
    assert direct['code']==via_cli['code']=='mandatory_floor_violation'
    history=s.api.request('GET',path+'/history').json()['items']
    # History is paged: the earlier state matrix's Disabled revision is required.
    cursor=s.api.request('GET',path+'/history').json()['next_cursor']
    while cursor:
        page=s.api.request('GET',path+'/history',params={'cursor':cursor}).json()
        history+=page['items']
        cursor=page['next_cursor']
    disabled=next(item for item in history if item['enforcement_state']=='disabled')
    direct=s.api.request('POST',path+'/rollback',body=envelope(target_revision=disabled['revision'],reason='rollback'),
        revision=before['revision'],actor='operator',status=409).json()
    via_cli=s.cli('bindings','rollback','--tenant','local-proof','--workload','gateway-a','--to-revision',disabled['revision'],
        '--revision',before['revision'],'--reason','rollback',actor='operator',status=4)
    assert direct['code']==via_cli['code']=='mandatory_floor_violation'
    weak=policy('weaker-'+uuid4().hex[:8])
    weak['checks'][0]['requested_action']='log_only'
    s.wait(s.api.request('POST',PREFIX+'/policies',body=weak,actor='operator',status=201).json())
    direct=s.api.request('PUT',path,body=envelope(policy_id='pii',policy_version=weak['version'],reason='policy_update'),
        revision=before['revision'],actor='operator',status=409).json()
    via_cli=s.cli('bindings','set','--tenant','local-proof','--workload','gateway-a','--policy','pii','--version',weak['version'],
        '--revision',before['revision'],'--reason','policy_update',actor='operator',status=4)
    assert direct['code']==via_cli['code']=='mandatory_floor_violation'
    assert s.binding('a')==before
    direct=s.api.request('PUT','/v1/governance/floors/tenant/local-proof',body=constraint,revision=1,actor='operator',status=403).json()
    constraint_file=s.root/'floor-authority.json'
    constraint_file.write_text(json.dumps(constraint))
    via_cli=s.cli('floors','set','--scope','tenant','--scope-id','local-proof','--file',constraint_file,
        '--revision',1,actor='operator',status=3)
    assert direct['code']==via_cli['code']=='identity_mismatch'
    observe(s,'mandatory-floor-owner',rejected_mutations=8,binding_revision=before['revision'],floor_applied=True)


def test_final_privacy_and_readback(gateways):
    s = gateways
    from proof.mock_model import SCENARIOS
    from proof.privacy import TRAFFIC, SECRETS, unsafe, input_file, retained_database
    prohibited = set(s.fixture_content) | set(SCENARIOS.values()) | TRAFFIC | SECRETS
    prohibited |= {segment.text for fixture in FIXTURES if isinstance(fixture, PiiFixture) for segment in fixture.input_segments.segments}
    prohibited |= {p.read_text().strip() for p in (s.root/'secrets').glob('*.token')}
    for service in ('management','runtime','mock-model','gateway-a','gateway-b'):
        result = s.compose('logs','--no-color',service)
        path = s.root / (service+'.log')
        path.write_text(result.stdout + result.stderr)
        path.chmod(0o600)
    scanned, excluded = [], []
    for path in s.root.rglob('*'):
        if not path.is_file():
            continue
        # Only authored configuration/credentials and the separate opt-in
        # database are excluded; unexpected generated file types are scanned.
        if input_file(path,s.root) or retained_database(path):
            excluded.append(str(path.relative_to(s.root)))
            continue
        raw = path.read_bytes()
        assert not unsafe(raw,prohibited), 'default_privacy_failed'
        scanned.append(str(path.relative_to(s.root)))
        if path.suffix == '.sqlite3':
            with sqlite3.connect(path) as connection:
                assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert scanned
    assert s.mock('DELETE')['cleared'] and s.mock()['count'] == 0
    health = {name:s.ready(name) for name in ('gateway-a','gateway-b','runtime')}
    assert all(health[n]['external_connect_attempts'] == 0 for n in ('gateway-a','gateway-b'))
    observe(s, 'final-privacy', scanned_files=scanned, health=health, volatile_spy_cleared=True)
    report = Path(os.environ['PROOF_ACCEPTANCE_REPORT'])
    write_safe(report/'observations.json', s.observations, prohibited)
    write_safe(report/'privacy.json', dict(passed=True, scanned_files=scanned,
        exclusions=excluded), prohibited)
    TRAFFIC.update(s.fixture_content)
    TRAFFIC.update(segment.text for fixture in FIXTURES if isinstance(fixture,PiiFixture) for segment in fixture.input_segments.segments)
    TRAFFIC.update(SCENARIOS.values())
    SECRETS.update(p.read_text().strip() for p in s.root.rglob('*.token'))
