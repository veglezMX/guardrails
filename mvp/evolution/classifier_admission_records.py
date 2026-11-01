"""Exclusive admission records and independently anchored final consumption.

The worker recorder cannot create completion authority. The parent supplies an
independently pinned completion only after its original process exits. All files
remain under the original private operation and original whole-run budget.
"""
from __future__ import annotations

import base64
import copy
import re
from pathlib import PurePosixPath
from uuid import UUID

from . import classifier_admission_authority as authority
from . import classifier_provenance_io_v2 as io
from . import classifier_provenance_v2 as proof

COMPLETION_SCHEMA = 'evolution-classifier-admission-completion-v1'
COMPLETION_FIELDS = {'schema_version', 'authorization', 'case_id', 'operation',
    'started_at', 'completed_at', 'pid', 'process_start', 'exit_code', 'elapsed_ms',
    'worker_result', 'observation', 'current_checks', 'governance_files', 'native_attempts'}


class AdmissionRecorder:
    def __init__(self, verifier, output, checks):
        proof.require(isinstance(verifier, authority.AdmissionAuthority)
            and isinstance(output, io.PrivateDirectory) and isinstance(checks, io.PrivateDirectory)
            and output.budget is checks.budget
            and isinstance(verifier.prerequisite._load, io.StableReader)
            and verifier.prerequisite._load.budget is output.budget)
        context = verifier.authorize(); authorized = context['authorization']
        proof.require(str(output.path) == authorized['output_directory']
            and str(checks.path) == authorized['checks_directory']
            and output.budget.deadline == authorized['deadline_monotonic'])
        self.verifier, self.output, self.checks = verifier, output, checks
        self.references, self.current_checks = {}, []
        self.failed = False

    def record(self, name, value):
        proof.require(not self.failed and type(name) is str and name not in self.references
            and len(self.references) < 64 and re.fullmatch(
                r'(?:admission(?:-case)?-[0-9]{2}|fault-[0-9]{2}-(?:intent|stopped|held|replacement|recovered|cleanup))\.json', name))
        proof.require(type(value) is dict)
        operation = value.get('operation')
        if name.startswith('fault-'):
            operation = value.get('authorization', {}).get('operation')
        proof.require(operation == self.verifier.expected['operation'])
        try:
            reference = self.output.document(name, copy.deepcopy(value))
        except BaseException:  # an uncertain write never permits replay or success
            self.failed = True
            raise
        self.references[name] = reference
        return copy.deepcopy(reference)

    def verify(self, **kwargs):
        proof.require(not self.failed)
        index = len(self.current_checks)
        proof.require(index < len(authority.PHASES) and kwargs.get('phase') == authority.PHASES[index])
        with self.checks.budget.cap(kwargs.get('deadline')):
            checked = self.verifier.check(**kwargs)
            try:
                reference = self.checks.document(f'{index + 1:02d}-{kwargs["phase"]}.json', checked)
            except BaseException:  # a current check is unusable until retained
                self.failed = True
                raise
        self.current_checks.append(reference)
        return True


def validate_completion(value, *, reference, authorized, now):
    proof.exact(value, COMPLETION_FIELDS)
    proof.require(value['schema_version'] == COMPLETION_SCHEMA and value['authorization'] == reference
        and value['case_id'] == authorized['case_id'] and value['operation'] == authorized['operation']
        and type(value['exit_code']) is int and value['exit_code'] == 0)
    proof.integer(value['pid'], 1, 2**31 - 1); proof.integer(value['process_start'], 1, 2**63 - 1)
    proof.number(value['elapsed_ms'], 0, 900000)
    proof.require(proof.timestamp(authorized['created_at']) <= proof.timestamp(value['started_at'])
        <= proof.timestamp(value['completed_at']) <= now < proof.timestamp(authorized['expires_at']))
    result = proof.exact(value['worker_result'], {'case_id', 'status', 'assertions', 'reason_codes'})
    proof.require(result['case_id'] == authorized['case_id'] and result['status'] == 'pass'
        and type(result['assertions']) is int and result['assertions'] >= 1 and result['reason_codes'] == [])
    proof.reference(value['observation'])
    proof.require(value['observation']['path'] == str(PurePosixPath(authorized['output_directory']) / 'admission-final.json'))
    proof.require(type(value['current_checks']) is list and len(value['current_checks']) == len(authority.PHASES))
    for index, (phase, ref) in enumerate(zip(authority.PHASES, value['current_checks'], strict=True)):
        proof.reference(ref)
        proof.require(ref['path'] == str(PurePosixPath(authorized['checks_directory']) / f'{index + 1:02d}-{phase}.json'))
    proof.exact(value['governance_files'], set(authority.evidence.TENANTS))
    proof.exact(value['native_attempts'], {'ordinary', 'cleanup'})
    for kind, ref in value['native_attempts'].items():
        proof.reference(ref)
        name = 'native-attempts' if kind == 'ordinary' else 'cleanup-native-attempts'
        proof.require(ref['path'] == str(PurePosixPath(authorized['checks_directory']) / name / 'index.json'))
    return value


