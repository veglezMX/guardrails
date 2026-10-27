"""Selection-six corpus authority with exact immutable author-v8 compatibility.

Legacy and selections four/five are delegated to the frozen original verifier.
Only selection six uses this version's explicit parent normalization record.
No accepted corpus authority establishes a model quality or deployment pass.
"""
from __future__ import annotations

import copy

from . import classifier_confirmation_v8 as author_v8
from . import classifier_corpus_extension as original
from . import classifier_provenance as p

CORPUS_MANIFEST_SCHEMA = 'evolution-classifier-qualification-corpora-v3'
SELECTION_SHA256 = '474f650267eaf8768ec57561eb1905ff87d254f0bb6f3e8b3faa5871128cf1d8'
PREVIOUS_MANIFEST_SHA256 = '764ca98ebe7460704cc5b9e3a7faa30d6535f7a728b6e950db7ae89d0d1d855a'
FIELDS = original.FIELDS | {'author_normalization'}
corpus_projection = original.corpus_projection


def verify_confirmation(manifest, *, load, projected):
    corpora = manifest['corpora']
    preceding = {name: ref for name, ref in corpora.items() if name != 'confirmation_6'}
    author_ref = p.reference(manifest['author_freeze'])
    checked = author_v8.verify(corpus_ref=corpora['confirmation_6'], author_ref=author_ref,
        normalization_ref=manifest['author_normalization'], prior=preceding,
        frozen_at=manifest['frozen_at'], load=load)
    for name, ref in preceding.items():
        p.require(not checked['texts'] & projected(name, ref)['texts'])
    review = p.document(load(p.reference(manifest['parent_overlap_review'])))
    p.exact(review, {'schema_version', 'reviewed_at', 'selection_index', 'confirmation', 'author_freeze',
        'prior_corpora', 'normalization', 'counts', 'within_corpus_duplicates', 'prior_corpus_overlap',
        'threshold', 'minimum_recall', 'maximum_false_positive_rate', 'model_evaluation_performed', 'limitations', 'status'})
    p.require(review['schema_version'] == 'evolution-classifier-confirmation-overlap-v1'
        and type(review['selection_index']) is int and review['selection_index'] == 6
        and review['confirmation'] == corpora['confirmation_6'] and review['author_freeze'] == author_ref
        and review['prior_corpora'] == preceding and review['normalization'] == 'unicode_nfkc_casefold_whitespace_v1'
        and p.canonical(review['counts']) == p.canonical({'total': 600, 'en_malicious': 200,
            'en_benign': 200, 'es_malicious': 100, 'es_benign': 100, 'families': 60})
        and type(review['within_corpus_duplicates']) is int and review['within_corpus_duplicates'] == checked['duplicates'] == 0
        and review['prior_corpus_overlap'] == {name: 0 for name in preceding}
        and all(type(number) is int for number in review['prior_corpus_overlap'].values())
        and review['model_evaluation_performed'] is False
        and review['limitations'] == ['synthetic', 'related_variants_within_families', 'not_human_adjudicated']
        and review['status'] == 'frozen_confirmation_schema_and_overlap_verified'
        and p.timestamp(p.document(load(author_ref))['frozen_at_utc']) <= p.timestamp(review['reviewed_at'])
            <= p.timestamp(manifest['frozen_at']))
    for name, expected in (('threshold', .90), ('minimum_recall', .90), ('maximum_false_positive_rate', .05)):
        p.number(review[name], expected, expected)


