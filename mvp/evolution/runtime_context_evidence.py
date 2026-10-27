"""Closed observations of the two original context-scope runtime requests."""

from __future__ import annotations

import math
from types import MappingProxyType

from guardrails.evolution.errors import STATUS

from .diagnostics import SourceCatalog
from .registry import EvidenceError

SCHEMA = "evolution-runtime-context-observation-v1"
FILENAME = "runtime-context-observation.json"
MAX_BYTES = 4096
CODES = MappingProxyType(dict(STATUS))
CASES = MappingProxyType({
    "INT-RUNTIME-OUTPUT-CONTEXT-SCOPE-OR-EXPIRY-REJECTS-BEFORE-DETECTOR-OR-RESERVATION-"
    + case.upper().replace("_", "-"): case
    for case in ("expired", "wrong_principal", "wrong_workload")
})
SOURCES = (
    "mvp/tests/evolution/integration/test_runtime_failclosed.py",
    "mvp/tests/evolution/integration/runtime_context_observation.py",
    "mvp/tests/evolution/integration/runtime_action_observation.py",
    "mvp/tests/evolution/integration/test_evaluation_runtime.py",
    "mvp/evolution/runtime_context_evidence.py",
    "mvp/evolution/measurements.py",
    "mvp/guardrails/evolution/errors.py",
    "mvp/guardrails/evolution/runtime_api.py",
)


def validate_sources(*, root, sources):
    """Verify the frozen source subset before opening an observation artifact."""
    if root is None or type(sources) is not dict:
        raise EvidenceError("measurement_schema_invalid")
    catalog = SourceCatalog(root, sources)
    try:
        for name in SOURCES:
            if name not in sources:
                raise EvidenceError("measurement_schema_invalid")
            catalog.load(catalog.path(name))
    except EvidenceError:
        raise EvidenceError("measurement_schema_invalid") from None


def validate(value, case_id, *, root, sources, complete=False):
    """A failed case may retain only its attempted prefix; success needs both rows."""
    validate_sources(root=root, sources=sources)
    if (type(case_id) is not str or case_id not in CASES
            or type(value) is not dict or set(value) != {"schema_version", "rows"}
            or type(value["schema_version"]) is not str or value["schema_version"] != SCHEMA
            or type(value["rows"]) is not list or type(complete) is not bool):
        raise EvidenceError("measurement_schema_invalid")
    rows = value["rows"]
    if len(rows) > 2 or complete and len(rows) != 2:
        raise EvidenceError("measurement_schema_invalid")
    for index, row in enumerate(rows):
        if type(row) is not dict or set(row) != {"stage", "http_status", "wire_code", "elapsed_ms"}:
            raise EvidenceError("measurement_schema_invalid")
        status, code, elapsed = row["http_status"], row["wire_code"], row["elapsed_ms"]
        if (type(row["stage"]) is not str or row["stage"] != ("input", "output")[index]
                or type(status) is not int or not (status == 0 or 100 <= status <= 599)
                or code is not None and (type(code) is not str or code not in CODES
                    or status in (0, 200) or CODES[code] != status)
                or type(elapsed) not in (int, float) or not 0 <= elapsed <= 120000
                or not math.isfinite(elapsed)
                or index == 0 and (complete or len(rows) == 2) and status != 200
                or index == 1 and complete and (status != 503 or code != "context_invalid")):
            raise EvidenceError("measurement_schema_invalid")
