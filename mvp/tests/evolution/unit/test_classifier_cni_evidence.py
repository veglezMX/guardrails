"""Portable synthetic CNI observations; no socket, DNS, process or model call."""
from __future__ import annotations

import builtins
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from evolution import classifier_cni_evidence as subject
from guardrails.evolution.classifier_contract import canonical

from mvp.tests.evolution.e2e import classifier_cni_support as support


def fixture():
    peers = {}
    for index, (name, (namespace, deployment, container)) in enumerate(subject.ORIGINS.items(), 1):
        peers[name] = {"namespace": namespace, "pod_name": deployment + "-abc123-def45", "pod_uid": "00000000-0000-0000-0000-" + str(index).zfill(12),
            "container": container, "container_id": str(index) * 64, "pod_ip": "10.244.0." + str(index + 10), "process_start_ticks": 1000 + index, "netns_inode": 2000 + index}
    readiness = {"owner_id": "a" * 32, "node_id": "b" * 64}
    return {**readiness, "readiness_binding_sha256": hashlib.sha256(canonical(readiness)).hexdigest(), "peers": peers,
        "addresses": {"classifier_service": "10.96.0.20", "sink_service": "10.96.0.21", "mock_service": "10.96.0.22",
            "postgres_service": "10.96.0.23", "api_service": "10.96.0.1", "dns_service": "10.96.0.10"},
        "host_sink": {"address": "192.168.51.1", "port": 12345, "identity_sha256": "c" * 64}}


def test_exact_matrix_covers_twenty_original_direct_ip_ingress_egress_and_dns_paths():
    bound = fixture()
    jobs = subject.jobs(bound)
    assert len(jobs) == len({row["job_id"] for row in jobs}) == 20
    assert {row["job_id"] for row in jobs if row["origin"] == "classifier"} == {
        "egress_sink_service", "egress_sink_pod", "egress_mock", "egress_postgres", "egress_api", "egress_host", "egress_external", "egress_metadata", "dns_tcp", "dns_udp"}
    assert {row["settings"]["destination_ip"] for row in jobs if row["origin"] == "allowed-team-b"} == {"10.96.0.20", "10.244.0.11"}
    assert all(row["settings"]["source_ip"] == bound["peers"][row["origin"]]["pod_ip"] for row in jobs)
    assert next(row for row in jobs if row["job_id"] == "egress_external")["settings"]["destination_ip"] == "1.1.1.1"


@pytest.mark.parametrize("fault", ("extra", "owner", "node", "missing_peer", "namespace", "pod_name", "pod_uid", "duplicate_uid", "duplicate_cid", "duplicate_ip",
    "source_loopback", "source_link_local", "source_ipv6", "start", "netns", "extra_address", "duplicate_address", "service_equals_pod", "host_address", "host_port", "host_identity"))
def test_binding_rejects_unknown_origins_ambiguous_identity_or_unapproved_destinations(fault):
    value = fixture()
    peer = value["peers"]["classifier"]
    if fault == "extra": value["unbound"] = True
    elif fault == "owner": value["owner_id"] = "invalid"
    elif fault == "node": value["node_id"] = "invalid"
    elif fault == "missing_peer": del value["peers"]["sink"]
    elif fault in {"namespace", "pod_name", "pod_uid"}: peer[fault] = "foreign"
    elif fault == "duplicate_uid": peer["pod_uid"] = value["peers"]["runtime-1"]["pod_uid"]
    elif fault == "duplicate_cid": peer["container_id"] = value["peers"]["runtime-1"]["container_id"]
    elif fault == "duplicate_ip": peer["pod_ip"] = value["peers"]["runtime-1"]["pod_ip"]
    elif fault == "source_loopback": peer["pod_ip"] = "127.0.0.1"
    elif fault == "source_link_local": peer["pod_ip"] = "169.254.0.1"
    elif fault == "source_ipv6": peer["pod_ip"] = "fd00::1"
    elif fault == "start": peer["process_start_ticks"] = True
    elif fault == "netns": peer["netns_inode"] = 0
    elif fault == "extra_address": value["addresses"]["extra"] = "10.96.0.55"
    elif fault == "duplicate_address": value["addresses"]["api_service"] = value["addresses"]["dns_service"]
    elif fault == "service_equals_pod": value["addresses"]["api_service"] = peer["pod_ip"]
    elif fault == "host_address": value["host_sink"]["address"] = "192.168.51.2"
    elif fault == "host_port": value["host_sink"]["port"] = 80
    else: value["host_sink"]["identity_sha256"] = "invalid"
    with pytest.raises(subject.CniObservationError): subject.bindings(value)


