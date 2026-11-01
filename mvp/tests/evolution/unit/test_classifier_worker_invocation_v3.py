"""Parent manifest/fixture contract against genuine pure synthetic receipt DAGs."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from evolution import classifier_provenance_io_v3 as io
from evolution import classifier_provenance_v3 as p
from evolution import classifier_worker_invocation_v3 as worker
from evolution.registry import EvidenceError

from mvp.tests.evolution.e2e.classifier_provenance_fixtures_v3 import (
    ClassifierFixtureContext,
    ClassifierFixturePlugin,
)
from mvp.tests.evolution.unit import test_classifier_provenance_io_v3 as fixtures
from mvp.tests.evolution.unit.classifier_provenance_v3_test_base import SHA, SOURCES

filesystem = fixtures.filesystem
flow = fixtures.flow
isolated = fixtures.isolated
put = fixtures.put


def manifest(f):
    catalog_ref = put(f.f.root / 'source-catalog.json', p.canonical(SOURCES) + b'\n')
    value = {'schema_version': worker.SCHEMA, 'authorization': f.c.auth_ref,
        'corpus_manifest': f.c.authorization['corpus_manifest'], 'case_id': f.c.case_id,
        'nodeid': f.args['nodeid'], 'registry_sha256': SHA, 'sources': dict(SOURCES),
        'source_catalog': catalog_ref, 'deadline_monotonic': f.f.budget.deadline, 'public_files': {},
        'evaluator_config': {'path': '/synthetic/endpoints.json', 'sha256': SHA},
        'deployment_state': '/synthetic/state', 'native_executables': {
            'docker': {'path': '/usr/bin/docker', 'sha256': SHA},
            'kubectl': {'path': '/synthetic/state/bin/kubectl', 'sha256': SHA}}}
    expected = {'case_id': f.c.case_id, 'nodeid': f.args['nodeid'], 'registry_sha256': SHA, 'sources': dict(SOURCES),
        'source_catalog_ref': catalog_ref, 'source_catalog': dict(SOURCES), 'root': f.f.root, 'deadline': f.f.budget.deadline}
    return value, expected


def test_fixed_two_key_environment_has_no_discovery_or_factory(flow):
    f = flow; value, expected = manifest(f)
    ref = put(f.f.root / 'manifest.json', p.canonical(value) + b'\n')
    selected = worker.environment_reference({worker.MANIFEST_ENV: ref['path'], worker.SHA_ENV: ref['sha256']})
    assert selected == ref
    actual = worker.load(selected, reader=f.f.reader, expected=expected)
    assert actual == value and actual is not value
    assert worker.PLUGIN_MODULE == 'mvp.tests.evolution.e2e.classifier_provenance_fixtures_v3'


@pytest.mark.parametrize('field,value', [('schema_version', 'foreign'), ('case_id', 'foreign'), ('nodeid', 'foreign'),
    ('registry_sha256', 'b' * 64), ('sources', {'mvp/foreign.py': SHA}), ('deadline_monotonic', 19999),
    ('source_catalog', {'path': '/foreign.json', 'sha256': SHA}), ('extra_factory', 'module:function'),
    ('evaluator_config', {'path': 'relative', 'sha256': SHA}), ('public_files', {'relative': SHA}),
    ('deployment_state', '/synthetic/../state'), ('native_executables', {'docker': {'path': '/foreign', 'sha256': SHA}})])
def test_manifest_mismatch_or_unknown_authority_refuses(flow, field, value):
    original, expected = manifest(flow)
    altered = original | {field: value}
    with pytest.raises(EvidenceError):
        worker.validate(altered, **expected)


@pytest.mark.parametrize('missing', [worker.MANIFEST_ENV, worker.SHA_ENV])
def test_missing_independent_environment_pin_refuses(missing):
    environment = {worker.MANIFEST_ENV: '/synthetic/manifest.json', worker.SHA_ENV: SHA}
    environment.pop(missing)
    with pytest.raises(EvidenceError):
        worker.environment_reference(environment)


def test_manifest_cannot_grant_its_own_initial_public_read(flow):
    f = flow; value, expected = manifest(f)
    ref = put(f.f.root / 'manifest.json', p.canonical(value) + b'\n', mode=0o644)
    public = io.StableReader(f.f.budget, public_files={ref['path']: ref['sha256']})
    with pytest.raises(EvidenceError):
        worker.load(ref, reader=public, expected=expected)


def test_source_catalog_must_match_actual_bytes_and_all_source_hashes(flow):
    f = flow; value, expected = manifest(f)
    ref = put(f.f.root / 'manifest.json', p.canonical(value) + b'\n')
    expected['source_catalog'] = {'mvp/foreign.py': SHA}
    with pytest.raises(EvidenceError):
        worker.load(ref, reader=f.f.reader, expected=expected)
    with pytest.raises(EvidenceError):
        worker.validate(value, **expected)


def context(f, constructor):
    value, expected = manifest(f)
    value = worker.validate(value, **expected)
    recorder = io.RecordedVerifier(p.ProvenanceVerifier(**f.c.options), f.checks)
    return ClassifierFixtureContext(manifest=value, recorder=recorder, read_only_constructor=constructor)


def test_exact_fixture_authorizes_before_single_cluster_and_records_actual_phases(flow):
    f = flow; events = []
    cluster = SimpleNamespace(close=lambda: events.append('close'))
    def construct(**kwargs):
        assert kwargs['context']['authorization'] == f.c.authorization
        assert kwargs['manifest']['authorization'] == f.c.auth_ref and kwargs['deadline'] == f.f.budget.deadline
        events.append('construct')
        return cluster
    selected = context(f, construct)
    plugin = ClassifierFixturePlugin(selected)
    request = SimpleNamespace(node=SimpleNamespace(nodeid=f.args['nodeid']))
    actual = ClassifierFixturePlugin.classifier_provenance_context.__wrapped__(plugin, request)
    assert actual is selected and events == []
    assert actual.cluster_factory(deadline=f.f.budget.deadline) is cluster and events == ['construct']
    result = actual.verify(phase='before', operation=None, binding=f.c.binding, sources=SOURCES, deadline=12345)
    assert result['parent_invocation'] == f.c.auth_ref['sha256'] and len(actual.recorder.refs) == 1
    with pytest.raises(EvidenceError):
        actual.cluster_factory(deadline=f.f.budget.deadline)
    with pytest.raises(EvidenceError):
        ClassifierFixturePlugin.classifier_provenance_context.__wrapped__(plugin, request)


@pytest.mark.parametrize('fault', ['missing_final_adapter', 'wrong_node', 'wrong_capsule', 'before_authorization', 'before_cluster'])
def test_invalid_fixture_never_constructs_clients(flow, fault):
    f = flow; called = []
    selected = context(f, lambda **kw: called.append(kw))
    if fault == 'missing_final_adapter':
        selected.recorder.verifier.qualify = lambda **kw: (_ for _ in ()).throw(EvidenceError('missing_final_worker'))
    elif fault == 'wrong_capsule':
        selected.manifest['authorization']['sha256'] = 'b' * 64
    if fault in {'before_authorization', 'before_cluster'}:
        if fault == 'before_cluster': selected.authorize()
        with pytest.raises(EvidenceError):
            selected.verify(phase='before', operation=None, binding=f.c.binding, sources=SOURCES, deadline=12345)
    else:
        plugin = ClassifierFixturePlugin(selected)
        request = SimpleNamespace(node=SimpleNamespace(nodeid='foreign' if fault == 'wrong_node' else f.args['nodeid']))
        with pytest.raises(EvidenceError):
            ClassifierFixturePlugin.classifier_provenance_context.__wrapped__(plugin, request)
    assert called == [] and selected.recorder.refs == []


@pytest.mark.parametrize('fault', ['raises', 'mutates', 'deadline'])
def test_failed_readonly_constructor_never_retries_and_cleans_returned_client(flow, fault):
    f = flow; closed = []; calls = []
    def construct(**kwargs):
        calls.append('construct')
        if fault == 'raises': raise OSError('synthetic private infrastructure detail')
        if fault == 'mutates': kwargs['context']['proof']['parent_invocation'] = 'b' * 64
        if fault == 'deadline': f.f.clock[0] = f.f.budget.deadline
        return SimpleNamespace(close=lambda: closed.append('close'))
    selected = context(f, construct); selected.authorize()
    with pytest.raises((EvidenceError, OSError)):
        selected.cluster_factory(deadline=f.f.budget.deadline)
    with pytest.raises(EvidenceError):
        selected.cluster_factory(deadline=f.f.budget.deadline)
    assert calls == ['construct'] and closed == ([] if fault == 'raises' else ['close'])


def test_constructor_deadline_cannot_reset_case_cap_and_closes_on_cap_exit(flow, monkeypatch):
    f = flow; closed = []; checks = []
    original = f.f.budget.check
    def construct(**kwargs):
        assert kwargs['deadline'] == 20 and f.f.budget.deadline == 20
        def check():
            checks.append('check')
            if len(checks) == 2:
                raise EvidenceError('classifier_provenance_io_deadline')
            original()
        monkeypatch.setattr(f.f.budget, 'check', check)
        return SimpleNamespace(close=lambda: closed.append('close'))
    selected = context(f, construct); selected.authorize()
    with pytest.raises(EvidenceError):
        selected.cluster_factory(deadline=20)
    assert selected.cluster is None and closed == ['close'] and f.f.budget.deadline == 20000
