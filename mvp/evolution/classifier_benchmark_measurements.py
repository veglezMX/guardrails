"""Retained benchmark invocation and quality evidence, without model execution.

The parent supplies an independent raw receipt hash. That provenance boundary
must never be synthesized by a test from the files that the test is validating.
"""

from __future__ import annotations

import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

from . import classifier_benchmark_evidence as evidence
from .diagnostics import SourceCatalog, read_catalog
from .registry import EvidenceError

CASE = "INT-CLASSIFIER-FROZEN-HELDOUT-ENGLISH-QUALITY-AND-SPANISH-REPORT"
CASES = frozenset({CASE})
FILENAME = "classifier-benchmark-observation.json"
SCHEMA = "evolution-classifier-benchmark-observation-v1"
BINDING_SCHEMA = "evolution-classifier-benchmark-binding-v1"
MAX_BYTES = 16384
SOURCES = (
    "mvp/evolution/classifier_benchmark_measurements.py",
    "mvp/evolution/classifier_benchmark_evidence.py",
    "mvp/tests/evolution/integration/test_classifier_benchmark.py",
    *evidence.SOURCE_FILES,
)
RECEIPT_ENV = "EVOLUTION_CLASSIFIER_BENCHMARK_RECEIPT"
PIN_ENV = "EVOLUTION_CLASSIFIER_BENCHMARK_RECEIPT_SHA256"


def _require(condition):
    if not condition:
        raise EvidenceError("classifier_benchmark_binding_invalid")


def _hash(value):
    return type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None


def _path(value):
    _require(type(value) is str and 0 < len(value) <= 4096)
    path = Path(value)
    _require(path.is_absolute() and str(path) == value and path.resolve() == path)
    return path


def _reference(value):
    _require(type(value) is dict and set(value) == {"path", "sha256"} and _hash(value["sha256"]))
    path = _path(value["path"])
    raw = evidence._read(path, private=True)
    _require(evidence._sha(raw) == value["sha256"])
    return path, raw, evidence._json(raw, maximum=MAX_BYTES)


def _utc(value):
    _require(type(value) is str and len(value) <= 40)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise EvidenceError("classifier_benchmark_binding_invalid") from None
    _require(parsed.tzinfo == UTC)
    return parsed


def validate_sources(root, sources):
    _require(root is not None and type(sources) is dict)
    catalog = SourceCatalog(root, sources)
    for name in SOURCES:
        _require(name in sources)
        catalog.load(catalog.path(name))


