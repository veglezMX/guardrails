"""Closed, metadata-only measurement attachments for acceptance reports."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path
from types import MappingProxyType

from guardrails.evolution.errors import STATUS

from . import (
    classifier_benchmark_measurements,
    classifier_deployed_collection,
    classifier_loaded_measurements,
    native_transport_measurements,
    runtime_context_evidence,
    stage_measurements,
)
from .diagnostics import SourceCatalog
from .registry import EvidenceError, unique_object

NOISY_CASE = "TEN-RUNTIME-NOISY-NEIGHBOR-MEASURED-THROUGHPUT"
REAL_MODEL_CASE = "INT-REAL-MODEL-TWENTY-REAL-OLLAMA-CALLS-PRESERVE-EXACT-POLICY-AUDIT-AND-PRIVATE-CONTENT"
NATIVE_CASE = "GW-CONNECTOR-TWO-NATIVE-GATEWAYS-INSPECT-TRANSFORM-BLOCK-AND-REJECT-BYPASS-OFFLINE"
NATIVE_SOURCE = "mvp/tests/evolution/e2e/native_gateway_probe.py"
NATIVE_SCHEMA = "evolution-native-fixture-observation-v1"
MATRIX_SCHEMA = "evolution-cluster-matrix-observation-v1"
MATRIX_SCHEMA_V2 = "evolution-cluster-matrix-observation-v2"
MATRIX_READINESS_ROLES = (
    ("management", None), ("runtime", "A"), ("gateway", "A"),
    ("runtime", "B"), ("gateway", "B"),
)
MATRIX_SOURCES = (
    "mvp/tests/evolution/e2e/test_cluster_gateways.py",
    "mvp/tests/evolution/e2e/cluster_support.py",
    "examples/evolution/client.py",
)
MATRIX_CASES = {
    "GW-CLUSTER-GATEWAYS-DEPLOYED-INPUT-OUTPUT-STATE-MATRIX-EXACT-DISPATCH-AND-ATTRIBUTION-TEAM-"
    + side + "-" + state.upper(): (side, state)
    for side in ("A", "B") for state in ("observe", "warn", "redact")
}
RUNTIME_ACTION_SCHEMA = "evolution-runtime-action-observation-v1"
RUNTIME_ACTION_LIMIT = 4096
RUNTIME_ACTION_CODES = MappingProxyType(dict(STATUS))
RUNTIME_ACTION_CASES = {
    "INT-RUNTIME-DECLARED-RULE-ACTION-" + action.upper() + "-" + stage.upper(): (action, stage)
    for action in ("allow", "warn", "redact", "block") for stage in ("input", "output")
}
RUNTIME_ACTION_SOURCES = (
    "mvp/tests/evolution/integration/test_runtime_actions.py",
    "mvp/tests/evolution/integration/runtime_action_observation.py",
    "mvp/tests/evolution/integration/test_evaluation_runtime.py",
    "mvp/guardrails/evolution/errors.py",
    "mvp/guardrails/evolution/runtime_api.py",
)
NATIVE_STAGES = frozenset({
    "tls_setup", "runtime_start", "model_start", "gateway_start", "gateway_readiness",
    "benign", "actions", "bypass", "disconnect", "recovery", "privacy", "complete", "unavailable",
})
NATIVE_CODES = dict(STATUS)
NATIVE_REASONS = frozenset({
    "gateway_start_failed", "gateway_readiness_timeout", "fixture_server_start_failed",
    "native_action_status", "input_block_dispatch", "raw_fixture_in_gateway_logs",
    "native_disconnect_start", "native_disconnect_cancel", "cancelled_response_not_inspected_or_released",
    "probe_failed", *("benign_" + code for code in NATIVE_CODES),
})
FILES = {NOISY_CASE: "noisy-neighbor.json", REAL_MODEL_CASE: "real-model-observation.json",
         NATIVE_CASE: "native-fixture-observation.json",
         **dict.fromkeys(MATRIX_CASES, "cluster-matrix-observation.json"),
         **dict.fromkeys(RUNTIME_ACTION_CASES, "runtime-action-observation.json"),
         **dict.fromkeys(native_transport_measurements.CASES, native_transport_measurements.FILENAME),
         **dict.fromkeys(runtime_context_evidence.CASES, runtime_context_evidence.FILENAME),
         **dict.fromkeys(classifier_benchmark_measurements.CASES, classifier_benchmark_measurements.FILENAME),
         **dict.fromkeys(classifier_loaded_measurements.CASES, classifier_loaded_measurements.FILENAME),
         **classifier_deployed_collection.FILES}
FIXED = {
    "schema_version": "evolution-noisy-neighbor-v2",
    "window_seconds": 30,
    "b_evaluations_per_second": 5,
    "a_concurrent_attempts_per_burst": 8,
    "a_bursts": 30,
    "text_bytes": 1024,
    "runtime_budget_ms": 500,
}
GROUPS = ("baseline_b", "contention_b", "contention_a")
COUNTS = (
    "scheduled", "correct_terminal", "correct_within_window",
    "quota_rejections", "queue_rejections", "operational_errors",
)
FLAGS = ("audit_checked", "audit_consistent", "leases_terminal")
RESPONSE_BINS = (
    "correct_decision", "operational_block", "incorrect_decision",
    "invalid_decision", "invalid_error", "transport_error", *sorted(STATUS),
)
CLOCK_INTERVALS = ("baseline", "lease_wait", "contention")
MEASUREMENT_VERSIONS = ("evolution-noisy-neighbor-v2", "evolution-noisy-neighbor-v3")
FAILURE_SAMPLE_LIMIT = 8


def _number(value):
    return type(value) in (int, float) and 0 <= value <= 1e12 and math.isfinite(value)


def validate_runtime_action_observation(value, case_id, *, root, sources, complete=False):
    """At most two original direct-runtime responses; never content or identifiers."""
    if (type(case_id) is not str or case_id not in RUNTIME_ACTION_CASES
            or type(value) is not dict or set(value) != {"schema_version", "rows"}
            or value["schema_version"] != RUNTIME_ACTION_SCHEMA
            or type(value["rows"]) is not list or root is None or type(sources) is not dict
            or type(complete) is not bool):
        raise EvidenceError("measurement_schema_invalid")
    expected = ["input"] if RUNTIME_ACTION_CASES[case_id][1] == "input" else ["input", "output"]
    rows = value["rows"]
    if len(rows) > len(expected) or complete and len(rows) != len(expected):
        raise EvidenceError("measurement_schema_invalid")
    for ordinal, row in enumerate(rows):
        if type(row) is not dict or set(row) != {"stage", "http_status", "wire_code", "elapsed_ms"}:
            raise EvidenceError("measurement_schema_invalid")
        status, code = row["http_status"], row["wire_code"]
        if (type(row["stage"]) is not str or row["stage"] != expected[ordinal] or type(status) is not int
                or not (status == 0 or 100 <= status <= 599)
                or code is not None and (type(code) is not str or code not in RUNTIME_ACTION_CODES
                    or status in (0, 200) or RUNTIME_ACTION_CODES[code] != status)
                or not _number(row["elapsed_ms"]) or row["elapsed_ms"] > 120000
                or (complete or ordinal < len(rows) - 1) and status != 200):
            raise EvidenceError("measurement_schema_invalid")
    catalog = SourceCatalog(root, sources)
    try:
        for name in RUNTIME_ACTION_SOURCES:
            if name not in sources:
                raise EvidenceError("measurement_schema_invalid")
            catalog.path(name)
            catalog.load(name)
    except EvidenceError:
        raise EvidenceError("measurement_schema_invalid") from None


def validate_matrix_readiness(value):
    """Only fixed endpoint roles and metadata from the original readiness GETs."""
    if (type(value) is not dict or set(value) != {"state", "rows"}
            or type(value["state"]) is not str or value["state"] not in {"unavailable", "partial", "complete"}
            or type(value["rows"]) is not list or len(value["rows"]) > len(MATRIX_READINESS_ROLES)):
        raise EvidenceError("measurement_schema_invalid")
    rows = value["rows"]
    if (value["state"] == "unavailable" and rows
            or value["state"] == "complete" and len(rows) != len(MATRIX_READINESS_ROLES)):
        raise EvidenceError("measurement_schema_invalid")
    for index, row in enumerate(rows):
        if type(row) is not dict or set(row) != {
            "endpoint_role", "tenant_side", "attempts", "http_status", "elapsed_ms",
        }:
            raise EvidenceError("measurement_schema_invalid")
        role, side = MATRIX_READINESS_ROLES[index]
        if (type(row["endpoint_role"]) is not str or row["endpoint_role"] != role
                or row["tenant_side"] != side or (side is not None and type(row["tenant_side"]) is not str)
                or type(row["attempts"]) is not int or not 1 <= row["attempts"] <= 256
                or type(row["http_status"]) is not int
                or not (row["http_status"] == 0 or 100 <= row["http_status"] <= 599)
                or not _number(row["elapsed_ms"]) or row["elapsed_ms"] > 60000
                or (index < len(rows) - 1 or value["state"] == "complete") and row["http_status"] != 200):
            raise EvidenceError("measurement_schema_invalid")


def validate_matrix_observation(value, case_id, *, root, sources, complete=False):
    """Original readiness and sample calls only; no identities, URLs or bodies."""
    keys = {"schema_version", "tenant_side", "state", "actual_attempts", "completed", "rows"}
    if (type(case_id) is not str or case_id not in MATRIX_CASES or type(value) is not dict
            or type(value.get("schema_version")) is not str
            or value["schema_version"] not in {MATRIX_SCHEMA, MATRIX_SCHEMA_V2}
            or set(value) != keys | ({"readiness"} if value["schema_version"] == MATRIX_SCHEMA_V2 else set())
            or (value["tenant_side"], value["state"]) != MATRIX_CASES[case_id]
            or type(value["actual_attempts"]) is not int or type(value["completed"]) is not int
            or type(value["rows"]) is not list or not 0 <= len(value["rows"]) <= 2
            or value["actual_attempts"] != len(value["rows"])
            or root is None or type(sources) is not dict or type(complete) is not bool):
        raise EvidenceError("measurement_schema_invalid")
    if value["schema_version"] == MATRIX_SCHEMA_V2:
        validate_matrix_readiness(value["readiness"])
    if complete and (value["schema_version"] != MATRIX_SCHEMA_V2
                     or value["readiness"]["state"] != "complete"
                     or value["actual_attempts"] != 2 or value["completed"] != 2):
        raise EvidenceError("measurement_schema_invalid")
    completed = 0
    for ordinal, row in enumerate(value["rows"]):
        if type(row) is not dict or set(row) != {
            "call_ordinal", "tenant_side", "http_status", "wire_code", "elapsed_ms",
            "sample_completed", "outcome", "sample_error",
        }:
            raise EvidenceError("measurement_schema_invalid")
        status, code, outcome = row["http_status"], row["wire_code"], row["outcome"]
        if (type(row["call_ordinal"]) is not int or row["call_ordinal"] != ordinal
                or row["tenant_side"] != value["tenant_side"]
                or type(status) is not int or not (status == 0 or 100 <= status <= 599)
                or code is not None and (type(code) is not str or code not in STATUS or status in (0, 200))
                or not _number(row["elapsed_ms"]) or row["elapsed_ms"] > 600000
                or type(row["sample_completed"]) is not bool
                or type(outcome) is not str or outcome not in {"returned", "evolution_error", "cancelled", "other_error"}
                or row["sample_error"] is not None and (type(row["sample_error"]) is not str or row["sample_error"] not in STATUS)
                or outcome == "returned" and (not row["sample_completed"] or status != 200 or row["sample_error"] is not None)
                or row["sample_completed"] and status != 200
                or outcome != "returned" and ordinal != len(value["rows"]) - 1
                or outcome != "evolution_error" and row["sample_error"] is not None
                or complete and outcome != "returned"):
            raise EvidenceError("measurement_schema_invalid")
        completed += row["sample_completed"]
    if value["completed"] != completed:
        raise EvidenceError("measurement_schema_invalid")
    catalog = SourceCatalog(root, sources)
    try:
        for name in MATRIX_SOURCES:
            if name not in sources:
                raise EvidenceError("measurement_schema_invalid")
            catalog.path(name)
            catalog.load(name)
    except (EvidenceError, KeyError, TypeError, ValueError):
        raise EvidenceError("measurement_schema_invalid") from None


def validate_native_fixture_observation(value, *, root, sources):
    """Closed original-call facts, not case success; cleanup_failed is child-only."""
    fields = {
        "schema_version", "command_outcome", "returncode", "elapsed_ms", "probe_status",
        "stage", "gateway_index", "failure_bin", "error_category", "checks_completed",
        "model_requests", "runtime_requests", "readiness_status", "readiness_code", "frames", "cleanup_failed",
    }
    if (type(value) is not dict or set(value) != fields or value["schema_version"] != NATIVE_SCHEMA
            or root is None or type(sources) is not dict or NATIVE_SOURCE not in sources):
        raise EvidenceError("measurement_schema_invalid")
    outcome, status = value["command_outcome"], value["probe_status"]
    if (type(outcome) is not str or outcome not in {"returned", "timeout", "command_error"}
            or type(status) is not str or status not in {"pass", "fail", "unavailable"}
            or not _number(value["elapsed_ms"])
            or type(value["stage"]) is not str or value["stage"] not in NATIVE_STAGES):
        raise EvidenceError("measurement_schema_invalid")
    returncode = value["returncode"]
    if ((outcome == "returned" and (type(returncode) is not int or not -64 <= returncode <= 255))
            or (outcome != "returned" and returncode is not None)):
        raise EvidenceError("measurement_schema_invalid")
    for name, maximum in (("gateway_index", 1), ("checks_completed", 57),
                          ("model_requests", 1000), ("runtime_requests", 1000)):
        number = value[name]
        if number is not None and (type(number) is not int or not 0 <= number <= maximum):
            raise EvidenceError("measurement_schema_invalid")
    readiness, code = value["readiness_status"], value["readiness_code"]
    if (readiness is not None and (type(readiness) is not int or not (readiness == 0 or 100 <= readiness <= 599))
            or code is not None and (type(code) is not str or code not in NATIVE_CODES
                                    or readiness != NATIVE_CODES[code])):
        raise EvidenceError("measurement_schema_invalid")
    reason, category = value["failure_bin"], value["error_category"]
    if status == "unavailable":
        bins = {"returned": {"observation_missing", "observation_invalid"},
                "timeout": {"command_timeout"}, "command_error": {"command_error"}}
        if (type(reason) is not str or reason not in bins[outcome] or value["stage"] != "unavailable"
                or any(value[name] is not None for name in (
                    "gateway_index", "checks_completed", "model_requests", "runtime_requests",
                    "readiness_status", "readiness_code", "error_category", "cleanup_failed",
                )) or value["frames"] != []):
            raise EvidenceError("measurement_schema_invalid")
    elif (outcome != "returned" or value["stage"] == "unavailable" or type(value["cleanup_failed"]) is not bool
          or status == "pass" and (reason is not None or category is not None
                                   or value["stage"] != "complete" or value["checks_completed"] != 57
                                   or value["frames"] != [] or value["cleanup_failed"]
                                   or value["model_requests"] is None or value["runtime_requests"] is None)
          or status == "fail" and (type(reason) is not str or reason not in NATIVE_REASONS
                                   or type(category) is not str or category not in {
                                       "assertion", "runtime", "transport_timeout", "transport_error", "other",
                                   })):
        raise EvidenceError("measurement_schema_invalid")
    frames = value["frames"]
    if type(frames) is not list or len(frames) > 8:
        raise EvidenceError("measurement_schema_invalid")
    catalog = SourceCatalog(root, sources)
    try:
        catalog.path(NATIVE_SOURCE)
        catalog.load(NATIVE_SOURCE)
        for frame in frames:
            if (type(frame) is not dict or set(frame) != {"file", "function", "line"}
                    or frame["file"] != NATIVE_SOURCE
                    or catalog.frame(frame["file"], frame["function"], frame["line"]) != frame):
                raise EvidenceError("measurement_schema_invalid")
    except (EvidenceError, KeyError, TypeError, ValueError):
        raise EvidenceError("measurement_schema_invalid") from None


def validate_real_model_observation(value):
    """Fixed twenty-call diagnostic; identifiers and error text are forbidden."""
    if (not isinstance(value, dict) or set(value) != {
            "schema_version", "expected_calls", "actual_attempts", "completed", "rows",
        } or value["schema_version"] != "evolution-real-model-observation-v1"
            or type(value["expected_calls"]) is not int or value["expected_calls"] != 20
            or type(value["actual_attempts"]) is not int
            or type(value["completed"]) is not int
            or not isinstance(value["rows"], list)
            or not 0 <= len(value["rows"]) <= 20
            or value["actual_attempts"] != len(value["rows"])):
        raise EvidenceError("measurement_schema_invalid")
    completed = 0
    for ordinal, row in enumerate(value["rows"]):
        if not isinstance(row, dict) or set(row) != {
            "call_ordinal", "tenant_side", "http_status", "wire_code",
            "elapsed_ms", "sample_completed",
        }:
            raise EvidenceError("measurement_schema_invalid")
        status, code, success = row["http_status"], row["wire_code"], row["sample_completed"]
        if (type(row["call_ordinal"]) is not int or row["call_ordinal"] != ordinal
                or row["tenant_side"] != ("A" if ordinal % 2 == 0 else "B")
                or type(status) is not int or not (status == 0 or 100 <= status <= 599)
                or (code is not None and (not isinstance(code, str) or code not in STATUS))
                or (status in {0, 200} and code is not None)
                or not _number(row["elapsed_ms"])
                or type(success) is not bool or (success and status != 200)
                or (not success and ordinal != len(value["rows"]) - 1)):
            raise EvidenceError("measurement_schema_invalid")
        completed += success
    if value["completed"] != completed:
        raise EvidenceError("measurement_schema_invalid")


def _validate_failure_samples(group):
    samples = group["failure_samples"]
    if not isinstance(samples, dict) or set(samples) != {"rows", "omitted"}:
        raise EvidenceError("measurement_schema_invalid")
    rows, omitted = samples["rows"], samples["omitted"]
    failed = group["scheduled"] - group["correct_terminal"]
    if (not isinstance(rows, list) or len(rows) != min(FAILURE_SAMPLE_LIMIT, failed)
            or type(omitted) is not int or omitted != failed - len(rows)):
        raise EvidenceError("measurement_schema_invalid")
    previous = -1
    observed = dict.fromkeys(RESPONSE_BINS, 0)
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "ordinal", "replica", "status", "response_bin", "elapsed_ms", "launch_lag_ms",
        }:
            raise EvidenceError("measurement_schema_invalid")
        ordinal, replica, status = row["ordinal"], row["replica"], row["status"]
        response = row["response_bin"]
        if (type(ordinal) is not int or not previous < ordinal < group["scheduled"]
                or type(replica) is not int or replica != ordinal % 2
                or type(status) is not int or not (status == 0 or 100 <= status <= 599)
                or not isinstance(response, str) or response not in RESPONSE_BINS
                or response == "correct_decision"
                or not _number(row["elapsed_ms"]) or not _number(row["launch_lag_ms"])
                or row["launch_lag_ms"] > group["max_launch_lag_ms"] + 0.002):
            raise EvidenceError("measurement_schema_invalid")
        expected_status = STATUS.get(response)
        if response in {"operational_block", "incorrect_decision", "invalid_decision"}:
            expected_status = 200
        elif response == "transport_error":
            expected_status = 0
        if ((expected_status is not None and status != expected_status)
                or (response == "invalid_error" and status in {0, 200})):
            raise EvidenceError("measurement_schema_invalid")
        previous = ordinal
        observed[response] += 1
    if any(count > group["response_counts"][name] for name, count in observed.items()):
        raise EvidenceError("measurement_schema_invalid")


def validate_measurement(value):
    """Reject free text and malformed numbers before retaining a signed digest."""
    expected = set(FIXED) | set(GROUPS) | set(FLAGS) | {
        "quota_rejection_bursts", "b_throughput_ratio", "clock_intervals",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise EvidenceError("measurement_schema_invalid")
    if (value["schema_version"] not in MEASUREMENT_VERSIONS
            or any(type(value[k]) is not type(v) or value[k] != v
                   for k, v in FIXED.items() if k != "schema_version")):
        raise EvidenceError("measurement_schema_invalid")
    if any(type(value[k]) is not bool for k in FLAGS):
        raise EvidenceError("measurement_schema_invalid")
    bursts = value["quota_rejection_bursts"]
    if type(bursts) is not int or not 0 <= bursts <= FIXED["a_bursts"]:
        raise EvidenceError("measurement_schema_invalid")
    for name in GROUPS:
        row = value[name]
        extra = {"failure_samples"} if value["schema_version"] == MEASUREMENT_VERSIONS[1] else set()
        if not isinstance(row, dict) or set(row) != set(COUNTS) | {
            "client_ms", "max_launch_lag_ms", "response_counts",
        } | extra:
            raise EvidenceError("measurement_schema_invalid")
        if any(type(row[k]) is not int or not 0 <= row[k] <= 10000 for k in COUNTS):
            raise EvidenceError("measurement_schema_invalid")
        scheduled = 240 if name == "contention_a" else 150
        if row["scheduled"] != scheduled or not (
            row["correct_within_window"] <= row["correct_terminal"]
            <= row["scheduled"]
        ) or sum(row[k] for k in (
            "correct_terminal", "quota_rejections", "queue_rejections", "operational_errors",
        )) > row["scheduled"]:
            raise EvidenceError("measurement_schema_invalid")
        responses = row["response_counts"]
        if (not isinstance(responses, dict) or set(responses) != set(RESPONSE_BINS)
                or any(type(n) is not int or not 0 <= n <= scheduled
                       for n in responses.values())
                or sum(responses.values()) != scheduled
                or responses["correct_decision"] != row["correct_terminal"]
                or responses["quota_exceeded"] != row["quota_rejections"]
                or responses["queue_full"] != row["queue_rejections"]
                or row["operational_errors"] != scheduled - sum(responses[k] for k in (
                    "correct_decision", "quota_exceeded", "queue_full",
                ))):
            raise EvidenceError("measurement_schema_invalid")
        latency = row["client_ms"]
        if not isinstance(latency, dict) or set(latency) != {"p50", "p95", "p99"}:
            raise EvidenceError("measurement_schema_invalid")
        if not all(_number(v) for v in latency.values()) or not (
            latency["p50"] <= latency["p95"] <= latency["p99"]
        ) or not _number(row["max_launch_lag_ms"]):
            raise EvidenceError("measurement_schema_invalid")
        if extra:
            _validate_failure_samples(row)
    clocks = value["clock_intervals"]
    if not isinstance(clocks, dict) or set(clocks) != set(CLOCK_INTERVALS):
        raise EvidenceError("measurement_schema_invalid")
    for interval in clocks.values():
        if not isinstance(interval, dict) or set(interval) != {
            "wall_elapsed_ms", "monotonic_elapsed_ms", "max_divergence_ms", "stable",
        }:
            raise EvidenceError("measurement_schema_invalid")
        wall = interval["wall_elapsed_ms"]
        monotonic = interval["monotonic_elapsed_ms"]
        divergence = interval["max_divergence_ms"]
        if (type(wall) not in (int, float) or not math.isfinite(wall)
                or abs(wall) > 1e12 or not _number(monotonic)
                or not _number(divergence)
                or divergence + 0.002 < abs(wall - monotonic)
                or type(interval["stable"]) is not bool
                or interval["stable"] != (divergence <= 100)):
            raise EvidenceError("measurement_schema_invalid")
    baseline = value["baseline_b"]["correct_within_window"]
    ratio = value["contention_b"]["correct_within_window"] / baseline if baseline else 0
    if not _number(value["b_throughput_ratio"]) or not math.isclose(
        value["b_throughput_ratio"], ratio, rel_tol=1e-12, abs_tol=1e-12,
    ):
        raise EvidenceError("measurement_schema_invalid")


def prepare_directory(destination: Path, case_id: str):
    if case_id not in FILES:
        return None
    base = destination / "measurements"
    if base.is_symlink():
        raise EvidenceError("measurement_path_invalid")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = base / case_id
    directory.mkdir(mode=0o700)
    return directory.resolve()


def _read_value(path, limit):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.getuid() or before.st_mode & 0o077
                    or not 1 <= before.st_size <= limit):
                raise EvidenceError("measurement_path_invalid")
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            current = path.lstat()
            def identity(s):
                return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if identity(before) != identity(after) or identity(before) != identity(current):
                raise EvidenceError("measurement_changed_during_read")
        value = json.loads(
            raw, object_pairs_hook=unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                EvidenceError("measurement_schema_invalid")
            ),
        )
        return raw, value
    except EvidenceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise EvidenceError("measurement_evidence_invalid") from None


def _read(path, case_id, *, root=None, sources=None, required=False, with_value=False):
    family = next((module for module in (native_transport_measurements, runtime_context_evidence,
                                        classifier_benchmark_measurements, classifier_loaded_measurements)
                   if case_id in module.CASES), None)
    limit = (family.MAX_BYTES if family is not None else
             RUNTIME_ACTION_LIMIT if case_id in RUNTIME_ACTION_CASES else 16384)
    try:
        if family is not None:
            family.validate_sources(root=root, sources=sources)
        raw, value = _read_value(path, limit)
        if family is not None:
            family.validate(value, case_id=case_id, root=root, sources=sources, complete=required)
        elif case_id in RUNTIME_ACTION_CASES:
            validate_runtime_action_observation(value, case_id, root=root, sources=sources, complete=required)
        elif case_id in MATRIX_CASES:
            validate_matrix_observation(value, case_id, root=root, sources=sources, complete=required)
        elif case_id == NATIVE_CASE:
            validate_native_fixture_observation(value, root=root, sources=sources)
        elif case_id == REAL_MODEL_CASE:
            validate_real_model_observation(value)
        else:
            validate_measurement(value)
        record = {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        if family is not None:
            record["source_files"] = {name: sources[name] for name in family.SOURCES}
        elif case_id in RUNTIME_ACTION_CASES:
            record["source_files"] = {name: sources[name] for name in RUNTIME_ACTION_SOURCES}
        elif case_id == NATIVE_CASE:
            record["source_files"] = {NATIVE_SOURCE: sources[NATIVE_SOURCE]}
        elif case_id in MATRIX_CASES:
            record["source_files"] = {name: sources[name] for name in MATRIX_SOURCES}
        return (record, value) if with_value else record
    except EvidenceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise EvidenceError("measurement_evidence_invalid") from None



def _read_stages(path, *, root, sources, measurement):
    raw, value = _read_value(path, stage_measurements.MAX_BYTES)
    stage_measurements.validate(value, case_id=NOISY_CASE, root=root, sources=sources,
                                measurement=measurement)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
            "source_files": {name: sources[name] for name in stage_measurements.SOURCES}}


def collect_measurements(destination: Path, case_ids, required=(), *, root=None, sources=None, provenance=None, classifier_deadline=None):
    """Read every allowed attachment; additions, missing required files and links fail."""
    allowed = {case_id for case_id in case_ids if case_id in FILES}
    required = set(required) & allowed
    base = destination / "measurements"
    if base.is_symlink():
        raise EvidenceError("measurement_path_invalid")
    if not base.exists():
        if required:
            raise EvidenceError("measurement_evidence_missing")
        return {}
    if not base.is_dir():
        raise EvidenceError("measurement_path_invalid")
    found = {}
    for directory in base.iterdir():
        if directory.name not in allowed or directory.is_symlink() or not directory.is_dir():
            raise EvidenceError("measurement_unexpected_files")
        if directory.name in classifier_deployed_collection.FAMILIES:
            attachments = classifier_deployed_collection.collect(
                directory, case_id=directory.name, root=root, sources=sources,
                complete=directory.name in required, provenance=provenance, read_value=_read_value, deadline=classifier_deadline,
            )
            found.update({str((directory / name).relative_to(destination)): value
                          for name, value in attachments.items()})
            continue
        filename = FILES[directory.name]
        names = {path.name for path in directory.iterdir()}
        allowed_names = {filename}
        if directory.name == NOISY_CASE:
            allowed_names.add(stage_measurements.FILENAME)
        if names - allowed_names:
            raise EvidenceError("measurement_unexpected_files")
        if filename in names:
            path = directory / filename
            record, value = _read(
                path, directory.name, root=root, sources=sources,
                required=directory.name in required, with_value=True,
            )
            found[str(path.relative_to(destination))] = record
            if directory.name == NOISY_CASE and stage_measurements.FILENAME in names:
                # Validate the original measurement first, then bind exact
                # selected failures/counts and both files to the frozen sources.
                stages = directory / stage_measurements.FILENAME
                stage_record = _read_stages(stages, root=root, sources=sources, measurement=value)
                record["source_files"] = dict(stage_record["source_files"])
                found[str(stages.relative_to(destination))] = stage_record
        elif names or directory.name in required:
            raise EvidenceError("measurement_evidence_missing")
    for case_id in required:
        if str(Path("measurements") / case_id / FILES[case_id]) not in found:
            raise EvidenceError("measurement_evidence_missing")
        if case_id == NOISY_CASE and str(
            Path("measurements") / case_id / stage_measurements.FILENAME
        ) not in found:
            raise EvidenceError("measurement_evidence_missing")
    return found
