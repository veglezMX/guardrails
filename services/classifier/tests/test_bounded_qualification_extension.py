"""Synthetic extension composition; no model, real corpus, or launch authority."""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib

import pytest
from guardrails.evolution.classifier_contract import ClassifierError, canonical

from services.classifier import bounded_qualification_core as original
from services.classifier import bounded_qualification_core_extended as core
from services.classifier import bounded_qualification_evidence_extended as evidence
from services.classifier.tests.test_bounded_qualification_core import (
    Child,
    Clock,
    no_ml_or_network,
    verify,
)
from services.classifier.tests.test_bounded_qualification_evidence import network

__all__ = ['no_ml_or_network']


def fixture(index=6):
    names = ('original', 'heldout_1', 'heldout_2', *(f'confirmation_{n}' for n in range(1, index + 1)))
    docs = {}
    for name in names:
        cases = []
        for n in range(600):
            part = {'part_id': f'p{n}', 'role': 'user', 'text': f'Only plain synthetic content {name} {n}'}
            if name == 'original':
                part['kind'] = 'text'
            cases.append({'case_id': f'metadata-{n}', 'language': 'en' if n < 400 else 'es',
                'expected_injection': n < 200 or 400 <= n < 500, 'category': 'synthetic',
                'family_id': f'family{n // 10}', 'split': 'heldout', 'part': part})
        docs[name] = {'threshold': .9, 'minimum_recall': .9, 'maximum_false_positive_rate': .05, 'cases': cases}
    raws = {name: canonical(doc) for name, doc in docs.items()}
    identity = {'repository': 'synthetic/candidate', 'revision': 'a' * 40, 'weights_sha256': 'b' * 64,
        'bundle_sha256': 'c' * 64, 'candidate_manifest_sha256': 'd' * 64, 'selection_index': index,
        'qualification_only': True, 'production_enabled': False}
    spec = core.Specification(index, 'd' * 64, 'e' * 64, canonical(identity),
        canonical({'services/classifier/synthetic.py': 'f' * 64}),
        tuple(core.CorpusPin(name, core.sha(raws[name])) for name in names),
        canonical({'original': core.ORIGINAL_APPROVAL_SHA256, 'extension': core.EXTENSION_APPROVAL_SHA256}))
    return spec, raws, docs


def report(index=6):
    spec, raws, docs = fixture(index)
    clock = Clock()
    child = Child(clock)
    child.loaded = {'qualification_only': True, 'quality_verified': False, 'scoring_requests': 0,
        **{name: spec.identity[name] for name in ('repository', 'revision', 'weights_sha256')}}
    retained = []
    rows, startup, loaded, attempts = asyncio.run(core.score_once(docs, spec, child,
        retain=retained.append, validate_loaded=lambda value: None, clock=clock))
    prior = {f'prior_{n}': {'path': f'/private/prior-{n}', 'sha256': str(n) * 64} for n in range(index + 2)}
    expected = evidence.Expected(spec, '8' * 64, '9' * 64, canonical(prior), canonical(loaded))
    start = evidence.started(expected, network(), '2026-10-12T18:00:00+00:00')
    cases = b''.join(retained)
    result = {**start, **core.summarize(rows, spec), 'status': 'completed',
        'completed_at': '2026-10-12T18:10:00+00:00', 'cases_sha256': core.sha(cases),
        'startup_seconds': startup, 'loaded': loaded,
        'cpu': {'child_user_seconds': 2., 'child_system_seconds': .1, 'child_max_rss_kib': 1024,
            'torch_threads': 2, 'torch_interop_threads': 1, 'affinity': [0, 1]},
        'resources_pass': True, 'network_after': network(), 'cost': evidence.cost(rows, attempts)}
    return expected, raws, docs, start, result, cases, child


def check(value, *, require_quality=True):
    expected, raws, _, start, result, cases, _ = value
    return evidence.validate_report(raws, canonical(start), canonical(result), cases, expected,
        verify_corpus=verify, require_quality=require_quality)


@pytest.fixture(scope='module')
def completed():
    return report()


@pytest.mark.parametrize('index,count', [(4, 4200), (5, 4800), (6, 5400)])
def test_whole_cumulative_extension_one_child_and_each_fresh_request_deadline(index, count):
    value = report(index)
    result = check(value)
    child = value[-1]
    assert result['cases'] == count and result['status'] == 'qualified_initial_only'
    assert child.starts == child.closes == 1 and child.attempts == len(child.seen) == count
    assert all('metadata' not in text and text.startswith('Only plain synthetic content ') for _, text, _ in child.seen)
    assert [sequence for sequence, _, _ in child.seen] == list(range(count))
    # Deadline advances independently per row, including across corpus boundaries.
    for n in (0, 599, 600, 797, count - 1):
        assert child.seen[n][2] == pytest.approx(100.5 + n * .001 + 2.9)
    assert not result['production_enabled'] and not result['spanish_enforcement_enabled']
    assert result['final_selected_runtime_rerun_required'] is True
    assert value[3]['approval_chain'] == value[0].specification.approval_chain


@pytest.mark.parametrize('index', [0, 1, 2, 3, 7, True, 4.0])
def test_extension_cannot_accept_old_or_unapproved_indices(index):
    spec, _, _ = fixture(4)
    with pytest.raises(ValueError, match='selection_index'):
        dataclasses.replace(spec, selection_index=index)


@pytest.mark.parametrize('value', [b'{}', canonical({'original': '0' * 64}),
    canonical({'original': core.ORIGINAL_APPROVAL_SHA256, 'extension': '0' * 64}),
    canonical({'original': '0' * 64, 'extension': core.EXTENSION_APPROVAL_SHA256}),
    canonical({'original': core.ORIGINAL_APPROVAL_SHA256, 'extension': core.EXTENSION_APPROVAL_SHA256, 'extra': True}),
    b'{"extension":"' + core.EXTENSION_APPROVAL_SHA256.encode() + b'","original":"' + core.ORIGINAL_APPROVAL_SHA256.encode() + b'"} '])