def verify_binding(receipt_path, receipt_sha256, *, root, sources):
    """Check parent-authenticated invocation plus all 600 original report rows."""
    validate_sources(root, sources)
    try:
        reference = {"path": str(receipt_path), "sha256": receipt_sha256}
        path, receipt_raw, binding = _reference(reference)
        _require(set(binding) == {"schema_version", "benchmark_directory", "evidence_sha256",
                                 "scoring_sources", "corpus_sha256", "invocation"}
                 and binding["schema_version"] == BINDING_SCHEMA
                 and binding["corpus_sha256"] == evidence.CORPUS_SHA256
                 and binding["scoring_sources"] == {name: sources[name] for name in evidence.SOURCE_FILES})
        output = _path(binding["benchmark_directory"])
        invocation = binding["invocation"]
        _require(type(invocation) is dict and set(invocation) == {
            "started", "result", "command_sha256", "launcher_sha256"}
            and _hash(invocation["command_sha256"]) and _hash(invocation["launcher_sha256"]))
        started_path, started_raw, started = _reference(invocation["started"])
        result_path, result_raw, result = _reference(invocation["result"])
        _require(started_path.name == "started.json" and result_path.name == "result.json"
                 and started_path.parent == result_path.parent
                 and not os.path.lexists(result_path.parent / "failed.json"))
        _require(set(started) == {"automatic_retry", "command_sha256", "health", "health_sha256",
                                 "maximum_seconds", "model_output", "started_at", "warmup_requests"}
                 and started["automatic_retry"] is False
                 and type(started["warmup_requests"]) is int and started["warmup_requests"] == 0
                 and type(started["maximum_seconds"]) is int and started["maximum_seconds"] == 3600
                 and started["command_sha256"] == invocation["command_sha256"]
                 and started["model_output"] == str(output) and _hash(started["health_sha256"]))
        _path(started["health"])
        invocation_time = _utc(started["started_at"])
        _require(set(result) == {"cost", "elapsed_seconds", "exit_code", "output", "phase_gate_claimed"}
                 and type(result["exit_code"]) is int and result["exit_code"] == 0
                 and result["output"] == str(output) and result["phase_gate_claimed"] is False
                 and evidence._number(result["elapsed_seconds"]) and 0 < result["elapsed_seconds"] <= 3600
                 and result["cost"] == {"tokens": "unavailable", "tool_credits": "unavailable"})
        verified = evidence.verify_benchmark(output, root=root, pins=binding["evidence_sha256"])
        report_raw = evidence._read(output / "result.json", private=True)
        report = evidence._json(report_raw)
        _require(evidence._sha(report_raw) == binding["evidence_sha256"]["result.json"]
                 and 0 <= (_utc(report["created_at"]) - invocation_time).total_seconds() <= result["elapsed_seconds"])
        # The hashes in this bounded attachment are revalidated by collect().
        value = {"schema_version": SCHEMA, "status": "verified", "binding": reference,
                 "report_sha256": binding["evidence_sha256"], "benchmark": verified,
                 "cpu": report["cpu"], "cost": report["cost"],
                 "invocation": {"started_sha256": invocation["started"]["sha256"],
                                "result_sha256": invocation["result"]["sha256"],
                                "command_sha256": invocation["command_sha256"],
                                "launcher_sha256": invocation["launcher_sha256"],
                                "exit_code": 0, "elapsed_seconds": result["elapsed_seconds"]}}
        _require(len(evidence._canonical(value) + b"\n") <= MAX_BYTES)
        _require(evidence._read(path, private=True) == receipt_raw
                 and evidence._read(started_path, private=True) == started_raw
                 and evidence._read(result_path, private=True) == result_raw
                 and not os.path.lexists(result_path.parent / "failed.json"))
        validate_sources(root, sources)
        return value
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise EvidenceError("classifier_benchmark_binding_invalid") from None


def validate(value, *, case_id, root, sources, complete=False):
    """A saved summary is insufficient: repeat the complete evidence check."""
    _require(type(case_id) is str and case_id in CASES and type(complete) is bool
             and type(value) is dict and set(value) == {
                 "schema_version", "status", "binding", "report_sha256", "benchmark", "cpu", "cost", "invocation"}
             and type(value["binding"]) is dict and set(value["binding"]) == {"path", "sha256"})
    expected = verify_binding(value["binding"]["path"], value["binding"]["sha256"], root=root, sources=sources)
    _require(evidence._canonical(value) == evidence._canonical(expected))
    return value


def _directory():
    value = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    if value is None:
        return None
    path = _path(value)
    before = path.lstat()
    _require(path.name == CASE and path.parent.name == "measurements"
             and stat.S_ISDIR(before.st_mode) and before.st_uid == os.getuid()
             and stat.S_IMODE(before.st_mode) == 0o700)
    return path, before


def gate_sources(root):
    checked = _directory()
    if checked is None:
        sources = {name: evidence._sha(evidence._read(Path(root) / name, private=False)) for name in SOURCES}
    else:
        sources = read_catalog(checked[0].parents[1] / (CASE + ".sources.json"), root)
    validate_sources(root, sources)
    return sources


def record(value, *, root, sources):
    validate(value, case_id=CASE, root=root, sources=sources, complete=True)
    checked = _directory()
    if checked is None:
        return value
    directory, before = checked
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _require(evidence._identity(os.fstat(fd)) == evidence._identity(before)
                 and directory.resolve() == directory
                 and evidence._identity(directory.lstat()) == evidence._identity(before))
        leaf = os.open(FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(leaf, "wb") as stream:
            stream.write(evidence._canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
    finally:
        os.close(fd)
    return value
