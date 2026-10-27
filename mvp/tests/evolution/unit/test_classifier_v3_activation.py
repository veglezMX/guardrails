"""Fixed deployed-v3 parent wiring; no client or process is constructed."""
from __future__ import annotations

import sys
import time
from types import ModuleType, SimpleNamespace

import pytest

from evolution import run
from evolution.registry import EvidenceError


@pytest.fixture
def setup(tmp_path, monkeypatch):
    calls = []
    final = ModuleType('evolution.classifier_final_worker_qualification')
    final.verify = lambda **kwargs: pytest.fail('final qualification remains deferred until context authentication')
    successor = ModuleType('evolution.classifier_parent_context_v3')
    predecessor = ModuleType('evolution.classifier_parent_context')
    def construct(reference, root, deadline, **kwargs):
        calls.append((reference, root, deadline, kwargs))
        return 'unentered-versioned-context'
    successor.ParentClassifierContext = construct
    predecessor.ParentClassifierContext = lambda *_args, **_kwargs: pytest.fail('old authority cannot be selected')
    monkeypatch.setitem(sys.modules, final.__name__, final)
    monkeypatch.setitem(sys.modules, successor.__name__, successor)
    monkeypatch.setitem(sys.modules, predecessor.__name__, predecessor)
    capsule = tmp_path / 'independent-parent.json'; capsule.write_text('{}')
    args = SimpleNamespace(classifier_run_capsule=capsule, classifier_run_capsule_sha256='a' * 64,
                           classifier_run_deadline=time.monotonic() + 30)
    return SimpleNamespace(**locals())


def test_fixed_v3_parent_receives_independent_reference_original_deadline_and_final_adapter(setup):
    f = setup
    assert run.classifier_run_context(f.args) == 'unentered-versioned-context'
    assert f.calls == [({'path': str(f.capsule), 'sha256': 'a' * 64}, run.ROOT,
        f.args.classifier_run_deadline, {'qualify_final_worker': f.final.verify})]


def test_unavailable_final_worker_adapter_still_refuses_before_context(setup):
    del setup.final.verify
    with pytest.raises(EvidenceError, match='classifier_run_adapter_unavailable'):
        run.classifier_run_context(setup.args)
    assert setup.calls == []


@pytest.mark.parametrize('fault', ('partial', 'relative', 'hash', 'expired'))
def test_invalid_parent_authority_cannot_reach_successor(setup, fault):
    f = setup
    if fault == 'partial': f.args.classifier_run_capsule_sha256 = None
    elif fault == 'relative': f.args.classifier_run_capsule = type(f.capsule)('relative.json')
    elif fault == 'hash': f.args.classifier_run_capsule_sha256 = 'b' * 63
    else: f.args.classifier_run_deadline = time.monotonic() - 1
    with pytest.raises(EvidenceError): run.classifier_run_context(f.args)
    assert f.calls == []


def test_unrelated_run_does_not_construct_any_classifier_context(setup):
    f = setup
    f.args.classifier_run_capsule = f.args.classifier_run_capsule_sha256 = f.args.classifier_run_deadline = None
    with run.classifier_run_context(f.args) as context: assert context is None
    assert f.calls == []
