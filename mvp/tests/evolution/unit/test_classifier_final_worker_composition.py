"""Original historical/image bytes are reopened; no real model or native calls."""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace as N

import pytest
from evolution import classifier_final_worker_evidence as e
from evolution import classifier_final_worker_qualification as s
from evolution import classifier_provenance as p
from evolution.registry import EvidenceError
from guardrails.evolution.classifier_contract import canonical

from mvp.tests.evolution.unit.test_classifier_provenance import synthetic_corpus
from services.classifier import bounded_qualification_core as core
from services.classifier import bounded_qualification_evidence as initial
from services.classifier.tests.test_bounded_qualification_evidence import network


class Store:
    def __init__(self):
        self.saved = {}

    def put(self, path, value):
        raw = value if type(value) is bytes else canonical(value) + b'\n'
        assert path not in self.saved
        self.saved[path] = raw
        return {'path': path, 'sha256': hashlib.sha256(raw).hexdigest()}

    def load(self, ref):
        return self.saved[ref['path']]


def historical_fixture(index=3, *, identity_override=None):
    if index <= 3:
        active_core, active_initial, family = core, initial, 'bounded-initial'
    else:
        from services.classifier import (
            bounded_qualification_core_extended as active_core,
        )
        from services.classifier import (
            bounded_qualification_evidence_extended as active_initial,
        )
        family = 'bounded-extended-initial'
    store = Store()
    source = store.put('/archive/services/classifier/old.py', b'old source retained')
    consumer = store.put('/archive/mvp/evolution/initial_consumer.py', b'original reviewed consumer')
    sources = {'services/classifier/old.py': source['sha256']}
    launcher = store.put('/operator/initial_launcher.py', b'original reviewed launcher')
    support = store.put('/operator/initial_support.py', b'original reviewed support')
    names = ('original', 'heldout_1', 'heldout_2', *(f'confirmation_{i}' for i in range(1, index + 1)))
    documents = {name: synthetic_corpus(name) for name in names}
    corpora = {name: store.put('/corpus/' + name + '.json', documents[name]) for name in names}
    manifest = store.put('/operator/corpus-manifest.json', {'scope': 'already independently verified upstream'})
    identity = {'model_id': 'synthetic/candidate', 'model_revision': 'a' * 40, 'weights_sha256': 'b' * 64,
        'bundle_sha256': 'c' * 64}
    if identity_override is not None: identity = copy.deepcopy(identity_override)
    old_identity = {'repository': identity['model_id'], 'revision': identity['model_revision'],
        'weights_sha256': identity['weights_sha256'], 'bundle_sha256': identity['bundle_sha256'],
        'candidate_manifest_sha256': 'd' * 64, 'selection_index': index, 'qualification_only': True, 'production_enabled': False}
    spec_args = (index, 'd' * 64, manifest['sha256'], canonical(old_identity), canonical(sources),
        tuple(active_core.CorpusPin(name, corpora[name]['sha256']) for name in names))
    if index > 3:
        spec_args += (canonical({'original': active_core.ORIGINAL_APPROVAL_SHA256,
            'extension': active_core.EXTENSION_APPROVAL_SHA256}),)
    spec = active_core.Specification(*spec_args)
    loaded = {'repository': identity['model_id'], 'revision': identity['model_revision'],
        'weights_sha256': identity['weights_sha256'], 'qualification_only': True, 'quality_verified': False, 'scoring_requests': 0}
    inventory = {'/python/bin/python3.13': ['a' * 64, 100], '/site/pkg.py': ['b' * 64, 10]}
    closure = store.put('/load/closure.json', inventory)
    load_refs = {name: store.put('/load/' + name + '.json', {'original': name})
        for name in ('helper', 'started', 'child', 'cleanup')}
    load_refs['closure'] = closure
    load_refs['result'] = store.put('/load/result.json', {'installed_closure_sha256': closure['sha256'],
        'installed_files': 2, 'metadata': loaded})
    prior = {f'prior{i}': store.put(f'/prior/{i}.json', {'scope': 'failed_retained'}) for i in range(index + 2)}
    plan_value = {'schema_version': family + '-qualification-plan-v1', 'corpus_manifest': manifest,
        'selection_index': index, 'sources': sources, 'prior_evidence': prior, 'load_only': load_refs,
        'created_at': '2026-10-12T16:00:00Z', 'output': '/original-run'}
    plan = store.put('/binding/plan.json', plan_value)
    expected = active_initial.Expected(spec, plan['sha256'], load_refs['result']['sha256'], canonical(prior), canonical(loaded))
    start = active_initial.started(expected, network(), '2026-10-12T16:02:00Z')
    rows = []
    for name in names:
        for case in documents[name]['cases']:
            label = case['expected_injection']
            rows.append({key: case[key] for key in ('case_id', 'category', 'language', 'family_id')} | {
                'corpus': name, 'expected': label, 'predicted': label, 'score': 1. if label else 0.,
                'error': None, 'elapsed_ms': 1., 'windows': 1})
    row_bytes = b''.join(canonical(row) + b'\n' for row in rows)
    result = {**start, **active_core.summarize(rows, spec), 'status': 'completed',
        'completed_at': '2026-10-12T16:03:00Z', 'cases_sha256': hashlib.sha256(row_bytes).hexdigest(),
        'startup_seconds': 1., 'loaded': loaded,
        'cpu': {'child_user_seconds': 2., 'child_system_seconds': .1, 'child_max_rss_kib': 1024,
            'torch_threads': 2, 'torch_interop_threads': 1, 'affinity': [0, 1]},
        'resources_pass': True, 'network_after': network(), 'cost': active_initial.cost(rows, len(rows))}
    quality = {name: store.put('/original-run/quality/' + name, body) for name, body in
        (('started.json', start), ('result.json', result), ('cases.jsonl', row_bytes))}
    cleanup = store.put('/original-run/invocation/cleanup.json', {'schema_version': 'wolf-full-initial-cleanup-v1',
        'group_signal_requested': True, 'group_already_absent': False, 'leader_reaped': True,
        'leader_exit_observed': True, 'errors': []})
    invoked = {'schema_version': family + '-invocation-v1', 'status': 'running',
        'started_at': '2026-10-12T16:01:00Z', 'plan_sha256': plan['sha256'],
        'launcher_sha256': launcher['sha256'], 'support_sha256': support['sha256'], 'output': '/original-run',
        'maximum_seconds': 3600, 'automatic_retry': False, 'warmup_requests': 0, 'sources': sources}
    complete = {**invoked, 'status': 'completed', 'completed_at': '2026-10-12T16:04:00Z',
        'exit_code': 0, 'elapsed_seconds': 180., 'cleanup_sha256': cleanup['sha256'],
        'quality_report_pins': {name: ref['sha256'] for name, ref in quality.items()},
        'enabled_model_changed': False, 'final_selected_runtime_rerun_required': True}
    invocation = {'started.json': store.put('/original-run/invocation/started.json', invoked),
        'result.json': store.put('/original-run/invocation/result.json', complete), 'cleanup.json': cleanup}
    capsule = store.put('/parent/initial-completion.json', {'schema_version': family + '-parent-completion-v1',
        'scope': active_initial.SCOPE, 'plan': plan, 'launcher': launcher, 'support': support, 'invocation': invocation, 'quality': quality})
    snapshot = store.put('/parent/historical-snapshot.json', {'status': 'initial_source_snapshot_before_scoring',
        'source_root': '/current', 'snapshot_root': '/archive', 'created_at': '2026-10-12T16:00:30Z',
        'files': {**sources, 'mvp/evolution/initial_consumer.py': consumer['sha256']}})
    authority = N(selection_index=index, initial_consumer=consumer['sha256'], initial_launcher=launcher['sha256'],
        initial_support=support['sha256'], installed_base='/python', installed_site='/site', installed_python='/python/bin/python3.13')
    value = {'capsule': capsule, 'snapshot': snapshot, 'consumer': consumer}
    return N(**locals())


