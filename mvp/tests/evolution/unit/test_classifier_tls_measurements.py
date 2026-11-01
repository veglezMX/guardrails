"""Synthetic original-probe envelopes; no sockets, model or deployment calls."""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from evolution import classifier_tls_measurements as evidence
from evolution.classifier_source_projection import NATIVE_ASSET
from evolution.registry import EvidenceError
from guardrails.evolution.classifier_contract import canonical

from mvp.tests.evolution.e2e import classifier_tls_acceptance_support as support
from mvp.tests.evolution.unit import (
    test_classifier_budget_measurements as budget_fixture,
)
from mvp.tests.evolution.unit import test_classifier_operational as component_fixture

SHA = "a" * 64
PROOF = dict.fromkeys(evidence.PROOFS, SHA)
INVOCATION = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def denied(*a, **kw):
        raise AssertionError("Synthetic TLS family forbids external operations")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def bound(runtime="runtime-1"):
    value = budget_fixture.bound()
    if runtime == "runtime-2":
        value["pods"][runtime] = value["pods"].pop("runtime-1")
        value["pods"][runtime]["pod_name"] = value["pods"][runtime]["pod_name"].replace("runtime-1", runtime)
    return value


def component(runtime, binding):
    return {"schema_version": evidence.component.SCHEMA, "runtime": runtime, "status": "passed",
        "binding_sha256": evidence.digest(binding), "identity_sha256": evidence.digest(evidence.identity()),
        "elapsed_ms": 100, "probe": component_fixture.result()}


def source_fixture(tmp_path, module=evidence):
    root = tmp_path / "sources"; sources = {}
    for name in (*module.SOURCES, NATIVE_ASSET):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
        raw = ("# " + name + "\n").encode(); path.write_bytes(raw)
        sources[name] = hashlib.sha256(raw).hexdigest()
    return root, sources


def payload(sources, runtime="runtime-1"):
    case = "SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-" + runtime.upper()
    binding = bound(runtime)
    value = {"schema_version": evidence.SCHEMA, "case_id": case, "invocation": INVOCATION,
        "status": "passed", "phase": "record", "failure_type": None, "elapsed_ms": 1200,
        "identity_sha256": evidence.digest(evidence.identity()), "sources_sha256": evidence.digest(sources),
        "intent_sha256": evidence.file_digest(evidence.intent(INVOCATION, case, sources)),
        "authorization_sha256": None, "provenance": dict(PROOF), "binding": binding,
        "component": component(runtime, binding), "cleanup": "passed"}
    value["authorization_sha256"] = evidence.file_digest(evidence.authorization(value))
    return value


@pytest.mark.parametrize("runtime", ("runtime-1", "runtime-2"))
def test_both_original_cases_keep_eight_exact_unauthorized_zero_dispatch_rows(tmp_path, runtime):
    root, sources = source_fixture(tmp_path); value = payload(sources, runtime)
    assert evidence.validate(value, value["case_id"], root=root, sources=sources, complete=True,
        verify_provenance=lambda **kw: dict(PROOF)) == value
    assert [r["scenario"] for r in value["component"]["probe"]["rows"]] == list(evidence.component.SCENARIOS)


@pytest.mark.parametrize("fault", ("absent", "failed_invocation", "legacy_qualification", "proof", "extra_proof",
    "source_before", "source_after", "map_mutation", "value_mutation", "detached"))
def test_consume_requires_independent_completion_and_current_immutable_sources(tmp_path, fault):
    root, sources = source_fixture(tmp_path); value = payload(sources); original = copy.deepcopy(value)
    def verifier(**kw):
        assert kw["phase"] == "consume" and kw["operation"] is None and kw["invocation"] == INVOCATION
        assert kw["observation"] == original and kw["binding"] == original["binding"]
        if fault in {"failed_invocation", "legacy_qualification"}: raise EvidenceError("synthetic proof refused")
        if fault == "proof": return {**PROOF, "transport": "b" * 64}
        if fault == "extra_proof": return {**PROOF, "unexpected": SHA}
        if fault == "source_after": (root / evidence.SOURCES[0]).write_text("# drift\n")
        if fault == "map_mutation": sources[evidence.SOURCES[0]] = "c" * 64
        if fault == "value_mutation": value["cleanup"] = "failed"
        if fault == "detached": kw["observation"].clear(); kw["binding"].clear(); kw["sources"].clear()
        return dict(PROOF)
    if fault == "source_before": (root / evidence.SOURCES[0]).write_text("# drift\n")
    kwargs = {"root": root, "sources": sources, "complete": True, "verify_provenance": None if fault == "absent" else verifier}
    if fault == "detached": assert evidence.validate(value, value["case_id"], **kwargs) == original
    else:
        with pytest.raises(ValueError): evidence.validate(value, value["case_id"], **kwargs)


