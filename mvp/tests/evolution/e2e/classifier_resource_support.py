"""Read-only owned-node cgroup and unfiltered current Kubernetes observations.

No command runs at import. The parent controls original invocation provenance,
the deployment lock and the common deadline around before/sample/after. This
reader alone is not a completed resource or classifier memory-fit proof.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from types import SimpleNamespace

from evolution import classifier_resource_evidence as evidence
from guardrails.evolution.classifier_contract import canonical, parse

from mvp.tests.evolution.e2e.classifier_network_support import (
    SOCKET,
    private_bytes,
    require,
)


class ResourceReader:
    def __init__(self, observer):
        self.observer = observer

    def _node(self, *args, maximum=4096, timeout=10):
        observer = self.observer
        require(observer.bound is not None)
        return observer._command(["docker", "--host", SOCKET, "exec", observer.bound["node_id"], *args], maximum=maximum, timeout=timeout)

    def _process(self, cid, pod_uid):
        raw = self._node("crictl", "inspect", "--output=json", cid, maximum=2 * 1024 * 1024)
        value = parse(raw.encode(), limit=2 * 1024 * 1024)
        status = value.get("status", {})
        require(status.get("id") == cid and status.get("state") == "CONTAINER_RUNNING"
                and status.get("metadata", {}).get("name") == "classifier"
                and status.get("labels", {}).get("io.kubernetes.pod.uid") == pod_uid)
        pid = evidence.integer(value.get("info", {}).get("pid"), 1, 2**31 - 1)
        raw = self._node("head", "-c", "4096", "/proc/" + str(pid) + "/stat")
        match = re.fullmatch(r"([1-9][0-9]*) \((.*)\) ([A-Za-z]) (.*)\n?", raw)
        require(match is not None and int(match[1]) == pid)
        fields = (match[3] + " " + match[4]).split()
        require(len(fields) >= 20 and re.fullmatch(r"[1-9][0-9]*", fields[19]) is not None)
        ticks = evidence.integer(int(fields[19]), 1)
        cgroup = self._node("head", "-c", "4096", "/proc/" + str(pid) + "/cgroup")
        return {"container_id": cid, "pid": pid, "start_ticks": ticks, "process_cgroup": cgroup}

    def _directory(self, path):
        raw = self._node("stat", "--format=%d:%i", "--", path)
        require(re.fullmatch(r"[0-9]+:[1-9][0-9]*\n?", raw) is not None)
        device, inode = (int(value) for value in raw.split(":"))
        return evidence.integer(device), evidence.integer(inode, 1)

    def kernel(self, *, cid, pod_uid):
        require(type(cid) is str and re.fullmatch(r"[0-9a-f]{64}", cid) is not None)
        evidence.uid(pod_uid)
        process = self._process(cid, pod_uid)
        end = "/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod" + pod_uid.replace("-", "_") + ".slice/cri-containerd-" + cid + ".scope"
        require(process["process_cgroup"] == "0::" + end + "\n")
        path = PurePosixPath(end)
        ancestors = [str(parent) for parent in reversed(path.parents)] + [str(path)]
        rows = []
        for suffix in ancestors:
            directory = "/sys/fs/cgroup" + (suffix if suffix != "/" else "")
            device, inode = self._directory(directory)
            row = {"path": suffix, "device": device, "inode": inode}
            for name in ("cpu.max", "memory.max", "memory.current", "memory.peak", "cpu.stat"):
                row[name] = self._node("head", "-c", "4096", directory + "/" + name)
            require(self._directory(directory) == (device, inode))
            rows.append(row)
        require(self._process(cid, pod_uid) == process)
        return evidence.kernel_sample({**process, "ancestors": rows}, cid=cid, pod_uid=pod_uid)

    def _objects(self):
        observer, d = self.observer, self.observer.deployment
        require(d.kubeconfig == d.state / "kubeconfig")
        for name, digest in observer._api_pins.items():
            raw = private_bytes(d.state / name, deadline=observer.deadline,
                modes=(0o600,) if name in observer._api_private else (0o600, 0o644))
            require(hashlib.sha256(raw).hexdigest() == digest)
        require(observer._native(observer._api_owner, observer._api_images) == observer._api_server)
        return observer._command([d.bin / "kubectl", "--kubeconfig", "/dev/stdin", "--context", "guardrails-evolution",
            "--request-timeout=10s", "get", "deployments,replicasets,pods", "--all-namespaces", "-o", "json"],
            maximum=2 * 1024 * 1024, input_text=observer._api_input)

    def _inventory(self, before):
        observer, d = self.observer, self.observer.deployment
        raw = private_bytes(d.state / "assembly/values.json", deadline=observer.deadline)
        require(hashlib.sha256(raw).hexdigest() == before["files"]["assembly/values.json"])
        values = parse(raw)
        # No label or deletion filter: terminating Pods and extra sidecars count.
        objects = parse(self._objects().encode(), limit=2 * 1024 * 1024)
        return evidence.inventory(objects, owner=before["owner_id"], values=values), objects

    def verify_images_locked(self, *, deadline):
        """Verify running OCI identities and bind historical init image declarations.

        This preflight shares the caller's deadline and does not download, pull,
        warm up or start anything. Completed init declarations use the identical
        prepared image bytes verified through running probes; terminated CRI
        process metadata may have been garbage-collected and is not required.
        """
        observer = self.observer
        require(observer.bound is not None)
        observer.deadline = deadline
        observer._remaining()
        before, _, _ = observer._capture()
        require(before == observer.bound)
        inventory, objects = self._inventory(before)
        verifier = observer.deployment.support("image_identity")
        def run(args, *, timeout, check=False):
            require(args[:3] == ["docker", "exec", "guardrails-evolution"] and check is False)
            return 0, self._node(*args[3:], maximum=2 * 1024 * 1024, timeout=timeout)
        identities = []
        for pod in inventory["applications"]:
            raw = next(row for row in objects["items"] if row["kind"] == "Pod" and row["metadata"]["uid"] == pod["pod_uid"])
            for container in pod["containers"]:
                status = next(row for row in raw["status"]["containerStatuses"] if row["name"] == container["name"])
                identities.append(verifier.verify_container(SimpleNamespace(run=run), container["image"], status))
        native = list(identities)
        for pod in inventory["applications"]:
            raw = next(row for row in objects["items"] if row["kind"] == "Pod" and row["metadata"]["uid"] == pod["pod_uid"])
            for container in pod["completed_initializers"]:
                status = next(row for row in raw["status"]["initContainerStatuses"] if row["name"] == container["name"])
                verifier._completed_declaration(status)
                matches = sorted((row for row in native if row["expected_reference"] == container["image"]), key=lambda row: row["container_id"])
                require(bool(matches))
                reference = matches[0]
                identities.append({**{key: reference[key] for key in ("expected_reference", "manifest_digest", "config_digest", "rootfs_chain_id")},
                    "container_id": container["container_id"], "completed": True, "exit_code": 0,
                    "image_verified_by_container_id": reference["container_id"], "declaration_sha256": container["completed_status_sha256"]})
        digest = evidence.image_closure(inventory, identities)
        current, _ = self._inventory(before)
        require(current["inventory_sha256"] == inventory["inventory_sha256"])
        after, _, _ = observer._capture()
        require(after == before)
        return {"binding_sha256": hashlib.sha256(canonical(before)).hexdigest(), "inventory_sha256": inventory["inventory_sha256"],
            "identities": identities, "identities_sha256": digest}

    def capture_locked(self, *, deadline, image_binding):
        observer = self.observer
        require(observer.bound is not None)
        observer.deadline = deadline
        observer._remaining()
        before, _, _ = observer._capture()
        require(before == observer.bound)
        inventory, _ = self._inventory(before)
        require(type(image_binding) is dict and set(image_binding) == {"binding_sha256", "inventory_sha256", "identities", "identities_sha256"}
                and image_binding["binding_sha256"] == hashlib.sha256(canonical(before)).hexdigest()
                and image_binding["inventory_sha256"] == inventory["inventory_sha256"]
                and image_binding["identities_sha256"] == evidence.image_closure(inventory, image_binding["identities"]))
        pod = next(row for row in inventory["applications"] if row["deployment"] == "classifier")
        classifier = next(row for row in pod["containers"] if row["name"] == "classifier")
        require(pod["pod_uid"] == before["pods"]["classifier"]["pod_uid"]
                and "containerd://" + classifier["container_id"] == before["pods"]["classifier"]["container_id"])
        kernel = self.kernel(cid=classifier["container_id"], pod_uid=pod["pod_uid"])
        current, _ = self._inventory(before)
        require(current["inventory_sha256"] == inventory["inventory_sha256"])
        after, _, _ = observer._capture()
        require(before == after)
        return {"binding_sha256": hashlib.sha256(canonical(before)).hexdigest(),
                "inventory": inventory, "images_sha256": image_binding["identities_sha256"], "kernel": kernel}
