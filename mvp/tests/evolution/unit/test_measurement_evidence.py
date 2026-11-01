"""Measurement privacy, closed inventories, and actual signed-runner attachment flow."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from evolution import run, stage_measurements
from evolution.measurements import (
    CLOCK_INTERVALS,
    FAILURE_SAMPLE_LIMIT,
    NOISY_CASE,
    RESPONSE_BINS,
    collect_measurements,
    prepare_directory,
    validate_measurement,
)
from evolution.registry import EvidenceError
from test_runner import case, make_test_root


def sample():
    def group(scheduled, correct, quota=0):
        return {
            "scheduled": scheduled, "correct_terminal": correct,
            "correct_within_window": correct, "quota_rejections": quota,
            "queue_rejections": 0, "operational_errors": 0,
            "client_ms": {"p50": 10.0, "p95": 20.0, "p99": 30.0},
            "max_launch_lag_ms": 2.0,
            "response_counts": {
                **dict.fromkeys(RESPONSE_BINS, 0),
                "correct_decision": correct, "quota_exceeded": quota,
            },
        }
    return {
        "schema_version": "evolution-noisy-neighbor-v2", "window_seconds": 30,
        "b_evaluations_per_second": 5, "a_concurrent_attempts_per_burst": 8,
        "a_bursts": 30, "text_bytes": 1024, "runtime_budget_ms": 500,
        "baseline_b": group(150, 150), "contention_b": group(150, 150),
        "contention_a": group(240, 30, 210), "quota_rejection_bursts": 30,
        "b_throughput_ratio": 1.0, "audit_checked": True,
        "audit_consistent": True, "leases_terminal": True,
        "clock_intervals": {name: {
            "wall_elapsed_ms": 30000.0, "monotonic_elapsed_ms": 30000.0,
            "max_divergence_ms": 0.0, "stable": True,
        } for name in CLOCK_INTERVALS},
    }


def save(directory, value):
    path = directory / "noisy-neighbor.json"
    run.write_once(path, value)
    return path


def sample_v3():
    value = sample()
    value["schema_version"] = "evolution-noisy-neighbor-v3"
    for name in ("baseline_b", "contention_b", "contention_a"):
        group = value[name]
        failed = group["scheduled"] - group["correct_terminal"]
        group["failure_samples"] = {
            "rows": [
                {"ordinal": ordinal, "replica": ordinal % 2, "status": 429,
                 "response_bin": "quota_exceeded", "elapsed_ms": 25.0,
                 "launch_lag_ms": 1.0}
                for ordinal in range(group["correct_terminal"], min(
                    group["scheduled"], group["correct_terminal"] + FAILURE_SAMPLE_LIMIT,
                ))
            ],
            "omitted": max(0, failed - FAILURE_SAMPLE_LIMIT),
        }
    return value


def stage_sample(measurement):
    """Synthetic missing-observer rows for collector/signature unit fixtures."""
    rows, omitted = [], {}
    for cohort in stage_measurements.COHORTS:
        summary = measurement[cohort]
        count = summary["scheduled"] - summary["correct_terminal"]
        take = min(count, stage_measurements.ROW_LIMIT - len(rows))
        omitted[cohort] = count - take
        for failure in summary["failure_samples"]["rows"][:take]:
            rows.append({"cohort": cohort, **{key: failure[key] for key in ("ordinal", "replica", "status")},
                         "observation": "unavailable", "reason": "missing", "response_ms": None, "jobs": []})
    return {"schema_version": stage_measurements.SCHEMA, "rows": rows, "omitted": omitted}


def stage_sources(root):
    result = {}
    for name in stage_measurements.SOURCES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("def public_source():\n    return None\n")
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def test_failure_samples_bind_order_replica_status_and_complete_counts():
    value = sample_v3()
    group = value["baseline_b"]
    group.update(correct_terminal=146, correct_within_window=146, operational_errors=4)
    group["response_counts"].update(correct_decision=146, evaluation_timeout=4)
    group["client_ms"]["p99"] = 532.0
    group["failure_samples"] = {
        "rows": [
            {"ordinal": ordinal, "replica": ordinal % 2, "status": 504,
             "response_bin": "evaluation_timeout", "elapsed_ms": 532.0,
             "launch_lag_ms": 1.0}
            for ordinal in (0, 1, 120, 121)
        ],
        "omitted": 0,
    }
    value["b_throughput_ratio"] = 150 / 146
    validate_measurement(value)
    # Even a fully failed experiment has at most24 rows, exact omitted counts,
    # and remains inside the unchanged attachment byte bound.
    for name, scheduled in (("baseline_b", 150), ("contention_b", 150), ("contention_a", 240)):
        group = value[name]
        group.update(correct_terminal=0, correct_within_window=0, quota_rejections=0,
                     operational_errors=scheduled)
        group["response_counts"] = {**dict.fromkeys(RESPONSE_BINS, 0), "evaluation_timeout": scheduled}
        group["failure_samples"] = {
            "rows": [{"ordinal": n, "replica": n % 2, "status": 504,
                      "response_bin": "evaluation_timeout", "elapsed_ms": 532.0,
                      "launch_lag_ms": 1.0} for n in range(FAILURE_SAMPLE_LIMIT)],
            "omitted": scheduled - FAILURE_SAMPLE_LIMIT,
        }
    value["b_throughput_ratio"] = 0.0
    validate_measurement(value)
    assert sum(len(value[name]["failure_samples"]["rows"]) for name in (
        "baseline_b", "contention_b", "contention_a",
    )) == 24
    assert len(json.dumps(value, sort_keys=True).encode()) < 16384


def test_failure_sample_schema_refuses_payload_and_inconsistent_rows():
    mutations = (
        lambda v, s, r: r.update(request_id="private-canary"),
        lambda v, s, r: r.update(response_bin="private-canary"),
        lambda v, s, r: r.update(response_bin="correct_decision"),
        lambda v, s, r: r.update(status=True),
        lambda v, s, r: r.update(status=504),
        lambda v, s, r: r.update(ordinal=True),
        lambda v, s, r: r.update(ordinal=-1),
        lambda v, s, r: r.update(ordinal=240),
        lambda v, s, r: r.update(replica=1),
        lambda v, s, r: r.update(replica=False),
        lambda v, s, r: r.update(elapsed_ms=float("nan")),
        lambda v, s, r: r.update(elapsed_ms=-1),
        lambda v, s, r: r.update(launch_lag_ms=3),
        lambda v, s, r: r.update(response_bin="queue_full"),
        lambda v, s, r: s.update(omitted=True),
        lambda v, s, r: s.update(omitted=0),
        lambda v, s, r: s["rows"].pop(),
        lambda v, s, r: s["rows"].append(deepcopy(r)),
        lambda v, s, r: s["rows"].reverse(),
        lambda v, s, r: s["rows"].__setitem__(1, deepcopy(r)),
        lambda v, s, r: v.update(schema_version="evolution-noisy-neighbor-v2"),
    )
    for mutate in mutations:
        value = sample_v3()
        samples = value["contention_a"]["failure_samples"]
        mutate(value, samples, samples["rows"][0])
        with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
            validate_measurement(value)


def test_collector_binds_v3_failure_samples_and_keeps_v2_readable(tmp_path):
    for version, value in (("v2", sample()), ("v3", sample_v3())):
        destination = tmp_path / version
        directory = prepare_directory(destination, NOISY_CASE)
        path = save(directory, value)
        result = collect_measurements(destination, [NOISY_CASE])
        assert result[str(path.relative_to(destination))] == {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        }


def test_measurement_schema_refuses_free_text_nonfinite_and_inconsistent_counts():
    validate_measurement(sample())
    mutations = (
        lambda v: v.update(prompt="private-canary"),
        lambda v: v.update(audit_checked="private-canary"),
        lambda v: v.update(runtime_budget_ms=True),
        lambda v: v.update(b_throughput_ratio=float("nan")),
        lambda v: v.update(b_throughput_ratio=0.5),
        lambda v: v["baseline_b"].update(correct_terminal=151),
        lambda v: v["baseline_b"]["client_ms"].update(p99=float("inf")),
        lambda v: v["baseline_b"]["client_ms"].update(p99=1),
        lambda v: v["baseline_b"].update(operational_errors=True),
        lambda v: v["baseline_b"]["response_counts"].update(raw="private-canary"),
        lambda v: v["baseline_b"]["response_counts"].update(snapshot_stale=1),
        lambda v: v["baseline_b"]["response_counts"].update(correct_decision=149),
        lambda v: v["clock_intervals"].update(private="private-canary"),
        lambda v: v["clock_intervals"]["baseline"].update(wall_elapsed_ms=float("nan")),
        lambda v: v["clock_intervals"]["baseline"].update(monotonic_elapsed_ms=-1),
        lambda v: v["clock_intervals"]["baseline"].update(wall_elapsed_ms=45000),
        lambda v: v["clock_intervals"]["baseline"].update(stable=False),
        lambda v: v["clock_intervals"]["baseline"].update(max_divergence_ms=101),
    )
    for mutate in mutations:
        value = sample()
        mutate(value)
        with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
            validate_measurement(value)
    # A failed clock-sensitive run must still retain valid diagnostic metadata.
    value = sample()
    value["clock_intervals"]["contention"].update(
        wall_elapsed_ms=-1000, monotonic_elapsed_ms=30000,
        max_divergence_ms=31000, stable=False,
    )
    value["contention_b"].update(correct_terminal=149, correct_within_window=149,
                                 operational_errors=1)
    value["contention_b"]["response_counts"].update(correct_decision=149, snapshot_stale=1)
    value["b_throughput_ratio"] = 149 / 150
    validate_measurement(value)


def test_measurement_inventory_rejects_missing_extra_links_and_public_files(tmp_path):
    assert collect_measurements(tmp_path, [NOISY_CASE]) == {}
    with pytest.raises(EvidenceError, match="measurement_evidence_missing"):
        collect_measurements(tmp_path, [NOISY_CASE], [NOISY_CASE])
    directory = prepare_directory(tmp_path, NOISY_CASE)
    path = save(directory, sample())
    relative = str(path.relative_to(tmp_path))
    assert collect_measurements(tmp_path, [NOISY_CASE]) == {
        relative: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                   "size_bytes": path.stat().st_size},
    }
    path.chmod(0o644)
    with pytest.raises(EvidenceError, match="measurement_path_invalid"):
        collect_measurements(tmp_path, [NOISY_CASE])
    path.chmod(0o600)
    elsewhere = tmp_path / "original.json"
    path.rename(elsewhere)
    path.symlink_to(elsewhere)
    with pytest.raises(EvidenceError, match="measurement_evidence_invalid"):
        collect_measurements(tmp_path, [NOISY_CASE])
    path.unlink()
    elsewhere.rename(path)
    extra = directory / "raw-content.json"
    extra.write_text("private-canary")
    with pytest.raises(EvidenceError, match="measurement_unexpected_files"):
        collect_measurements(tmp_path, [NOISY_CASE])
    extra.unlink()
    with pytest.raises(EvidenceError, match="measurement_unexpected_files"):
        collect_measurements(tmp_path, [])
    path.unlink()
    with pytest.raises(EvidenceError, match="measurement_evidence_missing"):
        collect_measurements(tmp_path, [NOISY_CASE], [NOISY_CASE])


def test_measurement_bytes_refuse_duplicate_keys_oversize_and_malformed_json(tmp_path):
    directory = prepare_directory(tmp_path, NOISY_CASE)
    path = directory / "noisy-neighbor.json"
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b'x' * 16385):
        path.write_bytes(raw)
        path.chmod(0o600)
        with pytest.raises(EvidenceError):
            collect_measurements(tmp_path, [NOISY_CASE])


def test_signed_gate_manifest_binds_real_worker_measurement_bytes(tmp_path, monkeypatch):
    value = sample_v3()
    stages = stage_sample(value)
    root = make_test_root(tmp_path / "root", f'''import json, os
from pathlib import Path
def test_behavior():
    path = Path(os.environ["EVOLUTION_TEST_EVIDENCE_DIR"]) / "noisy-neighbor.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({value!r}, stream)
    stage_path = path.parent / "runtime-stage-observation.json"
    descriptor = os.open(stage_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({stages!r}, stream)
    assert path.is_file()
''')
    selected = replace(case(), case_id=NOISY_CASE)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    from evolution import postgres
    monkeypatch.setattr(run, "load_registry", lambda *_: (selected,))
    monkeypatch.setattr(run, "verify_environment", lambda *_: None)
    monkeypatch.setattr(run, "verify_installed_wheels", lambda *_: None)
    monkeypatch.setattr(run, "phase_artifacts", lambda *_: [])
    monkeypatch.setattr(postgres, "prepare", lambda *_: {})
    fixture = root / selected.nodeid.split("::")[0]
    frozen = stage_sources(root) | {str(fixture.relative_to(root)): run.digest(fixture)}
    monkeypatch.setattr(run, "source_files", lambda *_: frozen)
    monkeypatch.setattr(run.subprocess, "check_output", lambda *_a, **_k: "2" * 40)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(tmp_path / "untrusted"))
    assert run._run_gate(2, root, str(uuid4()), destination) == 0
    report = json.loads((destination / "report.json").read_text())
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    run.verify_report(report, (root / ".evolution/secrets/acceptance-ed25519.pub").read_bytes())
    assert report["environment"]["manifest_sha256"] == run.digest(manifest_path)
    attachments = manifest["measurements"]
    assert len(attachments) == 2
    relative = f"measurements/{NOISY_CASE}/noisy-neighbor.json"
    info = attachments[relative]
    assert info["sha256"] == run.digest(destination / relative)
    stage_relative = f"measurements/{NOISY_CASE}/runtime-stage-observation.json"
    assert attachments[stage_relative]["sha256"] == run.digest(destination / stage_relative)
    expected_sources = {name: frozen[name] for name in stage_measurements.SOURCES}
    assert info["source_files"] == attachments[stage_relative]["source_files"] == expected_sources
    assert not (tmp_path / "untrusted").exists()
    changed = json.loads((destination / relative).read_text())
    changed["audit_checked"] = False
    (destination / relative).write_text(json.dumps(changed))
    assert info["sha256"] != run.digest(destination / relative)


def test_real_worker_cannot_pass_when_required_measurement_is_missing(tmp_path):
    root = make_test_root(tmp_path / "root", "def test_behavior():\n    assert True\n")
    selected = replace(case(), case_id=NOISY_CASE)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = run.execute_case(selected, root, destination)
    assert result["status"] == "fail"
    assert result["reason_codes"] == ["measurement_evidence_missing_or_invalid"]


@pytest.mark.parametrize("marker", ["clock_discontinuity", "private-canary"])
def test_failed_worker_retains_only_exact_clock_failure_marker(tmp_path, marker):
    root = make_test_root(tmp_path / "root", f'''def test_behavior(record_property):
    record_property("evolution_environment_failure", {marker!r})
    assert False, "private-exception-canary"
''')
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = run.execute_case(case(), root, destination)
    assert result["status"] == "fail"
    assert ("environment_clock_discontinuity" in result["reason_codes"]) == (
        marker == "clock_discontinuity"
    )
    assert "private" not in json.dumps(result)
