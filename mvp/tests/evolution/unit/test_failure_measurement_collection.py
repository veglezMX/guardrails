"""Original-attempt attachments remain part of actual signed worker results."""

import json
import shutil
from dataclasses import replace
from uuid import uuid4

import pytest
from evolution import (
    measurements,
    native_transport_measurements,
    postgres,
    run,
    runtime_context_evidence,
)
from evolution.registry import EvidenceError
from test_runner import ROOT, case, make_test_root

FAMILIES = (runtime_context_evidence, native_transport_measurements)
CANARY = "private-fixture-content-must-not-enter-signed-evidence"


def observation(family, failed):
    if family is runtime_context_evidence:
        rows = [{"stage": "input", "http_status": 504 if failed else 200,
                 "wire_code": "evaluation_timeout" if failed else None,
                 "elapsed_ms": 499.0 if failed else 100.0}]
        if not failed:
            rows.append({"stage": "output", "http_status": 503,
                         "wire_code": "context_invalid", "elapsed_ms": 50.0})
        return {"schema_version": family.SCHEMA, "rows": rows}
    return {
        "schema_version": family.SCHEMA,
        "entrypoint": "native-client", "command_outcome": "returned",
        "returncode": 1 if failed else 0, "elapsed_ms": 100.0,
        "observation": "complete", "reason": None,
        "rows": [{"status": "fail", "reason": "fixture_failed",
                  "type": "TimeoutExpired", "stage": "native_shutdown"}]
                if failed else [{"status": "pass"}],
    }


def fixture_root(path, family, *, failed=False, missing=False):
    value = observation(family, failed)
    body = "assert True\n" if missing else f'''import json, os
from pathlib import Path
path = Path(os.environ["EVOLUTION_TEST_EVIDENCE_DIR"]) / {family.FILENAME!r}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    json.dump({value!r}, stream)
assert path.is_file()
'''
    if failed:
        body += f"raise TimeoutError({CANARY!r})\n"
    root = make_test_root(path, "def test_behavior():\n" + "".join(
        "    " + line + "\n" for line in body.splitlines()))
    for name in family.SOURCES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    selected = replace(case(), case_id=next(iter(family.CASES)))
    names = (*family.SOURCES, selected.nodeid.partition("::")[0])
    return root, selected, {name: run.digest(root / name) for name in names}


def isolated_gate(monkeypatch, root, selected, sources, destination):
    monkeypatch.setattr(run, "load_registry", lambda *_: (selected,))
    monkeypatch.setattr(run, "verify_environment", lambda *_: None)
    monkeypatch.setattr(run, "verify_installed_wheels", lambda *_: None)
    monkeypatch.setattr(run, "phase_artifacts", lambda *_: [])
    monkeypatch.setattr(postgres, "prepare", lambda *_: {})
    monkeypatch.setattr(run, "source_files", lambda *_: sources)
    monkeypatch.setattr(run.subprocess, "check_output", lambda *_a, **_k: "2" * 40)
    destination.mkdir(mode=0o700)
    return run._run_gate(2, root, str(uuid4()), destination)


@pytest.mark.parametrize("family", FAMILIES, ids=("runtime-context", "native-transport"))
@pytest.mark.parametrize("failed", (False, True), ids=("pass", "original-failure"))
def test_signed_worker_binds_original_attachment_and_sources_without_reclassifying_failure(
    tmp_path, monkeypatch, family, failed,
):
    root, selected, sources = fixture_root(tmp_path / "root", family, failed=failed)
    destination = tmp_path / "report"
    assert isolated_gate(monkeypatch, root, selected, sources, destination) == int(failed)
    report = json.loads((destination / "report.json").read_bytes())
    manifest = json.loads((destination / "manifest.json").read_bytes())
    run.verify_report(report, (root / ".evolution/secrets/acceptance-ed25519.pub").read_bytes())
    assert report["status"] == report["cases"][0]["status"] == ("fail" if failed else "pass")
    assert report["environment"]["manifest_sha256"] == run.digest(destination / "manifest.json")
    relative = f"measurements/{selected.case_id}/{family.FILENAME}"
    attachment = destination / relative
    record = manifest["measurements"][relative]
    assert record["sha256"] == run.digest(attachment)
    assert record["source_files"] == {name: sources[name] for name in family.SOURCES}
    assert json.loads(attachment.read_bytes()) == observation(family, failed)
    assert CANARY not in attachment.read_text()
    assert CANARY not in json.dumps(report) and CANARY not in json.dumps(manifest)
    changed = observation(family, failed)
    if family is runtime_context_evidence:
        changed["rows"][0]["elapsed_ms"] += 1
    else:
        changed["elapsed_ms"] += 1
    attachment.write_text(json.dumps(changed))
    assert record["sha256"] != run.digest(attachment)


@pytest.mark.parametrize("family", FAMILIES, ids=("runtime-context", "native-transport"))
def test_successful_worker_requires_original_attachment(tmp_path, monkeypatch, family):
    root, selected, sources = fixture_root(tmp_path / "root", family, missing=True)
    destination = tmp_path / "report"
    assert isolated_gate(monkeypatch, root, selected, sources, destination) == 1
    report = json.loads((destination / "report.json").read_bytes())
    run.verify_report(report, (root / ".evolution/secrets/acceptance-ed25519.pub").read_bytes())
    assert report["status"] == "fail"
    assert report["cases"][0]["status"] == "fail"
    assert "measurement_evidence_missing_or_invalid" in report["cases"][0]["reason_codes"]


@pytest.mark.parametrize("family", FAMILIES, ids=("runtime-context", "native-transport"))
def test_changed_frozen_source_is_refused_before_attachment_read(tmp_path, monkeypatch, family):
    root, selected, sources = fixture_root(tmp_path / "root", family)
    directory = measurements.prepare_directory(tmp_path / "report", selected.case_id)
    path = directory / family.FILENAME
    path.write_text(json.dumps(observation(family, False)))
    path.chmod(0o600)
    changed = root / family.SOURCES[0]
    changed.write_bytes(changed.read_bytes() + b"\n# modified after freeze\n")
    reads = []

    def forbidden_read(*args):
        reads.append(True)
        raise AssertionError("attachment_read_before_source_validation")

    monkeypatch.setattr(measurements, "_read_value", forbidden_read)
    with pytest.raises(EvidenceError):
        measurements.collect_measurements(
            tmp_path / "report", [selected.case_id], [selected.case_id], root=root, sources=sources,
        )
    assert reads == []