class AdmissionConsumer:
    """Final parent adapter; completion cannot come from a worker callback."""
    def __init__(self, verifier, completion_ref):
        proof.require(isinstance(verifier, authority.AdmissionAuthority))
        proof.reference(completion_ref)
        self.verifier, self.completion_ref = verifier, copy.deepcopy(completion_ref)

    def __call__(self, **kwargs):
        proof.require(kwargs.get('phase') == 'consume')
        context = self.verifier.authorize(); authorized = context['authorization']
        load = self.verifier.prerequisite.load
        completion = validate_completion(proof.document(load(self.completion_ref)), reference=self.verifier.reference,
            authorized=authorized, now=proof.now_utc(self.verifier.prerequisite.now()))
        retained = proof.document(load(completion['observation']))
        from mvp.tests.evolution.e2e.classifier_admission_case import closed
        closed(retained)
        proof.require(retained['measurement'] == kwargs['measurement'] and retained['governance'] == kwargs['governance']
            and retained['authorization_sha256'] == self.verifier.reference['sha256'])
        previous = proof.timestamp(completion['started_at'])
        for phase, ref in zip(authority.PHASES, completion['current_checks'], strict=True):
            row = proof.document(load(ref))
            proof.exact(row, {'schema_version', 'authorization', 'phase', 'operation', 'current',
                             'transition_sha256', 'measurement_sha256', 'governance_sha256', 'measurement', 'governance'})
            proof.require(row['schema_version'] == authority.CHECK_SCHEMA
                and row['authorization'] == self.verifier.reference and row['phase'] == phase
                and row['operation'] == authorized['operation'])
            proof.require(row['governance_sha256'] == proof.digest(row['governance'])
                and row['measurement_sha256'] == (None if row['measurement'] is None else proof.digest(row['measurement'])))
            if phase in {'before', 'authorized'}:
                proof.require(row['measurement'] is None and row['governance'] == {})
            elif phase == 'restored':
                proof.require(row['measurement'] == kwargs['measurement'] and row['governance'] == kwargs['governance'])
            else:
                proof.require(type(row['measurement']) is dict and row['governance'] == {})
                stable = authority.evidence.FIELDS - {'status', 'phase', 'cleanup', 'elapsed_ms'}
                proof.require({key: row['measurement'][key] for key in stable}
                    == {key: kwargs['measurement'][key] for key in stable})
            now = proof.timestamp(row['current']['observed_at'])
            proof.require(previous <= now <= proof.timestamp(completion['completed_at'])); previous = now
            expected = copy.deepcopy(context['shared']['preflight']['expected'])
            if phase in {'after', 'restored'} and context['transition'] is not None:
                transition = kwargs['measurement']['fault']
                proof.require(row['transition_sha256'] == proof.digest(transition))
                expected['worker_epoch'] = transition['events'][3]['value']['ready']['epoch']
            else:
                proof.require(row['transition_sha256'] is None)
            proof.verify_current(expected, row['current'],
                preflight_sha256=context['shared']['authorization']['preflight']['sha256'],
                phase=authority.CURRENT_PHASE[phase], operation=authorized['operation'], now=now)
        arguments = copy.deepcopy({'completion': completion, 'authorization': authorized, 'observation': retained})
        before = proof.digest(arguments)
        proof.require(verify_governance(**arguments, load=load) is True
            and verify_native(**arguments, load=load) is True and proof.digest(arguments) == before)
        def current(measurement):
            proof.require(measurement == kwargs['measurement'])
            checked = self.verifier.check(**kwargs)
            self.verifier.prerequisite.stable()
            proof.require(checked['phase'] == 'consume')
            return True
        # Direct use of this consumer must retain the same measurement semantics
        # as the outer case consumer; the callback is the actual current proof.
        authority.evidence.validate(kwargs['measurement'], verify_provenance=current)
        return True


