"""Original frozen public metadata and in-memory negatives; never execute author code."""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from evolution import classifier_confirmation_v8 as v8
from evolution import classifier_corpus_extension_v2 as extension
from evolution import classifier_provenance as p
from evolution.registry import EvidenceError

from mvp.tests.evolution.unit import test_classifier_corpus_extension as old_extension
from mvp.tests.evolution.unit import test_classifier_provenance as old

no_operations = old.no_operations
FIXTURE = Path(__file__).parents[1] / 'fixtures/confirmation-v8-custody'
CREATED = '2026-10-12T19:00:00+00:00'
FROZEN = '2026-10-12T19:01:00+00:00'
ADMITTED = '2026-10-12T19:02:00+00:00'


def author_fixture(store=None):
    store = store or old.Store()
    for name, pin in v8.FILES.items():
        raw = (FIXTURE / name).read_bytes()
        assert p.raw_digest(raw) == pin
        store.raws['/synthetic/author-v8/' + name] = raw
    corpus = {'path': '/synthetic/author-v8/' + v8.CORPUS_NAME, 'sha256': v8.CORPUS_SHA256}
    author = {'path': '/synthetic/author-v8/FREEZE.json', 'sha256': v8.FREEZE_SHA256}
    record = v8.normalization_record(corpus_ref=corpus, author_ref=author, created_at=CREATED, load=store.load)
    normalized = store.put('parent-normalization.json', record)
    return SimpleNamespace(store=store, corpus=corpus, author=author, record=record, normalized=normalized)


def verify_author(f, *, load=None):
    return v8.verify(corpus_ref=f.corpus, author_ref=f.author, normalization_ref=f.normalized,
        prior={}, frozen_at=FROZEN, load=load or f.store.load)


def test_exact_original_twelve_files_pass_without_rewriting_author_or_cases():
    f = author_fixture()
    before = dict(f.store.raws)
    value = verify_author(f)
    assert value['families'] == 60 and value['duplicates'] == 0
    assert value['ordered_sha256'] == 'd65eb73e250a9a3a5a4b717df7e3d40d13ac18e69675c7fbe371dceacc5690a2'
    assert f.store.raws == before
    assert f.record['author_recorded_ordered_hash'] is False
    assert all(f.record[key] is False for key in ('texts_changed', 'labels_changed', 'case_order_changed', 'model_evaluation_performed'))
    assert len(f.record['original_files']) == 12
    assert {ref['path'] for ref in f.record['original_files'].values()} <= {ref['path'] for ref in f.store.calls}
    original = p.document(f.store.load(f.author))
    assert 'ordered_normalized_text_sha256' not in original['full_normalized_text_uniqueness']
    assert original['counts']['languages']['en']['malicious'] == 200
    assert f.record['derived_metadata']['counts'] == v8.FLAT_COUNTS
    assert value['provenance'] == p.document(f.store.load(f.corpus))['provenance']


@pytest.mark.parametrize('name', list(v8.FILES))
def test_every_original_artifact_byte_is_required(name):
    f = author_fixture()
    f.store.raws['/synthetic/author-v8/' + name] += b' '
    with pytest.raises(EvidenceError):
        verify_author(f)


@pytest.mark.parametrize('target', ['corpus', 'author'])
@pytest.mark.parametrize('fault', ['hash', 'name'])
def test_original_corpus_and_freeze_cannot_be_rebound_by_parent(target, fault):
    f = author_fixture()
    ref = getattr(f, target)
    ref['sha256' if fault == 'hash' else 'path'] = old.SHA if fault == 'hash' else '/synthetic/different-name.json'
    with pytest.raises(EvidenceError):
        verify_author(f)


@pytest.mark.parametrize('fault', ['text', 'label', 'order', 'language', 'family'])
def test_coherently_rehashed_case_edits_never_become_format_compatibility(fault):
    f = author_fixture()
    value = p.document(f.store.load(f.corpus))
    if fault == 'text': value['cases'][0]['part']['text'] += ' Parent revision.'
    elif fault == 'label': value['cases'][0]['expected_injection'] = not value['cases'][0]['expected_injection']
    elif fault == 'order': value['cases'][0], value['cases'][1] = value['cases'][1], value['cases'][0]
    elif fault == 'language': value['cases'][0]['language'] = 'es' if value['cases'][0]['language'] == 'en' else 'en'
    else: value['cases'][0]['family_id'] = 'parent-revised-family'
    f.corpus = f.store.put(f.corpus['path'], value)
    author = p.document(f.store.load(f.author))
    author['files'][v8.CORPUS_NAME] = f.corpus['sha256']
    f.author = f.store.put(f.author['path'], author)
    with pytest.raises(EvidenceError):
        v8.normalization_record(corpus_ref=f.corpus, author_ref=f.author, created_at=CREATED, load=f.store.load)