@pytest.mark.parametrize("fault", ("foreign_binding", "missing_binding", "foreign_runtime", "missing_probe", "extra_scenario",
    "dispatch", "epoch", "intent", "authorization", "cleanup", "failed", "late", "private"))
def test_outer_family_rejects_forged_or_incomplete_probe_success(tmp_path, fault):
    root, sources = source_fixture(tmp_path); value = payload(sources)
    if fault == "foreign_binding": value["binding"]["node_id"] = "b" * 64
    elif fault == "missing_binding": value["binding"] = None
    elif fault == "foreign_runtime": value["component"]["runtime"] = "runtime-2"
    elif fault == "missing_probe": value["component"]["probe"] = None
    elif fault == "extra_scenario": value["component"]["probe"]["rows"].append(value["component"]["probe"]["rows"][-1])
    elif fault == "dispatch": value["component"]["probe"]["rows"][0]["after_attempts"] += 1
    elif fault == "epoch": value["component"]["probe"]["rows"][-1]["epoch"] = "d" * 32
    elif fault == "intent": value["intent_sha256"] = "c" * 64
    elif fault == "authorization": value["authorization_sha256"] = "c" * 64
    elif fault == "cleanup": value["cleanup"] = "failed"
    elif fault == "failed": value.update(status="failed", failure_type="OtherError")
    elif fault == "late": value["elapsed_ms"] = 240001
    elif fault == "private": value["bearer_token"] = "not retained"
    with pytest.raises(ValueError): evidence.validate(value, value["case_id"], root=root, sources=sources,
        complete=True, verify_provenance=lambda **kw: dict(PROOF))


@pytest.mark.parametrize("fault", (None, "extra", "missing", "intent", "authorization", "result"))
def test_exact_auxiliary_inventory_and_bytes_are_required(tmp_path, fault):
    root, sources = source_fixture(tmp_path); value = payload(sources)
    files = {evidence.INTENT: canonical(evidence.intent(INVOCATION, value["case_id"], sources)) + b"\n",
        evidence.AUTHORIZATION: canonical(evidence.authorization(value)) + b"\n", evidence.FILENAME: canonical(value) + b"\n"}
    if fault == "extra": files["unknown.json"] = b"{}"
    elif fault == "missing": del files[evidence.AUTHORIZATION]
    elif fault: files[{"intent": evidence.INTENT, "authorization": evidence.AUTHORIZATION, "result": evidence.FILENAME}[fault]] += b" "
    if fault:
        with pytest.raises(ValueError): evidence.validate_auxiliaries(value, value["case_id"], files, root=root, sources=sources)
    else: assert set(evidence.validate_auxiliaries(value, value["case_id"], files, root=root, sources=sources)) == set(files)