def run_historical(f):
    reader = s.Reader(f.store.load)
    value = s.historical(reader, f.value, authority=f.authority, identity=f.identity,
        corpora=f.corpora, corpus_manifest=f.manifest)
    reader.finish()
    return value, reader


@pytest.mark.parametrize('index', [1, 2, 3, 4, 5, 6])
def test_historical_archive_authenticates_original_complete_rows_without_current_source_substitution(index):
    f = historical_fixture(index)
    f.store.put('/current/services/classifier/old.py', b'promoted source is deliberately different')
    assert run_historical(f)[0] == '2026-10-12T16:04:00Z'
    assert all(path != '/current/services/classifier/old.py' for path, _ in run_historical(f)[1].seen)


@pytest.mark.parametrize('path', ['/archive/services/classifier/old.py', '/original-run/quality/cases.jsonl',
    '/original-run/invocation/result.json', '/load/closure.json', '/prior/4.json'])
def test_historical_original_byte_drift_refuses_even_with_complete_success_summary(path):
    f = historical_fixture()
    f.store.saved[path] += b' '
    with pytest.raises(EvidenceError):
        run_historical(f)


def test_historical_failed_invocation_cannot_be_relabelled_as_selected_worker():
    f = historical_fixture()
    f.complete['exit_code'] = 1
    reference = f.store.put('/failed/result.json', f.complete)
    replacement = copy.deepcopy(p.document(f.store.load(f.capsule)))
    replacement['invocation']['result.json'] = reference
    f.value['capsule'] = f.store.put('/failed/capsule.json', replacement)
    with pytest.raises(EvidenceError):
        run_historical(f)


