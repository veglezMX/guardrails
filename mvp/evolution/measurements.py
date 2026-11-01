"""Closed, metadata-only measurement attachments for acceptance reports."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path

from guardrails.evolution.errors import STATUS

from .registry import EvidenceError, unique_object

NOISY_CASE = "TEN-RUNTIME-NOISY-NEIGHBOR-MEASURED-THROUGHPUT"
REAL_MODEL_CASE = "INT-REAL-MODEL-TWENTY-REAL-OLLAMA-CALLS-PRESERVE-EXACT-POLICY-AUDIT-AND-PRIVATE-CONTENT"
FILES = {NOISY_CASE: "noisy-neighbor.json", REAL_MODEL_CASE: "real-model-observation.json"}
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


def _read(path, case_id):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.getuid() or before.st_mode & 0o077
                    or not 1 <= before.st_size <= 16384):
                raise EvidenceError("measurement_path_invalid")
            raw = stream.read(16385)
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
        if case_id == REAL_MODEL_CASE:
            validate_real_model_observation(value)
        else:
            validate_measurement(value)
        return {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
    except EvidenceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise EvidenceError("measurement_evidence_invalid") from None


def collect_measurements(destination: Path, case_ids, required=()):
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
        filename = FILES[directory.name]
        paths = list(directory.iterdir())
        if any(path.name != filename for path in paths):
            raise EvidenceError("measurement_unexpected_files")
        if paths:
            path = paths[0]
            found[str(path.relative_to(destination))] = _read(path, directory.name)
        elif directory.name in required:
            raise EvidenceError("measurement_evidence_missing")
    for case_id in required:
        if str(Path("measurements") / case_id / FILES[case_id]) not in found:
            raise EvidenceError("measurement_evidence_missing")
    return found
