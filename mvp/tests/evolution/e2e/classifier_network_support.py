"""Parent-executed, authenticated in-cluster classifier dispatch observations.

No calls occur at import. Construct EvaluatorCluster before acquiring its
deployment lock; bind_locked/sample_locked must run while that lock is held.
The lock serializes acceptance requests, not unrelated users: positive deltas
count attempted child writes only. All unknown command/response data is private.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import time
from types import SimpleNamespace

from evolution.classifier_dispatch import delta, snapshot
from evolution.seed import private_read
from guardrails.evolution.classifier_contract import canonical, identity, parse

NAMESPACE = "gr-platform"
HOST = "classifier.gr-platform.svc.cluster.local"
SOCKET = "unix:///var/run/docker.sock"
MAX_METADATA = 2 * 1024 * 1024
SOURCES = (
    "mvp/tests/evolution/e2e/classifier_network_support.py",
    "mvp/evolution/classifier_dispatch.py",
    "services/classifier/app.py",
    "mvp/guardrails/evolution/classifier_contract.py",
    "deploy/evolution/image_identity.py",
)

# Runs inside the already verified runtime container. Its argv contains only
# public IP/identity and high-entropy credential digests. Token/CA bytes are read
# from the existing private mounts and never emitted, even on errors.
READY_SCRIPT = r'''
import hashlib, http.client, ipaddress, json, os, re, signal, socket, ssl, stat, sys, time

def need(value):
    if not value:
        raise ValueError()

def pairs(rows):
    result = {}
    for key, value in rows:
        need(key not in result)
        result[key] = value
    return result

def load(raw):
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))

def read(path, limit, digest):
    before = os.lstat(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        held = os.fstat(stream.fileno())
        need(stat.S_ISREG(held.st_mode) and held.st_uid == os.getuid()
             and stat.S_IMODE(held.st_mode) == 0o600 and held.st_nlink == 1
             and 0 < held.st_size <= limit)
        raw = stream.read(limit + 1)
        fields = lambda row: (row.st_dev,row.st_ino,row.st_mode,row.st_uid,row.st_nlink,
            row.st_size,row.st_mtime_ns,row.st_ctime_ns)
        need(all(fields(row) == fields(held) for row in (before, os.fstat(stream.fileno()), os.lstat(path))))
    need(len(raw) <= limit and hashlib.sha256(raw).hexdigest() == digest)
    return raw

def perform(settings):
    need(type(settings) is dict and set(settings) == {'address','identity','token_sha256','ca_sha256'})
    address = ipaddress.ip_address(settings['address'])
    need(str(address) == settings['address'] and address.is_private
         and not (address.is_loopback or address.is_multicast or address.is_unspecified))
    for key in ('token_sha256','ca_sha256'):
        need(type(settings[key]) is str and re.fullmatch('[0-9a-f]{64}', settings[key]))
    ca = read('/config/ca.crt', 65536, settings['ca_sha256'])
    token = read('/config/classifier.token', 4097, settings['token_sha256']).decode('ascii').removesuffix('\n')
    need(32 <= len(token) <= 4096 and all(33 <= ord(c) <= 126 for c in token))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=ca.decode('ascii'))
    started = time.monotonic()
    raw = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
    try:
        raw.settimeout(2.5)
        raw.connect((str(address), 8443))
        with context.wrap_socket(raw, server_hostname='classifier.gr-platform.svc.cluster.local') as channel:
            channel.settimeout(2.5)
            protocol = channel.version()
            channel.sendall(('GET /v1/classifier/ready HTTP/1.1\r\n'
                'Host: classifier.gr-platform.svc.cluster.local:8443\r\n'
                'Authorization: Bearer ' + token + '\r\n'
                'Accept: application/json\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n').encode('ascii'))
            response = http.client.HTTPResponse(channel)
            response.begin()
            headers = response.getheaders()
            need(response.status == 200 and len(headers) <= 64 and protocol in {'TLSv1.2','TLSv1.3'})
            selected = {}
            for name, value in headers:
                need(len(name) <= 128 and len(value) <= 4096)
                name = name.lower()
                if name in {'content-length','content-type','content-encoding','transfer-encoding',
                            'x-evolution-classifier-worker-epoch','x-evolution-classifier-dispatch-attempts'}:
                    need(name not in selected)
                    selected[name] = value
            need(selected.get('content-type') == 'application/json'
                 and selected.get('content-encoding', 'identity') == 'identity'
                 and 'transfer-encoding' not in selected)
            length = selected.get('content-length', '')
            need(re.fullmatch('0|[1-9][0-9]{0,3}', length) and 0 < int(length) <= 4096)
            body = response.read(4097)
            need(len(body) == int(length))
            value = load(body)
            need(type(value) is dict and set(value) == {'api_major','ready','identity'}
                 and type(value['api_major']) is int and value['api_major'] == 1
                 and value['ready'] is True and value['identity'] == settings['identity'])
            epoch = selected.get('x-evolution-classifier-worker-epoch', '')
            attempts = selected.get('x-evolution-classifier-dispatch-attempts', '')
            need(re.fullmatch('[0-9a-f]{32}', epoch)
                 and re.fullmatch('0|[1-9][0-9]{0,18}', attempts) and int(attempts) <= 2**63 - 1)
            elapsed = time.monotonic() - started
            need(0 <= elapsed < 8)
            return {'status': 200, 'tls': protocol, 'epoch': epoch, 'attempts': int(attempts),
                    'identity_sha256': hashlib.sha256(json.dumps(value['identity'], sort_keys=True,
                        separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
                    'elapsed_ms': round(elapsed * 1000, 6)}
    finally:
        raw.close()

if __name__ == '__main__':
    def expired(*unused):
        raise TimeoutError()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 8)
    try:
        need(len(sys.argv) == 2 and len(sys.argv[1]) <= 4096)
        result = perform(load(sys.argv[1]))
    except BaseException:
        print('{"error":"classifier_ready_probe_failed"}')
        raise SystemExit(1) from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    print(json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False))
'''


class ClassifierObservationError(RuntimeError):
    """Only a finite static refusal crosses the acceptance boundary."""


def require(condition):
    if not condition:
        raise ClassifierObservationError("classifier_network_observation_invalid")


def _uid(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f-]{36}", value) is not None)
    return value


def _reference(row, kind):
    refs = [r for r in row.get("metadata", {}).get("ownerReferences", []) if r.get("controller") is True]
    require(len(refs) == 1 and refs[0].get("kind") == kind)
    _uid(refs[0].get("uid"))
    return refs[0]


def project_objects(value, owner_id, expected_images, runtime):
    """Pure closure over one bounded API List, rejecting transitional targets."""
    require(type(value) is dict and type(value.get("items")) is list and len(value["items"]) <= 256)
    rows = value["items"]
    seen = set()
    for row in rows:
        require(type(row) is dict and type(row.get("metadata")) is dict)
        meta = row["metadata"]
        key = row.get("kind"), meta.get("namespace"), meta.get("name")
        require(key not in seen and key[1] == NAMESPACE)
        seen.add(key)
    def one(kind, name):
        selected = [r for r in rows if r.get("kind") == kind and r["metadata"].get("name") == name]
        require(len(selected) == 1 and not selected[0]["metadata"].get("deletionTimestamp"))
        return selected[0]
    service = one("Service", "classifier")
    spec, meta = service["spec"], service["metadata"]
    address = ipaddress.ip_address(spec.get("clusterIP"))
    require(meta.get("labels", {}).get("guardrails.evolution/owner") == owner_id
            and spec.get("type") == "ClusterIP" and spec.get("selector") == {"guardrails.evolution/component": "classifier"}
            and spec.get("ports") == [{"name": "https", "port": 8443, "targetPort": 8443, "protocol": "TCP"}]
            and not spec.get("externalIPs") and not spec.get("externalName")
            and address.is_private and not (address.is_loopback or address.is_multicast or address.is_unspecified))
    result = {"service": {"uid": _uid(meta.get("uid")), "address": str(address)}, "pods": {}}
    statuses = {}
    for name, component in ((runtime, "runtime"), ("classifier", "classifier")):
        target = one("Deployment", name)
        require(target["metadata"].get("labels", {}).get("guardrails.evolution/owner") == owner_id
                and target.get("spec", {}).get("replicas") == 1)
        pods = [r for r in rows if r.get("kind") == "Pod" and r["metadata"].get("labels", {}).get("guardrails.evolution/instance") == name]
        require(len(pods) == 1)
        pod = pods[0]
        require(not pod["metadata"].get("deletionTimestamp") and pod.get("status", {}).get("phase") == "Running")
        pod_spec = pod["spec"]
        require(pod_spec.get("nodeName") == "guardrails-evolution" and not pod_spec.get("hostNetwork")
                and pod_spec.get("serviceAccountName") == name and pod_spec.get("automountServiceAccountToken") is False
                and pod["metadata"].get("labels", {}).get("guardrails.evolution/component") == component)
        ref = _reference(pod, "ReplicaSet")
        replica = one("ReplicaSet", ref["name"])
        require(replica["metadata"].get("uid") == ref["uid"])
        parent = _reference(replica, "Deployment")
        require(parent["name"] == name and parent["uid"] == target["metadata"].get("uid"))
        containers, actual = pod_spec.get("containers"), pod.get("status", {}).get("containerStatuses")
        require(type(containers) is list and len(containers) == 1 and type(actual) is list and len(actual) == 1)
        container, status = containers[0], actual[0]
        require(container.get("name") == status.get("name") == component and container.get("image") == expected_images[component]
                and status.get("ready") is True and set(status.get("state", {})) == {"running"}
                and type(status.get("restartCount")) is int and status["restartCount"] >= 0
                and re.fullmatch(r"containerd://[0-9a-f]{64}", status.get("containerID", "")) is not None)
        require(container.get("securityContext", {}).get("readOnlyRootFilesystem") is True)
        result["pods"][name] = {"pod_name": pod["metadata"]["name"], "pod_uid": _uid(pod["metadata"].get("uid")),
            "deployment_uid": _uid(target["metadata"].get("uid")), "replica_uid": _uid(ref["uid"]),
            "container_id": status["containerID"], "image": container["image"], "restarts": status["restartCount"],
            "pod_ip": str(ipaddress.ip_address(pod["status"]["podIP"])),
            "spec_sha256": hashlib.sha256(canonical(pod_spec)).hexdigest()}
        statuses[component] = status
    selected_pods = [r for r in rows if r.get("kind") == "Pod"
                     and r["metadata"].get("labels", {}).get("guardrails.evolution/component") == "classifier"]
    require(len(selected_pods) == 1 and selected_pods[0]["metadata"]["uid"] == result["pods"]["classifier"]["pod_uid"])
    slices = [r for r in rows if r.get("kind") == "EndpointSlice"
              and r["metadata"].get("labels", {}).get("kubernetes.io/service-name") == "classifier"]
    require(len(slices) == 1)
    target = slices[0]
    ref = _reference(target, "Service")
    require(ref["name"] == "classifier" and ref["uid"] == result["service"]["uid"]
            and not target["metadata"].get("deletionTimestamp")
            and target.get("addressType") == ("IPv4" if address.version == 4 else "IPv6")
            and target.get("ports") == [{"name": "https", "protocol": "TCP", "port": 8443}])
    ends = target.get("endpoints")
    require(type(ends) is list and len(ends) == 1)
    end, pod = ends[0], result["pods"]["classifier"]
    require(end.get("addresses") == [pod["pod_ip"]]
            and end.get("conditions", {}).get("ready") is True
            and end.get("conditions", {}).get("serving", True) is True
            and end.get("conditions", {}).get("terminating", False) is False
            and end.get("targetRef", {}).get("kind") == "Pod"
            and end["targetRef"].get("namespace") == NAMESPACE
            and end["targetRef"].get("name") == pod["pod_name"]
            and end["targetRef"].get("uid") == pod["pod_uid"])
    result["service"]["endpoint_slice_uid"] = _uid(target["metadata"].get("uid"))
    return result, statuses


class ReadinessObserver:
    """One exact owned profile, with image verification once and drift refusal.

    No implicit platform/up/reconcile, forward, retry or cleanup mutation. Public
    binding metadata can be attached; mounted credential bytes remain private.
    bind_locked costs at most 120 seconds, sample_locked at most 50 seconds.
    """
    def __init__(self, cluster, *, runtime="runtime-1"):
        require(runtime in {"runtime-1", "runtime-2"} and cluster.profile == "classifier")
        self.cluster, self.deployment, self.runtime = cluster, cluster.deployment, runtime
        self.bound = None

    def _remaining(self, maximum=10):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0)
        return min(maximum, remaining)

    def _routing(self):
        for env in (os.environ, self.deployment.env):
            require(env.get("DOCKER_HOST", SOCKET) == SOCKET and not any(env.get(k) for k in (
                "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")))

    def _command(self, args, *, maximum=MAX_METADATA, timeout=10):
        self._routing()
        try:
            code, raw = self.deployment.run(args, timeout=self._remaining(timeout), check=False)
        except Exception:  # noqa: BLE001 - infrastructure output must not enter public evidence
            raise ClassifierObservationError("classifier_network_command_failed") from None
        require(code == 0 and type(raw) is str and len(raw) <= maximum and len(raw.encode()) <= maximum)
        self._remaining()
        return raw

    def _kube(self, *args, maximum=MAX_METADATA):
        d = self.deployment
        return self._command([d.bin / "kubectl", "--kubeconfig", d.kubeconfig,
            "--context", "guardrails-evolution", "--namespace", NAMESPACE, *args], maximum=maximum)

    def _files(self):
        d = self.deployment
        names = ("owner.json", "prepared.json", "assembly/access.json", "assembly/values.json",
                 "assembly/private/classifier.token", "assembly/tls/gr-platform/classifier/ca.crt")
        material = {name: private_read(d.state / name, maximum=MAX_METADATA) for name in names}
        owner, prepared = parse(material["owner.json"], limit=MAX_METADATA), parse(material["prepared.json"], limit=MAX_METADATA)
        require(owner.get("uid") == os.getuid() and owner.get("state") == str(d.state)
                and owner.get("profile") == "guardrails-evolution"
                and re.fullmatch(r"[0-9a-f]{64}", owner.get("container_id", "")) is not None
                and re.fullmatch(r"[0-9a-f]{32}", owner.get("owner_id", "")) is not None)
        require(parse(material["assembly/access.json"])["profile"] == "classifier")
        values = parse(material["assembly/values.json"], limit=MAX_METADATA)
        require(values.get("ownerId") == owner["owner_id"] and values.get("evaluatorProfile") == "classifier")
        artifacts = prepared.get("artifacts")
        require(type(artifacts) is list)
        images = {}
        for name, artifact in (("node", "kicbase-offline"), ("runtime", "evolution-service"), ("classifier", "evolution-classifier")):
            matches = [r for r in artifacts if r.get("name") == artifact and r.get("kind") == "image"]
            require(len(matches) == 1)
            reference = matches[0].get("source_url", "").removeprefix("docker://")
            require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", reference) is not None)
            images[name] = reference
        pins = {name: hashlib.sha256(raw).hexdigest() for name, raw in material.items()}
        return owner, images, pins

    def _capture(self):
        owner, images, pins = self._files()
        raw = self._command(["docker", "--host", SOCKET, "container", "inspect", owner["container_id"],
            "--format", '{{json .Id}} {{json .Name}} {{json .State.Running}} {{json .State.Paused}} {{json .State.Restarting}} {{json .State.Dead}} {{json .Config.Image}}'])
        expected = ' '.join(json.dumps(v) for v in (owner["container_id"], "/guardrails-evolution", True, False, False, False, images["node"]))
        require(raw.strip() == expected)
        objects, statuses = project_objects(parse(self._kube("get", "deployments,replicasets,pods,services,endpointslices.discovery.k8s.io", "-o", "json").encode(), limit=MAX_METADATA),
            owner["owner_id"], images, self.runtime)
        for row in self.cluster.endpoints.values():
            destination = row["destination"]
            require(destination["host"] == HOST and destination["port"] == 8443 and destination["path_prefix"] == "/v1/classifier"
                    and destination["cidrs"] == [objects["service"]["address"] + ("/128" if ":" in objects["service"]["address"] else "/32")])
        return {"owner_id": owner["owner_id"], "node_id": owner["container_id"], "files": pins, **objects}, statuses, images

    def bind_locked(self):
        require(self.bound is None)
        self.deadline = time.monotonic() + 120
        before, statuses, images = self._capture()
        verifier = self.deployment.support("image_identity")
        def run(args, *, timeout, check=False):
            require(args[:3] == ["docker", "exec", "guardrails-evolution"])
            raw = self._command(["docker", "--host", SOCKET, "exec", before["node_id"], *args[3:]], timeout=timeout)
            return 0, raw
        identities = {component: verifier.verify_container(SimpleNamespace(run=run), images[component], statuses[component])
                      for component in ("runtime", "classifier")}
        after, _, _ = self._capture()
        require(after == before)
        self.bound = before
        self.images = identities
        return {"binding": before, "images": identities}

    def sample_locked(self):
        require(self.bound is not None)
        self.deadline = time.monotonic() + 50
        before, _, _ = self._capture()
        require(before == self.bound)
        settings = {"address": before["service"]["address"], "identity": identity(),
            "token_sha256": before["files"]["assembly/private/classifier.token"],
            "ca_sha256": before["files"]["assembly/tls/gr-platform/classifier/ca.crt"]}
        raw = self._kube("exec", before["pods"][self.runtime]["pod_name"], "-c", "runtime", "--",
            "python", "-B", "-c", READY_SCRIPT, canonical(settings).decode(), maximum=4096)
        value = parse(raw.encode(), limit=4096)
        require(set(value) == {"status", "tls", "epoch", "attempts", "identity_sha256", "elapsed_ms"}
                and type(value["status"]) is int and value["status"] == 200
                and value["tls"] in {"TLSv1.2", "TLSv1.3"}
                and value["identity_sha256"] == hashlib.sha256(canonical(identity())).hexdigest()
                and type(value["elapsed_ms"]) in (int, float) and 0 <= value["elapsed_ms"] < 8000
                and type(value["epoch"]) is str and type(value["attempts"]) is int)
        counter = snapshot([(b"x-evolution-classifier-worker-epoch", value["epoch"].encode("ascii")),
            (b"x-evolution-classifier-dispatch-attempts", str(value["attempts"]).encode("ascii"))])
        after, _, _ = self._capture()
        require(after == before)
        return {"binding_sha256": hashlib.sha256(canonical(before)).hexdigest(), "counter": counter,
                "tls": value["tls"], "ready_elapsed_ms": value["elapsed_ms"]}


def observed_attempts(before, after):
    require(type(before) is dict and type(after) is dict and before.get("binding_sha256") == after.get("binding_sha256")
            and re.fullmatch(r"[0-9a-f]{64}", before.get("binding_sha256", "")) is not None)
    return delta(before["counter"], after["counter"])
