"""Pure validation of unfiltered workload inventory and enforced cgroup limits.

Caller-provided observations are not manufactured here. A successful parser is
not evidence that any Kubernetes, CRI or kernel observation was performed.
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from pathlib import PurePosixPath

from guardrails.evolution.classifier_contract import canonical

MIB = 1024**2
APPLICATIONS = {
    ("gr-platform", "postgres"): ("postgres", 500, 512),
    ("gr-platform", "management"): ("management", 500, 512),
    ("gr-platform", "runtime-1"): ("runtime", 750, 512),
    ("gr-platform", "runtime-2"): ("runtime", 750, 512),
    ("gr-platform", "classifier"): ("classifier", 2000, 3072),
    ("gr-team-a", "gateway-team-a"): ("gateway", 500, 1024),
    ("gr-team-b", "gateway-team-b"): ("gateway", 500, 1024),
    ("gr-model", "mock"): ("mock", 200, 512),
}
PROBES = {("gr-platform", "tls-probe"), ("gr-team-a", "allowed-probe"),
          ("gr-team-b", "allowed-probe"), ("gr-team-a", "denied-probe")}
NAMESPACES = {name[0] for name in APPLICATIONS}
TOTALS = {"requests": {"cpu_milli": 2250, "memory_bytes": 3024 * MIB},
          "limits": {"cpu_milli": 5950, "memory_bytes": 7936 * MIB}}


class ResourceObservationError(ValueError):
    """Finite refusal only; raw infrastructure metadata is private."""


def require(condition):
    if not condition:
        raise ResourceObservationError("classifier_resource_observation_invalid")


def integer(value, minimum=0, maximum=2**63 - 1):
    require(type(value) is int and minimum <= value <= maximum)
    return value


def quantity(value, kind):
    require(type(value) is str and 0 < len(value) <= 32 and kind in {"cpu", "memory"})
    match = re.fullmatch(r"(0|[1-9][0-9]*)(?:\.([0-9]{1,9}))?(m|Ki|Mi|Gi|k|K|M|G)?", value)
    require(match is not None)
    suffix = match[3] or ""
    factors = {"": 1000, "m": 1} if kind == "cpu" else {"": 1, "Ki": 1024, "Mi": MIB, "Gi": 1024**3, "k": 1000, "K": 1000, "M": 1000**2, "G": 1000**3}
    require(suffix in factors)
    number = Decimal(match[1] + ("." + match[2] if match[2] else "")) * factors[suffix]
    require(number == int(number) and 0 < number <= (6000 if kind == "cpu" else 8 * 1024**3))
    return int(number)


def resources(value):
    require(type(value) is dict and set(value) == {"requests", "limits"})
    result = {}
    for name in ("requests", "limits"):
        row = value[name]
        require(type(row) is dict and set(row) == {"cpu", "memory"})
        result[name] = {"cpu_milli": quantity(row["cpu"], "cpu"), "memory_bytes": quantity(row["memory"], "memory")}
    require(all(result["requests"][key] <= result["limits"][key] for key in result["requests"]))
    return result


def _expected(cpu, memory, request_cpu=100, request_memory=128):
    return {"requests": {"cpu_milli": request_cpu, "memory_bytes": request_memory * MIB},
            "limits": {"cpu_milli": cpu, "memory_bytes": memory * MIB}}


def uid(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is not None)
    return value


def container_id(value):
    require(type(value) is str and re.fullmatch(r"containerd://[0-9a-f]{64}", value) is not None)
    return value.removeprefix("containerd://")


def image_id(value):
    require(type(value) is str and len(value) <= 2048 and re.fullmatch(
        r"(?:(?:containerd|docker-pullable)://)?(?:[A-Za-z0-9][A-Za-z0-9._:/-]*@)?sha256:[0-9a-f]{64}", value) is not None)
    return value


def inventory(value, *, owner, values):
    try:
        return _inventory(value, owner=owner, values=values)
    except (AttributeError, KeyError, StopIteration, TypeError, ValueError):
        raise ResourceObservationError("classifier_resource_observation_invalid") from None


def _inventory(value, *, owner, values):
    """Every owned namespace Pod, including terminating/foreign/extra Pods.

    Transitions fail closed instead of disappearing from accounting. Init
    containers must be actually completed; every regular sidecar is included.
    System Pods are retained by count and digest, never treated as zero overhead.
    """
    require(type(owner) is str and re.fullmatch(r"[0-9a-f]{32}", owner) is not None)
    require(type(values) is dict and values.get("ownerId") == owner and values.get("evaluatorProfile") == "classifier"
            and type(values.get("components")) is dict and type(value) is dict and type(value.get("items")) is list
            and len(value["items"]) <= 512)
    require(set(values["components"]) == {name[1] for name in APPLICATIONS} | {"ollama"}
            and values["components"]["ollama"].get("enabled") is False)
    rows, seen, identifiers = value["items"], set(), set()
    objects = {}
    for row in rows:
        require(type(row) is dict and row.get("kind") in {"Deployment", "ReplicaSet", "Pod"} and type(row.get("metadata")) is dict)
        meta = row["metadata"]
        require(type(meta.get("name")) is str and 0 < len(meta["name"]) <= 253 and type(meta.get("namespace")) is str)
        key = row["kind"], meta["namespace"], meta["name"]
        identifier = uid(meta.get("uid"))
        require(key not in seen and identifier not in identifiers and meta["namespace"] in NAMESPACES | {"kube-system"})
        seen.add(key)
        identifiers.add(identifier)
        objects[key] = row
    deployments = {(row["metadata"]["namespace"], row["metadata"]["name"]): row for row in rows
        if row["kind"] == "Deployment" and row["metadata"]["namespace"] != "kube-system"}
    require(set(deployments) == set(APPLICATIONS) | PROBES)
    for replica in (row for row in rows if row["kind"] == "ReplicaSet" and row["metadata"]["namespace"] != "kube-system"):
        meta = replica["metadata"]
        refs = [r for r in meta.get("ownerReferences", []) if r.get("controller") is True]
        require(len(refs) == 1 and refs[0].get("kind") == "Deployment")
        target = deployments.get((meta["namespace"], refs[0].get("name")))
        require(target is not None and target["metadata"]["uid"] == uid(refs[0].get("uid"))
                and not meta.get("deletionTimestamp") and type(replica.get("spec", {}).get("replicas")) is int
                and replica["spec"]["replicas"] in (0, 1))
    pods = [row for row in rows if row["kind"] == "Pod"]
    require(len(pods) <= 128)
    systems, applications, targets = [], [], set()
    totals = {name: {"cpu_milli": 0, "memory_bytes": 0} for name in ("requests", "limits")}
    active, completed, container_ids, active_replicas = 0, 0, set(), set()
    for pod in pods:
        meta, spec, status = pod["metadata"], pod.get("spec", {}), pod.get("status", {})
        require(spec.get("nodeName") == "guardrails-evolution")
        if meta["namespace"] == "kube-system":
            systems.append({"metadata": meta, "spec": spec, "status": status})
            continue
        require(meta["namespace"] in NAMESPACES and not meta.get("deletionTimestamp") and status.get("phase") == "Running"
                and not spec.get("ephemeralContainers") and not spec.get("hostNetwork")
                and ("overhead" not in spec or spec["overhead"] == {}) and ("resources" not in spec or spec["resources"] == {}))
        refs = [r for r in meta.get("ownerReferences", []) if r.get("controller") is True]
        require(len(refs) == 1 and refs[0].get("kind") == "ReplicaSet")
        ref = refs[0]
        replica = objects.get(("ReplicaSet", meta["namespace"], ref.get("name")))
        require(replica is not None and replica["metadata"].get("uid") == uid(ref.get("uid"))
                and not replica["metadata"].get("deletionTimestamp") and replica["spec"]["replicas"] == 1)
        active_replicas.add(replica["metadata"]["uid"])
        refs = [r for r in replica["metadata"].get("ownerReferences", []) if r.get("controller") is True]
        require(len(refs) == 1 and refs[0].get("kind") == "Deployment")
        ref = refs[0]
        target = objects.get(("Deployment", meta["namespace"], ref.get("name")))
        require(target is not None and target["metadata"].get("uid") == uid(ref.get("uid"))
                and target["metadata"].get("labels", {}).get("guardrails.evolution/owner") == owner
                and not target["metadata"].get("deletionTimestamp")
                and type(target.get("spec", {}).get("replicas")) is int and target["spec"]["replicas"] == 1)
        name = meta["namespace"], target["metadata"]["name"]
        require(name in set(APPLICATIONS) | PROBES and name not in targets)
        targets.add(name)
        if name in PROBES:
            expected = {"probe": (values["initImage"], _expected(50, 48, 10, 16))}
            initial = {"private-secrets": (values["initImage"], _expected(50, 32, 10, 16))}
        else:
            component, cpu, memory = APPLICATIONS[name]
            wanted = values["components"].get(name[1])
            require(type(wanted) is dict and wanted.get("enabled") is True and wanted.get("namespace") == name[0]
                    and wanted.get("component") == component and not wanted.get("sidecar"))
            expected = {component: (wanted["image"], _expected(cpu, memory, 1500 if component == "classifier" else 100,
                2048 if component == "classifier" else 128))}
            initial = {"private-files": (values["initImage"], _expected(100, 32, 10, 16))}
            require(resources(wanted.get("resources")) == expected[component][1])
            if name[1] == "postgres":
                require(type(wanted.get("postgresTunnel")) is dict)
                expected["postgres-tunnel"] = wanted["postgresTunnel"]["image"], _expected(50, 64, 10, 16)
            else:
                require(not wanted.get("postgresTunnel"))
        def check(containers, statuses, declared, *, initialized):
            require(type(containers) is list and type(statuses) is list and len(containers) == len(statuses) == len(declared))
            require({c.get("name") for c in containers} == {c.get("name") for c in statuses} == set(declared))
            result = []
            for container in containers:
                c_name = container["name"]
                image, expected_resources = declared[c_name]
                require(container.get("image") == image and type(image) is str
                        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", image) is not None
                        and resources(container.get("resources")) == expected_resources and not container.get("restartPolicy"))
                actual = next(c for c in statuses if c["name"] == c_name)
                cid = container_id(actual.get("containerID"))
                require(cid not in container_ids)
                container_ids.add(cid)
                if initialized:
                    require(set(actual.get("state", {})) == {"terminated"} and type(actual["state"]["terminated"].get("exitCode")) is int
                            and actual["state"]["terminated"]["exitCode"] == 0)
                else:
                    require(actual.get("ready") is True and set(actual.get("state", {})) == {"running"})
                result.append({"name": c_name, "container_id": cid, "restarts": integer(actual.get("restartCount")),
                    "image": image, "observed_image_id": image_id(actual.get("imageID")), "resources": expected_resources,
                    **({"completed_status_sha256": hashlib.sha256(canonical({key: actual[key] for key in
                        ("name", "containerID", "imageID", "state")})).hexdigest()} if initialized else {})})
            return sorted(result, key=lambda c: c["name"])
        current = check(spec.get("containers"), status.get("containerStatuses"), expected, initialized=False)
        initialized = check(spec.get("initContainers"), status.get("initContainerStatuses"), initial, initialized=True)
        active += len(current)
        completed += len(initialized)
        for group, group_totals in totals.items():
            for resource in group_totals:
                total_running = sum(row["resources"][group][resource] for row in current)
                total_initial = max(row["resources"][group][resource] for row in initialized)
                group_totals[resource] += max(total_running, total_initial)
        applications.append({"namespace": name[0], "deployment": name[1], "pod_uid": uid(meta.get("uid")),
            "pod_name": meta["name"], "deployment_uid": uid(target["metadata"]["uid"]), "replica_uid": uid(replica["metadata"]["uid"]),
            "containers": current, "completed_initializers": initialized})
    require(targets == set(APPLICATIONS) | PROBES and active == 13 and completed == 12 and totals == TOTALS)
    require(all(row["metadata"]["uid"] in active_replicas or row["spec"]["replicas"] == 0 for row in rows
                if row["kind"] == "ReplicaSet" and row["metadata"]["namespace"] != "kube-system"))
    applications.sort(key=lambda row: (row["namespace"], row["deployment"]))
    return {"applications": applications, "totals": totals, "owned_pods": len(applications), "running_containers": active,
        "completed_initializers": completed, "system_pods": len(systems),
        "system_inventory_sha256": hashlib.sha256(canonical(systems)).hexdigest(),
        "inventory_sha256": hashlib.sha256(canonical(applications)).hexdigest()}


def image_closure(observed, identities):
    """Bind observed imageID to native verified OCI/config/rootfs image bytes.

    Thirteen running identities require native verification. Twelve completed
    init declarations bind to the same immutable image bytes verified through
    a currently running probe; they do not require retained terminated CRI
    metadata or represent current resource consumption. Validation alone is
    not native proof: the source-bound reader must produce these observations.
    """
    try:
        return _image_closure(observed, identities)
    except (AttributeError, KeyError, StopIteration, TypeError, ValueError):
        raise ResourceObservationError("classifier_resource_image_closure_invalid") from None


def _image_closure(observed, identities):
    require(type(observed) is dict and type(observed.get("applications")) is list and len(observed["applications"]) == 12
            and type(identities) is list and len(identities) == 25)
    expected = {row["container_id"]: (row, completed) for pod in observed["applications"]
        for completed, key in ((False, "containers"), (True, "completed_initializers")) for row in pod[key]}
    require(len(expected) == 25)
    native = {row.get("container_id"): row for row in identities if type(row) is dict and row.get("ready") is True}
    require(len(native) == 13)
    seen = set()
    for identity in identities:
        require(type(identity) is dict)
        cid = identity.get("container_id")
        require(cid in expected and cid not in seen)
        seen.add(cid)
        container, completed = expected[cid]
        common = {"container_id", "expected_reference", "manifest_digest", "config_digest", "rootfs_chain_id"}
        require(set(identity) == common | ({"completed", "exit_code", "image_verified_by_container_id", "declaration_sha256"} if completed else {"ready"})
                and identity["expected_reference"] == container["image"])
        for field in ("manifest_digest", "config_digest", "rootfs_chain_id"):
            require(type(identity[field]) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", identity[field]) is not None)
        if completed:
            require(identity["completed"] is True and type(identity["exit_code"]) is int and identity["exit_code"] == 0)
            reference = native.get(identity["image_verified_by_container_id"])
            require(reference is not None and all(reference[key] == identity[key] for key in common - {"container_id"})
                    and identity["declaration_sha256"] == container["completed_status_sha256"])
        else:
            require(identity["ready"] is True)
        observed_id = image_id(container["observed_image_id"]).removeprefix("containerd://").removeprefix("docker-pullable://")
        if "@" in observed_id:
            repository, observed_id = observed_id.rsplit("@", 1)
            require(repository == container["image"].rsplit("@", 1)[0])
        require(observed_id in {identity["manifest_digest"], identity["config_digest"], container["image"].rsplit("@", 1)[1]})
    require(seen == set(expected))
    return hashlib.sha256(canonical(sorted(identities, key=lambda row: row["container_id"]))).hexdigest()


def _cpu(raw):
    require(type(raw) is str and re.fullmatch(r"(?:max|[1-9][0-9]{0,18}) [1-9][0-9]{0,8}\n?", raw) is not None)
    quota, period = raw.split()
    period = integer(int(period), 1, 1000000)
    return (None if quota == "max" else integer(int(quota), 1)), period


def _bytes(raw, *, unlimited=False):
    require(type(raw) is str and len(raw) <= 32)
    if unlimited and raw in {"max", "max\n"}:
        return None
    require(re.fullmatch(r"(?:0|[1-9][0-9]{0,18})\n?", raw) is not None)
    return integer(int(raw))


def _cpu_stat(raw):
    require(type(raw) is str and 0 < len(raw) <= 4096)
    value = {}
    for line in raw.splitlines():
        match = re.fullmatch(r"([a-z_]+(?:\.[a-z_]+)?) (0|[1-9][0-9]{0,18})", line)
        require(match is not None and match[1] not in value)
        value[match[1]] = integer(int(match[2]))
    required = {"usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec"}
    require(required <= set(value) and set(value) <= required | {"nr_bursts", "burst_usec", "core_sched.force_idle_usec"}
            and value["nr_throttled"] <= value["nr_periods"])
    return {name: value[name] for name in sorted(required)}


def kernel_sample(value, *, cid, pod_uid):
    """Require actual process cgroup ancestry rooted in the owned node namespace."""
    require(type(cid) is str and re.fullmatch(r"[0-9a-f]{64}", cid) is not None)
    uid(pod_uid)
    require(type(value) is dict and set(value) == {"container_id", "pid", "start_ticks", "process_cgroup", "ancestors"}
            and value["container_id"] == cid)
    integer(value["pid"], 1, 2**31 - 1)
    integer(value["start_ticks"], 1)
    expected = "/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod" + pod_uid.replace("-", "_") + ".slice/cri-containerd-" + cid + ".scope"
    require(value["process_cgroup"] == "0::" + expected + "\n")
    path = PurePosixPath(expected)
    paths = [str(p) for p in reversed(path.parents)] + [str(path)]
    rows = value["ancestors"]
    require(type(rows) is list and len(rows) == len(paths))
    projected = []
    for expected_path, row in zip(paths, rows, strict=True):
        require(type(row) is dict and set(row) == {"path", "device", "inode", "cpu.max", "memory.max", "memory.current", "memory.peak", "cpu.stat"}
                and row["path"] == expected_path)
        integer(row["device"])
        integer(row["inode"], 1)
        quota, period = _cpu(row["cpu.max"])
        limit = _bytes(row["memory.max"], unlimited=True)
        current, peak = _bytes(row["memory.current"]), _bytes(row["memory.peak"])
        require(current <= peak and (limit is None or peak <= limit))
        projected.append({"path": expected_path, "device": row["device"], "inode": row["inode"], "cpu_quota_us": quota, "cpu_period_us": period,
            "memory_limit_bytes": limit, "memory_current_bytes": current, "memory_peak_bytes": peak, "cpu": _cpu_stat(row["cpu.stat"])})
    node, classifier = projected[0], projected[-1]
    require(node["cpu_quota_us"] == 6 * node["cpu_period_us"] and node["memory_limit_bytes"] == 8 * 1024**3
            and classifier["cpu_quota_us"] == 2 * classifier["cpu_period_us"] and classifier["memory_limit_bytes"] == 3072 * MIB)
    require(all((row["cpu_quota_us"] is None or row["cpu_quota_us"] >= 2 * row["cpu_period_us"])
                and (row["memory_limit_bytes"] is None or row["memory_limit_bytes"] >= 3072 * MIB) for row in projected))
    return {"container_id": cid, "pid": value["pid"], "start_ticks": value["start_ticks"], "ancestors": projected}


def compare_samples(before, after):
    require(type(before) is dict and type(after) is dict and set(before) == set(after) == {"container_id", "pid", "start_ticks", "ancestors"}
            and all(before[name] == after[name] for name in ("container_id", "pid", "start_ticks"))
            and type(before["ancestors"]) is list and type(after["ancestors"]) is list
            and len(before["ancestors"]) == len(after["ancestors"]) > 1)
    for old, new in zip(before["ancestors"], after["ancestors"], strict=True):
        require(all(old[key] == new[key] for key in ("path", "device", "inode", "cpu_quota_us", "cpu_period_us", "memory_limit_bytes"))
                and new["memory_peak_bytes"] >= old["memory_peak_bytes"]
                and all(new["cpu"][key] >= old["cpu"][key] for key in old["cpu"]))
    child_before, child_after = before["ancestors"][-1], after["ancestors"][-1]
    require(child_after["cpu"]["usage_usec"] > child_before["cpu"]["usage_usec"])
    return {"classifier_cpu_usage_delta_usec": child_after["cpu"]["usage_usec"] - child_before["cpu"]["usage_usec"],
        "classifier_throttled_delta_usec": child_after["cpu"]["throttled_usec"] - child_before["cpu"]["throttled_usec"],
        "classifier_memory_peak_bytes": child_after["memory_peak_bytes"],
        "node_memory_peak_bytes": after["ancestors"][0]["memory_peak_bytes"]}