def verify_governance(*, completion, authorization, observation, load):
    """Both actual original role-separated journals, including owned restore."""
    proof.require(observation['case_id'] == authorization['case_id']
        and observation['operation'] == authorization['operation'])
    measurement = observation['measurement']
    authority.evidence.subjects(measurement['subjects'])
    for tenant in authority.evidence.TENANTS:
        files = completion['governance_files'][tenant]
        receipt = observation['governance'][tenant]
        proof.require(all(files.get(name) == ref for name, ref in receipt['references'].items())
            and receipt['restoration'] == 'applied')
        subject = measurement['subjects'][tenant]
        expected = {'binding_revision': subject['binding_revision'], 'policy': subject['policy'],
            'tenant': tenant, 'stage': 'input', 'workload_id': subject['workload_id']}
        view = {'expected': expected, 'governance': {
            'intent_sha256': files['intent.json']['sha256'],
            'release_sha256': files['release-matrix-release.json']['sha256'],
            'restoration_sha256': files['release-restore-original-binding.json']['sha256'],
            'completion_sha256': files['completion.json']['sha256']}}
        governance_one(files, authorization=authorization, observation=view,
            operation=receipt['operation'], load=load, tenant=tenant)
        intent = proof.document(load(files['intent.json']))
        proof.require(receipt['source_sha256'] == intent['source_sha256'])
        audit = receipt['quota_audit']
        if tenant == 'team-a' and authorization['case_id'] != authority.evidence.CASES[0]:
            proof.require(audit['actor_id'] == intent['actors']['tenant_admin']
                and audit['tenant_id'] == tenant and subject['max_inflight'] == 1)
        else:
            proof.require(audit is None)
    return True


def verify_native(*, completion, authorization, observation, load):
    """Authenticate complete private native attempts without exposing contents."""
    from mvp.tests.evolution.e2e import classifier_native_readonly as native

    fault = authorization['case_id'] == authority.evidence.CASES[2]
    proof.require((observation['measurement']['fault'] is not None) is fault)
    for kind, index_ref in completion['native_attempts'].items():
        index = proof.document(load(index_ref))
        proof.exact(index, {'schema_version', 'authorization', 'outcome', 'attempts', 'raw_bytes', 'operational_success_claim'})
        proof.require(index['schema_version'] == 'evolution-classifier-private-native-attempts-v1'
            and index['authorization'] == completion['authorization']
            and index['outcome'] == 'worker_returned' and index['operational_success_claim'] is False
            and type(index['attempts']) is list and len(index['attempts']) <= native.MAX_ATTEMPTS)
        proof.require(bool(index['attempts']) if kind == 'ordinary' or fault else index['attempts'] == [])
        total = 0
        for position, ref in enumerate(index['attempts'], 1):
            proof.reference(ref)
            proof.require(ref['path'] == str(PurePosixPath(index_ref['path']).parent / f'{position:03d}.json'))
            row = proof.document(load(ref))
            proof.exact(row, {'spawned', 'stdout_base64', 'stderr_base64', 'truncated', 'cleanup', 'status'})
            proof.require(row['spawned'] is True and row['truncated'] is False
                and row['cleanup'] == 'completed' and row['status'] == 'completed')
            size = 0
            for key in ('stdout_base64', 'stderr_base64'):
                proof.require(type(row[key]) is str)
                try:
                    raw = base64.b64decode(row[key], validate=True)
                except ValueError:
                    proof.require(False, 'admission_native_encoding')
                proof.require(base64.b64encode(raw).decode('ascii') == row[key])
                size += len(raw)
            proof.require(size <= native.MAX_OUTPUT); total += size
            proof.require(total <= native.MAX_RETAINED)
        proof.require(type(index['raw_bytes']) is int and index['raw_bytes'] == total)
    return True


