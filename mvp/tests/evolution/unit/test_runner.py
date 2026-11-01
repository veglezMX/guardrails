"""Adversarial evidence tests use real subprocesses, signatures and filesystem bytes."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict
from zipfile import ZipFile

import pytest
from evolution.dependencies import (
    canonical,
    digest,
    verify_artifacts,
    verify_environment,
)
from evolution.registry import Case, EvidenceError, load_registry
from evolution.run import (
    ROOT,
    SOURCE_TOP,
    assert_same_source,
    execute_case,
    lock_files,
    run_gate,
    sign_report,
    signing_key,
    source_files,
    validate_results,
    verify_report,
    write_once,
)


def case(
    nodeid="mvp/tests/evolution/unit/test_fixture.py::test_behavior", *, timeout=20
):
    return Case(
        "GOV-RUNNER-EXACT",
        "P1.10",
        "local",
        "remote",
        "registry",
        nodeid,
        ("The observed behavior meets its exact assertion.",),
        timeout,
    )


def registry_file(tmp_path, cases=None):
    row = asdict(case())
    row["assertions"] = list(row["assertions"])
    document = {
        "schema_version": "guardrails-evolution-cases-v1",
        "phases": {
            str(number): {
                "status": "ready" if number == 1 else "unavailable",
                "required_families": ["registry"],
                "cases": [row] if number == 1 else [],
            }
            for number in range(1, 7)
        },
    }
    if cases is not None:
        document["phases"]["1"]["cases"] = cases
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(document))
    return path, document


def test_registry_rejects_duplicates_missing_required_families_and_unknown_profiles(
    tmp_path,
):
    path, document = registry_file(tmp_path)
    assert load_registry(path, 1) == (case(),)
    for phase in (0, 7, True):
        with pytest.raises(EvidenceError, match="unknown_phase"):
            load_registry(path, phase)
    with pytest.raises(EvidenceError, match="optional_profile"):
        load_registry(path, 1, "bedrock-live")
    with pytest.raises(EvidenceError, match="phase_not_implemented"):
        load_registry(path, 2)
    row = document["phases"]["1"]["cases"][0]
    for cases, reason in (
        ([row, row], "duplicate_case"),
        ([], "required_family_missing"),
    ):
        path, _ = registry_file(tmp_path, cases)
        with pytest.raises(EvidenceError, match=reason):
            load_registry(path, 1)
    path.write_text('{"schema_version": 1, "schema_version": 2}')
    with pytest.raises(EvidenceError, match="duplicate_json_key"):
        load_registry(path, 1)
    assert len(document["phases"]) == 6


def make_test_root(tmp_path, text):
    destination = tmp_path / "mvp/tests/evolution/unit/test_fixture.py"
    destination.parent.mkdir(parents=True)
    destination.write_text(text)
    shutil.copytree(
        ROOT / "mvp/evolution",
        tmp_path / "mvp/evolution",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return tmp_path


def test_real_pytest_worker_rejects_empty_skipped_xfailed_missing_and_failed_cases(
    tmp_path,
):
    root = make_test_root(
        tmp_path,
        """import pytest

def test_behavior():
    assert 2 + 3 == 5
    assert "safe".upper() == "SAFE"

def test_empty():
    pass

@pytest.fixture
def asserted_fixture():
    assert 1 == 1
    with pytest.raises(ValueError):
        raise ValueError("private-token-do-not-retain")
    yield
    with pytest.raises(ValueError):
        raise ValueError("private-token-do-not-retain")

def test_fixture_only(asserted_fixture):
    pass

@pytest.mark.skip(reason="private-token-do-not-retain")
def test_skip():
    assert True

@pytest.mark.xfail(reason="private-token-do-not-retain")
def test_xfail():
    assert False

def test_failure():
    assert "private-token-do-not-retain" == "another-value"

def fail_value():
    raise ValueError("private-token-do-not-retain")

def test_raises_context():
    with pytest.raises(ValueError, match="private-token-do-not-retain"):
        fail_value()

def test_raises_function():
    pytest.raises(ValueError, fail_value)

def test_raises_function_keyword():
    pytest.raises(ValueError, func=fail_value)

def test_raises_returned_info():
    with pytest.raises(ValueError) as info:
        fail_value()
    assert info.type is ValueError

def test_raises_unused():
    pytest.raises(ValueError)

def test_raises_unexecuted():
    if False:
        with pytest.raises(ValueError):
            fail_value()

def test_raises_missing():
    with pytest.raises(ValueError):
        pass

def test_raises_wrong_type():
    with pytest.raises(TypeError):
        fail_value()

def test_raises_wrong_match():
    with pytest.raises(ValueError, match="another-value"):
        fail_value()

