"""Synthetic wire, committed audit, ownership and absolute-budget composition."""
from __future__ import annotations

import copy
import hashlib
import socket
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from evolution import classifier_budget_evidence as evidence
from guardrails.evolution.classifier_contract import canonical, identity

from mvp.tests.evolution.e2e import classifier_budget_support as support
from mvp.tests.evolution.e2e.classifier_network_support import (
    ClassifierObservationError,
)

SHA = "a" * 64


@pytest.fixture(autouse=True)
def no_operations(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("synthetic budget suite forbids external operations")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def uid(n):
    return f"00000000-0000-0000-0000-{n:012d}"


def fixture(tenant="team-a", stage="input", scenario="benign"):
    policy = {"tenant_id": tenant, "policy_id": "classifier-policy", "version": 3, "digest": SHA}
    expected = {"tenant": tenant, "workload_id": "chat", "principal_id": uid(1), "stage": stage, "scenario": scenario,
        "policy": policy, "binding_revision": 3, "snapshot_generations": [4, 5]}
    sent = {"schema_version": "guardrails-v2", "request_id": uid(2), "correlation_id": uid(3), "tenant_id": tenant,
        "workload_id": "chat", "stage": stage, "provider_id": uid(4), "context_id": None if stage == "input" else uid(5),
        "parts": [{"part_id": "body", "kind": "text", "role": "user" if stage == "input" else "assistant", "text": "private synthetic body"}],
        "deadline_ms": 3000}
    malicious = scenario == "malicious"
    reasons = ["injection_detected"] if malicious else []
    findings = [{"rule_id": "classifier", "category": "injection", "part_id": "body", "reason_code": "injection_detected",
        "score": .99, "evaluator_revision": identity()["evaluator_revision"]}] if malicious else []
    decision = {"decision_id": uid(6), "request_id": sent["request_id"], "context_id": uid(5), "tenant_id": tenant,
        "workload_id": "chat", "stage": stage, "policy": policy, "binding_revision": 3, "snapshot_generation": 4,
        "action": "allow", "would_be_action": "block" if malicious else "allow", "reason_codes": reasons,
        "findings": findings, "transformed_parts": None, "evaluation_ms": 100.0, "audit_status": "persisted",
        "evaluator_revisions": [identity()["evaluator_revision"]]}
    audit = {"event_id": uid(6), "decision_id": uid(6), "tenant_id": tenant, "workload_id": "chat", "actor_id": uid(1),
        "event_type": "evaluation", "correlation_id": uid(3), "policy_digest": SHA, "binding_revision": 3, "reason_codes": reasons,
        "action": "allow", "occurred_at": "2026-10-12T04:00:00Z", "created_at": "2026-10-12T04:00:00Z",
        "previous_hash": None, "event_hash": "b" * 64, "raw_content_retained": False, "metadata": {"stage": stage}}
    return expected, sent, decision, audit


def ready(n):
    return {"binding_sha256": SHA, "counter": {"epoch": "c" * 32, "attempts": n}, "tls": "TLSv1.3", "ready_elapsed_ms": 3}


def normal_args(tenant="team-a", stage="input", scenario="benign"):
    expected, sent, decision, audit = fixture(tenant, stage, scenario)
    return decision, {"expected": expected, "sent": sent, "audit": audit,
        "ready_before": ready(4), "ready_after": ready(5), "client_elapsed_ms": 150}


@pytest.mark.parametrize("tenant", ("team-a", "team-b"))
@pytest.mark.parametrize("stage", ("input", "output"))
@pytest.mark.parametrize("scenario", ("benign", "malicious"))
def test_normal_requires_post_commit_response_matching_audit_and_one_dispatch(tenant, stage, scenario):
    value, args = normal_args(tenant, stage, scenario)
    result = evidence.normal(value, **args)
    assert result["server_elapsed_ms"] == 100 and result["client_elapsed_ms"] == 150
    assert result["dispatch_delta"] == 1 and result["audit_status"] == "persisted"
    raw = canonical(result)
    assert b"private synthetic body" not in raw and uid(2).encode() not in raw


@pytest.mark.parametrize("fault", ("server_late", "client_late", "server_after_client", "nan", "bool", "zero", "spooled",
    "request", "tenant", "workload", "stage", "policy", "binding", "snapshot", "revision", "context", "would_allow",
    "wrong_category", "low_score", "missing_finding", "foreign_audit", "audit_actor", "audit_correlation", "audit_decision",
    "audit_policy", "audit_revision", "audit_action", "audit_reasons", "audit_metadata", "raw_retained", "dispatch0", "dispatch2",
    "epoch", "ready_binding", "ready_late", "request_deadline", "request_role"))
def test_normal_refuses_unmeasured_failed_or_uncorrelated_observations(fault):
    value, args = normal_args(stage="output", scenario="malicious")
    if fault == "server_late": value["evaluation_ms"] = 3000.001
    elif fault == "client_late": args["client_elapsed_ms"] = 4000.001
    elif fault == "server_after_client": value["evaluation_ms"] = 151
    elif fault in {"nan", "bool", "zero"}: value["evaluation_ms"] = {"nan": float("nan"), "bool": True, "zero": 0}[fault]
    elif fault == "spooled": value["audit_status"] = "spooled"
    elif fault == "request": value["request_id"] = uid(22)
    elif fault == "tenant": value["tenant_id"] = "team-b"
    elif fault == "workload": value["workload_id"] = "other"
    elif fault == "stage": value["stage"] = "input"
    elif fault == "policy": value["policy"] = {**value["policy"], "version": 4}
    elif fault == "binding": value["binding_revision"] = 4
    elif fault == "snapshot": value["snapshot_generation"] = 6
    elif fault == "revision": value["evaluator_revisions"] = ["foreign"]
    elif fault == "context": value["context_id"] = uid(55)
    elif fault == "would_allow": value["would_be_action"] = "allow"
    elif fault == "wrong_category": value["findings"][0]["category"] = "other"
    elif fault == "low_score": value["findings"][0]["score"] = .8999
    elif fault == "missing_finding": value["findings"] = []
    elif fault == "foreign_audit": args["audit"]["tenant_id"] = "team-b"
    elif fault.startswith("audit_"):
        key, replacement = {"audit_actor": ("actor_id", uid(88)), "audit_correlation": ("correlation_id", uid(88)),
            "audit_decision": ("decision_id", uid(88)), "audit_policy": ("policy_digest", "c" * 64),
            "audit_revision": ("binding_revision", 4), "audit_action": ("action", "block"),
            "audit_reasons": ("reason_codes", []), "audit_metadata": ("metadata", {"stage": "input"})}[fault]
        args["audit"][key] = replacement
    elif fault == "raw_retained": args["audit"]["raw_content_retained"] = True
    elif fault.startswith("dispatch"): args["ready_after"]["counter"]["attempts"] = 4 + int(fault[-1])
    elif fault == "epoch": args["ready_after"]["counter"]["epoch"] = "d" * 32
    elif fault == "ready_binding": args["ready_after"]["binding_sha256"] = "d" * 64
    elif fault == "ready_late": args["ready_before"]["ready_elapsed_ms"] = 8000
    elif fault == "request_deadline": args["sent"]["deadline_ms"] = 3001
    elif fault == "request_role": args["sent"]["parts"][0]["role"] = "user"
    with pytest.raises((ValueError, TypeError)):
        evidence.normal(value, **args)


@pytest.mark.parametrize("reason", ("evaluation_timeout", "evaluator_unavailable", "inspection_incomplete", "queue_full", "audit_failed", "upstream_timeout"))
@pytest.mark.parametrize("where", ("reason_codes", "findings"))
def test_observe_allow_never_turns_operational_failure_into_accepted_budget(reason, where):
    value, args = normal_args(scenario="malicious")
    if where == "reason_codes": value[where].append(reason)
    else: value[where].append({**value["findings"][0], "reason_code": reason})
    with pytest.raises(ValueError): evidence.normal(value, **args)


def test_exact_frozen_budget_limits_accept_and_actual_block_is_allowed():
    value, args = normal_args(scenario="malicious")
    value["evaluation_ms"], args["client_elapsed_ms"] = 3000.0, 4000.0
    value["action"] = args["audit"]["action"] = "block"
    assert evidence.normal(value, **args)["action"] == "block"


def slow_args():
    expected, sent, _, _ = fixture(scenario="slow_body")
    value = {"error": {"code": "evaluation_timeout", "message": "Request timed out", "correlation_id": sent["correlation_id"], "retryable": False, "details": {}}}
    return value, {"expected": expected, "sent": sent, "ready_before": ready(4), "ready_after": ready(4),
        "client_elapsed_ms": 700, "body_delay_ms": 600, "audit_matches": 0}


@pytest.mark.parametrize("fault", (None, "dispatch", "epoch", "audit", "bool_audit", "early", "late", "delay_late", "delay_early", "correlation", "code"))
def test_slow_body_is_original_bounded_pre_admission_refusal(fault):
    value, args = slow_args()
    if fault == "dispatch": args["ready_after"]["counter"]["attempts"] += 1
    elif fault == "epoch": args["ready_after"]["counter"]["epoch"] = "e" * 32
    elif fault == "audit": args["audit_matches"] = 1
    elif fault == "bool_audit": args["audit_matches"] = False
    elif fault == "early": args["client_elapsed_ms"] = 549
    elif fault == "late": args["client_elapsed_ms"] = 4000.1
    elif fault == "delay_late": args["body_delay_ms"] = 701
    elif fault == "delay_early": args["body_delay_ms"] = 549
    elif fault == "correlation": value["error"]["correlation_id"] = uid(8)
    elif fault == "code": value["error"]["code"] = "evaluator_unavailable"
    if fault:
        with pytest.raises(ValueError): evidence.slow_body(value, **args)
    else:
        assert evidence.slow_body(value, **args)["dispatch_delta"] == 0


def material_fixture(tmp_path, expected):
    assembly = tmp_path / "assembly"; assembly.mkdir(mode=0o700)
    credentials = tmp_path / "credentials"; credentials.mkdir(mode=0o700)
    ca, token = credentials / "ca.crt", assembly / "workload.token"
    ca.write_bytes(b"synthetic CA"); ca.chmod(0o600)
    token.write_bytes(b"synthetic-private-bearer-0123456789\n"); token.chmod(0o600)
    item = {"workload": {"workload_id": "chat"}, "roles": {"workload": {"principal_id": expected["principal_id"], "token_file": str(token)}}}
    return SimpleNamespace(profile="classifier", state=tmp_path, inventory={"tenants": {expected["tenant"]: item}},
        access={"profile": "classifier", "runtime_url": "https://localhost:44444", "ca_file": str(ca),
                "tenants": {expected["tenant"]: {"workload_token_file": str(token)}}})


@pytest.mark.parametrize("fault", (None, "http", "foreign_host", "userinfo", "path", "query", "fragment", "public_token", "symlink", "principal", "access_token", "token_newline", "foreign_path", "deadline"))
def test_exact_private_transport_material_is_bound_without_network(tmp_path, monkeypatch, fault):
    expected = fixture()[0]; cluster = material_fixture(tmp_path, expected)
    monkeypatch.setattr(support.time, "monotonic", lambda: 1)
    if fault in {"http", "foreign_host", "userinfo", "path", "query", "fragment"}:
        cluster.access["runtime_url"] = {"http": "http://localhost:44444", "foreign_host": "https://example.com:44444",
            "userinfo": "https://user@localhost:44444", "path": "https://localhost:44444/", "query": "https://localhost:44444?a=1",
            "fragment": "https://localhost:44444#x"}[fault]
    elif fault == "public_token": (tmp_path / "assembly/workload.token").chmod(0o644)
    elif fault == "symlink":
        original = tmp_path / "assembly/workload.token"; original.rename(original.with_name("saved")); original.symlink_to(original.with_name("saved"))
    elif fault == "principal": expected["principal_id"] = uid(55)
    elif fault == "access_token": cluster.access["tenants"]["team-a"]["workload_token_file"] = "/foreign"
    elif fault == "token_newline": (tmp_path / "assembly/workload.token").write_bytes(b"x" * 32 + b"\nembedded")
    elif fault == "foreign_path": cluster.access["ca_file"] = str(tmp_path / "foreign")
    if fault:
        with pytest.raises((ValueError, FileNotFoundError, ClassifierObservationError)):
            support.transport_material(cluster, expected, deadline=1 if fault == "deadline" else 20)
    else:
        result = support.transport_material(cluster, expected, deadline=20)
        assert result["url"] == "https://127.0.0.1:44444/v2/evaluations"
        assert result["host"] == "localhost:44444" and result["token"].endswith("0123456789")
        assert set(result["pins"]) == {"ca_sha256", "token_sha256"}


@pytest.mark.parametrize("tenant", ("team-a", "team-b"))
def test_owned_credentials_ca_and_tenant_token_have_exact_byte_pins(tmp_path, monkeypatch, tenant):
    expected = fixture(tenant=tenant)[0]
    cluster = material_fixture(tmp_path, expected)
    monkeypatch.setattr(support.time, "monotonic", lambda: 1)
    result = support.transport_material(cluster, expected, deadline=20)
    assert result["ca"] == "synthetic CA"
    assert result["pins"] == {
        "ca_sha256": hashlib.sha256(b"synthetic CA").hexdigest(),
        "token_sha256": hashlib.sha256(b"synthetic-private-bearer-0123456789\n").hexdigest(),
    }


@pytest.mark.parametrize("fault", ("assembly_ca", "other_credentials_ca", "outside_ca", "relative_ca",
    "parent_alias_ca", "public_ca", "symlink_ca", "linked_ca", "outside_token", "credentials_token"))
def test_ca_path_correction_preserves_owned_private_transport_boundary(tmp_path, monkeypatch, fault):
    expected = fixture()[0]
    cluster = material_fixture(tmp_path, expected)
    monkeypatch.setattr(support.time, "monotonic", lambda: 1)
    ca = tmp_path / "credentials/ca.crt"
    if fault in {"assembly_ca", "other_credentials_ca", "outside_ca"}:
        replacement = {
            "assembly_ca": tmp_path / "assembly/ca.crt",
            "other_credentials_ca": tmp_path / "credentials/other.crt",
            "outside_ca": tmp_path / "outside.crt",
        }[fault]
        replacement.write_bytes(ca.read_bytes()); replacement.chmod(0o600)
        cluster.access["ca_file"] = str(replacement)
    elif fault == "relative_ca": cluster.access["ca_file"] = "credentials/ca.crt"
    elif fault == "parent_alias_ca": cluster.access["ca_file"] = str(tmp_path / "credentials/../credentials/ca.crt")
    elif fault == "public_ca": ca.chmod(0o644)
    elif fault == "symlink_ca":
        ca.rename(ca.with_name("saved.crt")); ca.symlink_to(ca.with_name("saved.crt"))
    elif fault == "linked_ca": ca.with_name("linked.crt").hardlink_to(ca)
    else:
        replacement = tmp_path / ("outside.token" if fault == "outside_token" else "credentials/workload.token")
        replacement.write_bytes(b"synthetic-private-bearer-0123456789\n"); replacement.chmod(0o600)
        cluster.inventory["tenants"]["team-a"]["roles"]["workload"]["token_file"] = str(replacement)
        cluster.access["tenants"]["team-a"]["workload_token_file"] = str(replacement)
    with pytest.raises((ValueError, ClassifierObservationError)):
        support.transport_material(cluster, expected, deadline=20)


def drive(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    else:
        coroutine.close()
        raise AssertionError("fake coroutine must never await actual IO")


def fake_wire(monkeypatch, *, slow=False, fault=None):
    _, sent, decision, _ = fixture(scenario="slow_body" if slow else "benign")
    value = slow_args()[0] if slow else decision
    raw = canonical(value)
    now, events = [100.0], []
    monkeypatch.setattr(support.time, "monotonic", lambda: now[0])
    class Context:
        def __init__(self, value): assert value == "CLIENT"; events.append("context")
        def load_verify_locations(self, **kw):
            assert kw == {"cadata": "synthetic CA"}
            if fault in {"setup_slow", "setup_expired"}: now[0] += 2 if fault == "setup_slow" else 4.1
    class Manager:
        def __init__(self, value, label): self.value, self.label = value, label
        async def __aenter__(self): events.append(self.label + "_enter"); return self.value
        async def __aexit__(self, *a):
            events.append(self.label + "_exit")
            if self.label == "client": now[0] += 4 if fault == "late_close" else .01
    async def sleep(seconds):
        assert seconds == .6; events.append("delay"); now[0] += seconds
    def timeout_at(deadline):
        assert deadline == 104; return Manager(None, "timeout")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()),
        (b"x-request-id", sent["correlation_id"].encode()), (b"cache-control", b"no-store")]
    if fault == "duplicate": headers.append(headers[0])
    elif fault == "compressed": headers.append((b"content-encoding", b"gzip"))
    elif fault == "correlation": headers[2] = (b"x-request-id", uid(9).encode())
    elif fault == "length": headers[1] = (b"content-length", b"1")
    elif fault == "oversized": headers[1] = (b"content-length", b"3000000")
    async def aiter_raw(**kw):
        assert kw == {"chunk_size": 65536}; now[0] += .1
        if fault == "body_timeout": raise TimeoutError("private synthetic body timeout")
        yield b"x" * (support.MAX_RESPONSE + 1) if fault == "oversized_chunk" else raw
    response = SimpleNamespace(status_code=503 if fault == "status" else 504 if slow else 200,
        headers=SimpleNamespace(raw=headers), aiter_raw=aiter_raw,
        extensions={"network_stream": SimpleNamespace(get_extra_info=lambda key: SimpleNamespace(version=lambda: "TLSv1.1" if fault == "tls" else "TLSv1.3"))})
    class ResponseManager(Manager):
        async def __aenter__(self):
            if hasattr(self.content, "__aiter__"):
                received = b"".join([part async for part in self.content])
            else: received = self.content
            assert received == canonical(sent)
            return await super().__aenter__()
    class Client(Manager):
        def __init__(self, **kw):
            assert set(kw) == {"verify", "trust_env", "follow_redirects", "timeout", "limits"}
            assert kw["verify"].minimum_version == "TLS12" and kw["trust_env"] is False and kw["follow_redirects"] is False
            assert kw["timeout"] == (4, .25) and kw["limits"] == (1, 0)
            super().__init__(self, "client")
        def stream(self, method, url, *, headers, content):
            assert method == "POST" and url == "https://127.0.0.1:44444/v2/evaluations"
            assert headers == {"Host": "localhost:44444", "Authorization": "Bearer synthetic bearer", "Content-Type": "application/json",
                "Content-Length": str(len(canonical(sent))), "Accept-Encoding": "identity", "X-Request-ID": sent["correlation_id"]}
            events.append("request")
            manager = ResponseManager(response, "response"); manager.content = content; return manager
    monkeypatch.setitem(sys.modules, "ssl", SimpleNamespace(SSLContext=Context, PROTOCOL_TLS_CLIENT="CLIENT", TLSVersion=SimpleNamespace(TLSv1_2="TLS12")))
    monkeypatch.setitem(sys.modules, "asyncio", SimpleNamespace(sleep=sleep, get_running_loop=lambda: SimpleNamespace(time=lambda: now[0]), timeout_at=timeout_at))
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(AsyncClient=Client, Timeout=lambda total, connect: (total, connect),
        Limits=lambda max_connections, max_keepalive_connections: (max_connections, max_keepalive_connections)))
    material = {"ca": "synthetic CA", "token": "synthetic bearer", "host": "localhost:44444", "url": "https://127.0.0.1:44444/v2/evaluations"}
    return lambda: drive(support.exchange(material, sent, slow=slow, deadline=120)), events


