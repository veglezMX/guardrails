"""Finite saved observations and governed wrapper composition; no live clients."""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from evolution import classifier_budget_measurements as evidence
from evolution.registry import EvidenceError
from guardrails.evolution import models
from guardrails.evolution.classifier_contract import canonical, identity

from mvp.tests.evolution.e2e import classifier_budget_acceptance_support as support
from mvp.tests.evolution.unit import test_classifier_budget_evidence as budget_fixture
from mvp.tests.evolution.unit import test_classifier_network_support as network_fixture

SHA = "a" * 64
PROOF = {name: SHA for name in evidence.PROOFS}
OPERATION = budget_fixture.uid(900)


@pytest.fixture(autouse=True)
def no_external_operations(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("budget family synthetic suite forbids live operations")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def bound():
    projection, _ = network_fixture.support.project_objects(network_fixture.objects(), network_fixture.OWNER, network_fixture.IMAGES, "runtime-1")
    return {"owner_id": network_fixture.OWNER, "node_id": "d" * 64, "api_server": "https://127.0.0.1:44444",
        "files": {key: SHA for key in ("owner.json", "prepared.json", "assembly/access.json", "assembly/values.json",
            "assembly/private/classifier.token", "assembly/tls/gr-platform/classifier/ca.crt")}, **projection}


def records(expected, sent):
    _, _, decision, audit = budget_fixture.fixture(expected["tenant"], expected["stage"], expected["scenario"])
    decision.update(request_id=sent["request_id"], context_id=sent["context_id"] or budget_fixture.uid(5),
        policy=expected["policy"], binding_revision=expected["binding_revision"], snapshot_generation=expected["snapshot_generations"][0])
    audit.update(correlation_id=sent["correlation_id"], actor_id=expected["principal_id"], policy_digest=expected["policy"]["digest"], binding_revision=expected["binding_revision"])
    return decision, audit


def reader(expected, sent, binding):
    before, after = budget_fixture.ready(4), budget_fixture.ready(4 if expected["scenario"] == "slow_body" else 5)
    before["binding_sha256"] = after["binding_sha256"] = evidence.digest(binding)
    arguments = {"expected": expected, "sent": sent, "ready_before": before, "ready_after": after}
    if expected["scenario"] == "slow_body":
        error = budget_fixture.slow_args()[0]
        error["error"]["correlation_id"] = sent["correlation_id"]
        row = evidence.budget.slow_body(error, **arguments, client_elapsed_ms=700, body_delay_ms=600, audit_matches=0)
        wire = {"client_elapsed_ms": 700, "body_delay_ms": 600, "tls": "TLSv1.3"}
    else:
        decision, audit = records(expected, sent)
        row = evidence.budget.normal(decision, **arguments, audit=audit, client_elapsed_ms=150)
        wire = {"client_elapsed_ms": 150, "body_delay_ms": 0, "tls": "TLSv1.3"}
    return {"status": "observed", "phase": "complete", "elapsed_ms": 1000, "row": row,
        "observations": {"transport_pins": {"ca_sha256": SHA, "token_sha256": "b" * 64}, "ready_before": before, "wire": wire, "ready_after": after}}


def payload(tenant="team-a", stage="input", scenario="benign", sources=None):
    case_id = next(key for key, value in evidence.CASES.items() if value == (tenant, stage, scenario))
    expected, sent, _, _ = budget_fixture.fixture(tenant, stage, scenario)
    expected["policy"]["policy_id"], expected["policy"]["version"] = "eval-" + OPERATION.replace("-", ""), 1
    binding = bound()
    sources = sources or {name: SHA for name in evidence.SOURCES}
    invocation = budget_fixture.uid(901)
    intended = evidence.file_digest(evidence.intent(invocation, case_id, sources))
    context = None if stage == "input" else {"context_sha256": evidence.budget.identifier(sent["context_id"]),
        "decision_sha256": SHA, "audit_event_hash": SHA, "policy_sha256": evidence.digest(expected["policy"]),
        "binding_revision": 3, "snapshot_generation": 4}
    value = {"schema_version": evidence.SCHEMA, "case_id": case_id, "invocation": invocation, "operation": OPERATION,
        "status": "observed", "phase": "record", "failure_type": None, "elapsed_ms": 12000,
        "identity_sha256": evidence.digest(identity()), "sources_sha256": evidence.digest(sources), "intent_sha256": intended,
        "authorization_sha256": evidence.file_digest(evidence.authorization(invocation, OPERATION, case_id, intended, PROOF, binding)),
        "provenance": dict(PROOF), "binding": binding, "expected": expected, "request": evidence.request_reference(sent),
        "context": context, "reader": reader(expected, sent, binding),
        "governance": dict.fromkeys(("intent_sha256", "release_sha256", "restoration_sha256", "completion_sha256"), SHA),
        "restoration": "applied", "invariants": dict.fromkeys(("other_binding_sha256", "baseline_sha256", "original_policy_sha256", "restored_policy_sha256"), SHA)}
    return value


@pytest.mark.parametrize("tenant,stage,scenario", tuple(evidence.CASES.values()))
def test_twelve_closed_original_rows_keep_exact_budget_and_context(tenant, stage, scenario):
    value = payload(tenant, stage, scenario)
    assert evidence.validate_payload(value, value["case_id"]) is value
    assert value["reader"]["row"]["dispatch_delta"] == (0 if scenario == "slow_body" else 1)


@pytest.mark.parametrize("fault", ("raw", "bad_case", "no_proof", "no_transport", "no_operation", "wrong_identity", "no_restore",
    "restore_mismatch", "no_journal", "late_case", "bool_elapsed", "reader_late", "reader_failed", "reader_raw", "wire_tls", "wire_late",
    "connect", "server_late", "server_bool", "server_after_client", "spooled", "action", "finding", "counter", "epoch", "context", "context_absent",
    "policy", "snapshot", "actor", "request", "correlation", "private_transport", "binding_owner", "binding_node", "api", "file_pin", "pod_image", "pod_id", "pod_raw"))
def test_complete_metadata_rejects_uncorrelated_unbounded_or_failed_claims(fault):
    value = payload(stage="output")
    observed = value["reader"]; row = observed["row"]
    if fault == "raw": value["request_body"] = "forbidden"
    elif fault == "bad_case": value["case_id"] = "foreign"
    elif fault == "no_proof": value["provenance"] = None
    elif fault == "no_transport": del value["provenance"]["transport"]
    elif fault == "no_operation": value["operation"] = None
    elif fault == "wrong_identity": value["identity_sha256"] = "b" * 64
    elif fault == "no_restore": value["restoration"] = "not_needed"
    elif fault == "restore_mismatch": value["invariants"]["restored_policy_sha256"] = "c" * 64
    elif fault == "no_journal": value["governance"] = None
    elif fault == "late_case": value["elapsed_ms"] = 900000.1
    elif fault == "bool_elapsed": value["elapsed_ms"] = True
    elif fault == "reader_late": observed["elapsed_ms"] = 60000.1
    elif fault == "reader_failed": observed["status"] = "failed"
    elif fault == "reader_raw": observed["observations"]["wire"]["raw"] = "forbidden"
    elif fault == "wire_tls": observed["observations"]["wire"]["tls"] = "TLSv1.1"
    elif fault == "wire_late": observed["observations"]["wire"]["client_elapsed_ms"] = row["client_elapsed_ms"] = 4000.1
    elif fault == "connect": row["connect_timeout_ms"] = 251
    elif fault == "server_late": row["server_elapsed_ms"] = 3000.1
    elif fault == "server_bool": row["server_elapsed_ms"] = True
    elif fault == "server_after_client": row["server_elapsed_ms"] = 200
    elif fault == "spooled": row["audit_status"] = "spooled"
    elif fault == "action": row["action"] = "warn"
    elif fault == "finding": row["finding_count"] = 1
    elif fault == "counter": observed["observations"]["ready_after"]["counter"]["attempts"] += 1
    elif fault == "epoch": observed["observations"]["ready_after"]["counter"]["epoch"] = "d" * 32
    elif fault == "context": row["context_sha256"] = "d" * 64
    elif fault == "context_absent": value["context"] = None
    elif fault == "policy": row["policy_sha256"] = "d" * 64
    elif fault == "snapshot": row["snapshot_generation"] = 99
    elif fault == "actor": row["actor_sha256"] = "d" * 64
    elif fault == "request": row["request_sha256"] = "d" * 64
    elif fault == "correlation": row["correlation_sha256"] = "d" * 64
    elif fault == "private_transport": observed["observations"]["transport_pins"]["token"] = "private"
    elif fault == "binding_owner": value["binding"]["owner_id"] = "foreign"
    elif fault == "binding_node": value["binding"]["node_id"] = "foreign"
    elif fault == "api": value["binding"]["api_server"] = "https://example.com:44444"
    elif fault == "file_pin": value["binding"]["files"]["prepared.json"] = "foreign"
    elif fault == "pod_image": value["binding"]["pods"]["classifier"]["image"] = "unbound:latest"
    elif fault == "pod_id": value["binding"]["pods"]["classifier"]["pod_uid"] = "bad"
    elif fault == "pod_raw": value["binding"]["pods"]["classifier"]["raw"] = "private"
    with pytest.raises((ValueError, KeyError)):
        evidence.validate_payload(value, payload()["case_id"] if fault == "bad_case" else value["case_id"])


@pytest.mark.parametrize("fault", ("delay_early", "delay_after_client", "audit", "code", "status", "dispatch", "bool_audit"))
def test_slow_body_cannot_be_substituted_with_a_success_or_inference(fault):
    value = payload(scenario="slow_body"); row = value["reader"]["row"]; wire = value["reader"]["observations"]["wire"]
    if fault == "delay_early": row["body_delay_ms"] = wire["body_delay_ms"] = 549
    elif fault == "delay_after_client": row["body_delay_ms"] = wire["body_delay_ms"] = 701
    elif fault == "audit": row["audit_matches"] = 1
    elif fault == "bool_audit": row["audit_matches"] = False
    elif fault == "code": row["error_code"] = "evaluator_unavailable"
    elif fault == "status": row["http_status"] = 200
    elif fault == "dispatch": row["dispatch_delta"] = 1
    with pytest.raises(ValueError): evidence.validate_payload(value, value["case_id"])


def source_fixture(tmp_path):
    root = tmp_path / "sources"; sources = {}
    for name in evidence.SOURCES:
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        raw = ("# " + name + "\n").encode(); path.write_bytes(raw)
        sources[name] = hashlib.sha256(raw).hexdigest()
    return root, sources


@pytest.mark.parametrize("fault", (None, "missing", "changed_proof", "legacy_qualification", "source", "source_after", "value_mutation", "map_mutation", "detached",
    "intent", "authorization", "failed"))
def test_consumption_requires_authentic_final_proof_and_immutable_inputs(tmp_path, fault):
    root, sources = source_fixture(tmp_path); value = payload(sources=sources); before = copy.deepcopy(value)
    def verifier(**kwargs):
        assert kwargs["phase"] == "consume" and kwargs["observation"] == before and kwargs["invocation"] == before["invocation"]
        if fault == "changed_proof": return {**PROOF, "transport": "b" * 64}
        if fault == "legacy_qualification": raise EvidenceError("synthetic final qualification absent")
        if fault == "source_after": (root / evidence.SOURCES[0]).write_text("# drift\n")
        if fault == "value_mutation": value["reader"]["row"]["http_status"] = 503
        if fault == "map_mutation": sources[evidence.SOURCES[0]] = "c" * 64
        if fault == "detached": kwargs["observation"].clear(); kwargs["binding"].clear(); kwargs["sources"].clear()
        return dict(PROOF)
    if fault == "source": (root / evidence.SOURCES[0]).write_text("# drift\n")
    elif fault == "intent": value["intent_sha256"] = "e" * 64
    elif fault == "authorization": value["authorization_sha256"] = "e" * 64
    elif fault == "failed": value.update(status="failed", failure_type="OtherError")
    kwargs = {"root": root, "sources": sources, "complete": True, "verify_provenance": None if fault == "missing" else verifier}
    if fault not in (None, "detached"):
        with pytest.raises(ValueError): evidence.validate(value, value["case_id"], **kwargs)
    else:
        assert evidence.validate(value, value["case_id"], **kwargs) == before


def directory(tmp_path, monkeypatch, case_id):
    path = tmp_path / "run/measurements" / case_id; path.mkdir(mode=0o700, parents=True)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(path))
    return path


