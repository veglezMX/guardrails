"""The actual matrix constructor with synthetic read-only access/TLS/DB ports."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from evolution.dependencies import ROOT as DEPENDENCY_ROOT

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def source(monkeypatch):
    monkeypatch.syspath_prepend(str(DEPENDENCY_ROOT / 'mvp/tests/evolution/e2e'))
    path = ROOT / 'mvp/tests/evolution/e2e/evaluator_matrix_support.py'
    spec = importlib.util.spec_from_file_location('engine_access_matrix', path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module); spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup(source, monkeypatch, tmp_path):
    events = []
    state = tmp_path / 'state'
    value = {'schema_version': 'guardrails-evolution-local-access-v1', 'profile': 'deterministic_mock',
        'ca_file': '/synthetic/ca', 'database_url_file': '/synthetic/database',
        **{name + '_url': 'https://localhost:' + str(18000 + index)
           for index, name in enumerate(('management', 'runtime', 'mock', 'ollama'))},
        'tenants': {tenant: {'gateway_url': 'https://localhost:' + str(19000 + index),
                            'workload_token_file': '/' + tenant}
                    for index, tenant in enumerate(('team-a', 'team-b'))}}
    inventory = {'tenants': {tenant: {'roles': {
        **{role: {'principal_id': str(UUID(int=index + 1))} for index, role in enumerate(
            ('tenant_admin', 'author', 'approver', 'operator', 'auditor'))},
        'workload': {'token_file': '/' + tenant}}} for tenant in ('team-a', 'team-b')}}
    config = tmp_path / 'operator-config'
    flags = SimpleNamespace(bundle_failure=False, readiness_failure=False)
    class Resource:
        def __init__(self, name): self.name = name
        def close(self): events.append('close_' + self.name)
    def access(*, read_only, deadline):
        assert read_only is True and source.time.monotonic() < deadline
        events.append('access')
        return {'status': 'available', 'access_file': str(state / 'assembly/access.json'), 'snapshot': value}
    def credentials(self): events.append('credentials'); return {'inventory': inventory}
    def ready(self):
        events.append('tls_ready')
        if flags.readiness_failure: raise RuntimeError('synthetic readiness refusal')
    def bundle(*args):
        assert args[-1] == config
        events.append('classifier_bundle')
        if flags.bundle_failure: raise RuntimeError('synthetic classifier refusal')
        return (json.dumps({'classifier': [{'tenant_id': tenant, 'provider': {'synthetic': tenant}}
                    for tenant in ('team-a', 'team-b')]}).encode(), None, None, None)
    deployment = SimpleNamespace(access=access, owner=dict,
        support=lambda name: SimpleNamespace(_classifier_bundle=bundle))
    monkeypatch.setenv('EVOLUTION_DEPLOYMENT_STATE', str(state))
    monkeypatch.delenv('EVOLUTION_EVALUATOR_CONFIG_FILE', raising=False)
    monkeypatch.setattr(source, 'load_module', lambda *args: SimpleNamespace(LocalDeployment=lambda *a, **kw: deployment))
    monkeypatch.setattr(source.EvaluatorCluster, '_credentials', credentials)
    monkeypatch.setattr(source.EvaluatorCluster, 'assert_ready', ready)
    def context(*, cafile):
        assert cafile == '/synthetic/ca'; events.append('ca_verification'); return 'synthetic_ssl'
    monkeypatch.setattr(source.ssl, 'create_default_context', context)
    def client(**options):
        assert options == {'verify': 'synthetic_ssl', 'trust_env': False, 'follow_redirects': False, 'timeout': 15}
        events.append('http'); return Resource('http')
    monkeypatch.setattr(source.httpx, 'Client', client)
    def repository(value):
        assert value == 'synthetic_database'; events.append('repo'); return Resource('repo')
    monkeypatch.setattr(source, 'TenantRepository', repository)
    monkeypatch.setattr(source, 'private_read', lambda *args, **kw: b'synthetic_database')
    return SimpleNamespace(source=source, events=events, access=value, flags=flags, config=config)


@pytest.mark.parametrize('engine', ['pii', 'secret', 'term', 'topic', 'json_schema'])
@pytest.mark.parametrize('profile', ['deterministic_mock', 'classifier'])
def test_every_deterministic_engine_keeps_actual_access_credential_tls_and_database_path(setup, monkeypatch, engine, profile):
    f = setup; f.access['profile'] = profile
    if profile == 'classifier': monkeypatch.setenv('EVOLUTION_EVALUATOR_CONFIG_FILE', str(f.config))
    cluster = f.source.EvaluatorCluster(engine=engine)
    assert cluster.profile == profile and cluster.requires_classifier is False
    assert f.events == ['access', 'credentials', 'ca_verification', 'http', 'tls_ready', 'repo'] + (
        ['classifier_bundle'] if profile == 'classifier' else [])
    for tenant in ('team-a', 'team-b'):
        if profile == 'classifier':
            assert cluster.operator_providers(tenant) == ({'synthetic': tenant},)
        else:
            assert cluster.endpoints == {} and cluster.operator_providers(tenant) == ()
            journey = cluster.journey(tenant, str(UUID(int=99)))
            assert journey.operator_providers == () and journey.tenant == tenant
    cluster.close()
    assert f.events[-2:] == ['close_http', 'close_repo']


@pytest.mark.parametrize('options', [{}, {'engine': 'injection'}])
@pytest.mark.parametrize('profile', ['deterministic_mock', 'local_model', 'unknown'])
def test_default_and_injection_always_refuse_without_actual_classifier_profile(setup, options, profile):
    f = setup; f.access['profile'] = profile
    with pytest.raises(AssertionError): f.source.EvaluatorCluster(**options)
    assert f.events == ['access']


@pytest.mark.parametrize('options', [{}, {'engine': 'injection'}, {'engine': 'pii'}])
def test_classifier_profile_still_requires_real_operator_config_even_for_other_engines(setup, options):
    f = setup; f.access['profile'] = 'classifier'
    with pytest.raises(KeyError, match='EVOLUTION_EVALUATOR_CONFIG_FILE'):
        f.source.EvaluatorCluster(**options)
    assert f.events[-2:] == ['close_http', 'close_repo']


@pytest.mark.parametrize('fault', ['readiness', 'classifier'])
def test_required_operational_failure_never_falls_back_to_deterministic_profile(setup, monkeypatch, fault):
    f = setup; f.access['profile'] = 'classifier'
    monkeypatch.setenv('EVOLUTION_EVALUATOR_CONFIG_FILE', str(f.config))
    f.flags.readiness_failure = fault == 'readiness'; f.flags.bundle_failure = fault == 'classifier'
    with pytest.raises(RuntimeError, match='synthetic .* refusal'): f.source.EvaluatorCluster(engine='pii')
    assert f.events[-2:] == (['close_http', 'close_repo'] if fault == 'classifier' else ['tls_ready', 'close_http'])
    assert f.access['profile'] == 'classifier'


@pytest.mark.parametrize('engine', [None, True, 'bedrock', 'INJECTION', 'pii,injection'])
def test_unknown_engine_refuses_before_any_access(setup, engine):
    f = setup
    with pytest.raises(AssertionError, match='unknown governed evaluator engine'):
        f.source.EvaluatorCluster(engine=engine)
    assert f.events == []


@pytest.mark.parametrize('profile', ['local_model', 'unknown'])
def test_deterministic_engine_branch_cannot_select_other_deployment_profiles(setup, profile):
    f = setup; f.access['profile'] = profile
    with pytest.raises(AssertionError): f.source.EvaluatorCluster(engine='pii')
    assert f.events == ['access']


def test_default_injection_retains_classifier_bundle_and_provider_identity(setup, monkeypatch):
    f = setup; f.access['profile'] = 'classifier'
    monkeypatch.setenv('EVOLUTION_EVALUATOR_CONFIG_FILE', str(f.config))
    cluster = f.source.EvaluatorCluster()
    assert cluster.requires_classifier is True and f.events[-1] == 'classifier_bundle'
    assert cluster.operator_providers('team-a') == ({'synthetic': 'team-a'},)
    cluster.close()