def test_raises_wrong_check():
    with pytest.raises(ValueError, check=lambda error: False):
        fail_value()

def test_raises_function_missing():
    pytest.raises(ValueError, lambda: None)

def test_raises_function_wrong_type():
    pytest.raises(TypeError, fail_value)

def test_caught_failed_expectation():
    try:
        with pytest.raises(ValueError, match="another-value"):
            fail_value()
    except AssertionError:
        pass
""",
    )
    destination = tmp_path / "results"
    destination.mkdir()
    positive = execute_case(case(), root, destination)
    assert positive["status"] == "pass"
    assert positive["assertions"] == 2
    for index, name in enumerate(
        (
            "empty", "fixture_only", "skip", "xfail", "missing", "failure",
            "raises_unused", "raises_unexecuted", "raises_missing",
            "raises_wrong_type", "raises_wrong_match", "raises_wrong_check",
            "raises_function_missing", "raises_function_wrong_type",
            "caught_failed_expectation",
        )
    ):
        selected = Case(
            f"GOV-RUNNER-{index}",
            "P1.10",
            "local",
            "remote",
            "registry",
            f"mvp/tests/evolution/unit/test_fixture.py::test_{name}",
            ("Required assertion",),
            20,
        )
        result = execute_case(selected, root, destination)
        assert result["status"] == "fail"
        assert result["assertions"] == 0
        assert result["reason_codes"]
    for name, expected_count in (
        ("raises_context", 1), ("raises_function", 1),
        ("raises_function_keyword", 1), ("raises_returned_info", 2),
    ):
        selected = Case(
            f"GOV-RUNNER-{name.upper()}", "P1.10", "local", "remote", "registry",
            f"mvp/tests/evolution/unit/test_fixture.py::test_{name}",
            ("Completed exception assertion",), 20,
        )
        result = execute_case(selected, root, destination)
        assert result["status"] == "pass"
        assert result["assertions"] == expected_count
    assert all(
        "private-token-do-not-retain" not in path.read_text()
        for path in destination.glob("*.json")
    )


def test_worker_enforces_real_process_deadline_and_exact_collection(tmp_path):
    root = make_test_root(
        tmp_path,
        """import time
import pytest

def test_behavior():
    time.sleep(5)
    assert True

@pytest.mark.parametrize("value", [1, 2])
def test_parameterized(value):
    assert value > 0
