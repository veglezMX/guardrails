"""Synthetic resource/counter/provenance composition; no deployment or inference."""
from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from evolution import classifier_resource_measurements as evidence
from evolution.classifier_dispatch import DispatchEvidenceError
from evolution.classifier_resource_evidence import ResourceObservationError
from evolution.registry import EvidenceError
from guardrails.evolution.classifier_contract import identity

from mvp.tests.evolution.e2e import classifier_network_support as network
from mvp.tests.evolution.e2e import classifier_resource_sampling_support as support
from mvp.tests.evolution.unit import (
    test_classifier_resource_evidence as resource_fixture,
)

CASE = "OPS-CLASSIFIER-RESOURCES-TEAM-A-INPUT"
SHA = "a" * 64
PROOF = {name: SHA for name in evidence.PROOFS}


@pytest.fixture(autouse=True)
def no_external_operations(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external operation forbidden in synthetic resource suite")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def kernel(usage=100):
    rows = []
    for index in range(5):
        rows.append({"path": "/" + "a/" * index, "device": 1, "inode": 10 + index,
            "cpu_quota_us": (6 if index == 0 else 2) * 100000, "cpu_period_us": 100000,
            "memory_limit_bytes": (8 if index == 0 else 3) * 1024**3,
            "memory_current_bytes": 1024, "memory_peak_bytes": 2048 if index == 0 else 1024,
            "cpu": {"usage_usec": usage, "user_usec": 50, "system_usec": 50,
                    "nr_periods": 10, "nr_throttled": 1, "throttled_usec": 2}})
    return {"container_id": "c" * 64, "pid": 8, "start_ticks": 99, "ancestors": rows}


def capture(usage=100):
    raw_inventory, values = resource_fixture.fixture()
    inv = support.resources.inventory(raw_inventory, owner=resource_fixture.OWNER, values=values)
    app = next(a for a in inv["applications"] if a["deployment"] == "classifier")
    raw, old_cid, old_pod = resource_fixture.kernel()
    cid, pod = app["containers"][0]["container_id"], app["pod_uid"]
    raw["container_id"] = cid
    raw["process_cgroup"] = raw["process_cgroup"].replace(old_cid, cid).replace(old_pod.replace("-", "_"), pod.replace("-", "_"))
    for row in raw["ancestors"]:
        row["path"] = row["path"].replace(old_cid, cid).replace(old_pod.replace("-", "_"), pod.replace("-", "_"))
        row["cpu.stat"] = row["cpu.stat"].replace("usage_usec 1000", f"usage_usec {usage}")
    identities = resource_fixture.identities_fixture(inv)
    image_sha = support.resources.image_closure(inv, identities)
    return {"binding_sha256": SHA, "images_sha256": image_sha,
        "kernel": support.resources.kernel_sample(raw, cid=cid, pod_uid=pod), "inventory": inv}


def image_binding():
    inv = capture()["inventory"]
    identities = resource_fixture.identities_fixture(inv)
    return {"binding_sha256": SHA, "inventory_sha256": inv["inventory_sha256"], "identities": identities,
            "identities_sha256": support.resources.image_closure(inv, identities)}


def ready(count):
    return {"binding_sha256": SHA, "counter": {"epoch": "b" * 32, "attempts": count},
            "tls": "TLSv1.3", "ready_elapsed_ms": 2}


def bracket(monkeypatch, *, change=None, error=None):
    events = []
    before, after = capture(), capture(150)
    r_before, r_after = ready(3), ready(4)
    if change is not None:
        change(before, after, r_before, r_after)
    observations, samples = iter((r_before, r_after)), iter((before, after))
    def observe():
        events.append("readiness")
        return next(observations)
    def read(**kwargs):
        assert kwargs["deadline"] == 61 and kwargs["image_binding"] == image
        events.append("capture")
        return next(samples)
    def dispatch():
        events.append("dispatch")
        if error is not None:
            raise error
        return sentinel
    monkeypatch.setattr(support.time, "monotonic", lambda: 1)
    image = image_binding()
    progress = {"image_binding": copy.deepcopy(image)}
    sentinel = object()
    run = lambda: support.sample_bracket(SimpleNamespace(sample_locked=observe),
        SimpleNamespace(capture_locked=read), dispatch=dispatch, deadline=100, image_binding=image, progress=progress)
    return run, events, sentinel


def test_bracket_runs_exactly_one_original_dispatch_with_numeric_usage(monkeypatch):
    run, events, sentinel = bracket(monkeypatch)
    result, value = run()
    assert result is sentinel
    assert events == ["capture", "readiness", "dispatch", "readiness", "capture"]
    assert value["usage"]["classifier_cpu_usage_delta_usec"] == 50
    assert value["counter_after"]["attempts"] - value["counter_before"]["attempts"] == 1


@pytest.mark.parametrize("change", [
    lambda b, a, rb, ra: ra["counter"].update(attempts=3),
    lambda b, a, rb, ra: ra["counter"].update(attempts=5),
    lambda b, a, rb, ra: ra["counter"].update(epoch="c" * 32),
    lambda b, a, rb, ra: ra.update(binding_sha256="d" * 64),
    lambda b, a, rb, ra: a.update(binding_sha256="d" * 64),
    lambda b, a, rb, ra: a.update(images_sha256="d" * 64),
    lambda b, a, rb, ra: a["inventory"].update(system_inventory_sha256="d" * 64),
    lambda b, a, rb, ra: a["kernel"].update(pid=9),
    lambda b, a, rb, ra: a["kernel"]["ancestors"][-1].update(inode=55),
    lambda b, a, rb, ra: a["kernel"]["ancestors"][-1]["cpu"].update(usage_usec=100),
    lambda b, a, rb, ra: a["kernel"]["ancestors"][-1]["cpu"].update(throttled_usec=0),
])
def test_counter_identity_inventory_and_kernel_drift_refuse(monkeypatch, change):
    run, events, _ = bracket(monkeypatch, change=change)
    with pytest.raises((EvidenceError, DispatchEvidenceError, ResourceObservationError, network.ClassifierObservationError)):
        run()
    assert events.count("dispatch") == 1


def test_original_request_failure_propagates_without_retry_or_extra_sampling(monkeypatch):
    original = RuntimeError("synthetic request failed")
    run, events, _ = bracket(monkeypatch, error=original)
    with pytest.raises(RuntimeError) as caught:
        run()
    assert caught.value is original and events == ["capture", "readiness", "dispatch"]


def test_expired_budget_refuses_before_first_observation(monkeypatch):
    run, events, _ = bracket(monkeypatch)
    monkeypatch.setattr(support.time, "monotonic", lambda: 101)
    with pytest.raises(EvidenceError):
        run()
    assert events == []


def test_readiness_reset_cannot_extend_active_sixty_second_deadline(monkeypatch):
    observer = SimpleNamespace(deadline=110, resource_deadline=61)
    monkeypatch.setattr(support.time, "monotonic", lambda: 60)
    assert support.bounded_remaining(observer, 50, 900) == 1
    monkeypatch.setattr(support.time, "monotonic", lambda: 61)
    with pytest.raises(EvidenceError):
        support.bounded_remaining(observer, 50, 900)


def test_partial_samples_remain_available_after_request_failure(monkeypatch):
    monkeypatch.setattr(support.time, "monotonic", lambda: 1)
    original = RuntimeError("synthetic request failure")
    observations = {"image_binding": image_binding()}
    observer = SimpleNamespace(resource_deadline=77, sample_locked=lambda: ready(3))
    def dispatch():
        assert observer.resource_deadline == 61
        raise original
    with pytest.raises(RuntimeError) as caught:
        support.sample_bracket(observer, SimpleNamespace(capture_locked=lambda **kw: capture()),
            dispatch=dispatch, deadline=900, image_binding=observations["image_binding"], progress=observations)
    assert caught.value is original and observer.resource_deadline == 77
    assert set(observations) == {"image_binding", "before", "ready_before"}
    evidence.validate_observations(observations)


def test_provenance_is_required_before_any_deployment_construction():
    with pytest.raises(EvidenceError):
        support.run_case("team-a", "input", verify_provenance=None, cluster_factory=None)


def test_verifier_receives_detached_binding_and_sources(monkeypatch):
    monkeypatch.setattr(support.time, "monotonic", lambda: 1)
    binding, sources = {"node": "owned"}, {"module": SHA}
    def verifier(**kwargs):
        assert kwargs["phase"] == "before" and kwargs["operation"] == "operation"
        kwargs["binding"]["node"] = "other"
        kwargs["sources"].clear()
        return PROOF
    actual = support.verify(verifier, phase="before", observer=SimpleNamespace(bound=binding),
                            sources=sources, operation="operation", deadline=5)
    actual["cold"] = "b" * 64
    assert binding == {"node": "owned"} and sources == {"module": SHA} and PROOF["cold"] == SHA


@pytest.mark.parametrize("value", [None, True, {}, {**PROOF, "historical": SHA}, {**PROOF, "cold": True}])
def test_callback_cannot_substitute_flags_or_partial_proof_references(monkeypatch, value):
    monkeypatch.setattr(support.time, "monotonic", lambda: 1)
    with pytest.raises(EvidenceError):
        support.verify(lambda **kw: value, phase="before", observer=SimpleNamespace(bound={}),
                       sources={}, operation="operation", deadline=5)


def payload():
    before, after = capture(), capture(150)
    observations = {"image_binding": image_binding(), "before": before, "after": after,
                    "ready_before": ready(3), "ready_after": ready(4)}
    return {"schema_version": evidence.SCHEMA, "tenant": "team-a", "stage": "input",
        "operation": str(uuid4()), "identity_sha256": evidence.digest(identity()), "provenance": dict(PROOF),
        "binding_sha256": SHA, "images_sha256": before["images_sha256"],
        "inventory_sha256": before["inventory"]["inventory_sha256"],
        "system_inventory_sha256": before["inventory"]["system_inventory_sha256"],
        "before_capture_sha256": evidence.digest(before), "after_capture_sha256": evidence.digest(after),
        "observations": observations,
        "counter_before": ready(3)["counter"], "counter_after": ready(4)["counter"],
        "usage": support.resources.compare_samples(before["kernel"], after["kernel"]),
        "inventory": {"owned_pods": 12, "running_containers": 13, "completed_initializers": 12,
            "cpu_limit_millicores": 5950, "memory_limit_mib": 7936, "node_cpu_limit": 6,
            "node_memory_limit_bytes": 8 * 1024**3, "classifier_cpu_limit": 2,
            "classifier_memory_limit_bytes": 3 * 1024**3},
        "decision": {"request_id": str(uuid4()), "decision_id": str(uuid4()), "audit_id": str(uuid4()),
            "policy_digest": SHA, "binding_revision": 3, "snapshot_generation": 4,
            "evaluator_revision": identity()["evaluator_revision"], "action": "allow"},
        "governance": {name: SHA for name in ("intent_sha256", "release_sha256", "restoration_sha256", "completion_sha256")},
        "elapsed_ms": 1000, "sample_elapsed_ms": 500}


def test_closed_finite_record_accepts_exact_one_sample():
    value = payload()
    assert evidence.validate_payload(value, CASE) is value


@pytest.mark.parametrize("change", [
    lambda v: v.update(tenant="team-b"),
    lambda v: v.update(stage="output"),
    lambda v: v.update(content="never retain"),
    lambda v: v.update(elapsed_ms=float("nan")),
    lambda v: v.update(elapsed_ms=900001),
    lambda v: v.update(sample_elapsed_ms=60001),
    lambda v: v.update(sample_elapsed_ms=float("nan")),
    lambda v: v["usage"].update(classifier_cpu_usage_delta_usec=0),
    lambda v: v["usage"].update(classifier_memory_peak_bytes=3 * 1024**3 + 1),
    lambda v: v["usage"].update(node_memory_peak_bytes=8 * 1024**3 + 1),
    lambda v: v["inventory"].update(node_cpu_limit=True),
    lambda v: v["decision"].update(evaluator_revision="foreign"),
    lambda v: v["decision"].update(action="block"),
    lambda v: v["governance"].pop("restoration_sha256"),
    lambda v: v["provenance"].pop("qualification"),
])
def test_finite_metadata_refuses_missing_proof_or_invalid_usage(change):
    value = payload()
    change(value)
    with pytest.raises((EvidenceError, DispatchEvidenceError, ResourceObservationError)):
        evidence.validate_payload(value, CASE)


@pytest.mark.parametrize("change", [
    lambda v: v["observations"]["before"].update(raw_output="not admitted"),
    lambda v: v["observations"]["before"]["kernel"]["ancestors"][-1].update(memory_limit_bytes=4 * 1024**3),
    lambda v: v["observations"]["image_binding"]["identities"].pop(),
    lambda v: v["observations"]["before"]["inventory"]["applications"][0].update(private_content="no"),
    lambda v: v["observations"]["after"]["kernel"].update(pid=444),
])
def test_success_binds_retained_raw_metadata_and_original_limits(change):
    value = payload()
    change(value)
    with pytest.raises((EvidenceError, DispatchEvidenceError, ResourceObservationError)):
        evidence.validate_payload(value, CASE)


def test_partial_failure_is_retained_but_never_successful_evidence(monkeypatch):
    monkeypatch.setattr(evidence, "validate_sources", lambda **kw: None)
    value = {"schema_version": evidence.SCHEMA, "status": "failed", "tenant": "team-a", "stage": "input",
        "operation": str(uuid4()), "failed_phase": "sampling", "exception_type": "TimeoutError",
        "observations": {"image_binding": image_binding(), "before": capture(), "ready_before": ready(3)}}
    assert evidence.validate(value, CASE, root="synthetic", sources={}, complete=False) == value
    with pytest.raises(EvidenceError):
        evidence.validate(value, CASE, root="synthetic", sources={}, complete=True)


def test_writer_exclusive_private_leaf_and_no_overwrite(tmp_path, monkeypatch):
    path = tmp_path / "measurements" / CASE
    path.mkdir(parents=True, mode=0o700)
    path.chmod(0o700)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(path))
    monkeypatch.setattr(evidence, "validate_sources", lambda **kw: None)
    value = payload()
    evidence.record(value, CASE, root=tmp_path, sources={})
    leaf = path / evidence.FILENAME
    assert leaf.stat().st_mode & 0o777 == 0o600 and json.loads(leaf.read_bytes()) == value
    old = leaf.read_bytes()
    with pytest.raises(FileExistsError):
        evidence.record(value, CASE, root=tmp_path, sources={})
    assert leaf.read_bytes() == old


