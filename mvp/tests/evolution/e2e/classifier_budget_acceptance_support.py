"""Twelve independent original requests, each under owned governed restoration.

Only explicit parent execution may construct deployment/HTTP/database clients.
The mandatory callback must authenticate the current final selected worker and
owned transport/credentials; there is no fallback to initial qualification.
"""
from __future__ import annotations

import copy
import hashlib
import sys
import time
from pathlib import Path
from uuid import uuid4

from evolution import classifier_budget_evidence as budget
from evolution import classifier_budget_measurements as evidence
from evolution.evaluator_attempt import GovernanceAttempt, operation_root
from evolution.evaluator_cases import content, control
from evolution.evaluator_governance import payload_from_approved, policy_reference
from guardrails.evolution import models
from guardrails.evolution.classifier_contract import identity, parse
from guardrails.evolution.deadlines import current_deadline

from mvp.tests.evolution.e2e.classifier_budget_support import (
    BudgetObserver,
    BudgetReader,
)
from mvp.tests.evolution.e2e.classifier_network_support import private_bytes

ROOT = Path(__file__).resolve().parents[4]
CASE_SECONDS = 900
JOURNAL_SOURCES = (
    "mvp/evolution/evaluator_attempt.py", "mvp/evolution/evaluator_governance.py", "mvp/evolution/evaluator_cases.py",
    "mvp/evolution/classifier_budget_measurements.py", "mvp/tests/evolution/e2e/classifier_budget_acceptance_support.py",
    "mvp/tests/evolution/e2e/test_cluster_classifier_budget.py", "mvp/tests/evolution/e2e/evaluator_matrix_support.py",
)


def check(deadline):
    evidence.require(time.monotonic() < deadline)


def verify(callback, value, sources, *, phase, deadline):
    """The callback owns actual verification; only finite references are stored."""
    evidence.require(callable(callback) and phase in {"before", "transport_before", "transport_after", "after", "restored"})
    check(deadline)
    before, source_before = evidence.digest(value), evidence.digest(sources)
    proof = callback(phase=phase, case_id=value["case_id"], invocation=value["invocation"], operation=value["operation"],
        binding=copy.deepcopy(value["binding"]), observation=copy.deepcopy(value), sources=dict(sources), deadline=deadline)
    check(deadline)
    evidence.require(evidence.digest(value) == before and evidence.digest(sources) == source_before)
    evidence.validate_sources(ROOT, sources)
    return copy.deepcopy(evidence.references(proof))


def request(item, tenant, stage, scenario, marker, context):
    """Existing untuned matrix payloads; output context is created separately."""
    return {"schema_version": "guardrails-v2", "request_id": str(uuid4()), "correlation_id": str(uuid4()),
        "tenant_id": tenant, "workload_id": item["workload"]["workload_id"], "stage": stage,
        "provider_id": item["profiles"]["mock"]["provider"]["provider_id"], "context_id": context,
        "parts": [content("injection", stage, marker, clean=scenario != "malicious")], "deadline_ms": 3000}


def context_reference(cluster, tenant, expected, marker):
    """Input prerequisite outside the timed output bracket; genuine durable audit."""
    decision, sent = cluster.evaluate(tenant, content("injection", "input", marker, clean=True), stage="input")
    evidence.require(decision.action == "allow" and not decision.findings and not decision.reason_codes
        and decision.audit_status == "persisted" and decision.transformed_parts is None
        and decision.policy.model_dump(mode="json") == expected["policy"]
        and decision.binding_revision == expected["binding_revision"] and decision.snapshot_generation in expected["snapshot_generations"])
    context = str(decision.context_id)
    budget.identifier(context)
    event = cluster.assert_exact_audit(tenant, decision, sent)
    parsed = models.AuditEvent.model_validate_json(evidence.canonical(event)).model_dump(mode="json")
    evidence.require(parsed["event_id"] == parsed["decision_id"] == decision.decision_id
        and parsed["actor_id"] == expected["principal_id"] and parsed["metadata"] == {"stage": "input"}
        and parsed["reason_codes"] == [] and parsed["action"] == "allow")
    return context, {"context_sha256": budget.identifier(context), "decision_sha256": budget.identifier(decision.decision_id),
        "audit_event_hash": parsed["event_hash"], "policy_sha256": evidence.digest(expected["policy"]),
        "binding_revision": decision.binding_revision, "snapshot_generation": decision.snapshot_generation}


def journal_hashes(attempt, deadline):
    attempt.check()
    names = {"intent_sha256": "intent.json", "release_sha256": "release-matrix-release.json",
        "restoration_sha256": "release-restore-original-binding.json", "completion_sha256": "completion.json"}
    raw = {key: private_bytes(attempt.path / name, deadline=deadline, maximum=16384) for key, name in names.items()}
    evidence.require(parse(raw["completion_sha256"], limit=16384) == {"operation": attempt.operation, "status": "case_passed",
        "restoration": "applied", "last_request_outcome_unknown": False})
    attempt.check()
    return {key: hashlib.sha256(value).hexdigest() for key, value in raw.items()}


