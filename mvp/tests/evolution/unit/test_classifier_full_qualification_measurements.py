"""No ML or actual corpus: complete synthetic 1800-row retained chains only."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
from evolution import classifier_full_qualification_measurements as measurement
from evolution.registry import EvidenceError
from guardrails.evolution.classifier_contract import canonical

from services.classifier import wolf_full_qualification as qualification
from services.classifier.tests.test_wolf_full_qualification import (  # noqa: F401 - shared autouse isolation fixture
    no_model_or_network,
    saved_chain,
)


def binding_fixture(monkeypatch, tmp_path):
    root, plan, report, put = saved_chain(monkeypatch, tmp_path)
    sources = dict(plan["sources"])
    for name in measurement.SOURCES:
        if name not in sources:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_text("# synthetic evidence consumer source\n")
            path.chmod(0o644)
            sources[name] = qualification.sha(path.read_bytes())
    plan_ref = put("plan.json", canonical(plan))
    report[1]["plan_sha256"] = report[2]["plan_sha256"] = plan_ref["sha256"]
    reports = {
        name: put("quality-output/" + name, raw)
        for name, raw in {
            "started.json": canonical(report[1]),
            "result.json": canonical(report[2]),
            "cases.jsonl": report[3],
        }.items()
    }
    launcher = put("launcher.py", b"# synthetic authenticated outer invocation\n")
    monkeypatch.setattr(measurement, "PARENT_LAUNCHER_SHA256", launcher["sha256"])
    start = {
        "schema_version": "wolf-full-qualification-invocation-v1",
        "status": "running",
        "started_at": "2026-10-12T04:00:00+00:00",
        "plan_sha256": plan_ref["sha256"],
        "launcher_sha256": launcher["sha256"],
        "output": plan["output"],
        "maximum_seconds": 3600,
        "automatic_retry": False,
        "warmup_requests": 0,
    }
    cleanup = put(
        "invocation/cleanup.json",
        canonical(
            {
                "schema_version": "wolf-full-initial-cleanup-v1",
                "group_signal_requested": True,
                "group_already_absent": False,
                "leader_reaped": True,
                "leader_exit_observed": True,
                "errors": [],
            }
        ),
    )
    result = {
        "schema_version": "wolf-full-qualification-invocation-v1",
        "status": "completed",
        "exit_code": 0,
        "elapsed_seconds": 10.0,
        "plan_sha256": plan_ref["sha256"],
        "launcher_sha256": launcher["sha256"],
        "output": plan["output"],
        "enabled_model_changed": False,
        "cleanup_sha256": cleanup["sha256"],
        "quality_report_pins": {name: row["sha256"] for name, row in reports.items()},
    }
    binding = {
        "schema_version": measurement.BINDING_SCHEMA,
        "plan": plan_ref,
        "reports": reports,
        "invocation": {
            "launcher": launcher,
            "cleanup": cleanup,
            "started": put("invocation/started.json", canonical(start)),
            "result": put("invocation/result.json", canonical(result)),
        },
    }
    ref = put("binding.json", canonical(binding))
    return root, sources, ref, binding


def repin(row, value):
    raw = canonical(value)
    Path(row["path"]).write_bytes(raw)
    row["sha256"] = qualification.sha(raw)


def verify(root, sources, ref):
    return measurement.verify_binding(
        ref["path"], ref["sha256"], root=root, sources=sources
    )


def test_complete_attachment_authenticates_parent_exit_and_all_three_600_case_reports(
    monkeypatch, tmp_path
):
    root, sources, ref, _ = binding_fixture(monkeypatch, tmp_path)
    value = verify(root, sources, ref)
    assert value["status"] == "verified" and value["qualification"]["cases"] == 1800
    assert value["invocation"]["exit_code"] == 0
    assert (
        measurement.validate(
            value, case_id=measurement.CASE, root=root, sources=sources, complete=True
        )
        == value
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "failure",
        "nonzero",
        "retry",
        "warmup",
        "time_before",
        "time_after",
        "source",
        "changed_report",
        "missing_report",
        "changed_rows",
        "plan",
        "output",
        "extra",
        "enabled",
        "report_pins",
    ],
)
def test_missing_failed_or_changed_invocation_and_evidence_cannot_satisfy_acceptance(
    monkeypatch, tmp_path, change
):
    root, sources, ref, binding = binding_fixture(monkeypatch, tmp_path)
    if change == "missing":
        Path(binding["invocation"]["result"]["path"]).unlink()
    elif change == "failure":
        Path(binding["invocation"]["result"]["path"]).with_name(
            "failure.json"
        ).write_text("{}")
    elif change == "source":
        (root / qualification.SOURCE_FILES[1]).write_text("# changed worker\n")
    elif change == "changed_report":
        Path(binding["reports"]["result.json"]["path"]).write_text("{}")
    elif change == "missing_report":
        Path(binding["reports"]["started.json"]["path"]).unlink()
    elif change == "changed_rows":
        Path(binding["reports"]["cases.jsonl"]["path"]).write_text("{}\n")
    elif change == "plan":
        Path(binding["plan"]["path"]).write_text("{}")
    else:
        field = (
            "started"
            if change in {"retry", "warmup", "time_before", "time_after"}
            else "result"
        )
        value = json.loads(Path(binding["invocation"][field]["path"]).read_bytes())
        if change == "nonzero":
            value["exit_code"] = 1
        elif change == "retry":
            value["automatic_retry"] = True
        elif change == "warmup":
            value["warmup_requests"] = 1
        elif change == "time_before":
            value["started_at"] = "2026-10-12T04:01:00+00:00"
        elif change == "time_after":
            value["started_at"] = "2026-10-12T03:59:00+00:00"
        elif change == "output":
            value["output"] = str(tmp_path / "foreign")
        elif change == "extra":
            value["claimed_pass"] = True
        elif change == "enabled":
            value["enabled_model_changed"] = True
        elif change == "report_pins":
            value["quality_report_pins"]["cases.jsonl"] = "a" * 64
        repin(binding["invocation"][field], value)
        repin(ref, binding)
    with pytest.raises(EvidenceError):
        verify(root, sources, ref)


def test_collector_does_not_trust_saved_verified_attachment_after_source_drift(
    monkeypatch, tmp_path
):
    root, sources, ref, _ = binding_fixture(monkeypatch, tmp_path)
    value = verify(root, sources, ref)
    (root / qualification.SOURCE_FILES[1]).write_text("# promoted selected runtime\n")
    with pytest.raises(EvidenceError):
        measurement.validate(
            value, case_id=measurement.CASE, root=root, sources=sources, complete=True
        )


def test_collector_rejects_attachment_summary_tamper(monkeypatch, tmp_path):
    root, sources, ref, _ = binding_fixture(monkeypatch, tmp_path)
    value = verify(root, sources, ref)
    changed = copy.deepcopy(value)
    changed["qualification"]["matrices"]["fresh"]["en"]["false_positive"] = 1
    with pytest.raises(EvidenceError):
        measurement.validate(
            changed, case_id=measurement.CASE, root=root, sources=sources, complete=True
        )


def test_exclusive_attachment_uses_owned_measurement_descriptor(monkeypatch, tmp_path):
    root, sources, ref, _ = binding_fixture(monkeypatch, tmp_path)
    value = verify(root, sources, ref)
    path = tmp_path / "measurements" / measurement.CASE
    path.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(path))
    assert measurement.record(value, root=root, sources=sources) == value
    raw = (path / measurement.FILENAME).read_bytes()
    assert (
        json.loads(raw) == value
        and (path / measurement.FILENAME).stat().st_mode & 0o777 == 0o600
    )
    with pytest.raises(FileExistsError):
        measurement.record(value, root=root, sources=sources)
    assert (path / measurement.FILENAME).read_bytes() == raw


def test_replaced_measurement_directory_cannot_receive_attachment(
    monkeypatch, tmp_path
):
    root, sources, ref, _ = binding_fixture(monkeypatch, tmp_path)
    value = verify(root, sources, ref)
    path = tmp_path / "measurements" / measurement.CASE
    path.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(path))
    original = os.open

    def swapped(name, *args, **kwargs):
        if name == path:
            path.rename(path.with_name("preserved"))
            path.mkdir(mode=0o700)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped)
    with pytest.raises(EvidenceError):
        measurement.record(value, root=root, sources=sources)
    assert (
        list(path.iterdir()) == [] and list(path.with_name("preserved").iterdir()) == []
    )


@pytest.mark.parametrize("known_pin", [None, "f" * 64])
def test_self_consistent_unapproved_launcher_cannot_claim_actual_execution(
    monkeypatch, tmp_path, known_pin
):
    root, sources, ref, _ = binding_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(measurement, "PARENT_LAUNCHER_SHA256", known_pin)
    with pytest.raises(EvidenceError):
        verify(root, sources, ref)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "changed",
        "unreaped",
        "unobserved",
        "no_group_cleanup",
        "cleanup_error",
        "nonboolean",
        "extra",
    ],
)
def test_actual_owned_group_cleanup_is_required_even_with_successful_quality(
    monkeypatch, tmp_path, change
):
    root, sources, ref, binding = binding_fixture(monkeypatch, tmp_path)
    row = binding["invocation"]["cleanup"]
    if change == "missing":
        Path(row["path"]).unlink()
    elif change == "changed":
        Path(row["path"]).write_text("{}")
    else:
        value = json.loads(Path(row["path"]).read_bytes())
        if change == "unreaped":
            value["leader_reaped"] = False
        elif change == "unobserved":
            value["leader_exit_observed"] = False
        elif change == "no_group_cleanup":
            value["group_signal_requested"] = value["group_already_absent"] = False
        elif change == "cleanup_error":
            value["errors"] = ["group_kill_failed"]
        elif change == "nonboolean":
            value["leader_reaped"] = 1
        elif change == "extra":
            value["raw_diagnostic"] = "forbidden"
        repin(row, value)
        result_ref = binding["invocation"]["result"]
        result = json.loads(Path(result_ref["path"]).read_bytes())
        result["cleanup_sha256"] = row["sha256"]
        repin(result_ref, result)
        repin(ref, binding)
    with pytest.raises(EvidenceError):
        verify(root, sources, ref)
