"""Bounded tenant-isolation observations, independent of rate/SLA qualification.

These new cases are deliberately unregistered. Consumption requires the parent
verifier and, for a fault, its separate immutable worker-transition authority.
The existing classifier provenance epoch invariant is never altered.
"""
from __future__ import annotations

import copy
import hashlib
import math
import re
from pathlib import PurePosixPath
from uuid import UUID

from guardrails.evolution.classifier_contract import canonical

SCHEMA = 'evolution-classifier-admission-v1'
CASES = ('P3-TENANT-SAME-IDENTIFIERS', 'P3-TENANT-ADMISSION-ISOLATION', 'P3-TENANT-CLASSIFIER-FAULT-RULES')
TENANTS = ('team-a', 'team-b')
OPERATIONAL = frozenset({'inspection_incomplete', 'queue_full', 'evaluator_unavailable', 'evaluation_timeout'})
PHASES = frozenset({'intent', 'governance', 'authorize', 'observe', 'restore', 'complete'})
FIELDS = {'schema_version', 'case_id', 'operation', 'status', 'phase', 'sources', 'authorization_sha256',
          'subjects', 'requests', 'observations', 'fault', 'cleanup', 'elapsed_ms'}
REQUEST_FIELDS = {'label', 'tenant_id', 'request_id', 'correlation_id', 'stage', 'context_id', 'body_sha256',
                  'status_code', 'decision', 'error_code', 'retry_after', 'started', 'finished', 'budget_ms'}
DECISION_FIELDS = {'decision_id', 'request_id', 'tenant_id', 'workload_id', 'context_id', 'stage', 'policy',
                   'binding_revision', 'action', 'reason_codes', 'evaluator_revisions', 'finding_rules', 'audit_status'}
SUBJECT_FIELDS = {'tenant_id', 'principal_id', 'workload_id', 'policy', 'binding_revision', 'quota_sha256',
                  'max_inflight', 'rule_id', 'evaluator_revision', 'budget_ms', 'provider_id'}
LEASE_FIELDS = {'tenant_id', 'principal_id', 'workload_id', 'request_id', 'policy_digest',
                'stage', 'lease_id', 'state', 'expires_epoch'}
PROCESS_FIELDS = {'pid', 'start_ticks', 'uid', 'command_sha256', 'cgroup_sha256', 'pid_namespace', 'state'}
TRANSITION_FIELDS = {'schema_version', 'operation', 'parent_invocation_sha256', 'preflight_sha256', 'owner_id', 'node_id',
    'pod_uid', 'container_id', 'selection_sha256', 'images_sha256', 'credentials_sha256', 'old_epoch', 'original',
    'supervisor_command_sha256', 'worker_command_sha256', 'worker_files', 'program_sha256', 'maximum_seconds'}


class AdmissionError(ValueError):
    """A finite code; never raw request, credential or driver text."""


def require(value, code='admission_invalid'):
    if not value:
        raise AdmissionError(code)


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def uuid(value):
    require(type(value) is str and len(value) == 36)
    try:
        require(str(UUID(value)) == value)
    except ValueError:
        raise AdmissionError('admission_invalid') from None


def number(value, minimum=0, maximum=2**53):
    require(type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum)
    return value


def policy(value, tenant):
    require(type(value) is dict and set(value) == {'tenant_id', 'policy_id', 'version', 'digest'})
    require(value['tenant_id'] == tenant and type(value['policy_id']) is str and 1 <= len(value['policy_id']) <= 128
            and type(value['version']) is int and value['version'] >= 1 and sha(value['digest']))