def verify_corpus_manifest(ref, *, authorization, load):
    """Authenticate current and all original history with one stable reader."""
    p.require(callable(load) and type(authorization) is dict)
    p.reference(ref)
    reference_before, authorization_before = p.canonical(ref), p.canonical(authorization)
    reference, admitted = copy.deepcopy(ref), copy.deepcopy(authorization)
    p.timestamp(admitted['created_at'])
    seen, projections = {}, {}

    def checked_load(item):
        p.reference(item)
        snapshot, argument = p.canonical(item), copy.deepcopy(item)
        raw = load(argument)
        p.require(p.canonical(argument) == snapshot and p.raw_digest(raw) == item['sha256'])
        key = (item['path'], item['sha256'])
        p.require(not any(path == item['path'] and pin != item['sha256'] for path, pin in seen))
        if key in seen:
            p.require(seen[key] == raw)
        seen[key] = raw
        return raw

    def projected(name, item):
        key = (name, item['path'], item['sha256'])
        if key not in projections:
            projections[key] = corpus_projection(checked_load(item), name)
        return projections[key]

    value = p.document(checked_load(reference))
    if value.get('schema_version') != CORPUS_MANIFEST_SCHEMA:
        p.integer(value.get('selection_index'), 1, 5)
        value = original.verify_corpus_manifest(reference, authorization=admitted, load=checked_load)
    else:
        p.exact(value, FIELDS)
        p.integer(value['selection_index'], 6, 6)
        p.require(p.timestamp(value['frozen_at']) <= p.timestamp(admitted['created_at']))
        baseline = p.exact(value['baseline_corpora'], p.BASELINE_CORPORA)
        for name, corpus in baseline.items():
            p.reference(corpus)
            p.require(corpus['sha256'] == p.BASELINE_CORPORA[name])
        corpora = p.exact(value['corpora'], set(p.BASELINE_CORPORA) | {f'confirmation_{index}' for index in range(1, 7)})
        for corpus in corpora.values():
            p.reference(corpus)
        p.require(all(corpora[name] == corpus for name, corpus in baseline.items())
            and len({row['path'] for row in corpora.values()}) == len(corpora)
            and len({row['sha256'] for row in corpora.values()}) == len(corpora))
        previous_ref = p.reference(value['previous_manifest'])
        p.require(previous_ref['sha256'] == PREVIOUS_MANIFEST_SHA256)
        previous = p.document(checked_load(previous_ref))
        p.integer(previous['selection_index'], 5, 5)
        previous_selected = p.document(checked_load(p.reference(previous['selection_manifest'])))
        previous_authority = {**admitted, 'identity': {**admitted['identity'],
            'model_id': previous_selected.get('repository'), 'model_revision': previous_selected.get('revision'),
            'weights_sha256': previous_selected.get('artifacts', {}).get('model.safetensors', {}).get('sha256')},
            'required_corpora': previous['corpora']}
        previous = original.verify_corpus_manifest(previous_ref, authorization=previous_authority, load=checked_load)
        p.require(previous['baseline_corpora'] == baseline and previous['approval'] == value['approval']
            and previous['extension_approval'] == value['extension_approval']
            and all(corpora[name] == corpus for name, corpus in previous['corpora'].items())
            and p.timestamp(previous['frozen_at']) < p.timestamp(value['frozen_at']))
        extension = original.verify_extension_approval(value['extension_approval'], original_approval=value['approval'], load=checked_load)
        selection_ref = p.reference(value['selection_manifest'])
        p.require(selection_ref['sha256'] == SELECTION_SHA256)
        selected = p.document(checked_load(selection_ref))
        p.require(selected.get('schema_version') == 'bounded-classifier-candidate-manifest-v1'
            and type(selected.get('selection_index')) is int and selected['selection_index'] == 6
            and type(selected.get('maximum_candidates')) is int and selected['maximum_candidates'] == 6
            and selected.get('approval') == value['extension_approval']
            and selected.get('status') == 'selected_for_acquisition_and_qualification_only'
            and selected.get('enabled') is False and selected.get('model_scored') is False
            and selected.get('model_loaded') is False and selected.get('weights_acquired') is False
            and p.timestamp(extension['recorded_at']) <= p.timestamp(selected['recorded_at']) <= p.timestamp(value['frozen_at'])
            and p.canonical(selected.get('qualification')) == p.canonical(original.qualification(6)))
        identity = admitted['identity']
        p.require(selected.get('repository') == identity['model_id'] and selected.get('revision') == identity['model_revision']
            and selected.get('artifacts', {}).get('model.safetensors', {}).get('sha256') == identity['weights_sha256'])
        verify_confirmation(value, load=checked_load, projected=projected)
    p.require(value['corpora'] == admitted['required_corpora'])
    for (path, pin), raw in tuple(seen.items()):
        p.require(checked_load({'path': path, 'sha256': pin}) == raw)
    p.require(p.canonical(authorization) == authorization_before and p.canonical(ref) == reference_before)
    return copy.deepcopy(value)
