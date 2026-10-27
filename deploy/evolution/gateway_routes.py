"""One governed gateway route, installed before release and exactly restored.

The operation owns an immutable routes-only Secret, never the credential Secret.
Its private journal is also the recovery input after a closed case worker. Native
process custody remains the caller's responsibility before independent recovery.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import signal
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import UUID

import httpx

from connector.evolution_routes import RouteManifest
from evolution.evaluator_governance import checked_policy, policy_reference
from evolution.seed import private_read
from guardrails.evolution.models import Binding, Destination, ProviderProfile, canonical_json_bytes, strict_json_loads

LABEL = "guardrails.evolution/route-operation"
VOLUME = "governed-route"
MAXIMUM_BYTES = 1024 * 1024
STAGE_SECONDS = 75
RESTORE_SECONDS = 75
GATE = "import os,sys,resource; f=int(sys.argv[1]); limit=int(sys.argv[2]); ok=os.read(f,1)==b'G'; os.close(f); ok or sys.exit(125); limit and resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit)); os.execvpe(sys.argv[3],sys.argv[3:],os.environ)"
MOUNT_PROBE = """import hashlib,json,os,stat
fd=os.open('/config/routes.json',os.O_RDONLY|os.O_NOFOLLOW)
with os.fdopen(fd,'rb') as f:
 s=os.fstat(f.fileno()); assert stat.S_ISREG(s.st_mode) and s.st_uid in (0,os.geteuid()) and not s.st_mode&0o022
 raw=f.read(1048577); assert len(raw)<=1048576
