"""Pure authority for explicitly approved cumulative classifier selections7–9.

All original1–6 corpus lineage and evidence remain verified by frozen predecessors.
Only approved cumulative counts grow; every quality/resource/window/deadline limit
and blind-author/overlap predicate stays unchanged. Corpus authority is not a
quality, final-worker, deployment or phase-gate pass. No filesystem or model IO.
"""
from __future__ import annotations

import copy

from . import classifier_provenance as prior
from . import classifier_corpus_extension as original
from . import classifier_corpus_extension_v2 as predecessor

CORPUS_MANIFEST_SCHEMA = 'evolution-classifier-qualification-corpora-v4'
EXTENSION_APPROVAL_SHA256 = '31ab3d35513ccdd2e683899337fdf01a474ecc7829a9e676f3a37b9729d06b77'
EXTENSION_PROPOSAL_SHA256 = '18de2c9ca682971cde91884d6502d08df68ada4ac32a9ff82ced28c864d93d59'
LEGACY_FINAL_MANIFEST_SHA256 = '0b63af536d6175962a1574668b9bdd1711b19228e61349eb575659dd7ae09875'
FIELDS = {'schema_version', 'frozen_at', 'selection_index', 'selection_manifest', 'approval',
    'extension_approval', 'second_extension_approval', 'previous_manifest', 'baseline_corpora', 'corpora', 'author_freeze', 'parent_overlap_review'}
corpus_projection = prior.corpus_projection


def qualification(index):
    """The same frozen limits; only cumulative existing-case count grows."""
    prior.integer(index, 7, 9)
    return {'initial_existing_cases': 1800 + (index - 1) * 600, 'fresh_blind_confirmation_cases': 600,
        'english_each_corpus_recall_min': .90, 'english_each_corpus_fpr_max': .05, 'cutoff': .90,
        'window_tokens': 512, 'stride_tokens': 256, 'maximum_windows': 16, 'score_aggregation': 'max',
        'cpu_cores': 2, 'memory_mib': 3072, 'torch_threads': 2, 'torch_interop_threads': 1,
        'zero_operational_errors': True, 'spanish_enabled': False, 'retries': 0, 'warmups': 0,
        'calibration': False, 'training': False, 'native_published_thresholds_used': False,
        'final_production_worker_and_all_operational_gates_required': True}


def verify_extension_approval(ref, *, original_approval, first_extension, load):
    prior.reference(ref); prior.reference(original_approval); prior.reference(first_extension)
    prior.require(ref['sha256'] == EXTENSION_APPROVAL_SHA256
        and original_approval['sha256'] == prior.SELECTION_APPROVAL_SHA256
        and first_extension['sha256'] == original.EXTENSION_APPROVAL_SHA256)
    value = prior.document(load(ref))
    prior.exact(value, {'schema_version', 'recorded_at', 'proposal', 'user_answer', 'question_item_id',
        'selection_indices', 'maximum_additional_candidates', 'previous_indices_exhausted', 'enabled_models',
        'threshold', 'minimum_recall', 'maximum_false_positive_rate', 'existing_corpus_count', 'existing_cases',
        'fresh_blind_cases_per_candidate', 'full_campaign_cases', 'resource_limits_unchanged',
        'original_deadlines_unchanged', 'retries_warmups_training_calibration_authorized',
        'automatic_further_expansion_authorized', 'final_production_worker_qualification_required',
        'all_six_phase_gates_required', 'final_one_owned_litellm_instance_test_required',
        'commit_push_merge_authorized', 'host_reboot_authorized'})
    prior.require(value['schema_version'] == 'classifier-bounded-selection-extension-approval-v1'
        and value['user_answer'] == 'Approve three more'
        and type(value['question_item_id']) is str and bool(value['question_item_id']))
    for key, expected in (('maximum_additional_candidates', 3), ('existing_corpus_count', 9),
                          ('existing_cases', 5400), ('fresh_blind_cases_per_candidate', 600)):
        prior.integer(value[key], expected, expected)
    for key, expected in (('selection_indices', [7, 8, 9]), ('previous_indices_exhausted', [1, 2, 3, 4, 5, 6]),
                          ('enabled_models', []), ('full_campaign_cases', [6000, 6600, 7200])):
        prior.require(prior.canonical(value[key]) == prior.canonical(expected))
    for key, expected in (('threshold', .90), ('minimum_recall', .90), ('maximum_false_positive_rate', .05)):
        prior.number(value[key], expected, expected)
    for key in ('resource_limits_unchanged', 'original_deadlines_unchanged',
                'final_production_worker_qualification_required', 'all_six_phase_gates_required',
                'final_one_owned_litellm_instance_test_required'):
        prior.require(value[key] is True)
    for key in ('retries_warmups_training_calibration_authorized', 'automatic_further_expansion_authorized',
                'commit_push_merge_authorized', 'host_reboot_authorized'):
        prior.require(value[key] is False)
    prior.timestamp(value['recorded_at']); prior.reference(value['proposal'])
    prior.require(value['proposal']['sha256'] == EXTENSION_PROPOSAL_SHA256)
    load(value['proposal'])
    return value