""",
    )
    destination = tmp_path / "results"
    destination.mkdir()
    result = execute_case(case(timeout=1), root, destination)
    assert result["status"] == "fail"
    assert result["reason_codes"] == ["case_timeout"]
    assert result["duration_ms"] < 4000
    selected = Case(
        "GOV-RUNNER-COLLECTION",
        "P1.10",
        "local",
        "remote",
        "registry",
        "mvp/tests/evolution/unit/test_fixture.py::test_parameterized",
        ("Exact case",),
        20,
    )
    result = execute_case(selected, root, destination)
    assert result["status"] == "fail"
    assert "case_inventory_mismatch" in result["reason_codes"]


def test_results_reject_missing_duplicate_skip_zero_and_failure():
    passing = {"case_id": case().case_id, "status": "pass", "assertions": 1}
    assert validate_results([case()], [passing]) is None
    for rows in (
        [],
        [passing, passing],
        [passing | {"status": "skip"}],
        [passing | {"status": "fail"}],
        [passing | {"assertions": 0}],
        [passing | {"case_id": "GOV-OTHER"}],
    ):
        with pytest.raises(EvidenceError):
            validate_results([case()], rows)
    assert passing["assertions"] == 1


def test_signed_reports_detect_tampering_and_cannot_overwrite(tmp_path):
    key, key_id = signing_key(tmp_path)
    public = (tmp_path / ".evolution/secrets/acceptance-ed25519.pub").read_bytes()
    signed = sign_report(
        {"run_id": "run-a", "status": "pass", "cases": []}, key, key_id
    )
    assert verify_report(signed, public) is None
    with pytest.raises(EvidenceError, match="evidence_signature_invalid"):
        verify_report(signed | {"status": "fail"}, public)
    path = tmp_path / "report.json"
    write_once(path, signed)
    with pytest.raises(FileExistsError):
        write_once(path, signed | {"status": "fail"})
    assert json.loads(path.read_text()) == signed
    again, again_id = signing_key(tmp_path)
    assert again_id == key_id
    assert (
        sign_report({"run_id": "run-a", "status": "pass", "cases": []}, again, again_id)
        == signed
    )
    (tmp_path / ".evolution/secrets/acceptance-ed25519.key").chmod(0o644)
    with pytest.raises(EvidenceError, match="signing_key_permissions"):
        signing_key(tmp_path)


def test_dirty_source_hash_detects_modification_addition_and_symlinks(tmp_path):
    for name in SOURCE_TOP:
        (tmp_path / name).write_text("frozen\n")
    code = tmp_path / "mvp/code.py"
    code.parent.mkdir()
    code.write_text("value = 1\n")
    initial = source_files(tmp_path)
    assert assert_same_source(initial, tmp_path) is None
    code.write_text("value = 2\n")
    with pytest.raises(EvidenceError, match="source_changed"):
        assert_same_source(initial, tmp_path)
    code.write_text("value = 1\n")
    added = tmp_path / "mvp/added.py"
    added.write_text("new = True\n")
    with pytest.raises(EvidenceError, match="source_changed"):
        assert_same_source(initial, tmp_path)
    added.unlink()
    added.symlink_to(code)
    with pytest.raises(EvidenceError, match="source_symlink"):
        source_files(tmp_path)
    assert initial["mvp/code.py"] != hashlib.sha256(b"value = 2\n").hexdigest()


def test_prepared_artifacts_reject_missing_modified_or_stale_bytes(tmp_path):
    from evolution.dependencies import declarations

    state = tmp_path / ".evolution"
    wheelhouse = state / "wheels-unique"
    wheelhouse.mkdir(parents=True)
    wheel = wheelhouse / "example-1.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "example-1.0.dist-info/METADATA", "Name: example\nVersion: 1.0\n"
        )
    lock = tmp_path / "evolution-requirements.lock"
    lock.write_text("example==1.0 \\\n    --hash=sha256:" + digest(wheel) + "\n")
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires=[]\n[project]\nrequires-python=">=3.14,<3.15"\ndependencies=[]\n[project.optional-dependencies]\n'
    )
    artifact = {
        "name": wheel.name,
        "version": "1.0",
        "source_url": "https://pypi.org/project/example/1.0/",
        "sha256": digest(wheel),
        "license": "MIT",
        "size_bytes": wheel.stat().st_size,
        "kind": "wheel",
    }
    manifest = {
        "lock_digest": digest(lock),
        "declarations_digest": hashlib.sha256(
            canonical(declarations(tmp_path))
        ).hexdigest(),
        "wheelhouse": wheelhouse.name,
        "artifacts": [artifact],
    }
    (state / "dependencies.json").write_text(json.dumps(manifest))
    assert verify_artifacts(tmp_path) == [artifact]
    original = wheel.read_bytes()
    wheel.write_bytes(original + b"tamper")
    with pytest.raises(EvidenceError, match="artifact_integrity_failed"):
        verify_artifacts(tmp_path)
    wheel.write_bytes(original)
    lock.write_text(lock.read_text() + "# changed\n")
    with pytest.raises(EvidenceError, match="prepared_lock_stale"):
        verify_artifacts(tmp_path)
    assert artifact["kind"] == "wheel"


def test_locked_environment_retains_baseline_and_imports_owned_dependencies():
    from evolution.dependencies import smoke

    pins = verify_environment(ROOT / "evolution-requirements.lock")
    assert pins["fastapi"] == "0.143.0"
    assert pins["pydantic"] == "2.14.0"
    assert pins["pytest"] == "9.1.1"
    assert smoke() is None


def test_worker_timeout_terminates_server_descendants(tmp_path):
    import socket

    root = make_test_root(
        tmp_path,
        """import pathlib
import subprocess
import sys
import time

def test_behavior():
    child = "import socket,time,pathlib; server=socket.socket(); server.bind(('127.0.0.1',0)); server.listen(); pathlib.Path('child.port').write_text(str(server.getsockname()[1])); time.sleep(30)"
    subprocess.Popen([sys.executable, "-c", child])
    while not pathlib.Path("child.port").exists():
        time.sleep(0.01)
    time.sleep(30)
    assert True