@pytest.mark.parametrize("fault", (None, "missing_env", "public", "symlink", "after_open", "prefix_changed", "prefix_replaced", "prefix_public"))
def test_durable_writer_never_redirects_or_adopts_a_changed_prefix(tmp_path, monkeypatch, fault):
    case_id = next(iter(evidence.CASES)); path = directory(tmp_path, monkeypatch, case_id)
    if fault == "missing_env": monkeypatch.delenv("EVOLUTION_TEST_EVIDENCE_DIR")
    elif fault == "public": path.chmod(0o755)
    elif fault == "symlink": path.rename(path.with_name("saved")); path.symlink_to(path.with_name("saved"), target_is_directory=True)
    if fault in {"missing_env", "public", "symlink"}:
        with pytest.raises(ValueError): evidence.EvidenceWriter(case_id)
        return
    writer = evidence.EvidenceWriter(case_id)
    try:
        writer.write(evidence.INTENT, {"original": True})
        if fault == "after_open": path.rename(path.with_name("saved")); path.mkdir(mode=0o700)
        elif fault == "prefix_changed": (path / evidence.INTENT).write_bytes(b"private foreign")
        elif fault == "prefix_replaced": (path / evidence.INTENT).rename(path / "saved"); (path / evidence.INTENT).write_bytes(b"foreign")
        elif fault == "prefix_public": (path / evidence.INTENT).chmod(0o644)
        if fault:
            with pytest.raises(ValueError): writer.write(evidence.AUTHORIZATION, {"later": True})
            assert not (path / evidence.AUTHORIZATION).exists()
        else:
            assert (path / evidence.INTENT).stat().st_mode & 0o777 == 0o600
            with pytest.raises(FileExistsError): writer.write(evidence.INTENT, {"replay": True})
            assert writer.write(evidence.AUTHORIZATION, {"later": True}) == evidence.file_digest({"later": True})
    finally:
        writer.close()


