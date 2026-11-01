"""Admission authority uses genuine shared static predicates over fake receipts."""
from __future__ import annotations

import copy
from types import SimpleNamespace as N

import pytest
from evolution import classifier_admission_authority as s
from evolution import classifier_provenance_v2 as p
from evolution.registry import EvidenceError

from mvp.tests.evolution.unit import classifier_provenance_v2_test_base as base
from mvp.tests.evolution.unit import test_classifier_admission_fault as fault
from mvp.tests.evolution.unit import test_classifier_provenance as original

no_operations = original.no_operations


def replace(c, ref, value):
    updated = c.store.put(ref['path'], value); ref.clear(); ref.update(updated)


@pytest.fixture
def case(monkeypatch, request):
    index = getattr(request, 'param', 0)
    transition, first, peer = fault.proof()
    monkeypatch.setattr(base, 'SOURCES', {**base.SOURCES,
        'mvp/tests/evolution/e2e/classifier_worker_transition.py': transition['authorization']['program_sha256']})
    monkeypatch.setattr(p, 'BASELINE_CORPORA', copy.deepcopy(original.p.BASELINE_CORPORA))
    c = base.case('resource')
    pod = {'pod_uid': transition['authorization']['pod_uid'],
           'container_id': 'containerd://' + transition['authorization']['container_id']}
    c.binding['pods']['classifier'] = pod
    c.expected['binding_sha256'] = p.digest(c.binding)
    c.rollout['binding_sha256'] = p.digest(c.binding)
    replace(c, c.cold['rollout'], c.rollout); replace(c, c.cold_ref, c.cold)
    replace(c, c.preflight_ref, c.preflight); replace(c, c.auth_ref, c.authorization)
    shared = p.ProvenanceVerifier(**c.options)
    case_id = s.evidence.CASES[index]; operation = transition['authorization']['operation']
    root = '/synthetic/admission/' + operation
    auth = {'schema_version': s.SCHEMA, 'created_at': base.stamp(-10), 'expires_at': base.stamp(500),
        'case_id': case_id, 'operation': operation, 'prerequisite': c.auth_ref,
        'sources': copy.deepcopy(base.SOURCES), 'registry_sha256': base.SHA,
        'source_root': '/synthetic/root', 'deadline_monotonic': 12345,
        'output_directory': root + '/measurements', 'checks_directory': root + '/checks',
        'governance_root': c.authorization['governance_root'], 'transition': None}
    authority = transition['authorization']
    authority.update(parent_invocation_sha256=c.auth_ref['sha256'], preflight_sha256=c.preflight_ref['sha256'],
        owner_id=c.expected['owner_id'], node_id=c.expected['node_id'],
        selection_sha256=p.digest(c.identity), images_sha256=p.digest(c.images),
        credentials_sha256=c.credentials['sha256'], old_epoch=c.expected['worker_epoch'])
    transition['events'][0]['value']['ready'] = fault.ready(authority)
    transition['events'][3]['value']['ready'] = fault.ready(authority, '9' * 32)
    if index == 2:
        auth['transition'] = c.store.put(root + '/transition.json', authority)
    ref = c.store.put(root + '/admission-authorization.json', auth)
    expected = {key: copy.deepcopy(auth[key]) for key in
        ('case_id', 'operation', 'sources', 'registry_sha256', 'source_root', 'deadline_monotonic')}
    calls = []
    def current(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        value = c.current(phase=kwargs['phase'], operation=operation, deadline=12345)
        if kwargs['transition'] is not None:
            value['worker_epoch'] = kwargs['transition']['events'][3]['value']['ready']['epoch']
        return value
    verifier = s.AdmissionAuthority(reference=ref, prerequisite=shared, expected=expected, current=current)
    measurement = {'case_id': case_id, 'operation': operation, 'sources': copy.deepcopy(base.SOURCES),
        'authorization_sha256': ref['sha256'], 'fault': transition if index == 2 else None,
        'requests': [{**first, 'label': 'a-original'}, {'label': 'a-overflow'}, {**peer, 'label': 'b-peer'}]}
    def check(phase='before', **overrides):
        values = {'phase': phase, 'case_id': case_id, 'operation': operation,
            'measurement': None if phase in {'before', 'authorized'} else measurement, 'governance': {},
            'sources': auth['sources'], 'authorization_sha256': ref['sha256'], 'deadline': 12345}
        return verifier.check(**(values | overrides))
    return N(**locals())


@pytest.mark.parametrize('case', [0, 1, 2], indirect=True)
def test_static_prerequisite_and_separate_original_admission_scope(case):
    f = case; context = f.verifier.authorize()
    assert context['authorization'] == f.auth and not f.calls
    checked = f.check()
    assert checked['current']['worker_epoch'] == f.c.expected['worker_epoch']
    assert checked['authorization'] == f.ref and checked['phase'] == 'before'


@pytest.mark.parametrize('field', ['case_id', 'operation', 'sources', 'registry_sha256',
    'source_root', 'deadline_monotonic', 'output_directory', 'checks_directory', 'governance_root', 'prerequisite'])
def test_scope_cannot_be_self_authorized_by_rehashing_new_capsule(case, field):
    f = case; f.auth[field] = None
    replace(f.c, f.ref, f.auth)
    f.verifier.reference = copy.deepcopy(f.ref)
    with pytest.raises((EvidenceError, TypeError)):
        f.verifier.authorize()
    assert not f.calls


@pytest.mark.parametrize('fault_name', ['missing_final', 'failed_final', 'expired', 'bad_pin', 'deadline_refresh'])
def test_prerequisite_failure_or_expiry_prevents_any_current_operation(case, fault_name):
    f = case
    if fault_name == 'missing_final':
        f.c.store.raws.pop(f.c.qualification['path'])
    elif fault_name == 'failed_final':
        f.c.final['operational_errors'] = 1
    elif fault_name == 'expired':
        f.auth['expires_at'] = base.stamp(-1); replace(f.c, f.ref, f.auth)
        f.verifier.reference = copy.deepcopy(f.ref)
    elif fault_name == 'bad_pin':
        f.c.store.raws[f.ref['path']] += b' '
    with pytest.raises((EvidenceError, KeyError)):
        f.check(deadline=12346 if fault_name == 'deadline_refresh' else 12345)
    assert not f.calls


@pytest.mark.parametrize('case', [2], indirect=True)
def test_only_complete_authorized_transition_changes_the_admission_epoch(case):
    f = case; checked = f.check('after')
    assert checked['current']['worker_epoch'] == '9' * 32
    assert checked['transition_sha256'] == p.digest(f.transition)
    assert f.c.expected['worker_epoch'] == 'f' * 32
    with pytest.raises(EvidenceError):
        p.verify_current(f.c.expected, checked['current'], preflight_sha256=f.c.preflight_ref['sha256'],
                         phase='after', operation=f.operation, now=base.NOW)
    prefix = copy.deepcopy(f.transition); prefix.update(status='stopped', cleanup='pending')
    prefix['events'] = prefix['events'][:4]
    assert f.verifier.verify_replacement(prefix, deadline=12345)
    assert f.verifier.authorize_transition(f.authority, f.c.preflight, deadline=12345)


@pytest.mark.parametrize('case', [2], indirect=True)
@pytest.mark.parametrize('key', ['parent_invocation_sha256', 'preflight_sha256', 'owner_id', 'node_id',
    'pod_uid', 'container_id', 'selection_sha256', 'images_sha256', 'credentials_sha256', 'old_epoch', 'program_sha256'])
def test_transition_cannot_be_rebound_to_another_host_worker_or_original(case, key):
    f = case; f.authority[key] = '0' * len(f.authority[key])
    replace(f.c, f.auth['transition'], f.authority); replace(f.c, f.ref, f.auth)
    f.verifier.reference = copy.deepcopy(f.ref)
    with pytest.raises((EvidenceError, s.evidence.AdmissionError)):
        f.verifier.authorize()
    assert not f.calls


@pytest.mark.parametrize('key', sorted(p.CURRENT_FIELDS))
def test_all_original_current_fields_stay_mandatory(case, key):
    f = case; original_current = f.verifier.current
    def changed(**kwargs):
        value = original_current(**kwargs); value[key] = None; return value
    f.verifier.current = changed
    with pytest.raises(EvidenceError):
        f.check()
