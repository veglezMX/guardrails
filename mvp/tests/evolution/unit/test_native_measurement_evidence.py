"""Closed native-fixture observations preserve failures without content or logs."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from evolution import run
from evolution.measurements import (
    FILES,
    NATIVE_CASE,
    NATIVE_SCHEMA,
    NATIVE_SOURCE,
    collect_measurements,
    prepare_directory,
    validate_native_fixture_observation,
)
from evolution.registry import EvidenceError
from test_runner import case, make_test_root

CANARY = "private-native-input-never-retained"


def source(root):
    path = root / NATIVE_SOURCE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def probe():\n    raise RuntimeError('synthetic')\n")
    return {NATIVE_SOURCE: hashlib.sha256(path.read_bytes()).hexdigest()}


def sample():
    return {
        "schema_version": NATIVE_SCHEMA, "command_outcome": "returned", "returncode": 1,
        "elapsed_ms": 35000.0, "probe_status": "fail", "stage": "gateway_readiness",
        "gateway_index": 0, "failure_bin": "gateway_readiness_timeout", "error_category": "runtime",
        "checks_completed": 0, "model_requests": 0, "runtime_requests": 0,
        "readiness_status": 503, "readiness_code": "evaluator_unavailable", "cleanup_failed": False,
        "frames": [{"file": NATIVE_SOURCE, "function": "probe", "line": 2}],
    }


def unavailable(outcome, reason, returncode=None):
    return {
        **sample(), "command_outcome": outcome, "returncode": returncode,
        "probe_status": "unavailable", "stage": "unavailable", "failure_bin": reason,
        **dict.fromkeys(("gateway_index", "error_category", "checks_completed", "model_requests",
                         "runtime_requests", "readiness_status", "readiness_code", "cleanup_failed")),
        "frames": [],
    }


def passed():
    return {**sample(), "returncode": 0, "probe_status": "pass", "stage": "complete",
            "failure_bin": None, "error_category": None, "checks_completed": 57,
            "model_requests": 14, "runtime_requests": 26, "readiness_status": 200,
            "readiness_code": None, "frames": []}


def validate(value, root, sources):
    validate_native_fixture_observation(value, root=root, sources=sources)


@pytest.mark.parametrize(("outcome", "reason", "returncode"), [
    ("returned", "observation_missing", 125),
    ("returned", "observation_invalid", 1),
    ("timeout", "command_timeout", None),
    ("command_error", "command_error", None),
], ids=["missing", "malformed", "timeout", "command-error"])
def test_native_unavailable_observation_retains_unknowns_without_invented_counts(tmp_path, outcome, reason, returncode):
    sources = source(tmp_path)
    value = unavailable(outcome, reason, returncode)
    validate(value, tmp_path, sources)
    assert value["checks_completed"] is None and value["model_requests"] is None
    assert value["runtime_requests"] is None and value["cleanup_failed"] is None
    altered = deepcopy(value)
    altered["checks_completed"] = 0
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        validate(altered, tmp_path, sources)


def test_native_schema_rejects_payload_enums_nonfinite_and_inconsistent_observations(tmp_path):
    sources = source(tmp_path)
    validate(sample(), tmp_path, sources)
    mutations = (
        lambda v: v.update(raw=CANARY),
        lambda v: v.update(failure_bin=CANARY),
        lambda v: v.update(error_category=CANARY),
        lambda v: v.update(stage=CANARY),
        lambda v: v.update(command_outcome=CANARY),
        lambda v: v.update(probe_status=CANARY),
        lambda v: v.update(returncode=True),
        lambda v: v.update(returncode=-65),
        lambda v: v.update(returncode=256),
        lambda v: v.update(returncode=None),
        lambda v: v.update(elapsed_ms=float("nan")),
        lambda v: v.update(elapsed_ms=float("inf")),
        lambda v: v.update(elapsed_ms=-1),
        lambda v: v.update(gateway_index=True),
        lambda v: v.update(gateway_index=2),
        lambda v: v.update(checks_completed=58),
        lambda v: v.update(model_requests=1001),
        lambda v: v.update(runtime_requests=-1),
        lambda v: v.update(readiness_status=True),
        lambda v: v.update(readiness_status=504),
        lambda v: v.update(readiness_code=CANARY),
        lambda v: v.update(cleanup_failed=CANARY),
        lambda v: v.update(cleanup_failed=None),
        lambda v: v.update(probe_status="pass"),
    )
    for mutate in mutations:
        value = sample()
        mutate(value)
        with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
            validate(value, tmp_path, sources)


@pytest.mark.parametrize("mutation", ["file", "function", "line", "boolean-line", "extra", "too-many"])
def test_native_source_frames_reject_forged_paths_members_and_bounds(tmp_path, mutation):
    sources = source(tmp_path)
    value = sample()
    frame = value["frames"][0]
    if mutation == "file":
        frame["file"] = "mvp/" + CANARY + ".py"
    elif mutation == "function":
        frame["function"] = CANARY
    elif mutation == "line":
        frame["line"] = 100
    elif mutation == "boolean-line":
        frame["line"] = True
    elif mutation == "extra":
        frame["locals"] = CANARY
    else:
        value["frames"] *= 9
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        validate(value, tmp_path, sources)


def test_native_frames_require_frozen_unchanged_regular_source_even_without_frames(tmp_path):
    sources = source(tmp_path)
    for frozen in (None, {}, {NATIVE_SOURCE: "0" * 64}):
        with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
            validate(passed(), tmp_path, frozen)
    path = tmp_path / NATIVE_SOURCE
    original = path.read_bytes()
    path.write_text("def " + CANARY.replace("-", "_") + "():\n    pass\n")
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        validate(passed(), tmp_path, sources)
    path.write_bytes(original)
    moved = path.with_suffix(".saved")
    path.rename(moved)
    path.symlink_to(moved)
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        validate(passed(), tmp_path, sources)


def test_native_collector_binds_exact_case_file_and_frozen_source_on_failure(tmp_path):
    root, destination = tmp_path / "root", tmp_path / "report"
    sources = source(root)
    directory = prepare_directory(destination, NATIVE_CASE)
    path = directory / FILES[NATIVE_CASE]
    run.write_once(path, sample())
    records = collect_measurements(destination, [NATIVE_CASE], root=root, sources=sources)
    assert records == {str(path.relative_to(destination)): {
        "sha256": run.digest(path), "size_bytes": path.stat().st_size, "source_files": sources,
    }}
    with pytest.raises(EvidenceError, match="measurement_unexpected_files"):
        collect_measurements(destination, [], root=root, sources=sources)
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        collect_measurements(destination, [NATIVE_CASE])
    assert CANARY not in json.dumps(records)


@pytest.mark.parametrize("mutation", ["extra", "symlink", "public", "oversize", "duplicate", "malformed"])
def test_native_collector_rejects_unsafe_and_malformed_private_attachments(tmp_path, mutation):
    root, destination = tmp_path / "root", tmp_path / "report"
    sources = source(root)
    directory = prepare_directory(destination, NATIVE_CASE)
    path = directory / FILES[NATIVE_CASE]
    run.write_once(path, sample())
    if mutation == "extra":
        (directory / "raw.json").write_text(CANARY)
    elif mutation == "symlink":
        moved = tmp_path / "saved.json"
        path.rename(moved)
        path.symlink_to(moved)
    elif mutation == "public":
        path.chmod(0o644)
    elif mutation == "oversize":
        path.write_text(" " * 16385)
    elif mutation == "duplicate":
        path.write_text('{"schema_version":"first","schema_version":"second"}')
    else:
        path.write_text("not-json-" + CANARY)
    with pytest.raises(EvidenceError):
        collect_measurements(destination, [NATIVE_CASE], root=root, sources=sources)


def test_native_original_failure_cannot_become_pass_from_a_reported_probe_pass(tmp_path):
    value = passed()
    # A terminal child report and the outer command result are separate facts.
    value.update(returncode=125, model_requests=0, runtime_requests=0)
    root = make_test_root(tmp_path / "root", f'''import json,os
from pathlib import Path
def test_behavior():
    path = Path(os.environ["EVOLUTION_TEST_EVIDENCE_DIR"]) / {FILES[NATIVE_CASE]!r}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({value!r}, stream)
    assert False, {CANARY!r}
''')
    source(root)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = run.execute_case(replace(case(), case_id=NATIVE_CASE), root, destination)
    assert result["status"] == "fail" and result["assertions"] == 0
    assert "case_call_failed" in result["reason_codes"]
    assert "measurement_evidence_missing_or_invalid" not in result["reason_codes"]
    records = collect_measurements(destination, [NATIVE_CASE], root=root, sources=run.source_snapshot(root))
    assert len(records) == 1
    assert CANARY not in "".join(path.read_text() for path in destination.glob("*.json"))


def test_native_actual_signed_runner_binds_attachment_and_frozen_probe_source(tmp_path, monkeypatch):
    value = passed()
    root = make_test_root(tmp_path / "root", f'''import json,os
from pathlib import Path
def test_behavior():
    path = Path(os.environ["EVOLUTION_TEST_EVIDENCE_DIR"]) / {FILES[NATIVE_CASE]!r}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({value!r}, stream)
    assert path.is_file()
''')
    frozen = source(root)
    selected = replace(case(), case_id=NATIVE_CASE)
    fixture = root / selected.nodeid.partition("::")[0]
    frozen[str(fixture.relative_to(root))] = run.digest(fixture)
    from evolution import postgres
    monkeypatch.setattr(run, "load_registry", lambda *_: (selected,))
    monkeypatch.setattr(run, "verify_environment", lambda *_: None)
    monkeypatch.setattr(run, "verify_installed_wheels", lambda *_: None)
    monkeypatch.setattr(run, "phase_artifacts", lambda *_: [])
    monkeypatch.setattr(postgres, "prepare", lambda *_: {})
    monkeypatch.setattr(run, "source_files", lambda *_: frozen)
    monkeypatch.setattr(run.subprocess, "check_output", lambda *_a, **_k: "2" * 40)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    assert run._run_gate(2, root, str(uuid4()), destination) == 0
    report = json.loads((destination / "report.json").read_text())
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    run.verify_report(report, (root / ".evolution/secrets/acceptance-ed25519.pub").read_bytes())
    assert report["environment"]["manifest_sha256"] == run.digest(manifest_path)
    relative = f"measurements/{NATIVE_CASE}/{FILES[NATIVE_CASE]}"
    record = manifest["measurements"][relative]
    assert record["sha256"] == run.digest(destination / relative)
    assert record["source_files"] == {NATIVE_SOURCE: frozen[NATIVE_SOURCE]}
    changed = json.loads((destination / relative).read_text())
    changed["returncode"] = 1
    (destination / relative).write_text(json.dumps(changed))
    assert record["sha256"] != run.digest(destination / relative)


def test_native_worker_cannot_pass_without_required_attachment(tmp_path):
    root = make_test_root(tmp_path / "root", "def test_behavior():\n    assert True\n")
    source(root)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = run.execute_case(replace(case(), case_id=NATIVE_CASE), root, destination)
    assert result["status"] == "fail"
    assert result["reason_codes"] == ["measurement_evidence_missing_or_invalid"]


def test_native_worker_rejects_input_named_modified_source_before_retention(tmp_path):
    value = sample()
    value["frames"][0]["function"] = CANARY.replace("-", "_")
    root = make_test_root(tmp_path / "root", f'''import json,os
from pathlib import Path
from evolution.diagnostics import read_catalog
from evolution.measurements import NATIVE_CASE,validate_native_fixture_observation
def test_behavior():
    root = Path.cwd()
    directory = Path(os.environ["EVOLUTION_TEST_EVIDENCE_DIR"])
    sources = read_catalog(directory.parents[1] / (NATIVE_CASE + ".sources.json"), root)
    (root / {NATIVE_SOURCE!r}).write_text({("def " + CANARY.replace("-", "_") + "():\n    pass\n")!r})
    value = {value!r}
    validate_native_fixture_observation(value, root=root, sources=sources)
    (directory / {FILES[NATIVE_CASE]!r}).write_text(json.dumps(value))
    assert False
''')
    source(root)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = run.execute_case(replace(case(), case_id=NATIVE_CASE), root, destination)
    assert result["status"] == "fail"
    assert not (destination / "measurements" / NATIVE_CASE / FILES[NATIVE_CASE]).exists()
    retained = "".join(path.read_text() for path in destination.glob("*.json"))
    assert CANARY not in retained and CANARY.replace("-", "_") not in retained
