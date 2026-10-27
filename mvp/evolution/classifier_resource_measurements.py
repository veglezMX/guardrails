"""Four admitted-request resource observations, not a throughput/SLA benchmark.

The final rollout proof schema is owned by the parent integration. A caller must
provide its verifier; digest references alone never authenticate that provenance.
This family is deliberately not registered or wired into the shared collector.
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

from . import classifier_resource_evidence as resources
from .classifier_dispatch import delta
from .classifier_source_projection import check_deadline, project_sources
from .diagnostics import SourceCatalog, read_catalog
from .registry import EvidenceError

SCHEMA = "evolution-classifier-admitted-resources-v1"
FILENAME = "classifier-resource-observation.json"
MAX_BYTES = 128 * 1024
CASES = {f"OPS-CLASSIFIER-RESOURCES-{tenant.upper()}-{stage.upper()}": (tenant, stage)
         for tenant in ("team-a", "team-b") for stage in ("input", "output")}
PROOFS = {"parent_invocation", "qualification", "prepared", "cold", "credentials"}
PHASES = {"setup", "binding", "governance", "context", "sampling", "audit", "restoration", "record"}
SOURCES = (
    "mvp/evolution/classifier_resource_measurements.py",
    "mvp/evolution/classifier_source_projection.py",
    "mvp/evolution/classifier_resource_evidence.py",
    "mvp/evolution/classifier_dispatch.py",
    "mvp/evolution/evaluator_attempt.py",
    "mvp/evolution/evaluator_governance.py",
    "mvp/evolution/evaluator_cases.py",
    "mvp/tests/evolution/e2e/classifier_resource_sampling_support.py",
    "mvp/tests/evolution/e2e/test_cluster_classifier_resources.py",
    "mvp/tests/evolution/e2e/classifier_resource_support.py",
    "mvp/tests/evolution/e2e/classifier_network_support.py",
    "mvp/tests/evolution/e2e/evaluator_matrix_support.py",
    "mvp/guardrails/evolution/classifier_contract.py",
    "deploy/evolution/image_identity.py",
)


def require(value):
    if not value:
        raise EvidenceError("measurement_schema_invalid")


def sha(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def integer(value, minimum=0, maximum=2**63 - 1):
    require(type(value) is int and minimum <= value <= maximum)
    return value


def uuid(value):
    require(type(value) is str and len(value) == 36)
    try:
        require(str(UUID(value)) == value)
    except ValueError:
        raise EvidenceError("measurement_schema_invalid") from None


def provenance(value):
    """Only a finite reference envelope; the mandatory callback verifies proofs."""
    require(type(value) is dict and set(value) == PROOFS and all(sha(v) for v in value.values()))
    return value


def validate_observations(value):
    """Retain only the frozen readers' finite projections, including failures."""
    require(type(value) is dict and set(value) <= {"image_binding", "before", "ready_before", "ready_after", "after"})
    if not value:
        return value
    require("image_binding" in value)
    images = value["image_binding"]
    require(type(images) is dict and set(images) == {"binding_sha256", "inventory_sha256", "identities", "identities_sha256"}
            and all(sha(images[k]) for k in ("binding_sha256", "inventory_sha256", "identities_sha256")))
    require(type(images["identities"]) is list and len(images["identities"]) == 25)
    # Exact native identity fields exclude command output or private content.
    common = {"container_id", "expected_reference", "manifest_digest", "config_digest", "rootfs_chain_id"}
    for row in images["identities"]:
        require(type(row) is dict and set(row) in (common | {"ready"}, common | {
            "completed", "exit_code", "image_verified_by_container_id", "declaration_sha256"}))
        require(sha(row["container_id"]) and type(row["expected_reference"]) is str
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", row["expected_reference"]) is not None)
        require(all(type(row[k]) is str and re.fullmatch(r"sha256:[a-f0-9]{64}", row[k]) is not None
                    for k in ("manifest_digest", "config_digest", "rootfs_chain_id")))
        if "ready" in row:
            require(row["ready"] is True)
        else:
            require(row["completed"] is True and type(row["exit_code"]) is int and row["exit_code"] == 0
                    and sha(row["image_verified_by_container_id"]) and sha(row["declaration_sha256"]))
    require(digest(sorted(images["identities"], key=lambda row: row["container_id"])) == images["identities_sha256"])
    for name in ("ready_before", "ready_after"):
        if name in value:
            row = value[name]
            require(type(row) is dict and set(row) == {"binding_sha256", "counter", "tls", "ready_elapsed_ms"}
                    and row["binding_sha256"] == images["binding_sha256"]
                    and row["tls"] in {"TLSv1.2", "TLSv1.3"}
                    and type(row["ready_elapsed_ms"]) in (int, float) and 0 <= row["ready_elapsed_ms"] < 8000)
            require(delta(row["counter"], row["counter"]) == 0)
    for name in ("before", "after"):
        if name not in value:
            continue
        sample = value[name]
        require(type(sample) is dict and set(sample) == {"binding_sha256", "inventory", "images_sha256", "kernel"}
                and sample["binding_sha256"] == images["binding_sha256"] and sample["images_sha256"] == images["identities_sha256"])
        inv = sample["inventory"]
        require(type(inv) is dict and set(inv) == {"applications", "totals", "owned_pods", "running_containers",
            "completed_initializers", "system_pods", "system_inventory_sha256", "inventory_sha256"}
            and inv["totals"] == resources.TOTALS and type(inv["applications"]) is list and len(inv["applications"]) == 12
            and inv["owned_pods"] == 12 and inv["running_containers"] == 13 and inv["completed_initializers"] == 12
            and sha(inv["system_inventory_sha256"]) and digest(inv["applications"]) == inv["inventory_sha256"] == images["inventory_sha256"])
        for key in ("owned_pods", "running_containers", "completed_initializers", "system_pods"):
            integer(inv[key], 0, 256)
        for app in inv["applications"]:
            require(type(app) is dict and set(app) == {"namespace", "deployment", "pod_uid", "pod_name", "deployment_uid",
                "replica_uid", "containers", "completed_initializers"})
            require((app["namespace"], app["deployment"]) in set(resources.APPLICATIONS) | resources.PROBES
                    and type(app["pod_name"]) is str and re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", app["pod_name"]) is not None)
            for key in ("pod_uid", "deployment_uid", "replica_uid"):
                uuid(app[key])
            for initialized, group in ((False, "containers"), (True, "completed_initializers")):
                require(type(app[group]) is list and 1 <= len(app[group]) <= 2)
                for container in app[group]:
                    require(type(container) is dict and set(container) == {"name", "container_id", "restarts", "image",
                        "observed_image_id", "resources"} | ({"completed_status_sha256"} if initialized else set())
                        and type(container["name"]) is str and re.fullmatch(r"[a-z0-9-]{1,64}", container["name"]) is not None)
                    integer(container["restarts"])
                    if initialized:
                        require(sha(container["completed_status_sha256"]))
                    alloc = container["resources"]
                    require(type(alloc) is dict and set(alloc) == {"requests", "limits"})
                    for row in alloc.values():
                        require(type(row) is dict and set(row) == {"cpu_milli", "memory_bytes"})
                        integer(row["cpu_milli"], 1, 6000)
                        integer(row["memory_bytes"], 1, 8 * 1024**3)
        require(len({(a["namespace"], a["deployment"]) for a in inv["applications"]}) == 12)
        require(resources.image_closure(inv, images["identities"]) == images["identities_sha256"])
        app = next(a for a in inv["applications"] if a["deployment"] == "classifier")
        kernel = sample["kernel"]
        require(type(kernel) is dict and set(kernel) == {"container_id", "pid", "start_ticks", "ancestors"}
                and kernel["container_id"] == app["containers"][0]["container_id"]
                and type(kernel["ancestors"]) is list and len(kernel["ancestors"]) == 5)
        rows = []
        for row in kernel["ancestors"]:
            require(type(row) is dict and set(row) == {"path", "device", "inode", "cpu_quota_us", "cpu_period_us",
                "memory_limit_bytes", "memory_current_bytes", "memory_peak_bytes", "cpu"})
            require(type(row["cpu"]) is dict and set(row["cpu"]) == {"usage_usec", "user_usec", "system_usec",
                "nr_periods", "nr_throttled", "throttled_usec"})
            for k in ("device", "inode", "cpu_period_us", "memory_current_bytes", "memory_peak_bytes"):
                integer(row[k])
            for k in ("cpu_quota_us", "memory_limit_bytes"):
                if row[k] is not None:
                    integer(row[k], 1)
            for n in row["cpu"].values():
                integer(n)
            rows.append({k: row[k] for k in ("path", "device", "inode")} | {
                "cpu.max": f"{'max' if row['cpu_quota_us'] is None else row['cpu_quota_us']} {row['cpu_period_us']}\n",
                "memory.max": f"{'max' if row['memory_limit_bytes'] is None else row['memory_limit_bytes']}\n",
                "memory.current": f"{row['memory_current_bytes']}\n", "memory.peak": f"{row['memory_peak_bytes']}\n",
                "cpu.stat": "".join(f"{k} {v}\n" for k, v in row["cpu"].items())})
        raw = {k: kernel[k] for k in ("container_id", "pid", "start_ticks")} | {
            "process_cgroup": "0::" + rows[-1]["path"] + "\n", "ancestors": rows}
        require(resources.kernel_sample(raw, cid=kernel["container_id"], pod_uid=app["pod_uid"]) == kernel)
    require(len(canonical(value)) <= MAX_BYTES - 8192)
    return value


def validate_payload(value, case_id):
    require(case_id in CASES and type(value) is dict and set(value) == {
        "schema_version", "tenant", "stage", "operation", "identity_sha256", "provenance",
        "binding_sha256", "images_sha256", "inventory_sha256", "system_inventory_sha256",
        "before_capture_sha256", "after_capture_sha256", "counter_before", "counter_after",
        "usage", "inventory", "decision", "governance", "elapsed_ms", "sample_elapsed_ms", "observations"})
    require(value["schema_version"] == SCHEMA and (value["tenant"], value["stage"]) == CASES[case_id])
    uuid(value["operation"])
    require(value["identity_sha256"] == digest(identity()))
    provenance(value["provenance"])
    observations = validate_observations(value["observations"])
    require(set(observations) == {"image_binding", "before", "ready_before", "ready_after", "after"})
    require(digest(observations["before"]) == value["before_capture_sha256"]
            and digest(observations["after"]) == value["after_capture_sha256"]
            and observations["before"]["inventory"] == observations["after"]["inventory"]
            and observations["before"]["binding_sha256"] == value["binding_sha256"]
            and observations["before"]["images_sha256"] == value["images_sha256"]
            and observations["before"]["inventory"]["inventory_sha256"] == value["inventory_sha256"]
            and observations["before"]["inventory"]["system_inventory_sha256"] == value["system_inventory_sha256"]
            and observations["ready_before"]["counter"] == value["counter_before"]
            and observations["ready_after"]["counter"] == value["counter_after"]
            and resources.compare_samples(observations["before"]["kernel"], observations["after"]["kernel"]) == value["usage"])
    for key in ("binding_sha256", "images_sha256", "inventory_sha256", "system_inventory_sha256",
                "before_capture_sha256", "after_capture_sha256"):
        require(sha(value[key]))
    require(delta(value["counter_before"], value["counter_after"]) == 1)
    usage = value["usage"]
    require(type(usage) is dict and set(usage) == {"classifier_cpu_usage_delta_usec",
        "classifier_throttled_delta_usec", "classifier_memory_peak_bytes", "node_memory_peak_bytes"})
    integer(usage["classifier_cpu_usage_delta_usec"], 1)
    integer(usage["classifier_throttled_delta_usec"])
    integer(usage["classifier_memory_peak_bytes"], 1, 3 * 1024**3)
    integer(usage["node_memory_peak_bytes"], usage["classifier_memory_peak_bytes"], 8 * 1024**3)
    require(value["inventory"] == {"owned_pods": 12, "running_containers": 13,
        "completed_initializers": 12, "cpu_limit_millicores": 5950, "memory_limit_mib": 7936,
        "node_cpu_limit": 6, "node_memory_limit_bytes": 8 * 1024**3,
        "classifier_cpu_limit": 2, "classifier_memory_limit_bytes": 3 * 1024**3})
    require(all(type(v) is int for v in value["inventory"].values()))
    decision = value["decision"]
    require(type(decision) is dict and set(decision) == {"request_id", "decision_id", "audit_id",
        "policy_digest", "binding_revision", "snapshot_generation", "evaluator_revision", "action"})
    for key in ("request_id", "decision_id", "audit_id"):
        uuid(decision[key])
    require(sha(decision["policy_digest"]) and decision["action"] == "allow"
            and decision["evaluator_revision"] == identity()["evaluator_revision"])
    integer(decision["binding_revision"], 1)
    integer(decision["snapshot_generation"], 1)
    governance = value["governance"]
    require(type(governance) is dict and set(governance) == {"intent_sha256", "release_sha256",
        "restoration_sha256", "completion_sha256"} and all(sha(v) for v in governance.values()))
    elapsed = value["elapsed_ms"]
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 < elapsed <= 900000)
    elapsed = value["sample_elapsed_ms"]
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= 60000)
    require(len(canonical(value)) <= MAX_BYTES)
    return value