print(json.dumps({'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw),'uid':s.st_uid,'mode':stat.S_IMODE(s.st_mode)}))
"""


class RouteStageError(RuntimeError):
    """A fixed code, never native output, resource contents or credentials."""


def need(value, code):
    if not value:
        raise RouteStageError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def document(value):
    return canonical_json_bytes(value) + b"\n"


def build_manifest(raw, *, tenant, workload, policy, original_policy, provider, destination):
    """Use approved bytes and the exact original model resources, never latest."""
    original = checked_policy(original_policy, tenant, status="approved")
    approved = checked_policy(policy, tenant, status="approved")
    need(policy_reference(original) != policy_reference(approved), "route_policy_not_new")
    value = strict_json_loads(raw, max_bytes=MAXIMUM_BYTES)
    manifest = RouteManifest.model_validate(value)
    need((manifest.tenant_id, manifest.workload_id) == (tenant, workload), "route_scope")
    # Seed JSON may omit optional defaults such as credential_ref=None. Compare
    # complete validated resources on every side, retaining all identity/digest
    # fields and the schema's refusal of extra or invalid fields.
    provider = ProviderProfile.model_validate_json(canonical_json_bytes(provider)).model_dump(mode="json")
    destination = Destination.model_validate_json(canonical_json_bytes(destination)).model_dump(mode="json")
    rows = manifest.model_dump(mode="json")["routes"]
    matching = [index for index, row in enumerate(rows) if row["policy"] == policy_reference(original)]
    need(len(matching) == 1, "route_original_resources")
    old = rows[matching[0]]
    need(old["provider"] == provider and old["destination"] == destination,
         "route_original_resources")
    for approved_policy in (original, approved):
        need(provider in approved_policy["providers"] and destination in approved_policy["resolved_destinations"],
             "route_approved_resources")
    need(len(rows) < 256 and not any(row["policy"]["digest"] == approved["compiled_digest"] for row in rows),
         "route_duplicate_or_limit")
    # Preserve every original serialized field (including trust/local-service
    # pins); validation cannot silently normalize a changed original route.
    value["routes"].append({**copy.deepcopy(value["routes"][matching[0]]), "policy": policy_reference(approved)})
    RouteManifest.model_validate(value)
    result = canonical_json_bytes(value)
    need(len(result) <= MAXIMUM_BYTES, "route_manifest_bound")
    return result


def staged_spec(original, secret_name, operation):
    value = copy.deepcopy(original)
    need(value["replicas"] == 1 and value["strategy"]["type"] == "Recreate", "route_deployment_shape")
    pod = value["template"]["spec"]
    containers = [row for row in pod["containers"] if row["name"] == "gateway"]
    need(len(containers) == 1 and len(pod["containers"]) == 1, "route_container_shape")
    need(not any(row["name"] == VOLUME for row in pod["volumes"]), "route_volume_conflict")
    mounts = containers[0]["volumeMounts"]
    need(not any(row["mountPath"] == "/config/routes.json" for row in mounts), "route_mount_conflict")
    need(any(row["mountPath"] == "/config" and row.get("readOnly") is True for row in mounts),
         "route_private_mount")
    # A subPath file mount is a regular file for RouteManifest.from_file's
    # O_NOFOLLOW check. No projected symlink or mutable Secret reload is used.
    pod["volumes"].append({"name": VOLUME, "secret": {"secretName": secret_name,
        "defaultMode": 0o444, "items": [{"key": "routes.json", "path": "routes.json"}]}})
    mounts.append({"name": VOLUME, "mountPath": "/config/routes.json", "subPath": "routes.json", "readOnly": True})
    annotations = value["template"]["metadata"].setdefault("annotations", {})
    need(LABEL not in annotations, "route_annotation_conflict")
    annotations[LABEL] = operation
    return value


class Journal:
    def __init__(self, path):
        self.path = Path(path).absolute()
        info = self.path.lstat()
        need(self.path.resolve() == self.path and stat.S_ISDIR(info.st_mode)
             and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "route_journal_directory")
        self.identity = info.st_dev, info.st_ino

    def check(self):
        info = self.path.lstat()
        need(self.path.resolve() == self.path and (info.st_dev, info.st_ino) == self.identity
             and stat.S_IMODE(info.st_mode) == 0o700, "route_journal_changed")

    def write(self, name, value):
        self.check()
        need(Path(name).name == name and name.endswith(".json"), "route_journal_name")
        raw = document(value)
        need(len(raw) <= 4 * MAXIMUM_BYTES, "route_journal_bound")
        fd = os.open(self.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        descriptor = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.check()
        return {"path": str(self.path / name), "sha256": digest(raw)}

    def read(self, name):
        self.check()
        return strict_json_loads(private_read(self.path / name, maximum=4 * MAXIMUM_BYTES), max_bytes=4 * MAXIMUM_BYTES)


class NativePort:
    """Gated synchronous children share the caller group; forwards are explicit.

    The parent must close the worker group before recovery. The durable launch
    record precedes gate release, including for separately owned forwards.
    """
    def __init__(self, deployment, journal, assembly):
        self.deployment, self.journal, self.assembly = deployment, journal, assembly
        self.sequence = 0

    def launch(self, argv, *, forward=False, command_id=None, **kwargs):
        while (self.journal.path / f"native-{self.sequence:03d}-started.json").exists():
            self.sequence += 1
        number = self.sequence; self.sequence += 1
        read_fd, write_fd = os.pipe()
        process = None
        try:
            process = subprocess.Popen([sys.executable, "-B", "-c", GATE, str(read_fd),
                str(0 if forward else 4 * MAXIMUM_BYTES), *map(str, argv)],
                pass_fds=(read_fd,), start_new_session=forward, **kwargs)
            identity = self.assembly._process_identity(process.pid)
            need(identity is not None, "route_native_launch")
            expected = digest(b"\0".join(str(value).encode() for value in argv) + b"\0")
            self.journal.write(f"native-{number:03d}-started.json", {"pid": identity["pid"], "start": identity["start"],
                "command": expected, "group": os.getpgid(process.pid), "forward": forward,
                "gate_command": identity["command"], "argv": list(map(str, argv)),
                "command_id": command_id, "status": "gate_held"})
            self.journal.write(f"native-{number:03d}-dispatch.json", {"status": "may_have_dispatched"})
            os.write(write_fd, b"G")
            process.route_native_number = number
            return process
        except BaseException:
            if process is not None:
                process.kill(); process.wait(timeout=2)
            raise
        finally:
            os.close(read_fd); os.close(write_fd)

    def kubectl(self, *args, namespace, timeout, input_text=None, command_id=None):
        argv = [str(self.deployment.bin / "kubectl"), "--kubeconfig", str(self.deployment.kubeconfig),
            "--context", "guardrails-evolution", "--namespace", namespace, *args]
        return self.run(argv, timeout=timeout, input_text=input_text, command_id=command_id)

    def run(self, argv, *, timeout, input_text=None, command_id=None):
        # RLIMIT_FSIZE bounds output during capture, before any bytes are read.
        # Output stays in an unlinked private file; only bounded bytes/hash leave
        # this adapter. No credential-bearing Secret is requested by this module.
        with tempfile.TemporaryFile(dir=self.journal.path) as output:
            process = self.launch(argv, env=self.deployment.env, command_id=command_id,
                stdin=subprocess.PIPE if input_text else subprocess.DEVNULL, stdout=output, stderr=output)
            failed = False
            try:
                process.communicate(None if input_text is None else input_text.encode(), timeout=timeout)
            except BaseException:
                failed = True
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2)
                raise
            finally:
                self.journal.write(f"native-{process.route_native_number:03d}-closed.json", {
                    "pid": process.pid, "exit_code": process.returncode, "reaped": process.returncode is not None,
                    "interrupted": failed})
            need(output.tell() <= 4 * MAXIMUM_BYTES, "route_native_output_bound")
            output.seek(0)
            raw = output.read(4 * MAXIMUM_BYTES + 1)
            need(process.returncode == 0, "route_native_failed")
            return process.returncode, raw.decode("utf-8", errors="strict")

    def forward(self, argv, **kwargs):
        need(kwargs.pop("start_new_session", None) is True, "route_forward_group")
        return self.launch(argv, forward=True, **kwargs)

    def readiness(self, *, url, ca_file, token_file, timeout):
        deadline = time.monotonic() + timeout
        token = private_read(Path(token_file), maximum=4096).decode().strip()
        with httpx.Client(verify=ssl.create_default_context(cafile=ca_file), trust_env=False,
                          follow_redirects=False) as client:
            while time.monotonic() < deadline:
                left = min(2, deadline - time.monotonic())
                if left <= 0:
                    break
                try:
                    with client.stream("GET", url, headers={"Authorization": "Bearer " + token}, timeout=left) as response:
                        if response.status_code == 200:
                            return 200
                except httpx.TransportError:
                    pass
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise RouteStageError("route_authenticated_readiness")


class GatewayRouteStage:
    def __init__(self, deployment, *, journal_root, deadline, native=None):
        self.deployment, self.journal, self.deadline = deployment, Journal(journal_root), deadline
        self.assembly = deployment.support("assembly")
        self.native = native or NativePort(deployment, self.journal, self.assembly)
        self.intent = None
        self.serial = 0

    def owner(self):
        """Read-only ownership; bootstrap.owner() would launch an unowned Docker command."""
        owner = strict_json_loads(private_read(self.deployment.state / "owner.json", maximum=4096))
        need(owner["profile"] == "guardrails-evolution" and owner["uid"] == os.getuid()
             and owner["state"] == str(self.deployment.state) and owner["container_id"], "route_owner")
        assembly = strict_json_loads(private_read(self.deployment.state / "assembly/owner.json", maximum=4096))
        need(assembly == {key: owner[key] for key in ("owner_id", "container_id", "state", "uid")}, "route_assembly_owner")
        return owner

    def guard_node(self):
        owner = self.owner()
        status, raw = self.native.run(["docker", "container", "inspect", "guardrails-evolution"],
                                     timeout=self.remaining(10))
        rows = strict_json_loads(raw, max_bytes=4 * MAXIMUM_BYTES)
        need(status == 0 and len(rows) == 1 and rows[0]["Id"] == owner["container_id"]
             and rows[0]["State"]["Running"] is True, "route_node_identity")

    def source_refs(self):
        paths = (Path(__file__), Path(self.assembly.__file__), Path(self.deployment.root) / "deploy/evolution/bootstrap.py")
        return [{"path": str(path.absolute()), "sha256": digest(path.read_bytes())} for path in paths]

    def load_intent(self):
        self.intent = self.journal.read("intent.json")
        need(self.intent["schema"] == "evolution-gateway-route-intent-v1"
             and self.intent["state"] == str(self.deployment.state)
             and self.intent["sources"] == self.source_refs(), "route_restore_scope_or_source")
        need(self.intent["owner"] == self.owner(), "route_owner_changed")

    def native_closed(self):
        for path in self.journal.path.glob("native-*-started.json"):
            row = self.journal.read(path.name)
            if not row["forward"]:
                current = self.assembly._process_identity(row["pid"])
                need(current is None or current["start"] != row["start"], "route_native_still_active")

    def unresolved_mutations(self):
        """Local process closure never proves a remote mutation did not commit."""
        unresolved = []
        for path in sorted(self.journal.path.glob("command-*-intent.json")):
            command = self.journal.read(path.name)
            if command["args"][0] not in {"create", "patch", "delete"}:
                continue
            result_path = path.name.replace("-intent", "-result")
            if (self.journal.path / result_path).exists() and self.journal.read(result_path).get("status") == "returned":
                continue
            number = int(path.name.split("-")[1])
            native = [(item, self.journal.read(item.name)) for item in self.journal.path.glob("native-*-started.json")]
            entries = [(item, row) for item, row in native if row["command_id"] == number]
            # No native launch, or a durably held gate with no dispatch-intent,
            # means this command could not reach kubectl. An injected fake port
            # must write the same ledger when modelling interruption.
            dispatched = [(item, row) for item, row in entries
                          if (self.journal.path / item.name.replace("-started", "-dispatch")).exists()]
            if not dispatched:
                continue
            closed = [self.journal.read(item.name.replace("-started", "-closed")) for item, _ in dispatched
                      if (self.journal.path / item.name.replace("-started", "-closed")).exists()]
            if len(closed) == len(dispatched) and all(row["reaped"] and row["exit_code"] == 0 and not row["interrupted"] for row in closed):
                continue
            unresolved.append((number, command))
        return unresolved

    def settle_mutations(self, current):
        projection = self.deployment_projection(current, self.intent["tenant"])
        for number, command in self.unresolved_mutations():
            verb = command["args"][0]
            # An earlier create might still win after absence was observed.
            # Keeping the immutable route is safer than an unsupported retry or
            # delete/absence assertion. Recovery requires separate settlement.
            need(verb != "create", "route_create_outcome_unresolved")
            if verb == "patch":
                matches = [self.journal.read(path.name) for path in self.journal.path.glob("patch-*-intent.json")
                           if self.journal.read(path.name)["command_id"] == number]
                need(len(matches) == 1, "route_patch_journal")
                issued = matches[0]
                need(issued["uid"] == current["metadata"]["uid"], "route_competing_deployment")
                need(current["metadata"]["resourceVersion"] != issued["resource_version"]
                     or projection["spec"] == issued["to_spec"], "route_patch_outcome_unresolved")
            # DELETE is UID/RV conditional and can only remove this immutable
            # operation-owned Secret. It cannot affect a replacement object.

    def remaining(self, maximum=20):
        value = min(maximum, self.deadline - time.monotonic())
        need(value > 0, "route_deadline")
        return value

    def call(self, *args, namespace, body=None, maximum=20):
        self.remaining()
        # Append-only native intent is retained even when the response is lost.
        while (self.journal.path / f"command-{self.serial:03d}-intent.json").exists():
            self.serial += 1
        number = self.serial; self.serial += 1
        raw = None if body is None else document(body).decode()
        self.journal.write(f"command-{number:03d}-intent.json", {"args": list(args), "namespace": namespace,
            "input_sha256": None if raw is None else digest(raw.encode()), "status": "outcome_unknown"})
        try:
            status, output = self.native.kubectl(*args, namespace=namespace,
                timeout=self.remaining(maximum), input_text=raw, command_id=number)
            need(status == 0 and len(output.encode()) <= 4 * MAXIMUM_BYTES, "route_native_result")
        except BaseException:
            self.journal.write(f"command-{number:03d}-result.json", {"status": "refused_or_interrupted"})
            raise
        self.journal.write(f"command-{number:03d}-result.json", {"status": "returned", "exit_code": status,
            "output_sha256": digest(output.encode())})
        return output

    def get(self, kind, name, namespace, *, optional=False):
        args = ["get", kind, name]
        if optional:
            args.append("--ignore-not-found")
        raw = self.call(*args, "-o", "json", namespace=namespace)
        return None if not raw.strip() else strict_json_loads(raw, max_bytes=4 * MAXIMUM_BYTES)

    def deployment_projection(self, row, tenant):
        meta = row["metadata"]
        need(row["apiVersion"] == "apps/v1" and row["kind"] == "Deployment"
             and meta["namespace"] == "gr-" + tenant and meta["name"] == "gateway-" + tenant
             and meta.get("labels", {}).get("guardrails.evolution/owner") == self.owner()["owner_id"],
             "route_deployment_owner")
        return {"uid": meta["uid"], "labels": meta["labels"], "spec": row["spec"]}

    def forward(self, tenant):
        path = self.deployment.state / "assembly" / ("forward-" + tenant + ".json")
        raw = private_read(path, maximum=4096)
        row = strict_json_loads(raw, max_bytes=4096)
        port = self.intent["port"] if self.intent and tenant == self.intent["tenant"] else self.deployment.read_json(
            self.deployment.state / "assembly/ports.json")[tenant]
        need(row["command"] == self.assembly._forward_digest(self.deployment, tenant, port), "route_forward_command")
        current = self.assembly._process_identity(row["pid"])
        need(current is None or current == row, "route_forward_custody")
        return {"path": str(path), "sha256": digest(raw), "raw_base64": base64.b64encode(raw).decode(), "record": row}

    def check_other(self):
        other = self.intent["other_tenant"]
        row = self.get("deployment", "gateway-" + other, "gr-" + other)
        need(self.deployment_projection(row, other) == self.intent["other_deployment"], "route_other_gateway_changed")
        need(self.forward(other) == self.intent["other_forward"], "route_other_forward_changed")

    def check_deployment(self, expected):
        value = self.get("deployment", "gateway-" + self.intent["tenant"], self.intent["namespace"])
        need(self.deployment_projection(value, self.intent["tenant"]) == expected, "route_competing_deployment")
        return value

    def pod_observation(self, expected, routes_sha256):
        namespace = self.intent["namespace"]
        name = "gateway-" + self.intent["tenant"]
        rows = strict_json_loads(self.call("get", "pods", "-l", "guardrails.evolution/instance=" + name,
                                "-o", "json", namespace=namespace), max_bytes=4 * MAXIMUM_BYTES)
        need(rows["kind"] == "List" and len(rows["items"]) <= 8, "route_pod_list")
        active = [row for row in rows["items"] if row.get("status", {}).get("phase") not in {"Succeeded", "Failed"}]
        need(len(active) == 1 and not active[0]["metadata"].get("deletionTimestamp"), "route_pod_count")
        pod = active[0]
        expected_image = expected["spec"]["template"]["spec"]["containers"][0]["image"]
        def identity(value):
            meta, status = value["metadata"], value["status"]
            controllers = [row for row in meta["ownerReferences"] if row.get("controller") is True]
            need(len(controllers) == 1 and controllers[0]["kind"] == "ReplicaSet", "route_pod_controller")
            containers = status["containerStatuses"]
            need(status["phase"] == "Running" and len(containers) == 1 and containers[0]["name"] == "gateway"
                 and containers[0]["ready"] is True and containers[0].get("state", {}).get("running"),
                 "route_pod_not_ready")
            # CRI may display the OCI config digest in status.image. Its imageID
            # identifies the running immutable image; the Pod's requested image
            # must independently retain the exact owned Deployment reference.
            declared = value["spec"]["containers"]
            need(len(declared) == 1 and declared[0]["name"] == "gateway"
                 and declared[0]["image"] == expected_image
                 and containers[0].get("imageID") == expected_image,
                 "route_pod_image_identity")
            need(any(row["type"] == "Ready" and row["status"] == "True" for row in status["conditions"]), "route_pod_condition")
            return {"name": meta["name"], "uid": meta["uid"], "namespace": meta["namespace"],
                    "controller": controllers[0], "container": containers[0]}
        observed = identity(pod)
        need(observed["namespace"] == namespace, "route_pod_namespace")
        replica = self.get("replicaset", observed["controller"]["name"], namespace)
        controllers = [row for row in replica["metadata"]["ownerReferences"] if row.get("controller") is True]
        need(replica["metadata"]["uid"] == observed["controller"]["uid"] and len(controllers) == 1
             and controllers[0]["kind"] == "Deployment" and controllers[0]["uid"] == expected["uid"]
             and controllers[0]["name"] == name, "route_replica_ancestry")
        template = copy.deepcopy(replica["spec"]["template"])
        template["metadata"]["labels"].pop("pod-template-hash", None)
        need(template == expected["spec"]["template"], "route_replica_template")
        mount = strict_json_loads(self.call("exec", pod["metadata"]["name"], "-c", "gateway", "--",
            "timeout", "-k", "2", "8", "python", "-B", "-c", MOUNT_PROBE,
            namespace=namespace, maximum=12), max_bytes=4096)
        need(set(mount) == {"sha256", "size_bytes", "uid", "mode"} and mount["sha256"] == routes_sha256
             and 0 < mount["size_bytes"] <= MAXIMUM_BYTES and mount["uid"] in {0, 10001}
             and not mount["mode"] & 0o022, "route_mounted_bytes")
        after = self.get("pod", observed["name"], namespace)
        need(identity(after) == observed, "route_pod_changed_during_mount_check")
        return {"pod": observed, "replica_set_uid": replica["metadata"]["uid"], "mount": mount}

    def patch(self, current, specification):
        self.guard_node()
        number = len(tuple(self.journal.path.glob("patch-*-intent.json")))
        self.journal.write(f"patch-{number:03d}-intent.json", {"uid": current["metadata"]["uid"],
            "resource_version": current["metadata"]["resourceVersion"], "from_spec": current["spec"],
            "to_spec": specification, "command_id": self.serial})
        self.call("patch", "deployment", current["metadata"]["name"], "--type=json", "--patch-file=/dev/stdin",
            namespace=self.intent["namespace"], body=[
                {"op": "test", "path": "/metadata/uid", "value": current["metadata"]["uid"]},
                {"op": "test", "path": "/metadata/resourceVersion", "value": current["metadata"]["resourceVersion"]},
                {"op": "test", "path": "/spec", "value": current["spec"]},
                {"op": "replace", "path": "/spec", "value": specification}])

    def stop_owned_forward(self, row):
        """A pidfd prevents signaling a reused PID after the identity check."""
        current = self.assembly._process_identity(row["pid"])
        if current is None or current["start"] != row["start"]:
            return
        need(current["command"] in {row["command"], row.get("gate_command", row["command"])}, "route_forward_custody")
        descriptor = os.pidfd_open(row["pid"])
        try:
            need(self.assembly._process_identity(row["pid"]) == current, "route_forward_custody")
            number = len(tuple(self.journal.path.glob("forward-stop-*-started.json")))
            self.journal.write(f"forward-stop-{number:03d}-started.json", {"identity": current})
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            end = time.monotonic() + self.remaining(2)
            while self.assembly._process_identity(row["pid"]) == current and time.monotonic() < end:
                time.sleep(0.01)
            need(self.assembly._process_identity(row["pid"]) != current, "route_forward_stop_timeout")
            try:
                os.waitpid(row["pid"], os.WNOHANG)
            except ChildProcessError:
                pass  # An adopted forward is not this process's child.
            self.journal.write(f"forward-stop-{number:03d}-closed.json", {"identity": current, "active": False})
        finally:
            os.close(descriptor)

    def close_unpublished(self):
        before = self.forward(self.intent["tenant"])
        closed = []
        for path in self.journal.path.glob("native-*-started.json"):
            row = self.journal.read(path.name)
            if row["forward"] and row["pid"] != before["record"]["pid"]:
                need(row["command"] == before["record"]["command"], "route_launched_forward_scope")
                self.stop_owned_forward(row)
                closed.append({key: row[key] for key in ("pid", "start", "command")})
        return closed

    def renew_forward(self, label, pod_uid):
        before = self.forward(self.intent["tenant"])
        allowed = [self.intent["original_forward"]]
        checkpoints = [self.journal.read(path.name) for path in sorted(self.journal.path.glob("*-forward-*.json"))]
        allowed.extend(row["after"] for row in checkpoints)
        # A killed worker may have published a forward record before publishing
        # the high-level receipt. The gated launch ledger still proves its exact
        # PID/start/argv; never adopt a merely similar unrecorded process.
        launched = [self.journal.read(path.name) for path in self.journal.path.glob("native-*-started.json")]
        need(before in allowed or any(row["forward"] and all(before["record"][key] == row[key]
             for key in ("pid", "start", "command")) for row in launched), "route_competing_forward")
        # Close an owned, unpublished forward left outside the worker group.
        self.close_unpublished()
        if any(row["label"] == label and row["pod_uid"] == pod_uid and row["after"] == before for row in checkpoints):
            if self.assembly._forward_listening(before["record"], self.intent["port"]):
                return before
        self.remaining(35)
        self.stop_owned_forward(before["record"])
        self.assembly._forward(self.deployment, self.deployment.state / "assembly", self.intent["tenant"], self.intent["port"],
            timeout=self.remaining(25), spawn=self.native.forward)
        after = self.forward(self.intent["tenant"])
        need(self.assembly._forward_listening(after["record"], self.intent["port"]), "route_forward_unavailable")
        self.journal.write(f"{label}-forward-{len(checkpoints):03d}.json", {
            "label": label, "pod_uid": pod_uid, "before": before, "after": after})
        return after

    def ready(self, expected, label):
        left = max(1, math.floor(self.remaining(45)))
        self.call("rollout", "status", "deployment/gateway-" + self.intent["tenant"], "--timeout=" + str(left) + "s",
                  namespace=self.intent["namespace"], maximum=left)
        row = self.check_deployment(expected)
        status = row.get("status", {})
        need(status.get("observedGeneration", 0) >= row["metadata"]["generation"]
             and all(status.get(key) == 1 for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")),
             "route_rollout_not_ready")
        routes_sha256 = self.intent["staged_routes_sha256" if label == "staged" else "original_routes_sha256"]
        observation = self.pod_observation(expected, routes_sha256)
        forward = self.renew_forward(label, observation["pod"]["uid"])
        self.native.readiness(url=self.intent["gateway_url"] + "/health/ready", ca_file=self.intent["ca_file"],
            token_file=self.intent["gateway_token_file"], timeout=self.remaining(10))
        self.check_other()
        return {"deployment": self.deployment_projection(row, self.intent["tenant"]), "forward": forward,
                "authenticated_readiness": 200, **observation}

    def stage(self, *, operation, tenant, policy, original_policy, original_binding, inventory, access, operator_id):
        need(str(UUID(operation)) == operation and tenant in {"team-a", "team-b"}, "route_operation")
        need(str(UUID(operator_id)) == operator_id and operator_id == inventory["roles"]["operator"]["principal_id"],
             "route_operator")
        binding = Binding.model_validate_json(canonical_json_bytes(original_binding))
        runtime_ids = sorted(row["runtime_id"] for row in inventory["runtimes"].values())
        need(len(runtime_ids) == len(set(runtime_ids)) == 2 and all(str(UUID(value)) == value for value in runtime_ids),
             "route_runtime_inventory")
        need(not any(self.journal.path.iterdir()), "route_journal_not_empty")
        namespace, other = "gr-" + tenant, "team-b" if tenant == "team-a" else "team-a"
        with self.deployment.lock():
            self.guard_node()
            current = self.get("deployment", "gateway-" + tenant, namespace)
            old = self.deployment_projection(current, tenant)
            other_row = self.get("deployment", "gateway-" + other, "gr-" + other)
            # Only the routes key is requested; credential and TLS Secret data
            # never enter command output or this journal.
            encoded = self.call("get", "secret", "gateway-" + tenant + "-config", "-o",
                "jsonpath={.data.routes\\.json}", namespace=namespace)
            raw = base64.b64decode(encoded, validate=True)
            selected = inventory["profiles"]["mock"]
            added = build_manifest(raw, tenant=tenant, workload=inventory["workload"]["workload_id"],
                policy=policy, original_policy=original_policy, provider=selected["provider"], destination=selected["destination"])
            name = "gateway-route-" + UUID(operation).hex
            need(self.get("secret", name, namespace, optional=True) is None, "route_secret_exists")
            secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "immutable": True,
                "metadata": {"name": name, "namespace": namespace, "labels": {
                    "guardrails.evolution/owner": self.owner()["owner_id"], LABEL: operation}},
                "data": {"routes.json": base64.b64encode(added).decode()}}
            updated = {**old, "spec": staged_spec(old["spec"], name, operation)}
            self.intent = {"schema": "evolution-gateway-route-intent-v1", "operation": operation, "tenant": tenant,
                "namespace": namespace, "operator_id": operator_id, "policy": policy_reference(policy),
                "original_policy": policy_reference(original_policy), "original": old, "staged": updated,
                "original_binding": binding.model_dump(mode="json", exclude={"reason"}), "runtime_ids": runtime_ids,
                "secret": secret, "original_routes_sha256": digest(raw), "staged_routes_sha256": digest(added),
                "original_forward": self.forward(tenant), "other_tenant": other,
                "other_deployment": self.deployment_projection(other_row, other), "other_forward": self.forward(other),
                "port": self.deployment.read_json(self.deployment.state / "assembly/ports.json")[tenant],
                "gateway_url": access["tenants"][tenant]["gateway_url"], "gateway_token_file": access["tenants"][tenant]["gateway_token_file"],
                "ca_file": access["ca_file"], "state": str(self.deployment.state),
                "owner": self.owner(), "sources": self.source_refs()}
            need(original_binding["tenant_id"] == tenant and original_binding["workload_id"] == inventory["workload"]["workload_id"]
                 and original_binding["policy"] == self.intent["original_policy"], "route_original_binding")
            self.journal.write("intent.json", self.intent)
            self.journal.write("original-pod.json", self.pod_observation(old, digest(raw)))
            self.check_other()
            self.call("create", "-f", "-", namespace=namespace, body=secret)
            created = self.get("secret", name, namespace)
            self.check_secret(created)
            self.journal.write("secret-created.json", {"uid": created["metadata"]["uid"]})
            current = self.check_deployment(old)
            self.patch(current, updated["spec"])
            observation = self.ready(updated, "staged")
            self.journal.write("active.json", {"schema": "evolution-gateway-route-active-v1", "operation": operation,
                "policy": self.intent["policy"], "routes_sha256": digest(added), **observation})
        return self

    def check_secret(self, value):
        expected = self.intent["secret"]
        need(value is not None and all(value.get(key) == expected[key] for key in ("apiVersion", "kind", "type", "immutable", "data"))
             and all(value["metadata"].get(key) == expected["metadata"][key] for key in ("name", "namespace", "labels")),
             "route_secret_identity")
        if (self.journal.path / "secret-created.json").exists():
            need(value["metadata"]["uid"] == self.journal.read("secret-created.json")["uid"], "route_secret_uid")

    def assert_active(self):
        self.deadline = time.monotonic() + 20
        need(self.intent is not None and (self.journal.path / "active.json").exists(), "route_not_active")
        self.check_deployment(self.intent["staged"])
        self.check_secret(self.get("secret", self.intent["secret"]["metadata"]["name"], self.intent["namespace"]))
        need(self.forward(self.intent["tenant"]) == self.journal.read("active.json")["forward"], "route_competing_forward")
        self.check_other()

    def restore(self, *, verify_binding):
        self.deadline = time.monotonic() + RESTORE_SECONDS
        return restore(self.deployment, journal_root=self.journal.path, deadline=self.deadline,
            worker_closed=True, verify_binding=verify_binding, native=self.native)


def restore(deployment, *, journal_root, deadline, worker_closed=False, verify_binding=None, native=None):
    """Independent recovery: caller has closed the worker and its native children.

    The in-worker finally may call this after its synchronous commands returned.
    No launch or mutation is replayed. Only the sealed operation's exact staged
    or original spec and exact owned immutable Secret are admissible.
    """
    need(worker_closed is True, "route_worker_not_closed")
    op = GatewayRouteStage(deployment, journal_root=journal_root, deadline=deadline, native=native)
    op.native_closed()
    if not (op.journal.path / "intent.json").exists():
        need(not any(p.name.startswith(("secret-", "active", "restored")) for p in op.journal.path.iterdir()),
             "route_unsealed_mutation")
        need(not any(op.journal.read(path.name)["forward"] for path in op.journal.path.glob("native-*-started.json")),
             "route_unsealed_forward")
        return {"schema": "evolution-gateway-route-restored-v1", "status": "not_staged", "gate_pass": False}
    op.load_intent()
    op.close_unpublished()
    need(callable(verify_binding), "route_binding_recovery_required")
    old_binding = op.intent["original_binding"]
    def binding_check():
        value = verify_binding(copy.deepcopy(op.intent))
        need(type(value) is dict and value.get("verified_original_binding") is True
             and all(value.get(key) == old_binding[key] for key in ("tenant_id", "workload_id", "policy", "state"))
             and type(value.get("revision")) is int and value["revision"] >= old_binding["revision"],
             "route_binding_recovery_required")
        return value
    binding = binding_check()
    if not (op.journal.path / "binding-restored.json").exists():
        op.journal.write("binding-restored.json", binding)
    with deployment.lock():
        op.guard_node()
        op.check_other()
        current = op.get("deployment", "gateway-" + op.intent["tenant"], op.intent["namespace"])
        projection = op.deployment_projection(current, op.intent["tenant"])
        need(projection in (op.intent["original"], op.intent["staged"]), "route_competing_deployment")
        op.settle_mutations(current)
        if projection == op.intent["staged"]:
            op.patch(current, op.intent["original"]["spec"])
        if (op.journal.path / "restored.json").exists():
            return verify_restored(deployment, journal_root=journal_root, deadline=deadline, locked=True, native=op.native)
        changes = [op.journal.read(path.name) for path in op.journal.path.glob("command-*-intent.json")
                   if op.journal.read(path.name)["args"][0] in {"create", "patch"}]
        if not changes:
            # A pre-mutation setup refusal must not rotate a healthy forward.
            observed = {"deployment": projection, "forward": op.forward(op.intent["tenant"]),
                        **op.pod_observation(op.intent["original"], op.intent["original_routes_sha256"])}
            need(observed["forward"] == op.intent["original_forward"]
                 and op.assembly._forward_listening(observed["forward"]["record"], op.intent["port"]), "route_original_forward_changed")
            op.native.readiness(url=op.intent["gateway_url"] + "/health/ready", ca_file=op.intent["ca_file"],
                token_file=op.intent["gateway_token_file"], timeout=op.remaining(10))
            observed["authenticated_readiness"] = 200
        else:
            observed = op.ready(op.intent["original"], "restored")
        binding_check()
        secret = op.get("secret", op.intent["secret"]["metadata"]["name"], op.intent["namespace"], optional=True)
        if secret is not None:
            op.check_secret(secret)
            # kubectl v1.35 raw DELETE accepts an explicit DeleteOptions body;
            # server-side UID/RV preconditions prevent name-reuse deletion.
            uri = "/api/v1/namespaces/" + op.intent["namespace"] + "/secrets/" + secret["metadata"]["name"]
            op.call("delete", "--raw=" + uri, "-f", "-", namespace=op.intent["namespace"], body={
                "apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {
                    "uid": secret["metadata"]["uid"], "resourceVersion": secret["metadata"]["resourceVersion"]}})
        need(op.get("secret", op.intent["secret"]["metadata"]["name"], op.intent["namespace"], optional=True) is None,
             "route_secret_not_deleted")
        result = {"schema": "evolution-gateway-route-restored-v1", "operation": op.intent["operation"],
            "status": "restored", "original_spec_restored": True, "created_secret_absent": True,
            "staging_command_issued": bool(changes),
            "other_gateway_unchanged": True, "original_forward": op.intent["original_forward"],
            **observed, "gate_pass": False}
        op.journal.write("restored.json", result)
        return result


def verify_restored(deployment, *, journal_root, deadline, locked=False, native=None):
    op = GatewayRouteStage(deployment, journal_root=journal_root, deadline=deadline, native=native)
    op.load_intent()
    op.native_closed()
    result = op.journal.read("restored.json")
    need(result["schema"] == "evolution-gateway-route-restored-v1" and result["status"] == "restored"
         and result["operation"] == op.intent["operation"] and result["original_spec_restored"] is True
         and result["created_secret_absent"] is True and result["other_gateway_unchanged"] is True
         and result["original_forward"] == op.intent["original_forward"]
         and result["deployment"] == op.intent["original"] and result["authenticated_readiness"] == 200,
         "route_restoration_receipt")
    def check():
        op.guard_node()
        op.check_deployment(op.intent["original"]); op.check_other()
        need(op.forward(op.intent["tenant"]) == result["forward"], "route_restored_forward_changed")
        need(op.assembly._forward_listening(result["forward"]["record"], op.intent["port"]), "route_restored_forward_unavailable")
        observed = op.pod_observation(op.intent["original"], op.intent["original_routes_sha256"])
        need(observed == {key: result[key] for key in ("pod", "replica_set_uid", "mount")}, "route_restored_pod_changed")
        need(op.get("secret", op.intent["secret"]["metadata"]["name"], op.intent["namespace"], optional=True) is None,
             "route_secret_reappeared")
        return result
    if locked:
        return check()
    with deployment.lock():
        return check()


def close_unpublished_forwards(deployment, *, journal_root, deadline, native=None):
    """Local owned-child cleanup before governance reconciliation; no route mutation."""
    op = GatewayRouteStage(deployment, journal_root=journal_root, deadline=deadline, native=native)
    op.native_closed()
    if not (op.journal.path / "intent.json").exists():
        need(not any(op.journal.read(path.name)["forward"] for path in op.journal.path.glob("native-*-started.json")),
             "route_unsealed_forward")
        return []
    op.load_intent()
    return op.close_unpublished()