def orchestration(tmp_path, monkeypatch, tenant, stage, scenario, fault=None):
    case_id = next(key for key, coordinates in evidence.CASES.items() if coordinates == (tenant, stage, scenario))
    path = directory(tmp_path, monkeypatch, case_id); root, sources = source_fixture(tmp_path)
    operations = tmp_path / "operations"; operations.mkdir(mode=0o700)
    monkeypatch.setattr(support, "ROOT", root)
    monkeypatch.setattr(evidence, "gate_sources", lambda *args, **kwargs: sources)
    monkeypatch.setattr(support, "operation_root", lambda: operations)
    events, now = [], [100.0]
    monkeypatch.setattr(support.time, "monotonic", lambda: now[0])
    failure = RuntimeError("synthetic private failure must never be retained")
    policy = {"tenant_id": tenant, "policy_id": "original", "version": 1, "compiled_digest": SHA,
        "rules": [{"rule_id": "mandatory", "mandatory": True}], "author_id": budget_fixture.uid(12)}
    original = {"tenant_id": tenant, "workload_id": "chat", "revision": 1, "state": "block", "policy": support.policy_reference(policy)}
    current = [copy.deepcopy(original), copy.deepcopy(policy)]
    baseline, other = {"baseline": SHA}, {"other": SHA}
    captured = {}
    roles = {name: {"principal_id": budget_fixture.uid(index)} for index, name in enumerate(("workload", "tenant_admin", "author", "approver", "operator", "auditor"), start=1)}
    item = {"roles": roles, "workload": {"workload_id": "chat"}, "runtimes": {str(i): {"runtime_id": budget_fixture.uid(i + 30)} for i in (1, 2)},
        "profiles": {"mock": {"provider": {"provider_id": budget_fixture.uid(40)}}}}
    class Attempt:
        def __init__(self, parent, **kwargs):
            events.append("attempt")
            assert parent == operations and kwargs["state"] == "observe" and kwargs["engine"] == "injection"
            assert kwargs["original_binding"] == original and set(kwargs["sources"]) == set(support.JOURNAL_SOURCES)
            self.operation = OPERATION; self.path = parent / OPERATION; self.path.mkdir(mode=0o700)
            self.pending = None
            self.write("intent.json", {"operation": OPERATION})
        def write(self, name, value):
            target = self.path / name; target.write_bytes(canonical(value) + b"\n"); target.chmod(0o600)
        def check(self): pass
        def finish(self, *, passed, restoration):
            events.append("finish")
            if fault == "journal": raise failure
            self.write("completion.json", {"operation": OPERATION, "status": "case_passed" if passed else "case_failed",
                "restoration": restoration, "last_request_outcome_unknown": False})
        def close(self):
            events.append("attempt_close")
            if fault == "attempt_close": raise failure
    class Journey:
        def __init__(self, journal): self.last_release = None; self.journal = journal
        def approve(self, payload):
            events.append("approve")
            assert (path / evidence.AUTHORIZATION).exists()
            if fault == "approve": raise failure
            return {**copy.deepcopy(policy), "policy_id": "eval-" + OPERATION.replace("-", ""),
                "rules": payload["rules"], "compiled_digest": "b" * 64, "author_id": roles["author"]["principal_id"]}
        def release(self, *, policy, workload_id, state, expected_revision, runtime_ids, label):
            events.append(label)
            assert workload_id == "chat" and expected_revision == current[0]["revision"]
            if label == "restore-original-binding" and fault == "restore": raise failure
            self.last_release = {"operation": OPERATION, "label": label, "binding_revision": expected_revision + 1}
            current[:] = [{**copy.deepcopy(original), "policy": support.policy_reference(policy), "state": state, "revision": expected_revision + 1}, copy.deepcopy(policy)]
            self.journal.write("release-" + label + ".json", {"handle": self.last_release})
            if label == "matrix-release" and fault in {"release_ack", "release_poll"}: raise failure
            return {"binding_revision": expected_revision + 1, "receipts": [{"runtime_id": runtime, "snapshot_generation": i + 4} for i, runtime in enumerate(runtime_ids)]}
    class Cluster:
        def __init__(self):
            assert (path / evidence.INTENT).exists()
            events.append("cluster")
            if fault == "constructor": raise failure
            self.inventory = {"tenants": {tenant: item}}; self.deployment = SimpleNamespace(lock=self.lock)
            self.endpoints = {tenant: {"provider": {"synthetic": "provider"}, "destination": {"synthetic": "destination"}}}
        @contextmanager
        def lock(self):
            events.append("lock")
            try: yield
            finally: events.append("unlock")
        def binding(self, selected):
            if selected != tenant: return copy.deepcopy(other), None
            return copy.deepcopy(current)
        def baseline(self, selected): return copy.deepcopy(baseline), copy.deepcopy(policy)
        def journey(self, selected, operation, *, journal):
            assert selected == tenant and operation == OPERATION
            return Journey(journal)
        def assert_governance_audit(self, *a, **kw): events.append("governance_audit")
        def owned_binding_revision(self, selected, handle):
            events.append("owned_revision")
            assert selected == tenant and handle["operation"] == OPERATION and handle["label"] == "matrix-release"
            if fault == "restore_drift": raise failure
            return handle["binding_revision"]
        def evaluate(self, selected, part, *, stage):
            events.append("context")
            assert "sample" not in events and stage == "input" and selected == tenant
            expected, sent, _, _ = budget_fixture.fixture(tenant=tenant)
            expected.update(policy=support.policy_reference(current[1]), binding_revision=current[0]["revision"], principal_id=roles["workload"]["principal_id"])
            decision, audit = records(expected, sent)
            if fault == "context_error": decision["reason_codes"] = ["evaluation_timeout"]
            captured["context_audit"] = audit
            return models.Decision.model_validate_json(canonical(decision)), sent
        def assert_exact_audit(self, *args): events.append("context_audit"); return captured["context_audit"]
        def assert_private_metadata(self, marker): events.append("private_metadata"); assert marker.startswith("budget-canary-")
        def close(self):
            events.append("cluster_close")
            if fault == "close_late": now[0] += 901
            if fault in {"cluster_close", "dual"}: raise OSError("synthetic private close failure")
    class Observer:
        def __init__(self, cluster): self.bound = None; self.cluster = cluster
        def bind_locked(self):
            events.append("bind")
            if fault == "binding": raise failure
            self.bound = bound()
        def _remaining(self, maximum=10): return maximum
    class Reader:
        def __init__(self, observer, *, recheck_transport):
            self.observer, self.recheck = observer, recheck_transport
            self.value = {"status": "unavailable", "phase": "not_started", "observations": {}}
        def sample_locked(self, sent, *, expected, deadline):
            events.append("sample"); assert deadline == 1000
            captured["sent"], captured["expected"] = copy.deepcopy(sent), copy.deepcopy(expected)
            assert sent["parts"] == [support.content("injection", stage, sent["parts"][0]["text"].rsplit(" ", 1)[1], clean=scenario != "malicious")]
            assert sent["deadline_ms"] == 3000
            self.value.update(phase="binding")
            self.recheck(deadline=160)
            observed = reader(expected, sent, self.observer.bound)
            if fault in {"request", "dual"}:
                self.value = {"status": "failed", "phase": "request", "failure_type": "OtherError", "observations": {
                    key: val for key, val in observed["observations"].items() if key in {"transport_pins", "ready_before"}}}
                raise failure
            self.value = observed
            self.recheck(deadline=160)
            if fault == "other_drift": other["other"] = "foreign"
            now[0] += 1
            return copy.deepcopy(self.value)
    def payload_from_approved(base, rule, *, replacements):
        assert base == policy and rule == support.control("injection", stage)
        assert rule["config"]["threshold"] == .90
        assert replacements == (("providers", {"synthetic": "provider"}), ("resolved_destinations", {"synthetic": "destination"}))
        return {"rules": [*copy.deepcopy(base["rules"]), rule]}
    def verifier(**kwargs):
        events.append(kwargs["phase"])
        assert events.count("lock") == 1 and "unlock" not in events
        assert kwargs["case_id"] == case_id and kwargs["operation"] == OPERATION and kwargs["binding"] == bound()
        if fault == "proof_before" and kwargs["phase"] == "before": raise failure
        if fault == "transport_before" and kwargs["phase"] == "transport_before": raise failure
        if fault == "transport_after" and kwargs["phase"] == "transport_after": raise failure
        if fault == "proof_after" and kwargs["phase"] == "after": raise failure
        if fault == "proof_restored" and kwargs["phase"] == "restored": raise failure
        if fault == "source_drift" and kwargs["phase"] == "after": (root / evidence.SOURCES[0]).write_text("# drift\n")
        return dict(PROOF)
    monkeypatch.setattr(support, "GovernanceAttempt", Attempt)
    monkeypatch.setattr(support, "BudgetObserver", Observer)
    monkeypatch.setattr(support, "BudgetReader", Reader)
    monkeypatch.setattr(support, "payload_from_approved", payload_from_approved)
    verifier.cluster_factory = lambda *, deadline: Cluster()
    return path, events, verifier, failure, captured, current, original


