"""Fixed readiness metadata complements, but never substitutes, original samples."""

import json
from copy import deepcopy

import pytest
from evolution import run
from evolution.measurements import (
    FILES,
    MATRIX_CASES,
    MATRIX_READINESS_ROLES,
    MATRIX_SCHEMA_V2,
    collect_measurements,
    prepare_directory,
    validate_matrix_observation,
)
from evolution.registry import EvidenceError
from test_matrix_observation import sample, sources

CASE = next(iter(MATRIX_CASES))
CANARY = "private-readiness-url-token-body-never-retained"


def ready():
    return {"state": "complete", "rows": [
        {"endpoint_role": role, "tenant_side": side, "attempts": 1,
         "http_status": 200, "elapsed_ms": 10.0}
        for role, side in MATRIX_READINESS_ROLES
    ]}


def observation():
    value = sample(completed=True)
    value.update(schema_version=MATRIX_SCHEMA_V2, readiness=ready(), actual_attempts=2, completed=2)
    value["rows"].append({**value["rows"][0], "call_ordinal": 1})
    return value


def test_matrix_readiness_preserves_exact_roles_and_constructor_failure_prefixes(tmp_path):
    frozen = sources(tmp_path)
    assert MATRIX_READINESS_ROLES == (
        ("management", None), ("runtime", "A"), ("gateway", "A"),
        ("runtime", "B"), ("gateway", "B"),
    )
    complete = observation()
    validate_matrix_observation(complete, CASE, root=tmp_path, sources=frozen, complete=True)
    for length in range(6):
        value = observation()
        value.update(actual_attempts=0, completed=0, rows=[])
        value["readiness"] = {"state": "partial", "rows": ready()["rows"][:length]}
        if length:
            value["readiness"]["rows"][-1].update(attempts=2, http_status=401, elapsed_ms=2295.803)
        validate_matrix_observation(value, CASE, root=tmp_path, sources=frozen)
        with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
            validate_matrix_observation(value, CASE, root=tmp_path, sources=frozen, complete=True)
    value["readiness"] = {"state": "unavailable", "rows": []}
    validate_matrix_observation(value, CASE, root=tmp_path, sources=frozen)
    assert CANARY not in json.dumps(value)


def test_matrix_readiness_rejects_private_fields_forged_roles_and_unbounded_numbers(tmp_path):
    frozen = sources(tmp_path)
    mutations = (
        lambda v: v["readiness"].update(error=CANARY),
        lambda v: v["readiness"].update(state=CANARY),
        lambda v: v["readiness"]["rows"][0].update(url=CANARY),
        lambda v: v["readiness"]["rows"][0].update(endpoint_role=CANARY),
        lambda v: v["readiness"]["rows"][0].update(tenant_side="A"),
        lambda v: v["readiness"]["rows"][1].update(tenant_side="B"),
        lambda v: v["readiness"]["rows"][0].update(attempts=0),
        lambda v: v["readiness"]["rows"][0].update(attempts=True),
        lambda v: v["readiness"]["rows"][0].update(attempts=257),
        lambda v: v["readiness"]["rows"][0].update(http_status=503),
        lambda v: v["readiness"]["rows"][0].update(http_status=True),
        lambda v: v["readiness"]["rows"][4].update(http_status=0),
        lambda v: v["readiness"]["rows"][0].update(elapsed_ms=float("nan")),
        lambda v: v["readiness"]["rows"][0].update(elapsed_ms=10 ** 1000),
        lambda v: v["readiness"]["rows"][0].update(elapsed_ms=60001),
        lambda v: v["readiness"]["rows"].append(deepcopy(v["readiness"]["rows"][-1])),
        lambda v: v["readiness"]["rows"].pop(),
        lambda v: v["readiness"].update(state="unavailable"),
    )
    for mutate in mutations:
        value = observation()
        mutate(value)
        with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
            validate_matrix_observation(value, CASE, root=tmp_path, sources=frozen)
    value = observation()
    value["rows"][-1]["outcome"] = "other_error"
    validate_matrix_observation(value, CASE, root=tmp_path, sources=frozen)
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        validate_matrix_observation(value, CASE, root=tmp_path, sources=frozen, complete=True)


def test_matrix_collector_keeps_v1_diagnostics_but_requires_complete_v2_on_pass(tmp_path):
    root = tmp_path / "source"
    frozen = sources(root)
    destination = tmp_path / "report"
    directory = prepare_directory(destination, CASE)
    path = directory / FILES[CASE]
    run.write_once(path, sample(completed=True))
    legacy = collect_measurements(destination, [CASE], root=root, sources=frozen)
    assert len(legacy) == 1
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        collect_measurements(destination, [CASE], [CASE], root=root, sources=frozen)
    value = observation()
    path.write_text(json.dumps(value))
    result = collect_measurements(destination, [CASE], [CASE], root=root, sources=frozen)
    assert result[str(path.relative_to(destination))] == {
        "sha256": run.digest(path), "size_bytes": path.stat().st_size, "source_files": frozen,
    }
    value.update(actual_attempts=0, completed=0, rows=[])
    value["readiness"] = {"state": "partial", "rows": []}
    path.write_text(json.dumps(value))
    assert collect_measurements(destination, [CASE], root=root, sources=frozen)
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        collect_measurements(destination, [CASE], [CASE], root=root, sources=frozen)
    (root / next(iter(frozen))).write_text("# changed readiness source\n")
    with pytest.raises(EvidenceError, match="measurement_schema_invalid"):
        collect_measurements(destination, [CASE], root=root, sources=frozen)
