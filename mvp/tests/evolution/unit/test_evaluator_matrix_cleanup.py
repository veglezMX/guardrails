"""Isolated resource-lifecycle faults in the actual matrix entrypoints.

HTTP/governance ports below are synthetic; these checks do not claim an actual
matrix result, model readiness, release acknowledgement, or deployment proof.
"""
from __future__ import annotations

import importlib.util
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from guardrails.evolution import models
from guardrails.evolution.compiler import PolicyCompiler

ROOT = Path(__file__).resolve().parents[4]
E2E = ROOT / 'mvp/tests/evolution/e2e'


def module(monkeypatch, name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, value)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def source(monkeypatch):
    monkeypatch.syspath_prepend(str(E2E))
    return module(monkeypatch, 'evaluator_matrix_support', E2E / 'evaluator_matrix_support.py')


class Failure(RuntimeError):
    pass


class Resource:
    def __init__(self, trace, name, fail=False):
        self.trace, self.name, self.fail = trace, name, fail

    def close(self):
        self.trace.append(self.name)
        if self.fail:
            raise Failure('private exception detail must not become a cleanup note')


@pytest.mark.parametrize('http_fail,repo_fail', [(False, False), (True, False), (False, True), (True, True)])
def test_close_attempts_both_once_and_has_finite_error(source, http_fail, repo_fail):
    trace = []
    cluster = object.__new__(source.EvaluatorCluster)
    cluster.http, cluster.repo = Resource(trace, 'http', http_fail), Resource(trace, 'repo', repo_fail)
    if http_fail or repo_fail:
        with pytest.raises(RuntimeError, match='^evaluator_cluster_cleanup_failed$'):
            cluster.close()
    else:
        cluster.close()
    assert trace == ['http', 'repo']
    assert cluster.http is cluster.repo is None
    cluster.close()
    assert trace == ['http', 'repo']


@pytest.mark.parametrize('resource', ['http', 'repo', 'neither'])
def test_partial_construction_cleanup_only_closes_created_resources(source, resource):
    trace = []
    cluster = object.__new__(source.EvaluatorCluster)
    if resource != 'neither':
        setattr(cluster, resource, Resource(trace, resource))
    cluster.close()
    assert trace == ([] if resource == 'neither' else [resource])


def policy():
    def item(cls, **values):
        return models.with_resource_digest(cls(tenant_id='team-a', created_at='2026-10-12T00:00:00Z', **values))
    base = models.PolicyVersion(tenant_id='team-a', policy_id='baseline', version=1,
        author_id=str(UUID(int=1)), created_at='2026-10-12T00:00:00Z',
        rules=(models.Rule(rule_id='baseline', kind='pii', stages=('input', 'output'), action='block',
            mandatory=True, minimum_state='observe', config={'entities': ['email']}, evaluator_revision='pii-v2'),),
        residency=item(models.ResidencyProfile, residency_id=str(UUID(int=2))),
        quota=item(models.Quota, quota_id=str(UUID(int=3))))
    return PolicyCompiler().compile(base).model_copy(update={'status': 'approved'}).model_dump(mode='json')