@pytest.mark.parametrize("tenant,stage,scenario", tuple(evidence.CASES.values()))
def test_real_wrapper_keeps_all_twelve_original_payloads_and_restores_before_observation(tmp_path, monkeypatch, tenant, stage, scenario):
    path, events, verifier, _, captured, current, original = orchestration(tmp_path, monkeypatch, tenant, stage, scenario)
    value = support.run_case(tenant, stage, scenario, verify_provenance=verifier, cluster_factory=verifier.cluster_factory)
    assert value["status"] == "observed" and value["restoration"] == "applied"
    assert value == json.loads((path / evidence.FILENAME).read_bytes())
    assert current[0]["policy"] == original["policy"] and current[0]["state"] == original["state"] and current[0]["revision"] == 3
    assert events.count("sample") == 1 and events.count("matrix-release") == events.count("restore-original-binding") == 1
    assert events.index("before") < events.index("approve") < events.index("sample") < events.index("restore-original-binding") < events.index("finish")
    assert events[-3:] == ["unlock", "attempt_close", "cluster_close"]
    assert ("context" in events) == (stage == "output")
    if stage == "output": assert events.index("context_audit") < events.index("sample") and captured["sent"]["context_id"] is not None
    assert value["request"]["body_sha256"] == evidence.digest(captured["sent"])
    assert b"budget-canary-" not in (path / evidence.FILENAME).read_bytes()
    assert value["intent_sha256"] == hashlib.sha256((path / evidence.INTENT).read_bytes()).hexdigest()
    assert value["authorization_sha256"] == hashlib.sha256((path / evidence.AUTHORIZATION).read_bytes()).hexdigest()


