"""Separate admission authority over the shared deployed-worker prerequisite.

The prerequisite authenticates corpus, final worker, cold recovery, credentials
and the original preflight. It grants no admission operation by itself. This
additional independently pinned capsule names one original admission operation,
its outputs, finite deadline and (only for the fault case) transition authority.
The common provenance verifier and its unchanged epoch predicate are never
patched. A separately authenticated transition permits exactly one new epoch.
"""
from __future__ import annotations

import copy
from datetime import timedelta
from pathlib import PurePosixPath

from . import classifier_admission_measurements as evidence
from . import classifier_provenance_v2 as proof

SCHEMA = 'evolution-classifier-admission-authorization-v1'
CHECK_SCHEMA = 'evolution-classifier-admission-current-v1'
PHASES = ('before', 'authorized', 'after', 'restored')
CURRENT_PHASE = {'before': 'before', 'authorized': 'before', 'after': 'after',
                 'restored': 'restored', 'consume': 'consume'}
FIELDS = {'schema_version', 'created_at', 'expires_at', 'case_id', 'operation',
          'prerequisite', 'sources', 'registry_sha256', 'source_root', 'deadline_monotonic',
          'output_directory', 'checks_directory', 'governance_root', 'transition'}


def validate_authorization(value, *, reference, context, prerequisite, expected, now):
    proof.exact(value, FIELDS)
    proof.reference(reference)
    proof.require(value['schema_version'] == SCHEMA and value['case_id'] in evidence.CASES
        and value['prerequisite'] == prerequisite and value['case_id'] == expected['case_id']
        and value['operation'] == expected['operation'] and value['sources'] == expected['sources']
        and value['registry_sha256'] == expected['registry_sha256']
        and value['deadline_monotonic'] == expected['deadline_monotonic']
        and value['source_root'] == expected['source_root'])
    proof.uuid(value['operation']); proof.source_map(value['sources']); proof.sha(value['registry_sha256'])
    proof.number(value['deadline_monotonic'], .001, 1e15)
    created, expires = proof.timestamp(value['created_at']), proof.timestamp(value['expires_at'])
    proof.require(created <= now < expires and expires - created <= timedelta(hours=24))
    for key in ('source_root', 'output_directory', 'checks_directory', 'governance_root'):
        proof.path(value[key])
    root = PurePosixPath(reference['path']).parent
    proof.require(PurePosixPath(reference['path']).name == 'admission-authorization.json'
        and root.name == value['operation']
        and value['output_directory'] == str(root / 'measurements')
        and value['checks_directory'] == str(root / 'checks'))
    common = context['authorization']
    proof.require(common['sources'] == value['sources']
        and common['registry_sha256'] == value['registry_sha256']
        and common['governance_root'] == value['governance_root']
        and created >= proof.timestamp(common['created_at'])
        and expires <= proof.timestamp(common['expires_at']))
    if value['case_id'] == evidence.CASES[2]:
        proof.reference(value['transition'])
    else:
        proof.require(value['transition'] is None)
    return value


def transition_binding(value, *, authorization, context, prerequisite):
    evidence.transition_authority(value, authorization['operation'])
    common, preflight = context['authorization'], context['preflight']
    expected, binding = preflight['expected'], preflight['binding']
    pod = binding['pods']['classifier']
    proof.require(value['parent_invocation_sha256'] == prerequisite['sha256']
        and value['preflight_sha256'] == common['preflight']['sha256']
        and value['owner_id'] == expected['owner_id'] and value['node_id'] == expected['node_id']
        and value['pod_uid'] == pod['pod_uid']
        and 'containerd://' + value['container_id'] == pod['container_id']
        and value['selection_sha256'] == proof.digest(common['identity'])
        and value['images_sha256'] == proof.digest(preflight['images'])
        and value['credentials_sha256'] == common['receipts']['credentials']['sha256']
        and value['old_epoch'] == expected['worker_epoch']
        and authorization['sources']['mvp/tests/evolution/e2e/classifier_worker_transition.py'] == value['program_sha256'])
    return value