def subjects(value):
    require(type(value) is dict and set(value) == set(TENANTS))
    for tenant, row in value.items():
        require(type(row) is dict and set(row) == SUBJECT_FIELDS and row['tenant_id'] == tenant)
        uuid(row['principal_id']); uuid(row['provider_id']); policy(row['policy'], tenant)
        require(type(row['workload_id']) is str and 1 <= len(row['workload_id']) <= 64
                and type(row['binding_revision']) is int and row['binding_revision'] >= 1
                and type(row['max_inflight']) is int and 1 <= row['max_inflight'] <= 10000
                and sha(row['quota_sha256']) and type(row['rule_id']) is str and row['rule_id']
                and type(row['evaluator_revision']) is str and row['evaluator_revision']
                and row['budget_ms'] == (3000 if tenant == 'team-a' else 500))
    require(value['team-a']['principal_id'] != value['team-b']['principal_id'])


def request(row, subject):
    require(type(row) is dict and set(row) == REQUEST_FIELDS)
    require(row['tenant_id'] == subject['tenant_id'] and row['stage'] in {'input', 'output'}
            and type(row['label']) is str and re.fullmatch('[a-z][a-z0-9-]{0,31}', row['label'])
            and sha(row['body_sha256']) and row['budget_ms'] == subject['budget_ms'])
    uuid(row['request_id']); uuid(row['correlation_id'])
    if row['context_id'] is not None: uuid(row['context_id'])
    number(row['started']); number(row['finished'], row['started'])
    require(row['finished'] - row['started'] <= (4 if row['budget_ms'] == 3000 else 1.5))
    if row['status_code'] == 429:
        require(row['decision'] is None and row['error_code'] == 'quota_exceeded'
                and type(row['retry_after']) is int and 1 <= row['retry_after'] <= 60)
        return
    require(row['status_code'] == 200 and row['error_code'] is None and row['retry_after'] is None)
    d = row['decision']
    require(type(d) is dict and set(d) == DECISION_FIELDS)
    for key in ('tenant_id', 'workload_id', 'policy', 'binding_revision'):
        require(d[key] == subject[key])
    require(d['request_id'] == row['request_id'] and d['stage'] == row['stage']
            and d['audit_status'] == 'persisted' and d['action'] in {'allow', 'block'})
    uuid(d['decision_id']); uuid(d['context_id'])
    require(row['stage'] == 'input' or d['context_id'] == row['context_id'])
    for key in ('reason_codes', 'evaluator_revisions', 'finding_rules'):
        require(type(d[key]) is list and len(d[key]) <= 128
                and all(type(x) is str and 1 <= len(x) <= 128 for x in d[key]))
    require(subject['evaluator_revision'] in d['evaluator_revisions'])


def successful(row, subject):
    request(row, subject)
    d = row['decision']
    require(d is not None and d['action'] == 'allow' and not OPERATIONAL.intersection(d['reason_codes']))
    if subject['tenant_id'] == 'team-b':
        require(subject['rule_id'] in d['finding_rules'])


def isolation(observation, row, subject, *, state):
    """Exact committed scope projection; no owner token/hash or raw content."""
    require(type(observation) is dict and set(observation) == {'tenant_id', 'observed', 'context', 'lease', 'audit', 'foreign'})
    number(observation['observed']); require(observation['tenant_id'] == row['tenant_id'])
    d = row['decision']; require(d is not None)
    c, lease, audit = (observation[key] for key in ('context', 'lease', 'audit'))
    require(type(c) is dict and set(c) == {'context_id', 'tenant_id', 'principal_id', 'workload_id', 'request_id', 'state', 'policy'})
    require(c == {'context_id': d['context_id'], 'tenant_id': row['tenant_id'], 'principal_id': subject['principal_id'],
                  'workload_id': subject['workload_id'], 'request_id': row['request_id'], 'state': state, 'policy': subject['policy']})
    require(type(lease) is dict and set(lease) == {'tenant_id', 'principal_id', 'workload_id', 'request_id',
            'policy_digest', 'stage', 'lease_id', 'state', 'expires_epoch'})
    for key in ('tenant_id', 'principal_id', 'workload_id'): require(lease[key] == subject[key])
    require(lease['request_id'] == row['request_id'] and lease['policy_digest'] == subject['policy']['digest']
            and lease['stage'] == row['stage'] and lease['state'] in {'released', 'expired'})
    uuid(lease['lease_id']); number(lease['expires_epoch'])
    require(type(audit) is dict and set(audit) == {'event_id', 'tenant_id', 'actor_id', 'workload_id', 'correlation_id',
            'policy_digest', 'binding_revision', 'action', 'stage', 'event_hash', 'raw_content_retained'})
    require(audit == {'event_id': d['decision_id'], 'tenant_id': row['tenant_id'], 'actor_id': subject['principal_id'],
        'workload_id': subject['workload_id'], 'correlation_id': row['correlation_id'], 'policy_digest': subject['policy']['digest'],
        'binding_revision': subject['binding_revision'], 'action': d['action'], 'stage': row['stage'],
        'event_hash': audit['event_hash'], 'raw_content_retained': False} and sha(audit['event_hash']))
    require(observation['foreign'] == {'context': False, 'lease': False, 'audit': False})


