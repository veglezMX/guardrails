"""Exact historical closure allowance; real private IO with synthetic proofs.

No model, native executor, network, PostgreSQL or deployed operation is used.
Parent and worker composition reuse the existing genuine pure synthetic receipt
fixtures; the additional bytes never grant qualification authority.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path

import pytest
from evolution import classifier_parent_context as parent
from evolution import classifier_provenance as proof
from evolution import classifier_provenance_io as io
from evolution import classifier_worker_invocation as worker
from evolution.registry import EvidenceError

from mvp.tests.evolution.e2e import classifier_fixture_builder as builder
from mvp.tests.evolution.unit import test_classifier_fixture_builder as builder_tests
from mvp.tests.evolution.unit import test_classifier_parent_context as parent_tests
from mvp.tests.evolution.unit import test_classifier_provenance_io as previous
from mvp.tests.evolution.unit import test_classifier_worker_invocation as worker_tests

isolated = previous.isolated
filesystem = previous.filesystem
flow = previous.flow
built = builder_tests.built
parent_flow = parent_tests.flow


def allowance(ref):
    return {'schema_version': io.PRIVATE_LARGE_SCHEMA, 'kind': 'historical_installed_closure',
            'reference': copy.deepcopy(ref), 'maximum_bytes': 16 * 1024 * 1024}


def closure(root, *, size=4 * 1024 * 1024 + 1):
    return previous.put(root / 'installed-closure.json', b'x' * size)


def reader(f, ref):
    return io.StableReader(f.budget, public_files={}, private_large_files=[allowance(ref)])


def test_exact_private_closure_above_default_cap_and_inclusive_maximum(filesystem):
    f = filesystem
    for size in (proof.MAX_BYTES + 1, io.PRIVATE_LARGE_BYTES):
        ref = closure(f.root, size=size)
        with pytest.raises(EvidenceError):
            f.reader(ref)
        value = reader(f, ref)(ref)
        assert len(value) == size and hashlib.sha256(value).hexdigest() == ref['sha256']
    assert proof.MAX_BYTES == 4 * 1024 * 1024


@pytest.mark.parametrize('change', ['hash', 'path', 'unrelated'])
def test_allowance_never_applies_to_another_reference(filesystem, change):
    f = filesystem; ref = closure(f.root)
    allowed = copy.deepcopy(ref)
    if change == 'hash':
        allowed['sha256'] = 'b' * 64
    else:
        allowed['path'] = str(f.root / ('renamed.json' if change == 'path' else 'unrelated.json'))
    with pytest.raises(EvidenceError):
        reader(f, allowed)(ref)


@pytest.mark.parametrize('mode', [0o400, 0o444, 0o640, 0o644, 0o660, 0o666])
def test_large_allowance_preserves_private_0600_only(filesystem, mode):
    f = filesystem; ref = closure(f.root); Path(ref['path']).chmod(mode)
    with pytest.raises(EvidenceError):
        reader(f, ref)(ref)


@pytest.mark.parametrize('fault', ['schema', 'kind', 'cap', 'bool', 'extra', 'reference_extra', 'duplicate', 'mapping', 'public'])
def test_closed_allowance_schema_and_single_private_record(filesystem, fault):
    f = filesystem; ref = previous.put(f.root / 'closure.json'); row = allowance(ref)
    values, public = [row], {}
    if fault == 'schema': row['schema_version'] = 'other'
    elif fault == 'kind': row['kind'] = 'arbitrary_file'
    elif fault == 'cap': row['maximum_bytes'] += 1
    elif fault == 'bool': row['maximum_bytes'] = True
    elif fault == 'extra': row['future_completion'] = 'b' * 64
    elif fault == 'reference_extra': row['reference']['maximum'] = 16 * 1024 * 1024
    elif fault == 'duplicate': values.append(copy.deepcopy(row))
    elif fault == 'mapping': values = {'path': ref}
    elif fault == 'public': public[ref['path']] = 'b' * 64
    with pytest.raises(EvidenceError):
        io.StableReader(f.budget, public_files=public, private_large_files=values)


@pytest.mark.parametrize('fault', ['oversize', 'symlink', 'hardlink', 'fifo', 'parent_mode', 'expired'])
def test_large_reference_retains_original_identity_and_deadline_guards(filesystem, fault):
    f = filesystem; ref = closure(f.root); path = Path(ref['path']); selected = reader(f, ref)
    if fault == 'oversize':
        with path.open('r+b') as stream: stream.truncate(io.PRIVATE_LARGE_BYTES + 1)
    elif fault == 'symlink': path.rename(f.root / 'other'); path.symlink_to(f.root / 'other')
    elif fault == 'hardlink': os.link(path, f.root / 'alias')
    elif fault == 'fifo': path.unlink(); os.mkfifo(path, 0o600)
    elif fault == 'parent_mode': f.root.chmod(0o755)
    elif fault == 'expired': f.clock[0] = f.budget.deadline
    with pytest.raises((EvidenceError, OSError)):
        selected(ref)


def test_allowance_copies_external_record_and_does_not_refresh_budget(filesystem):
    f = filesystem; ref = closure(f.root); rows = [allowance(ref)]
    selected = io.StableReader(f.budget, public_files={}, private_large_files=rows)
    rows[0]['reference']['sha256'] = 'b' * 64; rows.clear()
    assert len(selected(ref)) > proof.MAX_BYTES and selected.budget is f.budget
    with f.budget.cap(10):
        f.clock[0] = 10
        with pytest.raises(EvidenceError): selected(ref)
        f.clock[0] = 0
    assert f.budget.deadline == 20000


def test_deadline_expiry_during_large_read_refuses(filesystem, monkeypatch):
    f = filesystem; ref = closure(f.root); selected = reader(f, ref); original = os.read
    calls = []
    def limited(fd, size):
        calls.append(size)
        raw = original(fd, size)
        if len(calls) == 2: f.clock[0] = f.budget.deadline
        return raw
    monkeypatch.setattr(io.os, 'read', limited)
    with pytest.raises(EvidenceError): selected(ref)
    assert len(calls) == 2 and all(size <= 65536 for size in calls)


def test_initial_manifest_cannot_authorize_its_own_large_read(flow):
    f = flow; value, expected = worker_tests.manifest(f)
    ref = previous.put(f.f.root / 'manifest.json', proof.canonical(value) + b'\n')
    with pytest.raises(EvidenceError):
        worker.load(ref, reader=reader(f.f, ref), expected=expected)


def test_closed_worker_manifest_preserves_exact_allowance_without_mutation(flow):
    value, expected = worker_tests.manifest(flow)
    assert 'private_large_files' not in worker.validate(value, **expected)
    value['private_large_files'] = [allowance({'path': '/synthetic/closure.json', 'sha256': 'b' * 64})]
    validated = worker.validate(value, **expected)
    assert validated == value and validated is not value
    value['private_large_files'][0]['reference']['sha256'] = 'c' * 64
    assert validated['private_large_files'][0]['reference']['sha256'] == 'b' * 64
    value['public_files']['/synthetic/closure.json'] = 'c' * 64
    with pytest.raises(EvidenceError): worker.validate(value, **expected)


def test_real_worker_builder_reopens_large_closure_before_executor(built):
    b = built; ref = closure(b.f.f.root)
    b.manifest['private_large_files'] = [allowance(ref)]
    b.ref.update(previous.put(Path(b.ref['path']), proof.canonical(b.manifest) + b'\n'))
    reads = []
    def qualify(**kwargs):
        raw = kwargs['load'](ref)
        reads.append(len(raw)); assert 'executor' not in b.events
        return b.f.c.options['qualify_final_worker'](**kwargs)
    with builder.build_worker_plugin(b.ref, b.expected, b.f.f.root, qualify_final_worker=qualify) as plugin:
        assert reads and all(size > proof.MAX_BYTES for size in reads)
        assert plugin.context.recorder.verifier._load.private_large_files == (allowance(ref),)
    assert 'executor' in b.events


def test_parent_to_worker_and_consume_share_authenticated_allowance(parent_flow):
    f = parent_flow; f.context.close(); ref = closure(f.root)
    capsule = copy.deepcopy(f.capsule); capsule['private_large_files'] = [allowance(ref)]
    pinned = previous.put(f.root / 'large-capsule.json', proof.canonical(capsule) + b'\n')
    reads = []
    def qualify(**kwargs):
        reads.append(len(kwargs['load'](ref)))
        return f.qualify(**kwargs)
    with parent.ParentClassifierContext(pinned, f.root, f.outer, qualify_final_worker=qualify) as context:
        invocation = context.prepare(**f.args)
        assert invocation.manifest['private_large_files'] == capsule['private_large_files']
        assert context.reader.budget is context.budget and context.reader.budget.deadline == f.outer
        f.clock[0] += parent_tests.timedelta(seconds=1)
        invocation.started(pid=123, process_start=456, started_at=f.clock[0].isoformat())
        observation, result = parent_tests.worker_outputs(f, invocation)
        invocation.complete(exit_code=0, result=result, elapsed_ms=150)
        before = len(reads)
        context.consume_verifiers[f.case.case_id](**parent_tests.consume_args(f, observation))
        assert len(reads) > before and all(size > proof.MAX_BYTES for size in reads)
        assert context.reader.budget.deadline == f.outer


def test_parent_capsule_rejects_overlap_before_any_executor(parent_flow):
    f = parent_flow; f.context.close(); ref = closure(f.root)
    capsule = copy.deepcopy(f.capsule); capsule['private_large_files'] = [allowance(ref)]
    capsule['public_files'][ref['path']] = ref['sha256']
    pinned = previous.put(f.root / 'bad-capsule.json', proof.canonical(capsule) + b'\n')
    with pytest.raises(EvidenceError):
        parent.ParentClassifierContext(pinned, f.root, f.outer, qualify_final_worker=f.qualify)
    assert f.events == []


def verifier(selected):
    value = object.__new__(io.ClosureProvenanceVerifier)
    value._load, value.raws = selected, {}
    return value


def test_large_cache_reopens_original_and_rejects_path_hash_rebinding(filesystem):
    f = filesystem; ref = closure(f.root); value = verifier(reader(f, ref))
    assert len(value.load(ref)) > proof.MAX_BYTES
    value.stable()
    different = previous.put(Path(ref['path']), b'changed-small-value')
    with pytest.raises(EvidenceError): value.load(different)
    with pytest.raises(EvidenceError): value.stable()
    assert set(value.raws) == {(ref['path'], ref['sha256'])}


def test_prior_small_cache_cannot_be_rebound_as_authorized_large_reference(filesystem):
    f = filesystem; path = f.root / 'installed-closure.json'; small = previous.put(path)
    raw = b'x' * (proof.MAX_BYTES + 1)
    large = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
    value = verifier(reader(f, large)); value.load(small)
    previous.put(path, raw)
    with pytest.raises(EvidenceError): value.load(large)


def test_forged_in_file_allowance_does_not_authorize_another_large_file(filesystem):
    f = filesystem; ref = closure(f.root)
    ordinary = previous.put(f.root / 'untrusted-metadata.json',
                            proof.canonical({'private_large_files': [allowance(ref)]}))
    value = verifier(io.StableReader(f.budget, public_files={}))
    assert value.load(ordinary)
    with pytest.raises(EvidenceError): value.load(ref)


def test_ordinary_reference_uses_unchanged_superclass(filesystem, monkeypatch):
    f = filesystem; ref = closure(f.root); ordinary = previous.put(f.root / 'ordinary.json')
    value = verifier(reader(f, ref)); calls = []
    original = proof.ProvenanceVerifier.load
    def delegated(self, item):
        calls.append(copy.deepcopy(item)); return original(self, item)
    monkeypatch.setattr(proof.ProvenanceVerifier, 'load', delegated)
    assert value.load(ordinary)
    assert calls == [ordinary]
    value.load(ref)
    assert calls == [ordinary]


def test_large_reference_rejects_reader_mutation_and_cache_drift(filesystem, monkeypatch):
    f = filesystem; ref = closure(f.root); selected = reader(f, ref)
    base = type(selected)
    class Mutating(base):
        def __call__(self, reference):
            raw = super().__call__(reference)
            reference['sha256'] = 'b' * 64
            return raw
    value = verifier(Mutating(f.budget, public_files={}, private_large_files=[allowance(ref)]))
    with pytest.raises(EvidenceError): value.load(ref)
    assert value.raws == {} and ref['sha256'] != 'b' * 64