@pytest.mark.parametrize("slow", (False, True))
def test_one_fake_wire_request_absolute_clock_includes_body_stream_and_client_close(monkeypatch, slow):
    run, events = fake_wire(monkeypatch, slow=slow)
    value = run()
    assert events.count("request") == 1 and events[-2:] == ["client_exit", "timeout_exit"]
    assert value["client_elapsed_ms"] == pytest.approx(710 if slow else 110)
    assert value["body_delay_ms"] == pytest.approx(600 if slow else 0)


@pytest.mark.parametrize("fault", ("setup_slow", "setup_expired"))
def test_setup_elapsed_is_in_same_absolute_client_budget_before_dispatch(monkeypatch, fault):
    run, events = fake_wire(monkeypatch, fault=fault)
    if fault == "setup_expired":
        with pytest.raises(ValueError): run()
        assert events == ["context"]
    else:
        value = run()
        assert value["client_elapsed_ms"] == pytest.approx(2110)
        assert events.count("request") == 1


@pytest.mark.parametrize("fault", ("late_close", "duplicate", "compressed", "correlation", "length", "oversized", "oversized_chunk", "status", "tls", "body_timeout"))
def test_fake_wire_refuses_wrong_transport_or_late_complete_client(monkeypatch, fault):
    run, events = fake_wire(monkeypatch, fault=fault)
    with pytest.raises((ValueError, TimeoutError)): run()
    assert events.count("request") == 1 and "client_exit" in events


