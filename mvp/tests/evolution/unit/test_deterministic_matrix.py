"""Exact inventory and sequential custody/health contracts; no native operations."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from evolution import deterministic_matrix as matrix
from evolution.registry import EvidenceError


def ref(path, seed="proof"):
    return {"path": str(path), "sha256": hashlib.sha256(seed.encode()).hexdigest()}


@pytest.fixture
def actual_inventory():
    root = Path(__file__).resolve().parents[4]
    raw = (root / "mvp/evolution/cases.json").read_bytes()
    document = json.loads(raw)
    rows = [row for row in document["phases"]["3"]["cases"]
            if any(row["nodeid"].startswith(prefix + "[") for prefix in (matrix.GATEWAY, matrix.RUNTIME))
            and "-injection-" not in row["nodeid"]]
    frozen = matrix.canonical({"schema_version": "phase3-deterministic-target-inventory-v1",
                               "kind": "diagnostic_not_phase_gate", "cases": rows}) + b"\n"
    # This is the actual retained fixture's digest, not a relaxed test-only pin.
    assert hashlib.sha256(frozen).hexdigest() == matrix.INVENTORY_SHA256
    return raw, frozen, matrix.inventory(raw, frozen)[0]


@pytest.fixture
def plan(actual_inventory):
    operation = str(UUID(int=1))
    root = Path.home().resolve() / ".agent-sessions/unit-matrix"
    output = root / ("deterministic-matrix-" + operation)
    state = root / "deployment"
    return {"schema": "phase3-deterministic-matrix-plan-v1", "operation": operation,
            "root": str(root / "repository"), "state": str(state), "output": str(output),
            "source_catalog": ref(root / "catalog.json"),
            "inventory": {"path": str(root / "target180-inventory.json"), "sha256": matrix.INVENTORY_SHA256},
            "adapter": ref(root / "reviewed_adapter.py"), "adapter_review": ref(root / "review.json"),
            "case_review": ref(root / "case-review.json"),
            "initial_health": ref(root / "health.json"),
            **{name: ref(root / (name + ".py")) for name in ("health_helper", "health_launcher",
                "forward_authority", "forward_publisher", "forward_publication_parent")},
            "initial_forwards": {tenant: ref(state / "assembly" / ("forward-" + tenant + ".json"), tenant)
                                 for tenant in matrix.TENANTS},
            "case_operations": [{"index": index, "case_id": case.case_id,
                "operation": str(UUID(int=index + 2)),
                "output": str(output / "cases" / (f"{index:03d}-" + str(UUID(int=index + 2)))),
                "operation_root": str(output / "cases" / (f"{index:03d}-" + str(UUID(int=index + 2))) / "evaluator-operations")}
                for index, case in enumerate(actual_inventory[2])],
            "maximum_seconds": 54000, "maximum_case_seconds": 310, "maximum_worker_seconds": 300,
            "maximum_cleanup_seconds": 90, "maximum_closure_seconds": 10, "maximum_health_seconds": 190,
            "gate_pass": False}


class MemoryPorts:
    """Explicit authenticated-port fixture; never an operational adapter."""
    def __init__(self):
        self.now = 1.0
        self.wall = datetime(2026, 10, 13, tzinfo=UTC)
        self.dispatched = []; self.settled = []; self.rows = []; self.health = []; self.finished = None
        self.fail_at = None; self.raise_at = None; self.raise_error = RuntimeError("not-retained")
        self.health_change = None; self.closed_change = None; self.record_error_at = None
        self.cleanup_error_at = None; self.jump_after_run = 0; self.finish_error = False

    def monotonic(self):
        return self.now

    def monotonic_ns(self):
        return int(self.now * 1e9)

    def utcnow(self):
        return self.wall + timedelta(seconds=self.now)

    def prepare(self, plan, cases):
        assert len(cases) == 180

    def admit_health(self, plan, *, heads, closed_cases, previous, deadline):
        assert 0 < deadline - self.now <= 190
        self.now += 0.01
        value = {"schema": "phase3-deterministic-matrix-health-admission-v1",
            "capsule": ref(Path(plan["output"]) / (f"health-{len(self.health):03d}.json")),
            "source_catalog": plan["source_catalog"], "forward_heads": deepcopy(heads),
            "observed_at": self.utcnow().isoformat(), "parent_completed_at": self.utcnow().isoformat(),
            "valid_until": (self.utcnow() + timedelta(hours=5)).isoformat(),
            "previous_case": None if not closed_cases else closed_cases[-1]["closure"],
            "full_readset_rechecked": True, "gate_pass": False}
        if self.health_change:
            self.health_change(value)
        self.health.append(deepcopy(value))
        return value

    def run_case(self, plan, entry, case, *, admission, measurement_started_ns, measurement_deadline):
        assert abs(measurement_deadline - self.now - 310) < 1e-6 and case.timeout_s == 300
        assert measurement_deadline == measurement_started_ns / 1e9 + 310
        self.dispatched.append(entry["index"])
        self.now += 0.1 + self.jump_after_run
        if self.raise_at == entry["index"]:
            raise self.raise_error
        return {"worker": "closed", "index": entry["index"]}

    def settle_case(self, plan, entry, case, *, admission, stage, heads, publication_deadline):
        assert abs(publication_deadline - self.now - 100) < 1e-6
        self.settled.append(entry["index"])
        if self.cleanup_error_at == entry["index"]:
            raise RuntimeError("opaque-private-token")
        self.now += 0.1
        passed = entry["index"] not in {self.fail_at, self.raise_at}
        result = {"case_id": case.case_id, "requirement_id": case.requirement_id,
            "status": "pass" if passed else "fail", "duration_ms": 100.0,
            "assertions": 9 if passed else 0, "reason_codes": [] if passed else ["case_call_failed"]}
        parameters = matrix.case_parameters(case.nodeid); after = deepcopy(heads); transition = None
        if parameters["kind"] == "gateway":
            selected = parameters["tenant"]
            after[selected] = ref(heads[selected]["path"], str(entry["index"]))
            transition = ref(Path(entry["output"]) / "forward-transition.json")
        closed = {"schema": "phase3-deterministic-matrix-closed-case-v1", "operation": entry["operation"],
            "index": entry["index"], "case_id": case.case_id, "nodeid": case.nodeid,
            "source_catalog": plan["source_catalog"], "admission": admission["capsule"], "result": result,
            "result_ref": ref(Path(entry["output"]) / "target-result.json"),
            "closure": ref(Path(entry["output"]) / "closed-case.json"), "original_case_passed": passed,
            "dispatch_stopped": not passed,
            "worker_reaped": True, "native_nonforward_closed": True, "governance_verified": True,
            "route_restored": True if parameters["kind"] == "gateway" else None,
            "forward_before": deepcopy(heads), "forward_after": after, "transition": transition,
            "unknown_mutation": False, "gate_pass": False}
        if self.closed_change:
            self.closed_change(closed)
        return closed

    def record_case(self, plan, entry, result, closed, error):
        if self.record_error_at == entry["index"]:
            raise RuntimeError("private publication text")
        self.rows.append((entry["index"], deepcopy(result)))

    def finish(self, plan, result):
        self.finished = deepcopy(result)
        if self.finish_error:
            raise RuntimeError("publication-private-text")


def test_exact_inventory_keeps_100_runtime_and_80_gateway(actual_inventory):
    cases = actual_inventory[2]
    assert len(cases) == 180
    dimensions = [matrix.case_parameters(case.nodeid) for case in cases]
    assert sum(row["kind"] == "runtime" for row in dimensions) == 100
    assert sum(row["kind"] == "gateway" for row in dimensions) == 80
    assert sum(row["kind"] == "gateway" and row["tenant"] == "team-b" for row in dimensions) == 40


@pytest.mark.parametrize("change", ["missing", "reordered", "bounds", "assertions"])
def test_frozen_inventory_never_relaxes_rows(actual_inventory, change):
    value = json.loads(actual_inventory[1])
    if change == "missing": value["cases"].pop()
    elif change == "reordered": value["cases"].reverse()
    elif change == "bounds": value["cases"][0]["timeout_s"] = 301
    else: value["cases"][0]["assertions"] = ["easier assertion"]
    with pytest.raises(EvidenceError, match="matrix_inventory_pin"):
        matrix.inventory(actual_inventory[0], matrix.canonical(value) + b"\n")


def test_current_registry_row_change_refused(actual_inventory):
    registry = json.loads(actual_inventory[0])
    registry["phases"]["3"]["cases"][0]["assertions"] = ["changed"]
    # Locate a real frozen row regardless of the registry's unrelated first row.
    chosen = actual_inventory[2][0].case_id
    for row in registry["phases"]["3"]["cases"]:
        if row["case_id"] == chosen: row["timeout_s"] = 301
    with pytest.raises(EvidenceError, match="matrix_registry_projection_changed"):
        matrix.inventory(matrix.canonical(registry), actual_inventory[1])


@pytest.mark.parametrize("key,value", [
    ("maximum_seconds", 54001), ("maximum_case_seconds", 311), ("maximum_worker_seconds", 301),
    ("maximum_cleanup_seconds", 91), ("maximum_closure_seconds", 11), ("maximum_health_seconds", 191),
])
def test_original_budgets_unchanged(plan, actual_inventory, key, value):
    plan[key] = value
    with pytest.raises(EvidenceError, match="matrix_plan_bounds"):
        matrix.validate_plan(plan, actual_inventory[2])


def test_all_180_sequential_with_both_tenant_heads(plan, actual_inventory):
    ports = MemoryPorts()
    value = matrix.execute(plan, *actual_inventory[:2], ports)
    assert value["status"] == "pass" and value["gate_pass"] is False
    assert ports.dispatched == ports.settled == list(range(180))
    assert len(value["cases"]) == len(value["closed_cases"]) == 180
    assert len(ports.health) == 181
    assert value["forward_heads"] == ports.health[-1]["forward_heads"]
    for index in range(80):
        closed = value["closed_cases"][index]
        assert ports.health[index + 1]["forward_heads"] == closed["forward_after"]
        assert ports.health[index + 1]["previous_case"] == closed["closure"]
    assert all(closed["route_restored"] is None and closed["transition"] is None
               for closed in value["closed_cases"][80:])


@pytest.mark.parametrize("index", [0, 1, 79, 80, 179])
def test_failure_retains_all_remaining_rows_and_never_upgrades(plan, actual_inventory, index):
    ports = MemoryPorts(); ports.fail_at = index
    value = matrix.execute(plan, *actual_inventory[:2], ports)
    assert value["status"] == "fail" and value["failed_case_upgraded"] is False
    assert ports.dispatched == list(range(index + 1))
    assert len(value["cases"]) == 180 and value["cases"][index]["status"] == "fail"
    assert all(row["reason_codes"] == ["not_executed_after_matrix_failure"] for row in value["cases"][index + 1:])
    assert value["final_health"] is not None


@pytest.mark.parametrize("field,value", [
    ("worker_reaped", False), ("native_nonforward_closed", False), ("governance_verified", False),
    ("unknown_mutation", True), ("route_restored", False), ("original_case_passed", False),
])
def test_unresolved_or_forged_closure_latches(plan, actual_inventory, field, value):
    ports = MemoryPorts(); ports.closed_change = lambda row: row.__setitem__(field, value)
    result = matrix.execute(plan, *actual_inventory[:2], ports)
    assert ports.dispatched == ports.settled == [0]
    assert result["status"] == "fail" and result["closed_cases"] == [] and result["final_health"] is None
    assert len(result["cases"]) == 180


@pytest.mark.parametrize("mode", ["old_other_head", "wrong_previous", "stale", "exact_age_limit", "future", "expiry", "readset"])
def test_health_must_cover_exact_current_two_heads_and_freshness(plan, actual_inventory, mode):
    ports = MemoryPorts()
    def corrupt(row):
        if mode == "old_other_head": row["forward_heads"]["team-b"] = ref(row["forward_heads"]["team-b"]["path"], "foreign")
        elif mode == "wrong_previous": row["previous_case"] = ref(Path(plan["output"]) / "foreign.json")
        elif mode == "stale": row["observed_at"] = (ports.utcnow() - timedelta(seconds=301)).isoformat()
        elif mode == "exact_age_limit": row["observed_at"] = (ports.utcnow() - timedelta(seconds=300)).isoformat()
        elif mode == "future": row["observed_at"] = (ports.utcnow() + timedelta(seconds=1)).isoformat()
        elif mode == "expiry": row["valid_until"] = (ports.utcnow() + timedelta(seconds=14399)).isoformat()
        else: row["full_readset_rechecked"] = False
    ports.health_change = corrupt
    value = matrix.execute(plan, *actual_inventory[:2], ports)
    assert not ports.dispatched and not ports.settled and len(value["cases"]) == 180
    assert value["status"] == "fail" and value["final_health"] is None


def test_closed_evidence_publication_after_outer_deadline_does_not_need_new_health(plan, actual_inventory):
    ports = MemoryPorts(); ports.raise_at = 0; ports.jump_after_run = 54000
    value = matrix.execute(plan, *actual_inventory[:2], ports)
    assert ports.dispatched == ports.settled == [0] and len(ports.health) == 1
    assert value["cases"][0]["status"] == "fail" and value["status"] == "fail"
    assert value["final_health"] is None


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(2)])
def test_interruption_settles_then_retains_all_rows_before_propagating(plan, actual_inventory, error):
    ports = MemoryPorts(); ports.raise_at = 0; ports.raise_error = error
    with pytest.raises(type(error)):
        matrix.execute(plan, *actual_inventory[:2], ports)
    assert ports.settled == [0] and len(ports.finished["cases"]) == 180
    assert ports.finished["status"] == "fail" and len(ports.dispatched) == 1


def test_final_publication_failure_does_not_hide_original_interruption(plan, actual_inventory):
    ports = MemoryPorts(); ports.raise_at = 0; ports.raise_error = KeyboardInterrupt(); ports.finish_error = True
    with pytest.raises(KeyboardInterrupt):
        matrix.execute(plan, *actual_inventory[:2], ports)
    assert ports.settled == [0]


def test_case_publication_failure_blocks_dispatch_and_is_not_pass(plan, actual_inventory):
    ports = MemoryPorts(); ports.record_error_at = 0
    result = matrix.execute(plan, *actual_inventory[:2], ports)
    assert ports.dispatched == [0] and result["cases"][0]["reason_codes"] == ["matrix_case_publication_failed"]
    assert len(result["cases"]) == 180 and result["status"] == "fail"


def test_private_exception_arguments_are_never_copied(plan, actual_inventory):
    ports = MemoryPorts(); ports.cleanup_error_at = 0
    result = matrix.execute(plan, *actual_inventory[:2], ports)
    assert "opaque-private-token" not in json.dumps(result)
    assert result["errors"][0]["stage"] == "case_custody_or_restoration"


def test_duplicate_operation_and_escaping_case_path_refused(plan, actual_inventory):
    plan["case_operations"][1]["operation"] = plan["case_operations"][0]["operation"]
    with pytest.raises(EvidenceError, match="matrix_operations"):
        matrix.validate_plan(plan, actual_inventory[2])


def test_runtime_case_cannot_claim_route_transition(plan, actual_inventory):
    ports = MemoryPorts()
    def corrupt(row):
        if row["index"] == 80: row["route_restored"] = True
    ports.closed_change = corrupt
    result = matrix.execute(plan, *actual_inventory[:2], ports)
    assert ports.dispatched[-1] == 80 and result["status"] == "fail"
    assert result["cases"][80]["reason_codes"] == ["matrix_case_or_cleanup_failed"]


def test_successful_original_result_with_transport_stop_is_preserved_but_blocks_dispatch(plan, actual_inventory):
    ports = MemoryPorts()
    ports.closed_change = lambda row: row.__setitem__('dispatch_stopped', True)
    value = matrix.execute(plan, *actual_inventory[:2], ports)
    assert ports.dispatched == [0] and value['status'] == 'fail'
    assert value['cases'][0]['status'] == value['closed_cases'][0]['result']['status'] == 'pass'
    assert all(row['status'] == 'fail' for row in value['cases'][1:])
    assert value['final_health'] is not None
