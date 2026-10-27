"""Synthetic identity/group integration; no actual process or network access."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from evolution import classifier_cni_evidence as evidence
from guardrails.evolution.classifier_contract import canonical

from mvp.tests.evolution.e2e import classifier_cni_bindings as bindings
from mvp.tests.evolution.e2e import classifier_cni_group as group
from mvp.tests.evolution.unit.test_classifier_cni_evidence import fixture


def objects():
    bound = fixture()
    applications, pods, rows = [], [], []
    for name, (namespace, deployment, component) in evidence.ORIGINS.items():
        peer = bound["peers"][name]
        applications.append({"namespace": namespace, "deployment": deployment, "pod_name": peer["pod_name"],
            "pod_uid": peer["pod_uid"], "containers": [{"name": component, "container_id": peer["container_id"]}]})
        pods.append({"kind": "Pod", "metadata": {"uid": peer["pod_uid"]}, "status": {"podIP": peer["pod_ip"]}})
    for index, (key, (namespace, name, deployment, ports)) in enumerate(bindings.SERVICES.items(), 20):
        pod = next((p for p in applications if (p["namespace"], p["deployment"]) == (namespace, deployment)), None)
        if pod is None:
            pod = {"namespace": namespace, "deployment": deployment, "pod_name": deployment + "-abc123-def45",
                "pod_uid": "00000000-0000-0000-0000-" + str(index).zfill(12)}
            applications.append(pod)
            pods.append({"kind": "Pod", "metadata": {"uid": pod["pod_uid"]}, "status": {"podIP": "10.244.0." + str(index)}})
        address = next(p["status"]["podIP"] for p in pods if p["metadata"]["uid"] == pod["pod_uid"])
        uid = "10000000-0000-0000-0000-" + str(index).zfill(12)
        mapped = [{"name": "port-" + str(p), "port": p, "targetPort": p, "protocol": "TCP"} for p in ports]
        rows.append({"kind": "Service", "metadata": {"namespace": namespace, "name": name, "uid": uid,
            "labels": {"guardrails.evolution/owner": bound["owner_id"]}}, "spec": {"type": "ClusterIP", "clusterIP": bound["addresses"][key],
            "clusterIPs": [bound["addresses"][key]], "ipFamilies": ["IPv4"], "ports": mapped,
            "selector": {"app": "tls-probe"} if deployment == "tls-probe" else {"guardrails.evolution/component": deployment}}})
        rows.append({"kind": "EndpointSlice", "metadata": {"namespace": namespace, "name": name + "-abcde",
            "uid": "20000000-0000-0000-0000-" + str(index).zfill(12), "labels": {"kubernetes.io/service-name": name},
            "ownerReferences": [{"controller": True, "kind": "Service", "name": name, "uid": uid}]}, "addressType": "IPv4",
            "ports": [{k: p[k] for k in ("name", "port", "protocol")} for p in mapped],
            "endpoints": [{"addresses": [address], "conditions": {"ready": True, "serving": True, "terminating": False},
                "targetRef": {"kind": "Pod", "namespace": namespace, "name": pod["pod_name"], "uid": pod["pod_uid"]}}]})
    for index, (key, namespace, name, ports) in enumerate((("api_service", "default", "kubernetes", [(443, "TCP")]),
            ("dns_service", "kube-system", "kube-dns", [(53, "TCP"), (53, "UDP"), (9153, "TCP")])), 30):
        rows.append({"kind": "Service", "metadata": {"namespace": namespace, "name": name,
            "uid": "30000000-0000-0000-0000-" + str(index).zfill(12)}, "spec": {"type": "ClusterIP", "clusterIP": bound["addresses"][key],
            "clusterIPs": [bound["addresses"][key]], "ipFamilies": ["IPv4"], "ports": [{"port": p, "protocol": protocol} for p, protocol in ports]}})
    rows.append({"kind": "NetworkPolicy", "metadata": {"namespace": "gr-platform", "name": "classifier",
        "uid": "40000000-0000-0000-0000-000000000001"}, "spec": {"podSelector": {"matchLabels": {"guardrails.evolution/component": "classifier"}}, "egress": []}})
    return {"items": rows}, {"applications": applications, "inventory_sha256": "e" * 64}, {"items": pods}


@pytest.mark.parametrize("fault", (None, "dns_without_metrics", "duplicate", "missing_service", "foreign_owner", "selector", "external", "dualstack", "headless", "deleting", "service_uid", "service_port", "slice_count", "slice_owner", "slice_uid", "slice_ports", "slice_ipv6", "endpoint_count", "endpoint_not_ready", "endpoint_terminating", "endpoint_not_serving", "endpoint_ip", "endpoint_uid", "endpoint_name", "endpoint_namespace", "missing_policy", "policy_uid", "api_port", "dns_missing_tcp", "dns_extra_port", "dns_duplicate", "unknown_kind"))
def test_original_service_endpoint_and_policy_projection_rejects_ambiguous_or_retargeted_network(fault):
    network, inventory, pods = objects()
    service, endpoint = network["items"][:2]
    dns = next(r for r in network["items"] if r["metadata"]["name"] == "kube-dns")
    if fault == "duplicate": network["items"].append(copy.deepcopy(service))
    elif fault == "missing_service": network["items"].remove(service)
    elif fault == "foreign_owner": service["metadata"]["labels"]["guardrails.evolution/owner"] = "f" * 32
    elif fault == "selector": service["spec"]["selector"] = {"app": "foreign"}
    elif fault == "external": service["spec"]["externalIPs"] = ["1.1.1.1"]
    elif fault == "dualstack": service["spec"]["ipFamilies"].append("IPv6")
    elif fault == "headless": service["spec"]["clusterIP"] = "None"
    elif fault == "deleting": service["metadata"]["deletionTimestamp"] = "2026-10-12T00:00:00Z"
    elif fault == "service_uid": service["metadata"]["uid"] = "bad"
    elif fault == "service_port": service["spec"]["ports"][0]["targetPort"] = 9443
    elif fault == "slice_count": network["items"].append({**copy.deepcopy(endpoint), "metadata": {**endpoint["metadata"], "name": "other"}})
    elif fault == "slice_owner": endpoint["metadata"]["ownerReferences"][0]["uid"] = "50000000-0000-0000-0000-000000000001"
    elif fault == "slice_uid": endpoint["metadata"]["uid"] = "bad"
    elif fault == "slice_ports": endpoint["ports"][0]["port"] = 9443
    elif fault == "slice_ipv6": endpoint["addressType"] = "IPv6"
    elif fault == "endpoint_count": endpoint["endpoints"].append(copy.deepcopy(endpoint["endpoints"][0]))
    elif fault == "endpoint_not_ready": endpoint["endpoints"][0]["conditions"]["ready"] = False
    elif fault == "endpoint_terminating": endpoint["endpoints"][0]["conditions"]["terminating"] = True
    elif fault == "endpoint_not_serving": endpoint["endpoints"][0]["conditions"]["serving"] = False
    elif fault == "endpoint_ip": endpoint["endpoints"][0]["addresses"] = ["10.244.0.99"]
    elif fault in {"endpoint_uid", "endpoint_name", "endpoint_namespace"}: endpoint["endpoints"][0]["targetRef"][fault.removeprefix("endpoint_")] = "foreign"
    elif fault == "missing_policy": network["items"].pop()
    elif fault == "policy_uid": network["items"][-1]["metadata"]["uid"] = "bad"
    elif fault == "api_port": next(r for r in network["items"] if r["metadata"]["name"] == "kubernetes")["spec"]["ports"][0]["port"] = 80
    elif fault == "dns_missing_tcp": dns["spec"]["ports"].pop(0)
    elif fault == "dns_extra_port": dns["spec"]["ports"].append({"port": 443, "protocol": "TCP"})
    elif fault == "dns_duplicate": dns["spec"]["ports"].append(copy.deepcopy(dns["spec"]["ports"][0]))
    elif fault == "dns_without_metrics": dns["spec"]["ports"].pop()
    elif fault == "unknown_kind": network["items"].append({"kind": "Secret"})
    if fault not in (None, "dns_without_metrics"):
        with pytest.raises((ValueError, RuntimeError)): bindings.network_projection(network, inventory, pods, owner=fixture()["owner_id"])
    else:
        addresses, closure = bindings.network_projection(network, inventory, pods, owner=fixture()["owner_id"])
        assert addresses == fixture()["addresses"] and len(closure["services"]) == 6 and len(closure["network_policies"]) == 1
        assert closure["services"]["classifier_service"]["pod_uid"] == fixture()["peers"]["classifier"]["pod_uid"]


def reader_fixture(monkeypatch):
    network, inventory, pods = objects()
    bound = fixture()
    before = {"owner_id": bound["owner_id"], "node_id": bound["node_id"], "files": {"synthetic": "b" * 64}}
    bound["readiness_binding_sha256"] = hashlib.sha256(canonical(before)).hexdigest()
    observer = SimpleNamespace(bound=before, deployment=SimpleNamespace(state=Path("/synthetic")), _remaining=lambda: 1,
        _capture=lambda: (copy.deepcopy(before), None, None),
        _files=lambda: ({"owner_id": before["owner_id"], "container_id": before["node_id"]}, None, {"synthetic": "b" * 64}))
    images = {"binding_sha256": bound["readiness_binding_sha256"], "inventory_sha256": inventory["inventory_sha256"], "identities": [], "identities_sha256": "a" * 64}
    reader = bindings.CniBindingReader(observer, images, SimpleNamespace(identity=lambda: bound["host_sink"]))
    monkeypatch.setattr(reader.resources, "_inventory", lambda _: (copy.deepcopy(inventory), copy.deepcopy(pods)))
    monkeypatch.setattr(reader, "_network", lambda: copy.deepcopy(network))
    monkeypatch.setattr(bindings.resource, "image_closure", lambda *_: "a" * 64)
    monkeypatch.setattr(bindings, "private_bytes", lambda *_args, **_kwargs: b"synthetic-ca")
    calls = []
    def process(pod, _container):
        name = next(name for name, peer in bound["peers"].items() if peer["pod_uid"] == pod["pod_uid"])
        calls.append(name)
        peer = bound["peers"][name]
        return {"pid": 100 + len(name), "process_start_ticks": peer["process_start_ticks"], "netns_inode": peer["netns_inode"]}
    monkeypatch.setattr(reader, "_process", process)
    return reader, calls, network, inventory, pods


def test_original_binding_closes_all_six_processes_twice_then_checks_selected_native_processes_and_all_routing(monkeypatch):
    reader, calls, *_ = reader_fixture(monkeypatch)
    value = reader.bind_locked(deadline=500)
    assert calls == list(evidence.ORIGINS) * 2 and value["closure"]["native_images_sha256"] == "a" * 64
    calls.clear()
    assert reader.recheck_locked(deadline=500, origins=("runtime-1", "classifier")) == value["binding"]
    assert calls == ["classifier", "runtime-1"]
    calls.clear()
    assert reader(deadline=500) == value["binding"] and calls == list(evidence.ORIGINS)


@pytest.mark.parametrize("fault", ("image_inventory", "image_identity", "image_binding", "image_extra", "observer", "inventory_after", "process_after", "network_after", "host_after", "ca_after"))
def test_full_initial_capture_cannot_seal_changed_images_processes_network_or_private_state(monkeypatch, fault):
    reader, _calls, network, inventory, _pods = reader_fixture(monkeypatch)
    if fault == "image_inventory": reader.image_binding["inventory_sha256"] = "f" * 64
    elif fault == "image_identity": reader.image_binding["identities_sha256"] = "f" * 64
    elif fault == "image_binding": reader.image_binding["binding_sha256"] = "f" * 64
    elif fault == "image_extra": reader.image_binding["unbound"] = True
    elif fault == "observer": monkeypatch.setattr(reader.observer, "_capture", lambda: ({}, None, None))
    else:
        original = reader._capture
        count = [0]
        def capture(**kwargs):
            result = original(**kwargs)
            count[0] += 1
            if count[0] == 1:
                if fault == "inventory_after": inventory["inventory_sha256"] = "f" * 64
                elif fault == "process_after": monkeypatch.setattr(reader, "_process", lambda *_: {"pid": 1, "process_start_ticks": 1, "netns_inode": 1})
                elif fault == "network_after": network["items"][-1]["spec"]["egress"] = [{}]
                elif fault == "host_after": reader.host_sink.identity = lambda: {"address": "192.168.51.1", "port": 54321, "identity_sha256": "f" * 64}
                elif fault == "ca_after": monkeypatch.setattr(bindings, "private_bytes", lambda *_args, **_kwargs: b"changed")
            return result
        monkeypatch.setattr(reader, "_capture", capture)
    with pytest.raises((RuntimeError, ValueError)): reader.bind_locked(deadline=500)
    assert reader.bound is None and reader.closure is None


@pytest.mark.parametrize("fault", ("owner", "node", "files", "inventory", "images", "policy", "pod_ip", "pod_uid", "container_id", "process", "ca", "host", "empty_origins", "duplicate_origins", "unknown_origin"))
def test_per_row_recheck_rejects_current_identity_drift_without_repeating_native_oci_inspection(monkeypatch, fault):
    reader, calls, network, inventory, pods = reader_fixture(monkeypatch)
    reader.bind_locked(deadline=500)
    calls.clear()
    origins = ("classifier",)
    if fault in {"owner", "node", "files"}:
        owner, _, pins = reader.observer._files()
        if fault == "owner": owner["owner_id"] = "f" * 32
        elif fault == "node": owner["container_id"] = "f" * 64
        else: pins["synthetic"] = "f" * 64
        reader.observer._files = lambda: (owner, None, pins)
    elif fault == "inventory": inventory["inventory_sha256"] = "f" * 64
    elif fault == "images": monkeypatch.setattr(bindings.resource, "image_closure", lambda *_: "f" * 64)
    elif fault == "policy": network["items"][-1]["spec"]["egress"] = [{}]
    elif fault == "pod_ip": pods["items"][0]["status"]["podIP"] = "10.244.0.99"
    elif fault == "pod_uid": inventory["applications"][0]["pod_uid"] = "50000000-0000-0000-0000-000000000001"
    elif fault == "container_id": inventory["applications"][0]["containers"][0]["container_id"] = "f" * 64
    elif fault == "process": monkeypatch.setattr(reader, "_process", lambda *_: {"pid": 1, "process_start_ticks": 1, "netns_inode": 1})
    elif fault == "ca": monkeypatch.setattr(bindings, "private_bytes", lambda *_args, **_kwargs: b"changed")
    elif fault == "host": reader.host_sink.identity = dict
    elif fault == "empty_origins": origins = ()
    elif fault == "duplicate_origins": origins = ("classifier", "classifier")
    else: origins = ("foreign",)
    with pytest.raises((ValueError, RuntimeError)): reader.recheck_locked(deadline=500, origins=origins)


@pytest.mark.parametrize("fault", (None, "stopped", "cid", "container", "pod", "pid", "pid_stat", "start_changed", "namespace"))
def test_native_process_identity_uses_original_cri_container_and_stable_start_ticks(monkeypatch, fault):
    observer = SimpleNamespace()
    reader = bindings.CniBindingReader(observer, {}, None)
    pod, container = {"pod_uid": "synthetic-pod"}, {"name": "classifier", "container_id": "c" * 64}
    status = {"id": container["container_id"], "state": "CONTAINER_RUNNING", "metadata": {"name": "classifier"}, "labels": {"io.kubernetes.pod.uid": "synthetic-pod"}}
    if fault == "stopped": status["state"] = "CONTAINER_EXITED"
    elif fault == "cid": status["id"] = "f" * 64
    elif fault == "container": status["metadata"]["name"] = "foreign"
    elif fault == "pod": status["labels"]["io.kubernetes.pod.uid"] = "foreign"
    heads = []
    def node(*args, **_kwargs):
        if args[0] == "crictl": return canonical({"status": status, "info": {"pid": True if fault == "pid" else 123}}).decode()
        if args[0] == "readlink": return "mnt:[2000]" if fault == "namespace" else "net:[2000]\n"
        assert args == ("head", "-c", "4096", "/proc/123/stat")
        heads.append(1)
        return ("999" if fault == "pid_stat" else "123") + " (model worker) S " + " ".join(["0"] * 18 + ["1001" if fault == "start_changed" and len(heads) > 1 else "1000"]) + "\n"
    monkeypatch.setattr(reader.resources, "_node", node)
    if fault:
        with pytest.raises((RuntimeError, ValueError)): reader._process(pod, container)
    else: assert reader._process(pod, container) == {"pid": 123, "process_start_ticks": 1000, "netns_inode": 2000}


def group_fixture(monkeypatch, fault=None):
    bound, events, now = fixture(), [], [100.0]
    ready = {"binding_sha256": bound["readiness_binding_sha256"], "counter": {"epoch": "d" * 32, "attempts": 0}, "tls": "TLSv1.3", "ready_elapsed_ms": 5}
    counts = {"8443": 10, "9443": 0, "8445": 0}
    class Host:
        def __init__(self):
            self.descriptor = {"synthetic": True}
            self.accepted = 20
        def open_locked(self, *, deadline):
            assert deadline == 220; events.append("open")
            if fault == "open": raise RuntimeError("original")
        def count(self): return self.accepted
        def positive(self): self.accepted += 1; return self.accepted
        def close(self):
            events.append("close")
            if fault in {"cleanup", "dual_failure"}: raise RuntimeError("cleanup")
    class Capture:
        def __init__(self, *_): pass
        def bind_locked(self, *, deadline):
            assert deadline == 220
            if fault == "binding": raise RuntimeError("original")
            return {"binding": bound, "closure": {"synthetic": True}}
        def recheck_locked(self, *, deadline, origins):
            assert deadline == 220 and len(origins) == 1; events.append(("recheck", origins)); return bound
        def __call__(self, *, deadline):
            assert deadline == 220; events.append("final_closure")
            return {} if fault == "closure" else bound
    class Controls:
        def __init__(self, _): self.readies = 0; self.sinks = 0
        def ready(self, runtime, *, deadline):
            assert deadline == 220; events.append(("ready", runtime)); self.readies += 1
            value = copy.deepcopy(ready)
            if fault == "epoch" and self.readies > 2: value["counter"]["epoch"] = "e" * 32
            if fault == "dispatch" and self.readies > 2: value["counter"]["attempts"] = 1
            if fault == "ready": raise RuntimeError("original")
            return value
        def dns(self, transport, position, *, deadline):
            assert deadline == 220; events.append(("dns", transport, position))
            if fault == "dns": raise RuntimeError("original")
            return {"position": position, "transport": transport, "source_ip": bound["peers"]["allowed-team-a"]["pod_ip"], "server_ip": bound["addresses"]["dns_service"], "resolved_ip": bound["addresses"]["api_service"]}
        def sink(self, *, positive, deadline):
            assert deadline == 220; self.sinks += 1
            if positive: counts["8443"] += 1
            if fault == "sink_delivery" and self.sinks == 3: counts["8443"] += 1
            if fault == "sink_alternate" and self.sinks == 3: counts["9443"] += 1
            if fault == "sink_positive" and self.sinks == 2: counts["8443"] -= 1
            return {"counts": copy.deepcopy(counts), "tls": "TLSv1.3"}
    class Socket:
        def __init__(self, _, capture): self.capture = capture
        def probe_locked(self, current, job_id, *, deadline):
            assert current == bound and deadline == 220
            self.capture(deadline=deadline)
            if fault in {"denial", "dual_failure"} and len([e for e in events if isinstance(e, tuple) and e[0] == "row"]) == 5: raise RuntimeError("original")
            events.append(("row", job_id))
            job = next(j for j in evidence.jobs(bound) if j["job_id"] == job_id)
            now[0] += 2
            return {"job_id": job_id, "result": {**job["settings"], "outcome": "timeout", "elapsed_ms": 2000}}
    monkeypatch.setattr(group, "HostSink", Host)
    monkeypatch.setattr(group, "CniBindingReader", Capture)
    monkeypatch.setattr(group, "CniControls", Controls)
    monkeypatch.setattr(group, "CniSocketReader", Socket)
    ticks = [0]
    def clock():
        ticks[0] += 1
        return 221 if fault == "deadline" and ticks[0] > 1 else now[0]
    monkeypatch.setattr(group.time, "monotonic", clock)
    observer = group.CniObservation(SimpleNamespace(bound={"synthetic": True}), {"synthetic": True})
    return observer, events


@pytest.mark.parametrize("fault", (None, "open", "binding", "ready", "dns", "denial", "sink_delivery", "sink_alternate", "sink_positive", "dispatch", "epoch", "closure", "cleanup", "dual_failure", "deadline"))
def test_full_group_preserves_partial_progress_original_failure_cleanup_and_one_unchanged_deadline(monkeypatch, fault):
    runner, events = group_fixture(monkeypatch, fault)
    if fault:
        with pytest.raises((RuntimeError, ValueError)) as exc: runner.run_locked()
        assert runner.value["status"] == "failed"
        assert runner.value["cleanup"] == ("failed" if fault in {"cleanup", "dual_failure"} else "closed")
        if fault in {"denial", "dual_failure"}: assert len(runner.value["rows"]) == 5
        if fault == "dual_failure": assert str(exc.value) == "original" and exc.value.__notes__ == ["CNI cleanup also failed: RuntimeError"]
        if fault in {"cleanup", "deadline"}: assert runner.value["stage"] == "cleanup"
    else:
        value = runner.run_locked()
        assert value["status"] == "observed" and value["stage"] == "complete" and value["cleanup"] == "closed"
        assert len(value["rows"]) == 20 and value["elapsed_ms"] == 40000
        assert set(value["proofs"]) == {"runtime-1", "runtime-2"}
        assert all(p["classifier_dispatch_delta"] == 0 for p in value["proofs"].values())
        assert events[-2:] == ["final_closure", "close"]
        with pytest.raises(RuntimeError): runner.run_locked()
    assert events.count("close") == 1
    with pytest.raises(RuntimeError): runner.run_locked()