@pytest.mark.parametrize("fault", (None, "foreign", "duplicate", "absent", "other_decision", "deadline"))
def test_committed_audit_reader_uses_both_tenant_transactions_and_absolute_db_deadline(monkeypatch, fault):
    expected, sent, decision, audit = fixture()
    events = []
    monkeypatch.setattr(support.time, "monotonic", lambda: 10)
    @contextmanager
    def transaction(tenant):
        assert support.current_deadline.get() == 20
        events.append(tenant)
        def execute(sql, params):
            assert "LIMIT 2" in sql and params == {"tenant": tenant, "correlation": sent["correlation_id"]}
            values = [audit] if tenant == "team-a" and fault != "absent" or fault == "foreign" else []
            if fault == "duplicate": values *= 2
            return [SimpleNamespace(data=row) for row in values]
        def get(kind, key, required):
            assert kind == "audit" and key == decision["decision_id"] and required == (tenant == "team-a")
            return (dict(audit, event_hash="d" * 64) if fault == "other_decision" else audit) if tenant == "team-a" else None
        yield SimpleNamespace(execute=execute, get=get)
    cluster = SimpleNamespace(repo=SimpleNamespace(transaction=transaction))
    before = support.current_deadline.get()
    if fault:
        with pytest.raises(ValueError): support.audit_rows(cluster, expected, sent, decision, deadline=10 if fault == "deadline" else 20)
    else:
        assert support.audit_rows(cluster, expected, sent, decision, deadline=20) == audit
        assert events == ["team-a", "team-b"]
    assert support.current_deadline.get() == before