@pytest.mark.parametrize('fault', ['counts', 'ordered_hash', 'uniqueness', 'synthetic', 'prior_access',
    'candidate_access', 'human', 'calls', 'author_claim', 'texts', 'labels', 'order', 'evaluation',
    'original_files', 'derivation', 'scope', 'selection', 'before_freeze', 'future', 'extra'])
def test_parent_normalization_cannot_invent_claims_or_weaken_original_predicates(fault):
    f = author_fixture()
    value = copy.deepcopy(f.record)
    derived = value['derived_metadata']
    if fault == 'counts': derived['counts']['en_malicious'] = 199
    elif fault == 'ordered_hash': derived['full_normalized_text_uniqueness']['ordered_normalized_text_sha256'] = old.SHA
    elif fault == 'uniqueness': derived['full_normalized_text_uniqueness']['unique'] = 599
    elif fault == 'synthetic': derived['provenance']['synthetic'] = False
    elif fault == 'prior_access': derived['provenance']['prior_corpus_example_content_access'] = True
    elif fault == 'candidate_access': derived['provenance']['candidate_model_result_access'] = True
    elif fault == 'human': derived['provenance']['human_adjudicated'] = True
    elif fault == 'calls': derived['provenance']['model_inference_calls_for_evaluation'] = 1
    elif fault == 'author_claim': value['author_recorded_ordered_hash'] = True
    elif fault in {'texts', 'labels'}: value[fault + '_changed'] = True
    elif fault == 'order': value['case_order_changed'] = True
    elif fault == 'evaluation': value['model_evaluation_performed'] = True
    elif fault == 'original_files': del value['original_files']['literal_sources_06.json']
    elif fault == 'derivation': value['derivation']['ordered_normalized_text_sha256'] = 'author stated this'
    elif fault == 'scope': value['scope'] = 'author_amendment'
    elif fault == 'selection': value['selection_index'] = 5
    elif fault == 'before_freeze': value['created_at'] = '2026-10-12T18:55:00+00:00'
    elif fault == 'future': value['created_at'] = ADMITTED
    else: value['quality_pass'] = True
    f.normalized = f.store.put('changed-normalization.json', value)
    with pytest.raises(EvidenceError):
        verify_author(f)


@pytest.mark.parametrize('fault', ['argument_mutation', 'drift'])
def test_stable_reader_boundary_cannot_change_supplied_references_or_original_bytes(fault):
    f = author_fixture()
    original = f.store.load
    def changed(ref):
        raw = original(ref)
        if fault == 'argument_mutation': ref['sha256'] = old.SHA
        else: raw += b' '
        return raw
    with pytest.raises(EvidenceError):
        verify_author(f, load=changed)


def chain(monkeypatch):
    f = old_extension.build(monkeypatch, 5)
    monkeypatch.setattr(extension, 'PREVIOUS_MANIFEST_SHA256', f.ref['sha256'])
    a = author_fixture(f.store)
    selected = p.document(f.store.load(f.value['selection_manifest']))
    selected.update(selection_index=6, qualification=old_extension.extension.qualification(6))
    selected_ref = f.store.put('selected-six.json', selected)
    monkeypatch.setattr(extension, 'SELECTION_SHA256', selected_ref['sha256'])
    previous = copy.deepcopy(f.value['corpora'])
    corpora = {**previous, 'confirmation_6': a.corpus}
    review = {'schema_version': 'evolution-classifier-confirmation-overlap-v1', 'reviewed_at': CREATED,
        'selection_index': 6, 'confirmation': a.corpus, 'author_freeze': a.author, 'prior_corpora': previous,
        'normalization': 'unicode_nfkc_casefold_whitespace_v1', 'counts': {'total': 600, **v8.FLAT_COUNTS, 'families': 60},
        'within_corpus_duplicates': 0, 'prior_corpus_overlap': {key: 0 for key in previous},
        'threshold': .9, 'minimum_recall': .9, 'maximum_false_positive_rate': .05,
        'model_evaluation_performed': False, 'limitations': ['synthetic', 'related_variants_within_families', 'not_human_adjudicated'],
        'status': 'frozen_confirmation_schema_and_overlap_verified'}
    value = {**copy.deepcopy(f.value), 'schema_version': extension.CORPUS_MANIFEST_SCHEMA, 'frozen_at': FROZEN,
        'selection_index': 6, 'selection_manifest': selected_ref, 'previous_manifest': copy.deepcopy(f.ref),
        'corpora': corpora, 'author_freeze': a.author, 'author_normalization': a.normalized,
        'parent_overlap_review': f.store.put('overlap-six.json', review)}
    return SimpleNamespace(store=f.store, value=value, ref=f.store.put('corpus-six.json', value),
        authorization={**f.authorization, 'created_at': ADMITTED, 'required_corpora': copy.deepcopy(corpora)}, author=a)