def controls(bound):
    ready = {"binding_sha256": bound["readiness_binding_sha256"], "counter": {"epoch": "d" * 32, "attempts": 12}, "tls": "TLSv1.3", "ready_elapsed_ms": 25}
    sink = {"identity_sha256": hashlib.sha256(canonical(bound["peers"]["sink"])).hexdigest(), "before_positive": 10, "after_positive": 11, "after_denials": 11, "after_final_positive": 12}
    host = {**sink, "identity_sha256": bound["host_sink"]["identity_sha256"]}
    dns = [{"position": position, "transport": transport, "source_ip": bound["peers"]["allowed-team-a"]["pod_ip"],
        "server_ip": bound["addresses"]["dns_service"], "resolved_ip": bound["addresses"]["api_service"]}
        for position in ("before", "after") for transport in ("udp", "tcp")]
    return {"ready_before": copy.deepcopy(ready), "ready_after": copy.deepcopy(ready), "owned_sink": sink, "host_sink": host, "dns_controls": dns}


@pytest.mark.parametrize("fault", (None, "missing", "duplicate", "order", "refusal", "dns_error", "accepted", "wrong_source", "wrong_destination", "early", "late", "nan",
    "dispatch", "epoch", "missing_ready", "wrong_ready_binding", "sink_delivery", "sink_regression", "no_positive", "no_final_positive", "host_delivery", "sink_identity", "host_identity", "dns_missing", "dns_wrong_source", "dns_wrong_answer"))
def test_group_closure_requires_all_denials_independent_dispatch_and_live_sink_dns_controls(fault):
    bound = fixture()
    rows = [{"job_id": job["job_id"], "result": {**job["settings"], "outcome": "timeout", "elapsed_ms": 2000}} for job in subject.jobs(bound)]
    kwargs = controls(bound)
    if fault == "missing": rows.pop()
    elif fault == "duplicate": rows[-1] = copy.deepcopy(rows[0])
    elif fault == "order": rows.reverse()
    elif fault in {"refusal", "dns_error", "accepted"}: rows[0]["result"]["outcome"] = fault
    elif fault == "wrong_source": rows[0]["result"]["source_ip"] = "10.244.0.99"
    elif fault == "wrong_destination": rows[0]["result"]["destination_ip"] = "10.96.0.99"
    elif fault in {"early", "late", "nan"}: rows[0]["result"]["elapsed_ms"] = {"early": 0, "late": 5000, "nan": float("nan")}[fault]
    elif fault == "dispatch": kwargs["ready_after"]["counter"]["attempts"] += 1
    elif fault == "epoch": kwargs["ready_after"]["counter"]["epoch"] = "e" * 32
    elif fault == "missing_ready": kwargs["ready_before"] = {}
    elif fault == "wrong_ready_binding": kwargs["ready_after"]["binding_sha256"] = "e" * 64
    elif fault == "sink_delivery": kwargs["owned_sink"]["after_denials"] += 1
    elif fault == "sink_regression": kwargs["owned_sink"]["after_denials"] -= 1
    elif fault == "no_positive": kwargs["owned_sink"]["after_positive"] -= 1
    elif fault == "no_final_positive": kwargs["owned_sink"]["after_final_positive"] -= 1
    elif fault == "host_delivery": kwargs["host_sink"]["after_denials"] += 1
    elif fault == "sink_identity": kwargs["owned_sink"]["identity_sha256"] = "e" * 64
    elif fault == "host_identity": kwargs["host_sink"]["identity_sha256"] = "e" * 64
    elif fault == "dns_missing": kwargs["dns_controls"].pop()
    elif fault == "dns_wrong_source": kwargs["dns_controls"][0]["source_ip"] = "10.244.0.99"
    elif fault == "dns_wrong_answer": kwargs["dns_controls"][0]["resolved_ip"] = "1.1.1.1"
    if fault:
        with pytest.raises(ValueError): subject.validate_group(bound, rows, **kwargs)
    else:
        assert subject.validate_group(bound, rows, **kwargs) == {"binding_sha256": hashlib.sha256(canonical(bound)).hexdigest(),
            "denial_count": 20, "classifier_dispatch_delta": 0, "owned_sink_delivery_delta": 0, "host_sink_delivery_delta": 0}


def program(fault=None):
    events = []
    class Socket:
        def bind(self, address): self.source = address; events.append(("bind", address))
        def settimeout(self, seconds): assert seconds == 2.0
        def connect(self, address):
            events.append(("connect", address))
            if fault == "refused": raise ConnectionRefusedError()
            if fault == "unreachable": raise OSError()
            if fault != "accepted": raise TimeoutError()
        def sendto(self, raw, address):
            events.append(("dns", raw, address))
            assert raw.startswith(b'\x47\x52\x01\x00') and b'kubernetes' in raw and b'Authorization' not in raw
            return len(raw)
        def recvfrom(self, maximum):
            assert maximum == 512
            if fault == "refused": raise ConnectionRefusedError()
            if fault == "accepted": return b"reply", ("10.96.0.10", 53)
            raise TimeoutError()
        def getsockname(self): return ("10.244.0.99", 50000) if fault == "wrong_source" else self.source
        def close(self): events.append(("close",))
    def socket(family, kind):
        events.append(("socket", family, kind)); return Socket()
    tick = iter([100, 100.1 if fault == "early" else 105 if fault == "late" else 102])
    modules = {"socket": SimpleNamespace(socket=socket, AF_INET=2, SOCK_STREAM=1, SOCK_DGRAM=2),
        "time": SimpleNamespace(monotonic=lambda: next(tick)), "signal": SimpleNamespace(alarm=lambda _: pytest.fail("main must not run"))}
    def imports(name, *args, **kwargs):
        if name in modules: return modules[name]
        assert name in {"ipaddress", "json", "sys"}
        return builtins.__import__(name, *args, **kwargs)
    namespace = {"__name__": "synthetic_cni_library", "__builtins__": {**vars(builtins), "__import__": imports}}
    exec(compile(support.DENY_SCRIPT, "synthetic-cni-program", "exec"), namespace)  # noqa: S102 - fixed source, synthetic imports only
    return namespace["perform"], events


