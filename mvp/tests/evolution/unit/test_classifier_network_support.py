"""Isolated readiness observation contracts; never Docker, Kubernetes or model."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
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


def write_private(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


@pytest.fixture
def static_fixture(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    now = datetime.now(UTC)
    ca_key, client_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-ca")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(1).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(ca_key, hashes.SHA256()))
    client = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-admin")]))
        .issuer_name(ca_name).public_key(client_key.public_key()).serial_number(2)
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1)).sign(ca_key, hashes.SHA256()))
    material = {"certificate-authority": ca.public_bytes(serialization.Encoding.PEM),
        "client-certificate": client.public_bytes(serialization.Encoding.PEM),
        "client-key": client_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())}
    config = {"apiVersion": "v1", "kind": "Config", "preferences": {}, "current-context": "guardrails-evolution",
        "clusters": [{"name": "guardrails-evolution", "cluster": {"server": "https://127.0.0.1:44634",
            "certificate-authority-data": base64.b64encode(material["certificate-authority"]).decode()}}],
        "contexts": [{"name": "guardrails-evolution", "context": {"cluster": "guardrails-evolution", "user": "guardrails-evolution", "namespace": "default"}}],
        "users": [{"name": "guardrails-evolution", "user": {name + "-data": base64.b64encode(material[name]).decode()
            for name in ("client-certificate", "client-key")}}]}
    return tmp_path, config, material


def validate_fixture(fixture):
    state, config, _ = fixture
    return support.static_kubeconfig(canonical(config), state=state, server="https://127.0.0.1:44634", deadline=time.monotonic() + 10)


def test_static_embedded_credentials_validate_and_only_public_pins_are_retained(static_fixture):
    text, pins, private = validate_fixture(static_fixture)
    assert json.loads(text) == static_fixture[1]
    assert pins == {"kubeconfig": hashlib.sha256(canonical(static_fixture[1])).hexdigest()}
    assert private == {"kubeconfig"}
    assert "PRIVATE KEY" not in json.dumps(pins)


def test_standard_minikube_yaml_paths_and_inert_metadata_are_flattened(static_fixture):
    state, _, material = static_fixture
    paths = {name: write_private(state / ".minikube" / (name + ".pem"), raw) for name, raw in material.items()}
    raw = ("apiVersion: v1\nclusters:\n- cluster:\n"
        f"    certificate-authority: {paths['certificate-authority']}\n"
        "    extensions:\n    - extension:\n        last-update: Sun, 11 Oct 2026 19:00:00 UTC\n"
        "        provider: minikube.sigs.k8s.io\n        version: v1.37.0\n      name: cluster_info\n"
        "    server: https://127.0.0.1:44634\n  name: guardrails-evolution\ncontexts:\n- context:\n"
        "    cluster: guardrails-evolution\n    extensions:\n    - extension:\n        last-update: Sun, 11 Oct 2026 19:00:00 UTC\n"
        "        provider: minikube.sigs.k8s.io\n        version: v1.37.0\n      name: context_info\n"
        "    namespace: default\n    user: guardrails-evolution\n  name: guardrails-evolution\ncurrent-context: guardrails-evolution\n"
        "kind: Config\npreferences: {}\nusers:\n- name: guardrails-evolution\n  user:\n"
        f"    client-certificate: {paths['client-certificate']}\n    client-key: {paths['client-key']}\n").encode()
    text, pins, private = support.static_kubeconfig(raw, state=state, server="https://127.0.0.1:44634", deadline=time.monotonic() + 10)
    result = json.loads(text)
    assert result == static_fixture[1]
    assert len(pins) == 4 and private == {"kubeconfig", ".minikube/client-key.pem"}
    assert "extensions" not in text and str(state) not in text


@pytest.mark.parametrize("fault", ["exec", "token", "token_file", "auth_provider", "username", "impersonate", "proxy",
    "insecure", "sni_override", "external_server", "wrong_port", "extra_cluster", "extra_user", "extra_context",
    "wrong_context", "wrong_user", "namespace", "preferences", "extension_name", "extension_provider", "extension_field",
    "external_file", "both_auth", "invalid_base64", "duplicate_key", "wrong_ca", "wrong_key", "expired"])
def test_kubeconfig_refuses_unbound_route_dynamic_auth_or_invalid_static_identity(static_fixture, monkeypatch, fault):
    state, value, material = static_fixture
    cluster, context, user = value["clusters"][0]["cluster"], value["contexts"][0]["context"], value["users"][0]["user"]
    if fault in {"exec", "token", "token_file", "auth_provider", "username", "impersonate"}:
        user[{"token_file": "tokenFile", "auth_provider": "auth-provider", "impersonate": "as"}.get(fault, fault)] = {"command": "never-run"} if fault == "exec" else "forbidden"
    elif fault in {"proxy", "insecure", "sni_override"}:
        cluster[{"proxy": "proxy-url", "insecure": "insecure-skip-tls-verify", "sni_override": "tls-server-name"}[fault]] = True if fault == "insecure" else "foreign"
    elif fault in {"external_server", "wrong_port"}: cluster["server"] = "https://foreign.invalid:443" if fault == "external_server" else "https://127.0.0.1:44635"
    elif fault.startswith("extra_"): value[{"extra_cluster": "clusters", "extra_user": "users", "extra_context": "contexts"}[fault]].append(copy.deepcopy(value[{"extra_cluster": "clusters", "extra_user": "users", "extra_context": "contexts"}[fault]][0]))
    elif fault == "wrong_context": value["current-context"] = "foreign"
    elif fault == "wrong_user": context["user"] = "foreign"
    elif fault == "namespace": context["namespace"] = "foreign"
    elif fault == "preferences": value["preferences"] = {"colors": True}
    elif fault.startswith("extension_"):
        ext = {"name": "cluster_info", "extension": {"provider": "minikube.sigs.k8s.io", "version": "v1.37.0", "last-update": "now"}}
        cluster["extensions"] = [ext]
        if fault == "extension_name": ext["name"] = "exec"
        elif fault == "extension_provider": ext["extension"]["provider"] = "foreign"
        else: ext["extension"]["exec"] = "forbidden"
    elif fault == "external_file": del cluster["certificate-authority-data"]; cluster["certificate-authority"] = str(state / "foreign.pem")
    elif fault == "both_auth": user["client-key"] = str(state / ".minikube/key")
    elif fault == "invalid_base64": user["client-key-data"] = "!"
    elif fault == "wrong_ca": cluster["certificate-authority-data"] = user["client-certificate-data"]
    elif fault == "wrong_key": user["client-key-data"] = base64.b64encode(material["client-certificate"]).decode()
    elif fault == "expired":
        class Future:
            @staticmethod
            def now(tz): return datetime.now(tz) + timedelta(days=2)
        monkeypatch.setattr(support, "datetime", Future)
    raw = canonical(value)
    if fault == "duplicate_key": raw = b'{"apiVersion":"v1",' + raw[1:]
    with pytest.raises(support.ClassifierObservationError, match="classifier_network_kubeconfig_invalid"):
        support.static_kubeconfig(raw, state=state, server="https://127.0.0.1:44634", deadline=time.monotonic() + 10)


@pytest.mark.parametrize("raw", [b"a: 1\na: 2\n", b"a: &alias\n", b"a: *alias\n", b"a: !!str value\n",
    b"a: |\n  value\n", b"a:\n\tb: x\n", b"a: x #comment\n", b"a:\n   b: x\n", b"a: [x]\n",
    b"a:\n- scalar\n", b"a: x\n---\na: y\n", b"a: {\"inline\": 1}\n", b"a: 'unclosed\n"])
def test_narrow_yaml_parser_refuses_ambiguous_or_executable_constructs(raw):
    with pytest.raises(support.ClassifierObservationError): support.kube_document(raw)


def test_private_read_preserves_bytes_and_ignores_access_time(tmp_path):
    path = write_private(tmp_path / "state/value", b"synthetic")
    assert support.private_bytes(path, deadline=time.monotonic() + 5) == b"synthetic"
    assert support.private_bytes(path, deadline=time.monotonic() + 5) == b"synthetic"


@pytest.mark.parametrize("fault", ["fifo", "symlink", "hardlink", "directory", "mode", "parent_mode", "ancestor_link", "oversized", "empty", "expired"])
def test_private_read_refuses_unsafe_paths_without_blocking(tmp_path, fault):
    path = write_private(tmp_path / "state/value", b"synthetic")
    if fault == "fifo": path.unlink(); os.mkfifo(path, 0o600)
    elif fault == "symlink": path.rename(path.with_name("original")); path.symlink_to(path.with_name("original"))
    elif fault == "hardlink": os.link(path, path.with_name("alias"))
    elif fault == "directory": path.unlink(); path.mkdir()
    elif fault == "mode": path.chmod(0o644)
    elif fault == "parent_mode": path.parent.chmod(0o777)
    elif fault == "ancestor_link":
        path.parent.rename(tmp_path / "original"); (tmp_path / "state").symlink_to(tmp_path / "original", target_is_directory=True)
    elif fault == "oversized": path.write_bytes(b"x" * 17)
    elif fault == "empty": path.write_bytes(b"")
    started = time.monotonic()
    with pytest.raises(support.ClassifierObservationError, match="classifier_network_private_file_invalid"):
        support.private_bytes(path, maximum=16, deadline=started + (-1 if fault == "expired" else 5))
    assert time.monotonic() - started < 1


@pytest.mark.parametrize("fault", ["replace_before_open", "fifo_before_open", "changed_during_read", "ancestor_replaced", "deadline_during_read"])
def test_private_read_detects_path_descriptor_content_and_deadline_races(tmp_path, monkeypatch, fault):
    path = write_private(tmp_path / "state/value", b"synthetic")
    original_open, original_read = os.open, os.read
    now = time.monotonic()
    clock = [now]
    monkeypatch.setattr(support.time, "monotonic", lambda: clock[0])
    def opening(name, flags, **kwargs):
        if name == path.name:
            assert flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK
            if fault in {"replace_before_open", "fifo_before_open"}:
                path.unlink()
                if fault == "fifo_before_open": os.mkfifo(path, 0o600)
                else: write_private(path, b"different")
        return original_open(name, flags, **kwargs)
    def reading(fd, maximum):
        raw = original_read(fd, maximum)
        if fault == "changed_during_read": path.write_bytes(b"different")
        elif fault == "ancestor_replaced" and path.parent.exists():
            path.parent.rename(tmp_path / "old"); path.parent.mkdir(); write_private(path, b"synthetic")
        elif fault == "deadline_during_read": clock[0] = now + 10
        return raw
    monkeypatch.setattr(support.os, "open", opening)
    monkeypatch.setattr(support.os, "read", reading)
    with pytest.raises(support.ClassifierObservationError): support.private_bytes(path, deadline=now + 5)


@pytest.mark.parametrize("fault", ["foreign_node", "foreign_image", "paused", "stopped", "public_port", "extra_mapping", "leading_zero", "privileged_port", "overflow", "missing_port"])
def test_native_api_binding_refuses_unowned_or_ambiguous_routes(monkeypatch, fault):
    observer, _ = observer_fixture(monkeypatch)
    data = ["c" * 64, "/guardrails-evolution", True, False, False, False, IMAGES["node"], {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "44634"}]}]
    if fault == "foreign_node": data[0] = "d" * 64
    elif fault == "foreign_image": data[6] = "foreign"
    elif fault == "paused": data[3] = True
    elif fault == "stopped": data[2] = False
    elif fault == "public_port": data[7]["8443/tcp"][0]["HostIp"] = "0.0.0.0"
    elif fault == "extra_mapping": data[7]["8443/tcp"].append(copy.deepcopy(data[7]["8443/tcp"][0]))
    elif fault == "missing_port": data[7] = {}
    else: data[7]["8443/tcp"][0]["HostPort"] = {"leading_zero": "044634", "privileged_port": "443", "overflow": "65536"}[fault]
    monkeypatch.setattr(observer, "_command", lambda *args, **kwargs: canonical({"identity": data[:7], "ports": data[7]}).decode())
    with pytest.raises(support.ClassifierObservationError): observer._native({"container_id": "c" * 64}, IMAGES)


def test_native_binding_uses_exact_owned_container_publication(monkeypatch):
    observer, _ = observer_fixture(monkeypatch)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return canonical({"identity": ["c" * 64, "/guardrails-evolution", True, False, False, False, IMAGES["node"]],
            "ports": {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "44634"}]}}).decode()
    monkeypatch.setattr(observer, "_command", run)
    assert observer._native({"container_id": "c" * 64}, IMAGES) == "https://127.0.0.1:44634"
    assert len(calls) == 1 and calls[0][:6] == ["docker", "--host", support.SOCKET, "container", "inspect", "c" * 64]


def command_fixture(static_fixture, monkeypatch):
    state, config, _ = static_fixture
    path = write_private(state / "kubeconfig", canonical(config))
    calls = []
    d = SimpleNamespace(env={}, state=state, bin=state / "bin", kubeconfig=path)
    observer = support.ReadinessObserver(SimpleNamespace(profile="classifier", deployment=d))
    observer.deadline = time.monotonic() + 10
    observer._api_server = "https://127.0.0.1:44634"
    observer._api_input, observer._api_pins, observer._api_private = validate_fixture(static_fixture)
    observer._api_owner, observer._api_images = {"container_id": "c" * 64}, IMAGES
    def native(owner, images):
        assert owner == observer._api_owner and images == IMAGES
        calls.append("native")
        return observer._api_server
    monkeypatch.setattr(observer, "_native", native)
    def run(args, **options):
        calls.append("kubectl")
        assert args[:3] == [state / "bin/kubectl", "--kubeconfig", "/dev/stdin"]
        assert json.loads(options["input_text"]) == config
        assert "--request-timeout=10s" in args and options["timeout"] <= 10 and options["check"] is False
        assert not any("PRIVATE KEY" in str(arg) or "client-key" in str(arg) for arg in args)
        return 0, "{}"
    d.run = run
    return observer, calls


def test_every_kubectl_call_rechecks_bound_native_route_and_consumes_only_validated_stdin(static_fixture, monkeypatch):
    observer, calls = command_fixture(static_fixture, monkeypatch)
    assert observer._kube("get", "deployments,replicasets,pods,services,endpointslices.discovery.k8s.io", "-o", "json") == "{}"
    assert calls == ["native", "kubectl"]


@pytest.mark.parametrize("fault", ["config_drift", "api_drift", "config_path", "override", "proxy", "unbound"])
def test_kubectl_refuses_drift_or_routing_override_before_api_command(static_fixture, monkeypatch, fault):
    observer, calls = command_fixture(static_fixture, monkeypatch)
    args = ("get", "deployments,replicasets,pods,services,endpointslices.discovery.k8s.io", "-o", "json")
    if fault == "config_drift": observer.deployment.kubeconfig.write_bytes(b"changed")
    elif fault == "api_drift": monkeypatch.setattr(observer, "_native", lambda *args: "https://127.0.0.1:44635")
    elif fault == "config_path": observer.deployment.kubeconfig = observer.deployment.state / "foreign"
    elif fault == "override": args += ("--server=https://foreign.invalid",)
    elif fault == "proxy": observer.deployment.env["HTTPS_PROXY"] = "http://foreign.invalid"
    else: del observer._api_input
    with pytest.raises(support.ClassifierObservationError): observer._kube(*args)
    assert "kubectl" not in calls


def test_capture_binds_current_private_config_before_first_api_call(static_fixture, monkeypatch):
    state, config, _ = static_fixture
    write_private(state / "kubeconfig", canonical(config))
    calls = []
    d = SimpleNamespace(env={}, state=state, bin=state / "bin", kubeconfig=state / "kubeconfig")
    cluster = SimpleNamespace(profile="classifier", deployment=d, endpoints={"one": {"destination": {
        "host": support.HOST, "port": 8443, "path_prefix": "/v1/classifier", "cidrs": ["10.96.0.50/32"]}}})
    observer = support.ReadinessObserver(cluster)
    observer.deadline = time.monotonic() + 10
    monkeypatch.setattr(observer, "_files", lambda: ({"container_id": "c" * 64, "owner_id": OWNER}, IMAGES, {}))
    def run(args, **options):
        if args[0] == "docker":
            calls.append("native")
            assert "input_text" not in options
            return 0, canonical({"identity": ["c" * 64, "/guardrails-evolution", True, False, False, False, IMAGES["node"]],
                "ports": {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "44634"}]}}).decode()
        calls.append("kubectl")
        assert json.loads(options["input_text"]) == config
        assert args[2] == "/dev/stdin"
        return 0, canonical(objects()).decode()
    d.run = run
    binding, statuses, images = observer._capture()
    assert calls == ["native", "native", "kubectl"]
    assert binding["api_server"] == "https://127.0.0.1:44634"
    assert binding["files"]["kubeconfig"] == hashlib.sha256(canonical(config)).hexdigest()
    assert set(statuses) == {"runtime", "classifier"} and images == IMAGES


def test_reference_key_mode_drift_is_refused_even_with_nonstandard_filename(static_fixture, monkeypatch):
    state, config, material = static_fixture
    cert_path = write_private(state / ".minikube/public.pem", material["client-certificate"])
    key_path = write_private(state / ".minikube/private.pem", material["client-key"])
    config["users"][0]["user"] = {"client-certificate": str(cert_path), "client-key": str(key_path)}
    observer, calls = command_fixture(static_fixture, monkeypatch)
    key_path.chmod(0o644)
    with pytest.raises(support.ClassifierObservationError):
        observer._kube("get", "deployments,replicasets,pods,services,endpointslices.discovery.k8s.io", "-o", "json")
    assert calls == []
