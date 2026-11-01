"""Selected-worker evidence is required; synthetic rows grant no model authority."""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace as N

import pytest
from evolution import classifier_final_worker_evidence as e
from evolution import classifier_final_worker_qualification as s
from evolution import classifier_provenance as p
from evolution import classifier_provenance_io as io
from evolution.registry import EvidenceError
from mvp.tests.evolution.unit.test_classifier_provenance import case

from mvp.tests.evolution.unit.test_classifier_loaded_evidence import isolated_network


@pytest.fixture(autouse=True)
def no_external_operations(monkeypatch):
    import socket
    import subprocess
    def forbidden(*args, **kwargs):
        raise AssertionError('no actual operations in synthetic evidence checks')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def raw(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def ref(name, value):
    return {'path': '/synthetic/' + name, 'sha256': hashlib.sha256(value).hexdigest()}


def data(index=3):
    names = ['original', 'heldout_1', 'heldout_2'] + [f'confirmation_{i}' for i in range(1, index + 1)]
    documents, corpora, rows = {}, {}, []
    for name in names:
        cases = []
        for lang, count in [('en', 200), ('es', 100)]:
            for label in [False, True]:
                for number in range(count):
                    row = {'case_id': f'{name}-{lang}-{label}-{number}', 'language': lang,
                           'category': 'synthetic', 'family_id': 'synthetic-family', 'expected_injection': label}
                    cases.append(row)
                    rows.append({key: row[key] for key in ('case_id', 'language', 'category', 'family_id')} | {
                        'corpus': name, 'expected': label, 'predicted': label, 'score': .95 if label else .1,
                        'error': None, 'elapsed_ms': 1.0, 'windows': 1})
        documents[name] = {'cases': cases}
        corpora[name] = ref(name + '.json', raw(documents[name]))
    return N(documents=documents, corpora=corpora, rows=rows)


def rowbytes(rows):
    return b''.join(raw(row) for row in rows)


@pytest.mark.parametrize('index', [1, 2, 3, 4, 5, 6])
def test_complete_cumulative_rows_recompute_every_matrix(index):
    d = data(index); matrices, windows = e.rows(rowbytes(d.rows), d.documents, d.corpora)
    assert windows == 600 * (index + 3)
    assert all(langs['en'] == {'tp': 200, 'fn': 0, 'tn': 200, 'fp': 0, 'errors': 0} for langs in matrices.values())


@pytest.mark.parametrize('corpus', ['original', 'heldout_1', 'heldout_2', 'confirmation_1', 'confirmation_2', 'confirmation_3',
    'confirmation_4', 'confirmation_5', 'confirmation_6'])
@pytest.mark.parametrize('fault', ['false_positive', 'recall'])
def test_one_failing_english_corpus_cannot_hide_in_aggregate(corpus, fault):
    d = data(6); count = 0
    for row in d.rows:
        if row['corpus'] == corpus and row['language'] == 'en' and row['expected'] == (fault == 'recall'):
            row['score'] = .1 if fault == 'recall' else .9
            row['predicted'] = row['score'] >= .9; count += 1
            if count == (21 if fault == 'recall' else 11): break
    with pytest.raises(EvidenceError): e.rows(rowbytes(d.rows), d.documents, d.corpora)


def test_spanish_matrix_remains_diagnostic():
    d = data()
    for row in d.rows:
        if row['language'] == 'es': row['predicted'], row['score'] = not row['expected'], .1 if row['expected'] else .95
    matrices, _ = e.rows(rowbytes(d.rows), d.documents, d.corpora)
    assert all(langs['es'] == {'tp': 0, 'fn': 100, 'tn': 0, 'fp': 100, 'errors': 0} for langs in matrices.values())


@pytest.mark.parametrize('fault', ['missing', 'extra', 'reorder', 'label', 'score_bool', 'predicted', 'error', 'late', 'zero_windows', 'oversize_windows', 'extra_key'])
def test_all_rows_original_coverage_and_algebra_are_required(fault):
    d = data(1)
    if fault == 'missing': d.rows.pop()
    elif fault == 'extra': d.rows.append(copy.deepcopy(d.rows[-1]))
    elif fault == 'reorder': d.rows[0], d.rows[1] = d.rows[1], d.rows[0]
    elif fault == 'label': d.rows[0]['expected'] = True
    elif fault == 'score_bool': d.rows[0]['score'] = False
    elif fault == 'predicted': d.rows[0]['predicted'] = True
    elif fault == 'error': d.rows[0]['error'] = 'evaluation_timeout'
    elif fault == 'late': d.rows[0]['elapsed_ms'] = 3000.0001
    elif fault == 'zero_windows': d.rows[0]['windows'] = 0
    elif fault == 'oversize_windows': d.rows[0]['windows'] = 17
    elif fault == 'extra_key': d.rows[0]['accepted_summary'] = True
    with pytest.raises(EvidenceError): e.rows(rowbytes(d.rows), d.documents, d.corpora)


def quality():
    d = data(1); identity = case('tls').identity
    sources = {name: 'b' * 64 for name in s.REQUIRED_RUNTIME_SOURCES}
    plan = {'schema_version': e.PLAN_SCHEMA, 'scope': e.SCOPE, 'created_at': '2026-10-12T00:00:00Z',
            'identity': identity, 'sources': sources, 'corpora': d.corpora, 'entrypoints': copy.deepcopy(e.ENTRYPOINTS),
            'limits': copy.deepcopy(e.LIMITS), 'output_directory': '/synthetic/quality', 'host_namespace': 'net:[1]',
            'argv': ['/synthetic/python', '-I', '-S', '-B', '/synthetic/producer.py']}
    for name in ('corpus_manifest', 'selection_manifest', 'producer', 'launcher', 'environment', 'model_artifacts'):
        plan[name] = ref(name + '.json', raw({'synthetic': name}))
    plan_ref = ref('plan.json', raw(plan)); network = isolated_network()
    started = {'schema_version': e.QUALITY_SCHEMA, 'scope': e.SCOPE, 'status': 'running',
               'created_at': '2026-10-12T00:00:01Z', 'plan_sha256': plan_ref['sha256'],
               'network': network, 'automatic_retry': False, 'warmup_requests': 0}
    for name in ('identity', 'sources', 'corpora', 'entrypoints', 'limits'): started[name] = copy.deepcopy(plan[name])
    rows = rowbytes(d.rows); matrices, windows = e.rows(rows, d.documents, d.corpora)
    result = copy.deepcopy(started) | {'status': 'passed', 'completed_at': '2026-10-12T00:02:00Z',
        'cases_sha256': hashlib.sha256(rows).hexdigest(), 'cases': 2400, 'matrices': matrices,
        'model_loads': 1, 'attempted_dispatches': 2400, 'completed_windows': windows, 'operational_errors': 0,
        'cpu': {'affinity': [0, 1], 'maximum_rss_bytes': 1024 ** 3, 'torch_threads': 2, 'torch_interop_threads': 1},
        'network_after': copy.deepcopy(network), 'spanish_enforcement_enabled': False}
    def check():
        return e.quality({'started.json': raw(started), 'result.json': raw(result), 'cases.jsonl': rows},
            plan=plan, plan_ref=plan_ref, identity=identity, sources=sources, corpora=d.corpora,
            documents=d.documents, host_namespace='net:[1]')
    return N(**locals())


def test_selected_worker_quality_requires_full_rows_and_returns_measured_resources():
    q = quality(); checked = q.check()
    assert checked['memory_mib'] == 1024 and checked['cpu_cores'] == 2


@pytest.mark.parametrize('fault', ['initial_schema', 'initial_scope', 'loader_seam', 'retry', 'warmup', 'reload',
    'partial_dispatch', 'summary_only', 'faked_matrix', 'memory', 'threads', 'spanish', 'network_drift'])
def test_initial_loader_or_incomplete_claim_cannot_substitute_for_selected_worker(fault):
    q = quality()
    if fault == 'initial_schema': q.started['schema_version'] = q.result['schema_version'] = 'bounded-candidate-initial-quality-v1'
    elif fault == 'initial_scope': q.started['scope'] = q.result['scope'] = 'historical_initial_qualification_not_selected_runtime'
    elif fault == 'loader_seam': q.plan['entrypoints']['load'] = 'services.classifier.piguard_candidate_03_compat.load_for_qualification'
    elif fault == 'retry': q.started['automatic_retry'] = q.result['automatic_retry'] = True
    elif fault == 'warmup': q.started['warmup_requests'] = q.result['warmup_requests'] = 1
    elif fault == 'reload': q.result['model_loads'] = 2
    elif fault == 'partial_dispatch': q.result['attempted_dispatches'] = 2399
    elif fault == 'summary_only': q.result['cases'] = 2399
    elif fault == 'faked_matrix': q.result['matrices']['original']['en']['tp'] -= 1
    elif fault == 'memory': q.result['cpu']['maximum_rss_bytes'] = 3072 * 1024 ** 2 + 1
    elif fault == 'threads': q.result['cpu']['torch_threads'] = 3
    elif fault == 'spanish': q.result['spanish_enforcement_enabled'] = True
    elif fault == 'network_drift': q.result['network_after']['namespace'] = 'net:[99]'
    with pytest.raises((EvidenceError, e.loaded.LoadedEvidenceError)): q.check()


def inventory(*, count=22257):
    base, site = '/synthetic/python', '/synthetic/venv/lib/python3.13/site-packages'
    executable = base + '/bin/python3.13'
    value = {executable: ['a' * 64, 12345]}
    for i in range(count - 1): value[f'{site}/package_{i:05d}/' + 'component_' * 15 + '.py'] = ['b' * 64, i]
    return N(base=base, site=site, executable=executable, value=value,
             options={'expected_files': count, 'base': base, 'site': site, 'executable': executable})


def test_real_large_inventory_parser_after_exact_io_and_verifier_reopen(tmp_path):
    tmp_path.chmod(0o700); inv = inventory(); body = raw(inv.value)
    assert p.MAX_BYTES < len(body) <= io.PRIVATE_LARGE_BYTES
    path = tmp_path / 'closure.json'; path.write_bytes(body); path.chmod(0o600)
    reference = {'path': str(path), 'sha256': hashlib.sha256(body).hexdigest()}
    allowance = {'schema_version': io.PRIVATE_LARGE_SCHEMA, 'kind': 'historical_installed_closure',
                 'reference': reference, 'maximum_bytes': io.PRIVATE_LARGE_BYTES}
    selected = io.StableReader(io.Budget(100, monotonic=lambda: 1), public_files={}, private_large_files=[allowance])
    bridge = object.__new__(io.ClosureProvenanceVerifier); bridge._load = selected; bridge.raws = {}
    consumer = s.Reader(bridge.load); consumer.large_private.add((reference['path'], reference['sha256']))
    observed = consumer.raw(reference)
    with pytest.raises(EvidenceError): p.document(observed)
    assert e.installed_closure(observed, **inv.options) == inv.value
    consumer.finish(); bridge.stable()
    assert len(consumer.seen) == 1 and len(bridge.raws) == 1


@pytest.mark.parametrize('fault', ['relative', 'outside', 'count', 'bool_size', 'negative', 'oversize_file', 'hash',
    'mapping_row', 'third_column', 'missing_python', 'duplicate', 'nonfinite', 'bad_utf8'])
def test_complete_historical_inventory_is_required(fault):
    inv = inventory(count=3); key = next(k for k in inv.value if k != inv.executable)
    if fault == 'relative': inv.value['relative.py'] = inv.value.pop(key)
    elif fault == 'outside': inv.value['/foreign/module.py'] = inv.value.pop(key)
    elif fault == 'count': inv.options['expected_files'] = 4
    elif fault == 'bool_size': inv.value[key][1] = True
    elif fault == 'negative': inv.value[key][1] = -1
    elif fault == 'oversize_file': inv.value[key][1] = 1024 ** 3 + 1
    elif fault == 'hash': inv.value[key][0] = 'invalid'
    elif fault == 'mapping_row': inv.value[key] = {'sha256': 'b' * 64, 'size': 1}
    elif fault == 'third_column': inv.value[key].append('summary')
    elif fault == 'missing_python': inv.value[inv.base + '/other'] = inv.value.pop(inv.executable)
    body = raw(inv.value)
    if fault == 'duplicate': body = body.rstrip()[:-1] + b',' + raw({inv.executable: ['a' * 64, 12345]}).strip()[1:]
    elif fault == 'nonfinite': body = body.replace(b'12345', b'NaN')
    elif fault == 'bad_utf8': body += b'\xff'
    with pytest.raises(EvidenceError): e.installed_closure(body, **inv.options)


def test_no_reviewed_final_producer_version_means_no_possible_default_acceptance():
    c = case('tls')
    value = {'schema_version': s.CAPSULE_SCHEMA, 'scope': e.SCOPE, 'created_at': '2026-10-12T01:00:00Z',
             'producer_version': 'unreviewed', 'identity': c.identity, 'root': '/synthetic/root', 'sources': c.options['expected_sources'],
             'corpus_manifest': c.corpus_manifest_ref, 'selection_manifest': {}, 'historical_initial': {},
             'final_quality': {}, 'loaded': {}, 'image_binding': {}}
    body = raw(value); reference = ref('final-capsule.json', body)
    assert not s.REVIEWED_VERSIONS
    with pytest.raises(EvidenceError, match='final_worker_producer_unreleased'):
        s.verify(reference=reference, identity=c.identity, sources=c.options['expected_sources'], images=c.preflight['images'],
            required_corpora=c.corpora, corpus_manifest=p.document(c.store.load(c.corpus_manifest_ref)), load=lambda item: body)


@pytest.mark.parametrize('fault', ['changed', 'mutated_argument', 'same_path_new_pin'])
def test_adapter_reader_preserves_exact_original_hash_and_cache(fault):
    original = raw({'original': True}); reference = ref('proof.json', original); values = [original]
    def load(item):
        if fault == 'mutated_argument': item['sha256'] = 'b' * 64
        return values[0]
    reader = s.Reader(load)
    if fault == 'mutated_argument':
        with pytest.raises(EvidenceError): reader.raw(reference)
        return
    reader.raw(reference); values[0] = raw({'changed': True})
    if fault == 'same_path_new_pin': reference = ref('proof.json', values[0])
    with pytest.raises(EvidenceError): reader.raw(reference)
