"""Exact immutable author-v8 metadata compatibility; no source execution or IO.

Only this one frozen corpus is admitted. Derived metadata is explicitly a parent
record, never an author amendment. Original references and all cases stay intact.
The caller supplies its bounded stable reader; downloaded author code is data.
"""
from __future__ import annotations

import copy
import statistics
from pathlib import PurePosixPath

from . import classifier_provenance as p

CORPUS_NAME = 'injection-independent-confirmation-v8.json'
CORPUS_ID = 'injection-independent-confirmation-v8-20261012t1845z-c39d7b61'
CORPUS_SHA256 = '3ff31eab07d9310e1f1d2cf33e2a3df19fc62cb53fe4c277dd09c5664dacd7e9'
FREEZE_SHA256 = '04c75629138907e231fc92136f9467bf94b5bdd87c1ad206c8b0d04d184a0bb0'
FILES = {
    CORPUS_NAME: CORPUS_SHA256,
    'FREEZE.json': FREEZE_SHA256,
    'AUTHORSHIP.md': '2376bae1004afd0bc875ec0a759dd13a338e7736c7c3db9904ce4a4f9099bb95',
    'VALIDATION.json': 'dbd8a678fa35644b7b1cf86b469e7099d07db76b8f7335d10ff55703ffd0145f',
    'author_corpus.py': '235575f1d4432b07b6e1839930dbff31f73d82576686c81768752450ee823d54',
    'AUTHORING_STATUS.md': '1168f133c113d76605c7d337aea0df5a3759a32c218c3f9f4fac58b9e495e681',
    'literal_sources_01.json': '731216e03eed5f7b8a0086c77d26fd3980e7de570ebbcae5f8bc657833cb4268',
    'literal_sources_02.json': '2d51d97e8f70c0418f8b7502027a6fd8120d8625cbeb26b92f4dbe66ee3e3a83',
    'literal_sources_03.json': '6fb2259d745d3bcd9dd9856694fcaac0cab30f6b9d79f95082fa80a159f8ef7f',
    'literal_sources_04.json': 'c3e21df668cabff834a9e3e7050afcba0b63fdc7d73a4d20ae63cf5479299bcc',
    'literal_sources_05.json': 'd71717853ca700db5801144520a1e5b98a55f07fe030183f7dbd7fe394f876b1',
    'literal_sources_06.json': '9a1bcc67a08f7bd2af51357361b56426c580d077c3f6f57c6c9f5e195e9b236f',
}
LITERALS = tuple(f'literal_sources_{index:02d}.json' for index in range(1, 7))
NORMALIZATION = 'Unicode NFKC, Unicode casefold, collapse every whitespace run to a single ASCII space, strip both ends; compare the complete normalized part.text.'
FLAT_COUNTS = {'en_malicious': 200, 'en_benign': 200, 'es_malicious': 100, 'es_benign': 100}
DERIVATION = {
    'counts': 'Recomputed from all original cases; checked against nested original freeze and validation counts.',
    'ordered_normalized_text_sha256': 'Computed by parent verifier from original case order; absent from original author receipt.',
    'synthetic': 'Original provenance.corpus_kind and AUTHORSHIP.md explicitly describe synthetic authorship.',
    'candidate_model_access': 'Original candidate_information_accessed=false and model_access statement cover identity, configuration and results.',
    'prior_and_repository_access': 'Original repository_or_prior_corpora_accessed=false and AUTHORSHIP.md deny any repository or prior-corpus access, including schema access.',
    'external_access': 'Original external_data_or_network_accessed=false.',
    'human_adjudication': 'Original human_adjudication=false; same-author semantic review is retained as a limitation.',
    'evaluation_calls': 'Original evaluation_calls=0; authoring by a language-model agent is not relabeled as candidate inference.',
    'authored_languages': 'Original600 cases and literal families are en/es; AUTHORSHIP.md states direct Spanish authorship.',
}


def _read(ref, load):
    p.reference(ref)
    snapshot = copy.deepcopy(ref)
    argument = copy.deepcopy(snapshot)
    raw = load(argument)
    p.require(ref == snapshot and argument == snapshot and p.raw_digest(raw) == snapshot['sha256'])
    return raw


