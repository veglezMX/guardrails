"""Actual isolated failures retain source locations without exception content."""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from uuid import uuid4

import pytest
from evolution import diagnostics as d
from evolution import run
from evolution.registry import EvidenceError
from test_runner import case, make_test_root

CANARY = "private-content-must-never-be-retained"


def source(tmp_path, code="def origin():\n    raise ValueError('private-content-must-never-be-retained')\norigin()\n"):
    path = tmp_path / "mvp/tests/evolution/unit/test_source.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code)
    namespace = {}
    try:
        exec(compile(code, str(path), "exec"), namespace)  # noqa: S102 - synthetic traceback fixture
    except ValueError as error:
        return path, error
    raise AssertionError("Fixture must actually fail")


def document(tmp_path):
    path, error = source(tmp_path)
    catalog = d.SourceCatalog(tmp_path)
    value = {"schema_version": d.SCHEMA, "failures": [d.capture(error, "call", catalog)]}
    return path, value, catalog


def test_real_worker_keeps_original_context_across_from_none_without_private_data(tmp_path):
    root = make_test_root(tmp_path / "root", f'''import pytest
class ResourceBudgetError(RuntimeError):
    pass
class PrivateException(Exception):
    def __str__(self):
        raise RuntimeError("{CANARY}")
def origin():
    local_secret = "{CANARY}"
    print(local_secret)
    raise PrivateException(local_secret)
def boundary():
    try:
        origin()
    except PrivateException:
        raise ResourceBudgetError("{CANARY}") from None
def test_behavior():
    assert True
    boundary()
''')
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = run.execute_case(case(), root, destination)
    assert result["status"] == "fail"
    assert result["assertions"] == 1
    assert "diagnostic_evidence_missing_or_invalid" not in result["reason_codes"]
    raw_worker = json.loads((destination / (case().case_id + ".json")).read_text())
    assert set(raw_worker) == {"nodeid", "status", "duration_ms", "assertions", "reason_codes"}
    path = d.sidecar(destination / (case().case_id + ".json"))
    value, record = d.read_diagnostic(path, root)
    rows = value["failures"][0]["exceptions"]
    assert [row["type"] for row in rows] == ["ResourceBudgetError", "OtherError"]
    assert rows[1]["relation"] == "context"
    assert rows[1]["parent"] == 0
    assert any(frame["function"] == "origin" for frame in rows[1]["frames"])
    assert record["source_files"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert CANARY not in "".join(p.read_text() for p in destination.glob("*.json"))
    assert "PrivateException" not in path.read_text()


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_real_worker_captures_fixture_phase_without_counting_its_assertions(tmp_path, phase):
    body = f'''import pytest
@pytest.fixture
def fixture():
    {"raise ValueError('" + CANARY + "')" if phase == "setup" else "yield"}
    {"yield" if phase == "setup" else "raise ValueError('" + CANARY + "')"}
def test_behavior(fixture):
    assert True
'''
    root = make_test_root(tmp_path / "root", body)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = run.execute_case(case(), root, destination)
    assert result["status"] == "fail"
    assert result["assertions"] == (0 if phase == "setup" else 1)
    value, _ = d.read_diagnostic(d.sidecar(destination / (case().case_id + ".json")), root)
    assert [failure["phase"] for failure in value["failures"]] == [phase]
    assert CANARY not in json.dumps(value)


def test_diagnostic_chains_bound_cycles_deep_origins_and_both_relations(tmp_path):
    _, root_error = source(tmp_path)
    explicit = RuntimeError(CANARY)
    implicit = TypeError(CANARY)
    root_error.__cause__ = explicit
    root_error.__context__ = implicit
    explicit.__context__ = root_error
    value = d.capture(root_error, "call", d.SourceCatalog(tmp_path))
    assert [row["relation"] for row in value["exceptions"]] == ["root", "cause", "context"]
    assert value["truncated"] is True
    previous = root_error
    for _ in range(30):
        current = RuntimeError(CANARY)
        previous.__context__ = current
        previous = current
    bounded = d.capture(root_error, "call", d.SourceCatalog(tmp_path))
    assert len(bounded["exceptions"]) == d.MAX_EXCEPTIONS
    assert bounded["truncated"] is True
    assert CANARY not in json.dumps(bounded)


def test_diagnostic_frames_bound_recursion_and_drop_dynamic_paths_and_names(tmp_path):
    code = f'''def recurse(n):
    if n:
        recurse(n - 1)
    else:
        raise ValueError("{CANARY}")
recurse(30)
'''
    path, error = source(tmp_path, code)
    value = d.capture(error, "call", d.SourceCatalog(tmp_path))
    assert len(value["exceptions"][0]["frames"]) == d.MAX_FRAMES
    assert value["truncated"] is True
    private_type = type(CANARY, (Exception,), {})
    private_type.__module__ = CANARY
    try:
        exec(compile("raise private_type('secret')", f"/private/{CANARY}.py", "exec"),  # noqa: S102 - adversarial synthetic code
             {"private_type": private_type})
    except private_type as failure:
        dynamic = d.capture(failure, "call", d.SourceCatalog(tmp_path))
    assert dynamic["exceptions"][0]["type"] == "OtherError"
    assert CANARY not in json.dumps(dynamic)
    with pytest.raises(EvidenceError):
        d.SourceCatalog(tmp_path).frame(str(path), CANARY, 2)
    with pytest.raises(EvidenceError):
        d.SourceCatalog(tmp_path).frame(str(path), "recurse", 100000)


@pytest.mark.parametrize("mutation", ["extra", "type", "phase", "parent", "relation", "line", "file", "function", "overflow"])
def test_diagnostic_schema_rejects_nonstatic_or_malformed_metadata(tmp_path, mutation):
    _, value, catalog = document(tmp_path)
    row = value["failures"][0]["exceptions"][0]
    frame = row["frames"][-1]
    if mutation == "extra":
        row["message"] = CANARY
    elif mutation == "type":
        row["type"] = CANARY
    elif mutation == "phase":
        value["failures"][0]["phase"] = CANARY
    elif mutation == "parent":
        row["parent"] = True
    elif mutation == "relation":
        row["relation"] = "context"
    elif mutation == "line":
        frame["line"] = True
    elif mutation == "file":
        frame["file"] = "../" + CANARY
    elif mutation == "function":
        frame["function"] = CANARY
    else:
        row["frames"] *= 20
    with pytest.raises(EvidenceError):
        d.validate(value, catalog)


def test_diagnostic_files_refuse_links_fifo_public_oversize_duplicate_and_mutation(tmp_path):
    _, value, catalog = document(tmp_path / "root")
    path = tmp_path / "case.diagnostic.json"
    d.write_diagnostic(path, value["failures"], catalog)
    original = path.read_bytes()
    linked = tmp_path / "hardlink.json"
    os.link(path, linked)
    with pytest.raises(EvidenceError):
        d.read_diagnostic(path, catalog.root)
    linked.unlink()
    with pytest.raises(FileExistsError):
        d.write_diagnostic(path, value["failures"], catalog)
    path.chmod(0o644)
    with pytest.raises(EvidenceError):
        d.read_diagnostic(path, catalog.root)
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(EvidenceError):
        d.read_diagnostic(path, catalog.root)
    path.unlink()
    os.mkfifo(path, 0o600)
    with pytest.raises(EvidenceError):
        d.read_diagnostic(path, catalog.root)
    path.unlink()
    for raw in (b'x' * (d.MAX_BYTES + 1), b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff'):
        path.write_bytes(raw)
        path.chmod(0o600)
        with pytest.raises(EvidenceError):
            d.read_diagnostic(path, catalog.root)
    path.write_bytes(original)
    _, record = d.read_diagnostic(path, catalog.root)
    changed = deepcopy(value)
    changed["failures"][0]["exceptions"][0]["type"] = "RuntimeError"
    path.write_text(json.dumps(changed))
    _, changed_record = d.read_diagnostic(path, catalog.root)
    assert changed_record["sha256"] != record["sha256"]


def test_diagnostic_source_binding_and_required_presence_never_claim_success(tmp_path):
    path, value, catalog = document(tmp_path / "root")
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = {"case_id": "GOV-RUNNER-EXACT", "status": "fail", "reason_codes": ["case_call_failed"]}
    with pytest.raises(EvidenceError, match="diagnostic_evidence_missing"):
        d.check_case(destination, result["case_id"], catalog.root, result)
    diagnostic = d.sidecar(destination / (result["case_id"] + ".json"))
    d.write_diagnostic(diagnostic, value["failures"], catalog)
    expected = {path.relative_to(catalog.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()}
    assert d.collect_diagnostics(destination, catalog.root, [result], expected)
    extra = destination / "FOREIGN.diagnostic.json"
    extra.write_bytes(diagnostic.read_bytes())
    with pytest.raises(EvidenceError):
        d.collect_diagnostics(destination, catalog.root, [result], expected)
    extra.unlink()
    with pytest.raises(EvidenceError):
        d.check_case(destination, result["case_id"], catalog.root, result | {"status": "pass"})
    path.write_text(path.read_text() + "# changed source\n")
    with pytest.raises(EvidenceError):
        d.collect_diagnostics(destination, catalog.root, [result], expected)
    assert result["status"] == "fail"


def test_signed_failed_gate_binds_sidecar_and_exact_referenced_source(tmp_path, monkeypatch):
    root = make_test_root(tmp_path / "root", f'''def origin():
    raise ValueError("{CANARY}")
def test_behavior():
    assert True
    try:
        origin()
    except ValueError:
        raise RuntimeError("{CANARY}") from None
''')
    selected = case()
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    source_path = root / selected.nodeid.split("::")[0]
    manifest_source = {str(source_path.relative_to(root)): run.digest(source_path)}
    from evolution import postgres
    monkeypatch.setattr(run, "load_registry", lambda *_: (selected,))
    monkeypatch.setattr(run, "verify_environment", lambda *_: None)
    monkeypatch.setattr(run, "verify_installed_wheels", lambda *_: None)
    monkeypatch.setattr(run, "phase_artifacts", lambda *_: [])
    monkeypatch.setattr(postgres, "prepare", lambda *_: {})
    monkeypatch.setattr(run, "source_files", lambda *_: manifest_source)
    monkeypatch.setattr(run.subprocess, "check_output", lambda *_a, **_k: "2" * 40)
    assert run._run_gate(2, root, str(uuid4()), destination) == 1
    report = json.loads((destination / "report.json").read_text())
    manifest = json.loads((destination / "manifest.json").read_text())
    run.verify_report(report, (root / ".evolution/secrets/acceptance-ed25519.pub").read_bytes())
    assert report["environment"]["manifest_sha256"] == run.digest(destination / "manifest.json")
    assert set(manifest["case_diagnostics"]) == {selected.case_id + ".diagnostic.json"}
    info = manifest["case_diagnostics"][selected.case_id + ".diagnostic.json"]
    attachment = destination / (selected.case_id + ".diagnostic.json")
    assert info["sha256"] == run.digest(attachment)
    assert info["source_files"] == manifest_source
    assert report["cases"][0]["status"] == "fail"
    assert CANARY not in "".join(p.read_text() for p in destination.glob("*.json"))
    attachment.write_bytes(attachment.read_bytes() + b" ")
    assert info["sha256"] != run.digest(attachment)


@pytest.mark.parametrize("change", ["created", "modified"])
def test_real_worker_omits_input_named_or_modified_source_before_retention(tmp_path, change):
    private_function = CANARY.replace("-", "_")
    relative = f"mvp/evolution/{CANARY}.py" if change == "created" else "mvp/evolution/static_helper.py"
    hostile = f"def {private_function}():\n    raise ValueError({CANARY!r})\n{private_function}()\n"
    root = make_test_root(tmp_path / "root", f'''import runpy
from pathlib import Path
def test_behavior():
    path = Path({relative!r})
    path.write_text({hostile!r})
    assert True
    try:
        runpy.run_path(str(path))
    except ValueError:
        raise RuntimeError("{CANARY}") from None
''')
    if change == "modified":
        (root / relative).write_text("def static_origin():\n    return None\n")
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = run.execute_case(case(), root, destination)
    assert result["status"] == "fail"
    assert "diagnostic_evidence_missing_or_invalid" not in result["reason_codes"]
    path = d.sidecar(destination / (case().case_id + ".json"))
    value, _ = d.read_diagnostic(path, root)
    assert any(frame["function"] == "test_behavior"
               for row in value["failures"][0]["exceptions"] for frame in row["frames"])
    assert [row["type"] for row in value["failures"][0]["exceptions"]] == ["RuntimeError", "ValueError"]
    written = "".join(p.read_text() for p in destination.glob("*.json"))
    assert CANARY not in written
    assert private_function not in written


def test_source_catalog_requires_private_exact_pre_case_bytes_and_never_falls_back(tmp_path):
    path, _, _ = document(tmp_path / "root")
    root = tmp_path / "root"
    empty = root / "mvp/tests/evolution/unit/__init__.py"
    empty.write_bytes(b"")
    expected = d.source_snapshot(root)
    assert expected[str(empty.relative_to(root))] == hashlib.sha256(b"").hexdigest()
    target = tmp_path / "catalog.json"
    d.write_catalog(target, root, expected)
    assert d.read_catalog(target, root) == expected
    with pytest.raises(FileExistsError):
        d.write_catalog(target, root, expected)
    target.chmod(0o644)
    with pytest.raises(EvidenceError):
        d.read_catalog(target, root)
    target.unlink()
    target.symlink_to(path)
    with pytest.raises(EvidenceError):
        d.read_catalog(target, root)
    target.unlink()
    with pytest.raises(EvidenceError):
        d.read_catalog(target, root)
    target.write_bytes(b"")
    target.chmod(0o600)
    with pytest.raises(EvidenceError):
        d.read_catalog(target, root)
    path.write_text("def changed():\n    raise ValueError('secret')\n")
    with pytest.raises(EvidenceError, match="diagnostic_source_changed"):
        d.SourceCatalog(root, expected).frame(str(path), "changed", 2)
    with pytest.raises(EvidenceError):
        d.validate_catalog({"mvp/unknown.py": CANARY}, root)
