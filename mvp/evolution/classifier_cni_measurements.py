"""Retained CNI observations; acceptance requires a separate final-proof verifier.

Host, external and metadata denials demonstrate composed node/CNI isolation.
This family is unregistered. Neither an observed group nor digest references
alone authenticate qualification, deployment or the current node boundary.
"""
from __future__ import annotations

import copy
import hashlib
import math
import os
import re
import stat
from pathlib import Path
from uuid import UUID

from guardrails.evolution.classifier_contract import canonical, identity

from . import classifier_cni_evidence as cni
from .classifier_resource_measurements import validate_observations
from .classifier_source_projection import check_deadline, project_sources
from .diagnostics import SourceCatalog, read_catalog
from .registry import EvidenceError

SCHEMA = "evolution-classifier-cni-observation-v1"
CASE = "OPS-CLASSIFIER-CNI-ORIGINAL-GROUP"
FILENAME = "classifier-cni-observation.json"
INTENT = "classifier-cni-intent.json"
AUTHORIZATION = "classifier-cni-authorization.json"
MAX_BYTES = 2 * 1024 * 1024
PROOFS = {"parent_invocation", "qualification", "prepared", "cold", "credentials", "node_boundary"}
PHASES = {"setup", "binding", "provenance_before", "group", "provenance_after", "closure", "close", "record"}
GROUP_PHASES = {"setup", "binding", "ready_before", "dns_before", "positive_before", "denials",
                "positive_after", "dns_after", "ready_after", "closure", "complete", "cleanup"}
GROUP_FIELDS = {"schema_version", "status", "stage", "elapsed_ms", "binding", "closure",
                "readiness_binding", "image_binding", "host_descriptor", "rows", "controls", "proofs", "cleanup"}
CONTROL_FIELDS = {"ready_before", "ready_after", "dns", "sink_before", "sink_positive", "sink_after",
                  "sink_final", "host_before", "host_positive", "host_after", "host_final"}
SOURCES = (
    "mvp/evolution/classifier_cni_measurements.py",
    "mvp/evolution/classifier_source_projection.py", "mvp/evolution/classifier_cni_evidence.py",
    "mvp/evolution/classifier_dispatch.py", "mvp/evolution/classifier_resource_evidence.py",
    "mvp/evolution/classifier_resource_measurements.py",
    "mvp/tests/evolution/e2e/classifier_cni_acceptance_support.py",
    "mvp/tests/evolution/e2e/test_cluster_classifier_cni.py",
    "mvp/tests/evolution/e2e/classifier_cni_group.py", "mvp/tests/evolution/e2e/classifier_cni_bindings.py",
    "mvp/tests/evolution/e2e/classifier_cni_controls.py", "mvp/tests/evolution/e2e/classifier_cni_support.py",
    "mvp/tests/evolution/e2e/classifier_network_support.py", "mvp/tests/evolution/e2e/classifier_resource_support.py",
    "mvp/tests/evolution/e2e/evaluator_matrix_support.py", "mvp/tests/evolution/e2e/cluster_support.py",
    "mvp/guardrails/evolution/classifier_contract.py", "deploy/evolution/image_identity.py",
)


def require(condition):
    if not condition:
        raise EvidenceError("measurement_schema_invalid")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def operation_id(value):
    require(type(value) is str and len(value) == 36)
    try:
        require(str(UUID(value)) == value)
    except ValueError:
        raise EvidenceError("measurement_schema_invalid") from None


def references(value):
    require(type(value) is dict and set(value) == PROOFS and all(sha(v) for v in value.values()))
    return value


def intent(operation, sources):
    operation_id(operation)
    return {"schema_version": SCHEMA, "case_id": CASE, "operation": operation,
        "sources_sha256": digest(sources), "status": "intended", "group_seconds": 120,
        "identity_sha256": digest(identity())}


def authorization(operation, intent_sha256, provenance, binding, image_binding):
    return {"schema_version": SCHEMA, "case_id": CASE, "operation": operation,
        "intent_sha256": intent_sha256, "provenance": provenance, "binding": binding, "image_binding": image_binding}


def file_digest(value):
    return hashlib.sha256(canonical(value) + b"\n").hexdigest()


