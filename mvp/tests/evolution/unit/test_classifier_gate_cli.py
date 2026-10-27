"""Explicit parent authority and original CLI deadline; no native operations."""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from types import ModuleType, SimpleNamespace

import pytest
from evolution import run
from evolution.registry import EvidenceError, load_registry

from mvp.tests.evolution.unit.test_runner import case


@pytest.fixture
def authority(tmp_path, monkeypatch):
    events = []
    final = ModuleType('evolution.classifier_final_worker_qualification')
    final.verify = lambda **kwargs: pytest.fail('CLI must not evaluate a model or receipt')
    coordinator = ModuleType('evolution.classifier_parent_context')
    class Context:
        def __init__(self, reference, root, deadline, *, qualify_final_worker):
            events.append(('construct', reference, root, deadline, qualify_final_worker))
        def __enter__(self):
            events.append(('enter',)); return self
        def __exit__(self, *args):
            events.append(('close',)); return False
    coordinator.ParentClassifierContext = Context
    monkeypatch.setitem(sys.modules, final.__name__, final)
    monkeypatch.setitem(sys.modules, coordinator.__name__, coordinator)
    path = tmp_path / 'capsule.json'
    deadline = time.monotonic() + 100
    options = ['--classifier-run-capsule', str(path), '--classifier-run-capsule-sha256',
               'a' * 64, '--classifier-run-deadline', repr(deadline)]
    return SimpleNamespace(**locals())


@pytest.mark.parametrize('command', ['gate', 'acceptance'])
def test_exact_authority_closes_context_for_gate_and_release(authority, monkeypatch, command):
    f = authority; seen = []
    monkeypatch.setattr(run, 'load_registry', lambda *a: ())
    def gate(phase, *, classifier_context):
        assert isinstance(classifier_context, f.Context)
        seen.append(phase); return 0
    monkeypatch.setattr(run, 'run_gate', gate)
    arguments = ['gate', '3'] if command == 'gate' else ['acceptance']
    assert run.main(arguments + f.options) == 0
    assert seen == ([3] if command == 'gate' else list(range(1, 7)))
    assert f.events == [('construct', {'path': str(f.path), 'sha256': 'a' * 64}, run.ROOT,
                         f.deadline, f.final.verify), ('enter',), ('close',)]


@pytest.mark.parametrize('command', ['gate', 'acceptance'])
def test_ordinary_gate_never_constructs_classifier_context(authority, monkeypatch, command):
    f = authority; seen = []
    monkeypatch.setattr(run, 'load_registry', lambda *a: ())
    monkeypatch.setattr(run, 'run_gate', lambda phase: seen.append(phase) or 0)
    assert run.main(['gate', '3'] if command == 'gate' else ['acceptance']) == 0
    assert seen == ([3] if command == 'gate' else list(range(1, 7)))
    assert f.events == []


@pytest.mark.parametrize('fault', ['missing_path', 'missing_hash', 'missing_deadline', 'relative',
                                  'bad_hash', 'expired', 'nan', 'infinity'])
def test_invalid_authority_never_constructs_context_or_runs_gate(authority, monkeypatch, fault):
    f = authority
    monkeypatch.setattr(run, 'run_gate', lambda *a, **k: pytest.fail('unauthorized gate'))
    args = list(f.options)
    if fault.startswith('missing_'):
        index = {'missing_path': 0, 'missing_hash': 2, 'missing_deadline': 4}[fault]
        del args[index:index + 2]
    elif fault == 'relative': args[1] = 'capsule.json'
    elif fault == 'bad_hash': args[3] = 'not-a-sha'
    else: args[5] = {'expired': '1', 'nan': 'nan', 'infinity': 'inf'}[fault]
    assert run.main(['gate', '3', *args]) == 1
    assert f.events == []


def test_gate_failure_still_closes_original_context(authority, monkeypatch):
    f = authority
    def fail(*a, **k):
        raise EvidenceError('synthetic_gate_failure')
    monkeypatch.setattr(run, 'run_gate', fail)
    assert run.main(['gate', '3', *f.options]) == 1
    assert [row[0] for row in f.events] == ['construct', 'enter', 'close']


@pytest.mark.parametrize('identifier,valid', [('SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-RUNTIME-1', True),
    ('SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-RUNTIME-2', True), ('SECRET-CLASSIFIER', False),
    ('SEC-lowercase', False), ('SEC-', False), ('SEC-CLASSIFIER/FOREIGN', False)])
def test_security_case_namespace_keeps_closed_id_validation(tmp_path, identifier, valid):
    row = asdict(case()); row['case_id'] = identifier; row['assertions'] = list(row['assertions'])
    document = {'schema_version': 'guardrails-evolution-cases-v1', 'phases': {
        str(number): {'status': 'ready' if number == 1 else 'unavailable',
                      'required_families': ['registry'], 'cases': [row] if number == 1 else []}
        for number in range(1, 7)}}
    path = tmp_path / 'cases.json'; path.write_text(json.dumps(document))
    if valid:
        assert load_registry(path, 1)[0].case_id == identifier
    else:
        with pytest.raises(EvidenceError, match='invalid_case_id'): load_registry(path, 1)


@pytest.mark.parametrize('missing', ['final', 'coordinator'])
def test_absent_fixed_adapter_is_a_finite_refusal_before_context_or_gate(authority, monkeypatch, capsys, missing):
    f = authority
    module = f.final if missing == 'final' else f.coordinator
    monkeypatch.setitem(sys.modules, module.__name__, None)
    monkeypatch.setattr(run, 'run_gate', lambda *a, **kw: pytest.fail('gate without fixed adapter'))
    assert run.main(['gate', '3', *f.options]) == 1
    assert capsys.readouterr().err.strip() == 'classifier_run_adapter_unavailable'
    assert f.events == []


@pytest.mark.parametrize('failure', [KeyboardInterrupt, SystemExit])
def test_fixed_import_boundary_does_not_swallow_process_control(authority, monkeypatch, failure):
    f = authority
    monkeypatch.delattr(f.final, 'verify')
    def missing(name):
        if name == 'verify':
            raise failure()
        raise AttributeError(name)
    monkeypatch.setattr(f.final, '__getattr__', missing, raising=False)
    with pytest.raises(failure):
        run.main(['gate', '3', *f.options])
    assert f.events == []