def verify_chain(f, load=None):
    return extension.verify_corpus_manifest(f.ref, authorization=f.authorization, load=load or f.store.load)


def rewrite(f):
    f.ref = f.store.put('changed-six.json', f.value)


def test_full_sixth_selection_composes_original_all_five_predecessors_and_unchanged_corpora(monkeypatch):
    f = chain(monkeypatch)
    before = copy.deepcopy(f.authorization)
    value = verify_chain(f)
    assert value == f.value and value is not f.value and f.authorization == before
    assert len(value['corpora']) == 9
    assert all(ref['path'] in {call['path'] for call in f.store.calls} for ref in value['corpora'].values())


@pytest.mark.parametrize('index', [1, 2, 3, 4, 5])
def test_old_campaign_and_extension_authority_are_delegated_unchanged(monkeypatch, index):
    f = old_extension.build(monkeypatch, max(4, index))
    if index < 4:
        path = f'/synthetic/corpus-manifest-{index}.json'
        f.ref = {'path': path, 'sha256': p.raw_digest(f.store.raws[path])}
        f.value = p.document(f.store.load(f.ref))
        f.authorization['required_corpora'] = f.value['corpora']
    assert extension.verify_corpus_manifest(f.ref, authorization=f.authorization, load=f.store.load) == f.value


@pytest.mark.parametrize('fault', ['old_schema', 'index5', 'index7', 'missing_normalization', 'extra', 'future',
    'missing_previous_corpus', 'changed_previous_corpus', 'wrong_previous', 'changed_previous_bytes',
    'wrong_selected', 'wrong_identity', 'wrong_required', 'normalization_ref'])
def test_sixth_selection_cannot_bypass_custody_or_previous_history(monkeypatch, fault):
    f = chain(monkeypatch)
    if fault == 'old_schema': f.value['schema_version'] = old_extension.extension.CORPUS_MANIFEST_SCHEMA
    elif fault in {'index5', 'index7'}: f.value['selection_index'] = int(fault[-1])
    elif fault == 'missing_normalization': del f.value['author_normalization']
    elif fault == 'extra': f.value['quality_pass'] = True
    elif fault == 'future': f.value['frozen_at'] = '2026-10-12T19:03:00+00:00'
    elif fault == 'missing_previous_corpus': del f.value['corpora']['confirmation_4']
    elif fault == 'changed_previous_corpus': f.value['corpora']['confirmation_4']['sha256'] = old.SHA
    elif fault == 'wrong_previous': f.value['previous_manifest']['sha256'] = old.SHA
    elif fault == 'changed_previous_bytes': f.store.raws[f.value['previous_manifest']['path']] += b' '
    elif fault == 'wrong_selected': f.value['selection_manifest']['sha256'] = old.SHA
    elif fault == 'wrong_identity': f.authorization['identity']['model_id'] = 'wrong/candidate'
    elif fault == 'wrong_required': f.authorization['required_corpora'].pop('confirmation_5')
    else: f.value['author_normalization']['sha256'] = old.SHA
    rewrite(f)
    with pytest.raises(EvidenceError):
        verify_chain(f)


@pytest.mark.parametrize('fault', ['author_rewrite', 'threshold', 'false_overlap', 'prior_map', 'normalization', 'evaluation'])
def test_latest_overlap_receipt_retains_original_exact_predicates(monkeypatch, fault):
    f = chain(monkeypatch)
    review = p.document(f.store.load(f.value['parent_overlap_review']))
    if fault == 'author_rewrite': review['author_freeze']['sha256'] = old.SHA
    elif fault == 'threshold': review['threshold'] = .5
    elif fault == 'false_overlap': review['prior_corpus_overlap']['original'] = 1
    elif fault == 'prior_map': review['prior_corpora'] = {}
    elif fault == 'normalization': review['normalization'] = 'none'
    else: review['model_evaluation_performed'] = True
    f.value['parent_overlap_review'] = f.store.put('changed-overlap-six.json', review)
    rewrite(f)
    with pytest.raises(EvidenceError):
        verify_chain(f)


@pytest.mark.parametrize('fault', ['outer_reference', 'outer_authorization', 'late_literal_drift'])
def test_current_and_historical_reader_closure_remains_stable_until_return(monkeypatch, fault):
    f = chain(monkeypatch)
    calls = {}
    def changing(ref):
        raw = f.store.load(ref)
        calls[ref['path']] = calls.get(ref['path'], 0) + 1
        if fault == 'outer_reference': f.ref['path'] = '/synthetic/changed-after-call.json'
        elif fault == 'outer_authorization': f.authorization['created_at'] = CREATED
        elif ref['path'].endswith('literal_sources_06.json') and calls[ref['path']] > 2: return raw + b' '
        return raw
    with pytest.raises(EvidenceError):
        verify_chain(f, changing)
