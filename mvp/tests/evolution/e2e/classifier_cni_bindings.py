"""Concrete original CNI binding capture; no activity occurs during imports.

The parent owns the lock, native image preflight and original host sink. A
binding returned here is observational input, not a completed CNI acceptance
case. The recoverable public closure must accompany the eventual group report.
"""
from __future__ import annotations

import hashlib
import re

from evolution import classifier_cni_evidence as cni
from evolution import classifier_resource_evidence as resource
from guardrails.evolution.classifier_contract import canonical, parse

from mvp.tests.evolution.e2e.classifier_network_support import private_bytes, require
from mvp.tests.evolution.e2e.classifier_resource_support import ResourceReader

SERVICES = {
    "classifier_service": ("gr-platform", "classifier", "classifier", (8443,)),
    "sink_service": ("gr-platform", "probe", "tls-probe", (8443, 9443, 8445)),
    "mock_service": ("gr-model", "mock", "mock", (8443,)),
    "postgres_service": ("gr-platform", "postgres", "postgres", (5432,)),
}


def network_projection(value, inventory, objects, *, owner):
    """Project current Services and complete endpoint identity, never DNS guesses."""
    try:
        return _network_projection(value, inventory, objects, owner=owner)
    except (AttributeError, KeyError, StopIteration, TypeError, ValueError):
        raise cni.CniObservationError("classifier_cni_network_identity_invalid") from None


def _network_projection(value, inventory, objects, *, owner):
    require(type(value) is dict and type(value.get("items")) is list and len(value["items"]) <= 512)
    rows, seen = value["items"], set()
    for row in rows:
        require(type(row) is dict and row.get("kind") in {"Service", "EndpointSlice", "NetworkPolicy"})
        meta = row["metadata"]
        key = row["kind"], meta["namespace"], meta["name"]
        require(key not in seen)
        seen.add(key)
    def service(namespace, name):
        matches = [r for r in rows if r["kind"] == "Service" and (r["metadata"]["namespace"], r["metadata"]["name"]) == (namespace, name)]
        require(len(matches) == 1)
        value = matches[0]
        require(not value["metadata"].get("deletionTimestamp"))
        resource.uid(value["metadata"]["uid"])
        spec = value["spec"]
        require(spec.get("type") == "ClusterIP" and not spec.get("externalIPs") and not spec.get("externalName")
                and spec.get("ipFamilies") == ["IPv4"] and spec.get("clusterIPs") == [spec.get("clusterIP")])
        cni.private_ip(spec.get("clusterIP"))
        return value
    addresses, closure = {}, {}
    for key, (namespace, name, deployment, ports) in SERVICES.items():
        value = service(namespace, name)
        spec = value["spec"]
        require(value["metadata"].get("labels", {}).get("guardrails.evolution/owner") == owner
                and spec.get("selector") == ({"app": "tls-probe"} if deployment == "tls-probe" else {"guardrails.evolution/component": deployment}))
        observed_ports = spec.get("ports")
        require(type(observed_ports) is list and len(observed_ports) == len(ports)
                and sorted((p.get("port"), p.get("targetPort"), p.get("protocol")) for p in observed_ports) == [(p, p, "TCP") for p in sorted(ports)])
        pod = next(row for row in inventory["applications"] if (row["namespace"], row["deployment"]) == (namespace, deployment))
        raw_pod = next(row for row in objects["items"] if row["kind"] == "Pod" and row["metadata"]["uid"] == pod["pod_uid"])
        pod_ip = cni.private_ip(raw_pod["status"]["podIP"])
        matches = [r for r in rows if r["kind"] == "EndpointSlice" and r["metadata"]["namespace"] == namespace
            and r["metadata"].get("labels", {}).get("kubernetes.io/service-name") == name]
        require(len(matches) == 1)
        target = matches[0]
        meta = target["metadata"]
        resource.uid(meta["uid"])
        refs = [r for r in meta.get("ownerReferences", []) if r.get("controller") is True]
        require(not meta.get("deletionTimestamp") and len(refs) == 1 and refs[0].get("kind") == "Service"
                and refs[0].get("name") == name and refs[0].get("uid") == value["metadata"]["uid"]
                and target.get("addressType") == "IPv4")
        expected_ports = sorted((p["name"], p["port"], "TCP") for p in observed_ports)
        require(sorted((p.get("name"), p.get("port"), p.get("protocol")) for p in target.get("ports", [])) == expected_ports)
        ends = target.get("endpoints")
        require(type(ends) is list and len(ends) == 1)
        end = ends[0]
        require(end.get("addresses") == [pod_ip] and end.get("conditions", {}).get("ready") is True
                and end.get("conditions", {}).get("serving", True) is True and end.get("conditions", {}).get("terminating", False) is False
                and end.get("targetRef", {}).get("kind") == "Pod" and end["targetRef"].get("namespace") == namespace
                and end["targetRef"].get("name") == pod["pod_name"] and end["targetRef"].get("uid") == pod["pod_uid"])
        addresses[key] = spec["clusterIP"]
        closure[key] = {"uid": value["metadata"]["uid"], "spec": spec, "endpoint_slice_uid": meta["uid"],
            "endpoint_ports": target["ports"], "pod_uid": pod["pod_uid"], "pod_ip": pod_ip}
    for key, namespace, name, ports in (("api_service", "default", "kubernetes", {(443, "TCP")}),
                                      ("dns_service", "kube-system", "kube-dns", {(53, "TCP"), (53, "UDP")})):
        value = service(namespace, name)
        service_ports = value["spec"].get("ports")
        require(type(service_ports) is list)
        observed = {(p.get("port"), p.get("protocol")) for p in service_ports}
        require(len(service_ports) == len(observed) and (observed == ports or
                (key == "dns_service" and observed == ports | {(9153, "TCP")})))
        addresses[key] = value["spec"]["clusterIP"]
        closure[key] = {"uid": value["metadata"]["uid"], "spec": value["spec"]}
    policies = sorted(({"namespace": r["metadata"]["namespace"], "name": r["metadata"]["name"],
        "uid": resource.uid(r["metadata"]["uid"]), "spec": r["spec"]} for r in rows if r["kind"] == "NetworkPolicy"),
        key=lambda row: (row["namespace"], row["name"]))
    require(policies)
    return addresses, {"services": closure, "network_policies": policies}


