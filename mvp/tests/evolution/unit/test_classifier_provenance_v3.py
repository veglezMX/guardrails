"""Versioned deployed authority with complete synthetic receipt chains only."""
from __future__ import annotations

import copy
from datetime import datetime

import pytest
from evolution import classifier_provenance as original
from evolution import classifier_provenance_v3 as p
from evolution.registry import EvidenceError

from mvp.tests.evolution.unit import classifier_provenance_v3_test_base as old
from mvp.tests.evolution.unit import test_classifier_confirmation_v8 as confirmation
from mvp.tests.evolution.unit import test_classifier_corpus_extension as extension
from mvp.tests.evolution.unit import test_classifier_provenance as legacy

no_operations = legacy.no_operations


@pytest.fixture(autouse=True)
def synthetic_pins(no_operations, monkeypatch):
    monkeypatch.setattr(p, 'BASELINE_CORPORA', copy.deepcopy(original.BASELINE_CORPORA))


def replace(c, ref, value):
    new = c.store.put(ref['path'], value)
    ref.clear()
    ref.update(new)


def extended_case(monkeypatch, index, family='tls'):
    c = old.case(family, selection_index=min(index, 3))
    if index < 4:
        return c
    f = confirmation.chain(monkeypatch) if index == 6 else extension.build(monkeypatch, index)
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
    if index == 6:
        c.authorization.update(created_at=confirmation.ADMITTED, expires_at='2026-10-12T19:05:00+00:00')
        c.options['now'] = lambda: datetime.fromisoformat(confirmation.ADMITTED)
    replace(c, c.preflight_ref, c.preflight)
    replace(c, c.auth_ref, c.authorization)
    # The retained completion is original evidence in this synthetic store; bind
    # its existing current-check references to the extended original preflight.
    for ref in c.completion['current_checks']:
        value = p.document(c.store.load(ref))
        value.update(identity_sha256=c.expected['identity_sha256'], preflight_sha256=c.preflight_ref['sha256'])
        replace(c, ref, value)
    replace(c, c.completion_ref, c.completion)
    c.store.calls.clear()
    return c


def authorize(c):
    return p.ProvenanceVerifier(**c.options).authorize()


def invoke(c, phase='before', **overrides):
    options = {**c.options, **overrides}
    if phase == 'consume':
        options['completion_ref'] = c.completion_ref
    verifier = p.ProvenanceVerifier(**options)
    return verifier(phase=phase, case_id=c.case_id, invocation=old.INVOCATION, operation=c.operation,
        binding=copy.deepcopy(c.binding), sources=dict(old.SOURCES), observation=copy.deepcopy(c.observation),
        group=copy.deepcopy(c.observation.get('group')), deadline=12345)


@pytest.mark.parametrize('index', range(1, 7))
def test_all_original_and_extended_slots_require_real_cumulative_manifest_chain(monkeypatch, index):
    c = extended_case(monkeypatch, index)
    before = copy.deepcopy(c.authorization)
    value = authorize(c)
    assert value['authorization'] == before and c.authorization == before
    assert len(value['authorization']['required_corpora']) == index + 3
    assert all(ref['path'] in {call['path'] for call in c.store.calls} for ref in c.corpora.values())


@pytest.mark.parametrize('index', [4, 5])
@pytest.mark.parametrize('family', ['tls', 'budget', 'resource', 'cni'])
@pytest.mark.parametrize('phase', ['before', 'after', 'consume'])
def test_extended_original_families_keep_current_and_parent_completion_proof(monkeypatch, index, family, phase):
    c = extended_case(monkeypatch, index, family)
    result = invoke(c, phase)
    assert result['parent_invocation'] == c.auth_ref['sha256']
    assert (c.completion_ref in c.store.calls) is (phase == 'consume')


