"""Original runtime response evidence: privacy, failed workers and signed bytes."""

import hashlib
import importlib.util
import json
import os
import shutil
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from evolution import run
from evolution.diagnostics import source_snapshot, write_catalog
from evolution.measurements import (
    FILES,
    RUNTIME_ACTION_CASES,
    RUNTIME_ACTION_SCHEMA,
    RUNTIME_ACTION_SOURCES,
    collect_measurements,
    prepare_directory,
    validate_runtime_action_observation,
)
from evolution.registry import EvidenceError
from guardrails.evolution.errors import EvolutionError
from test_runner import case, make_test_root

ROOT = Path(__file__).resolve().parents[4]
HELPER = "mvp/tests/evolution/integration/runtime_action_observation.py"
CASE = "INT-RUNTIME-DECLARED-RULE-ACTION-ALLOW-OUTPUT"
CANARY = "private-action-token-body-never-retained"


@pytest.fixture
def observer_module():
    spec = importlib.util.spec_from_file_location("runtime_action_observation_port", ROOT / HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sources(root):
    result = {}
    for name in RUNTIME_ACTION_SOURCES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def public_source():\n    return None\n")
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def evidence(module, tmp_path, monkeypatch):
    root = tmp_path / "source"
    frozen = sources(root)
    monkeypatch.setattr(module, "ROOT", root)
    directory = prepare_directory(tmp_path / "report", CASE)
    write_catalog(directory.parents[1] / (CASE + ".sources.json"), root, frozen)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(directory))
    return root, directory, frozen


def response(status=504, code="evaluation_timeout", *, correlation=None, supplied=None):
    correlation = correlation or str(uuid4())
    request = httpx.Request("POST", "https://localhost/v2/evaluations",
                            headers={} if supplied is None else {"X-Request-ID": supplied})
    return httpx.Response(status, request=request,
                          headers={"X-Request-ID": correlation},
                          json=EvolutionError(code).envelope(correlation))


def sample():
    return {"schema_version": RUNTIME_ACTION_SCHEMA, "rows": [
        {"stage": "input", "http_status": 200, "wire_code": None, "elapsed_ms": 3.5},
        {"stage": "output", "http_status": 504, "wire_code": "evaluation_timeout", "elapsed_ms": 501.0},
    ]}


def test_direct_error_uses_server_correlation_and_exact_status(observer_module):
    for code in ("evaluation_timeout", "snapshot_stale", "authentication_failed", "quota_exceeded"):
        error = EvolutionError(code)
        value = response(error.status, code)
        assert not value.request.headers.get_list("x-request-id")
        assert observer_module.correlated_error_code(value) == code
        correlated = value.headers["x-request-id"]
        supplied = response(error.status, code, correlation=correlated, supplied=correlated)
        assert observer_module.correlated_error_code(supplied) == code
    assert observer_module.correlated_error_code(response(200)) is None
    assert observer_module.correlated_error_code(response(503)) is None


def test_error_projection_refuses_private_malformed_and_uncorrelated_values(observer_module):
    original = response()
    correlation = original.headers["x-request-id"]
    base = original.json()
    mutations = (
        lambda v: v.update(raw=CANARY),
        lambda v: v["error"].update(code=CANARY),
        lambda v: v["error"].update(message=CANARY),
        lambda v: v["error"].update(correlation_id=CANARY),
        lambda v: v["error"].update(correlation_id=str(uuid4())),
        lambda v: v["error"].update(retryable=0),
        lambda v: v["error"].update(details={"raw": CANARY}),
        lambda v: v["error"].update(extra=CANARY),
        lambda v: v["error"].pop("details"),
    )
    candidates = []
    for mutate in mutations:
        value = deepcopy(base)
        mutate(value)
        candidates.append(httpx.Response(504, request=original.request, json=value,
                                        headers={"X-Request-ID": correlation}))
    for raw in (b'{"error":1,"error":2}', b'{"error":NaN}', b'\xff', b'x' * 4097):
        candidates.append(httpx.Response(504, request=original.request, content=raw,
                          headers={"Content-Type": "application/json", "X-Request-ID": correlation}))
    for headers in ({}, {"X-Request-ID": CANARY},
                    [("X-Request-ID", correlation), ("X-Request-ID", correlation)]):
        candidates.append(httpx.Response(504, request=original.request, json=base, headers=headers))
    compressed = response()
    compressed.headers["content-encoding"] = "gzip"
    candidates.append(compressed)
    candidates.append(response(correlation=correlation, supplied=str(uuid4())))
    candidates.append(response(correlation=correlation, supplied=CANARY))
    projected = [observer_module.correlated_error_code(value) for value in candidates]
    assert projected == [None] * len(candidates)
    assert CANARY not in json.dumps(projected)