def test_slow_refusal_queries_both_tenants_and_requires_zero_committed_matches(monkeypatch):
    expected, sent, _, _ = fixture(scenario="slow_body")
    events = []
    monkeypatch.setattr(support.time, "monotonic", lambda: 10)
    @contextmanager
    def transaction(tenant):
        events.append(tenant)
        yield SimpleNamespace(execute=lambda *a, **kw: [])
    assert support.audit_rows(SimpleNamespace(repo=SimpleNamespace(transaction=transaction)), expected, sent, None, deadline=20) == 0
    assert events == ["team-a", "team-b"]


def test_readiness_reset_never_extends_budget_observation(monkeypatch):
    monkeypatch.setattr(support.time, "monotonic", lambda: 59)
    observer = support.BudgetObserver.__new__(support.BudgetObserver)
    observer.deadline, observer.budget_deadline = 100, 60
    assert observer._remaining(50) == 1
    monkeypatch.setattr(support.time, "monotonic", lambda: 60)
    with pytest.raises(ValueError): observer._remaining(50)


@pytest.mark.parametrize("fault", (None, "request", "audit", "after", "material_drift", "timeout", "operational_allow"))
def test_single_reader_retains_finite_original_prefix_and_restores_cap(monkeypatch, fault):
    expected, sent, decision, audit = fixture()
    events, now = [], [1.0]
    monkeypatch.setattr(support.time, "monotonic", lambda: now[0])
    observer = support.BudgetObserver.__new__(support.BudgetObserver)
    observer.cluster, observer.bound, observer.budget_deadline = object(), {"owned": True}, 999
    samples = iter((ready(4), ready(5)))
    def sample():
        assert observer.budget_deadline == 61
        events.append("ready"); return next(samples)
    observer.sample_locked = sample
    material = {"pins": {"ca_sha256": SHA, "token_sha256": "b" * 64}}
    def materials(*a, **kw):
        events.append("material")
        return {"pins": {"ca_sha256": "c" * 64}} if fault == "material_drift" and events.count("material") == 2 else copy.deepcopy(material)
    def recheck(**kw):
        assert kw["deadline"] == 61
        events.append("recheck")
        if fault == "after" and events.count("recheck") == 2: raise ValueError("synthetic private proof failure")
    async def exchange(*a, **kw):
        events.append("request")
        if fault == "request": raise RuntimeError("synthetic private request failure")
        if fault == "timeout": now[0] = 62
        if fault == "operational_allow": decision["reason_codes"] = ["evaluation_timeout"]
        return {"value": decision, "client_elapsed_ms": 150, "body_delay_ms": 0, "tls": "TLSv1.3"}
    def read(*a, **kw):
        events.append("audit")
        if fault == "audit": raise ValueError("synthetic private audit failure")
        return audit
    monkeypatch.setitem(sys.modules, "asyncio", SimpleNamespace(run=drive))
    monkeypatch.setattr(support, "transport_material", materials)
    monkeypatch.setattr(support, "exchange", exchange)
    monkeypatch.setattr(support, "audit_rows", read)
    reader = support.BudgetReader(observer, recheck_transport=recheck)
    if fault:
        with pytest.raises((ValueError, RuntimeError)): reader.sample_locked(sent, expected=expected, deadline=100)
        assert reader.value["status"] == "failed"
    else:
        value = reader.sample_locked(sent, expected=expected, deadline=100)
        assert value["status"] == "observed" and value["row"]["dispatch_delta"] == 1
        assert events == ["recheck", "material", "ready", "request", "audit", "ready", "recheck", "material"]
    assert observer.budget_deadline == 999 and events.count("request") == 1
    assert b"private" not in canonical(reader.value) and b"synthetic body" not in canonical(reader.value)
    with pytest.raises(ValueError): reader.sample_locked(sent, expected=expected, deadline=100)


def test_transport_verifier_cannot_be_omitted():
    with pytest.raises(ValueError): support.BudgetReader(object(), recheck_transport=None)