def test_model_acquisition_requires_every_selected_file_and_exact_bundle_digest():
    f = Store()
    files = {'model.safetensors': {'sha256': 'a' * 64, 'size_bytes': 1234, 'source_git_oid': 'b' * 40},
        'tokenizer.json': {'sha256': 'c' * 64, 'size_bytes': 999, 'source_git_oid': 'd' * 40}}
    selected = f.put('/selected.json', {'artifacts': {name: {'sha256': row['sha256'], 'size_bytes': row['size_bytes'],
        'git_oid': row['source_git_oid']} for name, row in files.items()}})
    bundle = {name: [row['sha256'], row['size_bytes']] for name, row in files.items()}
    bundle_raw = canonical(bundle)
    identity = {'model_id': 'synthetic/candidate', 'model_revision': 'e' * 40,
        'weights_sha256': 'a' * 64, 'bundle_sha256': hashlib.sha256(bundle_raw).hexdigest()}
    receipt = {'schema': 'bounded-candidate-acquisition-v1', 'status': 'artifacts_verified_not_qualified',
        'repository': identity['model_id'], 'revision': identity['model_revision'], 'manifest_sha256': selected['sha256'],
        'model_loaded': False, 'model_scored': False, 'threshold_changed': False, 'automatic_retry': False, 'files': files}
    reference = f.put('/acquired.json', receipt)
    reader = s.Reader(f.load)
    assert s.model_bundle(reader, reference, authority=N(model_artifacts=reference['sha256']), identity=identity, selected=selected) == bundle
    reader.finish()
    wrong = {**identity, 'bundle_sha256': hashlib.sha256(bundle_raw + b'\n').hexdigest()}
    with pytest.raises(EvidenceError):
        s.model_bundle(s.Reader(f.load), reference, authority=N(model_artifacts=reference['sha256']), identity=wrong, selected=selected)
    receipt['files'].pop('tokenizer.json')
    missing = f.put('/missing.json', receipt)
    with pytest.raises(EvidenceError):
        s.model_bundle(s.Reader(f.load), missing, authority=N(model_artifacts=missing['sha256']), identity=identity, selected=selected)