def process(value):
    require(type(value) is dict and set(value) == PROCESS_FIELDS)
    require(type(value['pid']) is int and 1 <= value['pid'] <= 2**31-1
        and type(value['start_ticks']) is int and 1 <= value['start_ticks'] <= 2**63-1
        and type(value['uid']) is int and value['uid'] == 10001
        and all(sha(value[k]) for k in ('command_sha256', 'cgroup_sha256'))
        and type(value['pid_namespace']) is str and re.fullmatch(r'pid:\[[0-9]{1,20}\]', value['pid_namespace'])
        and value['state'] in {'R', 'S', 'T', 'D', 'Z', 'X'})
    return {k: value[k] for k in PROCESS_FIELDS - {'state'}}


def transition_authority(value, operation):
    require(type(value) is dict and set(value) == TRANSITION_FIELDS
        and value['schema_version'] == 'evolution-classifier-worker-transition-v1' and value['operation'] == operation)
    uuid(operation); uuid(value['pod_uid'])
    require(type(value['owner_id']) is str and 1 <= len(value['owner_id']) <= 128
        and value['maximum_seconds'] == 180 and type(value['maximum_seconds']) is int
        and all(sha(value[k]) for k in ('parent_invocation_sha256', 'preflight_sha256', 'node_id', 'container_id',
            'selection_sha256', 'images_sha256', 'credentials_sha256', 'supervisor_command_sha256',
            'worker_command_sha256', 'program_sha256'))
        and type(value['old_epoch']) is str and re.fullmatch('[0-9a-f]{32}', value['old_epoch']))
    original = value['original']
    require(type(original) is dict and set(original) == {'supervisor', 'child'})
    for row in original.values(): process(row); require(row['state'] in {'R', 'S'})
    require(original['supervisor']['pid'] == 1 and original['child']['pid'] != 1
        and original['supervisor']['command_sha256'] == value['supervisor_command_sha256']
        and original['child']['command_sha256'] == value['worker_command_sha256']
        and all(original['supervisor'][k] == original['child'][k] for k in ('uid', 'pid_namespace', 'cgroup_sha256')))
    require(type(value['worker_files']) is dict and 1 <= len(value['worker_files']) <= 32)
    for name, hashed in value['worker_files'].items():
        require(type(name) is str and name.startswith('/app/') and name.endswith('.py')
            and PurePosixPath(name).as_posix() == name and '..' not in PurePosixPath(name).parts and sha(hashed))
    return value


def transition_ready(row, authority, *, old):
    require(type(row) is dict and set(row) == {'status', 'epoch', 'selection_sha256', 'images_sha256', 'credentials_sha256'})
    require(row['status'] == 'ready' and type(row['epoch']) is str and re.fullmatch('[0-9a-f]{32}', row['epoch'])
        and (row['epoch'] == authority['old_epoch']) is old)
    for key in ('selection_sha256', 'images_sha256', 'credentials_sha256'): require(row[key] == authority[key])


