"""Synthetic-only tests for deployed TLS/auth producers and closed evidence."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from evolution import classifier_operational_measurements as evidence
from evolution.registry import EvidenceError
from guardrails.evolution.classifier_contract import canonical, identity

from mvp.tests.evolution.e2e import classifier_operational_support as support


def result():
    rows = []
    for name in evidence.SCENARIOS:
        if name == "ready": outcome, status, tls, code = "ready", 200, "TLSv1.3", 0
        elif name in {"wrong_ca", "wrong_sni"}: outcome, status, tls, code = "certificate_rejected", 0, None, 20 if name == "wrong_ca" else 62
        elif name == "plaintext": outcome, status, tls, code = "plaintext_closed", 0, None, 0
        else: outcome, status, tls, code = "unauthorized", 401, "TLSv1.3", 0
        rows.append({"scenario": name, "outcome": outcome, "http_status": status, "tls": tls, "verify_code": code,
            "elapsed_ms": 1.0, "epoch": "a" * 32, "before_attempts": 7, "after_attempts": 7})
    return {"schema_version": evidence.PROBE_SCHEMA, "status": "passed", "failed_scenario": None, "rows": rows}


def test_closed_probe_requires_every_ordered_negative_with_authenticated_unchanged_counter():
    assert evidence.validate_probe(result(), complete=True) == result()


@pytest.mark.parametrize("fault", ["missing", "reordered", "extra", "unknown_field", "secret_field", "wrong_status", "false_status",
    "wrong_tls", "unknown_ca_code", "hostname_other_error", "plaintext_accepted", "one_dispatch", "counter_regression",
    "epoch_change", "between_dispatch", "counter_bool", "duration_nan", "duration_bool", "duration_limit", "failed", "partial"])
def test_closed_probe_refuses_partial_ambiguous_or_inconsistent_observations(fault):
    value = result()
    if fault == "missing": value["rows"].pop()
    elif fault == "reordered": value["rows"][1], value["rows"][2] = value["rows"][2], value["rows"][1]
    elif fault == "extra": value["rows"].append(copy.deepcopy(value["rows"][-1]))
    elif fault == "unknown_field": value["untrusted"] = "never retain"
    elif fault == "secret_field": value["rows"][3]["token"] = "never retain"
    elif fault == "wrong_status": value["rows"][4]["http_status"] = 503
    elif fault == "false_status": value["rows"][1]["http_status"] = False
    elif fault == "wrong_tls": value["rows"][4]["tls"] = "TLSv1.1"
    elif fault == "unknown_ca_code": value["rows"][1]["verify_code"] = 62
    elif fault == "hostname_other_error": value["rows"][2]["verify_code"] = 20
    elif fault == "plaintext_accepted": value["rows"][3]["http_status"] = 401
    elif fault == "one_dispatch": value["rows"][4]["after_attempts"] = 8
    elif fault == "counter_regression": value["rows"][4]["after_attempts"] = 6
    elif fault == "epoch_change": value["rows"][4]["epoch"] = "b" * 32
    elif fault == "between_dispatch": value["rows"][4].update(before_attempts=8, after_attempts=8)
    elif fault == "counter_bool": value["rows"][4].update(before_attempts=True, after_attempts=True)
    elif fault.startswith("duration_"): value["rows"][4]["elapsed_ms"] = {"duration_nan": float("nan"), "duration_bool": True, "duration_limit": 12001}[fault]
    elif fault == "failed": value["status"] = "failed"
    else: value.update(status="failed", rows=value["rows"][:3], failed_scenario="plaintext")
    with pytest.raises(EvidenceError): evidence.validate_probe(value, complete=True)


def test_original_failure_can_retain_only_completed_prefix_without_becoming_pass():
    value = result()
    value.update(status="failed", rows=value["rows"][:3], failed_scenario="plaintext")
    assert evidence.validate_probe(value) == value
    with pytest.raises(EvidenceError): evidence.validate_probe(value, complete=True)


def remote_fixture(monkeypatch):
    namespace = {"__name__": "isolated_tls_probe"}
    exec(compile(support.TLS_SCRIPT, "classifier-tls-probe", "exec"), namespace)  # noqa: S102 - fixed trusted source, all external boundaries replaced below
    calls = []
    token = "synthetic-private-token-" + "x" * 32
    ca = b"synthetic-public-ca"
    settings = {"address": "10.96.0.50", "identity": identity(),
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "ca_sha256": hashlib.sha256(ca).hexdigest()}
    def read(path, maximum, digest):
        raw = token.encode() if path.endswith("token") else ca
        assert hashlib.sha256(raw).hexdigest() == digest and len(raw) <= maximum
        return raw
    monkeypatch.setitem(namespace, "read", read)
    monkeypatch.setitem(namespace, "wrong_ca", lambda: b"synthetic-wrong-ca")
    options = {"sni_failure": False, "ca_failure": False, "plaintext": b"", "status": 401,
        "body": canonical({"error": "unauthorized"}), "connect_error": None, "verify_code": None}
    class Channel:
        def settimeout(self, value): calls.append(("timeout", value))
        def connect(self, value):
            calls.append(("connect", value))
            if options["connect_error"] is not None: raise options["connect_error"]
        def close(self): calls.append(("close",))
        def version(self): return "TLSv1.3"
        def sendall(self, value): calls.append(("send", value))
        def recv(self, maximum):
            if isinstance(options["plaintext"], Exception): raise options["plaintext"]
            return options["plaintext"][:maximum]
    class Context:
        def __init__(self, protocol): self.ca = None
        def load_verify_locations(self, *, cadata): self.ca = cadata; calls.append(("ca", cadata))
        def wrap_socket(self, channel, *, server_hostname):
            calls.append(("sni", server_hostname))
            if options["ca_failure"] and self.ca == "synthetic-wrong-ca" or options["sni_failure"] and server_hostname != namespace["HOST"]:
                error = namespace["ssl"].SSLCertVerificationError("synthetic failure")
                error.verify_code = options["verify_code"] or (20 if self.ca == "synthetic-wrong-ca" else 62)
                raise error
            return channel
    class Response:
        def __init__(self, channel): self.status = options["status"]
        def begin(self): pass
        def getheaders(self): return [("content-type", "application/json"), ("content-length", str(len(options["body"])))]
        def read(self, maximum): return options["body"][:maximum]
    monkeypatch.setattr(namespace["socket"], "socket", lambda *args: Channel())
    monkeypatch.setattr(namespace["socket"], "getaddrinfo", lambda *args: pytest.fail("DNS is forbidden"))
    monkeypatch.setattr(namespace["ssl"], "SSLContext", Context)
    monkeypatch.setattr(namespace["http"].client, "HTTPResponse", Response)
    return namespace, settings, token, ca, options, calls


@pytest.mark.parametrize("scenario", ("absent_ready", "wrong_ready", "absent_evaluate", "wrong_evaluate"))
def test_original_auth_probe_sends_exact_path_and_unauthorized_body_without_valid_bearer(monkeypatch, scenario):
    namespace, settings, token, ca, _, calls = remote_fixture(monkeypatch)
    observed = namespace["request"](settings, scenario, ca, token)
    assert observed == {"outcome": "unauthorized", "http_status": 401, "tls": "TLSv1.3", "verify_code": 0}
    wire = next(row[1] for row in calls if row[0] == "send")
    assert token.encode() not in wire
    assert (b"Authorization:" not in wire) == scenario.startswith("absent_")
    if scenario.endswith("evaluate"):
        assert wire.startswith(b"POST /v1/classifier/evaluate ")
        body = json.loads(wire.split(b"\r\n\r\n", 1)[1])
        assert body["identity"] == identity() and body["api_major"] == 1 and body["segments"][0]["text"] == "A clear blue sky."
    else: assert wire.startswith(b"GET /v1/classifier/ready ")
    assert ("connect", ("10.96.0.50", 8443)) in calls and ("sni", namespace["HOST"]) in calls
    assert token not in json.dumps(observed) and sum(row[0] == "send" for row in calls) == 1


@pytest.mark.parametrize("scenario", ("wrong_ca", "wrong_sni"))
def test_certificate_negative_requires_actual_expected_verification_failure_before_http_write(monkeypatch, scenario):
    namespace, settings, token, ca, options, calls = remote_fixture(monkeypatch)
    options["ca_failure" if scenario == "wrong_ca" else "sni_failure"] = True
    observed = namespace["request"](settings, scenario, ca, token)
    assert observed == {"outcome": "certificate_rejected", "http_status": 0, "tls": None, "verify_code": 20 if scenario == "wrong_ca" else 62}
    assert not any(row[0] == "send" for row in calls)
    assert ("close",) in calls


@pytest.mark.parametrize("fault", ("accepted_ca", "accepted_sni", "wrong_error", "connect_refused", "connect_timeout", "auth_200", "auth_503", "wrong_error_body", "plaintext_http", "plaintext_timeout"))
def test_transport_or_protocol_errors_do_not_masquerade_as_required_refusals(monkeypatch, fault):
    namespace, settings, token, ca, options, _ = remote_fixture(monkeypatch)
    scenario = "absent_evaluate"
    if fault.startswith("accepted_"): scenario = "wrong_ca" if fault == "accepted_ca" else "wrong_sni"
    elif fault == "wrong_error": scenario = "wrong_sni"; options.update(sni_failure=True, verify_code=20)
    elif fault == "connect_refused": options["connect_error"] = ConnectionRefusedError()
    elif fault == "connect_timeout": options["connect_error"] = TimeoutError()
    elif fault.startswith("auth_"): options["status"] = int(fault[5:])
    elif fault == "wrong_error_body": options["body"] = canonical({"error": "validation_failed"})
    elif fault.startswith("plaintext_"):
        scenario = "plaintext"; options["plaintext"] = b"HTTP/1.1 401 Unauthorized" if fault == "plaintext_http" else TimeoutError()
    with pytest.raises((ValueError, OSError)): namespace["request"](settings, scenario, ca, token)


@pytest.mark.parametrize("closed", (b"", ConnectionResetError()))
def test_plaintext_requires_established_connection_then_eof_or_reset(monkeypatch, closed):
    namespace, settings, token, ca, options, calls = remote_fixture(monkeypatch)
    options["plaintext"] = closed
    assert namespace["request"](settings, "plaintext", ca, token)["outcome"] == "plaintext_closed"
    assert ("connect", ("10.96.0.50", 8443)) in calls
    wire = next(row[1] for row in calls if row[0] == "send")
    assert wire.startswith(b"GET /v1/classifier/ready ")
    assert token.encode() not in wire and b"Authorization:" not in wire


def test_whole_original_probe_brackets_all_eight_scenarios_with_current_authenticated_counters(monkeypatch):
    namespace, settings, token, ca, options, _calls = remote_fixture(monkeypatch)
    options.update(ca_failure=True, sni_failure=True)
    ready_calls = []
    def ready(value):
        assert value == settings
        ready_calls.append(value)
        return {"epoch": "a" * 32, "attempts": 7, "status": 200, "tls": "TLSv1.3"}
    monkeypatch.setitem(namespace["library"], "perform", ready)
    observed = {"schema_version": evidence.PROBE_SCHEMA, "status": "failed", "failed_scenario": "setup", "rows": []}
    namespace["perform"](settings, observed)
    evidence.validate_probe(observed, complete=True)
    assert len(ready_calls) == 16 and len(observed["rows"]) == 8
    assert token not in json.dumps(observed) and ca.decode() not in json.dumps(observed)


@pytest.mark.parametrize("fault", ("epoch", "dispatch", "regression"))
def test_whole_probe_stops_and_retains_failure_if_auth_attempt_dispatches_or_worker_changes(monkeypatch, fault):
    namespace, settings, _, _, options, _ = remote_fixture(monkeypatch)
    options.update(ca_failure=True, sni_failure=True)
    count = [0]
    def ready(value):
        count[0] += 1
        changed = count[0] == 10
        return {"epoch": "b" * 32 if fault == "epoch" and changed else "a" * 32,
            "attempts": (8 if fault == "dispatch" else 6) if changed and fault != "epoch" else 7, "status": 200, "tls": "TLSv1.3"}
    monkeypatch.setitem(namespace["library"], "perform", ready)
    observed = {"schema_version": evidence.PROBE_SCHEMA, "status": "failed", "failed_scenario": "setup", "rows": []}
    with pytest.raises(ValueError): namespace["perform"](settings, observed)
    assert len(observed["rows"]) == 4 and count[0] == 10 and observed["failed_scenario"] == "absent_ready"
    evidence.validate_probe(observed)
    with pytest.raises(EvidenceError): evidence.validate_probe(observed, complete=True)


def source_fixture(tmp_path):
    sources = {}
    root = tmp_path / "sources"
    for name in evidence.SOURCES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'"""Synthetic source identity."""\n')
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return root, sources


def observation():
    return {"schema_version": evidence.SCHEMA, "runtime": "runtime-1", "status": "passed", "binding_sha256": "b" * 64,
        "identity_sha256": hashlib.sha256(canonical(identity())).hexdigest(), "elapsed_ms": 10, "probe": result()}


@pytest.mark.parametrize("fault", ("missing_source", "changed_source", "wrong_runtime", "wrong_identity", "no_binding", "no_probe", "failed", "too_slow", "unknown_field"))
def test_complete_attachment_requires_actual_source_closure_binding_and_all_probe_rows(tmp_path, fault):
    root, sources = source_fixture(tmp_path)
    value = observation()
    if fault == "missing_source": del sources[evidence.SOURCES[0]]
    elif fault == "changed_source": (root / evidence.SOURCES[0]).write_bytes(b"changed\n")
    elif fault == "wrong_runtime": value["runtime"] = "runtime-2"
    elif fault == "wrong_identity": value["identity_sha256"] = "c" * 64
    elif fault == "no_binding": value["binding_sha256"] = None
    elif fault == "no_probe": value["probe"] = None
    elif fault == "failed": value["status"] = "failed"
    elif fault == "too_slow": value["elapsed_ms"] = 90001
    else: value["content"] = "never retain"
    with pytest.raises(EvidenceError): evidence.validate(value, "SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-RUNTIME-1", root=root, sources=sources, complete=True)


def test_attachment_writer_is_exclusive_private_and_preserves_source_binding(tmp_path, monkeypatch):
    root, sources = source_fixture(tmp_path)
    case = "SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-RUNTIME-1"
    target = tmp_path / "measurements" / case
    target.mkdir(mode=0o700, parents=True)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(target))
    value = observation()
    evidence.record(value, case, root=root, sources=sources)
    path = target / evidence.FILENAME
    assert path.read_bytes() == canonical(value) + b"\n" and path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): evidence.record(value, case, root=root, sources=sources)


def test_original_parent_wrapper_binds_static_stdin_and_preserves_remote_failure(monkeypatch):
    d = SimpleNamespace(env={}, state=Path("/synthetic"), bin=Path("/synthetic/bin"))
    subject = support.TlsObservation(SimpleNamespace(profile="classifier", deployment=d), "runtime-1")
    observer = subject.observer
    binding = {"api_server": "https://127.0.0.1:44634", "service": {"address": "10.96.0.50"},
        "files": {"assembly/private/classifier.token": "a" * 64, "assembly/tls/gr-platform/classifier/ca.crt": "b" * 64},
        "pods": {"runtime-1": {"pod_name": "runtime-1-owned"}}}
    observer.bound = binding
    observer._api_owner, observer._api_images, observer._api_pins, observer._api_private = {}, {}, {}, set()
    observer._api_input = "synthetic static private config"
    monkeypatch.setattr(observer, "_capture", lambda: (copy.deepcopy(binding), {}, {}))
    monkeypatch.setattr(observer, "_native", lambda *args: binding["api_server"])
    remote = result()
    remote.update(status="failed", rows=remote["rows"][:3], failed_scenario="plaintext")
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        assert args[:3] == [d.bin / "kubectl", "--kubeconfig", "/dev/stdin"]
        assert args[-2] == support.TLS_SCRIPT and kwargs["input_text"] == observer._api_input
        assert kwargs["check"] is False and kwargs["timeout"] <= 50
        return 1, canonical(remote).decode()
    d.run = run
    with pytest.raises(RuntimeError): subject.run_locked()
    assert len(calls) == 1 and subject.value["status"] == "failed" and subject.value["probe"] == remote
    assert 0 <= subject.value["elapsed_ms"] <= 90000


@pytest.mark.parametrize("fault", ("none", "probe", "bind", "close", "record", "probe_close_record"))
def test_case_runs_one_locked_attempt_and_preserves_primary_failure_through_cleanup_and_evidence(monkeypatch, fault):
    calls = []
    primary_error = RuntimeError("synthetic probe failure")
    close_error = RuntimeError("synthetic close failure")
    record_error = RuntimeError("synthetic record failure")
    class Observation:
        def __init__(self, cluster, runtime):
            self.value = observation()
        def bind_locked(self):
            calls.append("bind")
            if fault == "bind": raise primary_error
        def run_locked(self):
            calls.append("probe")
            if fault.startswith("probe"): raise primary_error
            return self.value
    class Cluster:
        deployment = SimpleNamespace(lock=lambda: contextlib.nullcontext())
        def close(self):
            calls.append("close")
            if "close" in fault: raise close_error
    monkeypatch.setattr(support, "TlsObservation", Observation)
    monkeypatch.setattr(evidence, "gate_sources", lambda *args: {})
    monkeypatch.setattr(evidence, "validate", lambda *args, **kwargs: None)
    def record(value, *args, **kwargs):
        calls.append(("record", value["status"]))
        if "record" in fault: raise record_error
    monkeypatch.setattr(evidence, "record", record)
    if fault == "none":
        support.run_tls_case(Cluster, root=Path("/synthetic"), runtime="runtime-1")
        assert calls == ["bind", "probe", "close", ("record", "passed")]
    else:
        with pytest.raises(RuntimeError) as caught:
            support.run_tls_case(Cluster, root=Path("/synthetic"), runtime="runtime-1")
        expected = primary_error if fault in {"probe", "bind", "probe_close_record"} else close_error if fault == "close" else record_error
        assert caught.value is expected
        assert calls.count("bind") == calls.count("close") == 1
        assert calls.count("probe") == (0 if fault == "bind" else 1)
        assert calls[-1] == ("record", "passed" if fault == "record" else "failed")
