"""Synthetic raw-group, collector and durable orchestration composition only."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from evolution import classifier_cni_evidence as cni
from evolution import classifier_cni_measurements as evidence
from evolution.registry import EvidenceError
from guardrails.evolution.classifier_contract import identity

from mvp.tests.evolution.e2e import classifier_cni_acceptance_support as support
from mvp.tests.evolution.e2e import classifier_cni_group as producer
from mvp.tests.evolution.e2e import classifier_network_support as network
from mvp.tests.evolution.e2e import classifier_resource_support as resources
from mvp.tests.evolution.unit.test_classifier_cni_evidence import (
    fixture as binding_fixture,
)
from mvp.tests.evolution.unit.test_classifier_cni_group import objects
from mvp.tests.evolution.unit.test_classifier_resource_sampling import image_binding

SHA = "a" * 64
PROOF = {name: SHA for name in evidence.PROOFS}


@pytest.fixture(autouse=True)
def no_external_operations(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external operation forbidden in synthetic CNI suite")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def observed_group():
    bound = binding_fixture()
    image = image_binding()
    running = [row for row in image["identities"] if "ready" in row]
    for index, peer in enumerate(bound["peers"].values()):
        peer["container_id"] = running[index]["container_id"]
    readiness = {"owner_id": bound["owner_id"], "node_id": bound["node_id"], "api_server": "https://127.0.0.1:44634",
        "files": {"prepared.json": SHA}, "service": {}, "pods": {}}
    for name in ("runtime-1", "classifier"):
        peer = bound["peers"][name]
        readiness["pods"][name] = {**{k: peer[k] for k in ("pod_name", "pod_uid", "pod_ip")},
            "container_id": "containerd://" + peer["container_id"], "image": running[0]["expected_reference"],
            "deployment_uid": "00000000-0000-0000-0000-000000000031", "replica_uid": "00000000-0000-0000-0000-000000000032",
            "restarts": 0, "spec_sha256": SHA}
    raw_network, inv, pods = objects()
    from mvp.tests.evolution.e2e.classifier_cni_bindings import network_projection
    _, projected = network_projection(raw_network, inv, pods, owner=bound["owner_id"])
    service = projected["services"]["classifier_service"]
    readiness["service"] = {"uid": service["uid"], "endpoint_slice_uid": service["endpoint_slice_uid"],
        "address": bound["addresses"]["classifier_service"]}
    bound["readiness_binding_sha256"] = evidence.digest(readiness)
    image["binding_sha256"] = evidence.digest(readiness)
    host = {"process": {"pid": 123, "start_ticks": 456, "netns": "net:[4026531992]"},
        "address": "192.168.51.1", "port": 12345, "device": 9, "inode": 99}
    bound["host_sink"]["identity_sha256"] = evidence.digest(host)
    ready = {"binding_sha256": evidence.digest(readiness), "counter": {"epoch": "b" * 32, "attempts": 12},
        "tls": "TLSv1.3", "ready_elapsed_ms": 3}
    controls = {"ready_before": {name: copy.deepcopy(ready) for name in ("runtime-1", "runtime-2")},
        "ready_after": {name: copy.deepcopy(ready) for name in ("runtime-1", "runtime-2")},
        "dns": [{"position": position, "transport": transport, "source_ip": bound["peers"]["allowed-team-a"]["pod_ip"],
            "server_ip": bound["addresses"]["dns_service"], "resolved_ip": bound["addresses"]["api_service"]}
            for position in ("before", "after") for transport in ("udp", "tcp")]}
    for name, count in (("before", 10), ("positive", 11), ("after", 11), ("final", 12)):
        controls["sink_" + name] = {"counts": {"8443": count, "9443": 0, "8445": 0}, "tls": "TLSv1.3"}
        controls["host_" + name] = count + 10
    value = {"schema_version": "evolution-classifier-cni-group-v1", "status": "observed", "stage": "complete", "elapsed_ms": 40000,
        "binding": bound, "readiness_binding": readiness, "image_binding": image, "host_descriptor": host,
        "closure": {"inventory_sha256": image["inventory_sha256"], "native_images_sha256": image["identities_sha256"],
            "sink_ca_sha256": SHA, "network": projected, "processes": {name: {"pid": index + 10,
                **{key: row[key] for key in ("process_start_ticks", "netns_inode")}} for index, (name, row) in enumerate(bound["peers"].items())}},
        "rows": [{"job_id": job["job_id"], "result": {**job["settings"], "outcome": "timeout", "elapsed_ms": 2000}} for job in cni.jobs(bound)],
        "controls": controls, "cleanup": "closed", "proofs": {}}
    labels = ("before_positive", "after_positive", "after_denials", "after_final_positive")
    sink = {"identity_sha256": evidence.digest(bound["peers"]["sink"]), **dict(zip(labels, (10, 11, 11, 12), strict=True))}
    h = {"identity_sha256": evidence.digest(host), **dict(zip(labels, (20, 21, 21, 22), strict=True))}
    value["proofs"] = {name: cni.validate_group(bound, value["rows"], ready_before=controls["ready_before"][name],
        ready_after=controls["ready_after"][name], owned_sink=sink, host_sink=h, dns_controls=controls["dns"])
        for name in ("runtime-1", "runtime-2")}
    return value


def payload(sources=None):
    sources = sources or {name: SHA for name in evidence.SOURCES}
    group = observed_group()
    operation = str(uuid4())
    intended = evidence.file_digest(evidence.intent(operation, sources))
    auth = evidence.authorization(operation, intended, PROOF, group["readiness_binding"], group["image_binding"])
    return {"schema_version": evidence.SCHEMA, "case_id": evidence.CASE, "operation": operation, "status": "observed",
        "phase": "record", "exception_type": None, "elapsed_ms": 50000, "identity_sha256": evidence.digest(identity()),
        "sources_sha256": evidence.digest(sources), "intent_sha256": intended, "authorization_sha256": evidence.file_digest(auth),
        "provenance": copy.deepcopy(PROOF), "group": group, "group_sha256": evidence.digest(group),
        "isolation_scope": "node_and_cni_composed_host_external_metadata"}


@pytest.mark.parametrize("fault", (None, "extra", "missing_row", "order", "duplicate", "refused", "dispatch1", "dispatch2", "epoch2",
    "no_runtime2", "copied_proof", "bool_proof", "sink_other_port", "host_delivery", "no_positive", "dns_wrong", "dns_missing", "cleanup",
    "late", "nan", "bool_elapsed", "source_process", "same_netns", "host_identity", "service_ip", "image_sha", "image_rows", "readiness_sha"))
def test_complete_group_recomputes_both_runtime_and_all_twenty_rows(fault):
    value = observed_group()
    if fault == "extra": value["unbound"] = True
    elif fault == "missing_row": value["rows"].pop()
    elif fault == "order": value["rows"].reverse()
    elif fault == "duplicate": value["rows"][-1] = copy.deepcopy(value["rows"][0])
    elif fault == "refused": value["rows"][0]["result"]["outcome"] = "refused"
    elif fault in {"dispatch1", "dispatch2"}: value["controls"]["ready_after"]["runtime-" + fault[-1]]["counter"]["attempts"] += 1
    elif fault == "epoch2": value["controls"]["ready_after"]["runtime-2"]["counter"]["epoch"] = "d" * 32
    elif fault == "no_runtime2": del value["controls"]["ready_after"]["runtime-2"]
    elif fault == "copied_proof": value["proofs"]["runtime-2"]["binding_sha256"] = SHA
    elif fault == "bool_proof": value["proofs"]["runtime-2"]["classifier_dispatch_delta"] = False
    elif fault == "sink_other_port": value["controls"]["sink_after"]["counts"]["9443"] += 1
    elif fault == "host_delivery": value["controls"]["host_after"] += 1
    elif fault == "no_positive": value["controls"]["sink_positive"]["counts"]["8443"] -= 1
    elif fault == "dns_wrong": value["controls"]["dns"][0]["resolved_ip"] = "10.96.0.99"
    elif fault == "dns_missing": value["controls"]["dns"].pop()
    elif fault == "cleanup": value["cleanup"] = "failed"
    elif fault in {"late", "nan", "bool_elapsed"}: value["elapsed_ms"] = {"late": 120000.001, "nan": float("nan"), "bool_elapsed": True}[fault]
    elif fault == "source_process": value["closure"]["processes"]["classifier"]["process_start_ticks"] += 1
    elif fault == "same_netns":
        value["binding"]["peers"]["classifier"]["netns_inode"] = value["binding"]["peers"]["sink"]["netns_inode"]
        value["closure"]["processes"]["classifier"]["netns_inode"] = value["binding"]["peers"]["sink"]["netns_inode"]
    elif fault == "host_identity": value["host_descriptor"]["process"]["start_ticks"] += 1
    elif fault == "service_ip": value["closure"]["network"]["services"]["classifier_service"]["spec"]["clusterIP"] = "10.96.0.99"
    elif fault == "image_sha": value["image_binding"]["identities_sha256"] = SHA
    elif fault == "image_rows": value["image_binding"]["identities"].pop()
    elif fault == "readiness_sha": value["readiness_binding"]["files"]["prepared.json"] = "b" * 64
    if fault:
        with pytest.raises((EvidenceError, cni.CniObservationError, ValueError)):
            evidence.group_proofs(value)
    else:
        proofs = evidence.group_proofs(value)
        assert set(proofs) == {"runtime-1", "runtime-2"}
        assert all(p["denial_count"] == 20 and p["classifier_dispatch_delta"] == 0 for p in proofs.values())


def sources_fixture(tmp_path):
    root = tmp_path / "source"
    sources = {}
    for name in evidence.SOURCES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ("# " + name + "\n").encode()
        path.write_bytes(raw)
        sources[name] = hashlib.sha256(raw).hexdigest()
    return root, sources


@pytest.mark.parametrize("fault", (None, "no_verifier", "different_proof", "missing_node", "before_only", "wrong_sources", "source_drift",
    "intent_hash", "authorization_hash", "partial", "raw_hash", "qualification", "scope", "operation"))
def test_collector_requires_current_final_verifier_and_source_and_journal_closure(tmp_path, fault):
    root, sources = sources_fixture(tmp_path)
    value = payload(sources)
    called = []
    def verifier(**kwargs):
        called.append(kwargs)
        assert kwargs["phase"] == "consume" and kwargs["group"] == value["group"]
        result = copy.deepcopy(PROOF)
        if fault == "different_proof": result["qualification"] = "b" * 64
        elif fault == "missing_node": del result["node_boundary"]
        elif fault == "before_only": raise EvidenceError("synthetic missing final invocation")
        return result
    if fault == "wrong_sources": value["sources_sha256"] = "b" * 64
    elif fault == "source_drift": (root / evidence.SOURCES[0]).write_text("# drift\n")
    elif fault == "intent_hash": value["intent_sha256"] = "b" * 64
    elif fault == "authorization_hash": value["authorization_sha256"] = "b" * 64
    elif fault == "partial": value["status"], value["exception_type"] = "failed", "OtherError"
    elif fault == "raw_hash": value["group_sha256"] = "b" * 64
    elif fault == "qualification": del value["provenance"]["qualification"]
    elif fault == "scope": value["isolation_scope"] = "cni_only"
    elif fault == "operation": value["operation"] = "not-a-uuid"
    kwargs = {"root": root, "sources": sources, "complete": True, "verify_provenance": None if fault == "no_verifier" else verifier}
    if fault:
        with pytest.raises((EvidenceError, ValueError)):
            evidence.validate(value, evidence.CASE, **kwargs)
    else:
        assert evidence.validate(value, evidence.CASE, **kwargs) == value
        assert len(called) == 1


@pytest.mark.parametrize("fault", ("detached_arguments", "closure_mutation", "source_drift", "source_map_mutation"))
def test_collector_callback_cannot_change_accepted_value_or_checked_source(tmp_path, fault):
    root, sources = sources_fixture(tmp_path)
    value = payload(sources)
    before = copy.deepcopy(value)
    def verifier(**kwargs):
        kwargs["binding"].clear()
        kwargs["group"]["rows"].clear()
        kwargs["sources"].clear()
        if fault == "closure_mutation": value["group"]["rows"].clear()
        elif fault == "source_drift": (root / evidence.SOURCES[0]).write_text("# drift after initial checks\n")
        elif fault == "source_map_mutation": sources[evidence.SOURCES[0]] = "b" * 64
        return dict(PROOF)
    if fault == "detached_arguments":
        assert evidence.validate(value, evidence.CASE, root=root, sources=sources, complete=True, verify_provenance=verifier) == before
    else:
        with pytest.raises(EvidenceError): evidence.validate(value, evidence.CASE, root=root, sources=sources, complete=True, verify_provenance=verifier)


def directory(tmp_path, monkeypatch):
    path = tmp_path / "run" / "measurements" / evidence.CASE
    path.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(path))
    return path


@pytest.mark.parametrize("fault", (None, "missing_env", "relative", "wrong_name", "public", "symlink", "before_open", "after_open"))
def test_descriptor_writer_requires_explicit_owned_directory_and_never_redirects(tmp_path, monkeypatch, fault):
    path = directory(tmp_path, monkeypatch)
    if fault == "missing_env": monkeypatch.delenv("EVOLUTION_TEST_EVIDENCE_DIR")
    elif fault == "relative": monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", "relative")
    elif fault == "wrong_name": monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(path.parent))
    elif fault == "public": path.chmod(0o755)
    elif fault == "symlink":
        saved = path.with_name("saved"); path.rename(saved); path.symlink_to(saved, target_is_directory=True)
    original = evidence.os.open
    def swapped(target, flags, *args, **kwargs):
        if target == path and flags & os.O_DIRECTORY:
            path.rename(path.with_name("saved")); path.mkdir(mode=0o700); (path / "sentinel").write_bytes(b"foreign")
        return original(target, flags, *args, **kwargs)
    if fault == "before_open": monkeypatch.setattr(evidence.os, "open", swapped)
    if fault not in (None, "after_open"):
        with pytest.raises((EvidenceError, FileNotFoundError)):
            evidence.EvidenceWriter()
        if fault == "before_open": assert (path / "sentinel").read_bytes() == b"foreign" and not (path / evidence.INTENT).exists()
        return
    writer = evidence.EvidenceWriter()
    try:
        if fault == "after_open":
            path.rename(path.with_name("saved")); path.mkdir(mode=0o700); (path / "sentinel").write_bytes(b"foreign")
            with pytest.raises(EvidenceError): writer.write(evidence.INTENT, {"status": "intended"})
            assert (path / "sentinel").read_bytes() == b"foreign" and not (path / evidence.INTENT).exists()
        else:
            value = {"status": "intended"}
            assert writer.write(evidence.INTENT, value) == evidence.file_digest(value)
            assert (path / evidence.INTENT).stat().st_mode & 0o777 == 0o600
            with pytest.raises(FileExistsError): writer.write(evidence.INTENT, value)
    finally:
        writer.close()


def orchestration(tmp_path, monkeypatch, fault):
    path = directory(tmp_path, monkeypatch)
    root, sources = sources_fixture(tmp_path)
    expected = observed_group()
    events, now = [], [100.0]
    monkeypatch.setattr(support, "ROOT", root)
    monkeypatch.setattr(evidence, "gate_sources", lambda _, **kw: sources)
    monkeypatch.setattr(support.time, "monotonic", lambda: now[0])
    primary = RuntimeError("private synthetic failure must not be retained")
    class Cluster:
        def __init__(self):
            assert (path / evidence.INTENT).exists()
            events.append("cluster")
            if fault == "constructor": raise primary
            self.deployment = SimpleNamespace(lock=self.lock)
        @contextmanager
        def lock(self):
            events.append("lock")
            try: yield
            finally: events.append("unlock")
        def close(self):
            events.append("close")
            if fault == "close_deadline": now[0] += 601
            if fault in {"close", "dual"}: raise OSError("private cleanup failure")
    class Observer:
        def __init__(self, cluster): self.bound = None
        def bind_locked(self):
            assert events[-1] == "lock"
            if fault == "binding": raise primary
            self.bound = expected["readiness_binding"]
        def _remaining(self, maximum=10): return maximum
    class Reader:
        def __init__(self, observer): self.observer = observer
        def verify_images_locked(self, *, deadline):
            assert deadline == 400 and self.observer._remaining(500) == 300
            return copy.deepcopy(expected["image_binding"])
    class Runner:
        def __init__(self, observer, image):
            self.observer = observer
            self.value = copy.deepcopy(expected)
            self.value.update(status="failed", stage="setup", rows=[], controls={}, proofs=None, elapsed_ms=0, cleanup="pending")
        def run_locked(self):
            events.append("group")
            assert (path / evidence.AUTHORIZATION).exists()
            assert self.observer._remaining(500) == 120
            if fault in {"group", "dual"}:
                self.value.update(stage="denials", rows=copy.deepcopy(expected["rows"][:5]), elapsed_ms=10000, cleanup="closed")
                raise primary
            now[0] += 40
            self.value = copy.deepcopy(expected)
            if fault == "group_deadline": now[0] += 81
            if fault == "group_cleanup":
                self.value.update(status="failed", cleanup="failed", stage="cleanup")
                raise primary
            return self.value
    monkeypatch.setattr(network, "ReadinessObserver", Observer)
    monkeypatch.setattr(resources, "ResourceReader", Reader)
    monkeypatch.setattr(producer, "CniObservation", Runner)
    def verifier(**kwargs):
        events.append(kwargs["phase"])
        assert events.count("lock") == 1 and "unlock" not in events
        if fault == "provenance_before" and kwargs["phase"] == "before": raise primary
        if fault == "provenance_after" and kwargs["phase"] == "after": raise primary
        result = copy.deepcopy(PROOF)
        if fault == "changed_proof" and kwargs["phase"] == "after": result["node_boundary"] = "b" * 64
        if fault == "source_drift" and kwargs["phase"] == "after": (root / evidence.SOURCES[0]).write_text("# drift\n")
        return result
    verifier.cluster_factory = lambda *, deadline: Cluster()
    return path, sources, expected, events, primary, verifier


@pytest.mark.parametrize("fault", (None, "constructor", "binding", "provenance_before", "group", "group_deadline", "group_cleanup",
    "provenance_after", "changed_proof", "source_drift", "close", "close_deadline", "dual"))
def test_durable_one_group_retains_original_invocation_partial_rows_and_cleanup_failure(tmp_path, monkeypatch, fault):
    path, sources, expected, events, primary, verifier = orchestration(tmp_path, monkeypatch, fault)
    if fault:
        with pytest.raises((RuntimeError, EvidenceError, OSError)) as caught:
            support.run_case(verify_provenance=verifier, cluster_factory=verifier.cluster_factory)
        if fault in {"constructor", "binding", "provenance_before", "group", "group_cleanup", "provenance_after", "dual"}:
            assert caught.value is primary
        value = json.loads((path / evidence.FILENAME).read_bytes())
        assert value["status"] == "failed" and value["exception_type"] is not None
        if fault in {"group", "dual"}: assert value["group"]["rows"] == expected["rows"][:5]
        if fault == "dual": assert caught.value.__notes__ == ["CNI client close also failed: OSError"]
    else:
        value = support.run_case(verify_provenance=verifier, cluster_factory=verifier.cluster_factory)
        assert value["group"] == expected and value["status"] == "observed"
        assert events == ["cluster", "lock", "before", "group", "after", "unlock", "close"]
        evidence.validate_payload(value)
    assert "private synthetic" not in (path / evidence.FILENAME).read_text()
    assert value["intent_sha256"] == hashlib.sha256((path / evidence.INTENT).read_bytes()).hexdigest()
    assert value["sources_sha256"] == evidence.digest(sources)
    if value["provenance"] is not None:
        assert value["provenance"] == PROOF and value["authorization_sha256"] == hashlib.sha256((path / evidence.AUTHORIZATION).read_bytes()).hexdigest()
    if fault != "constructor": assert events.count("close") == 1
    assert events.count("group") <= 1


def test_absent_verifier_refuses_before_source_or_deployment_access(monkeypatch):
    monkeypatch.setattr(evidence, "gate_sources", lambda _, **kw: pytest.fail("must not read before verifier"))
    with pytest.raises(EvidenceError): support.run_case(verify_provenance=None, cluster_factory=None)


def test_group_remaining_cannot_reset_active_120_second_budget(monkeypatch):
    monkeypatch.setattr(support.time, "monotonic", lambda: 219)
    assert support.remaining(500, 220) == 1
    monkeypatch.setattr(support.time, "monotonic", lambda: 220)
    with pytest.raises(EvidenceError): support.remaining(500, 220)


@pytest.mark.parametrize("original_failure", (False, True))
def test_failed_durable_write_preserves_primary_and_closes_descriptor(tmp_path, monkeypatch, original_failure):
    path, _, _, _, primary, verifier = orchestration(tmp_path, monkeypatch, "group" if original_failure else None)
    original_write, original_close = evidence.EvidenceWriter.write, evidence.EvidenceWriter.close
    closes = []
    def write(self, name, value):
        if name == evidence.FILENAME: raise OSError("synthetic failed disk")
        return original_write(self, name, value)
    def close(self):
        closes.append(self.fd)
        original_close(self)
    monkeypatch.setattr(evidence.EvidenceWriter, "write", write)
    monkeypatch.setattr(evidence.EvidenceWriter, "close", close)
    with pytest.raises((RuntimeError, OSError)) as caught:
        support.run_case(verify_provenance=verifier, cluster_factory=verifier.cluster_factory)
    if original_failure:
        assert caught.value is primary
        assert primary.__notes__ == ["CNI evidence retention also failed: OSError"]
    else:
        assert type(caught.value) is OSError
    assert len(closes) == 1 and (path / evidence.INTENT).exists() and (path / evidence.AUTHORIZATION).exists()
    assert not (path / evidence.FILENAME).exists()


def test_replaced_directory_after_group_never_receives_result_or_failure(tmp_path, monkeypatch):
    path, _, _, _, _, original_verifier = orchestration(tmp_path, monkeypatch, None)
    def verifier(**kwargs):
        result = original_verifier(**kwargs)
        if kwargs["phase"] == "after":
            path.rename(path.with_name("saved")); path.mkdir(mode=0o700); (path / "sentinel").write_bytes(b"foreign")
        return result
    with pytest.raises(EvidenceError) as caught:
        support.run_case(verify_provenance=verifier, cluster_factory=original_verifier.cluster_factory)
    assert caught.value.__notes__ == ["CNI evidence retention also failed: EvidenceError"]
    assert list(path.iterdir()) == [path / "sentinel"]
    assert (path.with_name("saved") / evidence.AUTHORIZATION).exists()


@pytest.mark.parametrize("fault", ("deep", "nodes", "unknown_group", "foreign_control"))
def test_failure_prefix_stays_bounded_and_cannot_be_complete(fault):
    value = observed_group()
    value.update(status="failed", stage="denials", rows=value["rows"][:5], proofs=None)
    if fault == "deep":
        nested = None
        for _ in range(26): nested = [nested]
        value["closure"]["network"]["network_policies"] = nested
    elif fault == "nodes": value["closure"]["network"]["network_policies"] = [[0] * 512 for _ in range(100)]
    elif fault == "unknown_group": value["unexpected"] = "not allowed"
    elif fault == "foreign_control": value["controls"]["raw_private_response"] = "not allowed"
    with pytest.raises(EvidenceError): evidence.partial_group(value)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), -1, "x" * 4097, [0] * 513, {"x": object()}))
def test_retained_metadata_has_finite_early_bounds(value):
    with pytest.raises(EvidenceError): evidence.bounded_metadata(value)