def transition(value, *, operation, first, peer):
    """Dedicated old→new epoch proof; never changes the common current checker."""
    require(type(value) is dict and set(value) == {'status', 'authorization', 'events', 'cleanup'}
        and value['status'] == 'recovered' and value['cleanup'] == 'clear')
    auth = transition_authority(value['authorization'], operation)
    events = value['events']
    require(type(events) is list and len(events) == 6
        and [e['kind'] for e in events] == ['intent', 'stopped', 'held', 'replacement', 'recovered', 'cleanup'])
    previous = 0
    for event in events:
        require(type(event) is dict and set(event) == {'kind', 'observed', 'value'})
        previous = number(event['observed'], previous)
    require(events[-1]['observed'] - events[0]['observed'] <= 180)
    original = auth['original']; intent = events[0]['value']
    require(type(intent) is dict and set(intent) == {'original', 'ready'} and intent['original'] == original)
    transition_ready(intent['ready'], auth, old=True)
    stopped = events[1]['value']; held = events[2]['value']
    require(type(stopped) is dict and set(stopped) == {'status', 'supervisor', 'child'} and stopped['status'] == 'stopped')
    require(type(held) is dict and set(held) == {'status', 'old_terminated', 'old', 'supervisor', 'child'}
        and held['status'] == 'observed' and held['old_terminated'] is False)
    for observed in (stopped, held):
        require(process(observed['supervisor']) == process(original['supervisor'])
            and process(observed['child']) == process(original['child']) and observed['child']['state'] == 'T')
    require(held['old'] == held['child'])
    require(events[1]['observed'] <= first['started'] and peer['finished'] <= events[2]['observed'] <= first['finished'])
    replacement = events[3]['value']
    require(type(replacement) is dict and set(replacement) == {'process', 'ready'})
    observed = replacement['process']
    require(type(observed) is dict and set(observed) == {'status', 'old_terminated', 'old', 'supervisor', 'child'}
        and observed['status'] == 'observed' and observed['old_terminated'] is True)
    require(process(observed['supervisor']) == process(original['supervisor']))
    process(observed['child'])
    require(observed['child']['state'] in {'R', 'S'} and
        (observed['child']['pid'], observed['child']['start_ticks']) != (original['child']['pid'], original['child']['start_ticks']))
    for key in ('uid', 'command_sha256', 'cgroup_sha256', 'pid_namespace'):
        require(observed['child'][key] == original['child'][key])
    if observed['old'] is not None:
        process(observed['old'])
        require(observed['old']['pid'] == original['child']['pid'] and
            (observed['old']['start_ticks'] != original['child']['start_ticks'] or observed['old']['state'] in {'Z', 'X'}))
    transition_ready(replacement['ready'], auth, old=False)
    require(events[3]['observed'] >= first['finished'] and events[4]['value'] == {})
    require(events[5]['value'] in ({'status': 'gone', 'signalled': False},
        {'status': 'pid_reused_not_signalled', 'signalled': False}))