def verify_confirmation(manifest, *, load, projected):
    index = manifest['selection_index']; key = f'confirmation_{index}'
    corpora = manifest['corpora']; preceding = {name: ref for name, ref in corpora.items() if name != key}
    author_ref = prior.reference(manifest['author_freeze'])
    author = prior.document(load(author_ref)); checked = projected(key, corpora[key])
    prior.verify_confirmation_author(author, corpus_ref=corpora[key], author_ref=author_ref,
        projection=checked, prior=preceding, frozen_at=manifest['frozen_at'], load=load)
    for name, ref in preceding.items():
        prior.require(not checked['texts'] & projected(name, ref)['texts'])
    review = prior.document(load(prior.reference(manifest['parent_overlap_review'])))
    prior.exact(review, {'schema_version', 'reviewed_at', 'selection_index', 'confirmation', 'author_freeze',
        'prior_corpora', 'normalization', 'counts', 'within_corpus_duplicates', 'prior_corpus_overlap',
        'threshold', 'minimum_recall', 'maximum_false_positive_rate', 'model_evaluation_performed', 'limitations', 'status'})
    prior.require(review['schema_version'] == 'evolution-classifier-confirmation-overlap-v1'
        and type(review['selection_index']) is int and review['selection_index'] == index
        and review['confirmation'] == corpora[key] and review['author_freeze'] == author_ref
        and review['prior_corpora'] == preceding and review['normalization'] == 'unicode_nfkc_casefold_whitespace_v1'
        and prior.canonical(review['counts']) == prior.canonical({'total': 600, 'en_malicious': 200,
            'en_benign': 200, 'es_malicious': 100, 'es_benign': 100, 'families': 60})
        and type(review['within_corpus_duplicates']) is int and review['within_corpus_duplicates'] == checked['duplicates'] == 0
        and review['prior_corpus_overlap'] == {name: 0 for name in preceding}
        and all(type(number) is int for number in review['prior_corpus_overlap'].values())
        and review['model_evaluation_performed'] is False
        and review['limitations'] == ['synthetic', 'related_variants_within_families', 'not_human_adjudicated']
        and review['status'] == 'frozen_confirmation_schema_and_overlap_verified'
        and prior.timestamp(author['frozen_at_utc']) <= prior.timestamp(review['reviewed_at'])
            <= prior.timestamp(manifest['frozen_at']))
    for name, expected in (('threshold', .90), ('minimum_recall', .90), ('maximum_false_positive_rate', .05)):
        prior.number(review[name], expected, expected)