def test_observer_keeps_exact_original_calls_response_and_elapsed(observer_module, tmp_path, monkeypatch):
    root, directory, frozen = evidence(observer_module, tmp_path, monkeypatch)
    times = iter((1.0, 1.003, 2.0, 2.501))
    monkeypatch.setattr(observer_module, "monotonic", lambda: next(times))
    calls = []
    replies = [response(200), response()]
    def post(path, **kwargs):
        calls.append((path, kwargs))
        return replies[len(calls) - 1]
    client = SimpleNamespace(post=post)
    payloads = [{"correlation_id": str(uuid4()), "deadline_ms": 500, "text": CANARY},
                {"correlation_id": str(uuid4()), "deadline_ms": 500, "context_id": CANARY}]
    with pytest.raises(AssertionError), observer_module.RuntimeActionObservation("allow", "output") as observed:
        assert observed.post(client, "input", json=payloads[0]) is replies[0]
        result = observed.post(client, "output", json=payloads[1])
        assert result is replies[1]
        assert result.status_code == 200
    assert calls == [("/v2/evaluations", {"json": payload}) for payload in payloads]
    path = directory / FILES[CASE]
    value = json.loads(path.read_bytes())
    assert [row["elapsed_ms"] for row in value["rows"]] == [3.0, 501.0]
    assert [row["wire_code"] for row in value["rows"]] == [None, "evaluation_timeout"]
    assert CANARY not in path.read_text() and all(p["correlation_id"] not in path.read_text() for p in payloads)
    record = collect_measurements(directory.parents[1], [CASE], root=root, sources=frozen)
    assert next(iter(record.values()))["source_files"] == frozen


def test_transport_and_recording_failures_preserve_original_exception(observer_module, tmp_path, monkeypatch):
    _, directory, _ = evidence(observer_module, tmp_path, monkeypatch)
    class PrivateFailure(RuntimeError):
        def __str__(self):
            raise AssertionError("must not stringify the exception")
    original = PrivateFailure(CANARY)
    calls = []
    def fail_post(*args, **kwargs):
        calls.append(1)
        raise original
    with pytest.raises(PrivateFailure) as caught, observer_module.RuntimeActionObservation("allow", "output") as observed:
        observed.post(SimpleNamespace(post=fail_post), "input", json={})
    assert caught.value is original and calls == [1]
    value = json.loads((directory / FILES[CASE]).read_bytes())
    assert len(value["rows"]) == 1 and value["rows"][0]["http_status"] == 0
    assert value["rows"][0]["wire_code"] is None
    # A second write is forbidden. It must not replace the original failure.
    with pytest.raises(PrivateFailure) as caught, observer_module.RuntimeActionObservation("allow", "output"):
        raise original
    assert caught.value is original
    assert CANARY not in (directory / FILES[CASE]).read_text()
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"), observer_module.RuntimeActionObservation("allow", "output"):
        pass  # A successful case cannot publish an empty observation.
    with pytest.raises(FileExistsError), observer_module.RuntimeActionObservation("allow", "output") as observed:
        observed.value = sample()
        observed.value["rows"][1].update(http_status=200, wire_code=None)