@pytest.mark.parametrize("fault", ("constructor", "binding", "proof_before", "approve", "release_ack", "release_poll", "context_error",
    "transport_before", "transport_after", "request", "other_drift", "proof_after", "proof_restored", "source_drift", "restore",
    "restore_drift", "journal", "attempt_close", "cluster_close", "close_late", "dual"))
def test_real_wrapper_retains_failure_prefix_and_owned_cleanup_without_replaying(tmp_path, monkeypatch, fault):
    path, events, verifier, failure, _, _, _ = orchestration(tmp_path, monkeypatch, "team-a", "output", "benign", fault)
    with pytest.raises((RuntimeError, ValueError, OSError)) as caught:
        support.run_case("team-a", "output", "benign", verify_provenance=verifier, cluster_factory=verifier.cluster_factory)
    if fault in {"constructor", "binding", "proof_before", "approve", "release_ack", "release_poll", "transport_before", "transport_after", "request", "proof_after", "proof_restored", "restore", "restore_drift", "journal", "attempt_close", "dual"}:
        assert caught.value is failure
    value = json.loads((path / evidence.FILENAME).read_bytes())
    assert value["status"] == "failed" and value["failure_type"] is not None
    assert "synthetic private" not in (path / evidence.FILENAME).read_text()
    assert events.count("sample") <= 1 and events.count("restore-original-binding") <= 1
    if fault in {"request", "dual"}: assert set(value["reader"]["observations"]) == {"transport_pins", "ready_before"}
    if fault in {"release_ack", "release_poll", "request", "transport_after"}: assert value["restoration"] == "applied"
    if fault == "restore_drift": assert "restore-original-binding" not in events and value["restoration"] == "failed"
    if fault == "attempt_close": assert events[-2:] == ["attempt_close", "cluster_close"]
    if fault == "dual": assert caught.value.__notes__ == ["Budget client close also failed: OSError"]