def composition(tmp_path, monkeypatch, runtime="runtime-1", fault=None):
    root, sources = source_fixture(tmp_path); case = "SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-" + runtime.upper()
    output = tmp_path / "run/measurements" / case; output.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(output))
    monkeypatch.setattr(evidence, "gate_sources", lambda *a, **kw: sources)
    events, clock = [], [1.0]; original = RuntimeError("synthetic private original")
    monkeypatch.setattr(support.time, "monotonic", lambda: clock[0])
    class Cluster:
        def __init__(self, *, deadline):
            assert (output / evidence.INTENT).exists(); events.append("construct")
            if fault == "constructor": raise original
            self.deployment = SimpleNamespace(lock=self.lock)
        @contextmanager
        def lock(self):
            events.append("lock")
            try: yield
            finally: events.append("unlock")
        def close(self):
            events.append("close")
            if fault == "late": clock[0] += 241
            if fault in {"close", "dual"}: raise OSError("synthetic private close")
    class Probe:
        def __init__(self, cluster, selected):
            assert selected == runtime
            self.observer = SimpleNamespace(bound=None, _remaining=lambda maximum=10: maximum)
            self.value = component(runtime, bound(runtime)); self.value.update(status="failed", probe=None, binding_sha256=None)
        def bind_locked(self):
            events.append("bind")
            if fault == "binding": raise original
            self.observer.bound = bound(runtime)
        def run_locked(self):
            events.append("probe"); assert (output / evidence.AUTHORIZATION).exists()
            assert 0 < self.observer._remaining(50) <= 50
            self.value = component(runtime, bound(runtime))
            clock[0] += 1
            if fault in {"probe", "dual"}:
                self.value["status"] = "failed"
                self.value["probe"].update(status="failed", failed_scenario="plaintext", rows=self.value["probe"]["rows"][:3])
                raise original
            return self.value
    def verifier(**kw):
        events.append(kw["phase"])
        assert "lock" in events and "unlock" not in events and kw["operation"] is None
        assert kw["binding"] == bound(runtime) and kw["deadline"] == 241
        if fault == kw["phase"]: raise original
        if fault == "proof_drift" and kw["phase"] == "after": return {**PROOF, "transport": "b" * 64}
        if fault == "output_swap" and kw["phase"] == "before": output.rename(output.with_name("saved")); output.mkdir(mode=0o700)
        if fault == "source_drift" and kw["phase"] == "after": (root / "mvp/evolution/classifier_tls_measurements.py").write_text("# drift\n")
        return dict(PROOF)
    monkeypatch.setattr(support, "TlsObservation", Probe)
    return root, sources, output, Cluster, verifier, events, original


@pytest.mark.parametrize("runtime", ("runtime-1", "runtime-2"))
def test_actual_wrapper_retains_intent_authorization_original_probe_and_cleanup(tmp_path, monkeypatch, runtime):
    root, sources, output, factory, verifier, events, _ = composition(tmp_path, monkeypatch, runtime)
    result = support.run_tls_case(factory, root=root, runtime=runtime, verify_provenance=verifier)
    assert events == ["construct", "lock", "bind", "before", "probe", "after", "unlock", "close"]
    assert json.loads((output / evidence.FILENAME).read_bytes()) == result
    files = {p.name: p.read_bytes() for p in output.iterdir()}
    evidence.validate_auxiliaries(result, result["case_id"], files, root=root, sources=sources)
    assert result["component"]["probe"] == component_fixture.result()


@pytest.mark.parametrize("fault", ("constructor", "binding", "before", "probe", "after", "proof_drift", "close", "dual", "late", "source_drift", "output_swap"))
def test_failure_retains_original_prefix_and_never_retries_or_hides_primary(tmp_path, monkeypatch, fault):
    root, _sources, output, factory, verifier, events, original = composition(tmp_path, monkeypatch, fault=fault)
    with pytest.raises((ValueError, RuntimeError, OSError)) as caught:
        support.run_tls_case(factory, root=root, runtime="runtime-1", verify_provenance=verifier)
    if fault in {"constructor", "binding", "before", "probe", "after", "dual"}: assert caught.value is original
    assert events.count("probe") <= 1 and events.count("construct") == 1
    if fault == "output_swap": assert not list(output.iterdir()); return
    result = json.loads((output / evidence.FILENAME).read_bytes())
    assert result["status"] == "failed" and result["failure_type"] is not None
    assert b"synthetic private" not in (output / evidence.FILENAME).read_bytes()
    if fault in {"probe", "dual"}: assert len(result["component"]["probe"]["rows"]) == 3
    if fault == "dual": assert "TLS client close also failed: OSError" in caught.value.__notes__


def test_missing_callback_refuses_before_source_or_client_access(monkeypatch):
    monkeypatch.setattr(evidence, "gate_sources", lambda *a, **kw: pytest.fail("must not read"))
    with pytest.raises(ValueError): support.run_tls_case(lambda: pytest.fail("must not create"), root=None,
        runtime="runtime-1", verify_provenance=None)
