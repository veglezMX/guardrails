"""Authenticated outer TLS evidence; the original probe remains unchanged.

The parent supplies independent authorization and, at consumption only, actual
completion. No standalone fallback or digest-only/default proof is accepted.
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

from . import classifier_operational_measurements as component
from .classifier_source_projection import check_deadline, project_sources
from .diagnostics import SourceCatalog, read_catalog
from .registry import EvidenceError

SCHEMA = "evolution-classifier-authenticated-tls-v1"
FILENAME = "classifier-tls-observation.json"
INTENT = "classifier-tls-intent.json"
AUTHORIZATION = "classifier-tls-authorization.json"
MAX_BYTES = 128 * 1024
CASES = dict(component.CASES)
PROOFS = {"parent_invocation", "qualification", "prepared", "cold", "credentials", "transport"}
PHASES = {"setup", "binding", "provenance_before", "probe", "provenance_after", "close", "record"}
FAILURES = {"AssertionError", "EvidenceError", "ClassifierObservationError", "TimeoutError", "OtherError"}
SOURCES = tuple(dict.fromkeys((*component.SOURCES,
    "mvp/evolution/classifier_tls_measurements.py",
    "mvp/evolution/classifier_source_projection.py",
    "mvp/tests/evolution/e2e/classifier_tls_acceptance_support.py",
    "mvp/evolution/diagnostics.py", "mvp/evolution/registry.py",
    "mvp/guardrails/evolution/deadlines.py")))


def finite(value, *, maximum, minimum=0):
    require(type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum)
    return value

def require(value):
    if not value:
        raise EvidenceError("measurement_schema_invalid")

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None

def uuid(value):
    require(type(value) is str and len(value) == 36)
    try:
        require(str(UUID(value)) == value)
    except ValueError:
        raise EvidenceError("measurement_schema_invalid") from None

def bounded(value):
    """Bound arbitrary nesting before serialization; only finite metadata types."""
    count = 0
    def visit(item, depth):
        nonlocal count
        count += 1
        require(depth <= 16 and count <= 8192)
        if item is None or type(item) is bool:
            return
        if type(item) in (int, float):
            finite(item, maximum=2**63 - 1)
        elif type(item) is str:
            require(len(item) <= 4096)
        elif type(item) in (dict, list):
            require(len(item) <= 256)
            if type(item) is dict:
                require(all(type(key) is str and len(key) <= 256 for key in item))
            for child in item.values() if type(item) is dict else item:
                visit(child, depth + 1)
        else:
            require(False)
    visit(value, 0)
    require(len(canonical(value)) + 1 <= MAX_BYTES)

def references(value):
    require(type(value) is dict and set(value) == PROOFS and all(sha(row) for row in value.values()))
    return value

def binding(value, runtime):
    require(type(value) is dict and set(value) == {"owner_id", "node_id", "api_server", "files", "service", "pods"})
    require(type(value["owner_id"]) is str and re.fullmatch(r"[0-9a-f]{32}", value["owner_id"]) is not None and sha(value["node_id"]))
    require(type(value["api_server"]) is str and re.fullmatch(r"https://127\.0\.0\.1:[1-9][0-9]{3,4}", value["api_server"]) is not None
            and 1024 <= int(value["api_server"].rsplit(":", 1)[1]) <= 65535)
    require(type(value["files"]) is dict and 6 <= len(value["files"]) <= 64
            and {"owner.json", "prepared.json", "assembly/access.json", "assembly/values.json",
                 "assembly/private/classifier.token", "assembly/tls/gr-platform/classifier/ca.crt"} <= set(value["files"])
            and all(type(k) is str and not Path(k).is_absolute() and ".." not in Path(k).parts and sha(v) for k, v in value["files"].items()))
    service = value["service"]
    require(type(service) is dict and set(service) == {"uid", "address", "endpoint_slice_uid"})
    uuid(service["uid"]); uuid(service["endpoint_slice_uid"])
    require(type(service["address"]) is str and re.fullmatch(r"[0-9a-f:.]{1,45}", service["address"]) is not None)
    require(type(value["pods"]) is dict and set(value["pods"]) == {runtime, "classifier"})
    for name, pod in value["pods"].items():
        require(type(pod) is dict and set(pod) == {"pod_name", "pod_uid", "deployment_uid", "replica_uid", "container_id", "image", "restarts", "pod_ip", "spec_sha256"})
        require(type(pod["pod_name"]) is str and re.fullmatch(name + r"-[a-z0-9-]+", pod["pod_name"]) is not None)
        for key in ("pod_uid", "deployment_uid", "replica_uid"):
            uuid(pod[key])
        require(type(pod["container_id"]) is str and re.fullmatch(r"containerd://[0-9a-f]{64}", pod["container_id"]) is not None
            and type(pod["image"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}", pod["image"]) is not None
            and type(pod["restarts"]) is int and pod["restarts"] >= 0 and sha(pod["spec_sha256"])
            and type(pod["pod_ip"]) is str and re.fullmatch(r"[0-9a-f:.]{1,45}", pod["pod_ip"]) is not None)
    return value

def file_digest(value):
    return hashlib.sha256(canonical(value) + b"\n").hexdigest()


def validate_sources(root, sources):
    require(root is not None and type(sources) is dict and set(SOURCES) <= set(sources))
    catalog = SourceCatalog(root, sources)
    for name in SOURCES:
        catalog.load(catalog.path(name))


def intent(invocation, case_id, sources):
    uuid(invocation); require(case_id in CASES)
    return {"schema_version": SCHEMA, "case_id": case_id, "invocation": invocation,
        "identity_sha256": digest(identity()), "sources_sha256": digest(sources),
        "status": "intended", "case_budget_ms": 240000, "probe_budget_ms": 90000}


def authorization(value):
    return {"schema_version": SCHEMA, "case_id": value["case_id"], "invocation": value["invocation"],
        "intent_sha256": value["intent_sha256"], "provenance": value["provenance"], "binding": value["binding"]}


def validate_payload(value, case_id, *, root, sources):
    bounded(value)
    require(case_id in CASES and type(value) is dict and set(value) == {
        "schema_version", "case_id", "invocation", "status", "phase", "failure_type", "elapsed_ms",
        "identity_sha256", "sources_sha256", "intent_sha256", "authorization_sha256", "provenance",
        "binding", "component", "cleanup"}
        and value["schema_version"] == SCHEMA and value["case_id"] == case_id
        and value["status"] in {"passed", "failed"} and value["phase"] in PHASES
        and value["failure_type"] in FAILURES | {None} and value["cleanup"] in {"passed", "failed", "not_started"}
        and value["identity_sha256"] == digest(identity()) and value["sources_sha256"] == digest(sources))
    uuid(value["invocation"])
    finite(value["elapsed_ms"], maximum=2**63-1)
    require(value["intent_sha256"] == file_digest(intent(value["invocation"], case_id, sources)))
    if value["binding"] is not None:
        binding(value["binding"], CASES[case_id])
    if value["provenance"] is not None:
        references(value["provenance"])
    if value["authorization_sha256"] is not None:
        require(value["binding"] is not None and value["provenance"] is not None
            and value["authorization_sha256"] == file_digest(authorization(value)))
    passed = value["status"] == "passed"
    observed = value["component"]
    if observed is not None:
        component.validate(observed, case_id, root=root, sources=sources, complete=passed)
        require(observed["binding_sha256"] is None or value["binding"] is not None
                and observed["binding_sha256"] == digest(value["binding"]))
    if passed:
        require(value["phase"] == "record" and value["failure_type"] is None and value["cleanup"] == "passed"
            and observed is not None and value["binding"] is not None and value["provenance"] is not None
            and sha(value["authorization_sha256"]))
        finite(value["elapsed_ms"], maximum=240000, minimum=0.001)
    else:
        require(value["failure_type"] in FAILURES)
    return value


def validate_auxiliaries(value, case_id, artifacts, *, root, sources):
    """Collector must reopen every exact auxiliary file; no wildcard allowance."""
    validate_payload(value, case_id, root=root, sources=sources)
    expected = {INTENT: canonical(intent(value["invocation"], case_id, sources)) + b"\n",
                FILENAME: canonical(value) + b"\n"}
    if value["authorization_sha256"] is not None:
        expected[AUTHORIZATION] = canonical(authorization(value)) + b"\n"
    require(type(artifacts) is dict and set(artifacts) == set(expected)
        and all(type(raw) is bytes and len(raw) <= MAX_BYTES and raw == expected[name]
                for name, raw in artifacts.items()))
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()}


def validate(value, case_id, *, root, sources, complete=False, verify_provenance=None):
    require(type(complete) is bool)
    validate_sources(root, sources); validate_payload(value, case_id, root=root, sources=sources)
    if complete:
        require(value["status"] == "passed" and callable(verify_provenance))
        previous, source_previous = canonical(value), canonical(sources)
        proof = verify_provenance(phase="consume", case_id=case_id, invocation=value["invocation"], operation=None,
            binding=copy.deepcopy(value["binding"]), observation=copy.deepcopy(value), sources=dict(sources))
        require(references(proof) == value["provenance"] and canonical(value) == previous and canonical(sources) == source_previous)
        validate_sources(root, sources)
    return value


def directory(case_id):
    require(case_id in CASES)
    raw = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    require(type(raw) is str and bool(raw))
    path = Path(raw)
    require(path.is_absolute() and path.resolve() == path and path.name == case_id and path.parent.name == "measurements")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    return path, info

def gate_sources(root, case_id, *, deadline):
    check_deadline(deadline)
    path, _ = directory(case_id)
    sources = project_sources(root, read_catalog(path.parents[1] / (case_id + ".sources.json"), root), deadline=deadline)
    validate_sources(root, sources)
    check_deadline(deadline)
    return sources

class EvidenceWriter:
    """Exclusive durable intent/auth/result; held directory and written prefixes."""
    def __init__(self, case_id):
        self.case_id, self.fd, self.written = case_id, None, {}
        self.path, self.before = directory(case_id)
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            self.check()
        except BaseException:
            self.close(); raise

    @staticmethod
    def stamp(row):
        return row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_nlink, row.st_size, row.st_mtime_ns, row.st_ctime_ns

    def check(self):
        path, info = directory(self.case_id)
        stamp = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_uid)
        require(self.fd is not None and path == self.path and stamp(info) == stamp(self.before) == stamp(os.fstat(self.fd)))
        for name, (expected, hashed) in self.written.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
            with os.fdopen(fd, "rb") as stream:
                require(self.stamp(os.fstat(stream.fileno())) == expected)
                raw = stream.read(MAX_BYTES + 1)
                require(self.stamp(os.fstat(stream.fileno())) == expected == self.stamp(os.stat(name, dir_fd=self.fd, follow_symlinks=False))
                    and len(raw) <= MAX_BYTES and hashlib.sha256(raw).hexdigest() == hashed)

    def write(self, name, value):
        require(name in {INTENT, AUTHORIZATION, FILENAME})
        bounded(value); self.check()
        raw = canonical(value) + b"\n"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid() and info.st_nlink == 1)
            self.written[name] = self.stamp(info), hashlib.sha256(raw).hexdigest()
        os.fsync(self.fd); self.check()
        return self.written[name][1]

    def close(self):
        if self.fd is not None:
            os.close(self.fd); self.fd = None
