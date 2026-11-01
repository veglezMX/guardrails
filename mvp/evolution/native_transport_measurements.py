"""Finite original Docker-probe evidence; shared by recorder and collector."""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
from pathlib import Path

from .diagnostics import SourceCatalog, read_catalog
from .registry import EvidenceError, unique_object

FILENAME = "native-transport-observation.json"
SCHEMA = "evolution-native-transport-observation-v1"
MAX_BYTES = 4096
MAX_CAPTURE_BYTES = 65536
MAX_LINES = 128
MAX_LINE_BYTES = 4096
ROW_LIMIT = 8
CASES = {
    "GW-NATIVE-TRANSPORT-PINNED-OPENAI-OLLAMA-OWNED-TRANSPORT": "native-client",
    "GW-NATIVE-PRODUCTION-MODULE-IDENTITY": "production-module",
}
SOURCES = (
    "mvp/tests/evolution/e2e/test_native_transport.py",
    "mvp/tests/evolution/e2e/native_transport_probe.py",
    "mvp/evolution/native_transport_measurements.py",
)
REASONS = frozenset({
    "gateway_start_failed", "gateway_ready_timeout", "response_status", "dispatch_count",
    "inspection_count", "redirect_followed", "shared_client_changed", "native_replayed_transport",
    "client_closed_early", "client_not_closed", "closed_client_reopened", "successful_transport_missing",
    "raw_response_hook_missing", "credential_logged", "content_logged", "response_content",
    "inspection_actions", "preflight_dispatch", "shutdown_failed", "fixture_failed",
})
TYPES = frozenset({"AssertionError", "TimeoutExpired", "ReadTimeout", "ConnectError", "ValueError", "KeyError", "other"})
STAGES = frozenset({"setup", "profile_setup", "native_requests", "native_shutdown", "shutdown_checks"})
SHUTDOWN_STAGES = frozenset({
    "running", "drain_entered", "drain_exited", "owned_close_entered", "owned_close_exited",
    "lifespan_shutdown_received", "lifespan_shutdown_complete",
})


def _require(value):
    if not value:
        raise EvidenceError("measurement_schema_invalid")


def validate_sources(root, sources):
    if root is None or type(sources) is not dict or any(name not in sources for name in SOURCES):
        raise EvidenceError("measurement_source_invalid")
    catalog = SourceCatalog(root, sources)
    for name in SOURCES:
        catalog.path(name)
        catalog.load(name)


def _directory(case_id):
    value = os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR")
    if value is None:
        return None
    path = Path(value)
    info = path.lstat()
    if (not path.is_absolute() or path.resolve() != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
            or path.name != case_id or path.parent.name != "measurements"):
        raise EvidenceError("measurement_path_invalid")
    return path, info


def frozen_sources(root, case_id):
    _require(type(case_id) is str and case_id in CASES)
    checked = _directory(case_id)
    if checked is not None:
        directory, _ = checked
        sources = read_catalog(directory.parents[1] / (case_id + ".sources.json"), root)
    else:
        sources = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCES}
    validate_sources(root, sources)
    return sources


def _rows(rows):
    _require(type(rows) is list and len(rows) <= ROW_LIMIT)
    statuses = []
    shutdowns = 0
    for row in rows:
        _require(type(row) is dict)
        if row.get("status") == "pass":
            _require(row == {"status": "pass"})
            statuses.append("pass")
        elif row.get("status") == "fail":
            _require(set(row) == {"status", "reason", "type", "stage"}
                     and type(row["reason"]) is str and row["reason"] in REASONS
                     and type(row["type"]) is str and row["type"] in TYPES
                     and type(row["stage"]) is str and row["stage"] in STAGES)
            statuses.append("fail")
        else:
            _require(set(row) == {"event", "stage", "in_flight", "closed", "server_exiting", "connections", "tasks"}
                     and row["event"] == "shutdown" and type(row["stage"]) is str
                     and row["stage"] in SHUTDOWN_STAGES and type(row["closed"]) is bool
                     and (row["server_exiting"] is None or type(row["server_exiting"]) is bool)
                     and not statuses)
            for name in ("in_flight", "connections", "tasks"):
                number = row[name]
                _require((number is None and name != "in_flight")
                         or (type(number) is int and 0 <= number <= 1_000_000))
            shutdowns += 1
    # A late cleanup error may follow the probe's pass print. Never erase it.
    _require(statuses in (["pass"], ["fail"], ["pass", "fail"]) and shutdowns <= 1)
    _require(not shutdowns or statuses == ["fail"])