def test_both_literal_approvals_are_required_in_canonical_immutable_bytes(value):
    spec, _, _ = fixture(4)
    with pytest.raises(ValueError):
        dataclasses.replace(spec, approval_chain_json=value)


def test_mutating_approval_view_cannot_change_authority():
    spec, _, _ = fixture(4)
    before = spec.approval_chain_json
    view = spec.approval_chain
    view.clear()
    assert spec.approval_chain_json == before and len(spec.approval_chain) == 2


@pytest.mark.parametrize('failure', ['recall', 'fpr', 'operational'])
@pytest.mark.parametrize('corpus', ['original', 'heldout_1', 'heldout_2',
    'confirmation_1', 'confirmation_2', 'confirmation_3', 'confirmation_4', 'confirmation_5', 'confirmation_6'])
def test_every_old_and_new_english_corpus_still_independently_required(completed, corpus, failure):
    value = list(completed)
    value[4] = copy.deepcopy(value[4])
    expected, _, documents, _, result, cases, _ = value
    rows = core.validate_rows(cases, documents, expected.specification)
    selected = [row for row in rows if row['corpus'] == corpus and row['language'] == 'en']
    if failure == 'recall':
        for row in selected[:21]:
            row.update(score=0., predicted=False)
    elif failure == 'fpr':
        for row in selected[200:211]:
            row.update(score=.9, predicted=True)
    else:
        selected[0].update(score=None, predicted=None, error='evaluator_unavailable')
        selected[0].pop('windows')
    raw = b''.join(canonical(row) + b'\n' for row in rows)
    value[5] = raw
    result.update(core.summarize(rows, expected.specification), cases_sha256=core.sha(raw),
        cost=evidence.cost(rows, expected.specification.case_count))
    assert check(value, require_quality=False)['status'] == 'failed_initial'
    with pytest.raises(ValueError, match='english_quality_failed'):
        check(value)


@pytest.mark.parametrize('fault', ['missing_approval', 'changed_original', 'changed_extension', 'old_schema',
    'lower_cutoff', 'retry', 'warmup', 'model_reload', 'spanish', 'enable', 'resource', 'missing_row'])
def test_report_cannot_launder_extension_authority_or_weaken_execution_limits(completed, fault):
    value = list(completed)
    value[4] = copy.deepcopy(value[4])
    result = value[4]
    if fault == 'missing_approval': result.pop('approval_chain')
    elif fault.startswith('changed_'): result['approval_chain'][fault.removeprefix('changed_')] = '0' * 64
    elif fault == 'old_schema': result['schema_version'] = 'bounded-candidate-initial-quality-v1'
    elif fault == 'lower_cutoff': result['threshold'] = .89
    elif fault == 'retry': result['retry_requests'] = 1
    elif fault == 'warmup': result['warmup_requests'] = 1
    elif fault == 'model_reload': result['model_reloads'] = 1
    elif fault == 'spanish': result['spanish_enforcement_enabled'] = True
    elif fault == 'enable': result['enabled_model_changed'] = True
    elif fault == 'resource': result['cpu']['child_max_rss_kib'] = 3072 * 1024 + 1
    else:
        value[5] = b'\n'.join(value[5].splitlines()[:-1]) + b'\n'
        result['cases_sha256'] = core.sha(value[5])
    with pytest.raises(ValueError):
        check(value)


@pytest.mark.parametrize('delta', [-1, 1])
def test_each_predecessor_disposition_is_required_before_any_report(completed, delta):
    expected = completed[0]
    prior = {f'p{n}': {'path': f'/private/{n}', 'sha256': '1' * 64}
        for n in range(expected.specification.selection_index + 2 + delta)}
    with pytest.raises(ValueError, match='prior_evidence'):
        dataclasses.replace(expected, prior_evidence_json=canonical(prior))


def test_timeout_remains_original_row_and_never_retries_or_replaces():
    spec, _, docs = fixture(4)
    clock = Clock()
    child = Child(clock)
    original_evaluate = child.evaluate
    async def evaluate(sequence, text, deadline):
        if sequence == 797:
            child.attempts += 1
            child.seen.append((sequence, text, deadline))
            clock.now = deadline
            child.fail_at = 798
            raise ClassifierError('evaluation_timeout')
        return await original_evaluate(sequence, text, deadline)
    child.evaluate = evaluate
    retained = []
    rows, _, _, _ = asyncio.run(core.score_once(docs, spec, child, retain=retained.append,
        validate_loaded=lambda value: None, clock=clock))
    assert len(retained) == 4200 and child.starts == child.closes == 1
    assert rows[797]['error'] == 'evaluation_timeout' and rows[797]['elapsed_ms'] == pytest.approx(2900.)
    assert all(row['error'] == 'evaluator_unavailable' for row in rows[798:])
    assert core.summarize(rows, spec)['quality_pass'] is False


def test_original_scope_remains_unchanged_and_source_pins_preserved():
    from pathlib import Path

    from services.classifier import bounded_qualification_evidence as old_evidence
    assert original.MAXIMUM_ROWS == 3600 and old_evidence.SCHEMA == 'bounded-candidate-initial-quality-v1'
    for module, pin in ((original, 'cbfe9732c3939820910d6873305cd5bf255d083ceaace6b91fb114956c322ab8'),
                       (old_evidence, '8c01a352af5f93614deaaa9fff100a728fec7e2f09e17d9775c4e23e8af108ca')):
        assert hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == pin
