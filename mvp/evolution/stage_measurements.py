"""Closed runtime-stage evidence shared by the collector and test recorder.

Call validate_measurement on the base v3 artifact before validate. This module
imports no fixture, server, process recorder or runtime implementation.
"""

import json
import math

from .diagnostics import SourceCatalog
from .registry import EvidenceError

FILENAME = "runtime-stage-observation.json"
MAX_BYTES = 16384
CASE = "TEN-RUNTIME-NOISY-NEIGHBOR-MEASURED-THROUGHPUT"
SCHEMA = "evolution-runtime-stage-observation-v1"
SOURCES = (
    "mvp/tests/evolution/integration/test_noisy_neighbor.py",
    "mvp/tests/evolution/integration/runtime_stage_observation.py",
    "mvp/tests/evolution/integration/https_support.py",
    "mvp/tests/evolution/integration/test_evaluation_runtime.py",
    "mvp/guardrails/evolution/runtime_api.py",
    "mvp/guardrails/evolution/storage.py",
    "mvp/guardrails/evolution/identity.py",
    "mvp/guardrails/evolution/quotas.py",
    "mvp/guardrails/evolution/plugins.py",
    "mvp/evolution/stage_measurements.py",
)
JOB_LIMIT = 6
ROW_LIMIT = 8
PHASES = ("unknown", "authenticate", "admit", "detector", "finalize", "cleanup")
KINDS = ("metadata", "detector")
OUTCOMES = ("pending", "returned", "known_error", "cancelled", "other_error", "not_applicable")
COHORTS = ("baseline_b", "contention_b", "contention_a")


def validate_sources(root, sources):
    if root is None or type(sources) is not dict or any(name not in sources for name in SOURCES):
        raise EvidenceError("measurement_source_invalid")
    catalog = SourceCatalog(root, sources)
    for name in SOURCES:
        catalog.path(name)
        catalog.load(name)


def validate(value, *, case_id, root, sources, measurement):
    """Source-bound stage metadata paired with an already validated v3 measurement."""
    def require(condition):
        if not condition:
            raise EvidenceError("measurement_schema_invalid")
    require(case_id == CASE and type(value) is dict and set(value) == {"schema_version", "rows", "omitted"})
    require(value["schema_version"] == SCHEMA and type(value["rows"]) is list and len(value["rows"]) <= ROW_LIMIT)
    require(type(value["omitted"]) is dict and set(value["omitted"]) == set(COHORTS))
    require(type(measurement) is dict and measurement.get("schema_version") == "evolution-noisy-neighbor-v3")
    expected = []
    for name in COHORTS:
        summary = measurement.get(name)
        limit = 240 if name == "contention_a" else 150
        require(type(summary) is dict and type(summary.get("scheduled")) is int
                and summary["scheduled"] == limit
                and type(summary.get("correct_terminal")) is int
                and 0 <= summary["correct_terminal"] <= limit
                and type(summary.get("failure_samples")) is dict
                and type(summary["failure_samples"].get("rows")) is list)
        count = summary["scheduled"] - summary["correct_terminal"]
        take = min(count, ROW_LIMIT - len(expected))
        sample = summary["failure_samples"]["rows"][:take]
        require(len(sample) == take and type(value["omitted"][name]) is int
                and value["omitted"][name] == count - take)
        for item in sample:
            require(type(item) is dict and all(type(item.get(k)) is int for k in ("ordinal", "replica", "status")))
            require(0 <= item["ordinal"] < limit and item["replica"] in (0, 1)
                    and (item["status"] == 0 or 100 <= item["status"] <= 599))
        expected.extend((name, item) for item in sample)
    require(len(value["rows"]) == len(expected))
    for row, (name, result) in zip(value["rows"], expected, strict=True):
        require(type(row) is dict and set(row) == {"cohort", "ordinal", "replica", "status", "observation", "reason", "response_ms", "jobs"})
        require(row["cohort"] == name and all(type(row[k]) is int and row[k] == result[k] for k in ("ordinal", "replica", "status")))
        require(type(row["observation"]) is str and row["observation"] in {"complete", "partial", "unavailable"})
        require(type(row["jobs"]) is list and len(row["jobs"]) <= JOB_LIMIT)
        def number(v, nullable=False):
            return v is None if nullable and v is None else type(v) in (int, float) and 0 <= v <= 10000 and math.isfinite(v)
        require(number(row["response_ms"], True))
        if row["observation"] == "unavailable":
            require(type(row["reason"]) is str and row["reason"] in {"missing", "busy", "duplicate", "dropped_update", "status_mismatch", "no_jobs", "timing_out_of_range", "recorder_error"}
                    and row["response_ms"] is None and row["jobs"] == [])
            continue
        require(row["reason"] is None and row["jobs"])
        terminal = row["response_ms"] is not None
        for job in row["jobs"]:
            require(type(job) is dict and set(job) == {"phase", "kind", "submitted_ms", "worker_start_ms", "worker_end_ms", "await_end_ms", "worker_outcome", "await_outcome"})
            require(type(job["phase"]) is str and job["phase"] in PHASES
                    and type(job["kind"]) is str and job["kind"] in KINDS)
            terminal = terminal and job["phase"] != "unknown"
            require(number(job["submitted_ms"]) and all(number(job[k], True) for k in ("worker_start_ms", "worker_end_ms", "await_end_ms")))
            require(type(job["worker_outcome"]) is str and job["worker_outcome"] in OUTCOMES
                    and type(job["await_outcome"]) is str and job["await_outcome"] in OUTCOMES[:-1])
            require((job["await_end_ms"] is None) == (job["await_outcome"] == "pending"))
            if job["await_end_ms"] is not None:
                require(job["await_end_ms"] >= job["submitted_ms"])
            if job["kind"] == "detector":
                require(job["phase"] == "detector" and job["worker_start_ms"] is None
                        and job["worker_end_ms"] is None and job["worker_outcome"] == "not_applicable")
            else:
                require(job["phase"] != "detector" and job["worker_outcome"] != "not_applicable")
                require((job["worker_end_ms"] is None) == (job["worker_outcome"] == "pending"))
                if job["worker_start_ms"] is not None:
                    require(job["worker_start_ms"] >= job["submitted_ms"])
                if job["worker_end_ms"] is not None:
                    require(job["worker_start_ms"] is not None and job["worker_end_ms"] >= job["worker_start_ms"])
                terminal = terminal and job["worker_end_ms"] is not None
            terminal = terminal and job["await_end_ms"] is not None
        admitted = any(j["phase"] == "admit" and j["worker_outcome"] == "returned"
                       and j["await_outcome"] == "returned" for j in row["jobs"])
        terminal = terminal and (not admitted or any(j["phase"] == "cleanup" for j in row["jobs"]))
        require((row["observation"] == "complete") == bool(terminal))
    require(len(json.dumps(value, allow_nan=False, sort_keys=True).encode() + b"\n") <= MAX_BYTES)
    validate_sources(root, sources)  # Recheck exact frozen bytes before retaining anything.
    return value