def replacement_prefix(value, authority):
    """Authenticate the original four events before recovery is acknowledged."""
    proof.exact(value, {'status', 'authorization', 'events', 'cleanup'})
    proof.require(value['status'] == 'stopped' and value['authorization'] == authority
        and value['cleanup'] == 'pending' and type(value['events']) is list and len(value['events']) == 4)
    events = value['events']; previous = 0
    for name, event in zip(('intent', 'stopped', 'held', 'replacement'), events, strict=True):
        proof.exact(event, {'kind', 'observed', 'value'}); proof.require(event['kind'] == name)
        previous = evidence.number(event['observed'], previous)
    proof.require(events[-1]['observed'] - events[0]['observed'] <= 180)
    original = authority['original']; intent = events[0]['value']
    proof.exact(intent, {'original', 'ready'}); proof.require(intent['original'] == original)
    evidence.transition_ready(intent['ready'], authority, old=True)
    stopped, held = events[1]['value'], events[2]['value']
    proof.exact(stopped, {'status', 'supervisor', 'child'})
    proof.exact(held, {'status', 'old_terminated', 'old', 'supervisor', 'child'})
    proof.require(stopped['status'] == 'stopped' and held['status'] == 'observed'
        and held['old_terminated'] is False and held['old'] == held['child'])
    for row in (stopped, held):
        proof.require(evidence.process(row['supervisor']) == evidence.process(original['supervisor'])
            and evidence.process(row['child']) == evidence.process(original['child']) and row['child']['state'] == 'T')
    replacement = events[3]['value']; proof.exact(replacement, {'process', 'ready'})
    row = replacement['process']; proof.exact(row, {'status', 'old_terminated', 'old', 'supervisor', 'child'})
    proof.require(row['status'] == 'observed' and row['old_terminated'] is True
        and evidence.process(row['supervisor']) == evidence.process(original['supervisor']))
    evidence.process(row['child'])
    proof.require(row['child']['state'] in {'R', 'S'}
        and (row['child']['pid'], row['child']['start_ticks']) != (original['child']['pid'], original['child']['start_ticks'])
        and all(row['child'][key] == original['child'][key]
                for key in ('uid', 'command_sha256', 'cgroup_sha256', 'pid_namespace')))
    if row['old'] is not None:
        evidence.process(row['old'])
        proof.require(row['old']['pid'] == original['child']['pid']
            and (row['old']['start_ticks'] != original['child']['start_ticks'] or row['old']['state'] in {'Z', 'X'}))
    evidence.transition_ready(replacement['ready'], authority, old=False)
    return replacement['ready']['epoch']


class AdmissionAuthority:
    """Stable original references; no default final-worker or current adapter."""
    def __init__(self, *, reference, prerequisite, expected, current):
        proof.require(isinstance(prerequisite, proof.ProvenanceVerifier) and callable(current))
        proof.exact(expected, {'case_id', 'operation', 'sources', 'registry_sha256', 'source_root', 'deadline_monotonic'})
        self.reference = copy.deepcopy(reference)
        self.prerequisite = prerequisite
        self.expected = copy.deepcopy(expected)
        self.current = current
        self.last_context = None

    def authorize(self):
        context = self.prerequisite.authorize()
        value = proof.document(self.prerequisite.load(self.reference))
        validate_authorization(value, reference=self.reference, context=context,
            prerequisite=self.prerequisite.authorization_ref, expected=self.expected,
            now=proof.now_utc(self.prerequisite.now()))
        transition = None
        if value['transition'] is not None:
            transition = transition_binding(proof.document(self.prerequisite.load(value['transition'])),
                authorization=value, context=context, prerequisite=self.prerequisite.authorization_ref)
        self.prerequisite.stable()
        self.last_context = copy.deepcopy({'authorization': value, 'shared': context, 'transition': transition})
        return copy.deepcopy(self.last_context)

    def authorize_transition(self, authority, preflight, *, deadline):
        context = self.authorize()
        proof.require(deadline <= self.expected['deadline_monotonic'] and context['transition'] is not None
            and authority == context['transition'] and preflight == context['shared']['preflight'])
        return True

    def verify_replacement(self, value, *, deadline):
        context = self.authorize()
        proof.require(deadline <= self.expected['deadline_monotonic'] and context['transition'] is not None)
        replacement_prefix(value, context['transition'])
        return True

    def check(self, *, phase, case_id, operation, measurement, governance, sources,
              authorization_sha256, deadline):
        proof.require(phase in CURRENT_PHASE and deadline <= self.expected['deadline_monotonic'])
        context = self.authorize(); authorization = context['authorization']
        proof.require(case_id == authorization['case_id'] and operation == authorization['operation']
            and sources == authorization['sources'] and authorization_sha256 == self.reference['sha256'])
        preflight = context['shared']['preflight']; expected = copy.deepcopy(preflight['expected'])
        transition = None
        if phase in {'before', 'authorized'}:
            proof.require(measurement is None and governance == {})
        else:
            proof.require(type(measurement) is dict and measurement['case_id'] == case_id
                and measurement['operation'] == operation and measurement['sources'] == sources
                and measurement['authorization_sha256'] == authorization_sha256)
            if context['transition'] is not None:
                transition = measurement['fault']
                proof.require(transition['authorization'] == context['transition'])
                rows = measurement['requests']
                proof.require(len(rows) == 3 and rows[0]['label'] == 'a-original' and rows[2]['label'] == 'b-peer')
                evidence.transition(transition, operation=operation, first=rows[0], peer=rows[2])
                expected['worker_epoch'] = transition['events'][3]['value']['ready']['epoch']
            else:
                proof.require(measurement['fault'] is None)
        current = self.current(phase=CURRENT_PHASE[phase], operation=operation, deadline=deadline,
                               context=copy.deepcopy(context), transition=copy.deepcopy(transition))
        proof.verify_current(expected, current,
            preflight_sha256=context['shared']['authorization']['preflight']['sha256'],
            phase=CURRENT_PHASE[phase], operation=operation, now=proof.now_utc(self.prerequisite.now()))
        self.prerequisite.stable()
        return {'schema_version': CHECK_SCHEMA, 'authorization': copy.deepcopy(self.reference),
                'phase': phase, 'operation': operation, 'current': copy.deepcopy(current),
                'transition_sha256': proof.digest(transition) if transition is not None else None,
                'measurement_sha256': proof.digest(measurement) if measurement is not None else None,
                'governance_sha256': proof.digest(governance),
                'measurement': copy.deepcopy(measurement), 'governance': copy.deepcopy(governance)}