def validate(value, *, verify_provenance, complete=True):
    require(callable(verify_provenance), 'admission_verifier_required')
    require(type(value) is dict and set(value) == FIELDS and value['schema_version'] == SCHEMA and value['case_id'] in CASES)
    require(type(complete) is bool)
    uuid(value['operation']); require(value['phase'] in PHASES and type(value['sources']) is dict and value['sources'])
    require(len(value['sources']) <= 8192 and all(type(k) is str and 0 < len(k) <= 256
        and not PurePosixPath(k).is_absolute() and PurePosixPath(k).as_posix() == k
        and all(part not in {'', '.', '..'} for part in k.split('/')) and sha(v) for k, v in value['sources'].items()))
    number(value['elapsed_ms'], 0, 180000 if value['case_id'] == CASES[2] else 30000)
    require(type(value['requests']) is list and len(value['requests']) <= 4 and type(value['observations']) is dict)
    if not complete:
        require(value['status'] == 'failed'); return copy.deepcopy(value)
    require(value['status'] == 'observed' and value['phase'] == 'complete' and sha(value['authorization_sha256'])
            and value['cleanup'] == {'requests': 'joined', 'governance': 'restored', 'fault': 'clear'})
    subjects(value['subjects']); rows = value['requests']; require(len({r['label'] for r in rows}) == len(rows))
    if value['case_id'] == CASES[0]:
        require(len(rows) == 4 and value['fault'] is None and set(value['observations']) == {r['label'] for r in rows})
        for stage in ('input', 'output'):
            pair = [r for r in rows if r['stage'] == stage]
            require(len(pair) == 2 and {r['tenant_id'] for r in pair} == set(TENANTS)
                    and len({r['request_id'] for r in pair}) == 1
                    and max(r['started'] for r in pair) < min(r['finished'] for r in pair))
        require(len({r['request_id'] for r in rows}) == 2 and len({r['correlation_id'] for r in rows}) == 1)
        require(len({r['decision']['context_id'] for r in rows}) == 2)
        for tenant in TENANTS:
            require(len({r['decision']['context_id'] for r in rows if r['tenant_id'] == tenant}) == 1)
        for row in rows:
            successful(row, value['subjects'][row['tenant_id']])
            isolation(value['observations'][row['label']], row, value['subjects'][row['tenant_id']],
                      state='open' if row['stage'] == 'input' else 'completed')
    else:
        require(len(rows) == 3 and [r['label'] for r in rows] == ['a-original', 'a-overflow', 'b-peer'])
        first, overflow, peer = rows
        require([r['tenant_id'] for r in rows] == ['team-a', 'team-a', 'team-b']
                and all(r['stage'] == 'input' for r in rows) and value['subjects']['team-a']['max_inflight'] == 1)
        request(overflow, value['subjects']['team-a']); require(overflow['status_code'] == 429)
        successful(peer, value['subjects']['team-b'])
        active = value['observations']['active']
        require(type(active) is dict and set(active) == {'observed', 'lease', 'database_epoch'})
        number(active['observed']); number(active['database_epoch'])
        lease = active['lease']
        require(type(lease) is dict and set(lease) == LEASE_FIELDS)
        uuid(lease['lease_id']); number(lease['expires_epoch'])
        require(lease['state'] == 'active' and lease['request_id'] == first['request_id']
                and lease['tenant_id'] == 'team-a' and lease['principal_id'] == value['subjects']['team-a']['principal_id']
                and lease['workload_id'] == value['subjects']['team-a']['workload_id']
                and lease['stage'] == 'input' and lease['policy_digest'] == value['subjects']['team-a']['policy']['digest']
                and lease['expires_epoch'] > active['database_epoch'])
        require(first['started'] <= active['observed'] <= min(overflow['started'], peer['started'])
                and max(overflow['started'], peer['started']) < first['finished'])
        after = value['observations']['active_after_peer']
        require(type(after) is dict and set(after) == {'observed', 'lease', 'database_epoch'})
        number(after['observed']); number(after['database_epoch'])
        require(after['lease'] == lease and lease['expires_epoch'] > after['database_epoch']
            and max(peer['finished'], overflow['finished']) <= after['observed'] <= first['finished'])
        if value['case_id'] == CASES[1]:
            successful(first, value['subjects']['team-a']); require(value['fault'] is None)
        else:
            request(first, value['subjects']['team-a'])
            require(first['decision']['action'] == 'block' and 'evaluation_timeout' in first['decision']['reason_codes'])
            transition(value['fault'], operation=value['operation'], first=first, peer=peer)
        require(set(value['observations']) == {'active', 'active_after_peer', 'a-original', 'b-peer', 'overflow_absent'})
        require(value['observations']['overflow_absent'] == {'context_count': 0, 'lease': False, 'audit_count': 0})
        for row in (first, peer):
            isolation(value['observations'][row['label']], row, value['subjects'][row['tenant_id']],
                      state='blocked' if row['decision']['action'] == 'block' else 'open')
    before = canonical(value)
    # Required parent closure owns source freshness and final transition semantics.
    accepted = verify_provenance(copy.deepcopy(value))
    require(accepted is True and canonical(value) == before, 'admission_provenance_refused')
    return copy.deepcopy(value)
