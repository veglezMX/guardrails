"""Parent-only original TLS probe with durable authorization and observation.

The mandatory callback verifies independent authorization/current transport;
only collection may consume the separately authenticated parent completion.
No profile change, retry, warmup, repair or model dispatch is introduced.
"""
from __future__ import annotations

import copy
import time
from uuid import uuid4

from evolution import classifier_tls_measurements as evidence
from guardrails.evolution.classifier_contract import canonical
from guardrails.evolution.deadlines import current_deadline

from mvp.tests.evolution.e2e.classifier_operational_support import TlsObservation


def check(deadline):
    evidence.require(time.monotonic() < deadline)


def verify(verifier, *, phase, value, sources, deadline):
    check(deadline)
    original, original_sources = canonical(value), canonical(sources)
    proof = verifier(phase=phase, case_id=value["case_id"], invocation=value["invocation"], operation=None,
        binding=copy.deepcopy(value["binding"]), observation=copy.deepcopy(value), sources=dict(sources), deadline=deadline)
    check(deadline)
    evidence.require(canonical(value) == original and canonical(sources) == original_sources)
    return copy.deepcopy(evidence.references(proof))


def run_tls_case(cluster_factory, *, root, runtime, verify_provenance):
    """Retain intent before clients; execute the original probe exactly once."""
    evidence.require(callable(verify_provenance) and callable(cluster_factory))
    case_id = "SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-" + runtime.upper()
    evidence.require(case_id in evidence.CASES)
    started = time.monotonic()
    deadline = started + 240
    sources = evidence.gate_sources(root, case_id, deadline=deadline)
    writer = evidence.EvidenceWriter(case_id)
    token = current_deadline.set(deadline)
    value = {"schema_version": evidence.SCHEMA, "case_id": case_id, "invocation": str(uuid4()),
        "status": "failed", "phase": "setup", "failure_type": None, "elapsed_ms": 0,
        "identity_sha256": evidence.digest(evidence.identity()), "sources_sha256": evidence.digest(sources),
        "intent_sha256": None, "authorization_sha256": None, "provenance": None, "binding": None,
        "component": None, "cleanup": "not_started"}
    cluster = observation = primary = None
    try:
        value["intent_sha256"] = writer.write(evidence.INTENT, evidence.intent(value["invocation"], case_id, sources))
        check(deadline); writer.check()
        cluster = cluster_factory(deadline=deadline)  # Explicit authenticated read-only factory, before the case lock.
        observation = TlsObservation(cluster, runtime)
        original_remaining = observation.observer._remaining
        def remaining(maximum=10):
            check(deadline)
            return min(original_remaining(maximum), deadline - time.monotonic())
        observation.observer._remaining = remaining
        with cluster.deployment.lock():
            value["phase"] = "binding"
            check(deadline); writer.check(); observation.bind_locked()
            value["binding"] = copy.deepcopy(observation.observer.bound)
            evidence.binding(value["binding"], runtime)
            value["phase"] = "provenance_before"
            value["provenance"] = verify(verify_provenance, phase="before", value=value, sources=sources, deadline=deadline)
            value["authorization_sha256"] = writer.write(evidence.AUTHORIZATION, evidence.authorization(value))
            value["phase"] = "probe"
            check(deadline); writer.check()
            try:
                observation.run_locked()  # Original eight scenarios,90 s cap and cleanup unchanged.
            finally:
                value["component"] = copy.deepcopy(observation.value)
            check(deadline)
            evidence.component.validate(value["component"], case_id, root=root, sources=sources, complete=True)
            evidence.require(value["component"]["binding_sha256"] == evidence.digest(value["binding"]))
            value["phase"] = "provenance_after"
            evidence.require(verify(verify_provenance, phase="after", value=value, sources=sources, deadline=deadline)
                == value["provenance"])
            writer.check(); evidence.validate_sources(root, sources)
        value["phase"] = "close"
    except BaseException as error:
        primary = error
        raise
    finally:
        current_deadline.reset(token)
        cleanup_error = None
        try:
            if cluster is not None:
                cluster.close()
                value["cleanup"] = "passed"
        except BaseException as error:  # noqa: BLE001 - preserve primary failure and finite cleanup status
            value["cleanup"] = "failed"; cleanup_error = error
            if primary is not None:
                primary.add_note("TLS client close also failed: " + type(error).__name__)
        failure = primary or cleanup_error
        value["elapsed_ms"] = round((time.monotonic() - started) * 1000, 6)
        try:
            if failure is None:
                check(deadline)
                value.update(status="passed", phase="record")
                evidence.validate_sources(root, sources)
            else:
                value["failure_type"] = type(failure).__name__ if type(failure).__name__ in evidence.FAILURES else "OtherError"
        except BaseException as error:  # noqa: BLE001 - late refusal is retained, never a successful row
            failure = error
            value.update(status="failed", failure_type=type(error).__name__ if type(error).__name__ in evidence.FAILURES else "OtherError")
        try:
            if value["intent_sha256"] is not None:
                evidence.validate_payload(value, case_id, root=root, sources=sources)
                writer.write(evidence.FILENAME, value)
        except BaseException as error:  # noqa: BLE001 - failed retention never hides original request failure
            if failure is not None:
                failure.add_note("TLS evidence retention also failed: " + type(error).__name__)
            else:
                failure = error
        finally:
            writer.close()
        if primary is None and failure is not None:
            raise failure
    return value
