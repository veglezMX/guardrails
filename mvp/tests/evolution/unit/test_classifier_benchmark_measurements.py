"""Real file-bound consumer paths over synthetic reports; no model execution."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest
from evolution import classifier_benchmark_evidence as evidence
from evolution import classifier_benchmark_measurements as measurement
from evolution import measurements
from evolution.registry import EvidenceError
from test_classifier_benchmark_evidence import disk_package, documents, encode  # noqa: F401

ROOT = Path(__file__).resolve().parents[4]


def private(path, value):
    path.write_bytes(encode(value))
    path.chmod(0o600)
    return {"path": str(path), "sha256": evidence._sha(path.read_bytes())}


@pytest.fixture
def bound(request):
    root, output, pins = request.getfixturevalue("disk_package")
    for name in measurement.SOURCES:
        if name not in evidence.SOURCE_FILES:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
            target.chmod(0o644)
    sources = {name: evidence._sha((root / name).read_bytes()) for name in measurement.SOURCES}
    invocation = root.parent / "invocation"
    invocation.mkdir(mode=0o700)
    started = {"automatic_retry": False, "command_sha256": "a" * 64,
               "health": str(root.parent / "health.json"), "health_sha256": "b" * 64,
               "maximum_seconds": 3600, "model_output": str(output),
               "started_at": "2026-10-12T00:00:00+00:00", "warmup_requests": 0}
    result = {"cost": {"tokens": "unavailable", "tool_credits": "unavailable"},
              "elapsed_seconds": 139.0, "exit_code": 0, "output": str(output), "phase_gate_claimed": False}
    binding = {"schema_version": measurement.BINDING_SCHEMA, "benchmark_directory": str(output),
               "evidence_sha256": pins, "scoring_sources": {name: sources[name] for name in evidence.SOURCE_FILES},
               "corpus_sha256": evidence.CORPUS_SHA256,
               "invocation": {"started": private(invocation / "started.json", started),
                              "result": private(invocation / "result.json", result),
                              "command_sha256": "a" * 64, "launcher_sha256": "c" * 64}}
    reference = private(root.parent / "binding.json", binding)
    return root, output, sources, reference, binding


def check(bound):
    root, _, sources, reference, _ = bound
    return measurement.verify_binding(reference["path"], reference["sha256"], root=root, sources=sources)


def repin(bound):
    private(Path(bound[3]["path"]), bound[4])
    bound[3]["sha256"] = evidence._sha(Path(bound[3]["path"]).read_bytes())


def test_original_invocation_and_all_rows_validate_without_model_import(bound):
    result = check(bound)
    assert result["invocation"]["exit_code"] == 0
    assert result["benchmark"]["cases"] == 600
    assert result["cpu"]["torch_threads"] == 2
    assert result["benchmark"]["deployed_sla_verified"] is False
    assert result["benchmark"]["spanish_enforcement_enabled"] is False


@pytest.mark.parametrize("change", ("exit-one", "exit-bool", "invocation-failed", "wrong-output", "gate-claim", "timeout", "retry", "warmup", "command", "started-late", "unknown-private-field"))
def test_failed_or_misbound_invocation_refuses_even_perfect_scores(bound, change):
    binding = bound[4]
    start_ref, result_ref = binding["invocation"]["started"], binding["invocation"]["result"]
    start, result = json.loads(Path(start_ref["path"]).read_bytes()), json.loads(Path(result_ref["path"]).read_bytes())
    if change == "exit-one": result["exit_code"] = 1
    elif change == "exit-bool": result["exit_code"] = False
    elif change == "invocation-failed": private(Path(result_ref["path"]).parent / "failed.json", {"status": "failed"})
    elif change == "wrong-output": result["output"] = str(bound[1].parent / "foreign")
    elif change == "gate-claim": result["phase_gate_claimed"] = True
    elif change == "timeout": result["elapsed_seconds"] = 3601
    elif change == "retry": start["automatic_retry"] = True
    elif change == "warmup": start["warmup_requests"] = 1
    elif change == "command": start["command_sha256"] = "d" * 64
    elif change == "started-late": start["started_at"] = "2026-10-12T00:01:00+00:00"
    else: result["private_raw"] = "must not be retained"
    binding["invocation"]["started"] = private(Path(start_ref["path"]), start)
    binding["invocation"]["result"] = private(Path(result_ref["path"]), result)
    repin(bound)
    with pytest.raises(EvidenceError): check(bound)


@pytest.mark.parametrize("change", ("pin", "public", "symlink", "hardlink", "source", "corpus", "reports", "unknown"))
def test_parent_binding_is_required_and_closed(bound, change):
    reference, binding = bound[3:]
    path = Path(reference["path"])
    if change == "pin": reference["sha256"] = "0" * 64
    elif change == "public": path.chmod(0o644)
    elif change == "symlink":
        moved = path.with_name("other.json"); path.rename(moved); path.symlink_to(moved)
    elif change == "hardlink": os.link(path, path.with_name("other.json"))
    else:
        if change == "source": binding["scoring_sources"][evidence.SOURCE_FILES[0]] = "0" * 64
        elif change == "corpus": binding["corpus_sha256"] = "0" * 64
        elif change == "reports": binding["evidence_sha256"]["cases.jsonl"] = "0" * 64
        else: binding["secret"] = "must not be accepted"
        repin(bound)
    with pytest.raises(EvidenceError): check(bound)


def test_collector_reopens_cases_and_refuses_replaced_report(bound):
    value = check(bound)
    (bound[1] / "cases.jsonl").write_bytes(b"{}\n")
    with pytest.raises(EvidenceError):
        measurement.validate(value, case_id=measurement.CASE, root=bound[0], sources=bound[2], complete=True)


@pytest.mark.parametrize("field", ("cpu", "matrices", "binding", "raw"))
def test_saved_attachment_cannot_invent_success_metadata(bound, field):
    value = check(bound)
    if field == "cpu": value["cpu"]["torch_threads"] = 4
    elif field == "matrices": value["benchmark"]["matrices"]["es"]["false_positive"] = 1
    elif field == "binding": value["binding"]["sha256"] = "0" * 64
    else: value["stdout"] = "private"
    with pytest.raises(EvidenceError):
        measurement.validate(value, case_id=measurement.CASE, root=bound[0], sources=bound[2], complete=True)


def test_receipt_changed_between_validation_and_return_refuses(bound, monkeypatch):
    original = evidence.verify_benchmark
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        Path(bound[3]["path"]).write_bytes(b"{}\n")
        return value
    monkeypatch.setattr(evidence, "verify_benchmark", changed)
    with pytest.raises(EvidenceError): check(bound)


def measurement_directory(bound, monkeypatch):
    base = bound[0].parent / "measurements"
    base.mkdir(mode=0o700)
    directory = base / measurement.CASE
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(directory))
    return directory


def test_recorder_exclusively_retains_finite_attachment(bound, monkeypatch):
    value = check(bound)
    directory = measurement_directory(bound, monkeypatch)
    measurement.record(value, root=bound[0], sources=bound[2])
    raw = (directory / measurement.FILENAME).read_bytes()
    assert len(raw) <= measurement.MAX_BYTES
    assert json.loads(raw) == value
    with pytest.raises(FileExistsError): measurement.record(value, root=bound[0], sources=bound[2])
    assert (directory / measurement.FILENAME).read_bytes() == raw


def test_recorder_refuses_replaced_output_directory_without_foreign_write(bound, monkeypatch):
    value = check(bound)
    directory = measurement_directory(bound, monkeypatch)
    original = os.open
    def swapped(path, flags, *args, **kwargs):
        if path == directory:
            directory.rename(directory.with_name("held"))
            directory.mkdir(mode=0o700)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", swapped)
    with pytest.raises(EvidenceError): measurement.record(value, root=bound[0], sources=bound[2])
    assert list(directory.iterdir()) == []


def test_gate_cannot_skip_when_parent_binding_is_absent(monkeypatch):
    spec = importlib.util.spec_from_file_location("isolated_benchmark_gate", ROOT / measurement.SOURCES[2])
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.delenv(measurement.RECEIPT_ENV, raising=False)
    monkeypatch.delenv(measurement.PIN_ENV, raising=False)
    with pytest.raises(EvidenceError, match="binding_required"):
        module.test_frozen_heldout_english_quality_and_separate_spanish_report()


def test_gate_uses_actual_verifier_and_recording_once(bound, monkeypatch):
    spec = importlib.util.spec_from_file_location("isolated_benchmark_gate", ROOT / measurement.SOURCES[2])
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.__file__ = str(bound[0] / measurement.SOURCES[2])
    monkeypatch.setenv(measurement.RECEIPT_ENV, bound[3]["path"])
    monkeypatch.setenv(measurement.PIN_ENV, bound[3]["sha256"])
    # Gate source-catalog behavior is covered by existing worker tests; preserve
    # the real benchmark verifier and actual descriptor writer in this test.
    monkeypatch.setattr(measurement, "gate_sources", lambda root: bound[2])
    directory = measurement_directory(bound, monkeypatch)
    module.test_frozen_heldout_english_quality_and_separate_spanish_report()
    measurement.validate(json.loads((directory / measurement.FILENAME).read_bytes()),
                         case_id=measurement.CASE, root=bound[0], sources=bound[2], complete=True)


def test_shared_collector_binds_complete_benchmark_and_current_sources(bound, monkeypatch):
    value = check(bound)
    directory = measurement_directory(bound, monkeypatch)
    measurement.record(value, root=bound[0], sources=bound[2])
    attachment = directory / measurement.FILENAME
    destination = directory.parents[1]
    collected = measurements.collect_measurements(
        destination, [measurement.CASE], required=[measurement.CASE],
        root=bound[0], sources=bound[2],
    )
    assert collected == {str(attachment.relative_to(destination)): {
        "sha256": evidence._sha(attachment.read_bytes()),
        "size_bytes": attachment.stat().st_size,
        "source_files": bound[2],
    }}


@pytest.mark.parametrize("change", ("report", "attachment", "source"))
def test_shared_collector_rejects_lost_or_changed_benchmark_evidence(bound, monkeypatch, change):
    value = check(bound)
    directory = measurement_directory(bound, monkeypatch)
    measurement.record(value, root=bound[0], sources=bound[2])
    if change == "report":
        (bound[1] / "cases.jsonl").write_bytes(b"{}\n")
    elif change == "attachment":
        (directory / measurement.FILENAME).unlink()
    else:
        (bound[0] / measurement.SOURCES[0]).write_bytes(b"# changed\n")
    with pytest.raises(EvidenceError):
        measurements.collect_measurements(
            directory.parents[1], [measurement.CASE], required=[measurement.CASE],
            root=bound[0], sources=bound[2],
        )