@pytest.fixture
def matrix(monkeypatch, source, tmp_path):
    def setup(kind, *, fault=None, body_failure=False, disabled=False):
        path = E2E / ('test_cluster_evaluator_gateways.py' if kind == 'gateway' else 'test_cluster_evaluators.py')
        target = module(monkeypatch, 'cleanup_' + kind, path)
        trace, finishes = [], []
        original_policy = policy()
        actors = {role: {'principal_id': str(UUID(int=index))} for index, role in enumerate(
            ('tenant_admin', 'author', 'approver', 'operator', 'auditor'), 10)}
        original = {'workload_id': 'chat', 'revision': 1, 'state': 'observe'}
        baseline = {'revision': 1}
        operation = str(UUID(int=30))
        primary = Failure('original body failure')
        restoration = Failure('original restoration failure')

        class Journal:
            def __init__(self, *args, **kwargs):
                self.operation = operation
                trace.append('intent')

            def finish(self, **options):
                trace.append('finish')
                finishes.append(options)
                if fault == 'journal':
                    raise Failure('journal failure')

            def close(self):
                trace.append('journal_close')
                if fault == 'journal_close':
                    raise Failure('journal close failure')

        class Journey:
            last_release = None

            def __init__(self):
                self.actors = {role: row['principal_id'] for role, row in actors.items()}

            def approve(self, payload):
                trace.append('approve')
                candidate = models.PolicyVersion.model_validate_json(models.canonical_json_bytes({
                    **original_policy, 'policy_id': 'matrix', 'author_id': actors['author']['principal_id'],
                    'status': 'candidate', 'rules': payload['rules'], 'compiled_digest': '0' * 64}))
                self.approved = PolicyCompiler().compile(candidate).model_copy(update={'status': 'approved'}).model_dump(mode='json')
                return self.approved

            def release(self, **options):
                label = options['label']
                trace.append(label)
                if label == 'restore-original-binding' and fault == 'restore':
                    raise restoration
                self.last_release = {'operation': operation, 'label': label}
                return {'binding_revision': 2, 'receipts': [{}, {}]}

            def _request(self, *args, **kwargs):
                trace.append('floor_refusal')
                return {'error': {'code': 'floor_conflict'}}

        journey = Journey()

        class Cluster:
            def __init__(self, *, engine):
                assert engine == 'pii'
                self.tenants = ('team-a', 'team-b')
                self.inventory = {'tenants': {'team-a': {'roles': actors,
                    'runtimes': {'a': {'runtime_id': str(UUID(int=40))}, 'b': {'runtime_id': str(UUID(int=41))}}}}}
                self.deployment = SimpleNamespace(lock=nullcontext)
                self.http = Resource(trace, 'http_close', fault == 'http')
                self.repo = Resource(trace, 'repo_close', fault == 'repo')

            close = source.EvaluatorCluster.close

            def binding(self, tenant):
                return original, original_policy

            def baseline(self, tenant):
                return baseline, original_policy

            def journey(self, *args, **kwargs):
                return journey

            def assert_governance_audit(self, *args, **kwargs):
                if body_failure and kind == 'runtime':
                    raise primary

            def evaluate(self, *args, **kwargs):
                return SimpleNamespace(policy=models.PolicyRef.model_validate(target.policy_reference(journey.approved)),
                    binding_revision=2, snapshot_generation=1, action='allow',
                    findings=[SimpleNamespace(rule_id='phase3-pii', evaluator_revision='pii-v2')],
                    evaluator_revisions=('pii-v2',), would_be_action='block', transformed_parts=None), {}

            def assert_exact_audit(self, *args, **kwargs):
                pass

            def assert_private_metadata(self, *args, **kwargs):
                pass

            def owned_binding_revision(self, *args, **kwargs):
                return 2

            def scenario(self, name):
                trace.append('mock_reset')
                if fault == 'mock_reset':
                    raise Failure('mock restoration failure')

        def observed(*args, **kwargs):
            trace.append('sample')
            if body_failure:
                raise primary

        monkeypatch.setattr(target, 'operation_root', lambda: tmp_path)
        monkeypatch.setattr(target, 'GovernanceAttempt', Journal)
        monkeypatch.setattr(target, 'EvaluatorCluster', Cluster)
        if kind == 'gateway':
            monkeypatch.setattr(target, 'observed_sample', observed)
            entry = target.test_deployed_gateway_engine_matrix_exact_dispatch_transformation_and_attribution
        else:
            entry = target.test_deployed_governed_evaluator_matrix
        def run():
            entry('team-a', 'pii', 'input', 'disabled' if disabled else 'observe')
        return run, trace, finishes, primary, restoration
    return setup


@pytest.mark.parametrize('kind', ['gateway', 'runtime'])
@pytest.mark.parametrize('fault', [None, 'http', 'repo', 'restore', 'journal', 'journal_close'])
def test_actual_entrypoint_cleanup_closes_before_journal_outcome(matrix, kind, fault):
    run, trace, finishes, _, restoration = matrix(kind, fault=fault)
    if fault:
        with pytest.raises(RuntimeError) as caught:
            run()
        if fault == 'restore':
            assert caught.value is restoration
    else:
        run()
    assert trace.index('http_close') < trace.index('repo_close') < trace.index('finish') < trace.index('journal_close')
    assert finishes == [{'passed': fault in {None, 'journal', 'journal_close'},
                         'restoration': 'failed' if fault == 'restore' else 'applied'}]
    # A journal write/descriptor-close failure still fails the entrypoint; a
    # successfully written pass is not evidence of a successful failed worker.
    assert trace.count('restore-original-binding') == 1


