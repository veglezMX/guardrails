"""Version3 consumes genuine immutable corpus history, preserving deployed gates."""
from __future__ import annotations
import copy
import socket
import subprocess
from datetime import UTC, datetime
import pytest
from evolution import classifier_provenance as original
from evolution import classifier_provenance_v2 as previous
from evolution import classifier_provenance_v3 as p
from evolution.registry import EvidenceError
from mvp.tests.evolution.unit import classifier_provenance_v3_test_base as old
from mvp.tests.evolution.unit import classifier_extension_v3_test_seed as seed
from mvp.tests.evolution.unit.test_classifier_provenance_v3 import replace, authorize, invoke

REAL_CHAIN = True
authenticated_seed = seed.authenticated_seed

@pytest.fixture(autouse=True)
def no_operations(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Native, network and model operations are forbidden in source tests')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(old, 'NOW', datetime(2026, 10, 13, 0, tzinfo=UTC))

def extended_case(cache, index=7, family='tls'):
    c = old.case(family)
    f = seed.build(cache, index)
    c.store.raws.update(f.store.raws)
    c.identity.update(f.authorization['identity'])
    c.corpora.clear()
    c.corpora.update(copy.deepcopy(f.value['corpora']))
    c.corpus_manifest.clear()
    c.corpus_manifest.update(copy.deepcopy(f.value))
    c.corpus_manifest_ref.clear()
    c.corpus_manifest_ref.update(f.ref)
    c.matrices.clear()
    c.matrices.update({name: {language: {'tp': count, 'fn': 0, 'tn': count, 'fp': 0, 'errors': 0}
        for language, count in [('en', 200), ('es', 100)]} for name in c.corpora})
    c.rollout['identity_sha256'] = p.digest(c.identity)
    replace(c, c.cold['rollout'], c.rollout)
    replace(c, c.cold_ref, c.cold)
    c.expected['identity_sha256'] = p.digest(c.identity)
    replace(c, c.preflight_ref, c.preflight)
    replace(c, c.auth_ref, c.authorization)
    for ref in c.completion['current_checks']:
        value = p.document(c.store.load(ref))
        value.update(identity_sha256=c.expected['identity_sha256'], preflight_sha256=c.preflight_ref['sha256'])
        replace(c, ref, value)
    replace(c, c.completion_ref, c.completion)
    c.store.calls.clear()
    return c

@pytest.mark.parametrize('index', [7, 8, 9])
def test_approved_new_slots_use_genuine_cumulative_verification(authenticated_seed, index):
    c = extended_case(authenticated_seed, index)
    assert authorize(c)['authorization']['required_corpora'] == c.corpora
    assert len(c.corpora) == index + 3
    assert set(c.store.raws).issuperset(ref['path'] for ref in c.corpora.values())
    read = {ref['path'] for ref in c.store.calls}
    assert all(ref['path'] in read for ref in c.corpora.values())
    assert c.corpus_manifest['previous_manifest']['path'] in read
    with pytest.raises(EvidenceError):
        previous.ProvenanceVerifier(**c.options).authorize()

@pytest.mark.parametrize('family', ['tls', 'budget', 'resource', 'cni'])
@pytest.mark.parametrize('phase', ['before', 'after', 'consume'])
def test_new_slot_preserves_current_epoch_governance_and_parent_completion(authenticated_seed, family, phase):
    c = extended_case(authenticated_seed, 7, family)
    result = invoke(c, phase)
    assert result['parent_invocation'] == c.auth_ref['sha256']
    assert (c.completion_ref in c.store.calls) is (phase == 'consume')

@pytest.mark.parametrize('fault', ['history_missing', 'history_drift', 'approval_drift', 'omitted_confirmation', 'wrong_model', 'unapproved_slot'])
def test_new_slot_never_treats_structural_width_as_authority(authenticated_seed, fault):
    c = extended_case(authenticated_seed, 7)
    if fault.startswith('history'):
        path = c.corpus_manifest['previous_manifest']['path']
        if fault == 'history_missing':
            c.store.raws.pop(path)
        else:
            c.store.raws[path] += b' '
    elif fault == 'approval_drift':
        c.store.raws[c.corpus_manifest['approval']['path']] += b' '
    elif fault == 'omitted_confirmation':
        c.corpora.pop('confirmation_3')
        replace(c, c.auth_ref, c.authorization)
    elif fault == 'wrong_model':
        c.identity['model_revision'] = '0' * 40
        replace(c, c.auth_ref, c.authorization)
    else:
        c.corpora.update({f'confirmation_{i}': next(iter(c.corpora.values())) for i in (8, 9, 10)})
        replace(c, c.auth_ref, c.authorization)
    with pytest.raises((EvidenceError, KeyError)):
        authorize(c)

@pytest.mark.parametrize('field,value', [
    ('threshold', .89), ('minimum_recall', .89), ('maximum_fpr', .051),
    ('window_tokens', 511), ('window_stride', 257), ('maximum_windows', 17),
    ('memory_mib', 3073), ('cpu_cores', 3), ('operational_errors', 1),
    ('loaded_cases', 8), ('loaded_requests', 45), ('parent_exit_code', 1),
    ('automatic_retry', True), ('warmup_requests', 1), ('production_enabled', False),
])
def test_new_slots_preserve_all_final_worker_limits(authenticated_seed, field, value):
    c = extended_case(authenticated_seed)
    c.final[field] = value
    with pytest.raises(EvidenceError):
        authorize(c)

@pytest.mark.parametrize('index', [7, 8, 9])
@pytest.mark.parametrize('fault', ['recall', 'false_positive', 'missing_matrix'])
def test_every_added_english_matrix_is_required(authenticated_seed, index, fault):
    c = extended_case(authenticated_seed, index)
    key = f'confirmation_{index}'
    if fault == 'missing_matrix':
        c.matrices.pop(key)
    elif fault == 'recall':
        c.matrices[key]['en'].update(tp=179, fn=21)
    else:
        c.matrices[key]['en'].update(tn=189, fp=11)
    with pytest.raises(EvidenceError):
        authorize(c)

def test_shared_current_and_wire_contracts_are_unchanged():
    for field in ['AUTHORIZATION_SCHEMA', 'CASE_FAMILIES', 'PHASES', 'CHECK_PHASES', 'CURRENT_FIELDS']:
        assert getattr(p, field) == getattr(previous, field)
    assert p.verify_current.__code__.co_code == previous.verify_current.__code__.co_code
    assert p.verify_current.__code__.co_code == original.verify_current.__code__.co_code

