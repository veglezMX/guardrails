"""Durable one-group CNI orchestration with mandatory final rollout verification.

The callback must authenticate the original parent invocation, final selected
runtime qualification, current prepared/cold/credential lineage and the actual
owned node's offline boundary. Historical qualification is not sufficient.
There is no default verifier, profile mutation, readiness warmup or retry.
"""
from __future__ import annotations

import copy
import sys
import time
from pathlib import Path
from uuid import uuid4

from evolution import classifier_cni_measurements as evidence
from guardrails.evolution.classifier_contract import identity

ROOT = Path(__file__).resolve().parents[4]
CASE_SECONDS = 600
PREFLIGHT_SECONDS = 300
GROUP_SECONDS = 120


def check_deadline(deadline):
    evidence.require(time.monotonic() < deadline)


def remaining(original, deadline):
    check_deadline(deadline)
    return min(original, deadline - time.monotonic())


def verify(verifier, *, phase, observer, group, sources, operation, deadline):
    check_deadline(deadline)
    result = verifier(phase=phase, binding=copy.deepcopy(observer.bound), group=copy.deepcopy(group),
        sources=dict(sources), operation=operation, deadline=deadline)
    check_deadline(deadline)
    return copy.deepcopy(evidence.references(result))


def run_case(*, verify_provenance, cluster_factory):
    """One original 120s group, bracketed by final provenance under one lock.

The intent is flushed before constructing any deployment client. Every producer
prefix is saved after its own host cleanup even when an assertion or callback
fails. Unknown outcomes are retained and never resumed or automatically retried.
"""
    evidence.require(callable(verify_provenance) and callable(cluster_factory))
    started = time.monotonic()
    deadline = started + CASE_SECONDS
    sources = evidence.gate_sources(ROOT, deadline=deadline)
    operation = str(uuid4())
    writer = evidence.EvidenceWriter()
    cluster = runner = None
    primary = None
    proof = group = None
    phase = "setup"
    intent_sha = authorization_sha = None
    value = None
    try:
        intent_sha = writer.write(evidence.INTENT, evidence.intent(operation, sources))
        from mvp.tests.evolution.e2e.classifier_cni_group import CniObservation
        from mvp.tests.evolution.e2e.classifier_network_support import ReadinessObserver
        from mvp.tests.evolution.e2e.classifier_resource_support import ResourceReader

        cluster = cluster_factory(deadline=deadline)  # never acquires a nested deployment lock
        active = min(deadline, time.monotonic() + PREFLIGHT_SECONDS)
        class BoundedObserver(ReadinessObserver):
            def _remaining(self, maximum=10):
                return remaining(super()._remaining(maximum), active)

        observer = BoundedObserver(cluster)
        with cluster.deployment.lock():
            phase = "binding"
            check_deadline(active)
            observer.bind_locked()
            image_binding = ResourceReader(observer).verify_images_locked(deadline=active)
            phase = "provenance_before"
            proof = verify(verify_provenance, phase="before", observer=observer, group=None,
                sources=sources, operation=operation, deadline=active)
            authorization_sha = writer.write(evidence.AUTHORIZATION, evidence.authorization(
                operation, intent_sha, proof, observer.bound, image_binding))
            writer.check()
            evidence.validate_sources(root=ROOT, sources=sources)
            phase = "group"
            runner = CniObservation(observer, image_binding)
            active = min(deadline, time.monotonic() + GROUP_SECONDS)
            try:
                observed = runner.run_locked()
                check_deadline(active)
                evidence.require(observed == runner.value)
                evidence.group_proofs(observed)
            finally:
                # Save the exact entire producer value after cleanup on success
                # and every partial failure; no synthetic success replacement.
                group = copy.deepcopy(runner.value)
            active = deadline
            phase = "provenance_after"
            evidence.require(verify(verify_provenance, phase="after", observer=observer, group=group,
                sources=sources, operation=operation, deadline=deadline) == proof)
            phase = "closure"
            evidence.validate_sources(root=ROOT, sources=sources)
            writer.check()
            check_deadline(deadline)
    except BaseException as error:
        primary = error
        raise
    finally:
        if cluster is not None:
            try:
                cluster.close()
            except BaseException as error:  # noqa: BLE001 - preserve the original failure and still retain observations
                if primary is None:
                    primary, phase = error, "close"
                else:
                    primary.add_note("CNI client close also failed: " + type(error).__name__)
        try:
            if intent_sha is not None:
                elapsed = round((time.monotonic() - started) * 1000, 6)
                if primary is None and elapsed > CASE_SECONDS * 1000:
                    primary, phase = evidence.EvidenceError("measurement_schema_invalid"), "record"
                kind = type(primary).__name__ if primary is not None else None
                if kind not in {None, "AssertionError", "EvidenceError", "CniObservationError", "ClassifierObservationError", "TimeoutError"}:
                    kind = "OtherError"
                value = {"schema_version": evidence.SCHEMA, "case_id": evidence.CASE,
                    "operation": operation, "status": "failed" if primary is not None else "observed",
                    "phase": phase if primary is not None else "record", "exception_type": kind,
                    "elapsed_ms": elapsed,
                    "identity_sha256": evidence.digest(identity()), "sources_sha256": evidence.digest(sources),
                    "intent_sha256": intent_sha, "authorization_sha256": authorization_sha,
                    "provenance": proof, "group": group,
                    "group_sha256": evidence.digest(group) if group is not None else None,
                    "isolation_scope": "node_and_cni_composed_host_external_metadata"}
                evidence.validate_payload(value)
                writer.write(evidence.FILENAME, value)
        except BaseException as error:  # noqa: BLE001 - finish descriptor cleanup before propagating the first failure
            if primary is None:
                primary = error
            else:
                primary.add_note("CNI evidence retention also failed: " + type(error).__name__)
        finally:
            try:
                writer.close()
            except BaseException as error:  # noqa: BLE001 - retain first failure even if closing the owned descriptor fails
                if primary is None:
                    primary = error
                else:
                    primary.add_note("CNI evidence close also failed: " + type(error).__name__)
        if primary is not None and not sys.exc_info()[0]:
            raise primary
    return value
