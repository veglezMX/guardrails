"""Actual compiler/journal shapes, with synthetic management and SQL only."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path

import pytest
from evolution import classifier_provenance as original
from evolution import classifier_provenance_v3 as successor
from evolution.registry import EvidenceError
from guardrails.evolution import models

from mvp.tests.evolution.unit import test_classifier_admission_governance as actual

base = actual.base
cluster = actual.cluster
no_actual_operations = actual.no_actual_operations


def real_journals(cluster):
    governed = cluster.make(actual.s.evidence.CASES[0])
    subjects = governed.prepare(deadline=time.monotonic() + 30)
    governed.restore(passed=True, deadline=time.monotonic() + 30)
    attempt = governed.attempts['team-a']
    files = {path.name: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
             for path in attempt.path.iterdir()}
    store = {ref['path']: Path(ref['path']).read_bytes() for ref in files.values()}
    authorization = {'governance_root': str(cluster.root), 'sources': cluster.pins}
    observation = {'governance': {
        'intent_sha256': files['intent.json']['sha256'],
        'release_sha256': files['release-matrix-release.json']['sha256'],
        'restoration_sha256': files['release-restore-original-binding.json']['sha256'],
        'completion_sha256': files['completion.json']['sha256']},
        'expected': {'binding_revision': subjects['team-a']['binding_revision'],
                     'policy': subjects['team-a']['policy'], 'tenant': 'team-a', 'stage': 'input',
                     'workload_id': subjects['team-a']['workload_id']}}
    return files, authorization, observation, attempt.operation, store


def verify(case, module=successor):
    files, authorization, observation, operation, store = case
    module.verify_governance(files, authorization=authorization, observation=observation,
                             operation=operation, load=lambda ref: store[ref['path']])


def test_real_policy_reference_round_trip_and_original_defect_reproduced(cluster):
    case = real_journals(cluster)
    handle = json.loads(case[-1][case[0]['release-matrix-release.json']['path']])['handle']
    parsed = models.PolicyRef.model_validate_json(models.canonical_json_bytes(handle['policy']))
    assert parsed.model_dump(mode='json') == handle['policy']
    assert set(handle['policy']) == {'tenant_id', 'policy_id', 'version', 'digest'}
    with pytest.raises(EvidenceError):
        verify(case, original)
    verify(case)


@pytest.mark.parametrize('fault', ['compiled_alias', 'extra_alias', 'wrong_digest', 'foreign_tenant',
    'wrong_operator', 'wrong_revision', 'wrong_restored_policy', 'completion_failed',
    'unknown_outcome', 'missing_returned', 'foreign_sources'])
def test_genuine_journal_correction_preserves_closed_authority(cluster, fault):
    files, auth, observed, operation, store = real_journals(cluster)
    name = 'release-matrix-release.json'
    if fault in {'completion_failed', 'unknown_outcome'}:
        name = 'completion.json'
    elif fault == 'wrong_restored_policy':
        name = 'release-restore-original-binding.json'
    elif fault == 'foreign_sources':
        name = 'intent.json'
    if fault == 'missing_returned':
        files.pop(next(key for key in files if key.endswith('-returned.json')))
    else:
        value = json.loads(store[files[name]['path']])
        if fault == 'compiled_alias':
            value['handle']['policy']['compiled_digest'] = value['handle']['policy'].pop('digest')
        elif fault == 'extra_alias':
            value['handle']['policy']['compiled_digest'] = value['handle']['policy']['digest']
        elif fault == 'wrong_digest':
            value['handle']['policy']['digest'] = '0' * 64
        elif fault == 'foreign_tenant':
            value['handle']['tenant_id'] = 'team-b'
        elif fault == 'wrong_operator':
            value['handle']['actor_id'] = '00000000-0000-0000-0000-000000000001'
        elif fault == 'wrong_revision':
            value['handle']['previous_revision'] += 1
        elif fault == 'wrong_restored_policy':
            value['handle']['policy']['digest'] = '0' * 64
        elif fault == 'completion_failed':
            value['status'] = 'case_failed'
        elif fault == 'unknown_outcome':
            value['last_request_outcome_unknown'] = True
        elif fault == 'foreign_sources':
            value['source_sha256'] = {'mvp/foreign.py': '0' * 64}
        raw = successor.canonical(value) + b'\n'
        store[files[name]['path']] = raw
        files[name]['sha256'] = hashlib.sha256(raw).hexdigest()
        key = {'intent.json': 'intent_sha256', 'completion.json': 'completion_sha256',
               'release-matrix-release.json': 'release_sha256',
               'release-restore-original-binding.json': 'restoration_sha256'}[name]
        observed['governance'][key] = files[name]['sha256']
    with pytest.raises(EvidenceError):
        verify((files, auth, copy.deepcopy(observed), operation, store))