@pytest.mark.parametrize('kind', ['gateway', 'runtime'])
@pytest.mark.parametrize('fault', ['http', 'repo', 'restore', 'journal', 'journal_close'])
def test_original_body_failure_survives_every_cleanup_failure(matrix, kind, fault):
    run, trace, finishes, primary, _ = matrix(kind, fault=fault, body_failure=True)
    with pytest.raises(Failure) as caught:
        run()
    assert caught.value is primary
    assert finishes == [{'passed': False, 'restoration': 'failed' if fault == 'restore' else 'applied'}]
    assert 'http_close' in trace and 'repo_close' in trace and 'journal_close' in trace
    assert primary.__notes__ and all('private exception detail' not in note for note in primary.__notes__)


@pytest.mark.parametrize('fault', [None, 'http', 'repo'])
def test_disabled_refusal_has_no_restoration_and_no_early_journal_pass(matrix, fault):
    run, trace, finishes, _, _ = matrix('runtime', fault=fault, disabled=True)
    if fault:
        with pytest.raises(RuntimeError, match='^evaluator_cluster_cleanup_failed$'):
            run()
    else:
        run()
    assert 'matrix-release' not in trace and 'restore-original-binding' not in trace
    assert finishes == [{'passed': fault is None, 'restoration': 'not_needed'}]
    assert trace.index('repo_close') < trace.index('finish')


def test_gateway_mock_reset_failure_cannot_skip_binding_or_resource_cleanup(matrix):
    run, trace, finishes, _, _ = matrix('gateway', fault='mock_reset')
    with pytest.raises(Failure, match='mock restoration failure'):
        run()
    assert trace.index('mock_reset') < trace.index('restore-original-binding') < trace.index('http_close')
    assert finishes == [{'passed': False, 'restoration': 'applied'}]


@pytest.mark.parametrize('phase', ['ready', 'bundle'])
@pytest.mark.parametrize('cleanup_fault', [False, True])
def test_actual_constructor_preserves_original_error_and_closes_every_created_resource(
    source, monkeypatch, tmp_path, phase, cleanup_fault,
):
    trace, original = [], Failure('original constructor failure')
    access = {'ca_file': '/synthetic/ca', 'database_url_file': '/synthetic/database',
        'tenants': {tenant: {'workload_token_file': '/' + tenant} for tenant in ('team-a', 'team-b')}}
    inventory = {'tenants': {tenant: {'roles': {'workload': {'token_file': '/' + tenant}}}
                             for tenant in ('team-a', 'team-b')}}
    def bundle(*args):
        raise original
    deployment = SimpleNamespace(owner=dict, support=lambda name: SimpleNamespace(_classifier_bundle=bundle))
    monkeypatch.setenv('EVOLUTION_DEPLOYMENT_STATE', str(tmp_path))
    monkeypatch.setenv('EVOLUTION_EVALUATOR_CONFIG_FILE', str(tmp_path / 'config'))
    monkeypatch.setattr(source, 'load_module', lambda *args: SimpleNamespace(LocalDeployment=lambda *args, **kw: deployment))
    monkeypatch.setattr(source.EvaluatorCluster, '_access', lambda self: access)
    monkeypatch.setattr(source.EvaluatorCluster, '_credentials', lambda self: {'inventory': inventory})
    def ready(self):
        if phase == 'ready':
            raise original
    monkeypatch.setattr(source.EvaluatorCluster, 'assert_ready', ready)
    monkeypatch.setattr(source.ssl, 'create_default_context', lambda **kw: object())
    monkeypatch.setattr(source.httpx, 'Client', lambda **kw: Resource(trace, 'http', cleanup_fault))
    monkeypatch.setattr(source, 'TenantRepository', lambda *args: Resource(trace, 'repo', cleanup_fault))
    monkeypatch.setattr(source, 'private_read', lambda *args, **kw: b'synthetic')
    with pytest.raises(Failure) as caught:
        source.EvaluatorCluster()
    assert caught.value is original
    assert trace == (['http'] if phase == 'ready' else ['http', 'repo'])
    assert getattr(original, '__notes__', []) == (['Evaluator cluster cleanup also failed'] if cleanup_fault else [])
