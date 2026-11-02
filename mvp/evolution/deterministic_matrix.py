"""Exact Phase 3 deterministic matrix, with explicit per-case restoration.

This is a subset runner, never an acceptance gate. Its source-bound adapter owns
the actual process, health and restoration operations; the coordinator never
executes a second request or retries an uncertain operation. The adapter returns
authenticated receipts, not a caller-supplied success flag. Every required row
is retained when a failed or uncertain case stops dispatch.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from . import registry
from .registry import EvidenceError

INVENTORY_SHA256 = "bc79ac290110ab4dd6388b2a3c5b9828e76f3e56df1ee5399f4aea2b76cba1d0"
GATEWAY = ("mvp/tests/evolution/e2e/test_cluster_evaluator_gateways.py::"
           "test_deployed_gateway_engine_matrix_exact_dispatch_transformation_and_attribution")
RUNTIME = ("mvp/tests/evolution/e2e/test_cluster_evaluators.py::"
           "test_deployed_governed_evaluator_matrix")
ENGINES = ("pii", "secret", "term", "topic", "json_schema")
STATES = ("disabled", "observe", "warn", "redact", "block")
TENANTS = ("team-a", "team-b")
WORKER_SECONDS = 300
CASE_SECONDS = 310
CLEANUP_SECONDS = 90
CLOSURE_SECONDS = 10
HEALTH_SECONDS = 190
PUBLICATION_SECONDS = 100
MAXIMUM_SECONDS = 180 * WORKER_SECONDS
HASH = re.compile(r"[0-9a-f]{64}")
PARAMETERS = re.compile(r"(disabled|observe|warn|redact|block)-(input|output)-"
                        r"(pii|secret|term|topic|json_schema)-(team-a|team-b)")
CASE_REASONS = frozenset({
    "case_inventory_mismatch", "collection_failed", "unexpected_case", "required_case_skipped",
    "required_case_xfailed", "case_setup_failed", "case_call_failed", "environment_clock_discontinuity",
    "case_teardown_failed", "case_missing", "execution_incomplete", "assertions_missing",
    "pytest_nonzero_exit", "case_timeout", "worker_start_failed", "case_failed",
    "worker_evidence_missing_or_invalid", "diagnostic_catalog_missing_or_invalid",
    "diagnostic_evidence_missing_or_invalid", "measurement_evidence_missing_or_invalid",
    "gateway_route_recovery_required",
})


def require(value, code):
    if not value:
        raise EvidenceError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def reference(value):
    require(type(value) is dict and set(value) == {"path", "sha256"}
            and type(value["path"]) is str and Path(value["path"]).is_absolute()
            and str(Path(value["path"])) == value["path"]
            and all(part not in {".", ".."} for part in value["path"].split("/")[1:])
            and type(value["sha256"]) is str and HASH.fullmatch(value["sha256"]),
            "matrix_reference_invalid")
    return value


def instant(value):
    require(type(value) is str and len(value) <= 40, "matrix_timestamp_invalid")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise EvidenceError("matrix_timestamp_invalid") from error
    require(result.tzinfo is not None and result.utcoffset().total_seconds() == 0,
            "matrix_timestamp_invalid")
    return result


def case_parameters(nodeid):
    require(type(nodeid) is str, "matrix_case_invalid")
    prefix, mark, parameter = nodeid.partition("[")
    require(mark and parameter.endswith("]") and prefix in {GATEWAY, RUNTIME},
            "matrix_case_invalid")
    match = PARAMETERS.fullmatch(parameter[:-1])
    require(match is not None, "matrix_case_invalid")
    state, stage, engine, tenant = match.groups()
    kind = "gateway" if prefix == GATEWAY else "runtime"
    require(kind != "gateway" or state != "disabled", "matrix_case_invalid")
    return {"kind": kind, "tenant": tenant, "state": state, "stage": stage, "engine": engine}


def inventory(registry_raw, frozen_raw):
    """Validate all phases and preserve exact frozen rows, order and assertions."""
    require(type(registry_raw) is bytes and len(registry_raw) <= 16 * 1024 * 1024
            and type(frozen_raw) is bytes and len(frozen_raw) <= 1024 * 1024
            and hashlib.sha256(frozen_raw).hexdigest() == INVENTORY_SHA256,
            "matrix_inventory_pin")

    class Snapshot:
        def read_text(self):
            return registry_raw.decode("utf-8")

    # Selecting the ready Phase 1 exercises the full validator without claiming
    # that the still-unavailable Phase 3 is ready or changing its registry flag.
    registry.load_registry(Snapshot(), 1)
    current = json.loads(registry_raw, object_pairs_hook=registry.unique_object)
    frozen = json.loads(frozen_raw, object_pairs_hook=registry.unique_object)
    require(set(frozen) == {"schema_version", "kind", "cases"}
            and frozen["schema_version"] == "phase3-deterministic-target-inventory-v1"
            and frozen["kind"] == "diagnostic_not_phase_gate", "matrix_inventory_shape")
    nodes = {f"{prefix}[{state}-{stage}-{engine}-{tenant}]"
             for prefix, states in ((GATEWAY, STATES[1:]), (RUNTIME, STATES))
             for state in states for stage in ("input", "output")
             for engine in ENGINES for tenant in TENANTS}
    rows = frozen["cases"]
    require(type(rows) is list and len(rows) == 180
            and {row["nodeid"] for row in rows} == nodes
            and len({row["case_id"] for row in rows}) == 180
            and all(row["timeout_s"] == WORKER_SECONDS for row in rows), "matrix_inventory_shape")
    by_id = {row["case_id"]: row for row in current["phases"]["3"]["cases"]}
    actual_nodes = {row["nodeid"] for row in by_id.values()
                    if any(row["nodeid"].startswith(prefix + "[") for prefix in (GATEWAY, RUNTIME))
                    and "-injection-" not in row["nodeid"]}
    require(actual_nodes == nodes and all(by_id.get(row["case_id"]) == row for row in rows),
            "matrix_registry_projection_changed")
    return tuple(registry.Case(**(row | {"assertions": tuple(row["assertions"])})) for row in rows), current["phases"]["3"]["status"]


def validate_plan(plan, cases):
    require(type(plan) is dict and set(plan) == {
        "schema", "operation", "root", "state", "output", "source_catalog", "inventory",
        "adapter", "adapter_review", "case_review", "health_helper", "health_launcher", "forward_authority",
        "forward_publisher", "forward_publication_parent", "initial_health", "initial_forwards", "case_operations",
        "maximum_seconds", "maximum_case_seconds", "maximum_worker_seconds",
        "maximum_cleanup_seconds", "maximum_closure_seconds", "maximum_health_seconds", "gate_pass"},
        "matrix_plan_shape")
    require(plan["schema"] == "phase3-deterministic-matrix-plan-v1"
            and str(UUID(plan["operation"])) == plan["operation"] and plan["gate_pass"] is False
            and plan["maximum_seconds"] == MAXIMUM_SECONDS
            and plan["maximum_case_seconds"] == CASE_SECONDS
            and plan["maximum_worker_seconds"] == WORKER_SECONDS
            and plan["maximum_cleanup_seconds"] == CLEANUP_SECONDS
            and plan["maximum_closure_seconds"] == CLOSURE_SECONDS
            and plan["maximum_health_seconds"] == HEALTH_SECONDS, "matrix_plan_bounds")
    for key in ("root", "state", "output"):
        path = Path(plan[key])
        require(path.is_absolute() and str(path) == plan[key] and ".." not in path.parts,
                "matrix_plan_path")
    output = Path(plan["output"])
    require(output.is_relative_to(Path.home().resolve() / ".agent-sessions")
            and output.name == "deterministic-matrix-" + plan["operation"], "matrix_durable_output")
    for key in ("source_catalog", "inventory", "adapter", "adapter_review", "case_review", "health_helper", "health_launcher",
                "forward_authority", "forward_publisher", "forward_publication_parent", "initial_health"):
        reference(plan[key])
    require(plan["inventory"]["sha256"] == INVENTORY_SHA256, "matrix_inventory_pin")
    forward_heads(plan["initial_forwards"], plan["state"])
    operations = plan["case_operations"]
    require(type(operations) is list and len(operations) == len(cases) == 180, "matrix_operations")
    seen = {plan["operation"]}
    for index, (entry, case) in enumerate(zip(operations, cases, strict=True)):
        require(type(entry) is dict and set(entry) == {"index", "case_id", "operation", "output", "operation_root"}
                and type(entry["index"]) is int and entry["index"] == index and entry["case_id"] == case.case_id
                and str(UUID(entry["operation"])) == entry["operation"] and entry["operation"] not in seen,
                "matrix_operations")
        seen.add(entry["operation"])
        expected = output / "cases" / (f"{index:03d}-" + entry["operation"])
        require(entry["output"] == str(expected)
                and entry["operation_root"] == str(expected / "evaluator-operations"), "matrix_case_path")


def forward_heads(value, state):
    require(type(value) is dict and set(value) == set(TENANTS), "matrix_forward_heads")
    for tenant in TENANTS:
        reference(value[tenant])
        require(value[tenant]["path"] == str(Path(state) / "assembly" / ("forward-" + tenant + ".json")),
                "matrix_forward_path")
    return value


def validate_health(value, *, plan, heads, now, previous_case):
    require(type(value) is dict and set(value) == {"schema", "capsule", "source_catalog", "forward_heads",
        "observed_at", "parent_completed_at", "valid_until", "previous_case", "full_readset_rechecked", "gate_pass"}
        and value["schema"] == "phase3-deterministic-matrix-health-admission-v1"
        and value["source_catalog"] == plan["source_catalog"] and value["forward_heads"] == heads
        and value["previous_case"] == previous_case and value["full_readset_rechecked"] is True
        and value["gate_pass"] is False, "matrix_health_binding")
    reference(value["capsule"])
    require(now.tzinfo is not None and now.utcoffset().total_seconds() == 0, "matrix_clock")
    for key in ("observed_at", "parent_completed_at"):
        require(0 <= (now - instant(value[key])).total_seconds() < 300, "matrix_health_stale")
    require((instant(value["valid_until"]) - now).total_seconds() >= 14400, "matrix_credential_horizon")
    return value


def validate_case_result(value, case):
    require(type(value) is dict and set(value) == {"case_id", "requirement_id", "status", "duration_ms",
        "assertions", "reason_codes"} and value["case_id"] == case.case_id
        and value["requirement_id"] == case.requirement_id and value["status"] in {"pass", "fail"}
        and type(value["duration_ms"]) in (int, float) and math.isfinite(value["duration_ms"])
        and 0 <= value["duration_ms"] <= 1e12 and type(value["assertions"]) is int and value["assertions"] >= 0
        and type(value["reason_codes"]) is list and len(value["reason_codes"]) <= 32
        and len(set(value["reason_codes"])) == len(value["reason_codes"])
        and all(type(code) is str and code in CASE_REASONS for code in value["reason_codes"]),
        "matrix_case_result")
    require(value["status"] != "pass" or (not value["reason_codes"] and value["assertions"] >= len(case.assertions)),
            "matrix_case_pass_assertions")
    return value


def validate_closed_case(value, *, plan, entry, case, heads, admission):
    """Validate the projection only AFTER the bound adapter authenticates evidence.

    The closure reference must join original worker/native, governance and route
    journals through the reviewed adapter. These booleans alone confer no right
    to alter a live forward or claim restoration.
    """
    require(type(value) is dict and set(value) == {"schema", "operation", "index", "case_id", "nodeid",
        "source_catalog", "admission", "result", "result_ref", "closure", "original_case_passed", "dispatch_stopped",
        "worker_reaped", "native_nonforward_closed", "governance_verified", "route_restored",
        "forward_before", "forward_after", "transition", "unknown_mutation", "gate_pass"}
        and value["schema"] == "phase3-deterministic-matrix-closed-case-v1"
        and value["operation"] == entry["operation"] and value["index"] == entry["index"]
        and value["case_id"] == case.case_id and value["nodeid"] == case.nodeid
        and value["source_catalog"] == plan["source_catalog"] and value["admission"] == admission["capsule"]
        and value["forward_before"] == heads and value["worker_reaped"] is True
        and value["native_nonforward_closed"] is True and value["governance_verified"] is True
        and value["unknown_mutation"] is False and type(value["dispatch_stopped"]) is bool
        and value["gate_pass"] is False, "matrix_closed_case")
    for key in ("result_ref", "closure"):
        reference(value[key])
        require(Path(value[key]["path"]).is_relative_to(Path(entry["output"])), "matrix_case_receipt_path")
    result = validate_case_result(value["result"], case)
    require(type(value["original_case_passed"]) is bool
            and value["original_case_passed"] == (result["status"] == "pass"), "matrix_failed_case_upgrade")
    after = forward_heads(value["forward_after"], plan["state"])
    parameters = case_parameters(case.nodeid)
    if parameters["kind"] == "runtime":
        require(value["route_restored"] is None and value["transition"] is None and after == heads,
                "matrix_runtime_route_claim")
    else:
        require(value["route_restored"] is True, "matrix_route_recovery_required")
        other = next(tenant for tenant in TENANTS if tenant != parameters["tenant"])
        require(after[other] == heads[other], "matrix_other_forward_changed")
        if after == heads:
            require(value["transition"] is None and result["status"] == "fail", "matrix_gateway_no_transition")
        else:
            reference(value["transition"])
            require(Path(value["transition"]["path"]).is_relative_to(Path(entry["output"])),
                    "matrix_case_receipt_path")
    return value


def failure_row(case, code, *, assertions=0, duration_ms=0.0):
    return {"case_id": case.case_id, "requirement_id": case.requirement_id, "status": "fail",
            "duration_ms": duration_ms, "assertions": assertions, "reason_codes": [code]}


def safe_error(error, stage):
    # Do not copy arbitrary exception arguments, even apparently safe hex tokens.
    trace = []; cursor = error.__traceback__
    while cursor is not None and len(trace) < 24:
        code = cursor.tb_frame.f_code
        trace.append({"file": Path(code.co_filename).name[:160], "function": code.co_name[:100], "line": cursor.tb_lineno})
        cursor = cursor.tb_next
    return {"stage": stage, "type": type(error).__name__[:80], "trace": trace, "trace_truncated": cursor is not None}


class MatrixPorts(Protocol):
    """Concrete source-bound adapter contract; no production defaults or retries.

    prepare authenticates adapter/review/catalog/private paths and reserves the
    fresh durable output. run_case journals before dispatch, supervises one
    original case at its absolute measurement deadline and permits that same
    parent its one separately recorded restoration budget. settle_case is
    always called after dispatch, authenticates the closed lifecycle and
    publishes its finite prefix. It never starts or renews cleanup. Recovery
    after an incomplete lifecycle requires an explicit same-journal operation
    outside this matrix, which stops dispatch and retains all failed rows.
    admit_health performs the original complete current measurement if needed,
    then its complete current admission/readset recheck. Both forward heads are
    covered, including the finite ordered closure history. finish performs no
    deployment mutation and writes a separate final receipt.
    """
    def monotonic(self) -> float: ...
    def monotonic_ns(self) -> int: ...
    def utcnow(self) -> datetime: ...
    def prepare(self, plan, cases) -> None: ...
    def admit_health(self, plan, *, heads, closed_cases, previous, deadline): ...
    def run_case(self, plan, entry, case, *, admission, measurement_started_ns, measurement_deadline): ...
    def settle_case(self, plan, entry, case, *, admission, stage, heads, publication_deadline): ...
    def record_case(self, plan, entry, result, closed, error): ...
    def finish(self, plan, result): ...


def execute(plan, registry_raw, frozen_raw, ports: MatrixPorts):
    """Run exactly the frozen sequence through a reviewed native/health adapter.

    KeyboardInterrupt/SystemExit are retained as failed rows, cleanup is still
    attempted, and the original interruption is propagated only after finish.
    Ordinary operational exceptions stop all further dispatch and return failure.
    """
    cases, phase_status = inventory(registry_raw, frozen_raw)
    validate_plan(plan, cases)
    ports.prepare(plan, cases)
    started = ports.monotonic()
    require(type(started) in (int, float) and math.isfinite(started) and started >= 0, "matrix_clock")
    deadline = started + MAXIMUM_SECONDS
    heads = {tenant: dict(plan["initial_forwards"][tenant]) for tenant in TENANTS}
    results = []; closed_cases = []; errors = []; admission = None; final_health = None
    blocked = False; interruption = None; dispatched = 0
    for entry, case in zip(plan["case_operations"], cases, strict=True):
        if blocked:
            result = failure_row(case, "not_executed_after_matrix_failure")
            results.append(result)
            try:
                ports.record_case(plan, entry, result, None, None)
            except BaseException as error:  # noqa: BLE001 -- Retain failure evidence and propagate interruptions after cleanup.
                errors.append(safe_error(error, "unexecuted_row_publication"))
                if not isinstance(error, Exception) and interruption is None:
                    interruption = error
            continue
        stage = closed = case_error = None; attempted = False
        result = failure_row(case, "matrix_case_not_dispatched")
        try:
            require(deadline - ports.monotonic() >= CASE_SECONDS, "matrix_deadline")
            admission = ports.admit_health(plan, heads=heads, closed_cases=tuple(closed_cases), previous=admission,
                                           deadline=min(deadline, ports.monotonic() + HEALTH_SECONDS))
            validate_health(admission, plan=plan, heads=heads, now=ports.utcnow(),
                            previous_case=None if not closed_cases else closed_cases[-1]["closure"])
            require(deadline - ports.monotonic() >= CASE_SECONDS, "matrix_deadline")
            attempted = True; dispatched += 1
            dispatch_ns = ports.monotonic_ns()
            require(type(dispatch_ns) is int and dispatch_ns > 0, "matrix_clock")
            stage = ports.run_case(plan, entry, case, admission=admission,
                measurement_started_ns=dispatch_ns, measurement_deadline=dispatch_ns / 1e9 + CASE_SECONDS)
        except BaseException as error:  # noqa: BLE001 -- Retain failure evidence and propagate interruptions after cleanup.
            case_error = safe_error(error, "case_dispatch_or_supervision")
            if not isinstance(error, Exception):
                interruption = error
        finally:
            if attempted:
                try:
                    # The case parent already consumed its sole owned cleanup
                    # authority. This step only verifies/publishes that closed
                    # evidence; stale health cannot demand another request,
                    # and no second restoration allowance is granted here.
                    closed = ports.settle_case(plan, entry, case, admission=admission, stage=stage, heads=heads,
                                               publication_deadline=ports.monotonic() + PUBLICATION_SECONDS)
                    validate_closed_case(closed, plan=plan, entry=entry, case=case, heads=heads, admission=admission)
                    closed_cases.append(closed)
                    heads = {tenant: dict(closed["forward_after"][tenant]) for tenant in TENANTS}
                    result = dict(closed["result"])
                except BaseException as error:  # noqa: BLE001 -- Retain failure evidence and propagate interruptions after cleanup.
                    case_error = case_error or safe_error(error, "case_custody_or_restoration")
                    if not isinstance(error, Exception) and interruption is None:
                        interruption = error
            if case_error is not None:
                errors.append(case_error)
                result = failure_row(case, "matrix_case_or_cleanup_failed", assertions=result["assertions"],
                                     duration_ms=result["duration_ms"])
            results.append(result)
            blocked = (case_error is not None or result["status"] != "pass"
                       or (closed is not None and closed["dispatch_stopped"]))
            try:
                ports.record_case(plan, entry, result, closed, case_error)
            except BaseException as error:  # noqa: BLE001 -- Retain failure evidence and propagate interruptions after cleanup.
                errors.append(safe_error(error, "case_publication")); blocked = True
                results[-1] = failure_row(case, "matrix_case_publication_failed", assertions=result["assertions"],
                                          duration_ms=result["duration_ms"])
                if not isinstance(error, Exception) and interruption is None:
                    interruption = error
    # A restored failed case may receive a fresh health receipt. That does not
    # change its result, retry the case or authorize later cases in this run.
    if dispatched and len(closed_cases) == dispatched and ports.monotonic() < deadline and interruption is None:
        try:
            final_health = ports.admit_health(plan, heads=heads, closed_cases=tuple(closed_cases), previous=admission,
                                             deadline=min(deadline, ports.monotonic() + HEALTH_SECONDS))
            validate_health(final_health, plan=plan, heads=heads, now=ports.utcnow(),
                            previous_case=None if not closed_cases else closed_cases[-1]["closure"])
        except BaseException as error:  # noqa: BLE001 -- Retain failure evidence and propagate interruptions after cleanup.
            errors.append(safe_error(error, "final_full_health"))
            if not isinstance(error, Exception) and interruption is None:
                interruption = error
    elapsed = ports.monotonic() - started
    success = (len(results) == len(closed_cases) == dispatched == 180 and not errors
               and not blocked and all(row["status"] == "pass" for row in results) and final_health is not None
               and 0 <= elapsed <= MAXIMUM_SECONDS)
    output = {"schema": "phase3-deterministic-matrix-result-v1", "operation": plan["operation"],
              "status": "pass" if success else "fail", "phase": 3, "phase_registry_status": phase_status,
              "source_catalog": plan["source_catalog"], "inventory": plan["inventory"], "adapter": plan["adapter"],
              "cases": results, "closed_cases": closed_cases, "dispatched": dispatched,
              "forward_heads": heads, "final_health": final_health, "errors": errors,
              "elapsed_seconds": elapsed, "gate_pass": False, "failed_case_upgraded": False}
    try:
        ports.finish(plan, output)
    except BaseException:
        if interruption is not None:
            interruption.add_note("Matrix final evidence publication also failed")
            raise interruption
        raise
    if interruption is not None:
        raise interruption
    return output