def validate_sources(*, root, sources):
    require(root is not None and type(sources) is dict and set(SOURCES) <= set(sources))
    catalog = SourceCatalog(root, sources)
    for name in SOURCES:
        catalog.load(catalog.path(name))


def validate(value, case_id, *, root, sources, complete=False, verify_provenance=None):
    # An incomplete prefix is never accepted as a successful resource sample.
    require(type(complete) is bool)
    validate_sources(root=root, sources=sources)
    if type(value) is dict and value.get("status") == "failed":
        require(not complete and case_id in CASES and set(value) == {"schema_version", "status", "tenant", "stage",
            "operation", "failed_phase", "exception_type", "observations"}
            and value["schema_version"] == SCHEMA and (value["tenant"], value["stage"]) == CASES[case_id]
            and value["failed_phase"] in PHASES and value["exception_type"] in {
                "AssertionError", "EvidenceError", "ResourceObservationError", "ClassifierObservationError", "TimeoutError", "OtherError"})
        if value["operation"] is not None:
            uuid(value["operation"])
        validate_observations(value["observations"])
        return value
    validate_payload(value, case_id)
    if complete:
        require(callable(verify_provenance))
        before, source_before = canonical(value), canonical(sources)
        proof = verify_provenance(phase="consume", case_id=case_id, operation=value["operation"],
            binding_sha256=value["binding_sha256"], observation=copy.deepcopy(value), sources=dict(sources))
        require(provenance(proof) == value["provenance"] and canonical(value) == before and canonical(sources) == source_before)
        validate_sources(root=root, sources=sources)
    return value


def evidence_directory(case_id):
    raw = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    require(type(raw) is str and bool(raw) and case_id in CASES)
    path = Path(raw)
    require(path.is_absolute() and path.resolve() == path and path.name == case_id
            and path.parent.name == "measurements")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    return path, info


def gate_sources(root, case_id, *, deadline):
    check_deadline(deadline)
    path, _ = evidence_directory(case_id)
    sources = project_sources(root, read_catalog(path.parents[1] / (case_id + ".sources.json"), root), deadline=deadline)
    validate_sources(root=root, sources=sources)
    check_deadline(deadline)
    return sources


def record(value, case_id, *, root, sources):
    # Retention happens before parent completion exists. This validates original
    # observations only; successful collection separately requires provenance.
    validate(value, case_id, root=root, sources=sources, complete=False)
    path, before = evidence_directory(case_id)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        def stamp(info):
            return info.st_dev, info.st_ino, info.st_mode, info.st_uid
        current_path, current_info = evidence_directory(case_id)
        require(stamp(os.fstat(fd)) == stamp(before) == stamp(current_info) and current_path == path)
        output = os.open(FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(output, "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
        require(stamp(evidence_directory(case_id)[1]) == stamp(before))
    finally:
        os.close(fd)
    return value
