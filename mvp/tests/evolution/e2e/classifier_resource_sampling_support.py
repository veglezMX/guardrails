"""One real admitted classifier request under a serialized deployment lock.

The required verifier must authenticate the parent invocation, final selected
runtime qualification, current prepared/cold lineage and credential proofs.
There is no permissive verifier, historical-qualification fallback or implicit
profile switch. The parent must supply that final schema before registration.
"""
from __future__ import annotations

import copy
import hashlib
import sys
import time
from pathlib import Path
from uuid import uuid4

from evolution import classifier_resource_evidence as resources
from evolution import classifier_resource_measurements as evidence
from evolution.evaluator_attempt import GovernanceAttempt, operation_root
from evolution.evaluator_cases import content, control
from evolution.evaluator_governance import payload_from_approved, policy_reference
from guardrails.evolution.classifier_contract import identity, parse

ROOT = Path(__file__).resolve().parents[4]
CASE_BUDGET_SECONDS = 900
OBSERVATION_SECONDS = 60
OPERATIONAL_REASONS = {"inspection_incomplete", "queue_full", "evaluator_unavailable", "evaluation_timeout"}


def check_deadline(deadline):
    evidence.require(time.monotonic() < deadline)


def bounded_remaining(observer, original_allowance, case_deadline):
    active = min(case_deadline, getattr(observer, "resource_deadline", None) or case_deadline)
    check_deadline(active)
    return min(original_allowance, active - time.monotonic())


def verify(verifier, *, phase, observer, sources, operation, deadline):
    """A required reviewed integration callback, never a digest-only substitute."""
    evidence.require(callable(verifier) and phase in {"before", "after", "restored"})
    check_deadline(deadline)
    result = verifier(phase=phase, binding=copy.deepcopy(observer.bound),
                      sources=dict(sources), operation=operation, deadline=deadline)
    check_deadline(deadline)
    return copy.deepcopy(evidence.provenance(result))


