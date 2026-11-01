"""Actual exclusive retention/native producer and strict parent completion semantics."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as N
from uuid import uuid4

import pytest
from evolution import classifier_admission_records as s
from evolution.registry import EvidenceError

from mvp.tests.evolution.e2e import classifier_fixture_builder_v2 as builder
from mvp.tests.evolution.unit import test_classifier_admission_authority as auth_tests
from mvp.tests.evolution.unit import test_classifier_provenance_io_v2 as io_tests

no_operations = auth_tests.no_operations
case = auth_tests.case
filesystem = io_tests.filesystem


def put(path, value):
    raw = s.proof.canonical(value) + b'\n'; path.write_bytes(raw); path.chmod(0o600)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def completion(case):
    f = case; a = f.auth
    value = {'schema_version': s.COMPLETION_SCHEMA, 'authorization': copy.deepcopy(f.ref),
        'case_id': a['case_id'], 'operation': a['operation'], 'started_at': auth_tests.base.stamp(-5),
        'completed_at': auth_tests.base.stamp(-1), 'pid': 200, 'process_start': 300, 'exit_code': 0, 'elapsed_ms': 4000,
        'worker_result': {'case_id': a['case_id'], 'status': 'pass', 'assertions': 1, 'reason_codes': []},
        'observation': {'path': a['output_directory'] + '/admission-final.json', 'sha256': 'a' * 64},
        'current_checks': [{'path': a['checks_directory'] + f'/{i:02d}-{phase}.json', 'sha256': str(i) * 64}
                           for i, phase in enumerate(s.authority.PHASES, 1)],
        'governance_files': {'team-a': {}, 'team-b': {}},
        'native_attempts': {kind: {'path': a['checks_directory'] + '/' + folder + '/index.json', 'sha256': 'b' * 64}
            for kind, folder in [('ordinary', 'native-attempts'), ('cleanup', 'cleanup-native-attempts')]}}
    def validate(): return s.validate_completion(value, reference=f.ref, authorized=a, now=auth_tests.base.NOW)
    return N(**locals())


def test_original_parent_completion_closes_exact_four_checks_and_observation(completion):
    f = completion
    assert f.validate() == f.value


@pytest.mark.parametrize('change', ['worker-failed', 'exit-one', 'exit-bool', 'wrong-case', 'wrong-operation',
    'wrong-authorization', 'no-assertions', 'reason', 'pid-bool', 'pid-zero', 'start-zero', 'elapsed-bound',
    'before-created', 'completed-before-started', 'future-completion', 'expired', 'extra-field',
    'observation-path', 'current-omission', 'current-order', 'current-other-path', 'foreign-native', 'missing-tenant'])
def test_completion_never_upgrades_failed_unowned_or_replayed_worker(completion, change):
    f = completion; v = f.value
    if change == 'worker-failed': v['worker_result']['status'] = 'fail'
    elif change == 'exit-one': v['exit_code'] = 1
    elif change == 'exit-bool': v['exit_code'] = False
    elif change == 'wrong-case': v['case_id'] = s.authority.evidence.CASES[1]
    elif change == 'wrong-operation': v['operation'] = str(uuid4())
    elif change == 'wrong-authorization': v['authorization']['sha256'] = '0' * 64
    elif change == 'no-assertions': v['worker_result']['assertions'] = 0
    elif change == 'reason': v['worker_result']['reason_codes'] = ['incomplete']
    elif change == 'pid-bool': v['pid'] = True
    elif change == 'pid-zero': v['pid'] = 0
    elif change == 'start-zero': v['process_start'] = 0
    elif change == 'elapsed-bound': v['elapsed_ms'] = 900001
    elif change == 'before-created': v['started_at'] = auth_tests.base.stamp(-100)
    elif change == 'completed-before-started': v['completed_at'] = auth_tests.base.stamp(-6)
    elif change == 'future-completion': v['completed_at'] = auth_tests.base.stamp(1)
    elif change == 'expired': f.a['expires_at'] = auth_tests.base.stamp(0)
    elif change == 'extra-field': v['successful'] = True
    elif change == 'observation-path': v['observation']['path'] = '/foreign/admission-final.json'
    elif change == 'current-omission': v['current_checks'].pop()
    elif change == 'current-order': v['current_checks'].reverse()
    elif change == 'current-other-path': v['current_checks'][0]['path'] = '/foreign/current.json'
    elif change == 'foreign-native': v['native_attempts']['ordinary']['path'] = '/foreign/index.json'
    elif change == 'missing-tenant': del v['governance_files']['team-b']
    with pytest.raises(EvidenceError): f.validate()


@pytest.fixture
def native(filesystem, request):
    f = filesystem; fault = getattr(request, 'param', False)
    authorization = {'case_id': s.authority.evidence.CASES[2 if fault else 0]}
    auth_ref = {'path': str(f.root / 'authorization.json'), 'sha256': 'c' * 64}
    observation = {'measurement': {'fault': {} if fault else None}}
    attempt = {'spawned': True, 'stdout': b'private native output', 'stderr': b'',
               'truncated': False, 'cleanup': 'completed', 'status': 'completed'}
    directories = {kind: s.io.PrivateDirectory(f.root / kind, f.budget, create=True) for kind in ('ordinary','cleanup')}
    indexes = {}
    try:
        for kind, directory in directories.items():
            rows = [copy.deepcopy(attempt)] if kind == 'ordinary' or fault else []
            indexes[kind] = builder.save_attempts(N(attempts=rows), directory,
                authorization_ref=auth_ref, outcome='worker_returned')
    finally:
        for directory in directories.values(): directory.close()
    completion = {'authorization': auth_ref, 'native_attempts': indexes}
    def load(ref): return f.reader(ref)
    def verify(): return s.verify_native(completion=completion, authorization=authorization,
        observation=observation, load=load)
    return N(**locals())


@pytest.mark.parametrize('native', [False, True], indirect=True)
def test_original_retention_producer_is_consumed_without_exposing_private_output(native):
    f = native
    assert f.verify() is True
    assert not any('stdout' in k or 'stderr' in k for k in f.completion)


@pytest.mark.parametrize('change', ['failed-outcome', 'foreign-authorization', 'no-ordinary', 'raw-count',
    'success-claim', 'truncated', 'cleanup-failed', 'status-failed', 'not-spawned', 'invalid-base64',
    'noncanonical-base64', 'extra-row-field', 'wrong-row-path', 'empty-fault-cleanup', 'unexpected-cleanup'])
def test_coherently_rehashed_native_failures_do_not_become_success(native, change):
    f = native; index = json.loads(Path(f.indexes['ordinary']['path']).read_bytes())
    row_ref = index['attempts'][0]; row = json.loads(Path(row_ref['path']).read_bytes())
    if change == 'failed-outcome': index['outcome'] = 'worker_failed'
    elif change == 'foreign-authorization': index['authorization']['sha256'] = '0' * 64
    elif change == 'no-ordinary': index['attempts'] = []; index['raw_bytes'] = 0
    elif change == 'raw-count': index['raw_bytes'] += 1
    elif change == 'success-claim': index['operational_success_claim'] = True
    elif change == 'truncated': row['truncated'] = True
    elif change == 'cleanup-failed': row['cleanup'] = 'failed'
    elif change == 'status-failed': row['status'] = 'failed'
    elif change == 'not-spawned': row['spawned'] = False
    elif change == 'invalid-base64': row['stdout_base64'] = '!'
    elif change == 'noncanonical-base64': row['stdout_base64'] += '='
    elif change == 'extra-row-field': row['success'] = True
    elif change == 'wrong-row-path': index['attempts'][0]['path'] = str(f.f.root / 'elsewhere.json')
    elif change == 'empty-fault-cleanup': f.authorization['case_id'] = s.authority.evidence.CASES[2]; f.observation['measurement']['fault'] = {}
    elif change == 'unexpected-cleanup': f.indexes['cleanup'] = f.indexes['ordinary']
    if change in {'truncated','cleanup-failed','status-failed','not-spawned','invalid-base64','noncanonical-base64','extra-row-field'}:
        index['attempts'][0] = put(Path(row_ref['path']), row)
    f.indexes['ordinary'] = put(Path(f.indexes['ordinary']['path']), index)
    with pytest.raises((EvidenceError, FileNotFoundError)): f.verify()