def run_case(tenant, stage, scenario, *, verify_provenance, cluster_factory):
    evidence.require(callable(verify_provenance) and callable(cluster_factory))  # no source/client access without authentic integration
    case_id = f"OPS-CLASSIFIER-BUDGET-{tenant.upper()}-{stage.upper()}-{scenario.upper().replace('_', '-')}"
    evidence.require(case_id in evidence.CASES)
    started = time.monotonic()
    deadline = started + CASE_SECONDS
    sources = evidence.gate_sources(ROOT, case_id, deadline=deadline)
    operations = operation_root()
    writer = evidence.EvidenceWriter(case_id)
    value = {"schema_version": evidence.SCHEMA, "case_id": case_id, "invocation": str(uuid4()), "operation": None,
        "status": "failed", "phase": "setup", "failure_type": None, "elapsed_ms": 0,
        "identity_sha256": evidence.digest(identity()), "sources_sha256": evidence.digest(sources),
        "intent_sha256": None, "authorization_sha256": None, "provenance": None, "binding": None,
        "expected": None, "request": None, "context": None, "reader": None, "governance": None,
        "restoration": "not_needed", "invariants": {}}
    cluster = observer = attempt = journey = reader = None
    primary = None
    token = current_deadline.set(deadline)
    try:
        value["intent_sha256"] = writer.write(evidence.INTENT, evidence.intent(value["invocation"], case_id, sources))

        cluster = cluster_factory(deadline=deadline)  # authenticated read-only construction before the case lock
        class CaseObserver(BudgetObserver):
            def _remaining(self, maximum=10):
                original = super()._remaining(maximum)
                check(deadline)
                return min(original, deadline - time.monotonic())
        observer = CaseObserver(cluster)
        with cluster.deployment.lock():
            try:
                value["phase"] = "binding"
                check(deadline); observer.bind_locked()
                value["binding"] = copy.deepcopy(observer.bound)
                evidence.binding(value["binding"])
                original, original_policy = cluster.binding(tenant)
                other = "team-b" if tenant == "team-a" else "team-a"
                other_before = cluster.binding(other)[0]
                baseline_before, base = cluster.baseline(tenant)
                value["invariants"] = {"other_binding_sha256": evidence.digest(other_before),
                    "baseline_sha256": evidence.digest(baseline_before), "original_policy_sha256": evidence.digest(policy_reference(original_policy))}
                item = cluster.inventory["tenants"][tenant]
                actors = {role: item["roles"][role]["principal_id"] for role in ("tenant_admin", "author", "approver", "operator", "auditor")}
                attempt = GovernanceAttempt(operations, tenant=tenant, original_binding=original, original_policy=original_policy,
                    actors=actors, engine="injection", stage=stage, state="observe", sources={name: ROOT / name for name in JOURNAL_SOURCES})
                value["operation"] = attempt.operation
                value["phase"] = "provenance_before"
                value["provenance"] = verify(verify_provenance, value, sources, phase="before", deadline=deadline)
                value["authorization_sha256"] = writer.write(evidence.AUTHORIZATION,
                    evidence.authorization(value["invocation"], attempt.operation, case_id, value["intent_sha256"], value["provenance"], value["binding"]))
                journey = cluster.journey(tenant, attempt.operation, journal=attempt)
                value["phase"] = "governance"
                runtime_ids = tuple(row["runtime_id"] for row in item["runtimes"].values())
                endpoint = cluster.endpoints[tenant]
                rule = control("injection", stage)
                approved = journey.approve(payload_from_approved(base, rule, replacements=(
                    ("providers", endpoint["provider"]), ("resolved_destinations", endpoint["destination"]))))
                evidence.require(approved["author_id"] != actors["approver"] and all(row in approved["rules"] for row in base["rules"] if row["mandatory"]))
                check(deadline)
                release = journey.release(policy=approved, workload_id=original["workload_id"], state="observe",
                    expected_revision=original["revision"], runtime_ids=runtime_ids, label="matrix-release")
                evidence.require(len(release["receipts"]) == 2 and {row["runtime_id"] for row in release["receipts"]} == set(runtime_ids))
                cluster.assert_governance_audit(tenant, approved, applied=True)
                value["expected"] = {"tenant": tenant, "stage": stage, "scenario": scenario, "workload_id": item["workload"]["workload_id"],
                    "principal_id": item["roles"]["workload"]["principal_id"], "policy": policy_reference(approved),
                    "binding_revision": release["binding_revision"], "snapshot_generations": sorted({row["snapshot_generation"] for row in release["receipts"]})}
                budget.expectation(value["expected"])
                marker = "budget-canary-" + uuid4().hex
                context = None
                if stage == "output":
                    value["phase"] = "context"
                    context, value["context"] = context_reference(cluster, tenant, value["expected"], marker)
                check(deadline)
                sent = request(item, tenant, stage, scenario, marker, context)
                budget.request(sent, value["expected"])
                value["request"] = evidence.request_reference(sent)
                calls = []
                def recheck_transport(*, deadline):
                    evidence.require(len(calls) < 2)
                    phase = "transport_before" if not calls else "transport_after"
                    value["reader"] = copy.deepcopy(reader.value)
                    evidence.require(verify(verify_provenance, value, sources, phase=phase, deadline=deadline) == value["provenance"])
                    calls.append(phase)
                value["phase"] = "sampling"
                reader = BudgetReader(observer, recheck_transport=recheck_transport)
                try:
                    observed = reader.sample_locked(sent, expected=value["expected"], deadline=deadline)
                    evidence.require(observed == reader.value and calls == ["transport_before", "transport_after"])
                finally:
                    value["reader"] = copy.deepcopy(reader.value)
                evidence.reader_value(value["reader"], bound=value["binding"], expected=value["expected"], request=value["request"], complete=True)
                value["phase"] = "invariants"
                evidence.require(cluster.binding(other)[0] == other_before and cluster.baseline(tenant)[0] == baseline_before)
                cluster.assert_private_metadata(marker)
                value["phase"] = "provenance_after"
                evidence.require(verify(verify_provenance, value, sources, phase="after", deadline=deadline) == value["provenance"])
            except BaseException as error:
                primary = error
                raise
            finally:
                try:
                    handle = journey.last_release if journey is not None else None
                    if handle is not None:
                        if primary is None:
                            value["phase"] = "restoration"
                        evidence.require(handle["operation"] == attempt.operation and handle["label"] == "matrix-release")
                        value["restoration"] = "failed"
                        # Cleanup is one separate bounded owned CAS, even after an
                        # expired observation. It never retries the original case.
                        cleanup_token = current_deadline.set(time.monotonic() + 120)
                        try:
                            revision = cluster.owned_binding_revision(tenant, handle)
                            journey.release(policy=original_policy, workload_id=original["workload_id"], state=original["state"],
                                expected_revision=revision, runtime_ids=runtime_ids, label="restore-original-binding")
                            restored, restored_policy = cluster.binding(tenant)
                            evidence.require(restored["policy"] == original["policy"] and restored["state"] == original["state"]
                                and restored["revision"] == revision + 1 and policy_reference(restored_policy) == policy_reference(original_policy))
                            evidence.require(cluster.binding(other)[0] == other_before and cluster.baseline(tenant)[0] == baseline_before)
                            value["invariants"]["restored_policy_sha256"] = evidence.digest(policy_reference(restored_policy))
                            value["restoration"] = "applied"
                        finally:
                            current_deadline.reset(cleanup_token)
                    if primary is None:
                        value["phase"] = "restored"
                        evidence.require(value["restoration"] == "applied" and verify(verify_provenance, value, sources,
                            phase="restored", deadline=deadline) == value["provenance"])
                except BaseException as error:
                    if primary is None:
                        primary = error
                        raise
                    primary.add_note("Budget restoration also failed: " + type(error).__name__)
                finally:
                    if attempt is not None:
                        try:
                            attempt.finish(passed=primary is None and value["reader"] is not None and value["restoration"] == "applied", restoration=value["restoration"])
                        except BaseException as error:
                            if primary is None:
                                primary = error
                                raise
                            primary.add_note("Budget journal completion also failed: " + type(error).__name__)
            value["phase"] = "journal"
            value["governance"] = journal_hashes(attempt, deadline)
            evidence.validate_sources(ROOT, sources); writer.check(); check(deadline)
    except BaseException as error:
        primary = error
        raise
    finally:
        current_deadline.reset(token)
        for close in ([attempt.close] if attempt is not None else []) + ([cluster.close] if cluster is not None else []):
            try:
                close()
            except BaseException as error:  # noqa: BLE001 - preserve first failure and finish remaining cleanup
                if primary is None:
                    primary, value["phase"] = error, "close"
                else:
                    primary.add_note("Budget client close also failed: " + type(error).__name__)
        try:
            if value["intent_sha256"] is not None:
                value["elapsed_ms"] = round((time.monotonic() - started) * 1000, 6)
                if primary is None and value["elapsed_ms"] > CASE_SECONDS * 1000:
                    primary, value["phase"] = evidence.EvidenceError("measurement_schema_invalid"), "record"
                if primary is None:
                    value.update(status="observed", phase="record")
                else:
                    kind = type(primary).__name__
                    value["failure_type"] = kind if kind in evidence.FAILURES else "OtherError"
                evidence.validate_payload(value, case_id)
                writer.write(evidence.FILENAME, value)
        except BaseException as error:  # noqa: BLE001 - failure artifact never replaces original failure
            if primary is None:
                primary = error
            else:
                primary.add_note("Budget evidence retention also failed: " + type(error).__name__)
        finally:
            try:
                writer.close()
            except BaseException as error:  # noqa: BLE001 - closing cannot erase earlier errors
                if primary is None:
                    primary = error
                else:
                    primary.add_note("Budget evidence close also failed: " + type(error).__name__)
        if primary is not None and not sys.exc_info()[0]:
            raise primary
    return value