def sample_bracket(observer, reader, *, dispatch, deadline, image_binding, progress):
    """No warmup/retry: exactly one unchanged caller request between counters."""
    started = time.monotonic()
    deadline = min(deadline, started + OBSERVATION_SECONDS)
    previous = getattr(observer, "resource_deadline", None)
    observer.resource_deadline = deadline
    try:
        result, sample = _sample_bracket(observer, reader, dispatch=dispatch, deadline=deadline,
                                         image_binding=image_binding, progress=progress)
        check_deadline(deadline)
        sample["sample_elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        evidence.require(sample["sample_elapsed_ms"] <= 60000)
        return result, sample
    finally:
        observer.resource_deadline = previous


def _sample_bracket(observer, reader, *, dispatch, deadline, image_binding, progress):
    from mvp.tests.evolution.e2e.classifier_network_support import observed_attempts

    check_deadline(deadline)
    before = reader.capture_locked(deadline=deadline, image_binding=image_binding)
    progress["before"] = copy.deepcopy(before)
    ready_before = observer.sample_locked()
    progress["ready_before"] = copy.deepcopy(ready_before)
    check_deadline(deadline)
    result = dispatch()
    check_deadline(deadline)
    ready_after = observer.sample_locked()
    progress["ready_after"] = copy.deepcopy(ready_after)
    after = reader.capture_locked(deadline=deadline, image_binding=image_binding)
    progress["after"] = copy.deepcopy(after)
    check_deadline(deadline)
    evidence.require(observed_attempts(ready_before, ready_after) == 1)
    binding = ready_before["binding_sha256"]
    evidence.require(before["binding_sha256"] == after["binding_sha256"] == binding
                     == image_binding["binding_sha256"]
                     and before["inventory"] == after["inventory"]
                     and before["images_sha256"] == after["images_sha256"]
                     == image_binding["identities_sha256"]
                     and before["inventory"]["inventory_sha256"] == image_binding["inventory_sha256"])
    # The reader already validates real raw kernel limits and full inventories.
    # Comparison additionally rejects PID/cgroup replacement and counter drift.
    usage = resources.compare_samples(before["kernel"], after["kernel"])
    evidence.require(before["inventory"]["totals"] == resources.TOTALS)
    return result, {"binding_sha256": binding, "images_sha256": before["images_sha256"],
        "inventory_sha256": before["inventory"]["inventory_sha256"],
        "system_inventory_sha256": before["inventory"]["system_inventory_sha256"],
        "before_capture_sha256": evidence.digest(before), "after_capture_sha256": evidence.digest(after),
        "counter_before": ready_before["counter"], "counter_after": ready_after["counter"], "usage": usage,
        "observations": copy.deepcopy(progress),
        "inventory": {"owned_pods": before["inventory"]["owned_pods"],
            "running_containers": before["inventory"]["running_containers"],
            "completed_initializers": before["inventory"]["completed_initializers"],
            "cpu_limit_millicores": resources.TOTALS["limits"]["cpu_milli"],
            "memory_limit_mib": resources.TOTALS["limits"]["memory_bytes"] // resources.MIB,
            "node_cpu_limit": 6, "node_memory_limit_bytes": 8 * 1024**3,
            "classifier_cpu_limit": 2, "classifier_memory_limit_bytes": 3 * 1024**3}}


def journal_hashes(attempt, *, deadline):
    from mvp.tests.evolution.e2e.classifier_network_support import private_bytes

    attempt.check()
    names = {"intent_sha256": "intent.json", "release_sha256": "release-matrix-release.json",
             "restoration_sha256": "release-restore-original-binding.json", "completion_sha256": "completion.json"}
    raw = {key: private_bytes(attempt.path / name, deadline=deadline, maximum=16384)
           for key, name in names.items()}
    completion = parse(raw["completion_sha256"], limit=16384)
    evidence.require(completion == {"operation": attempt.operation, "status": "case_passed",
        "restoration": "applied", "last_request_outcome_unknown": False})
    attempt.check()
    return {key: hashlib.sha256(value).hexdigest() for key, value in raw.items()}


def run_case(tenant, stage, *, verify_provenance, cluster_factory):
    """Execute only with an explicit durable journal root and evidence catalog.

    The 900s budget covers governance, native inventory and cleanup checks; each
    original HTTP evaluation retains its 3000ms request / 4s client budget.
    This is four independently registered requests, never a rate/SLA test.
    """
    evidence.require(callable(verify_provenance) and callable(cluster_factory))  # before any deployment access
    from mvp.tests.evolution.e2e.classifier_network_support import ReadinessObserver
    from mvp.tests.evolution.e2e.classifier_resource_support import ResourceReader

    case_id = f"OPS-CLASSIFIER-RESOURCES-{tenant.upper()}-{stage.upper()}"
    evidence.require(case_id in evidence.CASES)
    started = time.monotonic()
    deadline = started + CASE_BUDGET_SECONDS
    sources = evidence.gate_sources(ROOT, case_id, deadline=deadline)
    operations = operation_root()
    cluster = None
    attempt = journey = None
    primary = None
    restoration = "not_needed"
    result = None
    progress = {}
    phase = "setup"
    try:
        cluster = cluster_factory(deadline=deadline)  # authenticated read-only construction before the case lock
        # Commands use the shorter of their original allowance and case budget.
        class BoundedObserver(ReadinessObserver):
            def _remaining(self, maximum=10):
                return bounded_remaining(self, super()._remaining(maximum), deadline)

        observer = BoundedObserver(cluster)
        reader = ResourceReader(observer)
        with cluster.deployment.lock():
            try:
                check_deadline(deadline)
                phase = "binding"
                observer.bind_locked()
                image_binding = reader.verify_images_locked(deadline=deadline)
                progress["image_binding"] = copy.deepcopy(image_binding)
                original, original_policy = cluster.binding(tenant)
                other = next(value for value in cluster.tenants if value != tenant)
                other_before = cluster.binding(other)[0]
                baseline_before, base = cluster.baseline(tenant)
                item = cluster.inventory["tenants"][tenant]
                actors = {name: item["roles"][name]["principal_id"] for name in
                          ("tenant_admin", "author", "approver", "operator", "auditor")}
                journal_sources = {name: ROOT / name for name in evidence.SOURCES if name in {
                    "mvp/evolution/evaluator_attempt.py", "mvp/evolution/evaluator_governance.py",
                    "mvp/evolution/evaluator_cases.py", "mvp/evolution/classifier_resource_measurements.py",
                    "mvp/tests/evolution/e2e/classifier_resource_sampling_support.py",
                    "mvp/tests/evolution/e2e/test_cluster_classifier_resources.py",
                    "mvp/tests/evolution/e2e/evaluator_matrix_support.py"}}
                attempt = GovernanceAttempt(operations, tenant=tenant, original_binding=original,
                    original_policy=original_policy, actors=actors, engine="injection", stage=stage,
                    state="observe", sources=journal_sources)
                proof = verify(verify_provenance, phase="before", observer=observer, sources=sources,
                               operation=attempt.operation, deadline=deadline)
                journey = cluster.journey(tenant, attempt.operation, journal=attempt)
                phase = "governance"
                runtime_ids = tuple(row["runtime_id"] for row in item["runtimes"].values())
                rule = control("injection", stage)
                endpoint = cluster.endpoints[tenant]
                approved = journey.approve(payload_from_approved(base, rule, replacements=(
                    ("providers", endpoint["provider"]), ("resolved_destinations", endpoint["destination"]))))
                evidence.require(approved["author_id"] != actors["approver"] and
                    all(row in approved["rules"] for row in base["rules"] if row["mandatory"]))
                check_deadline(deadline)
                release = journey.release(policy=approved, workload_id=original["workload_id"], state="observe",
                    expected_revision=original["revision"], runtime_ids=runtime_ids, label="matrix-release")
                evidence.require(len(release["receipts"]) == 2)
                cluster.assert_governance_audit(tenant, approved, applied=True)
                marker = "resource-canary-" + uuid4().hex
                context = None
                if stage == "output":
                    phase = "context"
                    initial, request = cluster.evaluate(tenant, content("injection", "input", marker, clean=True), stage="input")
                    evidence.require(initial.action == "allow" and not initial.findings
                        and not OPERATIONAL_REASONS.intersection(initial.reason_codes)
                        and initial.policy.model_dump(mode="json") == policy_reference(approved))
                    cluster.assert_exact_audit(tenant, initial, request)
                    context = initial.context_id
                    evidence.require(context is not None)
                phase = "sampling"
                (decision, request), sample = sample_bracket(observer, reader,
                    dispatch=lambda: cluster.evaluate(tenant, content("injection", stage, marker, clean=True),
                        stage=stage, context_id=context), deadline=deadline, image_binding=image_binding, progress=progress)
                phase = "audit"
                evidence.require(decision.action == "allow" and decision.audit_status == "persisted"
                    and not OPERATIONAL_REASONS.intersection(decision.reason_codes)
                    and decision.transformed_parts is None and decision.binding_revision == release["binding_revision"]
                    and decision.policy.model_dump(mode="json") == policy_reference(approved)
                    and rule["evaluator_revision"] in decision.evaluator_revisions)
                audit = cluster.assert_exact_audit(tenant, decision, request)
                evidence.require(cluster.binding(other)[0] == other_before and cluster.baseline(tenant)[0] == baseline_before)
                cluster.assert_private_metadata(marker)
                evidence.require(verify(verify_provenance, phase="after", observer=observer, sources=sources,
                    operation=attempt.operation, deadline=deadline) == proof)
                result = {"schema_version": evidence.SCHEMA, "tenant": tenant, "stage": stage,
                    "operation": attempt.operation, "identity_sha256": evidence.digest(identity()), "provenance": proof,
                    **sample, "decision": {"request_id": str(decision.request_id), "decision_id": str(decision.decision_id),
                        "audit_id": str(audit["event_id"]), "policy_digest": decision.policy.digest,
                        "binding_revision": decision.binding_revision, "snapshot_generation": decision.snapshot_generation,
                        "evaluator_revision": rule["evaluator_revision"], "action": decision.action}}
            except BaseException as error:
                primary = error
                raise
            finally:
                try:
                    handle = journey.last_release if journey is not None else None
                    if handle is not None:
                        if primary is None:
                            phase = "restoration"
                        evidence.require(handle["operation"] == attempt.operation and handle["label"] == "matrix-release")
                        restoration = "failed"
                        revision = cluster.owned_binding_revision(tenant, handle)
                        # Cleanup uses the original finite release allowance even
                        # if sampling exhausted its budget; it never replays work.
                        journey.release(policy=original_policy, workload_id=original["workload_id"],
                            state=original["state"], expected_revision=revision, runtime_ids=runtime_ids,
                            label="restore-original-binding")
                        restoration = "applied"
                    if primary is None and result is not None:
                        evidence.require(restoration == "applied" and verify(verify_provenance, phase="restored",
                            observer=observer, sources=sources, operation=attempt.operation, deadline=deadline) == proof)
                except BaseException as cleanup_error:
                    if primary is None:
                        primary = cleanup_error
                        raise
                    primary.add_note("Resource case restoration also failed: " + type(cleanup_error).__name__)
                finally:
                    if attempt is not None:
                        try:
                            attempt.finish(passed=primary is None and result is not None and restoration == "applied",
                                           restoration=restoration)
                        except BaseException as journal_error:
                            if primary is None:
                                raise
                            primary.add_note("Resource journal completion also failed: " + type(journal_error).__name__)
            result["governance"] = journal_hashes(attempt, deadline=deadline)
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
            phase = "record"
    except BaseException as error:
        primary = error
        raise
    finally:
        for close in ([attempt.close] if attempt is not None else []) + ([cluster.close] if cluster is not None else []):
            try:
                close()
            except BaseException as close_error:  # noqa: BLE001 - retain first failure, including cancellation, then re-raise
                if primary is None:
                    primary = close_error
                else:
                    primary.add_note("Resource client close also failed: " + type(close_error).__name__)
        try:
            if primary is not None:
                kind = type(primary).__name__
                failed = {"schema_version": evidence.SCHEMA, "status": "failed", "tenant": tenant, "stage": stage,
                    "operation": attempt.operation if attempt is not None else None, "failed_phase": phase,
                    "exception_type": kind if kind in {"AssertionError", "EvidenceError", "ResourceObservationError",
                        "ClassifierObservationError", "TimeoutError"} else "OtherError", "observations": progress}
                evidence.record(failed, case_id, root=ROOT, sources=sources)
            else:
                evidence.record(result, case_id, root=ROOT, sources=sources)
        except BaseException as retention_error:
            if primary is None:
                raise
            primary.add_note("Resource observation retention also failed: " + type(retention_error).__name__)
        # If closing was the first failure there is no active exception to resume.
        if primary is not None and not sys.exc_info()[0]:
            raise primary
    return result
