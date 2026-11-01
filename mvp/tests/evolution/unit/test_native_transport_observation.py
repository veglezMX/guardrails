"""Original subprocess evidence through fake process ports and private files."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
from evolution import native_transport_measurements as observation
from evolution.diagnostics import write_catalog
from evolution.registry import EvidenceError

ROOT = Path(__file__).resolve().parents[4]
NATIVE, PRODUCTION = observation.CASES
CANARY = "synthetic-private-canary-never-retain"


@pytest.fixture
def sources():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in observation.SOURCES}


@pytest.fixture
def wrapper():
    path = ROOT / "mvp/tests/evolution/e2e/test_native_transport.py"
    spec = importlib.util.spec_from_file_location("native_transport_wrapper_port", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pass_row(case_id=NATIVE):
    native = case_id == NATIVE
    return {"status": "pass", "checks": 195 if native else 65,
            "fixture": "synthetic-native-http-providers" if native else "production-module-entrypoint",
            "profiles": 2, "model_requests": 14 if native else 8, "sink_requests": 0,
            "runtime_requests": 34 if native else 12}


def failure():
    return {"status": "fail", "reason": "fixture_failed", "type": "TimeoutExpired", "stage": "native_shutdown"}


def shutdown():
    return {"event": "shutdown", "stage": "drain_entered", "in_flight": 2,
            "closed": False, "server_exiting": True, "connections": 0, "tasks": 1}


def result(rows=(), *, code=1, raw=None):
    return subprocess.CompletedProcess([CANARY], code,
        stdout="\n".join(json.dumps(row) for row in rows) if raw is None else raw, stderr=CANARY)


def directory(tmp_path, monkeypatch, sources, case_id=NATIVE):
    report = tmp_path / "report"
    path = report / "measurements" / case_id
    path.mkdir(parents=True, mode=0o700)
    write_catalog(report / (case_id + ".sources.json"), ROOT, sources)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(path))
    return path


@pytest.mark.parametrize("case_id", [NATIVE, PRODUCTION], ids=["native", "production"])
def test_complete_pass_projection_is_bound_to_the_exact_entrypoint(case_id, sources):
    value = observation.project(result([pass_row(case_id)], code=0), None, 43, case_id=case_id)
    assert value["entrypoint"] == observation.CASES[case_id]
    assert value["rows"] == [{"status": "pass"}] and value["returncode"] == 0
    assert observation.validate(value, case_id=case_id, root=ROOT, sources=sources, complete=True) is value
    other = PRODUCTION if case_id == NATIVE else NATIVE
    with pytest.raises(EvidenceError):
        observation.validate(value, case_id=other, root=ROOT, sources=sources)


def test_failure_projection_retains_only_finite_original_shutdown_and_failure(sources):
    raw = CANARY + "\n" + json.dumps({"event": "debug", "body": CANARY}) + "\n"
    raw += "\n".join(json.dumps(row) for row in [shutdown(), failure()])
    value = observation.project(result(raw=raw), None, 38942, case_id=NATIVE)
    assert value["rows"] == [shutdown(), failure()] and value["observation"] == "complete"
    assert observation.validate(value, case_id=NATIVE, root=ROOT, sources=sources) is value
    assert CANARY not in json.dumps(value)


def test_late_cleanup_failure_after_pass_is_retained_and_cannot_supply_pass_evidence(sources):
    value = observation.project(result([pass_row(), failure()]), None, 1, case_id=NATIVE)
    assert value["rows"] == [{"status": "pass"}, failure()]
    with pytest.raises(EvidenceError):
        observation.validate(value, case_id=NATIVE, root=ROOT, sources=sources, complete=True)


@pytest.mark.parametrize("variant", [
    "duplicate-pass", "duplicate-fail", "fail-then-pass", "shutdown-then-pass", "extra-private",
    "unknown-reason", "unknown-stage", "unknown-type", "unknown-shutdown-stage", "negative-counter",
    "boolean-counter", "oversized-counter", "private-boolean", "null-inflight", "wrong-profile-pass",
    "boolean-pass-counter", "duplicate-key", "nan", "malformed", "oversized-line", "oversized-capture",
    "too-many-lines", "too-many-rows",
])
def test_untrusted_or_incomplete_terminal_metadata_becomes_fixed_unavailable(variant, sources):
    rows, raw = [failure()], None
    if variant == "duplicate-pass":
        rows = [pass_row(), pass_row()]
    elif variant == "duplicate-fail":
        rows *= 2
    elif variant == "fail-then-pass":
        rows += [pass_row()]
    elif variant == "shutdown-then-pass":
        rows = [shutdown(), pass_row()]
    elif variant == "extra-private":
        rows[0]["private"] = CANARY
    elif variant.startswith("unknown-") and variant != "unknown-shutdown-stage":
        rows[0][variant.removeprefix("unknown-")] = CANARY
    elif variant in {"unknown-shutdown-stage", "negative-counter", "boolean-counter", "oversized-counter", "private-boolean", "null-inflight"}:
        changed = shutdown()
        field, value = {
            "unknown-shutdown-stage": ("stage", CANARY), "negative-counter": ("tasks", -1),
            "boolean-counter": ("tasks", True), "oversized-counter": ("connections", 1_000_001),
            "private-boolean": ("closed", CANARY), "null-inflight": ("in_flight", None),
        }[variant]
        changed[field] = value
        rows.insert(0, changed)
    elif variant == "wrong-profile-pass":
        rows = [pass_row(PRODUCTION)]
    elif variant == "boolean-pass-counter":
        rows = [pass_row() | {"sink_requests": False}]
    elif variant == "duplicate-key":
        raw = '{"status":"fail","status":"fail","reason":"fixture_failed","type":"other","stage":"setup"}'
    elif variant == "nan":
        raw = '{"event":"private","value":NaN}'
    elif variant == "malformed":
        raw = '{' + CANARY
    elif variant == "oversized-line":
        raw = json.dumps({"event": "private", "body": CANARY * 300})
    elif variant == "oversized-capture":
        raw = "x" * (observation.MAX_CAPTURE_BYTES + 1)
    elif variant == "too-many-lines":
        raw = "\n" * (observation.MAX_LINES + 1)
    elif variant == "too-many-rows":
        rows *= observation.ROW_LIMIT + 1
    value = observation.project(result(rows, raw=raw), None, 1, case_id=NATIVE)
    assert value["observation"] == "unavailable" and value["reason"] == "observation_invalid"
    assert value["rows"] == [] and CANARY not in json.dumps(value)
    observation.validate(value, case_id=NATIVE, root=ROOT, sources=sources)


def test_missing_terminal_stdout_preserves_numeric_returncode_without_inventing_cause(sources):
    value = observation.project(result(raw=CANARY, code=125), None, 1, case_id=NATIVE)
    assert value["returncode"] == 125 and value["reason"] == "observation_missing" and value["rows"] == []
    observation.validate(value, case_id=NATIVE, root=ROOT, sources=sources)
    with pytest.raises(EvidenceError):
        observation.validate(value, case_id=NATIVE, root=ROOT, sources=sources, complete=True)


@pytest.mark.parametrize("kind", ["timeout", "decode", "interrupted", "missing-result"])
def test_no_completed_process_never_retains_partial_output_or_invents_returncode(kind, sources):
    class PrivateError(BaseException):
        def __str__(self):
            raise AssertionError("exception_string_must_not_be_read")
    error = {"timeout": subprocess.TimeoutExpired([CANARY], 210, output=CANARY, stderr=CANARY),
             "decode": UnicodeDecodeError("utf8", b"\xff", 0, 1, CANARY),
             "interrupted": PrivateError(), "missing-result": None}[kind]
    value = observation.project(None, error, 1, case_id=NATIVE)
    assert value["command_outcome"] == ("timeout" if kind == "timeout" else "command_error")
    assert value["returncode"] is None and value["rows"] == [] and CANARY not in json.dumps(value)
    observation.validate(value, case_id=NATIVE, root=ROOT, sources=sources)


@pytest.mark.parametrize("variant", ["extra-field", "boolean-code", "unknown-entrypoint", "negative-time", "nan-time", "false-pass", "missing-source", "wrong-source"])
def test_collector_validation_rejects_tampered_or_unbound_evidence(variant, sources):
    value = observation.project(result([pass_row()], code=0), None, 1, case_id=NATIVE)
    if variant == "extra-field":
        value["private"] = CANARY
    elif variant == "boolean-code":
        value["returncode"] = False
    elif variant == "unknown-entrypoint":
        value["entrypoint"] = CANARY
    elif variant == "negative-time":
        value["elapsed_ms"] = -1
    elif variant == "nan-time":
        value["elapsed_ms"] = float("nan")
    elif variant == "false-pass":
        value["returncode"] = 1
    elif variant == "missing-source":
        sources.pop(observation.SOURCES[0])
    else:
        sources[observation.SOURCES[0]] = "0" * 64
    with pytest.raises(EvidenceError):
        observation.validate(value, case_id=NATIVE, root=ROOT, sources=sources, complete=True)


def test_record_is_private_exclusive_source_bound_and_does_not_retain_raw_capture(tmp_path, monkeypatch, sources):
    path = directory(tmp_path, monkeypatch, sources)
    frozen = observation.frozen_sources(ROOT, NATIVE)
    value = observation.record(result([failure()]), None, 1, case_id=NATIVE, root=ROOT, sources=frozen)
    output = path / observation.FILENAME
    raw = output.read_bytes()
    assert json.loads(raw) == value and CANARY.encode() not in raw
    assert output.stat().st_mode & 0o777 == 0o600 and len(raw) <= observation.MAX_BYTES
    with pytest.raises(FileExistsError):
        observation.record(result([pass_row()], code=0), None, 1, case_id=NATIVE, root=ROOT, sources=frozen)
    assert output.read_bytes() == raw


@pytest.mark.parametrize("variant", ["public-directory", "wrong-case", "symlink-directory", "symlink-file", "stale-source"])
def test_record_refuses_unsafe_evidence_destinations_and_stale_source(tmp_path, monkeypatch, sources, variant):
    path = directory(tmp_path, monkeypatch, sources)
    if variant == "public-directory":
        path.chmod(0o755)
    elif variant == "wrong-case":
        other = path.parent / "wrong-case"
        other.mkdir(mode=0o700)
        monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(other))
    elif variant == "symlink-directory":
        alias = path.parent / "alias"
        alias.symlink_to(path, target_is_directory=True)
        monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(alias))
    elif variant == "symlink-file":
        target = tmp_path / "untouched"
        target.write_text(CANARY)
        (path / observation.FILENAME).symlink_to(target)
    else:
        sources[observation.SOURCES[0]] = "0" * 64
    with pytest.raises((EvidenceError, OSError)):
        observation.record(result([failure()]), None, 1, case_id=NATIVE, root=ROOT, sources=sources)
    if variant == "symlink-file":
        assert target.read_text() == CANARY
    else:
        assert not (path / observation.FILENAME).exists()


@pytest.mark.parametrize("variant", ["case", "ancestor"])
def test_record_refuses_directory_replacement_before_descriptor_open(tmp_path, monkeypatch, sources, variant):
    path = directory(tmp_path, monkeypatch, sources)
    original_open = os.open
    swapped = False
    moved = path.with_name("original-case") if variant == "case" else path.parent.with_name("original-measurements")
    def open_file(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == path and flags & os.O_DIRECTORY:
            assert not swapped
            swapped = True
            if variant == "case":
                path.rename(moved)
            else:
                path.parent.rename(moved)
                path.parent.mkdir(mode=0o700)
            path.mkdir(mode=0o700)
        return original_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(observation.os, "open", open_file)
    with pytest.raises(EvidenceError, match="measurement_changed_during_write"):
        observation.record(result([failure()]), None, 1, case_id=NATIVE, root=ROOT, sources=sources)
    original = moved if variant == "case" else moved / NATIVE
    assert swapped and not (path / observation.FILENAME).exists()
    assert not (original / observation.FILENAME).exists()


def test_directory_descriptor_prevents_redirection_after_identity_check(tmp_path, monkeypatch, sources):
    path = directory(tmp_path, monkeypatch, sources)
    moved = path.with_name("original-case")
    original_open = os.open
    def open_file(name, flags, *args, **kwargs):
        if name == observation.FILENAME:
            assert type(kwargs.get("dir_fd")) is int
            path.rename(moved)
            path.mkdir(mode=0o700)
        return original_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(observation.os, "open", open_file)
    observation.record(result([failure()]), None, 1, case_id=NATIVE, root=ROOT, sources=sources)
    assert not (path / observation.FILENAME).exists()
    assert json.loads((moved / observation.FILENAME).read_bytes())["rows"] == [failure()]


@pytest.mark.parametrize("production", [False, True], ids=["native", "production"])
def test_wrapper_observes_one_original_nonzero_command_after_exact_cleanup(wrapper, tmp_path, monkeypatch, sources, production):
    case_id = PRODUCTION if production else NATIVE
    path = directory(tmp_path, monkeypatch, sources, case_id)
    calls = []
    completed = result([failure()])
    def run(command, **options):
        assert not (path / observation.FILENAME).exists()
        calls.append((command, options))
        if command[1] == "run":
            assert options == {"capture_output": True, "text": True, "timeout": 210, "check": False}
            assert command[-1] == ("--production-entrypoint" if production else "/evolution/tests/evolution/e2e/native_transport_probe.py")
            assert command[command.index("--volume")+1] == str(ROOT / "mvp") + ":/evolution:ro"
            return completed
        assert options == {"capture_output": True, "timeout": 10 if command[1] == "rm" else 5, "check": False}
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(wrapper.subprocess, "run", run)
    with pytest.raises(AssertionError):
        wrapper.run_probe(production_entrypoint=production)
    value = json.loads((path / observation.FILENAME).read_bytes())
    assert value["returncode"] == 1 and value["rows"] == [failure()]
    assert [command[1] for command, _ in calls] == ["run", "rm", "ps"]
    name = calls[0][0][calls[0][0].index("--name")+1]
    assert calls[1][0][-1] == name and "name=^/" + name + "$" in calls[2][0]


@pytest.mark.parametrize("command_failure", [False, True], ids=["returned", "timeout"])
def test_wrapper_preserves_original_command_or_cleanup_failure_precedence(wrapper, tmp_path, monkeypatch, sources, command_failure):
    path = directory(tmp_path, monkeypatch, sources)
    primary = subprocess.TimeoutExpired([CANARY], 210, output=CANARY)
    cleanup = RuntimeError(CANARY)
    calls = []
    def run(command, **options):
        calls.append(command[1])
        if command[1] == "run":
            if command_failure:
                raise primary
            return result([failure()])
        raise cleanup  # Original finally cleanup exception takes precedence.
    monkeypatch.setattr(wrapper.subprocess, "run", run)
    with pytest.raises(RuntimeError) as raised:
        wrapper.run_probe()
    assert raised.value is cleanup and calls == ["run", "rm"]
    value = json.loads((path / observation.FILENAME).read_bytes())
    assert value["command_outcome"] == ("timeout" if command_failure else "returned")
    assert CANARY not in json.dumps(value)


def test_wrapper_timeout_preserves_same_exception_and_original_cleanup(wrapper, tmp_path, monkeypatch, sources):
    path = directory(tmp_path, monkeypatch, sources)
    primary = subprocess.TimeoutExpired([CANARY], 210, output=json.dumps(pass_row()), stderr=CANARY)
    calls = []
    def run(command, **options):
        calls.append(command[1])
        if command[1] == "run":
            raise primary
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(wrapper.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        wrapper.run_probe()
    assert raised.value is primary and calls == ["run", "rm", "ps"]
    value = json.loads((path / observation.FILENAME).read_bytes())
    assert value["command_outcome"] == "timeout" and value["rows"] == [] and value["returncode"] is None


@pytest.mark.parametrize("primary", [False, True], ids=["recorder-fails-closed", "primary-preserved"])
def test_recorder_fault_never_replaces_an_active_original_exception(wrapper, tmp_path, monkeypatch, sources, primary):
    directory(tmp_path, monkeypatch, sources)
    original = subprocess.TimeoutExpired([CANARY], 210)
    recorder = EvidenceError("measurement_path_invalid")
    def run(command, **options):
        if command[1] == "run":
            if primary:
                raise original
            return result([pass_row()], code=0)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
    def record(*args, **kwargs):
        raise recorder
    monkeypatch.setattr(wrapper.subprocess, "run", run)
    monkeypatch.setattr(wrapper.observation, "record", record)
    with pytest.raises((subprocess.TimeoutExpired, EvidenceError)) as raised:
        wrapper.run_probe()
    assert raised.value is (original if primary else recorder)


@pytest.mark.parametrize("variant", ["nonzero", "invalid-json"])
def test_recorder_fault_preserves_original_post_cleanup_failure(wrapper, tmp_path, monkeypatch, sources, variant):
    directory(tmp_path, monkeypatch, sources)
    completed = result([failure()]) if variant == "nonzero" else result(raw="{" + CANARY, code=0)
    recorder = EvidenceError("measurement_path_invalid")
    calls = []
    def run(command, **options):
        calls.append(command[1])
        return completed if command[1] == "run" else subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
    def record(*args, **kwargs):
        assert calls == ["run", "rm", "ps"]
        raise recorder
    monkeypatch.setattr(wrapper.subprocess, "run", run)
    monkeypatch.setattr(wrapper.observation, "record", record)
    with pytest.raises(AssertionError if variant == "nonzero" else json.JSONDecodeError) as raised:
        wrapper.run_probe()
    assert raised.value is not recorder and calls == ["run", "rm", "ps"]
    if variant == "nonzero":
        assert raised.value.args == ([failure()],)
    else:
        assert raised.value.doc == completed.stdout


def test_wrapper_original_return_values_and_assertions_remain_authoritative(wrapper, tmp_path, monkeypatch, sources):
    directory(tmp_path, monkeypatch, sources)
    original_rows = [pass_row(), {"event": "case", "kind": "clean", "status": 200}]
    def run(command, **options):
        return result(original_rows, code=0) if command[1] == "run" else subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
    monkeypatch.setattr(wrapper.subprocess, "run", run)
    records, reports = wrapper.run_probe()
    assert records == original_rows and reports == [pass_row()]