def test_closed_case_schema_rejects_false_sequences_and_private_fields(tmp_path):
    frozen = sources(tmp_path)
    assert len(RUNTIME_ACTION_CASES) == 8
    for case_id, (_, stage) in RUNTIME_ACTION_CASES.items():
        value = sample()
        if stage == "input":
            value["rows"] = value["rows"][:1]
        validate_runtime_action_observation(value, case_id, root=tmp_path, sources=frozen)
        assert FILES[case_id] == "runtime-action-observation.json"
    mutations = (
        lambda v: v.update(raw=CANARY),
        lambda v: v["rows"][1].update(stage="input"),
        lambda v: v["rows"][0].update(stage="output"),
        lambda v: v["rows"][0].update(http_status=503),
        lambda v: v["rows"][1].update(http_status=True),
        lambda v: v["rows"][1].update(http_status=503),
        lambda v: v["rows"][1].update(http_status=600),
        lambda v: v["rows"][1].update(wire_code=CANARY),
        lambda v: v["rows"][1].update(elapsed_ms=float("nan")),
        lambda v: v["rows"][1].update(elapsed_ms=True),
        lambda v: v["rows"][1].update(elapsed_ms=-1),
        lambda v: v["rows"][1].update(elapsed_ms=120001),
        lambda v: v["rows"][1].update(request_id=CANARY),
        lambda v: v["rows"].append(deepcopy(v["rows"][1])),
    )
    for mutate in mutations:
        value = sample()
        mutate(value)
        with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
            validate_runtime_action_observation(value, CASE, root=tmp_path, sources=frozen)
    for invalid_case in (CASE + "-UNKNOWN", [], None):
        with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
            validate_runtime_action_observation(sample(), invalid_case, root=tmp_path, sources=frozen)
    for value in (sample(), {"schema_version": RUNTIME_ACTION_SCHEMA, "rows": []}):
        with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
            validate_runtime_action_observation(value, CASE, root=tmp_path, sources=frozen, complete=True)


def test_capture_rechecks_frozen_sources_and_never_falls_back(observer_module, tmp_path, monkeypatch):
    root, directory, _ = evidence(observer_module, tmp_path, monkeypatch)
    observed = observer_module.RuntimeActionObservation("allow", "output")
    source = root / RUNTIME_ACTION_SOURCES[0]
    source.write_text("# " + CANARY + "\n")
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        observed.write(complete=False)
    assert not (directory / FILES[CASE]).exists()
    catalog = directory.parents[1] / (CASE + ".sources.json")
    catalog.unlink()
    with pytest.raises(EvidenceError, match="diagnostic_evidence_invalid"):
        observer_module.RuntimeActionObservation("allow", "output")
    assert not (directory / FILES[CASE]).exists()


def test_collector_rejects_links_oversize_missing_and_source_tamper(tmp_path):
    root = tmp_path / "source"
    frozen = sources(root)
    report = tmp_path / "report"
    directory = prepare_directory(report, CASE)
    path = directory / FILES[CASE]
    run.write_once(path, sample())
    assert collect_measurements(report, [CASE], root=root, sources=frozen)
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        collect_measurements(report, [CASE], [CASE], root=root, sources=frozen)
    for raw in (b'{"rows":[],"rows":[]}', b'{"x":NaN}', b'x' * 4097):
        path.write_bytes(raw)
        with pytest.raises(EvidenceError):
            collect_measurements(report, [CASE], root=root, sources=frozen)
    path.write_text(json.dumps(sample()))
    path.chmod(0o644)
    with pytest.raises(EvidenceError, match="measurement_path_invalid"):
        collect_measurements(report, [CASE], root=root, sources=frozen)
    path.chmod(0o600)
    original = tmp_path / "attachment"
    path.rename(original)
    path.symlink_to(original)
    with pytest.raises(EvidenceError):
        collect_measurements(report, [CASE], root=root, sources=frozen)
    path.unlink()
    os.mkfifo(path, 0o600)
    with pytest.raises(EvidenceError, match="measurement_path_invalid"):
        collect_measurements(report, [CASE], root=root, sources=frozen)
    path.unlink()
    original.rename(path)
    (root / RUNTIME_ACTION_SOURCES[0]).write_text("# changed source\n")
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        collect_measurements(report, [CASE], root=root, sources=frozen)
    path.unlink()
    with pytest.raises(EvidenceError, match="measurement_evidence_missing"):
        collect_measurements(report, [CASE], [CASE], root=root, sources=frozen)


