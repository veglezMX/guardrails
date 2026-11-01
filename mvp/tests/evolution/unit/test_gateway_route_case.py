"""Pure/injected parent recovery checks; no Docker, HTTP, process signals or models."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from evolution import gateway_route_case as route
from evolution import run
from evolution.dependencies import canonical
from evolution.registry import Case, EvidenceError

NODE = ('mvp/tests/evolution/e2e/test_cluster_evaluator_gateways.py::'
        'test_deployed_gateway_engine_matrix_exact_dispatch_transformation_and_attribution'
        '[observe-input-pii-team-a]')
POLICY = {'tenant_id': 'team-a', 'policy_id': 'original', 'version': 1, 'digest': 'a' * 64}
TIME = '2026-10-13T00:00:00Z'


def case(node=NODE, number=1):
    return Case(f'ROUTE-{number}', 'P3.1', 'local', 'remote', 'matrix', node, ('exact behavior',), 300)


def saved(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return route.write(path, value)


@pytest.fixture(autouse=True)
def no_latch_between_tests(monkeypatch):
    monkeypatch.setattr(route, '_RECOVERY_LATCHED', False)
    monkeypatch.delenv(route.OPERATION_ENV, raising=False)


@pytest.mark.parametrize('node,selected', [
    (NODE, True), (NODE.replace('team-a', 'team-b'), True),
    (NODE.replace('observe', 'disabled'), False), (NODE + 'suffix', False),
    (NODE.replace('e2e/', 'unit/'), False), (NODE.split('[')[0], False),
    ('mvp/tests/evolution/unit/test_runner.py::test_case', False),
])
def test_exact_case_family(node, selected):
    assert route.applies(node) is selected


def governance(tmp_path):
    operation = str(uuid4()); root = tmp_path / 'source'; root.mkdir()
    source = root / 'mvp/helper.py'; source.parent.mkdir(); source.write_text('source\n')
    sources = {'mvp/helper.py': hashlib.sha256(source.read_bytes()).hexdigest()}
    old = {'tenant_id': 'team-a', 'workload_id': 'chat', 'policy': POLICY, 'state': 'observe',
           'revision': 7, 'release_id': str(uuid4()), 'previous_revision': 6,
           'activated_by': str(uuid4()), 'status': 'pending', 'effective_after': TIME, 'created_at': TIME}
    actors = {name: str(uuid4()) for name in ('tenant_admin', 'author', 'approver', 'operator', 'auditor')}
    intent = {'schema_version': 'evolution-governance-attempt-v1', 'operation': operation,
              'tenant_id': 'team-a', 'engine': 'pii', 'stage': 'input', 'state': 'observe',
              'original_binding': old, 'original_policy': POLICY, 'actors': actors,
              'source_sha256': sources, 'status': 'intent_only_no_mutation_claim'}
    parent = tmp_path / 'operations'; parent.mkdir(mode=0o700)
    path = parent / operation; path.mkdir(mode=0o700)
    saved(path / 'intent.json', intent)
    sealed = {'operation': operation, 'tenant': 'team-a', 'original_binding': old, 'original_policy': POLICY}
    return SimpleNamespace(root=root, sources=sources, parent=parent, path=path, intent=intent, sealed=sealed)


def checked(g):
    return route.verify_governance_journal(g.sealed, g.parent, g.sources, g.root)


def test_original_full_binding_and_source_are_bound(tmp_path):
    g = governance(tmp_path)
    assert checked(g)['binding_revision'] == 7
    g.sealed = copy.deepcopy(g.sealed); g.sealed['original_binding']['release_id'] = str(uuid4())
    with pytest.raises(EvidenceError, match=route.RECOVERY_REQUIRED):
        checked(g)


def test_unknown_post_refuses_without_replay(tmp_path):
    g = governance(tmp_path)
    saved(g.path / 'request-01-intent.json', {'operation': g.intent['operation'], 'method': 'POST'})
    with pytest.raises(FileNotFoundError):
        checked(g)
    saved(g.path / 'request-01-returned.json', {'operation': g.intent['operation'],
        'status': 'port_returned_expected_status_not_application_proof'})
    assert checked(g)['binding_revision'] == 7


def test_restore_handle_must_follow_exact_owned_matrix_revision(tmp_path):
    g = governance(tmp_path); op = g.intent['operation']
    matrix = {'operation': op, 'label': 'matrix-release', 'previous_revision': 7, 'binding_revision': 8}
    restored = {'operation': op, 'label': 'restore-original-binding',
        'actor_id': g.intent['actors']['operator'], 'tenant_id': 'team-a', 'workload_id': 'chat',
        'policy': POLICY, 'state': 'observe', 'previous_revision': 8, 'binding_revision': 9,
        'release_id': str(uuid4())}
    saved(g.path / 'release-matrix-release.json', {'handle': matrix})
    saved(g.path / 'release-restore-original-binding.json', {
        'schema_version': 'evolution-governance-release-handle-v1',
        'status': 'acknowledged_not_yet_applied', 'handle': restored})
    assert checked(g)['binding_revision'] == 9
    g.sealed = copy.deepcopy(g.sealed); g.sealed['original_binding']['revision'] = 6
    with pytest.raises(EvidenceError):
        checked(g)


def observations():
    expected = {'tenant_id': 'team-a', 'workload_id': 'chat', 'policy': POLICY, 'state': 'observe',
        'binding_revision': 9, 'previous_revision': 8, 'release_id': str(uuid4()), 'actor_id': str(uuid4())}
    workload = {'tenant_id': 'team-a', 'workload_id': 'chat', 'desired_binding': {'binding_revision': 9},
                'applied_binding': {'binding_revision': 9, 'policy': POLICY}}
    release = {'release_id': expected['release_id'], 'tenant_id': 'team-a', 'policy': POLICY,
        'workload_ids': ['chat'], 'stage': 'canary', 'state': 'observe', 'expected_revisions': {'chat': 8},
        'status': 'applied', 'actor_id': expected['actor_id'], 'reason': 'Restored original binding', 'created_at': TIME}
    runtimes = [str(uuid4()), str(uuid4())]
    receipts = [{'runtime_id': runtime, 'tenant_id': 'team-a', 'workload_id': 'chat', 'binding_revision': 9,
        'snapshot_generation': 15, 'digest': POLICY['digest'], 'applied_at': TIME, 'created_at': TIME,
        'health': 'ready'} for runtime in runtimes]
    return expected, workload, {'release': release, 'receipts': receipts}, runtimes


def test_exact_current_pointers_release_and_both_receipts_are_required():
    values = observations()
    result = route.verify_binding_observation(*values)
    assert result['revision'] == 9 and len(result['runtime_receipts']) == 2
    assert result['release_id'] == values[0]['release_id']


@pytest.mark.parametrize('field', ['desired', 'applied', 'policy', 'state', 'actor', 'release',
                                  'one_receipt', 'latest_failed', 'conflicting_generation'])
def test_unrelated_binding_or_receipt_cannot_authorize_route_removal(field):
    expected, workload, observed, runtimes = observations()
    if field == 'desired': workload['desired_binding']['binding_revision'] = 10
    elif field == 'applied': workload['applied_binding']['binding_revision'] = 10
    elif field == 'policy': workload['applied_binding']['policy'] = {**POLICY, 'digest': 'b' * 64}
    elif field == 'state': observed['release']['state'] = 'warn'
    elif field == 'actor': observed['release']['actor_id'] = str(uuid4())
    elif field == 'release': observed['release']['release_id'] = str(uuid4())
    elif field == 'one_receipt': observed['receipts'].pop()
    elif field == 'latest_failed': observed['receipts'].append({**observed['receipts'][0], 'snapshot_generation': 16, 'health': 'failed'})
    else: observed['receipts'].append({**observed['receipts'][0], 'health': 'failed'})
    with pytest.raises(EvidenceError, match=route.RECOVERY_REQUIRED):
        route.verify_binding_observation(expected, workload, observed, runtimes)


def test_reused_pid_is_never_signaled(monkeypatch):
    monkeypatch.setattr(route, 'identity', lambda pid, deadline: {'pid': pid, 'start': 'new'})
    monkeypatch.setattr(route.os, 'pidfd_open', lambda *a: pytest.fail('reused PID was opened'))
    route.stop_recorded({'pid': 42, 'start': 'old'}, 50)


def test_unknown_adopted_child_blocks_without_signal(monkeypatch, tmp_path):
    worker = {'pid': 41, 'start': '100', 'group': 41}
    monkeypatch.setattr(route, 'stop_recorded', lambda *a: None)
    monkeypatch.setattr(route, 'native_records', lambda *a: [])
    monkeypatch.setattr(route, 'children', lambda *a: {42: {'pid': 42, 'start': '200', 'command': 'x'}})
    monkeypatch.setattr(route, 'identity', lambda *a: None)
    with pytest.raises(EvidenceError):
        route.close_native(tmp_path, worker, {}, 50)


def test_owned_forward_gate_can_reach_independent_restoration(monkeypatch, tmp_path):
    worker = {'pid': 41, 'start': '100', 'group': 41}
    forward = {'pid': 42, 'start': '200', 'group': 42, 'forward': True, 'command': 'exec', 'gate_command': 'gate'}
    stopped = []
    monkeypatch.setattr(route, 'stop_recorded', lambda row, deadline: stopped.append(row['pid']))
    monkeypatch.setattr(route, 'native_records', lambda *a: [forward])
    monkeypatch.setattr(route, 'children', lambda *a: {42: {**forward, 'command': 'gate'}})
    monkeypatch.setattr(route, 'identity', lambda *a: None)
    proof = route.close_native(tmp_path, worker, {}, 50)
    assert proof['native_nonforward_closed'] and stopped == [41]


def test_original_worker_is_closed_before_final_native_ledger_read(monkeypatch, tmp_path):
    seen = []
    worker = {'pid': 41, 'start': '100', 'group': 41}
    monkeypatch.setattr(route, 'stop_recorded', lambda *a: seen.append('stop'))
    monkeypatch.setattr(route, 'native_records', lambda *a: seen.append('ledger') or [])
    monkeypatch.setattr(route, 'children', lambda *a: {})
    monkeypatch.setattr(route, 'identity', lambda *a: None)
    route.close_native(tmp_path, worker, {}, 50)
    assert seen == ['stop', 'ledger']


@pytest.mark.parametrize('error', [KeyboardInterrupt, SystemExit, OSError])
def test_runner_interruption_always_finalizes_and_latches(monkeypatch, tmp_path, error):
    calls = []; owned = SimpleNamespace(envelope_ref={'path': 'private'},
        finish=lambda process, passed: calls.append(('finish', passed)) or False)
    def execute(*a, route_lifecycle, **kw):
        route_lifecycle.update(route=owned, process=object())
        raise error()
    monkeypatch.setattr(run, '_execute_case', execute)
    monkeypatch.setattr(run, 'latch_route_recovery', lambda **kw: calls.append(('latch', None)))
    with pytest.raises(error):
        run.execute_case(case(), tmp_path, tmp_path)
    assert calls == [('latch', None), ('finish', False)]


def test_cleanup_error_does_not_replace_original_interruption(monkeypatch, tmp_path):
    calls = []
    def failed_finish(*a, **kw): raise OSError('private value')
    owned = SimpleNamespace(envelope_ref={}, finish=failed_finish)
    def execute(*a, route_lifecycle, **kw):
        route_lifecycle.update(route=owned, process=object()); raise KeyboardInterrupt()
    monkeypatch.setattr(run, '_execute_case', execute)
    monkeypatch.setattr(run, 'latch_route_recovery', lambda **kw: calls.append('latch'))
    with pytest.raises(KeyboardInterrupt): run.execute_case(case(), tmp_path, tmp_path)
    assert calls == ['latch', 'latch']


def test_remaining_required_inventory_is_failed_without_dispatch(monkeypatch, tmp_path):
    cases = [case(number=number) for number in range(1, 4)]; calls = []
    def failed(value, *a, **kw):
        calls.append(value.case_id)
        return {'case_id': value.case_id, 'requirement_id': value.requirement_id, 'status': 'fail',
                'duration_ms': 1, 'assertions': 2, 'reason_codes': [route.RECOVERY_REQUIRED]}
    monkeypatch.setattr(run, 'execute_case', failed)
    result = run.execute_cases(cases, tmp_path, tmp_path, sources={})
    assert calls == ['ROUTE-1'] and [row['case_id'] for row in result] == [row.case_id for row in cases]
    assert all(row['status'] == 'fail' for row in result)
    assert all(row['assertions'] == 0 and row['duration_ms'] == 0
               and row['reason_codes'] == ['not_executed_due_to_gateway_route_recovery'] for row in result[1:])


def test_private_journal_rejects_symlink(tmp_path):
    value = tmp_path / 'value.json'; saved(value, {'only': 'metadata'})
    link = tmp_path / 'link.json'; link.symlink_to(value)
    with pytest.raises(EvidenceError): route.read(link)


def test_latch_is_persistent_and_never_silently_removed(monkeypatch, tmp_path):
    home = tmp_path / 'home'; parent = home / '.agent-sessions' / 'operation'
    parent.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(Path, 'home', lambda: home)
    env = {route.OPERATION_ENV: str(parent)}
    route.latch_recovery(env, envelope={'path': 'owned'})
    monkeypatch.setattr(route, '_RECOVERY_LATCHED', False)
    assert route.recovery_latched(env)
    assert route.document(parent / 'gateway-route-recovery-required.json')['gate_pass'] is False


def test_two_normal_cases_receive_exact_source_catalog(monkeypatch, tmp_path):
    cases = [case('mvp/tests/evolution/unit/test_fixture.py::test_example', number=n) for n in (1, 2)]
    sources = {'mvp/example.py': 'a' * 64}; seen = []
    def passed(value, *a, sources, **kw):
        seen.append((value.case_id, sources))
        return {'case_id': value.case_id, 'requirement_id': value.requirement_id, 'status': 'pass',
                'duration_ms': 1, 'assertions': 1, 'reason_codes': []}
    monkeypatch.setattr(run, 'execute_case', passed)
    result = run.execute_cases(cases, tmp_path, tmp_path, sources=sources)
    assert seen == [('ROUTE-1', sources), ('ROUTE-2', sources)]
    assert len(result) == 2 and all(row['status'] == 'pass' for row in result)


def test_unexpected_route_finalizer_error_retains_every_required_row(monkeypatch, tmp_path):
    calls = []
    def failed(value, *a, **kw): calls.append(value.case_id); raise OSError('private failure')
    monkeypatch.setattr(run, 'execute_case', failed)
    monkeypatch.setattr(run, 'latch_route_recovery', lambda: calls.append('latch'))
    result = run.execute_cases([case(number=n) for n in (1, 2)], tmp_path, tmp_path, sources={})
    assert calls == ['ROUTE-1', 'latch'] and len(result) == 2
    assert result[0]['reason_codes'] == [route.RECOVERY_REQUIRED]
    assert result[1]['reason_codes'] == ['not_executed_due_to_gateway_route_recovery']


@pytest.mark.parametrize('change', ['added_python', 'added_resource', 'symlink'])
def test_deferred_finalizer_source_guard_rejects_added_or_redirected_sources(monkeypatch, tmp_path, change):
    import evolution.diagnostics as diagnostics
    from evolution.dependencies import digest
    source = tmp_path / 'mvp/a.py'; source.parent.mkdir(); source.write_text('one\n')
    base = {'mvp/a.py': digest(source)}
    envelope = {'root': str(tmp_path), 'sources': base, 'source_inventory': base}
    monkeypatch.setattr(diagnostics, 'source_snapshot', lambda root: base if change != 'added_python' else {**base, 'mvp/b.py': 'b' * 64})
    if change == 'symlink':
        def rejected(root): raise EvidenceError('source_symlink_rejected')
        monkeypatch.setattr(run, 'source_files', rejected)
    else:
        monkeypatch.setattr(run, 'source_files', lambda root: base if change != 'added_resource' else {**base, 'deploy/extra.json': 'c' * 64})
    with pytest.raises(EvidenceError): route.source_guard(envelope)


def test_no_registered_governance_or_route_cannot_upgrade_passed_case(monkeypatch, tmp_path):
    owned = object.__new__(route.RouteCase)
    owned.path = tmp_path; owned.route_root = tmp_path / 'routes'; owned.route_root.mkdir(mode=0o700)
    owned.root = tmp_path; owned.envelope = {}; owned.envelope_ref = {'path': 'envelope', 'sha256': 'a' * 64}
    owned.worker = {'pid': 41, 'start': '1', 'group': 41}; owned.baseline = {}; owned.previous_subreaper = None
    child = SimpleNamespace(returncode=0, wait=lambda **kw: 0)
    owned.close_worker = lambda process: None
    owned._spawn = lambda *a, **kw: (child, {'pid': 42, 'start': '2', 'group': 42})
    monkeypatch.setattr(route, 'native_records', lambda *a: [])
    monkeypatch.setattr(route, 'stop_recorded', lambda *a: None)
    monkeypatch.setattr(route, 'children', lambda *a: {})
    monkeypatch.setattr(route, 'source_guard', lambda *a: None)
    saved(tmp_path / 'cleanup-result.json', {'status': 'not_staged', 'gate_pass': False, 'route': {}})
    assert owned.finish(child, passed=True) is False
    assert route.document(tmp_path / 'completion.json')['recovery_required'] is True


def test_failed_case_remains_failed_even_if_independent_route_restore_succeeds(monkeypatch, tmp_path):
    # The central result is fixed before finalization; finish returns only a
    # recovery admission, never a new passing case result.
    owned = object.__new__(route.RouteCase)
    owned.path = tmp_path; owned.route_root = tmp_path / 'routes'; owned.route_root.mkdir(mode=0o700)
    owned.root = tmp_path; owned.envelope = {}; owned.envelope_ref = {'path': 'envelope', 'sha256': 'a' * 64}
    owned.worker = {'pid': 41, 'start': '1', 'group': 41}; owned.baseline = {}; owned.previous_subreaper = None
    child = SimpleNamespace(returncode=0, wait=lambda **kw: 0)
    owned.close_worker = lambda process: None
    owned._spawn = lambda *a, **kw: (child, {'pid': 42, 'start': '2', 'group': 42})
    monkeypatch.setattr(route, 'native_records', lambda *a: [])
    monkeypatch.setattr(route, 'stop_recorded', lambda *a: None)
    monkeypatch.setattr(route, 'children', lambda *a: {})
    monkeypatch.setattr(route, 'source_guard', lambda *a: None)
    saved(tmp_path / 'cleanup-result.json', {'status': 'restored', 'gate_pass': False, 'route': {}})
    assert owned.finish(child, passed=False) is True
    completion = route.document(tmp_path / 'completion.json')
    assert completion['case_passed_before_cleanup'] is False and completion['gate_pass'] is False


@pytest.fixture
def renewed_binding(tmp_path, monkeypatch):
    """Actual completed renewal journal; HTTP responses use existing dispatch."""
    import base64
    from contextlib import nullcontext
    from mvp.tests.evolution.unit import test_credentials as fixture
    value = fixture.saved.__wrapped__(tmp_path)
    directory, owner, historical, journal, _ = value
    state = directory.parent
    owner.update(profile='guardrails-evolution', uid=os.getuid(), state=str(state))
    fixture.private(state / 'owner.json', owner)
    ca = state / 'credentials/ca.crt'; fixture.private(ca, b'fixture CA, no network\n')
    fields = {'management_url': 'https://localhost:18443', 'ca_file': str(ca),
              'seed_inventory_file': str(directory / 'seed/inventory.json')}
    access = fixture.credentials._read(directory / 'access.json'); access.update(fields)
    fixture.private(directory / 'access.json', access)
    metadata = journal['metadata']['access.json']
    for key in ('before', 'after'):
        decoded = fixture.credentials.strict_json_loads(base64.b64decode(metadata[key]))
        decoded.update(fields)
        metadata[key] = base64.b64encode(canonical(decoded)).decode()
    fixture.publish(value)
    deployment = SimpleNamespace(state=state, lock=nullcontext,
        support=lambda name: fixture.credentials if name == 'credentials' else pytest.fail('wrong support'),
        owner=lambda: pytest.fail('credential resolution must not run owner/native inspection'))
    monkeypatch.setattr(route, 'load_deployment', lambda envelope: deployment)
    monkeypatch.setattr(route, 'source_guard', lambda envelope: None)
    expected, workload, observed, _ = observations()
    tenant = historical['tenants']['team-a']; workload_id = tenant['workload']['workload_id']
    expected['workload_id'] = workload_id; workload['workload_id'] = workload_id
    observed['release']['workload_ids'] = [workload_id]
    observed['release']['expected_revisions'] = {workload_id: expected['previous_revision']}
    runtimes = [row['runtime_id'] for row in tenant['runtimes'].values()]
    for receipt, runtime in zip(observed['receipts'], runtimes, strict=True):
        receipt.update(workload_id=workload_id, runtime_id=runtime)
    envelope = {'state': str(state), 'root': str(tmp_path), 'operation_root': str(tmp_path), 'sources': {}}
    inputs = route.credential_inputs(envelope)
    envelope.update(private_inputs=inputs['private_inputs'], credential_generation=inputs['credential_generation'])
    monkeypatch.setattr(route, 'verify_governance_journal', lambda *args: expected)
    return SimpleNamespace(fixture=fixture, saved=value, envelope=envelope, expected=expected,
        workload=workload, observed=observed, runtimes=runtimes, inputs=inputs,
        intent={'tenant': 'team-a', 'runtime_ids': sorted(runtimes)})


def management_transport(value, *, after=None):
    """Authenticate fixture generation, then exercise real management GET dispatch."""
    import httpx
    from guardrails.evolution.identity import AuthenticatedIdentity
    from guardrails.evolution.management_api import ManagementApi
    tenant = value.inputs['inventory']['tenants']['team-a']
    token = Path(tenant['roles']['operator']['token_file']).read_text().strip()
    principal = {'principal_id': tenant['roles']['operator']['principal_id'], 'roles': ['operator'],
                 'tenant_id': 'team-a', 'status': 'active'}
    identity = AuthenticatedIdentity(principal, {}, 'team-a', None)
    service = ManagementApi.__new__(ManagementApi)
    class Transaction:
        tenant_id = 'team-a'
        def get(self, kind, key, *, required=False):
            records = {'releases': value.observed['release'], 'workloads': value.workload,
                'binding_pointers': value.workload['desired_binding'],
                'applied_bindings': value.workload['applied_binding']}
            assert key == (value.expected['release_id'] if kind == 'releases' else value.expected['workload_id'])
            return records[kind]
        def page(self, kind, *, limit, after, filters):
            assert kind == 'receipts' and limit == 100 and after is None
            assert filters == {'workload_id': value.expected['workload_id'],
                               'binding_revision': value.expected['binding_revision']}
            return [{'data': row} for row in value.observed['receipts']]
    seen = []
    def handle(request):
        seen.append((request.method, request.url.path))
        assert request.method == 'GET' and request.url.host == 'localhost' and request.url.port == 18443
        assert request.headers['accept-encoding'] == 'identity'
        if request.headers.get('authorization') != 'Bearer ' + token:
            return httpx.Response(401, json={'error': {'code': 'unauthorized'}})
        parts = request.url.path.removeprefix('/v2/tenants/team-a/').split('/')
        body, status = service.dispatch(Transaction(), identity, 'GET', parts, {}, {}, None)
        if after: after(len(seen))
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handle), seen


def install_management_transport(monkeypatch, value, *, after=None):
    import httpx
    client = httpx.Client
    transport, seen = management_transport(value, after=after)
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return client(**{**kwargs, 'verify': False, 'transport': transport})
    monkeypatch.setattr(httpx, 'Client', factory)
    monkeypatch.setattr(route.ssl, 'create_default_context', lambda **kwargs: None)
    return seen


def test_binding_verifier_uses_completed_renewal_with_existing_management_gets(renewed_binding, monkeypatch):
    value = renewed_binding
    old = value.saved[2]['tenants']['team-a']['roles']['operator']['token_file']
    current = value.inputs['inventory']['tenants']['team-a']['roles']['operator']['token_file']
    assert old != current and str(old) not in value.envelope['private_inputs']
    assert str(current) in value.envelope['private_inputs']
    seen = install_management_transport(monkeypatch, value)
    result = route.binding_verifier(value.envelope, value.intent, route.time.monotonic() + 30)
    assert result['verified_original_binding'] and result['revision'] == value.expected['binding_revision']
    assert len(result['runtime_receipts']) == 2
    assert seen == [('GET', '/v2/tenants/team-a/releases/' + value.expected['release_id']),
                    ('GET', '/v2/tenants/team-a/workloads/' + value.expected['workload_id'])]


def test_pending_credential_renewal_refuses_before_any_management_get(renewed_binding, monkeypatch):
    value = renewed_binding; seen = install_management_transport(monkeypatch, value)
    value.fixture.private(value.saved[0] / 'credential-renewals/pending.json', {'pending': True})
    with pytest.raises(value.fixture.credentials.CredentialRenewalError, match='credential_renewal_pending'):
        route.binding_verifier(value.envelope, value.intent, route.time.monotonic() + 30)
    assert seen == []


def test_changed_current_operator_file_refuses_before_any_management_get(renewed_binding, monkeypatch):
    value = renewed_binding; seen = install_management_transport(monkeypatch, value)
    token = Path(value.inputs['inventory']['tenants']['team-a']['roles']['operator']['token_file'])
    token.write_text(token.read_text() + '\n')
    with pytest.raises((EvidenceError, value.fixture.credentials.CredentialRenewalError)):
        route.binding_verifier(value.envelope, value.intent, route.time.monotonic() + 30)
    assert seen == []


def test_generation_change_during_management_gets_cannot_authorize_cleanup(renewed_binding, monkeypatch):
    value = renewed_binding
    def change(number):
        if number == 2:
            value.fixture.private(value.saved[0] / 'credential-renewals/pending.json', {'pending': True})
    seen = install_management_transport(monkeypatch, value, after=change)
    with pytest.raises(value.fixture.credentials.CredentialRenewalError, match='credential_renewal_pending'):
        route.binding_verifier(value.envelope, value.intent, route.time.monotonic() + 30)
    assert len(seen) == 2
