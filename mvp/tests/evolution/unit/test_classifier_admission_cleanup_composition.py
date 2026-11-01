"""Real fault objects→private cleanup inventory route; native execution faked."""
from __future__ import annotations

import copy
import time

import pytest

from mvp.tests.evolution.e2e import classifier_admission_fault as s
from mvp.tests.evolution.unit import test_classifier_admission_fault as admission
from mvp.tests.evolution.unit import test_classifier_native_readonly as native

forbid_native = native.forbid_native
static_fixture = native.static_fixture
fixture = native.fixture


def configured(f, monkeypatch):
    executor = f.executor
    executor.manifest['deadline_monotonic'] = time.monotonic() - 1
    program = b'# synthetic fixed original-child transition\n'
    auth = admission.authority(program)
    bound = executor.preflight['binding']; pod = bound['pods']['classifier']
    auth.update(owner_id=bound['owner_id'], node_id=bound['node_id'], pod_uid=pod['pod_uid'],
                container_id=pod['container_id'].removeprefix('containerd://'))
    calls = []
    old = executor._invoke
    def invoke(self, argv, name, deadline, maximum, input_text=None):
        self._check(deadline)
        if name == 'kubectl' and argv[-2] == program.decode():
            settings = s.native.contract.parse(argv[-1].encode())
            assert settings['operation'] == 'resume' and settings['expected'] == auth['original']
            calls.append('resume')
            return '{"status":"gone","signalled":false}'
        calls.append('inventory' if argv[-4:] == s.native.GET else 'docker')
        return old(argv, name, deadline, maximum, input_text)
    monkeypatch.setattr(s.native.NativeReadOnlyExecutor, '_invoke', invoke)
    monkeypatch.setattr(s.native.NativeReadOnlyExecutor, '_sources', lambda self, deadline: self._check(deadline))
    records = []
    fault = s.OwnedClassifierFault(executor, auth, program, record=lambda name, value: records.append((name, value)),
        readiness=lambda **kwargs: pytest.fail('cleanup cannot request readiness'),
        authorize=lambda *args, **kwargs: pytest.fail('cleanup cannot renew authority'),
        verify=lambda *args, **kwargs: pytest.fail('cleanup cannot qualify'), deadline=time.monotonic() - 1)
    fault.expected = copy.deepcopy(auth['original'])
    fault.bound_transport = (f.prefix, f.text, pod['pod_name'])
    fault.cleanup_binding = (s.evidence.digest(executor.manifest), s.evidence.digest(executor.preflight), executor.root)
    fault.cleanup_evidence = (s.evidence.digest(auth), s.hashlib.sha256(program).hexdigest(),
        s.evidence.digest(fault.expected), s.evidence.digest(fault.bound_transport))
    return fault, calls, records


def test_expired_public_manifest_does_not_prevent_exact_owned_resume_route(fixture, monkeypatch):
    f = fixture; fault, calls, records = configured(f, monkeypatch)
    original_manifest = copy.deepcopy(f.executor.manifest)
    with pytest.raises(s.native.NativeReadOnlyError, match='native_deadline_extension'):
        native.call(f, (*f.prefix, *s.native.GET), input_text=f.text)
    assert calls == []
    assert fault.cleanup(deadline=time.monotonic() - 1) == 'clear'
    assert calls.count('resume') == 1 and calls.count('inventory') == 2
    assert records[-1][0].endswith('-cleanup.json')
    assert f.executor.manifest == original_manifest
    with pytest.raises(s.evidence.AdmissionError):
        fault.cleanup(deadline=time.monotonic() + 10)
    assert calls.count('resume') == 1


@pytest.mark.parametrize('fault_name', ['owner', 'pod', 'route', 'original_manifest', 'program', 'expected'])
def test_cleanup_never_signals_after_owned_scope_changes(fixture, monkeypatch, fault_name):
    f = fixture; fault, calls, _ = configured(f, monkeypatch)
    if fault_name == 'owner':
        fault.auth['owner_id'] = 'foreign'
    elif fault_name == 'pod':
        fault.auth['pod_uid'] = admission.authority()['pod_uid']
    elif fault_name == 'route':
        fault.bound_transport = (f.prefix, 'foreign config', fault.bound_transport[2])
    elif fault_name == 'original_manifest':
        f.executor.manifest['deadline_monotonic'] += 1
    elif fault_name == 'program':
        fault.program += b'\n'
    else:
        fault.expected['child']['start_ticks'] += 1
    with pytest.raises(s.evidence.AdmissionError):
        fault.cleanup(deadline=time.monotonic() - 1)
    assert 'resume' not in calls


@pytest.mark.parametrize('capability', ['readiness', 'delete', 'docker', 'new_deadline', 'large_output', 'foreign_input'])
def test_private_cleanup_callable_is_inventory_only(fixture, monkeypatch, capability):
    f = fixture; fault, calls, _ = configured(f, monkeypatch)
    stop = time.monotonic() + 10
    budget = s._CleanupBudget(f.executor, stop, *fault.cleanup_binding)
    executor = s._CleanupExecutor(f.executor, budget, fault.bound_transport)
    argv = (*f.prefix, *s.native.GET); deadline = stop
    maximum = s.native.MAX_OUTPUT; text = f.text
    if capability == 'readiness': argv = (*f.prefix, *f.ready)
    elif capability == 'delete': argv = (*f.prefix, 'delete', 'pod', fault.bound_transport[2])
    elif capability == 'docker': argv = ('/usr/bin/docker', '--host', s.native.network.SOCKET, 'container', 'inspect', 'anything')
    elif capability == 'new_deadline': deadline += 1
    elif capability == 'large_output': maximum += 1
    else: text += ' '
    with pytest.raises(s.evidence.AdmissionError):
        executor(argv, deadline=deadline, timeout=10, maximum=maximum, input_text=text)
    assert calls == []
