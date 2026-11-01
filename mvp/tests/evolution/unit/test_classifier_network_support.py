"""Isolated readiness observation contracts; never Docker, Kubernetes or model."""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from guardrails.evolution.classifier_contract import canonical, identity

from mvp.tests.evolution.e2e import classifier_network_support as support

OWNER = "a" * 32
IMAGES = {name: "fixture/" + name + "@sha256:" + "b" * 64 for name in ("node", "runtime", "classifier")}


def uid(index):
    return "00000000-0000-0000-0000-" + str(index).zfill(12)


def metadata(name, index, **kwargs):
    return {"name": name, "namespace": support.NAMESPACE, "uid": uid(index), **kwargs}


def controlled(kind, name, index):
    return [{"kind": kind, "name": name, "uid": uid(index), "controller": True}]


def objects():
    rows = [{"kind": "Service", "metadata": metadata("classifier", 1, labels={"guardrails.evolution/owner": OWNER}),
        "spec": {"type": "ClusterIP", "clusterIP": "10.96.0.50", "selector": {"guardrails.evolution/component": "classifier"},
                 "ports": [{"name": "https", "port": 8443, "targetPort": 8443, "protocol": "TCP"}]}}]
    for index, name, component in ((10, "runtime-1", "runtime"), (20, "classifier", "classifier")):
        rows.extend([
            {"kind": "Deployment", "metadata": metadata(name, index, labels={"guardrails.evolution/owner": OWNER}), "spec": {"replicas": 1}},
            {"kind": "ReplicaSet", "metadata": metadata(name + "-rs", index + 1, ownerReferences=controlled("Deployment", name, index))},
            {"kind": "Pod", "metadata": metadata(name + "-pod", index + 2,
                ownerReferences=controlled("ReplicaSet", name + "-rs", index + 1),
                labels={"guardrails.evolution/instance": name, "guardrails.evolution/component": component}),
             "spec": {"nodeName": "guardrails-evolution", "serviceAccountName": name, "automountServiceAccountToken": False,
                "containers": [{"name": component, "image": IMAGES[component], "securityContext": {"readOnlyRootFilesystem": True}}]},
             "status": {"phase": "Running", "podIP": "10.244.0." + str(index), "containerStatuses": [
                {"name": component, "ready": True, "restartCount": 0, "containerID": "containerd://" + str(index // 10) * 64,
                 "state": {"running": {"startedAt": "2026-10-12T00:00:00Z"}}}]}}])
    rows.append({"kind": "EndpointSlice", "metadata": metadata("classifier-slice", 30,
        ownerReferences=controlled("Service", "classifier", 1), labels={"kubernetes.io/service-name": "classifier"}),
        "addressType": "IPv4", "ports": [{"name": "https", "protocol": "TCP", "port": 8443}],
        "endpoints": [{"addresses": ["10.244.0.20"], "conditions": {"ready": True, "serving": True, "terminating": False},
                       "targetRef": {"kind": "Pod", "namespace": support.NAMESPACE, "name": "classifier-pod", "uid": uid(22)}}]})
    return {"items": rows}


def select(value, kind, name):
    return next(row for row in value["items"] if row["kind"] == kind and row["metadata"]["name"] == name)


def test_actual_projection_binds_service_backend_owner_chain_and_exact_containers():
    value = objects()
    result, statuses = support.project_objects(value, OWNER, IMAGES, "runtime-1")
    assert result["service"] == {"uid": uid(1), "address": "10.96.0.50", "endpoint_slice_uid": uid(30)}
    assert result["pods"]["classifier"]["pod_uid"] == uid(22)
    assert result["pods"]["classifier"]["container_id"] == "containerd://" + "2" * 64
    assert set(statuses) == {"runtime", "classifier"}
    assert "spec_sha256" in result["pods"]["runtime-1"]


@pytest.mark.parametrize("fault", ["duplicate", "foreign_namespace", "wrong_owner", "wrong_service", "external_ip",
    "loopback", "deployment_replicas", "terminating", "extra_pod", "foreign_backend", "pod_owner", "replica_owner",
    "host_network", "wrong_node", "service_account", "token_mount", "wrong_image", "unready", "restarted_state",
    "bad_cid", "sidecar", "writable", "slice_owner", "slice_duplicate", "slice_extra_backend", "slice_unready",
    "slice_target", "slice_address", "slice_port"])
def test_projection_rejects_drift_or_ambiguous_live_targets(fault):
    value = objects()
    service = select(value, "Service", "classifier")
    target = select(value, "Deployment", "classifier")
    pod = select(value, "Pod", "classifier-pod")
    replica = select(value, "ReplicaSet", "classifier-rs")
    sliced = select(value, "EndpointSlice", "classifier-slice")
    if fault == "duplicate": value["items"].append(copy.deepcopy(service))
    elif fault == "foreign_namespace": service["metadata"]["namespace"] = "foreign"
    elif fault == "wrong_owner": target["metadata"]["labels"]["guardrails.evolution/owner"] = "foreign"
    elif fault == "wrong_service": service["spec"]["selector"] = {"different": "pod"}
    elif fault == "external_ip": service["spec"]["externalIPs"] = ["203.0.113.10"]
    elif fault == "loopback": service["spec"]["clusterIP"] = "127.0.0.1"
    elif fault == "deployment_replicas": target["spec"]["replicas"] = 2
    elif fault == "terminating": pod["metadata"]["deletionTimestamp"] = "2026-10-12T00:00:00Z"
    elif fault in {"extra_pod", "foreign_backend"}:
        extra = copy.deepcopy(pod); extra["metadata"]["name"] = "extra"
        if fault == "foreign_backend": extra["metadata"]["labels"]["guardrails.evolution/instance"] = "foreign"
        value["items"].append(extra)
    elif fault == "pod_owner": pod["metadata"]["ownerReferences"][0]["uid"] = uid(99)
    elif fault == "replica_owner": replica["metadata"]["ownerReferences"][0]["uid"] = uid(99)
    elif fault == "host_network": pod["spec"]["hostNetwork"] = True
    elif fault == "wrong_node": pod["spec"]["nodeName"] = "foreign"
    elif fault == "service_account": pod["spec"]["serviceAccountName"] = "foreign"
    elif fault == "token_mount": pod["spec"]["automountServiceAccountToken"] = True
    elif fault == "wrong_image": pod["spec"]["containers"][0]["image"] = "unbound:latest"
    elif fault == "unready": pod["status"]["containerStatuses"][0]["ready"] = False
    elif fault == "restarted_state": pod["status"]["containerStatuses"][0]["state"] = {"terminated": {}}
    elif fault == "bad_cid": pod["status"]["containerStatuses"][0]["containerID"] = "containerd://bad"
    elif fault == "sidecar": pod["spec"]["containers"].append(copy.deepcopy(pod["spec"]["containers"][0]))
    elif fault == "writable": pod["spec"]["containers"][0]["securityContext"]["readOnlyRootFilesystem"] = False
    elif fault == "slice_owner": sliced["metadata"]["ownerReferences"][0]["uid"] = uid(99)
    elif fault == "slice_duplicate":
        extra = copy.deepcopy(sliced); extra["metadata"]["name"] = "extra"; value["items"].append(extra)
    elif fault == "slice_extra_backend": sliced["endpoints"].append(copy.deepcopy(sliced["endpoints"][0]))
    elif fault == "slice_unready": sliced["endpoints"][0]["conditions"]["ready"] = False
    elif fault == "slice_target": sliced["endpoints"][0]["targetRef"]["uid"] = uid(99)
    elif fault == "slice_address": sliced["endpoints"][0]["addresses"] = ["10.244.0.99"]
    elif fault == "slice_port": sliced["ports"][0]["port"] = 80
    with pytest.raises(support.ClassifierObservationError):
        support.project_objects(value, OWNER, IMAGES, "runtime-1")


def script_fixture(monkeypatch):
    namespace = {"__name__": "isolated_probe_test"}
    exec(compile(support.READY_SCRIPT, "classifier-readiness-probe", "exec"), namespace)  # noqa: S102 - literal producer, all external boundaries mocked
    calls = []
    token = "fixture-private-token-" + "x" * 32
    ca = b"fixture-ca"
    settings = {"address": "10.96.0.50", "identity": identity(),
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "ca_sha256": hashlib.sha256(ca).hexdigest()}
    def read(path, maximum, expected):
        raw = ca if path == "/config/ca.crt" else token.encode()
        assert expected == hashlib.sha256(raw).hexdigest() and len(raw) <= maximum
        return raw
    monkeypatch.setitem(namespace, "read", read)
    body = canonical({"api_major": 1, "ready": True, "identity": identity()})
    response = SimpleNamespace(status=200, body=body, headers=[("content-type", "application/json"),
        ("content-length", str(len(body))), ("x-evolution-classifier-worker-epoch", "a" * 32),
        ("x-evolution-classifier-dispatch-attempts", "7")])
    response.begin = lambda: None
    response.getheaders = lambda: response.headers
    response.read = lambda maximum: response.body[:maximum]
    class Channel:
        def settimeout(self, value): calls.append(("timeout", value))
        def connect(self, value): calls.append(("connect", value))
        def close(self): calls.append(("close",))
        def version(self): return "TLSv1.3"
        def sendall(self, raw): calls.append(("send", raw))
        def __enter__(self): return self
        def __exit__(self, *unused): self.close()
    class Context:
        def __init__(self, protocol): calls.append(("context", protocol))
        def load_verify_locations(self, *, cadata): calls.append(("ca", cadata))
        def wrap_socket(self, raw, *, server_hostname):
            calls.append(("sni", server_hostname)); return raw
    monkeypatch.setattr(namespace["socket"], "socket", lambda *args: Channel())
    monkeypatch.setattr(namespace["socket"], "getaddrinfo", lambda *args: pytest.fail("no DNS"))
    monkeypatch.setattr(namespace["ssl"], "SSLContext", Context)
    monkeypatch.setattr(namespace["http"].client, "HTTPResponse", lambda channel: response)
    return namespace, settings, response, calls, token


def test_original_remote_probe_authenticates_direct_ip_and_emits_only_finite_projection(monkeypatch):
    namespace, settings, _, calls, token = script_fixture(monkeypatch)
    observed = namespace["perform"](settings)
    assert set(observed) == {"status", "tls", "epoch", "attempts", "identity_sha256", "elapsed_ms"}
    assert observed["attempts"] == 7 and observed["tls"] == "TLSv1.3"
    assert ("connect", ("10.96.0.50", 8443)) in calls and ("sni", support.HOST) in calls
    sent = next(row[1] for row in calls if row[0] == "send")
    assert b"GET /v1/classifier/ready HTTP/1.1" in sent and token.encode() in sent
    assert token not in json.dumps(observed) and "fixture-ca" not in json.dumps(observed)
    assert sum(row[0] == "send" for row in calls) == 1


@pytest.mark.parametrize("fault", ["status", "duplicate_counter", "missing_counter", "duplicate_length", "chunked",
    "gzip", "large_length", "length", "identity", "false_ready", "bool_version", "extra_body", "bad_epoch", "negative", "overflow"])
def test_remote_probe_rejects_partial_or_ambiguous_success(monkeypatch, fault):
    namespace, settings, response, calls, _ = script_fixture(monkeypatch)
    if fault == "status": response.status = 401
    elif fault == "duplicate_counter": response.headers.append(response.headers[-1])
    elif fault == "missing_counter": response.headers.pop()
    elif fault == "duplicate_length": response.headers.append(response.headers[1])
    elif fault == "chunked": response.headers.append(("transfer-encoding", "chunked"))
    elif fault == "gzip": response.headers.append(("content-encoding", "gzip"))
    elif fault == "large_length": response.headers[1] = ("content-length", "9999")
    elif fault == "length": response.headers[1] = ("content-length", "1")
    elif fault in {"identity", "false_ready", "bool_version", "extra_body"}:
        body = json.loads(response.body)
        if fault == "identity": body["identity"]["model_revision"] = "foreign"
        elif fault == "false_ready": body["ready"] = False
        elif fault == "bool_version": body["api_major"] = True
        else: body["secret"] = "never-retained"
        response.body = canonical(body); response.headers[1] = ("content-length", str(len(response.body)))
    elif fault == "bad_epoch": response.headers[-2] = (response.headers[-2][0], "no")
    elif fault == "negative": response.headers[-1] = (response.headers[-1][0], "-1")
    elif fault == "overflow": response.headers[-1] = (response.headers[-1][0], str(2**63))
    with pytest.raises(ValueError): namespace["perform"](settings)
    assert ("close",) in calls


def observer_fixture(monkeypatch):
    projected, statuses = support.project_objects(objects(), OWNER, IMAGES, "runtime-1")
    binding = {"owner_id": OWNER, "node_id": "c" * 64, "files": {
        "assembly/private/classifier.token": "d" * 64, "assembly/tls/gr-platform/classifier/ca.crt": "e" * 64}, **projected}
    cluster = SimpleNamespace(profile="classifier", deployment=SimpleNamespace(env={}))
    observer = support.ReadinessObserver(cluster)
    monkeypatch.setattr(observer, "_capture", lambda: (copy.deepcopy(binding), statuses, IMAGES))
    return observer, binding


def test_bind_uses_existing_oci_verifier_on_exact_native_node_only(monkeypatch):
    observer, binding = observer_fixture(monkeypatch)
    commands = []
    def verify(deployment, reference, status):
        assert status["ready"] is True
        assert deployment.run(["docker", "exec", "guardrails-evolution", "crictl", "inspect", status["containerID"]], timeout=30) == (0, "observed")
        return {"expected_reference": reference}
    observer.deployment.support = lambda name: SimpleNamespace(verify_container=verify)
    monkeypatch.setattr(observer, "_command", lambda args, **options: commands.append((args, options)) or "observed")
    result = observer.bind_locked()
    assert len(commands) == 2 and all(row[0][:5] == ["docker", "--host", support.SOCKET, "exec", binding["node_id"]] for row in commands)
    assert result["binding"] == binding
    with pytest.raises(support.ClassifierObservationError): observer.bind_locked()


def test_sample_unchanged_remote_program_and_public_arguments_are_delegated_once(monkeypatch):
    observer, binding = observer_fixture(monkeypatch)
    observer.bound = binding
    calls = []
    value = {"status": 200, "tls": "TLSv1.3", "epoch": "a" * 32, "attempts": 1,
        "identity_sha256": hashlib.sha256(canonical(identity())).hexdigest(), "elapsed_ms": 1.0}
    monkeypatch.setattr(observer, "_kube", lambda *args, **kw: calls.append((args, kw)) or canonical(value).decode())
    sample = observer.sample_locked()
    assert len(calls) == 1 and calls[0][0][:5] == ("exec", "runtime-1-pod", "-c", "runtime", "--")
    assert calls[0][0][5:9] == ("python", "-B", "-c", support.READY_SCRIPT)
    assert set(json.loads(calls[0][0][-1])) == {"address", "identity", "token_sha256", "ca_sha256"}
    assert sample["counter"] == {"epoch": "a" * 32, "attempts": 1}
    assert support.observed_attempts(sample, sample) == 0


@pytest.mark.parametrize("fault", ["node", "pod", "worker", "counter", "missing", "extra", "bad_identity"])
def test_no_success_when_owner_or_counter_changes(monkeypatch, fault):
    observer, binding = observer_fixture(monkeypatch)
    observer.bound = binding
    valid = {"status": 200, "tls": "TLSv1.3", "epoch": "a" * 32, "attempts": 1,
        "identity_sha256": hashlib.sha256(canonical(identity())).hexdigest(), "elapsed_ms": 1.0}
    monkeypatch.setattr(observer, "_kube", lambda *args, **kw: canonical(valid).decode())
    before = observer.sample_locked()
    if fault in {"node", "pod"}:
        changed = copy.deepcopy(binding)
        if fault == "node": changed["node_id"] = "f" * 64
        else: changed["pods"]["classifier"]["container_id"] = "containerd://" + "f" * 64
        monkeypatch.setattr(observer, "_capture", lambda: (changed, {}, IMAGES))
    elif fault == "worker": valid["epoch"] = "b" * 32
    elif fault == "counter": valid["attempts"] = 0
    elif fault == "missing": del valid["attempts"]
    elif fault == "extra": valid["private"] = "never-retained"
    else: valid["identity_sha256"] = "f" * 64
    with pytest.raises((support.ClassifierObservationError, ValueError)):
        support.observed_attempts(before, observer.sample_locked())


@pytest.mark.parametrize("key,value", [("DOCKER_HOST", "tcp://foreign:2375"), ("DOCKER_CONTEXT", "other"),
    ("DOCKER_TLS_VERIFY", "1"), ("DOCKER_CERT_PATH", "/foreign")])
def test_ambient_docker_routing_refuses_before_command(monkeypatch, key, value):
    observer, _ = observer_fixture(monkeypatch)
    observer.deployment.env[key] = value
    with pytest.raises(support.ClassifierObservationError): observer._routing()