def image_fixture(*, fault=None, identity_override=None, sources_override=None, bundle_override=None):
    store = Store()
    identity = {'model_id': 'synthetic/candidate', 'model_revision': 'a' * 40}
    sources = {name: hashlib.sha256(name.encode()).hexdigest() for name in sorted(s.REQUIRED_RUNTIME_SOURCES)}
    bundle = {'model.safetensors': ['b' * 64, 1234], 'tokenizer.json': ['c' * 64, 234]}
    if identity_override is not None: identity = copy.deepcopy(identity_override)
    if sources_override is not None: sources = dict(sources_override)
    if bundle_override is not None: bundle = copy.deepcopy(bundle_override)
    inputs = [{'path': 'app/' + name, 'sha256': pin, 'size_bytes': 100} for name, pin in sources.items()]
    inputs += [{'path': 'model/' + name, 'sha256': pin, 'size_bytes': size} for name, (pin, size) in bundle.items()]
    if fault == 'source_inside_context': inputs[0]['sha256'] = 'd' * 64
    if fault == 'model_inside_context': inputs[-1]['sha256'] = 'd' * 64
    source_digest = hashlib.sha256((json.dumps(inputs, sort_keys=True, separators=(',', ':')) + '\n').encode()).hexdigest()
    context = {'schema': 'classifier-image-context-v1', 'scope': 'context_only_not_built', 'plan_sha256': 'e' * 64,
        'base_image': 'base@sha256:' + 'f' * 64, 'inputs': inputs, 'source_sha256': sources, 'source_digest': source_digest,
        'context_sha256': '1' * 64, 'context_size_bytes': 4567, 'docker_operations': 0, 'model_loads': 0}
    context_ref = store.put('/build/context-manifest.json', context)
    seal = {key: context[key] for key in ('scope', 'context_sha256', 'context_size_bytes', 'plan_sha256')}
    seal.update(schema='classifier-image-context-seal-v1', complete=True, manifest_sha256=context_ref['sha256'])
    seal_ref = store.put('/build/seal.json', seal)
    config_ref = store.put('/image/config.json', {'rootfs': {'diff_ids': ['sha256:' + '2' * 64]}})
    manifest_ref = store.put('/image/manifest.json', {'config': {'digest': 'sha256:' + config_ref['sha256']}})
    artifact = {'name': 'evolution-classifier', 'kind': 'image', 'sha256': manifest_ref['sha256'],
        'source_url': 'docker://classifier@sha256:' + manifest_ref['sha256']}
    inventory = dict(sources)
    if fault == 'source_inventory': inventory.pop(next(iter(inventory)))
    build = {'schema': 'classifier-image-build-v1', 'artifact': artifact, 'source_digest': source_digest,
        'inputs': inputs, 'source_inventory': inventory, 'context_directory': '/build/context',
        'seal_sha256': seal_ref['sha256'], 'manifest_sha256': context_ref['sha256'], 'context_sha256': context['context_sha256'],
        'context_size_bytes': 4567, 'base_image': context['base_image'], 'image_id': 'sha256:' + config_ref['sha256'],
        'image_manifest_digest': 'sha256:' + manifest_ref['sha256']}
    build_ref = store.put('/prepared/builds/classifier.json', build)
    prep_ref = store.put('/prepared/preparation-lock.json', {'images': [artifact]})
    artifacts_ref = store.put('/prepared/artifacts-lock.json', {'artifacts': [artifact]})
    prepared = {'schema_version': 1, 'preparation_lock_digest': prep_ref['sha256'],
        'files': [{'path': 'builds/classifier.json', 'sha256': build_ref['sha256']}], 'artifacts': [artifact]}
    if fault == 'prepared_build': prepared['files'][0]['sha256'] = '0' * 64
    prepared_ref = store.put('/prepared/prepared.json', prepared)
    classifier = {'container_id': '3' * 64, 'expected_reference': artifact['source_url'].removeprefix('docker://'),
        'manifest_digest': build['image_manifest_digest'], 'config_digest': build['image_id'],
        'rootfs_chain_id': 'sha256:' + '2' * 64, 'ready': True}
    runtime = {'container_id': '4' * 64, 'expected_reference': 'runtime@sha256:' + '5' * 64,
        'manifest_digest': 'sha256:' + '5' * 64, 'config_digest': 'sha256:' + '6' * 64,
        'rootfs_chain_id': 'sha256:' + '7' * 64, 'ready': True}
    if fault == 'classifier_config': classifier['config_digest'] = 'sha256:' + '8' * 64
    if fault == 'classifier_layers': classifier['rootfs_chain_id'] = 'sha256:' + '8' * 64
    if fault == 'runtime_not_ready': runtime['ready'] = False
    node = {'container_id': '9' * 64, 'image': 'owned-node@sha256:' + 'a' * 64,
        'rules_sha256': 'b' * 64, 'network_namespace': 'net:[123]', 'cpu_limit': 6,
        'cpu_quota_us': 600000, 'cpu_period_us': 100000, 'memory_limit_bytes': 8 * 1024 ** 3}
    observations = {name: {'runtime': name, 'native_images': {
        'runtime': {**runtime, 'container_id': ('4' if name == 'runtime-1' else '8') * 64},
        'classifier': copy.deepcopy(classifier)}, 'node_boundary_snapshot': copy.deepcopy(node)}
        for name in ('runtime-1', 'runtime-2')}
    if fault == 'other_runtime_not_ready': observations['runtime-1']['native_images']['runtime']['ready'] = False
    elif fault == 'other_node': observations['runtime-1']['node_boundary_snapshot']['container_id'] = '0' * 64
    elif fault == 'same_runtime_container': observations['runtime-1']['native_images']['runtime']['container_id'] = '8' * 64
    elif fault == 'node_resources':
        for row in observations.values(): row['node_boundary_snapshot']['cpu_quota_us'] = 700000
    images = copy.deepcopy(observations['runtime-2'])
    if fault == 'runtime_swap': images['native_images']['runtime']['container_id'] = 'a' * 64
    native_ref = store.put('/native/observations.json', {'schema_version': 'selected-worker-native-image-observations-v1',
        'prepared_sha256': prepared_ref['sha256'], 'identity': identity, 'observations': observations})
    producer = store.put('/operator/image-producer.py', b'synthetic independently pinned image producer')
    refs = {'build': build_ref, 'context_manifest': context_ref, 'context_seal': seal_ref, 'prepared': prepared_ref,
        'preparation_lock': prep_ref, 'artifacts_lock': artifacts_ref, 'oci_manifest': manifest_ref,
        'oci_config': config_ref, 'native': native_ref}
    start = {'schema_version': 'selected-worker-image-observation-invocation-v1', 'status': 'running',
        'producer': producer, 'created_at': '2026-10-12T17:00:00Z', 'identity': identity, 'sources': sources}
    complete = {**start, 'status': 'completed', 'completed_at': '2026-10-12T17:01:00Z', 'exit_code': 0, 'artifacts': refs}
    if fault == 'producer_exit': complete['exit_code'] = 1
    value = {'schema_version': 'selected-worker-image-binding-v1', 'producer': producer, 'identity': identity,
        'sources': sources, **refs, 'started': store.put('/image-invocation/started.json', start),
        'result': store.put('/image-invocation/result.json', complete)}
    reference = store.put('/parent/image-binding.json', value)
    authority = N(image_producer=producer['sha256'], runtime_paths=tuple(sources))
    return N(**locals())