def _statistics(cases, validation, freeze):
    counts = {'total': len(cases), 'benign': 0, 'malicious': 0, 'languages': {}}
    family = {}
    for row in cases:
        family.setdefault(row['family_id'], []).append(row)
    for language in ('en', 'es'):
        rows = [row for row in cases if row['language'] == language]
        benign = [row for row in rows if row['expected_injection'] is False]
        indirect = sum(row['indirect'] for row in rows)
        quoted = sum(row['quoted_security'] for row in benign)
        counts['languages'][language] = {'total': len(rows), 'benign': len(benign),
            'malicious': len(rows) - len(benign), 'indirect': indirect, 'benign_quoted_security': quoted,
            'families': len({row['family_id'] for row in rows}), 'indirect_fraction': indirect / len(rows),
            'benign_quoted_security_fraction': quoted / len(benign)}
        counts['benign'] += len(benign)
        counts['malicious'] += len(rows) - len(benign)
    p.require(p.canonical(counts) == p.canonical(validation['counts']) == p.canonical(freeze['counts']))
    for rows in family.values():
        p.require(len(rows) == 10 and sum(row['expected_injection'] for row in rows) == 5
                  and len({row['language'] for row in rows}) == 1)
    case_ids = {row['case_id'] for row in cases}
    part_ids = {row['part']['part_id'] for row in cases}
    family_ids = set(family)
    p.require(len(case_ids) == len(part_ids) == 600 and len(family_ids) == 60
              and not (case_ids & part_ids or case_ids & family_ids or part_ids & family_ids)
              and not any(row['quoted_security'] and row['expected_injection'] for row in cases))
    for key, values in (
        ('text_utf8_bytes', [len(row['part']['text'].encode()) for row in cases]),
        ('text_whitespace_words', [len(row['part']['text'].split()) for row in cases]),
    ):
        p.require(validation[key] == {'minimum': min(values), 'median': statistics.median(values), 'maximum': max(values)})


def _literal_rows(documents, cases):
    """Authenticate literal prose and metadata without executing author_corpus.py."""
    expected = {}
    roles = ('retrieval', 'user', 'tool', 'user', 'user', 'retrieval', 'tool', 'retrieved', 'user', 'user')
    for name in LITERALS:
        families = p.document(b'{"families":' + documents[name] + b'}')['families']
        p.require(type(families) is list and len(families) == 10)
        for family in families:
            p.exact(family, {'family', 'language', 'category', 'texts'})
            p.require(family['language'] in {'en', 'es'} and type(family['texts']) is list and len(family['texts']) == 10)
            family_id = f"confirmation-v8-{family['language']}-{family['family']}"
            for index, text in enumerate(family['texts']):
                malicious = index >= 5
                case_id = f"{family_id}-{'m' if malicious else 'b'}{index % 5 + 1:02d}"
                p.require(case_id not in expected)
                expected[case_id] = {'case_id': case_id, 'language': family['language'], 'expected_injection': malicious,
                    'split': 'heldout', 'family_id': family_id, 'category': family['category'],
                    'indirect': index in {0, 2, 5, 6, 7}, 'quoted_security': index in {0, 1},
                    'part': {'part_id': case_id + '-part', 'role': roles[index], 'text': text}}
    p.require(len(expected) == 600 and all(p.canonical(row) == p.canonical(expected.get(row['case_id'])) for row in cases))


