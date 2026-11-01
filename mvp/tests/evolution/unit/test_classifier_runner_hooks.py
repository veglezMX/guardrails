"""Parent lifecycle checks with fake children; no deployment or model operations."""
import json
import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest
from evolution import run
from evolution.classifier_worker_invocation_v3 import MANIFEST_ENV, SHA_ENV
from evolution.registry import Case, EvidenceError

CASE = Case('OPS-CLASSIFIER-CNI-ORIGINAL-GROUP', 'P3.4', 'local', 'remote', 'classifier',
    'mvp/tests/evolution/e2e/test_cluster_classifier_cni.py::test_classifier_original_cni_group', ('Original evidence',), 120)


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    root = tmp_path / 'root'; (root / 'mvp/evolution').mkdir(parents=True)
    (root / 'mvp/evolution/cases.json').write_text('{}')
    destination = tmp_path / 'results'; destination.mkdir(mode=0o700)
    events = []; state = {'fault': None}; observed = {}
    result = {'nodeid': CASE.nodeid, 'status': 'pass', 'duration_ms': 1, 'assertions': 1, 'reason_codes': []}
    class Invocation:
        def __init__(self):
            self.worker_environment = {MANIFEST_ENV: str(tmp_path / 'manifest.json'), SHA_ENV: 'a' * 64}
        def started(self, **kwargs):
            events.append('started'); observed['started'] = kwargs
            if state['fault'] == 'start': raise EvidenceError('synthetic')
        def complete(self, **kwargs):
            events.append('complete'); observed['completed'] = kwargs
            if state['fault'] == 'completion': raise EvidenceError('synthetic')
        def failed(self, **kwargs): events.append('failed'); observed['failed'] = kwargs
    invocation = Invocation()
    class Context:
        def __init__(self):
            self.consume_verifiers = {CASE.case_id: object()}
            self.deadline = run.time.monotonic() + 1000
        def prepare(self, **kwargs):
            events.append('prepare'); observed['prepare'] = kwargs
            if state['fault'] == 'prepare': raise EvidenceError('synthetic')
            if state['fault'] == 'argv': kwargs['argv'].append('--foreign')
            if state['fault'] == 'environment': invocation.worker_environment = {'FOREIGN': 'bad'}
            return invocation
    context = Context()
    class Process:
        pid = 24680
        returncode = 0
        def __init__(self, argv, **kwargs):
            events.append('spawn'); observed['argv'] = list(argv); observed['env'] = dict(kwargs['env'])
            assert kwargs['start_new_session'] is True
        def wait(self, timeout=None):
            events.append('reap' if timeout is None else 'wait')
            if timeout is not None:
                observed['timeout'] = timeout
                if state['fault'] == 'timeout': raise subprocess.TimeoutExpired('synthetic', timeout)
                if state['fault'] == 'worker': self.returncode = 1; result['status'] = 'fail'; result['reason_codes'] = ['case_call_failed']
                (destination / (CASE.case_id + '.json')).write_text(json.dumps(result))
            return self.returncode
    monkeypatch.setattr(run, 'source_snapshot', lambda *args: {'mvp/example.py': 'b' * 64})
    monkeypatch.setattr(run, 'write_catalog', lambda path, *args: path.write_text('{}'))
    monkeypatch.setattr(run, 'prepare_directory', lambda *args: None)
    monkeypatch.setattr(run, 'check_case', lambda *args: None)
    monkeypatch.setattr(run.subprocess, 'Popen', Process)
    monkeypatch.setattr(run, 'worker_process_start', lambda pid: 123456)
    monkeypatch.setattr(run.os, 'killpg', lambda pid, sig: events.append('kill_owned'))
    def collect(*args, **kwargs):
        events.append('collect'); observed['consume'] = kwargs['provenance']; observed['collector_deadline'] = kwargs['classifier_deadline']; return {}
    monkeypatch.setattr(run, 'collect_measurements', collect)
    from evolution import platform_database
    monkeypatch.setattr(platform_database, 'environment_for_case', lambda *args, **kwargs: {})
    monkeypatch.setenv(MANIFEST_ENV, '/foreign/ambient.json'); monkeypatch.setenv(SHA_ENV, 'f' * 64)
    return SimpleNamespace(**locals())


def test_missing_context_refuses_before_any_process_or_authorization(scenario):
    s = scenario; value = run.execute_case(CASE, s.root, s.destination)
    assert value['status'] == 'fail' and 'classifier_provenance_missing_or_invalid' in value['reason_codes']
    assert s.events == ['collect']


def test_original_process_observed_before_wait_and_same_verifier_reaches_collector(scenario):
    s = scenario; value = run.execute_case(CASE, s.root, s.destination, classifier_context=s.context)
    assert value['status'] == 'pass'
    assert s.events == ['prepare', 'spawn', 'started', 'wait', 'complete', 'collect']
    assert s.observed['started']['pid'] == 24680 and s.observed['started']['process_start'] == 123456
    assert s.observed['env'][MANIFEST_ENV] == s.invocation.worker_environment[MANIFEST_ENV]
    assert s.observed['env'][SHA_ENV] == 'a' * 64 and 0 < s.observed['timeout'] <= 120
    assert s.observed['completed']['exit_code'] == 0 and s.observed['completed']['result'] == s.result
    assert s.observed['consume'] is s.context.consume_verifiers
    assert s.observed['collector_deadline'] == s.context.deadline
    args = s.observed['argv']
    assert args[args.index('--classifier-case-id') + 1] == CASE.case_id
    assert float(args[args.index('--classifier-deadline') + 1]) == s.observed['prepare']['deadline']


@pytest.mark.parametrize('fault', ['prepare', 'argv', 'environment'])
def test_invalid_prelaunch_authority_never_spawns(scenario, fault):
    s = scenario; s.state['fault'] = fault
    value = run.execute_case(CASE, s.root, s.destination, classifier_context=s.context)
    assert value['status'] == 'fail' and 'spawn' not in s.events and 'complete' not in s.events


@pytest.mark.parametrize('fault', ['start', 'timeout'])
def test_owned_child_is_killed_and_reaped_on_parent_identity_or_deadline_failure(scenario, fault):
    s = scenario; s.state['fault'] = fault
    value = run.execute_case(CASE, s.root, s.destination, classifier_context=s.context)
    assert value['status'] == 'fail' and 'complete' not in s.events
    assert s.events.index('kill_owned') < s.events.index('reap') < s.events.index('failed')


@pytest.mark.parametrize('fault', ['worker', 'completion'])
def test_failure_never_becomes_successful_collected_case(scenario, fault):
    s = scenario; s.state['fault'] = fault
    value = run.execute_case(CASE, s.root, s.destination, classifier_context=s.context)
    assert value['status'] == 'fail' and value['reason_codes']
    assert ('failed' in s.events) == (fault == 'worker')


def test_nonclassifier_cannot_inherit_a_classifier_manifest(scenario):
    s = scenario
    selected = replace(CASE, case_id='GOV-UNRELATED')
    run.execute_case(selected, s.root, s.destination)
    assert 'prepare' not in s.events and 'started' not in s.events
    assert MANIFEST_ENV not in s.observed['env'] and SHA_ENV not in s.observed['env']