def test_writer_refuses_replaced_directory_before_leaf_write(tmp_path, monkeypatch):
    path = tmp_path / "measurements" / CASE
    path.mkdir(parents=True, mode=0o700)
    path.chmod(0o700)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(path))
    monkeypatch.setattr(evidence, "validate_sources", lambda **kw: None)
    original = os.open
    def replaced(target, flags, *args, **kwargs):
        fd = original(target, flags, *args, **kwargs)
        if target == path:
            path.rename(path.with_name("retained"))
            path.mkdir(mode=0o700)
            (path / "sentinel").write_bytes(b"foreign")
        return fd
    monkeypatch.setattr(evidence.os, "open", replaced)
    with pytest.raises(EvidenceError):
        evidence.record(payload(), CASE, root=tmp_path, sources={})
    assert {p.name for p in path.iterdir()} == {"sentinel"}
    assert not list(path.with_name("retained").iterdir())


def case_fixture(monkeypatch, *, failure=None, restore_failure=None, reason_codes=(), reason_stage=None, observed_finding=False):
    """Real run_case/finally/bracket, fake governed and deployment boundaries."""
    from mvp.tests.evolution.e2e import classifier_resource_support as reader_module

    events, retained = [], []
    operation = str(uuid4())
    ref = {"tenant_id": "team-a", "policy_id": "approved", "version": 1, "digest": SHA}
    original = {"workload_id": "chat", "state": "observe", "revision": 4}
    actors = {name: {"principal_id": str(uuid4())} for name in
              ("tenant_admin", "author", "approver", "operator", "auditor")}

    class Attempt:
        def __init__(self, *args, **kwargs):
            self.operation = operation
            events.append("intent")
        def finish(self, **kwargs):
            events.append(("finish", kwargs["passed"], kwargs["restoration"]))
        def close(self):
            events.append("journal-close")
            if failure == "journal_close":
                raise original_error

    class Journey:
        last_release = None
        def approve(self, payload):
            events.append("approve")
            return {"author_id": actors["author"]["principal_id"], "rules": []}
        def release(self, **kwargs):
            if kwargs["label"] == "matrix-release":
                events.append("release-ack")
                self.last_release = {"operation": operation, "label": "matrix-release"}
                if failure == "release_poll":
                    raise original_error
            else:
                events.append("restore")
                if restore_failure:
                    raise restore_error
            return {"receipts": [1, 2], "binding_revision": 5}

    class Cluster:
        tenants = ("team-a", "team-b")
        attempts = 3
        evaluations = 0
        def __init__(self):
            self.deployment = SimpleNamespace(lock=self.lock)
            self.endpoints = {"team-a": {"provider": {}, "destination": {}}}
            self.inventory = {"tenants": {"team-a": {"roles": actors, "runtimes": {
                "runtime-1": {"runtime_id": "r1"}, "runtime-2": {"runtime_id": "r2"}}}}}
        @contextmanager
        def lock(self):
            events.append("lock")
            yield
            events.append("unlock")
        def binding(self, tenant):
            return dict(original), dict(ref)
        def baseline(self, tenant):
            return "baseline", {"rules": []}
        def journey(self, *args, **kwargs):
            assert events.index("intent") < len(events)
            return Journey()
        def owned_binding_revision(self, *args):
            events.append("owned-selector")
            if failure == "foreign_restore":
                raise original_error
            return 5
        def assert_governance_audit(self, *args, **kwargs):
            events.append("governance-audit")
        def evaluate(self, tenant, part, *, stage, context_id=None):
            self.evaluations += 1
            events.append("evaluate-" + stage)
            if failure == "request":
                raise original_error
            # The output-only policy's preceding input context dispatches none.
            if stage == selected_stage:
                self.attempts += 1
            decision = SimpleNamespace(action="allow", audit_status="persisted",
                findings=["synthetic injection finding"] if observed_finding and stage == selected_stage else [],
                reason_codes=reason_codes if reason_stage is None or stage == reason_stage else (),
                transformed_parts=None, binding_revision=5, snapshot_generation=1,
                evaluator_revisions=[identity()["evaluator_revision"]],
                request_id=uuid4(), decision_id=uuid4(), context_id=uuid4(),
                policy=SimpleNamespace(digest=SHA, model_dump=lambda **kw: ref))
            return decision, {}
        def assert_exact_audit(self, *args):
            events.append("audit")
            if failure == "audit":
                raise original_error
            return {"event_id": str(uuid4())}
        def assert_private_metadata(self, *args):
            pass
        def close(self):
            events.append("close")

    class Observer:
        bound = None
        def __init__(self, cluster):
            self.cluster = cluster
        def _remaining(self, maximum=10):
            return maximum
        def bind_locked(self):
            self.bound = {"node": "bound"}
        def sample_locked(self):
            self.deadline = 100
            assert self._remaining() > 0
            events.append("ready")
            return ready(self.cluster.attempts)

    class Reader:
        def __init__(self, observer):
            self.reads = 0
        def verify_images_locked(self, **kwargs):
            return image_binding()
        def capture_locked(self, **kwargs):
            self.reads += 1
            events.append("capture")
            return capture(100 if self.reads == 1 else 150)

    original_error, restore_error = RuntimeError("original synthetic failure"), RuntimeError("restore synthetic failure")
    monkeypatch.setattr(network, "ReadinessObserver", Observer)
    monkeypatch.setattr(reader_module, "ResourceReader", Reader)
    monkeypatch.setattr(support, "GovernanceAttempt", Attempt)
    monkeypatch.setattr(support, "operation_root", lambda: "explicit-synthetic-root")
    monkeypatch.setattr(support, "payload_from_approved", lambda *a, **kw: {})
    monkeypatch.setattr(support, "policy_reference", lambda *a: ref)
    monkeypatch.setattr(support, "journal_hashes", lambda *a, **kw: {name: SHA for name in
        ("intent_sha256", "release_sha256", "restoration_sha256", "completion_sha256")})
    monkeypatch.setattr(evidence, "gate_sources", lambda *a, **kw: {})
    monkeypatch.setattr(evidence, "validate_sources", lambda **kw: None)
    clock = iter(index / 1000 for index in range(1, 10000))
    monkeypatch.setattr(support.time, "monotonic", lambda: next(clock))
    def record(value, case_id, **kwargs):
        events.append("record")
        retained.append(copy.deepcopy(value))
        if failure == "retention":
            raise original_error
        evidence.validate(value, case_id, root="synthetic", sources={}, complete=value.get("status") != "failed",
                          verify_provenance=lambda **kwargs: dict(PROOF))
        return value
    monkeypatch.setattr(evidence, "record", record)
    selected_stage = "input"
    def run(stage="input"):
        nonlocal selected_stage
        selected_stage = stage
        return support.run_case("team-a", stage, verify_provenance=lambda **kw: dict(PROOF), cluster_factory=lambda *, deadline: Cluster())
    return run, events, retained, original_error