def image_check(f):
    reader = s.Reader(f.store.load)
    result = s.image_closure(reader, f.reference, authority=f.authority, identity=f.identity,
        sources=f.sources, images=f.images, bundle=f.bundle)
    reader.finish()
    return result


def test_complete_original_build_context_locks_oci_and_both_native_observations_join():
    assert image_check(image_fixture()) == '2026-10-12T17:01:00Z'


@pytest.mark.parametrize('fault', ['source_inside_context', 'model_inside_context', 'source_inventory', 'prepared_build',
    'classifier_config', 'classifier_layers', 'runtime_not_ready', 'runtime_swap', 'producer_exit',
    'other_runtime_not_ready', 'other_node', 'same_runtime_container', 'node_resources'])
def test_self_consistent_rehashed_image_summary_cannot_hide_missing_native_or_source_binding(fault):
    with pytest.raises(EvidenceError):
        image_check(image_fixture(fault=fault))


@pytest.mark.parametrize('fault', ['authority_slot', 'family', 'approval_chain', 'missing_confirmation'])
def test_extended_historical_original_cannot_be_reinterpreted_or_shortened(fault):
    f = historical_fixture(4)
    if fault == 'authority_slot':
        f.authority.selection_index = 3
    elif fault == 'missing_confirmation':
        del f.corpora['confirmation_4']
    elif fault == 'family':
        value = p.document(f.store.load(f.capsule))
        value['schema_version'] = 'bounded-initial-parent-completion-v1'
        f.value['capsule'] = f.store.put('/relabelled/capsule.json', value)
    else:
        # Even an independently rehashed capsule cannot remove extension consent
        # from otherwise internally consistent report/parent hash projections.
        value = p.document(f.store.load(f.capsule))
        result = p.document(f.store.load(f.quality['result.json']))
        result['approval_chain']['extension'] = '0' * 64
        changed = f.store.put('/changed/result.json', result)
        value['quality']['result.json'] = changed
        ended = copy.deepcopy(f.complete)
        ended['quality_report_pins']['result.json'] = changed['sha256']
        value['invocation']['result.json'] = f.store.put('/changed/invocation.json', ended)
        f.value['capsule'] = f.store.put('/changed/capsule.json', value)
    with pytest.raises((EvidenceError, ValueError)):
        run_historical(f)


