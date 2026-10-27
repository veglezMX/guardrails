"""Synthetic cumulative corpus authority; no inference, native work, or sockets."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from evolution import classifier_corpus_extension as extension
from evolution import classifier_provenance as p
from evolution.registry import EvidenceError

from mvp.tests.evolution.unit import test_classifier_provenance as old

no_operations = old.no_operations


def build(monkeypatch, index=4):
    store = old.Store()
    identity = {'model_id': 'synthetic/extension-final', 'model_revision': 'synthetic-revision',
        'weights_sha256': old.SHA}
    corpora, _, _ = old.selection_chain(store, identity, max(3, index))
    def ref(name):
        return {'path': '/synthetic/' + name, 'sha256': p.raw_digest(store.raws['/synthetic/' + name])}
    previous = ref('corpus-manifest-3.json')
    monkeypatch.setattr(extension, 'LEGACY_FINAL_MANIFEST_SHA256', previous['sha256'])
    original_approval = ref('selection-approval.json')
    proposal = store.put('extension-proposal.md', {'synthetic': 'explicit additional3 only'})
    monkeypatch.setattr(extension, 'EXTENSION_PROPOSAL_SHA256', proposal['sha256'])
    approval = store.put('extension-approval.json', {'schema_version': 'classifier-bounded-selection-extension-approval-v1',
        'recorded_at': old.stamp(-290), 'user_answer': 'Approve three more (Recommended)', 'question_item_id': 'synthetic',
        'proposal': proposal, 'previous_approval': original_approval, 'maximum_additional_candidates': 3,
        'permitted_selection_indices': [4, 5, 6], 'maximum_total_bounded_candidates': 6, 'first_campaign_consumed': 3,
        'new_candidates_consumed': 0, 'status': 'approved_no_additional_candidate_selected_or_qualified',
        'authority': 'synthetic separately authorized extension; unchanged constraints'})
    monkeypatch.setattr(extension, 'EXTENSION_APPROVAL_SHA256', approval['sha256'])
    for current in range(4, index + 1):
        value = p.document(store.load(ref(f'corpus-manifest-{current}.json')))
        value.update(schema_version=extension.CORPUS_MANIFEST_SCHEMA, extension_approval=approval,
                     previous_manifest=previous, frozen_at=old.stamp(-100 + current))
        selected = p.document(store.load(value['selection_manifest']))
        selected.update(recorded_at=old.stamp(-200 + current), maximum_candidates=6, approval=approval)
        value['selection_manifest'] = store.put(f'extension-selected-{current}.json', selected)
        author = p.document(store.load(value['author_freeze'])); author['frozen_at_utc'] = old.stamp(-160 + current)
        value['author_freeze'] = store.put(f'author-{current}/freeze-extension.json', author)
        review = p.document(store.load(value['parent_overlap_review']))
        review.update(author_freeze=value['author_freeze'], reviewed_at=old.stamp(-130 + current))
        value['parent_overlap_review'] = store.put(f'extension-overlap-{current}.json', review)
        previous = store.put(f'extension-corpus-manifest-{current}.json', value)
    value = p.document(store.load(previous))
    authorization = {'identity': identity, 'created_at': old.stamp(), 'required_corpora': value['corpora']}
    return SimpleNamespace(store=store, ref=previous, value=value, authorization=authorization, approval=approval,
        original=original_approval, proposal=proposal, corpora=corpora)


@pytest.fixture
def chain(monkeypatch): return build(monkeypatch)


def verify(f):
    return extension.verify_corpus_manifest(f.ref, authorization=f.authorization, load=f.store.load)


def rewrite(f, value=None):
    f.value = value if value is not None else f.value
    f.ref = f.store.put('changed-root.json', f.value)


def modify_selected(f, change):
    selected = p.document(f.store.load(f.value['selection_manifest'])); change(selected)
    f.value['selection_manifest'] = f.store.put('changed-selected.json', selected); rewrite(f)


@pytest.mark.parametrize('index', [4, 5, 6])
def test_each_additional_slot_requires_all_exact_cumulative_corpora(monkeypatch, index):
    f = build(monkeypatch, index); before = copy.deepcopy(f.authorization)
    result = verify(f)
    assert result == f.value and result is not f.value
    assert len(result['corpora']) == 3 + index and 600 * len(result['corpora']) == 1800 + 600 * index
    assert f.authorization == before
    assert all(row['path'] in {ref['path'] for ref in f.store.calls} for row in result['corpora'].values())


@pytest.mark.parametrize('index', [1, 2, 3])
def test_original_campaign_delegates_frozen_authority_unchanged(monkeypatch, index):
    f = build(monkeypatch); ref = {'path': f'/synthetic/corpus-manifest-{index}.json'}
    ref['sha256'] = p.raw_digest(f.store.raws[ref['path']]); value = p.document(f.store.load(ref))
    authority = {**f.authorization, 'required_corpora': value['corpora']}
    assert extension.verify_corpus_manifest(ref, authorization=authority, load=f.store.load) == value


@pytest.mark.parametrize('field,value', [('schema_version', p.CORPUS_MANIFEST_SCHEMA), ('selection_index', 3),
    ('selection_index', 7), ('selection_index', True), ('previous_manifest', None), ('frozen_at', old.stamp(1)),
    ('extension_approval', None), ('approval', None)])
def test_extension_manifest_cannot_shrink_expand_skip_or_grant_its_own_authority(chain, field, value):
    chain.value[field] = value; rewrite(chain)
    with pytest.raises(EvidenceError): verify(chain)


@pytest.mark.parametrize('mutation', ['extra', 'missing_corpus', 'extra_corpus', 'duplicate_path', 'duplicate_hash',
    'baseline_rewrite', 'confirmation1_rewrite', 'required_map', 'wrong_identity', 'wrong_revision', 'wrong_weights'])
def test_all_old_and_new_corpus_and_selected_identity_bindings_are_required(chain, mutation):
    f = chain
    if mutation == 'extra': f.value['quality_pass'] = True
    elif mutation == 'missing_corpus': del f.value['corpora']['confirmation_2']
    elif mutation == 'extra_corpus': f.value['corpora']['confirmation_5'] = copy.deepcopy(f.value['corpora']['confirmation_4'])
    elif mutation in {'duplicate_path', 'duplicate_hash'}:
        key = 'path' if mutation == 'duplicate_path' else 'sha256'
        f.value['corpora']['confirmation_4'][key] = f.value['corpora']['confirmation_3'][key]
    elif mutation == 'baseline_rewrite': f.value['baseline_corpora']['original']['sha256'] = old.SHA
    elif mutation == 'confirmation1_rewrite': f.value['corpora']['confirmation_1']['sha256'] = old.SHA
    elif mutation == 'required_map': del f.authorization['required_corpora']['confirmation_4']
    else: f.authorization['identity'][{'wrong_identity': 'model_id', 'wrong_revision': 'model_revision', 'wrong_weights': 'weights_sha256'}[mutation]] = 'different'
    rewrite(f)
    with pytest.raises(EvidenceError): verify(f)


@pytest.mark.parametrize('field,value', [('maximum_candidates', 3), ('maximum_candidates', 7), ('selection_index', 5),
    ('enabled', True), ('model_scored', True), ('model_loaded', True), ('weights_acquired', True),
    ('status', 'quality_pass'), ('recorded_at', old.stamp(-291)), ('recorded_at', old.stamp(1))])
def test_selected_manifest_stays_within_exact_approved_unscored_selection(chain, field, value):
    modify_selected(chain, lambda selected: selected.update({field: value}))
    with pytest.raises(EvidenceError): verify(chain)


@pytest.mark.parametrize('field,value', [('initial_existing_cases', 1800), ('fresh_blind_confirmation_cases', 599),
    ('english_each_corpus_recall_min', .89), ('english_each_corpus_fpr_max', .1), ('cutoff', .89),
    ('window_tokens', 256), ('stride_tokens', 512), ('maximum_windows', 17), ('score_aggregation', 'mean'),
    ('cpu_cores', 3), ('memory_mib', 4096), ('torch_threads', 3), ('torch_interop_threads', 2),
    ('zero_operational_errors', False), ('spanish_enabled', True), ('retries', 1), ('warmups', 1),
    ('calibration', True), ('training', True), ('native_published_thresholds_used', True),
    ('final_production_worker_and_all_operational_gates_required', False)])
def test_extension_never_changes_any_quality_resource_or_runtime_limit(chain, field, value):
    modify_selected(chain, lambda selected: selected['qualification'].update({field: value}))
    with pytest.raises(EvidenceError): verify(chain)


@pytest.mark.parametrize('field,value', [('maximum_additional_candidates', 4), ('maximum_total_bounded_candidates', 7),
    ('first_campaign_consumed', 2), ('new_candidates_consumed', 1), ('permitted_selection_indices', [4, 5, 6, 7]),
    ('permitted_selection_indices', [4, 5, True]), ('user_answer', 'proposed'), ('status', 'qualified'),
    ('previous_approval', None), ('proposal', None)])
def test_approval_semantics_cannot_expand_even_if_test_reanchors_exact_bytes(chain, monkeypatch, field, value):
    f = chain; approval = p.document(f.store.load(f.approval)); approval[field] = value
    ref = f.store.put('changed-approval.json', approval); monkeypatch.setattr(extension, 'EXTENSION_APPROVAL_SHA256', ref['sha256'])
    f.value['extension_approval'] = ref
    modify_selected(f, lambda selected: selected.update(approval=ref))
    with pytest.raises(EvidenceError): verify(f)


def test_approval_raw_hash_cannot_be_reanchored_by_consistent_manifest(chain):
    approval = p.document(chain.store.load(chain.approval)); approval['authority'] += ' extra'
    ref = chain.store.put('different-approval.json', approval); chain.value['extension_approval'] = ref
    modify_selected(chain, lambda selected: selected.update(approval=ref))
    with pytest.raises(EvidenceError): verify(chain)


@pytest.mark.parametrize('fault', ['changed_bytes', 'different_reference', 'index2', 'time_after_extension'])
def test_predecessor_three_is_exact_immutable_frozen_campaign_anchor(chain, monkeypatch, fault):
    f = chain; ref = f.value['previous_manifest']; value = p.document(f.store.load(ref))
    if fault == 'index2': value['selection_index'] = 2
    elif fault == 'time_after_extension': value['frozen_at'] = old.stamp(-280)
    else: value['frozen_at'] = old.stamp(-296)
    if fault == 'changed_bytes': f.store.put(ref['path'], value)
    else:
        changed = f.store.put('rewritten-predecessor.json', value); f.value['previous_manifest'] = changed
        if fault != 'different_reference': monkeypatch.setattr(extension, 'LEGACY_FINAL_MANIFEST_SHA256', changed['sha256'])
    rewrite(f)
    with pytest.raises(EvidenceError): verify(f)


@pytest.mark.parametrize('index', [5, 6])
@pytest.mark.parametrize('fault', ['skip', 'rewrite_earlier', 'extension_change'])
def test_later_extension_recursion_never_drops_or_rewrites_a_consumed_slot(monkeypatch, index, fault):
    f = build(monkeypatch, index)
    if fault == 'skip':
        name = 'corpus-manifest-3.json'; path = '/synthetic/' + name
        f.value['previous_manifest'] = {'path': path, 'sha256': p.raw_digest(f.store.raws[path])}
    elif fault == 'rewrite_earlier': f.value['corpora']['confirmation_4']['sha256'] = old.SHA
    else:
        previous = p.document(f.store.load(f.value['previous_manifest'])); previous['extension_approval']['sha256'] = old.SHA
        f.value['previous_manifest'] = f.store.put('different-extension-history.json', previous)
    rewrite(f)
    with pytest.raises(EvidenceError): verify(f)


@pytest.mark.parametrize('field,value', [('selection_index', 3), ('model_evaluation_performed', True),
    ('normalization', 'casefold_only'), ('within_corpus_duplicates', False), ('threshold', .5),
    ('minimum_recall', .89), ('maximum_false_positive_rate', .06), ('limitations', []), ('prior_corpora', {}),
    ('prior_corpus_overlap', {}), ('status', 'pass')])
def test_parent_overlap_claim_must_match_exact_computed_evidence(chain, field, value):
    f = chain; review = p.document(f.store.load(f.value['parent_overlap_review'])); review[field] = value
    f.value['parent_overlap_review'] = f.store.put('changed-review.json', review); rewrite(f)
    with pytest.raises(EvidenceError): verify(f)


@pytest.mark.parametrize('field,value', [('candidate_information_accessed', True), ('evaluation_calls', 1),
    ('family_count', 59), ('variants_per_family', 9), ('texts_and_labels_immutable', False), ('state', 'proposed')])
def test_independent_blind_author_receipt_is_still_mandatory(chain, field, value):
    f = chain; author = p.document(f.store.load(f.value['author_freeze'])); author[field] = value
    f.value['author_freeze'] = f.store.put('author-4/changed-freeze.json', author)
    review = p.document(f.store.load(f.value['parent_overlap_review'])); review['author_freeze'] = f.value['author_freeze']
    f.value['parent_overlap_review'] = f.store.put('changed-review.json', review); rewrite(f)
    with pytest.raises(EvidenceError): verify(f)


@pytest.mark.parametrize('fault', ['argument_mutation', 'raw_hash', 'late_drift', 'outer_authorization', 'outer_reference'])
def test_supplied_reader_cannot_change_any_authority_or_raw_bytes(chain, fault):
    f = chain; calls = {}; original = f.store.load
    def changed(ref):
        raw = original(ref); path = ref['path']; calls[path] = calls.get(path, 0) + 1
        if fault == 'argument_mutation': ref['sha256'] = old.SHA
        elif fault == 'raw_hash' or fault == 'late_drift' and path == f.proposal['path'] and calls[path] > 1: return raw + b' '
        elif fault == 'outer_authorization': f.authorization['created_at'] = old.stamp(1)
        elif fault == 'outer_reference': f.ref['path'] = '/synthetic/mutated.json'
        return raw
    with pytest.raises(EvidenceError):
        extension.verify_corpus_manifest(f.ref, authorization=f.authorization, load=changed)


def rebind_confirmation(f, corpus):
    key = 'confirmation_4'; current = f.value['corpora'][key]
    changed = f.store.put(current['path'], corpus); f.value['corpora'][key] = changed
    f.authorization['required_corpora'][key] = changed
    author = p.document(f.store.load(f.value['author_freeze']))
    author['files'][current['path'].rsplit('/', 1)[1]] = changed['sha256']
    try:
        projection = p.corpus_projection(f.store.load(changed), key)
    except EvidenceError:
        pass
    else:
        author['full_normalized_text_uniqueness']['ordered_normalized_text_sha256'] = projection['ordered_sha256']
    f.value['author_freeze'] = f.store.put('author-4/changed-freeze.json', author)
    review = p.document(f.store.load(f.value['parent_overlap_review']))
    review.update(confirmation=changed, author_freeze=f.value['author_freeze'])
    f.value['parent_overlap_review'] = f.store.put('changed-overlap-review.json', review)
    rewrite(f)


@pytest.mark.parametrize('fault', ['old_original_overlap', 'old_confirmation_overlap', 'within_duplicate', 'family_count',
    'label_count', 'empty_text', 'access_to_model', 'access_to_example', 'inference', 'false_blindness'])
def test_corpus_bytes_are_recomputed_beyond_consistent_author_and_review_hashes(chain, fault):
    f = chain; corpus = p.document(f.store.load(f.value['corpora']['confirmation_4']))
    if fault.endswith('_overlap'):
        key = 'original' if fault == 'old_original_overlap' else 'confirmation_3'
        old_corpus = p.document(f.store.load(f.value['corpora'][key]))
        # Same complete normalized text even through whitespace/case differences.
        corpus['cases'][0]['part']['text'] = '  ' + old_corpus['cases'][0]['part']['text'].upper() + '\t\n'
    elif fault == 'within_duplicate': corpus['cases'][0]['part']['text'] = corpus['cases'][1]['part']['text']
    elif fault == 'family_count': corpus['cases'][0]['family_id'] = 'extra-family'
    elif fault == 'label_count': corpus['cases'][0]['expected_injection'] = False
    elif fault == 'empty_text': corpus['cases'][0]['part']['text'] = ' \t\n'
    else:
        field = {'access_to_model': 'candidate_model_identity_access', 'access_to_example': 'prior_corpus_example_content_access',
            'inference': 'model_inference_calls_for_evaluation', 'false_blindness': 'blind'}[fault]
        corpus['provenance'][field] = 1 if fault == 'inference' else fault != 'false_blindness'
    rebind_confirmation(f, corpus)
    with pytest.raises(EvidenceError): verify(f)