@pytest.mark.parametrize("reason", ("inspection_incomplete", "queue_full", "evaluator_unavailable", "evaluation_timeout"))
@pytest.mark.parametrize("stage", ("input", "output"))
def test_completed_observe_operational_error_is_failed_resource_sample(monkeypatch, reason, stage):
    run, events, retained, _ = case_fixture(monkeypatch, reason_codes=(reason,), reason_stage=stage)
    with pytest.raises(EvidenceError): run(stage)
    assert "restore" in events and retained[-1]["status"] == "failed"
    assert retained[-1]["failed_phase"] == "audit"
    assert set(retained[-1]["observations"]) == {"image_binding", "before", "ready_before", "ready_after", "after"}
    # Actual counter and CPU observations survive the failed decision guard.
    assert retained[-1]["observations"]["ready_after"]["counter"]["attempts"] == 4


@pytest.mark.parametrize("reason", ("inspection_incomplete", "queue_full", "evaluator_unavailable", "evaluation_timeout"))
def test_output_context_operational_error_refuses_before_measured_dispatch(monkeypatch, reason):
    run, events, retained, _ = case_fixture(monkeypatch, reason_codes=(reason,), reason_stage="input")
    with pytest.raises(EvidenceError): run("output")
    assert "evaluate-output" not in events and "restore" in events
    assert retained[-1]["status"] == "failed" and retained[-1]["failed_phase"] == "context"
    assert set(retained[-1]["observations"]) == {"image_binding"}