def final_fixture(monkeypatch, *, fault=None):
    """Join all original report families through the actual fixed seven-keyword port.

    Only the reviewed-version registry and separate, already unit-tested recursive
    corpus-authority port are explicit synthetic authorities here. No quality,
    lifecycle, image, historical-row or stable-reader validator is replaced.
    """
    from mvp.tests.evolution.unit.test_classifier_loaded_evidence import (
        documents as loaded_documents,
    )
    from mvp.tests.evolution.unit.test_classifier_loaded_evidence import (
        package as loaded_package,
    )

    bundle = {'model.safetensors': ['b' * 64, 1234], 'tokenizer.json': ['c' * 64, 234]}
    identity = {'model_id': 'synthetic/candidate', 'model_revision': 'a' * 40,
        'weights_sha256': 'b' * 64, 'bundle_sha256': hashlib.sha256(canonical(bundle)).hexdigest(),
        'evaluator_revision': 'synthetic-final-worker', 'class_mapping': {'0': 'SAFE', '1': 'INJECTION'},
        'supported_languages': ['en']}
    history = historical_fixture(identity_override=identity)
    store = history.store
    source_names = set(s.REQUIRED_RUNTIME_SOURCES) | set(s.loaded.SOURCE_FILES)
    sources = {name: store.put('/current/' + name, name.encode())['sha256'] for name in sorted(source_names)}
    image = image_fixture(identity_override=identity, sources_override=sources, bundle_override=bundle)
    assert not set(store.saved) & set(image.store.saved)
    store.saved.update(image.store.saved)
    selected_value = {'artifacts': {name: {'sha256': pin, 'size_bytes': size, 'git_oid': 'd' * 40}
        for name, (pin, size) in bundle.items()}}
    selected = store.put('/selected/manifest.json', selected_value)
    acquisition = store.put('/selected/acquisition.json', {'schema': 'bounded-candidate-acquisition-v1',
        'status': 'artifacts_verified_not_qualified', 'repository': identity['model_id'], 'revision': identity['model_revision'],
        'manifest_sha256': selected['sha256'], 'model_loaded': False, 'model_scored': False,
        'threshold_changed': False, 'automatic_retry': False, 'files': {name: {
            'sha256': pin, 'size_bytes': size, 'source_git_oid': 'd' * 40} for name, (pin, size) in bundle.items()}})
    manifest = {'selection_index': 3, 'selection_manifest': selected, 'corpora': history.corpora}
    calls = []
    def corpus_authority(reference, *, authorization, load):
        assert reference == history.manifest
        assert authorization['identity'] == identity and authorization['required_corpora'] == history.corpora
        assert load(reference) == store.saved[reference['path']]
        calls.append('independently tested corpus authority')
        return copy.deepcopy(manifest)
    monkeypatch.setattr(p, 'verify_corpus_manifest', corpus_authority)
    environment = store.put('/environment/verified.json', {'installed': 'synthetic-pinned-closure'})
    producers = {role: store.put('/operator/' + role + '-producer.py', (role + ' reviewed producer').encode())
        for role in ('quality', 'loaded')}
    launchers = {role: store.put('/operator/' + role + '-launcher.py', (role + ' reviewed launcher').encode())
        for role in ('quality', 'loaded')}
    def execution(role, plan, reports):
        plan_ref = store.put('/' + role + '/plan.json', plan)
        if role == 'quality':
            reports['started.json']['plan_sha256'] = plan_ref['sha256']
            reports['result.json']['plan_sha256'] = plan_ref['sha256']
        report_refs = {name: store.put(plan['output_directory'] + '/' + name, body) for name, body in reports.items()}
        start = {'schema_version': e.INVOCATION_SCHEMA, 'role': role, 'status': 'running',
            'created_at': plan['created_at'], 'plan': plan_ref, 'launcher': launchers[role], 'producer': producers[role],
            'argv': plan['argv'], 'python': '3.13.16', 'environment_sha256': environment['sha256'],
            'maximum_seconds': 3600 if role == 'quality' else 900, 'automatic_retry': False, 'warmup_requests': 0}
        cleanup = store.put('/' + role + '/invocation/cleanup.json', {'schema_version': 'selected-worker-cleanup-v1',
            'group_signal_requested': True, 'group_already_absent': False, 'leader_reaped': True,
            'leader_exit_observed': True, 'errors': []})
        end = {**start, 'status': 'completed', 'completed_at': '2026-10-12T16:40:00Z',
            'exit_code': 1 if fault == role + '_exit' else 0, 'elapsed_seconds': 1800. if role == 'quality' else 600.,
            'cleanup_sha256': cleanup['sha256'], 'report_files': report_refs}
        if fault == 'loaded_timestamp_over_budget' and role == 'loaded': end['completed_at'] = '2026-10-12T17:10:00Z'
        return {'plan': plan_ref, 'launcher': launchers[role], 'producer': producers[role],
            'started': store.put('/' + role + '/invocation/started.json', start),
            'result': store.put('/' + role + '/invocation/result.json', end), 'cleanup': cleanup, 'reports': report_refs}
    base_plan = {'scope': e.SCOPE, 'created_at': '2026-10-12T16:10:00Z', 'identity': identity,
        'entrypoints': copy.deepcopy(e.ENTRYPOINTS), 'environment': environment, 'host_namespace': 'net:[1]'}
    quality_plan = {**base_plan, 'schema_version': e.PLAN_SCHEMA, 'sources': sources, 'corpora': history.corpora,
        'corpus_manifest': history.manifest, 'selection_manifest': selected, 'limits': copy.deepcopy(e.LIMITS),
        'producer': producers['quality'], 'launcher': launchers['quality'], 'model_artifacts': acquisition,
        'output_directory': '/quality/reports', 'argv': ['/python/bin/python3.13', '-I', '-S', '-B', '/operator/quality-producer.py']}
    started = {'schema_version': e.QUALITY_SCHEMA, 'scope': e.SCOPE, 'status': 'running',
        'created_at': '2026-10-12T16:11:00Z', 'plan_sha256': '0' * 64, 'network': network(),
        'automatic_retry': False, 'warmup_requests': 0,
        **{name: copy.deepcopy(quality_plan[name]) for name in ('identity', 'sources', 'corpora', 'entrypoints', 'limits')}}
    matrices, windows = e.rows(history.row_bytes, history.documents, history.corpora)
    rows = history.row_bytes
    if fault == 'quality_missing_rows': rows = b'\n'.join(rows.splitlines()[:-1]) + b'\n'
    result = {**copy.deepcopy(started), 'status': 'passed', 'completed_at': '2026-10-12T16:12:00Z',
        'cases_sha256': hashlib.sha256(rows).hexdigest(), 'cases': len(history.rows), 'matrices': matrices,
        'model_loads': 1, 'attempted_dispatches': len(history.rows), 'completed_windows': windows, 'operational_errors': 0,
        'cpu': {'affinity': [0, 1], 'maximum_rss_bytes': 1024 ** 3, 'torch_threads': 2, 'torch_interop_threads': 1},
        'network_after': network(), 'spanish_enforcement_enabled': False}
    if fault == 'initial_only': started['scope'] = result['scope'] = initial.SCOPE
    quality_group = execution('quality', quality_plan, {'started.json': started, 'result.json': result, 'cases.jsonl': rows})
    loaded_start, loaded_rows, loaded_result = loaded_documents()
    for value in (loaded_start, loaded_result):
        value['identity'] = identity; value['sources'] = {name: sources[name] for name in s.loaded.SOURCE_FILES}
    if fault == 'missing_loaded': loaded_rows.pop()
    loaded_raw, _ = loaded_package(loaded_start, loaded_rows, loaded_result)
    loaded_plan = {**base_plan, 'schema_version': 'selected-worker-loaded-plan-v1',
        'created_at': '2026-10-12T16:30:00Z',
        'sources': loaded_start['sources'], 'scoring_sources': sources, 'producer': producers['loaded'],
        'launcher': launchers['loaded'], 'output_directory': '/loaded/reports',
        'argv': ['/python/bin/python3.13', '-I', '-S', '-B', '/operator/loaded-producer.py']}
    loaded_group = execution('loaded', loaded_plan, loaded_raw)
    authority = s.ProducerVersion(selection_index=3, quality_launcher=launchers['quality']['sha256'],
        quality_producer=producers['quality']['sha256'], loaded_launcher=launchers['loaded']['sha256'],
        loaded_producer=producers['loaded']['sha256'], image_producer=image.producer['sha256'],
        initial_launcher=history.launcher['sha256'], initial_consumer=history.consumer['sha256'],
        initial_support=history.support['sha256'], environment=environment['sha256'], model_artifacts=acquisition['sha256'],
        runtime_paths=tuple(sources), installed_base='/python', installed_site='/site', installed_python='/python/bin/python3.13')
    monkeypatch.setattr(s, 'REVIEWED_VERSIONS', {'synthetic-only-test-version': authority})
    capsule = {'schema_version': s.CAPSULE_SCHEMA, 'scope': e.SCOPE, 'created_at': '2026-10-12T17:02:00Z',
        'producer_version': 'synthetic-only-test-version', 'identity': identity, 'root': '/current', 'sources': sources,
        'corpus_manifest': history.manifest, 'selection_manifest': selected, 'historical_initial': history.value,
        'final_quality': quality_group, 'loaded': loaded_group, 'image_binding': image.reference}
    if fault == 'capsule_before_images': capsule['created_at'] = '2026-10-12T16:50:00Z'
    capsule_ref = store.put('/parent/final-capsule.json', capsule)
    options = {'reference': capsule_ref, 'identity': identity, 'sources': sources, 'images': image.images,
        'required_corpora': history.corpora, 'corpus_manifest': manifest, 'load': store.load}
    return N(**locals())


def test_fixed_final_port_joins_original_history_current_worker_lifecycle_and_image_proofs(monkeypatch):
    f = final_fixture(monkeypatch)
    result = s.verify(**f.options)
    assert result['scope'] == e.SCOPE and result['qualification_sha256'] == f.capsule_ref['sha256']
    assert result['loaded_cases'] == 9 and result['loaded_requests'] == 46
    assert result['corpora'] == f.history.corpora and result['parent_exit_code'] == 0
    assert f.calls == ['independently tested corpus authority']


@pytest.mark.parametrize('fault', ['initial_only', 'quality_exit', 'loaded_exit', 'quality_missing_rows',
    'missing_loaded', 'capsule_before_images', 'loaded_timestamp_over_budget'])
def test_full_port_refuses_individually_hashed_incomplete_or_unordered_original_groups(monkeypatch, fault):
    f = final_fixture(monkeypatch, fault=fault)
    with pytest.raises((EvidenceError, s.loaded.LoadedEvidenceError)):
        s.verify(**f.options)