""",
    )
    destination = tmp_path / "results"
    destination.mkdir()
    # Include bounded cold interpreter/pytest startup before the descendant
    # starts listening. Both processes then sleep 30 seconds, so this still
    # requires the real timeout path to terminate the whole process group.
    result = execute_case(case(timeout=10), root, destination)
    assert result["reason_codes"] == ["case_timeout"]
    assert (root / "child.port").exists()
    port = int((root / "child.port").read_text())
    with socket.socket() as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_registry_rejects_malformed_types_and_invalid_utf8(tmp_path):
    from copy import deepcopy

    path, valid = registry_file(tmp_path)
    malformed = [
        None,
        [],
        {"schema_version": "guardrails-evolution-cases-v1", "phases": []},
    ]
    for field, value in (("status", []), ("required_families", [None]), ("cases", {})):
        document = deepcopy(valid)
        document["phases"]["1"][field] = value
        malformed.append(document)
    for field in ("case_id", "nodeid", "mode", "family"):
        document = deepcopy(valid)
        document["phases"]["1"]["cases"][0][field] = []
        malformed.append(document)
    for document in malformed:
        path.write_text(json.dumps(document))
        with pytest.raises(EvidenceError):
            load_registry(path, 1)
    path.write_bytes(b"\xffprivate-credential-do-not-retain")
    with pytest.raises(EvidenceError) as failure:
        load_registry(path, 1)
    assert str(failure.value) == "invalid_json_document"


def test_worker_malformed_evidence_never_passes_or_echoes_private_values(tmp_path):
    root = make_test_root(tmp_path, "def test_behavior():\n    assert True\n")
    worker = root / "mvp/evolution/worker.py"
    valid_shape = {
        "nodeid": case().nodeid,
        "status": "pass",
        "duration_ms": 1.0,
        "assertions": 1,
        "reason_codes": [],
    }
    payloads = [
        [],
        valid_shape | {"assertions": True},
        valid_shape | {"duration_ms": "private-credential-do-not-retain"},
        valid_shape | {"reason_codes": ["private-credential-do-not-retain"]},
        valid_shape | {"reason_codes": ["case_call_failed"]},
    ]
    for index, payload in enumerate(payloads):
        worker.write_text(
            "import pathlib,sys\npathlib.Path(sys.argv[2]).write_text("
            + repr(json.dumps(payload))
            + ")\n"
        )
        destination = tmp_path / str(index)
        destination.mkdir()
        result = execute_case(case(), root, destination)
        assert result["status"] == "fail"
        assert result["assertions"] == 0
        assert result["reason_codes"] == ["worker_evidence_missing_or_invalid"]
        assert "private-credential-do-not-retain" not in json.dumps(result)


def test_gate_preflight_failure_emits_signed_failure_without_running_cases(
    tmp_path, monkeypatch
):
    import evolution.run as runner

    root = make_test_root(
        tmp_path, "def test_behavior():\n    raise RuntimeError('must-not-run')\n"
    )
    registry, _ = registry_file(tmp_path)
    shutil.copy2(registry, root / "mvp/evolution/cases.json")
    for name in SOURCE_TOP:
        shutil.copy2(ROOT / name, root / name)
    # Use the existing read-only Git identity; this fixture creates no commits.
    shutil.copy2(ROOT / ".git", root / ".git")
    nested = root / "deploy/evolution/artifacts.lock.json"
    nested.parent.mkdir(parents=True)
    nested.write_text('{"artifacts":[]}')
    assert str(nested.relative_to(root)) in lock_files(source_files(root))

    def fail_preflight(_):
        raise RuntimeError("database-password-private-credential-do-not-retain")

    monkeypatch.setattr(runner, "phase_artifacts", lambda root, phase: fail_preflight(root))
    assert run_gate(1, root=root) == 1
    reports = list((root / ".evolution/reports").glob("*/report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["status"] == "fail"
    assert len(report["cases"]) == 1
    assert report["cases"][0]["assertions"] == 0
    assert report["cases"][0]["reason_codes"] == ["preflight_failed"]
    public = (root / ".evolution/secrets/acceptance-ed25519.pub").read_bytes()
    assert verify_report(report, public) is None
    manifest = json.loads((reports[0].parent / "manifest.json").read_text())
    assert "preflight_operation_failed" in manifest["errors"]
    assert manifest["lock_files"][str(nested.relative_to(root))] == digest(nested)
    assert "private-credential-do-not-retain" not in "".join(
        path.read_text() for path in reports[0].parent.glob("*.json")
    )


def test_unavailable_or_invalid_source_gate_keeps_separate_append_only_diagnostics(
    tmp_path,
):
    root = make_test_root(tmp_path, "def test_behavior():\n    assert True\n")
    registry, _ = registry_file(tmp_path)
    shutil.copy2(registry, root / "mvp/evolution/cases.json")
    assert run_gate(2, root=root) == 1
    assert run_gate(1, root=root) == 1
    diagnostics = list((root / ".evolution/reports").glob("*/diagnostic.json"))
    assert len(diagnostics) == 2
    reasons = {json.loads(path.read_text())["reason_codes"][0] for path in diagnostics}
    assert reasons == {"phase_not_implemented", "source_file_missing"}
    assert not list((root / ".evolution/reports").glob("*/report.json"))
    assert len({json.loads(path.read_text())["run_id"] for path in diagnostics}) == 2