def bounded_metadata(value):
    """Public API/kernel projections only; cap recursion before serialization."""
    count = 0
    def visit(item, depth):
        nonlocal count
        count += 1
        require(count <= 32768 and depth <= 24)
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            require(0 <= item <= 2**63 - 1)
        elif type(item) is float:
            require(math.isfinite(item) and 0 <= item <= 2**63 - 1)
        elif type(item) is str:
            require(len(item) <= 4096)
        elif type(item) in (dict, list):
            require(len(item) <= 512)
            if type(item) is dict:
                require(all(type(k) is str and len(k) <= 256 for k in item))
                items = item.values()
            else:
                items = item
            for child in items:
                visit(child, depth + 1)
        else:
            require(False)
    visit(value, 0)
    require(len(canonical(value)) <= MAX_BYTES)
    return value


def partial_group(value):
    """Retain the entire producer prefix, never treat it as completed evidence."""
    if value is None:
        return None
    bounded_metadata(value)
    require(type(value) is dict and set(value) == GROUP_FIELDS
            and value["schema_version"] == "evolution-classifier-cni-group-v1"
            and value["status"] in {"failed", "observed"} and value["stage"] in GROUP_PHASES
            and value["cleanup"] in {"pending", "closed", "failed"}
            and type(value["elapsed_ms"]) in (int, float)
            and type(value["rows"]) is list and len(value["rows"]) <= 20
            and type(value["controls"]) is dict and set(value["controls"]) <= CONTROL_FIELDS)
    for row in value["rows"]:
        require(type(row) is dict and set(row) == {"job_id", "result"})
    return value


