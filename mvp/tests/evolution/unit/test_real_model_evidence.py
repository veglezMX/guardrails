"""Closed real-model diagnostics and real signed-worker attachment plumbing."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from evolution import run, stage_measurements
from evolution.measurements import (
    FILES,
    NOISY_CASE,
    REAL_MODEL_CASE,
    collect_measurements,
    prepare_directory,
    validate_real_model_observation,
)
from evolution.registry import EvidenceError
from test_measurement_evidence import sample_v3 as noisy_sample
from test_measurement_evidence import stage_sample, stage_sources
from test_runner import case, make_test_root


def sample(count=20, *, failed=False):
    rows = [
        {"call_ordinal": n, "tenant_side": "A" if n % 2 == 0 else "B",
         "http_status": 200, "wire_code": None, "elapsed_ms": 10.0,
         "sample_completed": True}
        for n in range(count)
    ]
    if failed:
        rows[-1].update(http_status=503, wire_code="evaluation_timeout", sample_completed=False)
    return {"schema_version": "evolution-real-model-observation-v1", "expected_calls": 20,
            "actual_attempts": count, "completed": count - int(failed), "rows": rows}


def test_real_model_schema_accepts_bounded_prefix_and_rejects_private_or_inconsistent_data():
    for value in (sample(), sample(0), sample(3, failed=True)):
        validate_real_model_observation(value)
        assert len(json.dumps(value).encode()) < 16384
    mutations = (
        lambda v: v.update(prompt="private-canary"),
        lambda v: v.update(schema_version="evolution-noisy-neighbor-v3"),
        lambda v: v.update(expected_calls=True),
        lambda v: v.update(expected_calls=21),
        lambda v: v.update(actual_attempts=True),
        lambda v: v.update(actual_attempts=2),
        lambda v: v.update(completed=3),
        lambda v: v["rows"].append(deepcopy(v["rows"][-1])),
        lambda v: v["rows"][0].update(request_id="private-canary"),
        lambda v: v["rows"][0].update(call_ordinal=True),
        lambda v: v["rows"][0].update(call_ordinal=1),
        lambda v: v["rows"][0].update(tenant_side="private-tenant"),
        lambda v: v["rows"][0].update(tenant_side="B"),
        lambda v: v["rows"][0].update(http_status=True),
        lambda v: v["rows"][0].update(http_status=600),
        lambda v: v["rows"][0].update(http_status=0),
        lambda v: v["rows"][0].update(wire_code="evaluation_timeout"),
        lambda v: v["rows"][0].update(elapsed_ms=float("nan")),
        lambda v: v["rows"][0].update(elapsed_ms=-1),
        lambda v: v["rows"][0].update(sample_completed=1),
        lambda v: v["rows"][0].update(sample_completed=False),
        lambda v: v["rows"][-1].update(wire_code="private-error"),
        lambda v: v["rows"][-1].update(wire_code={"private": "canary"}),
    )
    for mutate in mutations:
        value = sample(3, failed=True)
        mutate(value)
        with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
            validate_real_model_observation(value)
    value = sample(20)
    value["rows"].append(deepcopy(value["rows"][-1]))
    value.update(actual_attempts=21, completed=21)
    with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
        validate_real_model_observation(value)


def test_real_model_collector_binds_exact_case_and_refuses_cross_case_substitution(tmp_path):
    root = tmp_path / "sources"
    frozen = stage_sources(root)
    for case_id, correct, other in ((REAL_MODEL_CASE, sample(3, failed=True), noisy_sample()),
                                     (NOISY_CASE, noisy_sample(), sample())):
        destination = tmp_path / case_id
        directory = prepare_directory(destination, case_id)
        path = directory / FILES[case_id]
        run.write_once(path, correct)
        expected_paths = {str(path.relative_to(destination))}
        if case_id == NOISY_CASE:
            secondary = directory / stage_measurements.FILENAME
            run.write_once(secondary, stage_sample(correct))
            expected_paths.add(str(secondary.relative_to(destination)))
        attachments = collect_measurements(destination, [case_id], [case_id], root=root, sources=frozen)
        assert set(attachments) == expected_paths
        assert attachments[str(path.relative_to(destination))] == {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
            **({"source_files": frozen} if case_id == NOISY_CASE else {}),
        }
        if case_id == NOISY_CASE:
            assert attachments[str(secondary.relative_to(destination))] == {
                "sha256": hashlib.sha256(secondary.read_bytes()).hexdigest(),
                "size_bytes": secondary.stat().st_size,
                "source_files": frozen,
            }
        path.write_text(json.dumps(other))
        with pytest.raises(EvidenceError, match="^measurement_schema_invalid$"):
            collect_measurements(destination, [case_id], [case_id], root=root, sources=frozen)


def test_signed_worker_manifest_binds_real_model_observation_and_failed_prefix(tmp_path, monkeypatch):
    value = sample()
    root = make_test_root(tmp_path / "root", f'''import json, os
from pathlib import Path
def test_behavior():
    path = Path(os.environ["EVOLUTION_TEST_EVIDENCE_DIR"]) / "real-model-observation.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({value!r}, stream)
    assert path.is_file()
''')
    selected = replace(case(), case_id=REAL_MODEL_CASE)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    from evolution import postgres
    monkeypatch.setattr(run, "load_registry", lambda *_: (selected,))
    monkeypatch.setattr(run, "verify_environment", lambda *_: None)
    monkeypatch.setattr(run, "verify_installed_wheels", lambda *_: None)
    monkeypatch.setattr(run, "phase_artifacts", lambda *_: [])
    monkeypatch.setattr(postgres, "prepare", lambda *_: {})
    fixture = root / selected.nodeid.split("::")[0]
    monkeypatch.setattr(run, "source_files", lambda *_: {str(fixture.relative_to(root)): run.digest(fixture)})
    monkeypatch.setattr(run.subprocess, "check_output", lambda *_a, **_k: "2" * 40)
    assert run._run_gate(2, root, str(uuid4()), destination) == 0
    report = json.loads((destination / "report.json").read_text())
    run.verify_report(report, (root / ".evolution/secrets/acceptance-ed25519.pub").read_bytes())
    manifest = json.loads((destination / "manifest.json").read_text())
    assert report["environment"]["manifest_sha256"] == run.digest(destination / "manifest.json")
    relative = f"measurements/{REAL_MODEL_CASE}/real-model-observation.json"
    assert manifest["measurements"][relative]["sha256"] == run.digest(destination / relative)
    changed = sample(3, failed=True)
    (destination / relative).write_text(json.dumps(changed))
    assert manifest["measurements"][relative]["sha256"] != run.digest(destination / relative)
    # A failure prefix remains valid evidence, but cannot reuse the signed bytes.
    assert collect_measurements(destination, [REAL_MODEL_CASE], [REAL_MODEL_CASE])


def test_real_model_worker_requires_attachment_even_for_successful_assertions(tmp_path):
    root = make_test_root(tmp_path / "root", "def test_behavior():\n    assert True\n")
    selected = replace(case(), case_id=REAL_MODEL_CASE)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = run.execute_case(selected, root, destination)
    assert result["status"] == "fail"
    assert result["reason_codes"] == ["measurement_evidence_missing_or_invalid"]
