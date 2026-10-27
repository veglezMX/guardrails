"""Parent-bound loaded-worker invocation and finite acceptance attachments."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from guardrails.evolution.classifier_contract import identity

from . import classifier_loaded_evidence as evidence
from .diagnostics import SourceCatalog, read_catalog

BINDING_SCHEMA = "evolution-classifier-loaded-binding-v2"
SCHEMA = "evolution-classifier-loaded-observation-v2"
CASE_IDS = {name: "INT-CLASSIFIER-LOADED-" + name.upper().replace("_", "-") for name in evidence.CASES}
CASES = frozenset(CASE_IDS.values())
FILENAME = "classifier-loaded-observation.json"
MAX_BYTES = 16384
SOURCES = (*evidence.SOURCE_FILES, "mvp/evolution/classifier_loaded_measurements.py")
RECEIPT_ENV = "EVOLUTION_CLASSIFIER_LOADED_RECEIPT"
PIN_ENV = "EVOLUTION_CLASSIFIER_LOADED_RECEIPT_SHA256"


def validate_sources(root, sources):
    evidence.require(root is not None and type(sources) is dict, "source_catalog_required")
    catalog = SourceCatalog(root, sources)
    for name in SOURCES:
        evidence.require(name in sources, "source_catalog_required")
        catalog.load(catalog.path(name))


def reference(value):
    evidence.require(type(value) is dict and set(value) == {"path", "sha256"}
                     and type(value["path"]) is str and type(value["sha256"]) is str
                     and evidence.SHA.fullmatch(value["sha256"]), "reference_shape")
    path = Path(value["path"])
    raw = evidence.private_read(path)
    evidence.require(evidence.digest(raw) == value["sha256"], "reference_digest")
    return path, raw, evidence.parse(raw)


def verify_binding(receipt_path, receipt_sha256, *, root, sources):
    validate_sources(root, sources)
    path, raw, binding = reference({"path": str(receipt_path), "sha256": receipt_sha256})
    evidence.require(set(binding) == {"schema_version", "directory", "files", "sources", "identity",
                                     "host_namespace", "invocation"}
                     and binding["schema_version"] == BINDING_SCHEMA
                     and binding["sources"] == {name: sources[name] for name in evidence.SOURCE_FILES}
                     and binding["identity"] == identity(), "binding_shape")
    directory = Path(binding["directory"])
    invocation = binding["invocation"]
    evidence.require(type(invocation) is dict and set(invocation) == {
        "started", "result", "command_sha256", "launcher_sha256"}
        and all(type(invocation[k]) is str and evidence.SHA.fullmatch(invocation[k])
                for k in ("command_sha256", "launcher_sha256")), "invocation_shape")
    start_path, start_raw, start = reference(invocation["started"])
    end_path, end_raw, end = reference(invocation["result"])
    evidence.require(start_path.name == "started.json" and end_path.name == "result.json"
                     and start_path.parent == end_path.parent
                     and not os.path.lexists(end_path.parent / "failed.json"), "invocation_files")
    evidence.require(set(start) == {"automatic_retry", "command_sha256", "maximum_seconds",
                                   "model_output", "host_namespace", "warmup_requests"}
                     and start["automatic_retry"] is False
                     and type(start["warmup_requests"]) is int and start["warmup_requests"] == 0
                     and type(start["maximum_seconds"]) is int and start["maximum_seconds"] == 900
                     and start["command_sha256"] == invocation["command_sha256"]
                     and start["model_output"] == str(directory)
                     and start["host_namespace"] == binding["host_namespace"], "invocation_started")
    evidence.require(set(end) == {"cost", "elapsed_seconds", "exit_code", "output", "phase_gate_claimed"}
                     and type(end["exit_code"]) is int and end["exit_code"] == 0
                     and end["output"] == str(directory) and end["phase_gate_claimed"] is False
                     and evidence.milliseconds(end["elapsed_seconds"], 900)
                     and end["elapsed_seconds"] > 0
                     and end["cost"] == {"tokens": "unavailable", "tool_credits": "unavailable"},
                     "invocation_result")
    verified = evidence.verify(evidence.read_bundle(directory), pins=binding["files"],
                               expected_sources=binding["sources"], expected_identity=identity(),
                               host_namespace=binding["host_namespace"])
    result = {"schema_version": SCHEMA, "status": "verified",
              "binding": {"path": str(path), "sha256": receipt_sha256}, "loaded": verified,
              "invocation": {"started_sha256": invocation["started"]["sha256"],
                             "result_sha256": invocation["result"]["sha256"],
                             "command_sha256": invocation["command_sha256"],
                             "launcher_sha256": invocation["launcher_sha256"],
                             "exit_code": 0, "elapsed_seconds": end["elapsed_seconds"]}}
    evidence.require(len(evidence.canonical(result) + b"\n") <= MAX_BYTES, "observation_size")
    evidence.require(evidence.private_read(path) == raw and evidence.private_read(start_path) == start_raw
                     and evidence.private_read(end_path) == end_raw
                     and not os.path.lexists(end_path.parent / "failed.json"), "invocation_changed")
    validate_sources(root, sources)
    return result


def validate(value, *, case_id, root, sources, complete=False):
    evidence.require(case_id in CASES and type(complete) is bool and type(value) is dict
                     and set(value) == {"schema_version", "status", "binding", "loaded", "invocation"}
                     and type(value["binding"]) is dict
                     and set(value["binding"]) == {"path", "sha256"}, "observation_shape")
    expected = verify_binding(value["binding"]["path"], value["binding"]["sha256"], root=root, sources=sources)
    evidence.require(evidence.canonical(value) == evidence.canonical(expected), "observation_changed")
    return value


def directory(case_id):
    evidence.require(case_id in CASES, "case_id")
    raw = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    if raw is None:
        return None
    path = Path(raw)
    before = path.lstat()
    evidence.require(path.is_absolute() and path.resolve() == path and path.name == case_id
                     and path.parent.name == "measurements" and stat.S_ISDIR(before.st_mode)
                     and stat.S_IMODE(before.st_mode) == 0o700 and before.st_uid == os.getuid(),
                     "observation_directory")
    return path, before


def gate_sources(root, case_id):
    checked = directory(case_id)
    if checked is None:
        sources = {name: evidence.digest((Path(root) / name).read_bytes()) for name in SOURCES}
    else:
        sources = read_catalog(checked[0].parents[1] / (case_id + ".sources.json"), root)
    validate_sources(root, sources)
    return sources


def record(value, *, case_id, root, sources):
    validate(value, case_id=case_id, root=root, sources=sources, complete=True)
    checked = directory(case_id)
    if checked is None:
        return value
    path, before = checked
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        evidence.require(evidence.identity(os.fstat(descriptor)) == evidence.identity(before)
                         and path.resolve() == path and evidence.identity(path.lstat()) == evidence.identity(before),
                         "observation_directory_changed")
        output = os.open(FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=descriptor)
        with os.fdopen(output, "wb") as stream:
            stream.write(evidence.canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return value
