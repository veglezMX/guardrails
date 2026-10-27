"""Finite original-process observation through explicit local subprocess ports."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from evolution.diagnostics import write_catalog

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def wrapper():
    path = ROOT / "mvp/tests/evolution/e2e/test_native_connector.py"
    spec = importlib.util.spec_from_file_location("native_fixture_wrapper_port", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sources(wrapper):
    return {wrapper.PROBE: hashlib.sha256((ROOT / wrapper.PROBE).read_bytes()).hexdigest()}


def marker(wrapper):
    tree = ast.parse((ROOT / wrapper.PROBE).read_text())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    return {
        "probe_status": "fail", "stage": "gateway_readiness", "gateway_index": 0,
        "failure_bin": "gateway_readiness_timeout", "error_category": "runtime",
        "checks_completed": 0, "model_requests": 0, "runtime_requests": 0,
        "readiness_status": 503, "readiness_code": "evaluator_unavailable",
        "frames": [{"file": wrapper.PROBE, "function": "main", "line": main.lineno}],
        "cleanup_failed": False,
    }


def output(wrapper, value):
    return json.dumps({wrapper.OBSERVATION_MARKER: value}) + "\n"


def test_native_projection_retains_only_closed_child_fields_and_source_frame(wrapper):
    value = marker(wrapper)
    captured = "private-synthetic-canary\n" + output(wrapper, value)
    row = wrapper.project_observation(SimpleNamespace(returncode=1, stdout=captured), None, 35_000, sources(wrapper))
    assert row["command_outcome"] == "returned" and row["returncode"] == 1
    assert all(row[name] == item for name, item in value.items())
    assert "private-synthetic-canary" not in json.dumps(row)
    assert row["elapsed_ms"] == 35_000


@pytest.mark.parametrize("variant", ["missing", "duplicate", "malformed", "oversized", "unknown-field", "unknown-category"])
def test_native_projection_rejects_incomplete_or_untrusted_terminal_report(wrapper, variant):
    value = marker(wrapper)
    raw = output(wrapper, value)
    if variant == "missing":
        raw = "no terminal record\n"
    elif variant == "duplicate":
        raw += raw
    elif variant == "malformed":
        raw = '{"native_fixture_observation": invalid-private-synthetic-canary}\n'
    elif variant == "oversized":
        raw += "x" * wrapper.MAX_CAPTURE_BYTES
    elif variant == "unknown-field":
        value["body"] = "private-synthetic-canary"
        raw = output(wrapper, value)
    else:
        value["error_category"] = "private-synthetic-canary"
        raw = output(wrapper, value)
    row = wrapper.project_observation(SimpleNamespace(returncode=1, stdout=raw), None, 1, sources(wrapper))
    assert row["probe_status"] == "unavailable"
    assert row["failure_bin"] == ("observation_missing" if variant == "missing" else "observation_invalid")
    assert row["frames"] == [] and row["checks_completed"] is None
    assert row["model_requests"] is None and row["runtime_requests"] is None
    assert "private-synthetic-canary" not in json.dumps(row)


@pytest.mark.parametrize("variant", ["decode", "interrupted"])
def test_native_projection_does_not_infer_no_spawn_from_post_spawn_error(wrapper, variant):
    error = UnicodeDecodeError("utf8", b"\xff", 0, 1, "private-synthetic-canary") if variant == "decode" else KeyboardInterrupt("private-synthetic-canary")
    row = wrapper.project_observation(None, error, 1, sources(wrapper))
    assert row["command_outcome"] == row["failure_bin"] == "command_error"
    assert row["probe_status"] == "unavailable" and row["returncode"] is None
    assert "private-synthetic-canary" not in json.dumps(row)


def evidence_directory(wrapper, tmp_path, monkeypatch):
    report = tmp_path / "report"
    directory = report / "measurements" / wrapper.NATIVE_CASE
    directory.mkdir(parents=True, mode=0o700)
    write_catalog(report / (wrapper.NATIVE_CASE + ".sources.json"), ROOT, sources(wrapper))
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(directory))
    return directory


def test_native_wrapper_records_nonzero_child_before_assertion_and_attempts_owned_cleanup(wrapper, tmp_path, monkeypatch):
    directory = evidence_directory(wrapper, tmp_path, monkeypatch)
    calls = []
    raw = json.dumps({"status": "fail", "reason": "gateway_readiness_timeout"}) + "\n" + output(wrapper, marker(wrapper))

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1 if argv[1] == "run" else 0, stdout=raw if argv[1] == "run" else "", stderr="")

    monkeypatch.setattr(wrapper.subprocess, "run", run)
    with pytest.raises(AssertionError):
        wrapper.test_two_native_gateways_inspect_transform_block_and_reject_bypass_offline()
    row = json.loads((directory / "native-fixture-observation.json").read_text())
    assert row["failure_bin"] == "gateway_readiness_timeout" and row["returncode"] == 1
    assert len(calls) == 2 and calls[0][0][:2] == ["docker", "run"] and calls[1][0][:3] == ["docker", "rm", "--force"]
    assert calls[0][1]["timeout"] == 180 and calls[1][1]["timeout"] == 15
    assert calls[0][0][calls[0][0].index("--name") + 1] == calls[1][0][-1]


def test_native_wrapper_timeout_ignores_partial_report_and_still_attempts_owned_cleanup(wrapper, tmp_path, monkeypatch):
    directory = evidence_directory(wrapper, tmp_path, monkeypatch)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, 180, output=output(wrapper, marker(wrapper)) + "private-synthetic-canary")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(wrapper.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        wrapper.test_two_native_gateways_inspect_transform_block_and_reject_bypass_offline()
    raw = (directory / "native-fixture-observation.json").read_text()
    row = json.loads(raw)
    assert row["command_outcome"] == "timeout" and row["failure_bin"] == "command_timeout"
    assert row["probe_status"] == "unavailable" and row["returncode"] is None and row["frames"] == []
    assert "private-synthetic-canary" not in raw and len(calls) == 2
    assert calls[0][1]["timeout"] == 180 and calls[1][1]["timeout"] == 15


def test_probe_primary_failure_survives_cleanup_without_exception_stringification(wrapper):
    path = ROOT / wrapper.PROBE
    tree = ast.parse(path.read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "remember_failure"
                or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in {"PROBE_PATH", "FAILURE_TAGS"} for target in node.targets)]
    namespace = {"__file__": str(path), "httpx": httpx}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    state = marker(wrapper)
    state.update(probe_status="pass", failure_bin=None, error_category=None, frames=[])
    namespace["remember_failure"](RuntimeError("gateway_readiness_timeout"), state)
    original = dict(state)

    class PrivateError(Exception):
        def __str__(self):
            raise AssertionError("exception string must never be read")

    state["cleanup_failed"] = True
    namespace["remember_failure"](PrivateError("private-synthetic-canary"), state)
    assert state == original | {"cleanup_failed": True}
    assert state["failure_bin"] == "gateway_readiness_timeout" and state["error_category"] == "runtime"
    assert "private-synthetic-canary" not in json.dumps(state)


@pytest.mark.parametrize("variant", ["malformed", "non-object"])
def test_probe_readiness_records_current_headers_before_original_parser_failure(wrapper, variant):
    path = ROOT / wrapper.PROBE
    tree = ast.parse(path.read_text())
    readiness = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                     and node.body and isinstance(node.body[0], ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "ready" for target in node.body[0].targets))
    # Execute the exact source statements between HTTP receipt and its original
    # branch. This is a parser port, not a gateway, request or lifecycle claim.
    observed_statements = []
    for statement in readiness.body[1:]:
        if isinstance(statement, ast.If):
            break
        observed_statements.append(statement)

    def malformed_json():
        raise ValueError("private-synthetic-canary")

    ready = SimpleNamespace(status_code=503, json=malformed_json if variant == "malformed" else lambda: [])
    observation = {"readiness_status": 200, "readiness_code": "context_invalid"}
    namespace = {"ready": ready, "observation": observation}
    with pytest.raises(ValueError if variant == "malformed" else AttributeError):
        exec(compile(ast.Module(body=observed_statements, type_ignores=[]), str(path), "exec"), namespace)
    assert observation == {"readiness_status": 503, "readiness_code": None}
    assert "private-synthetic-canary" not in json.dumps(observation)
