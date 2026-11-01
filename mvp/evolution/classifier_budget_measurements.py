"""Closed, retained budget observations; no default rollout-proof verifier.

The twelve original governed cases are not registered by this module. Successful
consumption requires authentic invocation, final selected-worker qualification,
prepared/cold/current credentials and current owned transport verification.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
from pathlib import Path
from uuid import UUID

from guardrails.evolution.classifier_contract import canonical, identity

from . import classifier_budget_evidence as budget
from .classifier_dispatch import delta
from .classifier_source_projection import check_deadline, project_sources
from .diagnostics import SourceCatalog, read_catalog
from .registry import EvidenceError

SCHEMA = "evolution-classifier-governed-budget-v1"
FILENAME = "classifier-budget-observation.json"
INTENT = "classifier-budget-intent.json"
AUTHORIZATION = "classifier-budget-authorization.json"
MAX_BYTES = 128 * 1024
CASES = {f"OPS-CLASSIFIER-BUDGET-{tenant.upper()}-{stage.upper()}-{scenario.upper().replace('_', '-')}": (tenant, stage, scenario)
    for tenant in ("team-a", "team-b") for stage in ("input", "output") for scenario in ("benign", "malicious", "slow_body")}
PROOFS = {"parent_invocation", "qualification", "prepared", "cold", "credentials", "transport"}
PHASES = {"setup", "binding", "provenance_before", "governance", "context", "sampling", "invariants",
          "provenance_after", "restoration", "restored", "journal", "close", "record"}
FAILURES = {"AssertionError", "EvidenceError", "BudgetObservationError", "ClassifierObservationError", "TimeoutError", "OtherError"}
SOURCES = (
    "mvp/evolution/classifier_budget_measurements.py",
    "mvp/evolution/classifier_source_projection.py", "mvp/evolution/classifier_budget_evidence.py",
    "mvp/evolution/classifier_dispatch.py", "mvp/evolution/evaluator_attempt.py", "mvp/evolution/evaluator_governance.py",
    "mvp/evolution/evaluator_cases.py", "mvp/evolution/diagnostics.py", "mvp/evolution/registry.py",
    "mvp/tests/evolution/e2e/classifier_budget_acceptance_support.py", "mvp/tests/evolution/e2e/test_cluster_classifier_budget.py",
    "mvp/tests/evolution/e2e/classifier_budget_support.py", "mvp/tests/evolution/e2e/classifier_network_support.py",
    "mvp/tests/evolution/e2e/evaluator_matrix_support.py", "mvp/tests/evolution/e2e/cluster_support.py",
    "mvp/guardrails/evolution/classifier_contract.py", "mvp/guardrails/evolution/runtime_api.py",
    "mvp/guardrails/evolution/execution_profiles.py", "mvp/guardrails/evolution/audit.py",
    "mvp/guardrails/evolution/storage.py", "mvp/guardrails/evolution/deadlines.py", "mvp/guardrails/evolution/models.py",
    "deploy/evolution/certificates.py", "deploy/evolution/image_identity.py",
)


def require(value):
    if not value:
        raise EvidenceError("measurement_schema_invalid")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def uuid(value):
    require(type(value) is str and len(value) == 36)
    try:
        require(str(UUID(value)) == value)
    except ValueError:
        raise EvidenceError("measurement_schema_invalid") from None


def bounded(value):
    """Bound arbitrary nesting before serialization; only finite metadata types."""
    count = 0
    def visit(item, depth):
        nonlocal count
        count += 1
        require(depth <= 16 and count <= 8192)
        if item is None or type(item) is bool:
            return
        if type(item) in (int, float):
            budget.finite(item, maximum=2**63 - 1)
        elif type(item) is str:
            require(len(item) <= 4096)
        elif type(item) in (dict, list):
            require(len(item) <= 256)
            if type(item) is dict:
                require(all(type(key) is str and len(key) <= 256 for key in item))
            for child in item.values() if type(item) is dict else item:
                visit(child, depth + 1)
        else:
            require(False)
    visit(value, 0)
    require(len(canonical(value)) + 1 <= MAX_BYTES)


def references(value):
    require(type(value) is dict and set(value) == PROOFS and all(sha(row) for row in value.values()))
    return value


def binding(value):
    require(type(value) is dict and set(value) == {"owner_id", "node_id", "api_server", "files", "service", "pods"})
    require(type(value["owner_id"]) is str and re.fullmatch(r"[0-9a-f]{32}", value["owner_id"]) is not None and sha(value["node_id"]))
    require(type(value["api_server"]) is str and re.fullmatch(r"https://127\.0\.0\.1:[1-9][0-9]{3,4}", value["api_server"]) is not None
            and 1024 <= int(value["api_server"].rsplit(":", 1)[1]) <= 65535)
    require(type(value["files"]) is dict and 6 <= len(value["files"]) <= 64
            and {"owner.json", "prepared.json", "assembly/access.json", "assembly/values.json",
                 "assembly/private/classifier.token", "assembly/tls/gr-platform/classifier/ca.crt"} <= set(value["files"])
            and all(type(k) is str and not Path(k).is_absolute() and ".." not in Path(k).parts and sha(v) for k, v in value["files"].items()))
    service = value["service"]
    require(type(service) is dict and set(service) == {"uid", "address", "endpoint_slice_uid"})
    uuid(service["uid"]); uuid(service["endpoint_slice_uid"])
    require(type(service["address"]) is str and re.fullmatch(r"[0-9a-f:.]{1,45}", service["address"]) is not None)
    require(type(value["pods"]) is dict and set(value["pods"]) == {"runtime-1", "classifier"})
    for name, pod in value["pods"].items():
        require(type(pod) is dict and set(pod) == {"pod_name", "pod_uid", "deployment_uid", "replica_uid", "container_id", "image", "restarts", "pod_ip", "spec_sha256"})
        require(type(pod["pod_name"]) is str and re.fullmatch(name + r"-[a-z0-9-]+", pod["pod_name"]) is not None)
        for key in ("pod_uid", "deployment_uid", "replica_uid"):
            uuid(pod[key])
        require(type(pod["container_id"]) is str and re.fullmatch(r"containerd://[0-9a-f]{64}", pod["container_id"]) is not None
            and type(pod["image"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", pod["image"]) is not None
            and type(pod["restarts"]) is int and pod["restarts"] >= 0 and sha(pod["spec_sha256"])
            and type(pod["pod_ip"]) is str and re.fullmatch(r"[0-9a-f:.]{1,45}", pod["pod_ip"]) is not None)
    return value


def intent(invocation, case_id, sources):
    uuid(invocation); require(case_id in CASES)
    return {"schema_version": SCHEMA, "invocation": invocation, "case_id": case_id,
        "status": "intended", "identity_sha256": digest(identity()), "sources_sha256": digest(sources),
        "runtime_budget_ms": 3000, "client_budget_ms": 4000, "connect_budget_ms": 250,
        "slow_body_delay_ms": 600, "observation_budget_ms": 60000}


def authorization(invocation, operation, case_id, intended, proof, bound):
    return {"schema_version": SCHEMA, "invocation": invocation, "operation": operation, "case_id": case_id,
        "intent_sha256": intended, "provenance": proof, "binding": bound}


def file_digest(value):
    return hashlib.sha256(canonical(value) + b"\n").hexdigest()


def request_reference(sent):
    return {"request_sha256": budget.identifier(sent["request_id"]), "correlation_sha256": budget.identifier(sent["correlation_id"]),
        "context_sha256": budget.identifier(sent["context_id"]) if sent["context_id"] is not None else None,
        "body_sha256": digest(sent)}


def ready(value, expected_binding):
    budget.readiness(value, value, attempts=0)
    require(value["binding_sha256"] == expected_binding)


def reader_value(value, *, bound=None, expected=None, request=None, complete=False):
    if value is None:
        require(not complete)
        return
    require(type(value) is dict and {"status", "phase", "observations"} <= set(value)
        and set(value) <= {"status", "phase", "observations", "row", "elapsed_ms", "failure_type"}
        and value["status"] in {"unavailable", "failed", "observed"}
        and value["phase"] in {"not_started", "binding", "request", "audit", "after", "complete"})
    if "failure_type" in value:
        require(value["failure_type"] in FAILURES and value["status"] == "failed")
    if "elapsed_ms" in value:
        budget.finite(value["elapsed_ms"], maximum=2**63 - 1)
    observations = value["observations"]
    require(type(observations) is dict and set(observations) <= {"transport_pins", "ready_before", "wire", "ready_after"})
    if "transport_pins" in observations:
        row = observations["transport_pins"]
        require(type(row) is dict and set(row) == {"ca_sha256", "token_sha256"} and all(sha(v) for v in row.values()))
    for key in ("ready_before", "ready_after"):
        if key in observations:
            require(bound is not None)
            ready(observations[key], digest(bound))
    if "wire" in observations:
        wire = observations["wire"]
        require(type(wire) is dict and set(wire) == {"client_elapsed_ms", "body_delay_ms", "tls"} and wire["tls"] in {"TLSv1.2", "TLSv1.3"})
        budget.finite(wire["client_elapsed_ms"], maximum=4000, minimum=0.001)
        budget.finite(wire["body_delay_ms"], maximum=4000)
    if not complete:
        # A failed post-validation row still has the same finite closed schema.
        if "row" not in value:
            return
    else:
        require(value["status"] == "observed" and value["phase"] == "complete" and "failure_type" not in value)
        budget.finite(value.get("elapsed_ms"), maximum=60000, minimum=0.001)
    require(set(observations) == {"transport_pins", "ready_before", "wire", "ready_after"} and expected is not None and request is not None)
    row = value.get("row")
    common = {"tenant", "stage", "scenario", "request_sha256", "correlation_sha256", "binding_sha256", "counter_before", "counter_after", "dispatch_delta", "http_status", "client_elapsed_ms", "connect_timeout_ms"}
    slow = expected["scenario"] == "slow_body"
    fields = common | ({"error_code", "audit_matches", "body_delay_ms"} if slow else {"decision_sha256", "context_sha256", "actor_sha256", "policy_sha256", "binding_revision", "snapshot_generation", "evaluator_identity_sha256", "action", "finding_count", "audit_status", "audit_event_hash", "server_elapsed_ms"})
    require(type(row) is dict and set(row) == fields and all(row[k] == expected[k] for k in ("tenant", "stage", "scenario"))
        and all(row[k] == request[k] for k in ("request_sha256", "correlation_sha256")) and row["binding_sha256"] == digest(bound)
        and row["counter_before"] == observations["ready_before"]["counter"] and row["counter_after"] == observations["ready_after"]["counter"]
        and type(row["dispatch_delta"]) is int and row["dispatch_delta"] == (0 if slow else 1)
        and delta(row["counter_before"], row["counter_after"]) == row["dispatch_delta"]
        and type(row["http_status"]) is int and row["http_status"] == (504 if slow else 200)
        and type(row["connect_timeout_ms"]) is int and row["connect_timeout_ms"] == 250
        and row["client_elapsed_ms"] == observations["wire"]["client_elapsed_ms"])
    budget.finite(row["client_elapsed_ms"], maximum=4000, minimum=0.001)
    if slow:
        require(row["error_code"] == "evaluation_timeout" and type(row["audit_matches"]) is int and row["audit_matches"] == 0
            and row["body_delay_ms"] == observations["wire"]["body_delay_ms"])
        budget.finite(row["body_delay_ms"], maximum=row["client_elapsed_ms"], minimum=550)
    else:
        require(all(sha(row[k]) for k in ("decision_sha256", "context_sha256", "actor_sha256", "policy_sha256", "evaluator_identity_sha256", "audit_event_hash"))
            and row["actor_sha256"] == budget.identifier(expected["principal_id"]) and row["policy_sha256"] == digest(expected["policy"])
            and row["binding_revision"] == expected["binding_revision"] and type(row["binding_revision"]) is int
            and type(row["snapshot_generation"]) is int and row["snapshot_generation"] in expected["snapshot_generations"]
            and row["evaluator_identity_sha256"] == digest(identity()) and row["audit_status"] == "persisted"
            and row["action"] in {"allow", "block"} and type(row["finding_count"]) is int
            and (row["finding_count"] >= 1 if expected["scenario"] == "malicious" else row["finding_count"] == 0 and row["action"] == "allow")
            and observations["wire"]["body_delay_ms"] == 0)
        budget.finite(row["server_elapsed_ms"], maximum=3000, minimum=0.001)
        require(row["server_elapsed_ms"] <= row["client_elapsed_ms"])
        if expected["stage"] == "output":
            require(row["context_sha256"] == request["context_sha256"])


def validate_payload(value, case_id):
    bounded(value)
    require(case_id in CASES and type(value) is dict and set(value) == {"schema_version", "case_id", "invocation", "operation", "status", "phase", "failure_type", "elapsed_ms", "identity_sha256", "sources_sha256", "intent_sha256", "authorization_sha256", "provenance", "binding", "expected", "request", "context", "reader", "governance", "restoration", "invariants"}
        and value["schema_version"] == SCHEMA and value["case_id"] == case_id and value["status"] in {"failed", "observed"}
        and value["phase"] in PHASES and value["failure_type"] in FAILURES | {None}
        and value["identity_sha256"] == digest(identity()) and all(sha(value[k]) for k in ("sources_sha256", "intent_sha256"))
        and (value["authorization_sha256"] is None or sha(value["authorization_sha256"]))
        and value["restoration"] in {"not_needed", "failed", "applied"})
    uuid(value["invocation"])
    budget.finite(value["elapsed_ms"], maximum=2**63 - 1)
    if value["operation"] is not None:
        uuid(value["operation"])
    if value["provenance"] is not None:
        references(value["provenance"])
    if value["binding"] is not None:
        binding(value["binding"])
    expected = value["expected"]
    if expected is not None:
        budget.expectation(expected)
        require((expected["tenant"], expected["stage"], expected["scenario"]) == CASES[case_id] and value["operation"] is not None
            and expected["policy"]["policy_id"] == "eval-" + UUID(value["operation"]).hex and expected["policy"]["version"] == 1)
    sent = value["request"]
    if sent is not None:
        require(type(sent) is dict and set(sent) == {"request_sha256", "correlation_sha256", "context_sha256", "body_sha256"}
            and all(sha(sent[k]) for k in ("request_sha256", "correlation_sha256", "body_sha256"))
            and (sent["context_sha256"] is None or sha(sent["context_sha256"])))
    context = value["context"]
    if context is not None:
        require(type(context) is dict and set(context) == {"context_sha256", "decision_sha256", "audit_event_hash", "policy_sha256", "binding_revision", "snapshot_generation"}
            and all(sha(context[k]) for k in ("context_sha256", "decision_sha256", "audit_event_hash", "policy_sha256"))
            and expected is not None and expected["stage"] == "output" and context["policy_sha256"] == digest(expected["policy"])
            and type(context["binding_revision"]) is int and context["binding_revision"] == expected["binding_revision"]
            and type(context["snapshot_generation"]) is int and context["snapshot_generation"] in expected["snapshot_generations"])
    if value["governance"] is not None:
        require(type(value["governance"]) is dict and set(value["governance"]) == {"intent_sha256", "release_sha256", "restoration_sha256", "completion_sha256"}
                and all(sha(row) for row in value["governance"].values()))
    invariants = value["invariants"]
    require(type(invariants) is dict and set(invariants) <= {"other_binding_sha256", "baseline_sha256", "original_policy_sha256", "restored_policy_sha256"}
        and all(sha(row) for row in invariants.values()))
    complete = value["status"] == "observed"
    reader_value(value["reader"], bound=value["binding"], expected=expected, request=sent, complete=complete)
    if complete:
        require(value["phase"] == "record" and value["failure_type"] is None and value["restoration"] == "applied"
                and value["operation"] is not None and value["binding"] is not None and expected is not None and sent is not None
                and value["governance"] is not None and value["provenance"] is not None and sha(value["authorization_sha256"])
                and set(invariants) == {"other_binding_sha256", "baseline_sha256", "original_policy_sha256", "restored_policy_sha256"}
                and invariants["original_policy_sha256"] == invariants["restored_policy_sha256"])
        budget.finite(value["elapsed_ms"], maximum=900000, minimum=0.001)
        if expected["stage"] == "output":
            require(context is not None and context["context_sha256"] == sent["context_sha256"])
        else:
            require(context is None and sent["context_sha256"] is None)
    else:
        require(value["failure_type"] is not None)
    return value


def validate_sources(root, sources):
    require(root is not None and type(sources) is dict and set(SOURCES) <= set(sources))
    catalog = SourceCatalog(root, sources)
    for name in SOURCES:
        catalog.load(catalog.path(name))


def validate(value, case_id, *, root, sources, complete=False, verify_provenance=None):
    require(type(complete) is bool)
    validate_sources(root, sources); validate_payload(value, case_id)
    require(value["sources_sha256"] == digest(sources) and value["intent_sha256"] == file_digest(intent(value["invocation"], case_id, sources)))
    if complete:
        require(value["status"] == "observed" and callable(verify_provenance))
        expected = authorization(value["invocation"], value["operation"], case_id, value["intent_sha256"], value["provenance"], value["binding"])
        require(value["authorization_sha256"] == file_digest(expected))
        previous, source_previous = canonical(value), canonical(sources)
        proof = verify_provenance(phase="consume", case_id=case_id, invocation=value["invocation"], operation=value["operation"],
            binding=copy.deepcopy(value["binding"]), observation=copy.deepcopy(value), sources=dict(sources))
        require(references(proof) == value["provenance"] and canonical(value) == previous and canonical(sources) == source_previous)
        validate_sources(root, sources)
    return value


def directory(case_id):
    require(case_id in CASES)
    raw = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    require(type(raw) is str and bool(raw))
    path = Path(raw)
    require(path.is_absolute() and path.resolve() == path and path.name == case_id and path.parent.name == "measurements")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    return path, info


def gate_sources(root, case_id, *, deadline):
    check_deadline(deadline)
    path, _ = directory(case_id)
    sources = project_sources(root, read_catalog(path.parents[1] / (case_id + ".sources.json"), root), deadline=deadline)
    validate_sources(root, sources)
    check_deadline(deadline)
    return sources


class EvidenceWriter:
    """Exclusive durable intent/auth/result; held directory and written prefixes."""
    def __init__(self, case_id):
        self.case_id, self.fd, self.written = case_id, None, {}
        self.path, self.before = directory(case_id)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            self.check()
        except BaseException:
            self.close(); raise

    @staticmethod
    def stamp(row):
        return row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns

    def check(self):
        path, info = directory(self.case_id)
        stamp = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_uid)
        require(self.fd is not None and path == self.path and stamp(info) == stamp(self.before) == stamp(os.fstat(self.fd)))
        for name, (expected, hashed) in self.written.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
            with os.fdopen(fd, "rb") as stream:
                require(self.stamp(os.fstat(stream.fileno())) == expected)
                raw = stream.read(MAX_BYTES + 1)
                require(self.stamp(os.fstat(stream.fileno())) == expected == self.stamp(os.stat(name, dir_fd=self.fd, follow_symlinks=False))
                    and len(raw) <= MAX_BYTES and hashlib.sha256(raw).hexdigest() == hashed)

    def write(self, name, value):
        require(name in {INTENT, AUTHORIZATION, FILENAME})
        bounded(value); self.check()
        raw = canonical(value) + b"\n"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid() and info.st_nlink == 1)
            self.written[name] = self.stamp(info), hashlib.sha256(raw).hexdigest()
        os.fsync(self.fd); self.check()
        return self.written[name][1]

    def close(self):
        if self.fd is not None:
            os.close(self.fd); self.fd = None