def derive(*, corpus_ref, author_ref, load):
    """Return authenticated original data and explicitly derived metadata copies."""
    p.reference(corpus_ref)
    p.reference(author_ref)
    p.require(corpus_ref['sha256'] == CORPUS_SHA256 and author_ref['sha256'] == FREEZE_SHA256
              and PurePosixPath(corpus_ref['path']).name == CORPUS_NAME
              and PurePosixPath(author_ref['path']).name == 'FREEZE.json')
    directory = PurePosixPath(author_ref['path']).parent
    references = {name: {'path': str(directory / name), 'sha256': pin} for name, pin in FILES.items()}
    references[CORPUS_NAME] = copy.deepcopy(corpus_ref)
    p.require(references['FREEZE.json'] == author_ref)
    documents = {name: _read(ref, load) for name, ref in references.items()}
    corpus = p.document(documents[CORPUS_NAME])
    projection = p.corpus_projection(documents[CORPUS_NAME], 'confirmation_6')
    freeze = p.document(documents['FREEZE.json'])
    validation = p.document(documents['VALIDATION.json'])
    p.require(corpus['corpus_id'] == freeze['corpus_id'] == validation['corpus_id'] == CORPUS_ID
              and validation['corpus_sha256'] == CORPUS_SHA256 and validation['status'] == 'passed'
              and p.timestamp(validation['validated_at_utc']) <= p.timestamp(freeze['frozen_at_utc']))
    required = {name: pin for name, pin in FILES.items() if name not in LITERALS and name != 'FREEZE.json'}
    p.require(freeze['files'] == required and freeze['candidate_information_accessed'] is False
              and freeze['texts_and_labels_immutable'] is True and freeze['state'] == 'frozen_before_evaluation'
              and type(freeze['evaluation_calls']) is int and freeze['evaluation_calls'] == 0)
    p.require(validation['candidate_information_accessed'] is False and type(validation['evaluation_calls']) is int
              and validation['evaluation_calls'] == 0 and validation['per_family_balance'] == {'benign': 5, 'malicious': 5})
    _statistics(corpus['cases'], validation, freeze)
    _literal_rows(documents, corpus['cases'])
    provenance = corpus['provenance']
    p.require(provenance['independent'] is True and provenance['blind'] is True
              and provenance['corpus_kind'] == 'single-author synthetic, independently written for this task'
              and provenance['candidate_information_accessed'] is False
              and provenance['external_data_or_network_accessed'] is False
              and provenance['repository_or_prior_corpora_accessed'] is False
              and provenance['human_adjudication'] is False
              and type(provenance['evaluation_calls']) is int and provenance['evaluation_calls'] == 0
              and provenance['literal_source_files'] == validation['literal_source_files'] == {name: FILES[name] for name in LITERALS}
              and provenance['semantic_review'] == validation['semantic_review'])
    p.require(provenance['semantic_review']['completed_before_freeze'] is True
              and provenance['semantic_review']['model_evaluation_used'] is False
              and provenance['semantic_review']['labels_changed_from_evaluation_feedback'] is False)
    uniqueness = {'method': NORMALIZATION, 'total': 600, 'unique': 600, 'duplicates': projection['duplicates'],
                  'ordered_normalized_text_sha256': projection['ordered_sha256']}
    original_unique = {'passed': True, 'total': 600, 'unique': 600,
                       'normalization': 'NFKC, then casefold, then collapse Unicode whitespace'}
    p.require(p.canonical(freeze['full_normalized_text_uniqueness']) == p.canonical(original_unique)
              == p.canonical(validation['full_normalized_text_uniqueness']))
    mapped = {**copy.deepcopy(provenance), 'synthetic': True, 'prior_corpus_example_content_access': False,
        'candidate_model_identity_access': False, 'candidate_model_configuration_access': False,
        'candidate_model_result_access': False, 'repository_code_access': False,
        'external_data_or_network_access': False, 'human_adjudicated': False,
        'model_inference_calls_for_evaluation': 0, 'authored_languages': ['en', 'es'],
        'prior_corpus_access': False, 'schema_shape_only_access': False, 'schema_shape_source': None}
    derived = {'counts': copy.deepcopy(FLAT_COUNTS), 'full_normalized_text_uniqueness': uniqueness, 'provenance': mapped}
    # Never replace raw references, create rewritten corpus bytes or claim the
    # author recorded the ordered digest. Only these predicate inputs are copies.
    normalized_author = {**copy.deepcopy(freeze), 'counts': derived['counts'], 'full_normalized_text_uniqueness': uniqueness}
    normalized_projection = {**copy.deepcopy(projection), 'provenance': mapped}
    return {'references': references, 'freeze': freeze, 'projection': projection, 'derived': derived,
            'normalized_author': normalized_author, 'normalized_projection': normalized_projection}


def normalization_record(*, corpus_ref, author_ref, created_at, load):
    """Build a parent-owned record; this function has no filesystem writes."""
    value = derive(corpus_ref=corpus_ref, author_ref=author_ref, load=load)
    p.require(p.timestamp(value['freeze']['frozen_at_utc']) <= p.timestamp(created_at))
    return {'schema_version': 'confirmation-v8-parent-metadata-normalization-v1', 'created_at': created_at,
        'scope': 'parent_derived_format_compatibility_not_author_amendment', 'selection_index': 6,
        'corpus': copy.deepcopy(corpus_ref), 'author_freeze': copy.deepcopy(author_ref),
        'original_files': value['references'], 'derived_metadata': value['derived'], 'derivation': copy.deepcopy(DERIVATION),
        'author_recorded_ordered_hash': False, 'texts_changed': False, 'labels_changed': False,
        'case_order_changed': False, 'model_evaluation_performed': False,
        'limitations': ['synthetic', 'related_variants_within_families', 'not_human_adjudicated']}


def verify(*, corpus_ref, author_ref, normalization_ref, prior, frozen_at, load):
    value = derive(corpus_ref=corpus_ref, author_ref=author_ref, load=load)
    record = p.document(_read(normalization_ref, load))
    expected = normalization_record(corpus_ref=corpus_ref, author_ref=author_ref, created_at=record.get('created_at'), load=load)
    p.require(p.canonical(record) == p.canonical(expected)
              and p.timestamp(record['created_at']) <= p.timestamp(frozen_at))
    p.verify_confirmation_author(value['normalized_author'], corpus_ref=corpus_ref, author_ref=author_ref,
        projection=value['normalized_projection'], prior=prior, frozen_at=frozen_at, load=load)
    return copy.deepcopy(value['projection'])
