"""Actual runner and signed failure report; only allocation/worker IO is fake."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from evolution import platform_database, run
from evolution.registry import Case, EvidenceError
from guardrails.evolution.models import EvidenceReport
from pydantic import ValidationError


@pytest.fixture
def runner(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "source_snapshot", lambda *_: {"source.py": "1" * 64})
    monkeypatch.setattr(run, "write_catalog", lambda *_: None)
    monkeypatch.setattr(run, "prepare_directory", lambda *_: None)
    monkeypatch.setattr(run, "MEASUREMENT_FILES", {})
    destination = tmp_path / "reports"
    destination.mkdir(mode=0o700)
    calls = []

    class Child:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
            self.returncode = 0
            Path(command[5]).write_text(json.dumps({
                "nodeid": command[4], "status": "pass", "duration_ms": 1,
                "assertions": 1, "reason_codes": [],
            }))

        def wait(self, timeout=None):
            assert timeout == 60

    monkeypatch.setattr(run.subprocess, "Popen", Child)
    for name in (
        "EVOLUTION_PLATFORM_TEST_DSN_FILE", "EVOLUTION_PLATFORM_FIXTURE_ROOT",
        "EVOLUTION_PLATFORM_OPERATION_ROOT", "EVOLUTION_TEST_DSN_FILE",
        "EVOLUTION_TEST_OWNER_DSN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    return run.execute_case, destination, calls


@pytest.mark.parametrize("exact", (True, False))
def test_only_exact_node_gets_allocator_dsn_with_worker_arguments_unchanged(
    runner, monkeypatch, tmp_path, exact,
):
    execute, destination, calls = runner
    allocated = []
    monkeypatch.setenv("EVOLUTION_PLATFORM_FIXTURE_ROOT", "/owned-root")
    monkeypatch.setenv("EVOLUTION_PLATFORM_OPERATION_ROOT", "/durable-ops")
    monkeypatch.setattr(
        platform_database, "allocate",
        lambda **kwargs: allocated.append(kwargs) or {
            platform_database.ENV: "/durable-ops/new/app-dsn",
        },
    )
    node = platform_database.NODE if exact else "mvp/tests/evolution/unit/test_fixture.py::test_behavior"
    case = Case("P3-PLATFORM-UNIT", "P3.4", "local", "remote", "deployment", node, ("Required behavior",), 60)
    result = execute(case, tmp_path, destination)
    assert result["status"] == "pass" and len(calls) == 1 and len(allocated) == int(exact)
    command, kwargs = calls[0]
    assert command[1:5] == ["-B", "-m", "evolution.worker", node]
    assert kwargs["cwd"] == tmp_path and kwargs["start_new_session"] is True
    assert kwargs["env"]["PYTHONPATH"] == str(tmp_path / "mvp")
    assert kwargs["env"].get(platform_database.ENV) == ("/durable-ops/new/app-dsn" if exact else None)
    if exact:
        assert allocated[0]["fixture_root"] == Path("/owned-root")
        assert allocated[0]["operation_root"] == Path("/durable-ops")
        assert allocated[0]["sources"] == {"source.py": "1" * 64}


@pytest.mark.parametrize("fault", ("missing-root", "external-dsn", "allocation-refusal"))
def test_preparation_failure_never_launches_worker_and_is_signed_as_failure(
    runner, monkeypatch, tmp_path, fault,
):
    execute, destination, calls = runner
    if fault != "missing-root":
        monkeypatch.setenv("EVOLUTION_PLATFORM_FIXTURE_ROOT", "/owned-root")
        monkeypatch.setenv("EVOLUTION_PLATFORM_OPERATION_ROOT", "/durable-ops")
    if fault == "external-dsn":
        monkeypatch.setenv(platform_database.ENV, "postgresql://private-canary")

    def refuse(**kwargs):
        raise EvidenceError("platform_database_allocation_failed")

    monkeypatch.setattr(platform_database, "allocate", refuse)
    case = Case(
        "P3-PLATFORM-UNIT", "P3.4", "local", "remote", "deployment",
        platform_database.NODE, ("Required behavior",), 60,
    )
    result = execute(case, tmp_path, destination)
    assert result["status"] == "fail" and result["assertions"] == 0 and calls == []
    assert result["reason_codes"] == ["platform_database_allocation_failed"]
    assert "private-canary" not in json.dumps(result)
    # The actual collector accepts an absent worker diagnostic when no worker
    # ran. The ordinary gate must still refuse this required case as a pass.
    assert run.collect_diagnostics(destination, tmp_path, [result], {"source.py": "1" * 64}) == {}
    with pytest.raises(EvidenceError, match="^required_case_failed$"):
        run.validate_results([case], [result])
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    report = {
        "run_id": str(uuid4()), "created_at": "2026-10-12T00:00:00Z",
        "source_commit": "1" * 40, "source_digest": "2" * 64, "lock_digest": "3" * 64,
        "profile": "local", "phase": 3, "started_at": "2026-10-12T00:00:00Z",
        "finished_at": "2026-10-12T00:00:01Z", "cases": [result],
        "artifact_digests": [], "status": "fail", "environment": {},
    }
    signed = run.sign_report(report, key, hashlib.sha256(public).hexdigest())
    run.verify_report(signed, public)
    parsed = EvidenceReport.model_validate_json(run.canonical(signed))
    assert parsed.status == "fail"
    assert parsed.cases[0].reason_codes == ("platform_database_allocation_failed",)
    with pytest.raises(ValidationError, match="Passing evidence requires passing asserted cases"):
        EvidenceReport.model_validate({**signed, "status": "pass"})