def governance_one(files, *, authorization, observation, operation, load, tenant):
    proof.require(tenant in authority.evidence.TENANTS)
    proof.uuid(operation)
    proof.require(authorization["governance_root"] is not None and type(files) is dict)
    fixed = {"intent.json", "completion.json", "release-matrix-release.json", "release-restore-original-binding.json"}
    proof.require(fixed <= set(files))
    extra = set(files) - fixed
    proof.require(len(extra) % 2 == 0 and 2 <= len(extra) <= 32 and extra == {
        f"request-{index:02d}-{suffix}.json" for index in range(1, len(extra) // 2 + 1) for suffix in ("intent", "returned")})
    rows = {}
    for name, ref in files.items():
        proof.reference(ref); proof.require(ref["path"] == str(PurePosixPath(authorization["governance_root"]) / operation / name))
        rows[name] = proof.document(load(ref))
    intent, done = rows["intent.json"], rows["completion.json"]
    proof.require(intent.get("schema_version") == "evolution-governance-attempt-v1" and intent.get("operation") == operation
            and intent.get("status") == "intent_only_no_mutation_claim" and intent.get("engine") == ("injection" if tenant == "team-a" else "secret")
            and intent.get("state") == "observe" and intent.get("stage") in {"input", "output"}
            and intent.get("tenant_id") == tenant)
    proof.require(done == {"operation": operation, "status": "case_passed", "restoration": "applied", "last_request_outcome_unknown": False})
    proof.require(type(intent.get("source_sha256")) is dict and bool(intent["source_sha256"])
            and all(authorization["sources"].get(name) == hashed for name, hashed in intent["source_sha256"].items()))
    handles = []
    for label in ("matrix-release", "restore-original-binding"):
        row = proof.exact(rows["release-" + label + ".json"], {"schema_version", "status", "handle"})
        proof.require(row["schema_version"] == "evolution-governance-release-handle-v1" and row["status"] == "acknowledged_not_yet_applied")
        handle = proof.exact(row["handle"], {"operation", "label", "release_id", "tenant_id", "workload_id", "policy", "binding_revision", "previous_revision", "state", "actor_id"})
        proof.uuid(handle["release_id"])
        proof.require(handle["operation"] == operation and handle["label"] == label and handle["tenant_id"] == intent["tenant_id"]
                and handle["actor_id"] == intent["actors"]["operator"] and handle["workload_id"] == intent["original_binding"]["workload_id"])
        proof.integer(handle["previous_revision"], 1, 2**63 - 2)
        proof.require(type(handle["binding_revision"]) is int and handle["binding_revision"] == handle["previous_revision"] + 1)
        handles.append(handle)
    proof.require(handles[0]["previous_revision"] == intent["original_binding"]["revision"]
            and handles[1]["previous_revision"] == handles[0]["binding_revision"]
            and handles[1]["policy"] == intent["original_policy"] and handles[1]["state"] == intent["original_binding"]["state"])
    policy = proof.exact(handles[0]["policy"], {"tenant_id", "policy_id", "version", "digest"})
    proof.require(policy["tenant_id"] == intent["tenant_id"] and policy["policy_id"] == "eval-" + UUID(operation).hex
            and type(policy["version"]) is int and policy["version"] == 1 and handles[0]["state"] == "observe")
    proof.sha(policy["digest"])
    for index in range(1, len(extra) // 2 + 1):
        before, after = (rows[f"request-{index:02d}-{suffix}.json"] for suffix in ("intent", "returned"))
        proof.require(before.get("operation") == after.get("operation") == operation
                and before.get("status") == "request_intended_outcome_unknown"
                and after.get("status") == "port_returned_expected_status_not_application_proof")
        proof.sha(before.get("body_sha256")); proof.sha(after.get("response_sha256"))
    proof.require(observation.get("governance") == {
        "intent_sha256": files["intent.json"]["sha256"], "release_sha256": files["release-matrix-release.json"]["sha256"],
        "restoration_sha256": files["release-restore-original-binding.json"]["sha256"], "completion_sha256": files["completion.json"]["sha256"]})
    expected = observation.get("expected") or observation.get("decision")
    proof.require(type(expected) is dict and expected.get("binding_revision") == handles[0]["binding_revision"])
    if "policy" in expected:
        proof.require(expected["policy"] == handles[0]["policy"] and expected.get("tenant") == intent["tenant_id"]
                and expected.get("stage") == intent["stage"] and expected.get("workload_id") == handles[0]["workload_id"])
    if "policy_digest" in expected:
        proof.require(expected["policy_digest"] == handles[0]["policy"]["digest"])
    proof.require(observation.get("tenant", intent["tenant_id"]) == intent["tenant_id"] and observation.get("stage", intent["stage"]) == intent["stage"])

