"""Genuine cumulative-authority fixtures; file reads only, future8/9 are synthetic.
Copied fixture functions preserve independent author's130-test construction.
No deployment, native process or model execution. Never an operational receipt.
"""
from __future__ import annotations
import copy,hashlib,os,stat
from pathlib import Path
from datetime import UTC,datetime,timedelta
from types import SimpleNamespace
import pytest
from evolution import classifier_provenance as p
from evolution import classifier_corpus_extension_v3 as s
from mvp.tests.evolution.unit import classifier_provenance_v3_test_base as old
SESSION=Path('/home/veglez/.agent-sessions/own/ai/guardrails/sessions/2026-10-11T1910Z_gpt-6_f945d455_guardrails-evolution-resume')
IWT=Path('/home/veglez/.worktrees/own/ai/guardrails/guardrails-phase3-integration-f945d455')
REAL_REF={'path':str(SESSION/'tasks/05-phase3/bounded-selection-candidate-07/qualification-corpus-manifest.json'),'sha256':'0fe778873a48d46e19a1643392b83cbee557a3ed0d7bed70ed139c84279855b3'}
def file_raw(ref):
    """Only exact corpus-authority references below the supplied public roots."""
    path = Path(ref['path'])
    assert path.is_absolute() and path.resolve() == path
    assert path.is_relative_to(SESSION) or path.is_relative_to(IWT / 'mvp/tests/evolution/fixtures')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        assert stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid() and before.st_nlink == 1
        assert stat.S_IMODE(before.st_mode) in {0o400, 0o444, 0o600, 0o644}
        assert 0 < before.st_size <= 4 * 1024 * 1024
        raw = bytearray()
        while len(raw) <= before.st_size:
            chunk = os.read(fd, min(65536, before.st_size + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        def stamp(row):
            return row.st_dev, row.st_ino, row.st_uid, row.st_mode, row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns
        assert len(raw) == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.stat(follow_symlinks=False))
        assert hashlib.sha256(raw).hexdigest() == ref['sha256']
        return bytes(raw)
    finally:
        os.close(fd)


class Store:
    def __init__(self, cached):
        self.raws = dict(cached)
        self.calls = []
        self.serial = 0

    def load(self, ref):
        self.calls.append(copy.deepcopy(ref))
        return self.raws[ref['path']]

    def put(self, name, value):
        path = '/synthetic-v3/' + name
        raw = p.canonical(value) + b'\n'
        self.raws[path] = raw
        return {'path': path, 'sha256': hashlib.sha256(raw).hexdigest()}

    def changed(self, name, value):
        self.serial += 1
        return self.put(f'changed-{self.serial}-{name}.json', value)


def authorization(value, store):
    selected = p.document(store.load(value['selection_manifest']))
    return {'created_at': '2026-10-13T00:00:00+00:00',
        'identity': {'model_id': selected['repository'], 'model_revision': selected['revision'],
                     'weights_sha256': selected['artifacts']['model.safetensors']['sha256']},
        'required_corpora': copy.deepcopy(value['corpora'])}


@pytest.fixture(scope='module')
def authenticated_seed():
    cache = {}
    def load(ref):
        raw = file_raw(ref)
        assert ref['path'] not in cache or cache[ref['path']] == raw
        cache[ref['path']] = raw
        return raw
    root = p.document(load(REAL_REF))
    selected = p.document(load(root['selection_manifest']))
    auth = {'created_at': '2026-10-13T00:00:00+00:00', 'identity': {
        'model_id': selected['repository'], 'model_revision': selected['revision'],
        'weights_sha256': selected['artifacts']['model.safetensors']['sha256']},
        'required_corpora': copy.deepcopy(root['corpora'])}
    assert s.verify_corpus_manifest(copy.deepcopy(REAL_REF), authorization=auth, load=load) == root
    return dict(cache)


def moment(index, seconds):
    return (datetime(2026, 10, 12, 20, tzinfo=UTC) + timedelta(minutes=index, seconds=seconds)).isoformat()


def build(seed, index=8):
    store = Store(seed)
    previous = copy.deepcopy(REAL_REF)
    value = p.document(store.load(previous))
    for current in range(8, index + 1):
        original_value = value
        value = copy.deepcopy(original_value)
        key = f'confirmation_{current}'
        corpus = old.synthetic_corpus(f'future-{current}-verifier-only')
        corpus['provenance'].update(prior_corpus_access=False, schema_shape_only_access=False, schema_shape_source=None)
        corpus_ref = store.put(f'{key}.json', corpus)
        value['corpora'][key] = corpus_ref
        selected = p.document(store.load(original_value['selection_manifest']))
        selected.update(selection_index=current, recorded_at=moment(current, 0), repository=f'synthetic/future-{current}',
                        revision=f'{current:040x}', qualification=s.qualification(current))
        selected['artifacts'] = {'model.safetensors': {'sha256': str(current) * 64}}
        value['selection_manifest'] = store.put(f'selected-{current}.json', selected)
        projection = p.corpus_projection(store.load(corpus_ref), key)
        files = {Path(corpus_ref['path']).name: corpus_ref['sha256']}
        for filename in ('AUTHORSHIP.md', 'VALIDATION.json', 'author_corpus.py', 'AUTHORING_STATUS.md'):
            files[filename] = store.put(f'author-{current}/{filename}', {'synthetic': filename})['sha256']
        author = p.document(store.load(original_value['author_freeze']))
        author.update(corpus_id=corpus['corpus_id'], frozen_at_utc=moment(current, 10), files=files,
                      post_freeze_prior_overlap_check='Synthetic verifier exercise; no operational author claim')
        author['full_normalized_text_uniqueness']['ordered_normalized_text_sha256'] = projection['ordered_sha256']
        value['author_freeze'] = store.put(f'author-{current}/FREEZE.json', author)
        review = p.document(store.load(original_value['parent_overlap_review']))
        review.update(reviewed_at=moment(current, 20), selection_index=current, confirmation=corpus_ref,
                      author_freeze=value['author_freeze'], prior_corpora=copy.deepcopy(original_value['corpora']),
                      prior_corpus_overlap={name: 0 for name in original_value['corpora']})
        value['parent_overlap_review'] = store.put(f'overlap-{current}.json', review)
        value.update(selection_index=current, frozen_at=moment(current, 30), previous_manifest=previous)
        previous = store.put(f'manifest-{current}.json', value)
    return SimpleNamespace(store=store, ref=previous, value=value, authorization=authorization(value, store))

