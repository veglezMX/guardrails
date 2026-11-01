"""The successor Budget must traverse the unchanged real native call guard."""
from __future__ import annotations

import pytest
from evolution import classifier_provenance_io_v3 as io

from mvp.tests.evolution.unit import test_classifier_native_readonly as original

forbid_native = original.forbid_native
static_fixture = original.static_fixture
fixture = original.fixture


@pytest.mark.parametrize('kind', ['docker', 'inventory', 'readiness'])
def test_versioned_budget_composes_with_original_native_guard(fixture, kind):
    f = fixture
    f.executor.budget = io.Budget(f.budget.deadline)
    assert isinstance(f.executor.budget, original.subject.private_io.Budget)
    if kind == 'docker':
        argv = ('/usr/bin/docker', '--host', original.subject.network.SOCKET,
                *next(iter(original.subject.BOUNDARY)))
        result = original.call(f, argv)
    else:
        suffix = original.subject.GET if kind == 'inventory' else f.ready
        result = original.call(f, (*f.prefix, *suffix), input_text=f.text)
    assert result and f.calls
    assert f.executor.budget.deadline == f.budget.deadline


def test_new_budget_does_not_extend_original_native_manifest(fixture):
    f = fixture
    f.executor.budget = io.Budget(f.budget.deadline + 100)
    with pytest.raises(original.subject.NativeReadOnlyError, match='native_deadline_extension'):
        f.executor((*f.prefix, *original.subject.GET), deadline=f.budget.deadline + 1,
                   timeout=10, maximum=original.subject.MAX_OUTPUT, input_text=f.text)
    assert not f.calls


def test_plain_budget_lookalike_remains_refused(fixture):
    f = fixture
    class Lookalike:
        deadline = f.budget.deadline
        def check(self):
            raise AssertionError('lookalike must be refused before check')
    f.executor.budget = Lookalike()
    with pytest.raises(original.subject.NativeReadOnlyError, match='native_call_limits'):
        original.call(f, (*f.prefix, *original.subject.GET), input_text=f.text)
    assert not f.calls
