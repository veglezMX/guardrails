"""Original private/journal/lock validation with independently scoped admission epochs."""
from __future__ import annotations

import copy
import sys
import time
from pathlib import Path
from types import SimpleNamespace as N
from uuid import uuid4

import pytest
from guardrails.evolution.classifier_contract import parse

from mvp.tests.evolution.e2e import classifier_admission_current as s
from mvp.tests.evolution.unit import test_classifier_admission_fault as fault
from mvp.tests.evolution.unit import test_classifier_provenance_support_v2 as common

no_external = common.no_external
flow = common.flow


@pytest.fixture
def admission_flow(flow, monkeypatch):
    f = flow
    checker = s.AdmissionCurrentChecker(f.cluster, source_root=f.root,
        authorization_raw=f.auth_raw, authorization_sha256=s.digest(f.auth_raw),
        preflight_raw=f.pref_raw, preflight_sha256=s.digest(f.pref_raw),
        node_boundary_raw=f.boundary_raw, executor=f.checker.executor)
    for name in ('_sources', '_loaded_sources', '_forwards', '_boundary'):
        monkeypatch.setattr(checker, name, getattr(f.checker, name))
    monkeypatch.setattr(s, 'actual_subjects', common.s.actual_subjects)
    monkeypatch.setattr(s, 'datetime', common.s.datetime)
    epoch = {'value': f.pref['expected']['worker_epoch']}
    original_observer = common.s.BoundedObserver
    class Observer(original_observer):
        def sample_locked(self):
            row = super().sample_locked(); row['counter']['epoch'] = epoch['value']; return row
    monkeypatch.setattr(s, 'BoundedObserver', Observer)
    monkeypatch.setattr(common.s, 'BoundedObserver', Observer)
    operation = str(uuid4()); deadline = time.monotonic() + 30
    context = {'authorization': {'operation': operation, 'deadline_monotonic': deadline,
        'case_id': s.admission.evidence.CASES[0],
        'prerequisite': {'sha256': checker.authorization_sha256}},
        'shared': {'authorization': parse(f.auth_raw), 'preflight': parse(f.pref_raw)}, 'transition': None}
    def observe(phase='before', transition=None, **kwargs):
        return checker.observe_admission_locked(phase=phase, operation=operation,
            deadline=kwargs.get('deadline', deadline), context=context, transition=transition)
    return N(**locals())


def test_genuine_current_private_journal_and_lock_remain_required(admission_flow):
    f = admission_flow; before = copy.deepcopy(f.context)
    for phase in ('before', 'after', 'restored', 'consume'):
        result = f.observe(phase)
        assert result['worker_epoch'] == f.epoch['value'] and result['operation'] == f.operation
        assert result['subjects'] == f.f.pref['expected']['subjects']
    assert f.context == before
    assert f.f.calls.count('authenticate') == 4 and f.f.calls.count('ready') == 8


@pytest.mark.parametrize('change', ['prerequisite', 'shared-authorization', 'shared-preflight', 'operation',
    'deadline-extension', 'expired-deadline', 'missing-transition', 'foreign-epoch'])
def test_separate_admission_scope_cannot_change_common_state(admission_flow, change):
    f = admission_flow; deadline = f.deadline; phase = 'before'
    if change == 'prerequisite': f.context['authorization']['prerequisite']['sha256'] = '0' * 64
    elif change == 'shared-authorization': f.context['shared']['authorization']['registry_sha256'] = '0' * 64
    elif change == 'shared-preflight': f.context['shared']['preflight']['expected']['worker_epoch'] = '0' * 32
    elif change == 'operation': f.context['authorization']['operation'] = str(uuid4())
    elif change == 'deadline-extension': deadline += 1
    elif change == 'expired-deadline': deadline = 0
    elif change == 'missing-transition': f.context['transition'] = {}; phase = 'after'
    elif change == 'foreign-epoch': f.epoch['value'] = '0' * 32
    with pytest.raises(s.ProvenanceCheckError): f.observe(phase, deadline=deadline)


def replacement(f):
    transition, _, _ = fault.proof()
    authority = transition['authorization']; authority['operation'] = f.operation
    authority['old_epoch'] = f.f.pref['expected']['worker_epoch']
    transition['events'][0]['value']['ready'] = fault.ready(authority)
    transition['events'][3]['value']['ready'] = fault.ready(authority, '9' * 32)
    f.context['authorization']['case_id'] = s.admission.evidence.CASES[2]
    f.context['transition'] = copy.deepcopy(authority)
    f.epoch['value'] = '9' * 32
    return transition


def test_exact_replacement_observation_does_not_relax_original_epoch_checker(admission_flow):
    f = admission_flow; transition = replacement(f)
    original_auth, original_preflight = bytes(f.checker.authorization_raw), bytes(f.checker.preflight_raw)
    result = f.observe('after', transition)
    assert result['worker_epoch'] == '9' * 32
    with pytest.raises(common.s.ProvenanceCheckError): common.observe(f.f)
    assert f.checker.authorization_raw == original_auth and f.checker.preflight_raw == original_preflight
    assert f.context['shared']['preflight']['expected']['worker_epoch'] == 'f' * 32


@pytest.mark.parametrize('change', ['before-phase', 'foreign-authority', 'incomplete', 'cleanup', 'prefix', 'old-child',
    'old-still-running', 'foreign-supervisor', 'new-identity', 'worker-epoch'])
def test_replacement_requires_original_scoped_complete_transition(admission_flow, change):
    f = admission_flow; transition = replacement(f)
    if change == 'foreign-authority': transition['authorization']['operation'] = str(uuid4())
    elif change == 'incomplete': transition['events'].pop()
    elif change == 'cleanup': transition['cleanup'] = 'pending'
    elif change == 'prefix': transition['events'][2]['kind'] = 'other'
    elif change == 'old-child': transition['events'][1]['value']['child']['state'] = 'R'
    elif change == 'old-still-running': transition['events'][3]['value']['process']['old_terminated'] = False
    elif change == 'foreign-supervisor': transition['events'][3]['value']['process']['supervisor']['start_ticks'] += 1
    elif change == 'new-identity': transition['events'][3]['value']['process']['child']['pid_namespace'] = 'foreign'
    elif change == 'worker-epoch': f.epoch['value'] = '8' * 32
    with pytest.raises(s.ProvenanceCheckError): f.observe('before' if change == 'before-phase' else 'after', transition)


@pytest.mark.parametrize('name', sorted(s.ADMISSION_MODULES))
def test_every_admission_module_loaded_from_original_authorized_root(admission_flow, monkeypatch, name):
    f = admission_flow
    monkeypatch.setattr(s.CurrentChecker, '_loaded_sources', lambda self: None)
    f.checker.root = Path(__file__).resolve().parents[4]
    monkeypatch.setitem(sys.modules, name, N(__file__='/foreign/' + s.ADMISSION_MODULES[name]))
    with pytest.raises(s.ProvenanceCheckError, match='admission_loaded_source_mismatch'):
        s.AdmissionCurrentChecker._loaded_sources(f.checker)


def test_missing_own_authority_source_rejected_before_common_source_reader(admission_flow, monkeypatch):
    f = admission_flow; calls = []
    monkeypatch.setattr(s.CurrentChecker, '_sources', lambda *args: calls.append(args))
    with pytest.raises(s.ProvenanceCheckError, match='admission_source_scope'):
        s.AdmissionCurrentChecker._sources(f.checker, {}, f.deadline)
    assert calls == []