@pytest.mark.parametrize("stage", ("input", "output"))
def test_genuine_injection_finding_remains_valid_observe_resource_sample(monkeypatch, stage):
    run, events, retained, _ = case_fixture(monkeypatch, reason_codes=("injection_detected",), reason_stage=stage, observed_finding=True)
    result = run(stage)
    assert result["decision"]["action"] == "allow" and "restore" in events
    assert "status" not in retained[-1]


@pytest.mark.parametrize("stage", ["input", "output"])
def test_whole_case_restores_before_success_and_output_context_precedes_bracket(monkeypatch, stage):
    run, events, retained, _ = case_fixture(monkeypatch)
    value = run(stage)
    assert value is not None and "status" not in retained[0]
    assert events.index("intent") < events.index("release-ack") < events.index("capture")
    assert events.index("owned-selector") < events.index("restore") < events.index("record")
    assert ("finish", True, "applied") in events
    assert events.count("evaluate-" + stage) == 1
    if stage == "output":
        assert events.index("evaluate-input") < events.index("capture")


@pytest.mark.parametrize("failure", ["request", "release_poll", "audit", "foreign_restore"])
def test_case_failure_preserves_original_and_owned_cleanup_authority(monkeypatch, failure):
    run, events, retained, original = case_fixture(monkeypatch, failure=failure)
    with pytest.raises(RuntimeError) as caught:
        run()
    assert caught.value is original and retained[0]["status"] == "failed"
    assert events.count("release-ack") == 1 and events.count("owned-selector") == 1
    assert events.count("restore") == (0 if failure == "foreign_restore" else 1)
    if failure == "request":
        assert set(retained[0]["observations"]) == {"image_binding", "before", "ready_before"}


