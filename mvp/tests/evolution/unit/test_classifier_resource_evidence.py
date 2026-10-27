"""Synthetic resource inventories and kernel files; no host or service reads."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from evolution import classifier_resource_evidence as subject
from guardrails.evolution.classifier_contract import canonical

from mvp.tests.evolution.e2e import classifier_resource_support as reader_module
from mvp.tests.evolution.e2e.classifier_network_support import (
    ClassifierObservationError,
)
from mvp.tests.evolution.e2e.classifier_resource_support import ResourceReader

OWNER = "a" * 32
IMAGE = "fixture/image@sha256:" + "b" * 64


def uid(index):
    return "00000000-0000-0000-0000-" + str(index).zfill(12)


def allocation(cpu, memory, request_cpu="100m", request_memory="128Mi"):
    return {"requests": {"cpu": request_cpu, "memory": request_memory}, "limits": {"cpu": cpu, "memory": memory}}


def fixture():
    definitions = [
        ("gr-platform", "postgres", "postgres", "500m", "512Mi"),
        ("gr-platform", "management", "management", "500m", "512Mi"),
        ("gr-platform", "runtime-1", "runtime", "750m", "512Mi"),
        ("gr-platform", "runtime-2", "runtime", "750m", "512Mi"),
        ("gr-platform", "classifier", "classifier", "2", "3Gi"),
        ("gr-team-a", "gateway-team-a", "gateway", "500m", "1Gi"),
        ("gr-team-b", "gateway-team-b", "gateway", "500m", "1Gi"),
        ("gr-model", "mock", "mock", "200m", "512Mi"),
        ("gr-platform", "tls-probe", "probe", "50m", "48Mi"),
        ("gr-team-a", "allowed-probe", "probe", "50m", "48Mi"),
        ("gr-team-b", "allowed-probe", "probe", "50m", "48Mi"),
        ("gr-team-a", "denied-probe", "probe", "50m", "48Mi"),
    ]
    values = {"ownerId": OWNER, "evaluatorProfile": "classifier", "initImage": IMAGE, "components": {"ollama": {"enabled": False}}}
    rows = []
    for ordinal, (namespace, name, component, cpu, memory) in enumerate(definitions, 1):
        resources = allocation(cpu, memory, "1500m" if component == "classifier" else "10m" if component == "probe" else "100m",
            "2048Mi" if component == "classifier" else "16Mi" if component == "probe" else "128Mi")
        containers = [{"name": component, "image": IMAGE, "resources": resources}]
        init = [{"name": "private-secrets" if component == "probe" else "private-files", "image": IMAGE,
            "resources": allocation("50m" if component == "probe" else "100m", "32Mi", "10m", "16Mi")}]
        if component != "probe":
            values["components"][name] = {"enabled": True, "namespace": namespace, "component": component, "image": IMAGE, "resources": resources}
        if name == "postgres":
            values["components"][name]["postgresTunnel"] = {"image": IMAGE}
            containers.append({"name": "postgres-tunnel", "image": IMAGE, "resources": allocation("50m", "64Mi", "10m", "16Mi")})
        def statuses(allocated, terminated=False, ordinal=ordinal):
            return [{"name": row["name"], "containerID": "containerd://" + f"{ordinal * 10 + n + (1000 if terminated else 0):064x}", "restartCount": 0,
                "imageID": "containerd://sha256:" + "f" * 64,
                "ready": not terminated, "state": {"terminated": {"exitCode": 0, "containerID": "containerd://" + f"{ordinal * 10 + n + 1000:064x}",
                    "startedAt": "2026-10-12T01:00:00Z", "finishedAt": "2026-10-12T01:00:01Z"}} if terminated else {"running": {}}}
                for n, row in enumerate(allocated)]
        base = ordinal * 3
        rows.extend([
            {"kind": "Deployment", "metadata": {"namespace": namespace, "name": name, "uid": uid(base), "labels": {"guardrails.evolution/owner": OWNER}}, "spec": {"replicas": 1}},
            {"kind": "ReplicaSet", "metadata": {"namespace": namespace, "name": name + "-rs", "uid": uid(base + 1),
                "ownerReferences": [{"kind": "Deployment", "name": name, "uid": uid(base), "controller": True}]}, "spec": {"replicas": 1}},
            {"kind": "Pod", "metadata": {"namespace": namespace, "name": name + "-pod", "uid": uid(base + 2),
                "ownerReferences": [{"kind": "ReplicaSet", "name": name + "-rs", "uid": uid(base + 1), "controller": True}]},
             "spec": {"nodeName": "guardrails-evolution", "containers": containers, "initContainers": init},
             "status": {"phase": "Running", "containerStatuses": statuses(containers), "initContainerStatuses": statuses(init, True)}}])
    rows.append({"kind": "Pod", "metadata": {"namespace": "kube-system", "name": "system-pod", "uid": uid(999)},
        "spec": {"nodeName": "guardrails-evolution", "containers": [{"name": "system"}]}, "status": {"phase": "Running"}})
    return {"items": rows}, values


def select(value, kind, name):
    return next(row for row in value["items"] if row["kind"] == kind and row["metadata"]["name"] == name)


def test_complete_observed_inventory_counts_all_apps_sidecar_initializers_and_separate_system_overhead():
    actual, values = fixture()
    result = subject.inventory(actual, owner=OWNER, values=values)
    assert result["totals"] == {"requests": {"cpu_milli": 2250, "memory_bytes": 3024 * 1024**2},
        "limits": {"cpu_milli": 5950, "memory_bytes": 7936 * 1024**2}}
    assert result["owned_pods"] == result["completed_initializers"] == 12
    assert result["running_containers"] == 13 and result["system_pods"] == 1
    assert len(result["system_inventory_sha256"]) == len(result["inventory_sha256"]) == 64
    assert len(next(row for row in result["applications"] if row["deployment"] == "postgres")["containers"]) == 2


@pytest.mark.parametrize("fault", ["terminating", "extra_pod", "missing_pod", "duplicate", "foreign_namespace", "foreign_owner", "wrong_replica", "wrong_deployment",
    "unready", "wrong_node", "host_network", "extra_sidecar", "ephemeral", "live_init", "failed_init", "missing_init", "init_drift", "sidecar_drift", "requests_drift",
    "limits_drift", "image_drift", "extra_desired", "ollama_enabled", "bool_replicas", "bad_cid", "negative_restarts", "large_inventory"])
def test_resource_inventory_refuses_hidden_or_unaccounted_consumers(fault):
    actual, values = fixture()
    pod = select(actual, "Pod", "classifier-pod")
    if fault == "terminating": pod["metadata"]["deletionTimestamp"] = "2026-10-12T00:00:00Z"
    elif fault == "extra_pod":
        row = copy.deepcopy(pod); row["metadata"]["name"] = "old-classifier-pod"; row["metadata"]["deletionTimestamp"] = "now"; actual["items"].append(row)
    elif fault == "missing_pod": actual["items"].remove(pod)
    elif fault == "duplicate": actual["items"].append(copy.deepcopy(pod))
    elif fault == "foreign_namespace": pod["metadata"]["namespace"] = "foreign"
    elif fault == "foreign_owner": select(actual, "Deployment", "classifier")["metadata"]["labels"]["guardrails.evolution/owner"] = "b" * 32
    elif fault == "wrong_replica": pod["metadata"]["ownerReferences"][0]["uid"] = uid(999)
    elif fault == "wrong_deployment": select(actual, "ReplicaSet", "classifier-rs")["metadata"]["ownerReferences"][0]["uid"] = uid(999)
    elif fault == "unready": pod["status"]["containerStatuses"][0]["ready"] = False
    elif fault == "wrong_node": pod["spec"]["nodeName"] = "foreign"
    elif fault == "host_network": pod["spec"]["hostNetwork"] = True
    elif fault == "extra_sidecar": pod["spec"]["containers"].append(copy.deepcopy(pod["spec"]["containers"][0]))
    elif fault == "ephemeral": pod["spec"]["ephemeralContainers"] = [{"name": "debug"}]
    elif fault == "live_init": pod["status"]["initContainerStatuses"][0]["state"] = {"running": {}}
    elif fault == "failed_init": pod["status"]["initContainerStatuses"][0]["state"]["terminated"]["exitCode"] = 1
    elif fault == "missing_init": pod["status"]["initContainerStatuses"] = []
    elif fault == "init_drift": pod["spec"]["initContainers"][0]["resources"]["limits"]["cpu"] = "200m"
    elif fault == "sidecar_drift": select(actual, "Pod", "postgres-pod")["spec"]["containers"][1]["resources"]["limits"]["memory"] = "128Mi"
    elif fault == "requests_drift": pod["spec"]["containers"][0]["resources"]["requests"]["memory"] = "1024Mi"
    elif fault == "limits_drift": pod["spec"]["containers"][0]["resources"]["limits"]["cpu"] = "3"
    elif fault == "image_drift": pod["spec"]["containers"][0]["image"] = "unpinned:latest"
    elif fault == "extra_desired": values["components"]["extra"] = {"enabled": True}
    elif fault == "ollama_enabled": values["components"]["ollama"]["enabled"] = True
    elif fault == "bool_replicas": select(actual, "Deployment", "classifier")["spec"]["replicas"] = True
    elif fault == "bad_cid": pod["status"]["containerStatuses"][0]["containerID"] = "foreign"
    elif fault == "negative_restarts": pod["status"]["containerStatuses"][0]["restartCount"] = -1
    else: actual["items"] *= 20
    with pytest.raises(subject.ResourceObservationError): subject.inventory(actual, owner=OWNER, values=values)


@pytest.mark.parametrize("value,kind", [(True, "cpu"), (1, "cpu"), ("01", "cpu"), ("-1", "cpu"), ("1e3", "cpu"), ("0.0001", "cpu"),
    ("6001m", "cpu"), ("1Ki", "cpu"), ("1m", "memory"), ("9Gi", "memory"), ("0", "memory"), ("0.1", "memory"), ("1" * 33, "memory")])
def test_quantity_rejects_coercion_fractional_units_and_overflow(value, kind):
    with pytest.raises(subject.ResourceObservationError): subject.quantity(value, kind)


def kernel():
    cid, pod = "c" * 64, uid(5)
    end = "/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod" + pod.replace("-", "_") + ".slice/cri-containerd-" + cid + ".scope"
    paths = ["/"]
    for part in end.strip("/").split("/"): paths.append(paths[-1].rstrip("/") + "/" + part)
    rows = []
    for i, path in enumerate(paths):
        rows.append({"path": path, "device": 42, "inode": i + 1, "cpu.max": "600000 100000\n" if i == 0 else "200000 100000\n" if path == end else "max 100000\n",
            "memory.max": str(8 * 1024**3) + "\n" if i == 0 else str(3072 * 1024**2) + "\n" if path == end else "max\n",
            "memory.current": "1048576\n", "memory.peak": "2097152\n",
            "cpu.stat": "usage_usec 1000\nuser_usec 800\nsystem_usec 200\nnr_periods 10\nnr_throttled 2\nthrottled_usec 10\n"})
    return {"container_id": cid, "pid": 12345, "start_ticks": 67890, "process_cgroup": "0::" + end + "\n", "ancestors": rows}, cid, pod


def test_kernel_ancestry_enforces_actual_node_and_classifier_caps_and_preserves_measured_counters():
    raw, cid, pod = kernel()
    before = subject.kernel_sample(raw, cid=cid, pod_uid=pod)
    assert before["ancestors"][0]["memory_limit_bytes"] == 8 * 1024**3
    assert before["ancestors"][-1]["memory_limit_bytes"] == 3072 * 1024**2
    after = copy.deepcopy(before)
    after["ancestors"][-1]["cpu"]["usage_usec"] += 750
    observed = subject.compare_samples(before, after)
    assert observed["classifier_cpu_usage_delta_usec"] == 750
    assert observed["classifier_memory_peak_bytes"] == 2097152


@pytest.mark.parametrize("fault", ["foreign_container", "foreign_pod", "path_escape", "missing_ancestor", "reordered", "node_unlimited_cpu", "node_wrong_memory",
    "child_unlimited_cpu", "child_unlimited_memory", "tight_parent_cpu", "tight_parent_memory", "over_limit", "current_above_peak", "negative_usage",
    "duplicate_stat", "missing_stat", "unknown_stat", "bool_pid", "zero_start"])
def test_actual_kernel_sample_rejects_missing_limits_ancestry_or_identity(fault):
    raw, cid, pod = kernel()
    if fault == "foreign_container": raw["container_id"] = "d" * 64
    elif fault == "foreign_pod": pod = uid(6)
    elif fault == "path_escape": raw["process_cgroup"] = "0::/../foreign\n"
    elif fault == "missing_ancestor": raw["ancestors"].pop(1)
    elif fault == "reordered": raw["ancestors"].reverse()
    elif fault == "node_unlimited_cpu": raw["ancestors"][0]["cpu.max"] = "max 100000\n"
    elif fault == "node_wrong_memory": raw["ancestors"][0]["memory.max"] = "max\n"
    elif fault == "child_unlimited_cpu": raw["ancestors"][-1]["cpu.max"] = "max 100000\n"
    elif fault == "child_unlimited_memory": raw["ancestors"][-1]["memory.max"] = "max\n"
    elif fault == "tight_parent_cpu": raw["ancestors"][2]["cpu.max"] = "100000 100000\n"
    elif fault == "tight_parent_memory": raw["ancestors"][2]["memory.max"] = str(2048 * 1024**2) + "\n"
    elif fault == "over_limit": raw["ancestors"][-1]["memory.peak"] = str(4096 * 1024**2) + "\n"
    elif fault == "current_above_peak": raw["ancestors"][-1]["memory.current"] = "4194304\n"
    elif fault == "negative_usage": raw["ancestors"][-1]["cpu.stat"] = raw["ancestors"][-1]["cpu.stat"].replace("usage_usec 1000", "usage_usec -1")
    elif fault == "duplicate_stat": raw["ancestors"][-1]["cpu.stat"] += "usage_usec 1000\n"
    elif fault == "missing_stat": raw["ancestors"][-1]["cpu.stat"] = "usage_usec 1000\n"
    elif fault == "unknown_stat": raw["ancestors"][-1]["cpu.stat"] += "unknown 1\n"
    elif fault == "bool_pid": raw["pid"] = True
    else: raw["start_ticks"] = 0
    with pytest.raises(subject.ResourceObservationError): subject.kernel_sample(raw, cid=cid, pod_uid=pod)


@pytest.mark.parametrize("fault", ("pid", "start", "inode", "limit", "counter_regression", "peak_regression", "no_cpu_activity"))
def test_cross_sample_comparison_refuses_identity_limits_counter_or_peak_drift(fault):
    raw, cid, pod = kernel()
    before = subject.kernel_sample(raw, cid=cid, pod_uid=pod)
    after = copy.deepcopy(before)
    after["ancestors"][-1]["cpu"]["usage_usec"] += 1
    if fault == "pid": after["pid"] += 1
    elif fault == "start": after["start_ticks"] += 1
    elif fault == "inode": after["ancestors"][-1]["inode"] += 1
    elif fault == "limit": after["ancestors"][-1]["memory_limit_bytes"] += 1
    elif fault == "counter_regression": after["ancestors"][-1]["cpu"]["throttled_usec"] = 0
    elif fault == "peak_regression": after["ancestors"][-1]["memory_peak_bytes"] -= 1
    else: after["ancestors"][-1]["cpu"]["usage_usec"] -= 1
    with pytest.raises(subject.ResourceObservationError): subject.compare_samples(before, after)


def reader_fixture(fault=None):
    value, cid, pod = kernel()
    calls, counters = [], {}
    def command(argv, **kwargs):
        assert argv[:5] == ["docker", "--host", "unix:///var/run/docker.sock", "exec", "d" * 64]
        args = argv[5:]
        calls.append(args)
        counters[args[0]] = counters.get(args[0], 0) + 1
        if args[:3] == ["crictl", "inspect", "--output=json"]:
            assert args[3] == cid
            return canonical({"status": {"id": cid, "state": "CONTAINER_RUNNING", "metadata": {"name": "classifier"},
                "labels": {"io.kubernetes.pod.uid": pod if fault != "cri_pod" else uid(99)}}, "info": {"pid": 12345}}).decode()
        if args[0] == "head" and args[-1] == "/proc/12345/stat":
            fields = ["S", *("0" for _ in range(49))]
            fields[19] = "67891" if fault == "start_drift" and counters["crictl"] == 2 else "67890"
            return "12345 (python with spaces) " + " ".join(fields) + "\n"
        if args[0] == "head" and args[-1] == "/proc/12345/cgroup":
            return "0::/../escape\n" if fault == "cgroup_escape" else value["process_cgroup"]
        if args[0] == "stat":
            assert args[:3] == ["stat", "--format=%d:%i", "--"]
            suffix = args[-1].removeprefix("/sys/fs/cgroup") or "/"
            row = next(r for r in value["ancestors"] if r["path"] == suffix)
            inode = row["inode"] + (1 if fault == "inode_drift" and counters["stat"] % 2 == 0 else 0)
            return str(row["device"]) + ":" + str(inode) + "\n"
        assert args[:3] == ["head", "-c", "4096"]
        directory, name = args[-1].rsplit("/", 1)
        suffix = directory.removeprefix("/sys/fs/cgroup") or "/"
        row = next(r for r in value["ancestors"] if r["path"] == suffix)
        return row[name]
    observer = SimpleNamespace(bound={"node_id": "d" * 64}, _command=command)
    return ResourceReader(observer), cid, pod, calls


def test_owned_node_reader_binds_cri_pid_start_time_and_cgroup_ancestry_twice():
    reader, cid, pod, calls = reader_fixture()
    value = reader.kernel(cid=cid, pod_uid=pod)
    assert value["pid"] == 12345 and value["start_ticks"] == 67890
    assert value["ancestors"][-1]["memory_limit_bytes"] == 3072 * 1024**2
    assert sum(args[0] == "crictl" for args in calls) == 2
    assert sum(args[0] == "stat" for args in calls) == 10
    assert len(calls) == 41
    assert all(args[0] in {"crictl", "head", "stat"} for args in calls)


@pytest.mark.parametrize("fault", ("cri_pod", "start_drift", "cgroup_escape", "inode_drift"))
def test_original_owned_node_reader_refuses_cri_process_path_or_inode_drift(fault):
    reader, cid, pod, _ = reader_fixture(fault)
    with pytest.raises((ValueError, RuntimeError)): reader.kernel(cid=cid, pod_uid=pod)


@pytest.mark.parametrize("fault", ("extra_deployment", "extra_active_replica", "unowned_replica", "duplicate_uid", "duplicate_cid", "replica_zero"))
def test_inventory_closes_unobserved_deployment_and_replica_resources(fault):
    actual, values = fixture()
    if fault == "duplicate_uid": actual["items"][-1]["metadata"]["uid"] = actual["items"][0]["metadata"]["uid"]
    elif fault == "duplicate_cid":
        pod = select(actual, "Pod", "classifier-pod")
        pod["status"]["initContainerStatuses"][0]["containerID"] = pod["status"]["containerStatuses"][0]["containerID"]
    elif fault == "replica_zero": select(actual, "ReplicaSet", "classifier-rs")["spec"]["replicas"] = 0
    else:
        row = copy.deepcopy(select(actual, "Deployment" if fault == "extra_deployment" else "ReplicaSet", "classifier" if fault == "extra_deployment" else "classifier-rs"))
        row["metadata"].update(name="extra", uid=uid(9999))
        if fault == "unowned_replica": row["metadata"]["ownerReferences"][0]["uid"] = uid(5555)
        actual["items"].append(row)
    with pytest.raises(subject.ResourceObservationError, match="^classifier_resource_observation_invalid$"):
        subject.inventory(actual, owner=OWNER, values=values)


def test_scaled_zero_historical_replica_is_retained_without_an_unobserved_running_consumer():
    actual, values = fixture()
    row = copy.deepcopy(select(actual, "ReplicaSet", "classifier-rs"))
    row["metadata"].update(name="historical", uid=uid(9999))
    row["spec"]["replicas"] = 0
    actual["items"].append(row)
    assert subject.inventory(actual, owner=OWNER, values=values)["owned_pods"] == 12


@pytest.mark.parametrize("part", ("metadata", "spec", "status", "ownerReferences", "containers", "containerStatuses", "initContainers", "initContainerStatuses"))
@pytest.mark.parametrize("bad", (None, True, [], "private-input"))
def test_malformed_inventory_shape_has_only_finite_public_refusal(part, bad):
    actual, values = fixture()
    pod = select(actual, "Pod", "classifier-pod")
    target = pod["metadata"] if part == "ownerReferences" else pod["spec"] if part in {"containers", "initContainers"} else pod["status"] if part in {"containerStatuses", "initContainerStatuses"} else pod
    target[part] = bad
    with pytest.raises(subject.ResourceObservationError, match="^classifier_resource_observation_invalid$"):
        subject.inventory(actual, owner=OWNER, values=values)


@pytest.mark.parametrize("fault", (None, "pin", "native", "kube_path"))
def test_unfiltered_inventory_command_uses_only_bound_static_api_and_rechecks_every_pin(monkeypatch, fault):
    commands, reads = [], []
    state = Path("/synthetic/private-state")
    def read(path, **kwargs):
        reads.append((path, kwargs))
        return b"changed" if fault == "pin" else b"static-fixture"
    monkeypatch.setattr(reader_module, "private_bytes", read)
    def command(args, **kwargs):
        commands.append((args, kwargs))
        return '{"items":[]}'
    observer = SimpleNamespace(deployment=SimpleNamespace(state=state, kubeconfig=state / ("wrong" if fault == "kube_path" else "kubeconfig"), bin=state / "bin"),
        deadline=1234, _api_pins={"kubeconfig": hashlib.sha256(b"static-fixture").hexdigest()}, _api_private={"kubeconfig"},
        _api_owner={"owner": "fixture"}, _api_images={"image": "fixture"}, _api_server="https://127.0.0.1:12345",
        _native=lambda *_: "https://127.0.0.1:" + ("54321" if fault == "native" else "12345"),
        _api_input='{"static":"private-fixture"}', _command=command)
    if fault:
        with pytest.raises(ClassifierObservationError): ResourceReader(observer)._objects()
        assert commands == []
    else:
        assert ResourceReader(observer)._objects() == '{"items":[]}'
        assert reads == [(state / "kubeconfig", {"deadline": 1234, "modes": (0o600,)})]
        assert commands == [([state / "bin/kubectl", "--kubeconfig", "/dev/stdin", "--context", "guardrails-evolution", "--request-timeout=10s",
            "get", "deployments,replicasets,pods", "--all-namespaces", "-o", "json"], {"maximum": 2 * 1024 * 1024, "input_text": '{"static":"private-fixture"}'})]


@pytest.mark.parametrize("fault", (None, "initial_binding", "values_pin", "pod_uid", "cid", "final_binding", "other_pod_drift", "image_binding"))
def test_capture_closes_values_inventory_current_pod_and_initial_final_binding(monkeypatch, fault):
    actual, values = fixture()
    raw = canonical(values)
    monkeypatch.setattr(reader_module, "private_bytes", lambda *_args, **_kwargs: raw)
    pod = select(actual, "Pod", "classifier-pod")
    bound = {"owner_id": OWNER, "files": {"assembly/values.json": hashlib.sha256(raw).hexdigest()},
        "pods": {"classifier": {"pod_uid": pod["metadata"]["uid"], "container_id": pod["status"]["containerStatuses"][0]["containerID"]}}}
    if fault == "values_pin": bound["files"]["assembly/values.json"] = "0" * 64
    if fault == "pod_uid": bound["pods"]["classifier"]["pod_uid"] = uid(98765)
    if fault == "cid": bound["pods"]["classifier"]["container_id"] = "containerd://" + "0" * 64
    observed = []
    def capture():
        observed.append("capture")
        value = copy.deepcopy(bound)
        if (fault == "initial_binding" and len(observed) == 1) or (fault == "final_binding" and len(observed) == 2):
            value["owner_id"] = "b" * 32
        return value, None, None
    observer = SimpleNamespace(bound=bound, deployment=SimpleNamespace(state=Path("/synthetic/state")), _remaining=lambda: 10, _capture=capture)
    reader = ResourceReader(observer)
    inventory = subject.inventory(actual, owner=OWNER, values=values)
    identities = identities_fixture(inventory)
    image_binding = {"binding_sha256": hashlib.sha256(canonical(bound)).hexdigest(), "inventory_sha256": inventory["inventory_sha256"],
        "identities": identities, "identities_sha256": subject.image_closure(inventory, identities)}
    if fault == "image_binding": image_binding["inventory_sha256"] = "a" * 64
    monkeypatch.setattr(reader, "_objects", lambda: canonical(actual).decode())
    kernel_calls = []
    def sample(**kwargs):
        kernel_calls.append(kwargs)
        if fault == "other_pod_drift": select(actual, "Pod", "management-pod")["status"]["containerStatuses"][0]["restartCount"] += 1
        return {"synthetic": True}
    monkeypatch.setattr(reader, "kernel", sample)
    if fault:
        with pytest.raises(ClassifierObservationError): reader.capture_locked(deadline=12345, image_binding=image_binding)
        assert len(kernel_calls) == (1 if fault in {"final_binding", "other_pod_drift"} else 0)
    else:
        result = reader.capture_locked(deadline=12345, image_binding=image_binding)
        assert observer.deadline == 12345 and observed == ["capture", "capture"]
        assert result["inventory"]["running_containers"] == 13 and result["kernel"] == {"synthetic": True}
        assert result["binding_sha256"] == hashlib.sha256(canonical(bound)).hexdigest()
        assert kernel_calls == [{"cid": pod["status"]["containerStatuses"][0]["containerID"].removeprefix("containerd://"), "pod_uid": pod["metadata"]["uid"]}]


def identities_fixture(inventory):
    native = [{"container_id": row["container_id"], "expected_reference": row["image"], "manifest_digest": "sha256:" + "e" * 64,
        "config_digest": "sha256:" + "f" * 64, "rootfs_chain_id": "sha256:" + "d" * 64,
        "ready": True} for pod in inventory["applications"] for row in pod["containers"]]
    completed = [{"container_id": row["container_id"], "expected_reference": row["image"], "manifest_digest": "sha256:" + "e" * 64,
        "config_digest": "sha256:" + "f" * 64, "rootfs_chain_id": "sha256:" + "d" * 64,
        "completed": True, "exit_code": 0, "image_verified_by_container_id": next(image["container_id"] for image in native if image["expected_reference"] == row["image"]),
        "declaration_sha256": row["completed_status_sha256"]} for pod in inventory["applications"] for row in pod["completed_initializers"]]
    return native + completed


@pytest.mark.parametrize("fault", (None, "missing", "duplicate", "foreign", "image", "observed", "repository", "config", "init_ready", "failed_init", "declaration", "unverified_init", "extra", "malformed"))
def test_native_image_closure_binds_every_running_and_completed_container_to_observed_image_id(fault):
    actual, values = fixture()
    observed = subject.inventory(actual, owner=OWNER, values=values)
    identities = identities_fixture(observed)
    if fault == "missing": identities.pop()
    elif fault == "duplicate": identities[-1] = copy.deepcopy(identities[0])
    elif fault == "foreign": identities[-1]["container_id"] = "c" * 64
    elif fault == "image": identities[0]["expected_reference"] = "wrong/image@sha256:" + "b" * 64
    elif fault == "observed": observed["applications"][0]["containers"][0]["observed_image_id"] = "containerd://sha256:" + "a" * 64
    elif fault == "repository": observed["applications"][0]["containers"][0]["observed_image_id"] = "foreign/repository@sha256:" + "f" * 64
    elif fault == "config": identities[0]["config_digest"] = "sha256:" + "a" * 64
    elif fault == "init_ready": identities[-1] = {key: value for key, value in identities[-1].items() if key not in {"completed", "exit_code", "image_verified_by_container_id", "declaration_sha256"}} | {"ready": True}
    elif fault == "failed_init": identities[-1]["exit_code"] = 1
    elif fault == "declaration": identities[-1]["declaration_sha256"] = "a" * 64
    elif fault == "unverified_init": identities[-1]["image_verified_by_container_id"] = "a" * 64
    elif fault == "extra": identities[0]["private"] = "must-not-cross-boundary"
    elif fault == "malformed": identities[0] = None
    if fault:
        with pytest.raises(subject.ResourceObservationError, match="^classifier_resource_image_closure_invalid$"): subject.image_closure(observed, identities)
    else:
        digest = subject.image_closure(observed, identities)
        assert len(digest) == 64 and digest == subject.image_closure(observed, list(reversed(identities)))


@pytest.mark.parametrize("fault", (None, "native_failure", "image_drift", "binding_drift"))
def test_native_image_preflight_verifies_all_thirteen_running_and_twelve_completed_ids(monkeypatch, fault):
    actual, values = fixture()
    observed = subject.inventory(actual, owner=OWNER, values=values)
    identities = {row["container_id"]: row for row in identities_fixture(observed)}
    calls, captures, inventories, declarations = [], [], [], []
    def verify(deployment, expected, status):
        cid = status["containerID"].removeprefix("containerd://")
        calls.append(cid)
        assert expected == IMAGE and status["imageID"] == "containerd://sha256:" + "f" * 64
        assert deployment.run(["docker", "exec", "guardrails-evolution", "crictl", "inspect", cid], timeout=20, check=False) == (0, "fake-native-stdout")
        if fault == "native_failure": raise RuntimeError("native_image_identity_refused")
        return identities[cid]
    verifier = SimpleNamespace(verify_container=verify, _completed_declaration=lambda status: declarations.append(status["containerID"]))
    def capture():
        captures.append(1)
        return ({"stable": len(captures)} if fault == "binding_drift" else {"stable": 1}), None, None
    observer = SimpleNamespace(bound={"stable": 1}, _capture=capture, _remaining=lambda: 10,
        deployment=SimpleNamespace(support=lambda name: verifier if name == "image_identity" else pytest.fail("unapproved module")))
    reader = ResourceReader(observer)
    def inventory(_before):
        inventories.append(1)
        result = copy.deepcopy(observed)
        if fault == "image_drift" and len(inventories) > 1: result["inventory_sha256"] = "a" * 64
        return result, actual
    monkeypatch.setattr(reader, "_inventory", inventory)
    monkeypatch.setattr(reader, "_node", lambda *_args, **_kwargs: "fake-native-stdout")
    if fault:
        with pytest.raises(RuntimeError): reader.verify_images_locked(deadline=12345)
    else:
        result = reader.verify_images_locked(deadline=12345)
        assert len(calls) == len(set(calls)) == 13 and len(declarations) == len(set(declarations)) == 12
        assert result["inventory_sha256"] == observed["inventory_sha256"]
        assert result["identities_sha256"] == subject.image_closure(observed, result["identities"])


@pytest.mark.parametrize("name", ("overhead", "resources"))
@pytest.mark.parametrize("value", ({"cpu": "1m", "memory": "1Mi"}, {"limits": {"cpu": "3", "memory": "4Gi"}}, None, True, []))
def test_pod_overhead_and_pod_level_resources_cannot_escape_exact_profile_accounting(name, value):
    actual, values = fixture()
    select(actual, "Pod", "classifier-pod")["spec"][name] = value
    with pytest.raises(subject.ResourceObservationError): subject.inventory(actual, owner=OWNER, values=values)