def verify_corpus_manifest(ref, *, authorization, load):
    """Return the authenticated manifest; never infer qualification or readiness.

    The caller independently pins ref and supplies identity, created_at, and the
    exact required_corpora mapping in authorization, as for the frozen verifier.
    Raw hashes are checked here too; callbacks cannot mutate references or change
    prior bytes between recursive verification and the final stability recheck.
    """
    prior.require(callable(load) and type(authorization) is dict)
    prior.reference(ref)
    reference_before = prior.canonical(ref)
    admitted_reference = copy.deepcopy(ref)
    authorization_before = prior.canonical(authorization)
    admitted = copy.deepcopy(authorization)
    prior.timestamp(admitted['created_at'])
    seen, projections = {}, {}
    def checked_load(item):
        prior.reference(item); snapshot = prior.canonical(item); argument = copy.deepcopy(item)
        raw = load(argument)
        prior.require(prior.canonical(argument) == snapshot and prior.raw_digest(raw) == item['sha256'])
        key = (item['path'], item['sha256'])
        prior.require(not any(pathname == item['path'] and hashed != item['sha256'] for pathname, hashed in seen))
        if key in seen:
            prior.require(seen[key] == raw)
        seen[key] = raw
        return raw
    def projected(name, item):
        key = (name, item['path'], item['sha256'])
        if key not in projections:
            projections[key] = corpus_projection(checked_load(item), name)
        return projections[key]
    def historical(item):
        prior.require(item['sha256'] == LEGACY_FINAL_MANIFEST_SHA256)
        value = prior.document(checked_load(item))
        prior.require(type(value.get('selection_index')) is int and value['selection_index'] == 6)
        selected = prior.document(checked_load(prior.reference(value['selection_manifest'])))
        authority = copy.deepcopy(admitted)
        authority['identity'] = {**authority['identity'], 'model_id': selected.get('repository'),
            'model_revision': selected.get('revision'),
            'weights_sha256': selected.get('artifacts', {}).get('model.safetensors', {}).get('sha256')}
        authority['required_corpora'] = value['corpora']
        # These are corpus lineage receipts, including failed selected models;
        # this does not grant any historical model a quality or runtime pass.
        return predecessor.verify_corpus_manifest(item, authorization=authority, load=checked_load)
    def check(item, expected=None):
        prior.reference(item); value = prior.document(checked_load(item))
        prior.exact(value, FIELDS)
        prior.require(value['schema_version'] == CORPUS_MANIFEST_SCHEMA)
        index = prior.integer(value['selection_index'], 7, 9)
        prior.require(expected is None or index == expected)
        prior.require(prior.timestamp(value['frozen_at']) <= prior.timestamp(admitted['created_at']))
        baseline = prior.exact(value['baseline_corpora'], prior.BASELINE_CORPORA)
        for name, corpus in baseline.items():
            prior.reference(corpus); prior.require(corpus['sha256'] == prior.BASELINE_CORPORA[name])
        corpora = prior.exact(value['corpora'], set(prior.BASELINE_CORPORA) | {f'confirmation_{i}' for i in range(1, index + 1)})
        for corpus in corpora.values(): prior.reference(corpus)
        prior.require(all(corpora[name] == corpus for name, corpus in baseline.items())
            and len({row['path'] for row in corpora.values()}) == len(corpora)
            and len({row['sha256'] for row in corpora.values()}) == len(corpora))
        previous_ref = prior.reference(value['previous_manifest'])
        previous = historical(previous_ref) if index == 7 else check(previous_ref, index - 1)
        prior.require(previous['baseline_corpora'] == baseline and previous['approval'] == value['approval']
            and previous['extension_approval'] == value['extension_approval']
            and all(corpora[name] == corpus for name, corpus in previous['corpora'].items())
            and prior.timestamp(previous['frozen_at']) < prior.timestamp(value['frozen_at']))
        if index > 7:
            prior.require(previous['second_extension_approval'] == value['second_extension_approval'])
        extension = verify_extension_approval(value['second_extension_approval'], original_approval=value['approval'], first_extension=value['extension_approval'], load=checked_load)
        if index == 7:
            prior.require(prior.timestamp(previous['frozen_at']) <= prior.timestamp(extension['recorded_at']))
        selected = prior.document(checked_load(prior.reference(value['selection_manifest'])))
        prior.require(selected.get('schema_version') == 'bounded-classifier-candidate-manifest-v1'
            and type(selected.get('selection_index')) is int and selected['selection_index'] == index
            and type(selected.get('maximum_candidates')) is int and selected['maximum_candidates'] == 9
            and selected.get('approval') == value['second_extension_approval']
            and selected.get('status') == 'selected_for_acquisition_and_qualification_only'
            and selected.get('enabled') is False and selected.get('model_scored') is False
            and selected.get('model_loaded') is False and selected.get('weights_acquired') is False
            and prior.timestamp(extension['recorded_at']) <= prior.timestamp(selected['recorded_at'])
                <= prior.timestamp(value['frozen_at'])
            and prior.canonical(selected.get('qualification')) == prior.canonical(qualification(index)))
        if expected is None:
            identity = admitted['identity']
            prior.require(selected.get('repository') == identity['model_id'] and selected.get('revision') == identity['model_revision']
                and selected.get('artifacts', {}).get('model.safetensors', {}).get('sha256') == identity['weights_sha256'])
        verify_confirmation(value, load=checked_load, projected=projected)
        return value
    root = prior.document(checked_load(admitted_reference))
    if root.get('schema_version') != CORPUS_MANIFEST_SCHEMA:
        prior.integer(root.get('selection_index'), 1, 6)
        value = predecessor.verify_corpus_manifest(admitted_reference, authorization=admitted, load=checked_load)
    else:
        value = check(admitted_reference)
    prior.require(value['corpora'] == admitted['required_corpora'])
    for (pathname, hashed), raw in tuple(seen.items()):
        prior.require(checked_load({'path': pathname, 'sha256': hashed}) == raw)
    prior.require(prior.canonical(authorization) == authorization_before and prior.canonical(ref) == reference_before)
    return copy.deepcopy(value)