@pytest.mark.parametrize("original_failure", (False, True))
def test_final_write_failure_closes_descriptor_and_preserves_original(tmp_path, monkeypatch, original_failure):
    path, _, verifier, failure, _, _, _ = orchestration(tmp_path, monkeypatch, "team-a", "input", "benign", "request" if original_failure else None)
    original_write, original_close = evidence.EvidenceWriter.write, evidence.EvidenceWriter.close
    closed = []
    def write(self, name, value):
        if name == evidence.FILENAME: raise OSError("synthetic private disk failure")
        return original_write(self, name, value)
    def close(self): closed.append(self.fd); original_close(self)
    monkeypatch.setattr(evidence.EvidenceWriter, "write", write); monkeypatch.setattr(evidence.EvidenceWriter, "close", close)
    with pytest.raises((OSError, RuntimeError)) as caught: support.run_case("team-a", "input", "benign", verify_provenance=verifier, cluster_factory=verifier.cluster_factory)
    if original_failure:
        assert caught.value is failure and failure.__notes__ == ["Budget evidence retention also failed: OSError"]
    assert len(closed) == 1 and not (path / evidence.FILENAME).exists()
    assert (path / evidence.INTENT).exists() and (path / evidence.AUTHORIZATION).exists()


def test_missing_final_verifier_refuses_before_any_source_or_deployment_access(monkeypatch):
    monkeypatch.setattr(evidence, "gate_sources", lambda *a, **kw: pytest.fail("source access before verifier"))
    with pytest.raises(ValueError): support.run_case("team-a", "input", "benign", verify_provenance=None, cluster_factory=None)


@pytest.mark.parametrize("value", (float("nan"), -1, "x" * 4097, [0] * 257, {"token": object()}))
def test_retention_bounds_reject_nonfinite_or_unbounded_values(value):
    with pytest.raises(ValueError): evidence.bounded(value)
