"""Closed finite observations of original deployed classifier operations.

TLS/auth is the first complete source family. It does not cover the separate
required CNI, cgroup, runtime-audit budget or tenant-isolation acceptance groups.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import stat
from pathlib import Path

from guardrails.evolution.classifier_contract import canonical, identity

from .diagnostics import SourceCatalog, read_catalog
from .registry import EvidenceError

SCHEMA = "evolution-classifier-deployed-tls-v1"
PROBE_SCHEMA = "evolution-classifier-tls-probe-v1"
FILENAME = "classifier-operational-observation.json"
MAX_BYTES = 16384
SCENARIOS = ("ready", "wrong_ca", "wrong_sni", "plaintext", "absent_ready", "wrong_ready", "absent_evaluate", "wrong_evaluate")
CASES = {"SEC-CLASSIFIER-DEPLOYED-TLS-AUTH-" + runtime.upper(): runtime for runtime in ("runtime-1", "runtime-2")}
SOURCES = (
    "mvp/evolution/classifier_operational_measurements.py",
    "mvp/tests/evolution/e2e/classifier_operational_support.py",
    "mvp/tests/evolution/e2e/test_classifier_deployed_operations.py",
    "mvp/tests/evolution/e2e/classifier_network_support.py",
    "mvp/tests/evolution/e2e/evaluator_matrix_support.py",
    "mvp/evolution/classifier_dispatch.py",
    "mvp/guardrails/evolution/classifier_contract.py",
    "services/classifier/app.py",
    "deploy/evolution/image_identity.py",
    "deploy/evolution/bootstrap.py",
)


def require(condition):
    if not condition:
        raise EvidenceError("measurement_schema_invalid")


def sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def number(value, maximum):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum


def validate_sources(*, root, sources):
    require(root is not None and type(sources) is dict and set(SOURCES) <= set(sources))
    catalog = SourceCatalog(root, sources)
    for name in SOURCES:
        catalog.load(catalog.path(name))


def validate_probe(value, *, complete=False):
    require(type(complete) is bool and type(value) is dict
            and set(value) == {"schema_version", "status", "failed_scenario", "rows"}
            and value["schema_version"] == PROBE_SCHEMA and type(value["status"]) is str and value["status"] in {"passed", "failed"}
            and type(value["rows"]) is list and len(value["rows"]) <= len(SCENARIOS))
    rows = value["rows"]
    if value["status"] == "passed":
        require(len(rows) == len(SCENARIOS) and value["failed_scenario"] is None)
    else:
        require(len(rows) < len(SCENARIOS) and type(value["failed_scenario"]) is str
                and value["failed_scenario"] in ({"setup", SCENARIOS[0]} if not rows else {SCENARIOS[len(rows)]}))
    require(not complete or value["status"] == "passed")
    epoch = None
    attempts = None
    for index, row in enumerate(rows):
        require(type(row) is dict and set(row) == {"scenario", "outcome", "http_status", "tls", "verify_code",
            "elapsed_ms", "epoch", "before_attempts", "after_attempts"} and row["scenario"] == SCENARIOS[index]
            and type(row["epoch"]) is str and re.fullmatch(r"[0-9a-f]{32}", row["epoch"]) is not None
            and type(row["before_attempts"]) is int and 0 <= row["before_attempts"] <= 2**63 - 1
            and type(row["after_attempts"]) is int and row["after_attempts"] == row["before_attempts"]
            and number(row["elapsed_ms"], 12000) and type(row["http_status"]) is int
            and type(row["verify_code"]) is int)
        require(epoch is None or row["epoch"] == epoch and row["before_attempts"] == attempts)
        epoch, attempts = row["epoch"], row["after_attempts"]
        if row["scenario"] == "ready":
            require(row["outcome"] == "ready" and row["http_status"] == 200
                    and type(row["tls"]) is str and row["tls"] in {"TLSv1.2", "TLSv1.3"} and row["verify_code"] == 0)
        elif row["scenario"] in {"wrong_ca", "wrong_sni"}:
            require(row["outcome"] == "certificate_rejected" and row["http_status"] == 0 and row["tls"] is None
                    and row["verify_code"] in ({18, 19, 20, 21} if row["scenario"] == "wrong_ca" else {62}))
        elif row["scenario"] == "plaintext":
            require(row["outcome"] == "plaintext_closed" and row["http_status"] == row["verify_code"] == 0 and row["tls"] is None)
        else:
            require(row["outcome"] == "unauthorized" and row["http_status"] == 401
                    and type(row["tls"]) is str and row["tls"] in {"TLSv1.2", "TLSv1.3"} and row["verify_code"] == 0)
    return value


def validate(value, case_id, *, root, sources, complete=False):
    validate_sources(root=root, sources=sources)
    require(type(case_id) is str and case_id in CASES and type(complete) is bool and type(value) is dict
            and set(value) == {"schema_version", "runtime", "status", "binding_sha256", "identity_sha256", "elapsed_ms", "probe"}
            and value["schema_version"] == SCHEMA and value["runtime"] == CASES[case_id]
            and type(value["status"]) is str and value["status"] in {"passed", "failed"} and number(value["elapsed_ms"], 240000)
            and value["identity_sha256"] == hashlib.sha256(canonical(identity())).hexdigest()
            and (value["binding_sha256"] is None or sha(value["binding_sha256"])))
    if value["probe"] is not None:
        validate_probe(value["probe"], complete=complete)
    require(not complete or value["status"] == "passed" and value["probe"] is not None
            and sha(value["binding_sha256"]) and value["elapsed_ms"] <= 90000)
    return value


def gate_sources(root, case_id):
    require(case_id in CASES)
    directory = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    if directory is None:
        sources = {name: hashlib.sha256((Path(root) / name).read_bytes()).hexdigest() for name in SOURCES}
    else:
        path = Path(directory)
        require(path.is_absolute() and path.name == case_id and path.parent.name == "measurements")
        sources = read_catalog(path.parents[1] / (case_id + ".sources.json"), root)
    validate_sources(root=root, sources=sources)
    return sources


def record(value, case_id, *, root, sources):
    validate(value, case_id, root=root, sources=sources, complete=value["status"] == "passed")
    directory = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    if directory is None:
        return value
    path = Path(directory)
    require(path.is_absolute() and path.resolve() == path and path.name == case_id and path.parent.name == "measurements")
    before = path.lstat()
    require(stat.S_ISDIR(before.st_mode) and before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == 0o700)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        held = os.fstat(descriptor)
        require((held.st_dev, held.st_ino, held.st_mode, held.st_uid) == (before.st_dev, before.st_ino, before.st_mode, before.st_uid))
        raw = canonical(value) + b"\n"
        require(len(raw) <= MAX_BYTES)
        output = os.open(FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        with os.fdopen(output, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(descriptor)
        after = path.lstat()
        require(path.resolve() == path and (after.st_dev, after.st_ino, after.st_mode, after.st_uid)
                == (before.st_dev, before.st_ino, before.st_mode, before.st_uid))
    finally:
        os.close(descriptor)
    return value