def test_cleanup_failure_cannot_replace_original_and_retains_partial_evidence(monkeypatch):
    run, events, retained, original = case_fixture(monkeypatch, failure="request", restore_failure=True)
    with pytest.raises(RuntimeError) as caught:
        run()
    assert caught.value is original and any("restoration" in note for note in original.__notes__)
    assert retained[0]["status"] == "failed" and ("finish", False, "failed") in events


def test_retention_failure_cannot_turn_completed_case_into_success(monkeypatch):
    run, events, _retained, original = case_fixture(monkeypatch, failure="retention")
    with pytest.raises(RuntimeError) as caught:
        run()
    assert caught.value is original and events.index("restore") < events.index("record")


def test_first_journal_close_failure_still_closes_client_and_retains_failure(monkeypatch):
    run, events, retained, original = case_fixture(monkeypatch, failure="journal_close")
    with pytest.raises(RuntimeError) as caught:
        run()
    assert caught.value is original and events.index("journal-close") < events.index("close") < events.index("record")
    assert retained[0]["status"] == "failed"


def test_bracket_over_sixty_seconds_refuses_and_preserves_prefix(monkeypatch):
    now = [1]
    monkeypatch.setattr(support.time, "monotonic", lambda: now[0])
    progress = {"image_binding": image_binding()}
    observer = SimpleNamespace(sample_locked=lambda: ready(3))
    def dispatch():
        now[0] = 62
        return "original-result"
    with pytest.raises(EvidenceError):
        support.sample_bracket(observer, SimpleNamespace(capture_locked=lambda **kw: capture()),
            dispatch=dispatch, deadline=900, image_binding=progress["image_binding"], progress=progress)
    assert set(progress) == {"image_binding", "before", "ready_before"}