class CniBindingReader:
    def __init__(self, observer, image_binding, host_sink):
        self.observer, self.image_binding, self.host_sink = observer, image_binding, host_sink
        self.resources = ResourceReader(observer)
        self.bound = self.closure = None

    def _network(self):
        observer, d = self.observer, self.observer.deployment
        require(d.kubeconfig == d.state / "kubeconfig")
        for name, digest in observer._api_pins.items():
            raw = private_bytes(d.state / name, deadline=observer.deadline,
                modes=(0o600,) if name in observer._api_private else (0o600, 0o644))
            require(hashlib.sha256(raw).hexdigest() == digest)
        require(observer._native(observer._api_owner, observer._api_images) == observer._api_server)
        raw = observer._command([d.bin / "kubectl", "--kubeconfig", "/dev/stdin", "--context", "guardrails-evolution",
            "--request-timeout=10s", "get", "services,endpointslices.discovery.k8s.io,networkpolicies", "--all-namespaces", "-o", "json"],
            maximum=2 * 1024 * 1024, input_text=observer._api_input)
        return parse(raw.encode(), limit=2 * 1024 * 1024)

    def _process(self, pod, container):
        cid = container["container_id"]
        raw = self.resources._node("crictl", "inspect", "--output=json", cid, maximum=2 * 1024 * 1024)
        native = parse(raw.encode(), limit=2 * 1024 * 1024)
        status = native.get("status", {})
        require(status.get("id") == cid and status.get("state") == "CONTAINER_RUNNING"
                and status.get("metadata", {}).get("name") == container["name"]
                and status.get("labels", {}).get("io.kubernetes.pod.uid") == pod["pod_uid"])
        pid = resource.integer(native.get("info", {}).get("pid"), 1, 2**31 - 1)
        def start():
            raw = self.resources._node("head", "-c", "4096", "/proc/" + str(pid) + "/stat")
            match = re.fullmatch(r"([1-9][0-9]*) \((.*)\) ([A-Za-z]) (.*)\n?", raw)
            require(match is not None and int(match[1]) == pid)
            fields = (match[3] + " " + match[4]).split()
            require(len(fields) >= 20 and re.fullmatch(r"[1-9][0-9]*", fields[19]) is not None)
            return resource.integer(int(fields[19]), 1)
        ticks = start()
        raw = self.resources._node("readlink", "--", "/proc/" + str(pid) + "/ns/net")
        match = re.fullmatch(r"net:\[([1-9][0-9]*)\]\n?", raw)
        require(match is not None)
        inode = resource.integer(int(match[1]), 1)
        require(start() == ticks)
        return {"pid": pid, "process_start_ticks": ticks, "netns_inode": inode}

    def _capture(self, *, deadline):
        observer = self.observer
        require(observer.bound is not None)
        observer.deadline = deadline
        observer._remaining()
        before, _, _ = observer._capture()
        require(before == observer.bound)
        inventory, objects = self.resources._inventory(before)
        image = self.image_binding
        require(type(image) is dict and set(image) == {"binding_sha256", "inventory_sha256", "identities", "identities_sha256"}
                and image["binding_sha256"] == hashlib.sha256(canonical(before)).hexdigest()
                and image["inventory_sha256"] == inventory["inventory_sha256"]
                and image["identities_sha256"] == resource.image_closure(inventory, image["identities"]))
        addresses, network = network_projection(self._network(), inventory, objects, owner=before["owner_id"])
        peers, processes = {}, {}
        for name, (namespace, deployment, component) in cni.ORIGINS.items():
            pod = next(row for row in inventory["applications"] if (row["namespace"], row["deployment"]) == (namespace, deployment))
            raw = next(row for row in objects["items"] if row["kind"] == "Pod" and row["metadata"]["uid"] == pod["pod_uid"])
            container = next(row for row in pod["containers"] if row["name"] == component)
            process = self._process(pod, container)
            processes[name] = process
            peers[name] = {"namespace": namespace, "pod_name": pod["pod_name"], "pod_uid": pod["pod_uid"], "container": component,
                "container_id": container["container_id"], "pod_ip": cni.private_ip(raw["status"]["podIP"]),
                "process_start_ticks": process["process_start_ticks"], "netns_inode": process["netns_inode"]}
        require(len({row["netns_inode"] for row in peers.values()}) == len(peers))
        ca = private_bytes(observer.deployment.state / "credentials/ca.crt", deadline=deadline, modes=(0o600, 0o644), maximum=65536)
        host = self.host_sink.identity()
        bound = cni.bindings({"owner_id": before["owner_id"], "node_id": before["node_id"],
            "readiness_binding_sha256": hashlib.sha256(canonical(before)).hexdigest(), "peers": peers, "addresses": addresses, "host_sink": host})
        current, _ = self.resources._inventory(before)
        after, _, _ = observer._capture()
        require(before == after and current["inventory_sha256"] == inventory["inventory_sha256"])
        closure = {"inventory_sha256": inventory["inventory_sha256"], "native_images_sha256": image["identities_sha256"],
            "processes": processes, "network": network, "sink_ca_sha256": hashlib.sha256(ca).hexdigest()}
        return bound, closure

    def bind_locked(self, *, deadline):
        require(self.bound is None and self.closure is None)
        before, closure = self._capture(deadline=deadline)
        after, current = self._capture(deadline=deadline)
        require(before == after and closure == current)
        self.bound, self.closure = before, closure
        return {"binding": before, "closure": closure}

    def __call__(self, *, deadline):
        require(self.bound is not None and self.closure is not None)
        bound, closure = self._capture(deadline=deadline)
        require(bound == self.bound and closure == self.closure)
        return bound

    def recheck_locked(self, *, deadline, origins):
        """Check current routing/all Pod identities and the executing processes.

        OCI content is verified once by the native image preflight. Every row
        still checks the full current Pod/CID/image inventory and Service/
        EndpointSlice/policy closure. Its executing peer is rechecked natively;
        TLS controls also check their target. The full six-process closure is
        read again at group end. A restart cannot restore the prior PID start
        time and container identity, so it cannot disappear between these reads.
        """
        require(self.bound is not None and self.closure is not None and type(origins) in (tuple, list)
                and 1 <= len(origins) <= 2 and len(origins) == len(set(origins)) and set(origins) <= set(cni.ORIGINS))
        observer = self.observer
        observer.deadline = deadline
        observer._remaining()
        owner, _, pins = observer._files()
        require(owner["owner_id"] == self.bound["owner_id"] and owner["container_id"] == self.bound["node_id"]
                and all(observer.bound["files"].get(name) == digest for name, digest in pins.items()))
        inventory, objects = self.resources._inventory(observer.bound)
        require(inventory["inventory_sha256"] == self.closure["inventory_sha256"]
                and resource.image_closure(inventory, self.image_binding["identities"]) == self.closure["native_images_sha256"])
        addresses, network = network_projection(self._network(), inventory, objects, owner=self.bound["owner_id"])
        require(addresses == self.bound["addresses"] and network == self.closure["network"])
        for name, (namespace, deployment, component) in cni.ORIGINS.items():
            pod = next(row for row in inventory["applications"] if (row["namespace"], row["deployment"]) == (namespace, deployment))
            raw = next(row for row in objects["items"] if row["kind"] == "Pod" and row["metadata"]["uid"] == pod["pod_uid"])
            container = next(row for row in pod["containers"] if row["name"] == component)
            peer = self.bound["peers"][name]
            require(pod["pod_uid"] == peer["pod_uid"] and pod["pod_name"] == peer["pod_name"]
                    and container["container_id"] == peer["container_id"] and raw["status"]["podIP"] == peer["pod_ip"])
            if name in origins:
                require(self._process(pod, container) == self.closure["processes"][name])
        ca = private_bytes(observer.deployment.state / "credentials/ca.crt", deadline=deadline, modes=(0o600, 0o644), maximum=65536)
        require(hashlib.sha256(ca).hexdigest() == self.closure["sink_ca_sha256"] and self.host_sink.identity() == self.bound["host_sink"])
        observer._remaining()
        return self.bound