def group_proofs(value):
    """Recompute both runtime proofs from one actual group, not copied counters."""
    partial_group(value)
    require(value is not None and value["status"] == "observed" and value["stage"] == "complete"
            and value["cleanup"] == "closed" and 0 < value["elapsed_ms"] <= 120000)
    bound = cni.bindings(value["binding"])
    ready = value["readiness_binding"]
    require(type(ready) is dict and set(ready) == {"owner_id", "node_id", "api_server", "files", "service", "pods"}
            and ready["owner_id"] == bound["owner_id"] and ready["node_id"] == bound["node_id"]
            and digest(ready) == bound["readiness_binding_sha256"])
    require(type(ready["api_server"]) is str and re.fullmatch(r"https://127\.0\.0\.1:[1-9][0-9]{3,4}", ready["api_server"]) is not None
            and 1024 <= int(ready["api_server"].rsplit(":", 1)[1]) <= 65535
            and type(ready["files"]) is dict and 1 <= len(ready["files"]) <= 512
            and all(type(k) is str and not Path(k).is_absolute() and ".." not in Path(k).parts and sha(v) for k, v in ready["files"].items())
            and type(ready["service"]) is dict and set(ready["service"]) == {"uid", "address", "endpoint_slice_uid"}
            and ready["service"]["address"] == bound["addresses"]["classifier_service"]
            and type(ready["pods"]) is dict and set(ready["pods"]) == {"runtime-1", "classifier"})
    for key in ("uid", "endpoint_slice_uid"):
        operation_id(ready["service"][key])
    for name, pod in ready["pods"].items():
        peer = bound["peers"][name]
        require(type(pod) is dict and set(pod) == {"pod_name", "pod_uid", "deployment_uid", "replica_uid",
            "container_id", "image", "restarts", "pod_ip", "spec_sha256"}
            and all(pod[key] == peer[key] for key in ("pod_name", "pod_uid", "pod_ip"))
            and pod["container_id"] == "containerd://" + peer["container_id"] and sha(pod["spec_sha256"])
            and type(pod["image"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", pod["image"]) is not None)
        for key in ("pod_uid", "deployment_uid", "replica_uid"):
            operation_id(pod[key])
        cni.integer(pod["restarts"])
    images = value["image_binding"]
    validate_observations({"image_binding": images})
    require(images["binding_sha256"] == digest(ready))
    running = [row for row in images["identities"] if "ready" in row]
    require(len(running) == 13 and len({row["container_id"] for row in images["identities"]}) == 25
            and {row["container_id"] for row in bound["peers"].values()} <= {row["container_id"] for row in running})
    closure = value["closure"]
    require(type(closure) is dict and set(closure) == {"inventory_sha256", "native_images_sha256", "processes", "network", "sink_ca_sha256"}
            and closure["inventory_sha256"] == images["inventory_sha256"]
            and closure["native_images_sha256"] == images["identities_sha256"] and sha(closure["sink_ca_sha256"])
            and type(closure["processes"]) is dict and set(closure["processes"]) == set(cni.ORIGINS))
    for name, process in closure["processes"].items():
        require(type(process) is dict and set(process) == {"pid", "process_start_ticks", "netns_inode"})
        cni.integer(process["pid"], 1, 2**31 - 1)
        require(all(process[k] == bound["peers"][name][k] for k in ("process_start_ticks", "netns_inode")))
    require(len({p["netns_inode"] for p in closure["processes"].values()}) == 6)
    network = closure["network"]
    require(type(network) is dict and set(network) == {"services", "network_policies"}
            and type(network["services"]) is dict and set(network["services"]) == cni.ADDRESS_NAMES
            and type(network["network_policies"]) is list and 1 <= len(network["network_policies"]) <= 512)
    # Full Service/EndpointSlice/NetworkPolicy bytes are retained. The mandatory
    # parent verifier checks them against the current approved deployment.
    for name, service in network["services"].items():
        require(type(service) is dict and set(service) == ({"uid", "spec"} if name in {"api_service", "dns_service"}
            else {"uid", "spec", "endpoint_slice_uid", "endpoint_ports", "pod_uid", "pod_ip"})
            and type(service["spec"]) is dict and service["spec"].get("clusterIP") == bound["addresses"][name])
    service = network["services"]["classifier_service"]
    require(service["uid"] == ready["service"]["uid"] and service["endpoint_slice_uid"] == ready["service"]["endpoint_slice_uid"]
            and service["pod_uid"] == bound["peers"]["classifier"]["pod_uid"] and service["pod_ip"] == bound["peers"]["classifier"]["pod_ip"])
    for policy in network["network_policies"]:
        require(type(policy) is dict and set(policy) == {"namespace", "name", "uid", "spec"} and type(policy["spec"]) is dict)
    host = value["host_descriptor"]
    require(type(host) is dict and set(host) == {"process", "address", "port", "device", "inode"}
            and digest(host) == bound["host_sink"]["identity_sha256"]
            and host["address"] == bound["host_sink"]["address"] and host["port"] == bound["host_sink"]["port"]
            and type(host["process"]) is dict and set(host["process"]) == {"pid", "start_ticks", "netns"})
    for name in ("device", "inode"):
        cni.integer(host[name], 1)
    for name in ("pid", "start_ticks"):
        cni.integer(host["process"][name], 1)
    require(type(host["process"]["netns"]) is str and re.fullmatch(r"net:\[[1-9][0-9]*\]", host["process"]["netns"]) is not None)
    controls = value["controls"]
    require(set(controls) == CONTROL_FIELDS)
    for position in ("ready_before", "ready_after"):
        require(type(controls[position]) is dict and set(controls[position]) == {"runtime-1", "runtime-2"})
    for position in ("sink_before", "sink_positive", "sink_after", "sink_final"):
        row = controls[position]
        require(type(row) is dict and set(row) == {"counts", "tls"} and row["tls"] in {"TLSv1.2", "TLSv1.3"}
                and type(row["counts"]) is dict and set(row["counts"]) == {"8443", "9443", "8445"})
        for count in row["counts"].values():
            cni.integer(count)
    require(controls["sink_positive"]["counts"] == {**controls["sink_before"]["counts"], "8443": controls["sink_before"]["counts"]["8443"] + 1}
            and controls["sink_after"]["counts"] == controls["sink_positive"]["counts"]
            and controls["sink_final"]["counts"] == {**controls["sink_after"]["counts"], "8443": controls["sink_after"]["counts"]["8443"] + 1})
    labels = ("before_positive", "after_positive", "after_denials", "after_final_positive")
    sink = {"identity_sha256": digest(bound["peers"]["sink"]), **dict(zip(labels,
        (controls[k]["counts"]["8443"] for k in ("sink_before", "sink_positive", "sink_after", "sink_final")), strict=True))}
    host = {"identity_sha256": digest(host), **dict(zip(labels,
        (controls[k] for k in ("host_before", "host_positive", "host_after", "host_final")), strict=True))}
    proofs = {runtime: cni.validate_group(bound, value["rows"], ready_before=controls["ready_before"][runtime],
        ready_after=controls["ready_after"][runtime], owned_sink=sink, host_sink=host, dns_controls=controls["dns"])
        for runtime in ("runtime-1", "runtime-2")}
    require(type(value["proofs"]) is dict and value["proofs"] == proofs
            and all(type(v) in (str, int) for proof in value["proofs"].values() for v in proof.values()))
    return proofs


def validate_payload(value):
    bounded_metadata(value)
    require(type(value) is dict and set(value) == {"schema_version", "case_id", "operation", "status", "phase",
        "exception_type", "elapsed_ms", "identity_sha256", "sources_sha256", "intent_sha256", "authorization_sha256", "provenance",
        "group", "group_sha256", "isolation_scope"} and value["schema_version"] == SCHEMA and value["case_id"] == CASE
        and value["status"] in {"failed", "observed"} and value["phase"] in PHASES
        and value["exception_type"] in {None, "AssertionError", "EvidenceError", "CniObservationError",
            "ClassifierObservationError", "TimeoutError", "OtherError"}
        and value["isolation_scope"] == "node_and_cni_composed_host_external_metadata"
        and value["identity_sha256"] == digest(identity())
        and sha(value["sources_sha256"]) and sha(value["intent_sha256"])
        and type(value["elapsed_ms"]) in (int, float))
    operation_id(value["operation"])
    require(value["authorization_sha256"] is None or sha(value["authorization_sha256"]))
    partial_group(value["group"])
    require(value["group_sha256"] == (digest(value["group"]) if value["group"] is not None else None))
    if value["provenance"] is not None:
        references(value["provenance"])
    if value["status"] == "observed":
        require(value["phase"] == "record" and value["exception_type"] is None and 0 < value["elapsed_ms"] <= 600000)
        require(sha(value["authorization_sha256"]))
        references(value["provenance"])
        group_proofs(value["group"])
    else:
        require(value["exception_type"] is not None)
    return value


def validate_sources(*, root, sources):
    require(root is not None and type(sources) is dict and set(SOURCES) <= set(sources))
    catalog = SourceCatalog(root, sources)
    for name in SOURCES:
        catalog.load(catalog.path(name))


def validate(value, case_id, *, root, sources, complete=False, verify_provenance=None):
    """Collector entry: successful attachment always requires the real verifier."""
    require(case_id == CASE and type(complete) is bool)
    validate_sources(root=root, sources=sources)
    validate_payload(value)
    require(value["sources_sha256"] == digest(sources))
    require(value["intent_sha256"] == file_digest(intent(value["operation"], sources)))
    if complete:
        require(value["status"] == "observed" and callable(verify_provenance))
        require(value["authorization_sha256"] == file_digest(authorization(value["operation"], value["intent_sha256"],
            value["provenance"], value["group"]["readiness_binding"], value["group"]["image_binding"])))
        before, source_before = canonical(value), canonical(sources)
        actual = verify_provenance(phase="consume", binding=copy.deepcopy(value["group"]["readiness_binding"]),
            group=copy.deepcopy(value["group"]), sources=dict(sources), operation=value["operation"])
        require(references(actual) == value["provenance"])
        require(canonical(value) == before and canonical(sources) == source_before)
        validate_sources(root=root, sources=sources)
    return value


def evidence_directory():
    raw = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    require(type(raw) is str and bool(raw))
    path = Path(raw)
    require(path.is_absolute() and path.resolve() == path and path.name == CASE and path.parent.name == "measurements")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    return path, info


def gate_sources(root, *, deadline):
    check_deadline(deadline)
    path, _ = evidence_directory()
    sources = project_sources(root, read_catalog(path.parents[1] / (CASE + ".sources.json"), root), deadline=deadline)
    validate_sources(root=root, sources=sources)
    check_deadline(deadline)
    return sources


class EvidenceWriter:
    """Hold the worker's exclusive durable directory through intent and result."""
    def __init__(self):
        self.fd = None
        self.path, self.before = evidence_directory()
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            self.check()
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def check(self):
        path, info = evidence_directory()
        def stamp(row):
            return row.st_dev, row.st_ino, row.st_mode, row.st_uid
        require(self.fd is not None and path == self.path and stamp(info) == stamp(self.before) == stamp(os.fstat(self.fd)))

    def write(self, name, value):
        require(name in {INTENT, AUTHORIZATION, FILENAME})
        bounded_metadata(value)
        self.check()
        raw = canonical(value) + b"\n"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self.fd)
        self.check()
        return hashlib.sha256(raw).hexdigest()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