def validate(value, *, case_id, root, sources, complete=False):
    _require(type(case_id) is str and case_id in CASES and type(complete) is bool
             and type(value) is dict and set(value) == {
                 "schema_version", "entrypoint", "command_outcome", "returncode", "elapsed_ms",
                 "observation", "reason", "rows",
             })
    _require(value["schema_version"] == SCHEMA and value["entrypoint"] == CASES[case_id]
             and type(value["elapsed_ms"]) in (int, float) and math.isfinite(value["elapsed_ms"])
             and 0 <= value["elapsed_ms"] <= 600000)
    outcome, code = value["command_outcome"], value["returncode"]
    _require(type(outcome) is str and outcome in {"returned", "timeout", "command_error"})
    _require((outcome == "returned" and type(code) is int and -(2**31) <= code < 2**31)
             or (outcome != "returned" and code is None))
    if value["observation"] == "complete":
        _require(outcome == "returned" and value["reason"] is None)
        _rows(value["rows"])
    else:
        _require(value["observation"] == "unavailable" and value["rows"] == [])
        expected = {"returned": {"observation_missing", "observation_invalid"},
                    "timeout": {"command_timeout"}, "command_error": {"command_error"}}
        _require(type(value["reason"]) is str and value["reason"] in expected[outcome])
    if complete:
        _require(value["observation"] == "complete" and outcome == "returned" and code == 0
                 and value["rows"] == [{"status": "pass"}])
    _require(len(json.dumps(value, sort_keys=True, allow_nan=False).encode() + b"\n") <= MAX_BYTES)
    validate_sources(root, sources)
    return value


def _pass_report(case_id):
    native = CASES[case_id] == "native-client"
    return {"status": "pass", "checks": 195 if native else 65,
            "fixture": "synthetic-native-http-providers" if native else "production-module-entrypoint",
            "profiles": 2, "model_requests": 14 if native else 8,
            "sink_requests": 0, "runtime_requests": 34 if native else 12}


def project(result, error, elapsed_ms, *, case_id):
    _require(type(case_id) is str and case_id in CASES)
    timeout = isinstance(error, subprocess.TimeoutExpired)
    outcome = "returned" if error is None else "timeout" if timeout else "command_error"
    value = {"schema_version": SCHEMA, "entrypoint": CASES[case_id], "command_outcome": outcome,
             "returncode": result.returncode if error is None and result is not None else None,
             "elapsed_ms": elapsed_ms, "observation": "unavailable",
             "reason": "observation_missing" if error is None else "command_timeout" if timeout else "command_error",
             "rows": []}
    if error is not None:
        return value  # Never inspect partial TimeoutExpired output or stringify an error.
    if result is None:
        value.update(command_outcome="command_error", reason="command_error")
        return value
    try:
        _require(type(result.stdout) is str and len(result.stdout) <= MAX_CAPTURE_BYTES
                 and len(result.stdout.encode()) <= MAX_CAPTURE_BYTES)
        lines = result.stdout.splitlines()
        _require(len(lines) <= MAX_LINES)
        rows = []
        for line in lines:
            if not line.startswith("{"):
                continue
            _require(len(line) <= MAX_LINE_BYTES and len(line.encode()) <= MAX_LINE_BYTES)
            row = json.loads(line, object_pairs_hook=unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(row) is not dict:
                continue
            if row.get("status") == "pass":
                expected = _pass_report(case_id)
                _require(row == expected and all(type(row[name]) is int for name in (
                    "checks", "profiles", "model_requests", "sink_requests", "runtime_requests")))
                rows.append({"status": "pass"})
            elif row.get("status") == "fail" or row.get("event") == "shutdown":
                rows.append(row)
            _require(len(rows) <= ROW_LIMIT)
        if not rows:
            return value
        _rows(rows)
        value.update(observation="complete", reason=None, rows=rows)
    except (EvidenceError, ValueError, TypeError, UnicodeError, RecursionError):
        value.update(observation="unavailable", reason="observation_invalid", rows=[])
    return value


def record(result, error, elapsed_ms, *, case_id, root, sources):
    value = project(result, error, elapsed_ms, case_id=case_id)
    validate(value, case_id=case_id, root=root, sources=sources)
    checked = _directory(case_id)
    if checked is not None:
        directory, checked_info = checked
        raw = json.dumps(value, sort_keys=True, allow_nan=False).encode() + b"\n"
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            def identity(info):
                return info.st_dev, info.st_ino, info.st_mode, info.st_uid
            if (identity(os.fstat(directory_fd)) != identity(checked_info)
                    or directory.resolve() != directory
                    or identity(directory.lstat()) != identity(checked_info)):
                raise EvidenceError("measurement_changed_during_write")
            fd = os.open(FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(directory_fd)
    return value