@pytest.mark.parametrize("transport", ("tcp", "udp"))
@pytest.mark.parametrize("fault", (None, "refused", "accepted", "wrong_source", "early", "late"))
def test_literal_remote_program_requires_original_ip_and_full_timeout_without_credentials_or_dns_resolution(transport, fault):
    perform, events = program(fault)
    settings = {"source_ip": "10.244.0.11", "destination_ip": "10.96.0.10", "port": 53, "transport": transport}
    if fault:
        with pytest.raises((ValueError, OSError)): perform(settings)
    else:
        assert perform(settings) == {**settings, "outcome": "timeout", "elapsed_ms": 2000.0}
    assert events[-1] == ("close",)
    assert events[1] == ("bind", ("10.244.0.11", 0))
    assert not any(event[0] == "dns" for event in events) if transport == "tcp" else True


@pytest.mark.parametrize("fault", ("extra", "port_bool", "source_loopback", "target_loopback", "target_multicast", "udp_alternate", "unknown_transport"))
def test_remote_settings_refuse_before_any_socket_is_created(fault):
    perform, events = program()
    value = {"source_ip": "10.244.0.11", "destination_ip": "10.96.0.10", "port": 53, "transport": "tcp"}
    if fault == "extra": value["bearer"] = "synthetic-must-never-be-sent"
    elif fault == "port_bool": value["port"] = True
    elif fault == "source_loopback": value["source_ip"] = "127.0.0.1"
    elif fault == "target_loopback": value["destination_ip"] = "127.0.0.1"
    elif fault == "target_multicast": value["destination_ip"] = "224.0.0.1"
    elif fault == "udp_alternate": value.update(transport="udp", port=8443)
    else: value["transport"] = "raw"
    with pytest.raises(ValueError): perform(value)
    assert events == []


@pytest.mark.parametrize("fault", (None, "initial_identity", "final_identity", "credential", "api_route", "unknown_job"))
def test_owned_static_api_reader_refuses_binding_or_route_changes_around_exact_literal_program(monkeypatch, fault):
    bound = fixture()
    state = Path("/synthetic/private-state")
    calls, captures = [], []
    def capture(**kwargs):
        assert kwargs == {"deadline": 12345}
        captures.append(1)
        result = copy.deepcopy(bound)
        if (fault == "initial_identity" and len(captures) == 1) or (fault == "final_identity" and len(captures) == 2): result["peers"]["classifier"]["process_start_ticks"] += 1
        return result
    monkeypatch.setattr(support, "private_bytes", lambda *_args, **_kwargs: b"changed" if fault == "credential" else b"static")
    def command(args, **kwargs):
        calls.append((args, kwargs))
        assert args[args.index("-c") + 1] == "classifier"
        assert args[-2] == support.DENY_SCRIPT
        job = subject.jobs(bound)[0]
        assert args[-1] == canonical(job["settings"]).decode()
        assert kwargs == {"maximum": 4096, "timeout": 7, "input_text": "static-private-kubeconfig"}
        return canonical({**job["settings"], "outcome": "timeout", "elapsed_ms": 2000}).decode()
    observer = SimpleNamespace(bound={"owner_id": bound["owner_id"], "node_id": bound["node_id"]}, _remaining=lambda: 10,
        deployment=SimpleNamespace(state=state, kubeconfig=state / "kubeconfig", bin=state / "bin"),
        _api_pins={"kubeconfig": hashlib.sha256(b"static").hexdigest()}, _api_private={"kubeconfig"}, _api_owner={}, _api_images={},
        _api_server="https://127.0.0.1:12345", _native=lambda *_: "https://127.0.0.1:" + ("54321" if fault == "api_route" else "12345"),
        _api_input="static-private-kubeconfig", _command=command)
    reader = support.CniSocketReader(observer, capture)
    if fault:
        with pytest.raises(RuntimeError): reader.probe_locked(bound, "unregistered" if fault == "unknown_job" else "egress_sink_service", deadline=12345)
        assert len(calls) == (1 if fault == "final_identity" else 0)
    else:
        result = reader.probe_locked(bound, "egress_sink_service", deadline=12345)
        assert result["result"]["outcome"] == "timeout" and len(calls) == 1 and len(captures) == 2
        assert calls[0][0][:9] == [state / "bin/kubectl", "--kubeconfig", "/dev/stdin", "--context", "guardrails-evolution", "--namespace", "gr-platform", "--request-timeout=6s", "exec"]