@pytest.mark.parametrize('index', [4, 5, 6])
@pytest.mark.parametrize('fault', ['missing_previous', 'rewritten_previous', 'author_source', 'manifest_pin'])
def test_extension_never_turns_structural_width_into_corpus_authority(monkeypatch, index, fault):
    c = extended_case(monkeypatch, index)
    if fault == 'manifest_pin':
        c.options['expected_corpus_manifest'] = {**c.corpus_manifest_ref, 'sha256': old.SHA}
    elif fault == 'author_source':
        freeze = p.document(c.store.load(c.corpus_manifest['author_freeze']))
        filename = next(name for name in freeze['files'] if name.endswith('.py'))
        parent = c.corpus_manifest['author_freeze']['path'].rsplit('/', 1)[0]
        c.store.raws[parent + '/' + filename] += b' '
    else:
        name = c.corpus_manifest['previous_manifest']['path']
        if fault == 'missing_previous':
            c.store.raws.pop(name)
        else:
            c.store.raws[name] += b' '
    with pytest.raises((EvidenceError, KeyError)):
        authorize(c)


@pytest.mark.parametrize('field,value', [
    ('threshold', .89), ('minimum_recall', .89), ('maximum_fpr', .051),
    ('window_tokens', 511), ('window_stride', 257), ('maximum_windows', 17),
    ('memory_mib', 3073), ('cpu_cores', 3), ('operational_errors', 1),
    ('loaded_cases', 8), ('loaded_requests', 45), ('parent_exit_code', 1),
    ('automatic_retry', True), ('warmup_requests', 1), ('production_enabled', False),
])
def test_extended_final_worker_never_relaxes_existing_limits(monkeypatch, field, value):
    c = extended_case(monkeypatch, 5)
    c.final[field] = value
    with pytest.raises(EvidenceError):
        authorize(c)


@pytest.mark.parametrize('index', [4, 5, 6])
def test_every_new_english_matrix_remains_required(monkeypatch, index):
    c = extended_case(monkeypatch, index)
    c.final['matrices'][f'confirmation_{index}']['en'] = {'tp': 179, 'fn': 21, 'tn': 200, 'fp': 0, 'errors': 0}
    with pytest.raises(EvidenceError):
        authorize(c)


@pytest.mark.parametrize('field', sorted(original.CURRENT_FIELDS))
def test_new_corpora_never_relax_common_current_identity_or_epoch(monkeypatch, field):
    c = extended_case(monkeypatch, 4)
    def changed(**kwargs):
        value = c.current(**kwargs)
        value[field] = [] if field == 'subjects' else 'foreign'
        return value
    with pytest.raises(EvidenceError):
        invoke(c, current=changed)


@pytest.mark.parametrize('fault', ['no_completion', 'bad_exit', 'expired', 'same_cold_node', 'unrestored', 'boolean_final'])
def test_extended_authority_keeps_original_non_corpus_gates(monkeypatch, fault):
    c = extended_case(monkeypatch, 5, 'resource')
    if fault == 'no_completion':
        c.completion_ref = None
    elif fault == 'bad_exit':
        c.completion['exit_code'] = 1
        replace(c, c.completion_ref, c.completion)
    elif fault == 'expired':
        c.authorization['expires_at'] = old.stamp(-1)
        replace(c, c.auth_ref, c.authorization)
    elif fault == 'same_cold_node':
        c.cold['node_id'] = c.seal['source_owner']['container_id']
        replace(c, c.cold_ref, c.cold)
        replace(c, c.preflight_ref, c.preflight)
        replace(c, c.auth_ref, c.authorization)
    elif fault == 'unrestored':
        ref = c.governance_files['completion.json']
        value = p.document(c.store.load(ref)); value['restoration'] = 'failed'
        replace(c, ref, value); replace(c, c.completion_ref, c.completion)
    else:
        c.options['qualify_final_worker'] = lambda **kwargs: True
    with pytest.raises(EvidenceError):
        invoke(c, 'consume')


def test_original_module_and_all_common_case_epoch_predicates_stay_separate(monkeypatch):
    c = extended_case(monkeypatch, 4)
    assert p.CASE_FAMILIES == original.CASE_FAMILIES and p.PHASES == original.PHASES
    assert p.CHECK_PHASES == original.CHECK_PHASES and p.CURRENT_FIELDS == original.CURRENT_FIELDS
    assert p.verify_current.__code__.co_code == original.verify_current.__code__.co_code
    with pytest.raises(EvidenceError):
        original.ProvenanceVerifier(**c.options).authorize()
    assert authorize(c)['authorization']['required_corpora'] == c.corpora