def worker_root(tmp_path, *, output_status):
    root = make_test_root(tmp_path, f'''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
import httpx
from uuid import uuid4
from guardrails.evolution.errors import EvolutionError
from runtime_action_observation import RuntimeActionObservation

class Client:
    calls = 0
    def post(self, path, **kwargs):
        self.calls += 1
        correlation = str(uuid4())
        return httpx.Response(200 if self.calls == 1 else {output_status},
            request=httpx.Request("POST", "https://localhost/v2/evaluations"),
            headers={{"X-Request-ID": correlation}},
            json=EvolutionError("evaluation_timeout").envelope(correlation))

def test_behavior():
    client = Client()
    with RuntimeActionObservation("allow", "output") as observed:
        first = observed.post(client, "input", json={{"raw": {CANARY!r}}})
        assert first.status_code == 200
        second = observed.post(client, "output", json={{"raw": {CANARY!r}}})
        assert second.status_code == 200, {CANARY!r}
        assert client.calls == 2
''')
    # measurements imports classifier evidence validators during collection.
    # Mirror make_test_root's complete evolution package copy so this isolated
    # worker resolves their guardrails/services imports from the same sources.
    # Copying source does not import or execute the classifier/model entrypoints.
    for package in ("mvp/guardrails", "services/classifier"):
        shutil.copytree(
            ROOT / package,
            root / package,
            ignore=shutil.ignore_patterns("__pycache__", "tests"),
        )
    for name in RUNTIME_ACTION_SOURCES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return root


def test_real_failed_worker_retains_original_response_without_exception_content(tmp_path):
    root = worker_root(tmp_path / "root", output_status=504)
    selected = replace(case(), case_id=CASE)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    result = run.execute_case(selected, root, destination)
    assert result["status"] == "fail"
    assert "case_call_failed" in result["reason_codes"]
    assert "measurement_evidence_missing_or_invalid" not in result["reason_codes"]
    observation = destination / "measurements" / CASE / FILES[CASE]
    rows = json.loads(observation.read_bytes())["rows"]
    assert [row["http_status"] for row in rows] == [200, 504]
    assert rows[-1]["wire_code"] == "evaluation_timeout"
    for path in destination.rglob("*.json"):
        assert CANARY not in path.read_text()


def test_signed_gate_binds_original_response_bytes_and_frozen_sources(tmp_path, monkeypatch):
    from evolution import postgres
    root = worker_root(tmp_path / "root", output_status=200)
    selected = replace(case(), case_id=CASE)
    destination = tmp_path / "report"
    destination.mkdir(mode=0o700)
    frozen = source_snapshot(root)
    monkeypatch.setattr(run, "load_registry", lambda *_: (selected,))
    monkeypatch.setattr(run, "verify_environment", lambda *_: None)
    monkeypatch.setattr(run, "verify_installed_wheels", lambda *_: None)
    monkeypatch.setattr(run, "phase_artifacts", lambda *_: [])
    monkeypatch.setattr(postgres, "prepare", lambda *_: {})
    monkeypatch.setattr(run, "source_files", lambda *_: frozen)
    monkeypatch.setattr(run.subprocess, "check_output", lambda *_a, **_k: "2" * 40)
    assert run._run_gate(2, root, str(uuid4()), destination) == 0
    report = json.loads((destination / "report.json").read_bytes())
    manifest = json.loads((destination / "manifest.json").read_bytes())
    run.verify_report(report, (root / ".evolution/secrets/acceptance-ed25519.pub").read_bytes())
    assert report["environment"]["manifest_sha256"] == run.digest(destination / "manifest.json")
    relative, info = next(iter(manifest["measurements"].items()))
    assert info["source_files"] == {name: frozen[name] for name in RUNTIME_ACTION_SOURCES}
    assert info["sha256"] == run.digest(destination / relative)
    value = json.loads((destination / relative).read_bytes())
    value["rows"][0]["elapsed_ms"] += 1
    (destination / relative).write_text(json.dumps(value))
    assert info["sha256"] != run.digest(destination / relative)
