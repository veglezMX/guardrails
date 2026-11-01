"""Runtime fail-closed assembly over TLS, real PG, signed caches and workers."""

import copy
import json
import multiprocessing
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from guardrails.evolution import models
from guardrails.evolution.audit import AuditStore
from guardrails.evolution.plugins import EvaluatorRegistry
from guardrails.evolution.runtime_api import EvaluationRuntime, RuntimeSlot, create_app
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https
from runtime_context_observation import RuntimeContextObservation
from test_evaluation_runtime import (
    config,
    fixture_system,
    http,
    ref,
    refresh,
    request,
    runtime,
)


class CountingRegistry(EvaluatorRegistry):
    """Observe entry to the real isolated detector without replacing its work."""

    def __init__(self, calls, **options):
        super().__init__(**options)
        self.calls = calls

    async def evaluate(self, *args, **kwargs):
        with self.calls.get_lock():
            self.calls.value += 1
        return await super().evaluate(*args, **kwargs)


class DatabaseFaultAudit(AuditStore):
    """Fault injection at actual PG append, preserving runtime transaction code."""

    def __init__(self, repository, fail):
        super().__init__(repository)
        self.fail = fail

    def append(self, tx, event_type, **options):
        if event_type == "evaluation" and self.fail.value:
            tx.execute("SELECT 1 / 0")
        return super().append(tx, event_type, **options)


def observe(system, *, audit_fail=None, clock_offset=None):
    calls = multiprocessing.get_context("fork").Value("i", 0)

    def factory():
        registry = CountingRegistry(
            calls,
            tenant_ids=(system["tenant"],),
            workers=2,
            max_queued_jobs=4,
            timeout_ms=400,
        )
        updates = {"registry": registry}
        if clock_offset is not None:
            updates["clock"] = lambda: (
                datetime.now(UTC) + timedelta(seconds=clock_offset.value)
            )
        service = runtime(system, **updates)
        if audit_fail is not None:
            service.audit.close()
            service.audit = DatabaseFaultAudit(service.repository, audit_fail)
        return create_app(service=service)

    return calls, factory


def rows(system):
    with system["repo"].transaction(system["tenant"]) as tx:
        return {
            "contexts": tx.list("evaluation_contexts"),
            "leases": tx.list("quota_leases"),
            "audit": [
                row for row in tx.list("audit") if row["event_type"] == "evaluation"
            ],
        }


def assert_error(response, code, *, status=503):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert "transformed_parts" not in body and "action" not in body
    assert "synthetic" not in response.text


def cleanup(system):
    system["service"].audit.close()
    system["repo"].close()


@pytest.mark.parametrize("case", ["expired", "wrong_principal", "wrong_workload"])
def test_output_context_scope_or_expiry_rejects_before_detector_or_reservation(
    tmp_path, case
):
    system = fixture_system(tmp_path)
    offset = multiprocessing.get_context("fork").Value("d", 0.0)
    calls, factory = observe(system, clock_offset=offset)
    try:
        token = system["tokens"]["workload"]
        other_token = None
        if case != "expired":
            with system["repo"].transaction(system["tenant"]) as tx:
                principal = system["actors"]["workload"].principal_id
                workload_id = "chat"
                if case == "wrong_principal":
                    principal = system["service"].identity.provision_principal(
                        tx, "Other workload caller", ["workload"]
                    )["principal_id"]
                else:
                    workload_id = "other"
                    original = tx.get("workloads", "chat", required=True)
                    tx.insert(
                        "workloads",
                        workload_id,
                        models.Workload(
                            tenant_id=system["tenant"],
                            workload_id=workload_id,
                            display_name="Independent workload",
                            mode="remote",
                            allowed_stages=("input", "output"),
                            provider_ids=(system["provider"].provider_id,),
                            quota_id=original["quota_id"],
                            required_state="disabled",
                        ),
                    )
                other_token = system["service"].identity.issue_credential(
                    tx, principal, workload_id=workload_id
                )["one_time_secret"]
        with (
            RuntimeContextObservation(case) as observation,
            serve_https(factory, tmp_path / "tls") as server,
            http(server, token) as client,
        ):
            first = observation.post(
                client, "input", json=request(system, text="safe synthetic message")
            )
            assert first.status_code == 200, first.text
            assert first.json()["action"] == "allow"
            context_id = first.json()["context_id"]
            initial = rows(system)
            assert calls.value == 1
            if case == "expired":
                offset.value = 121
            output = request(
                system,
                stage="output",
                context_id=context_id,
                text="safe synthetic response",
            )
            if case == "wrong_workload":
                output["workload_id"] = "other"
            failure = observation.post(
                client, "output",
                json=output,
                headers={"Authorization": "Bearer " + (other_token or token)},
            )
            assert_error(failure, "context_invalid")
            after = rows(system)
            assert calls.value == 1
            assert after["contexts"] == initial["contexts"]
            assert len(after["leases"]) == len(initial["leases"]) == 1
            assert len(after["audit"]) == len(initial["audit"]) == 1
            if case != "expired":
                valid = client.post(
                    "/v2/evaluations",
                    json=request(
                        system,
                        stage="output",
                        context_id=context_id,
                        text="safe synthetic response",
                    ),
                )
                assert valid.status_code == 200, valid.text
                assert (
                    valid.json()["action"] == "allow"
                    and valid.json()["context_id"] == context_id
                )
                assert calls.value == 2
    finally:
        cleanup(system)


def test_stricter_published_baseline_invalidates_inflight_context_before_output_worker(
    tmp_path,
):
    system = fixture_system(tmp_path)
    calls, factory = observe(system)
    try:
        with (
            serve_https(factory, tmp_path / "tls") as server,
            http(server, system["tokens"]["workload"]) as client,
        ):
            first = client.post(
                "/v2/evaluations", json=request(system, text="safe synthetic message")
            )
            assert first.status_code == 200, first.text
            context_id = first.json()["context_id"]
            before = rows(system)
            stronger = copy.deepcopy(system["payload"])
            stronger["rules"][0].update(mandatory=True, minimum_state="block")
            with system["repo"].transaction(system["tenant"]) as tx:
                governance = system["service"].governance
                policy = governance.submit(
                    tx, system["actors"]["author"], "stronger-floor", stronger
                )
                governance.review(
                    tx,
                    system["actors"]["approver"],
                    "stronger-floor",
                    1,
                    outcome="approve",
                    compiled_digest=policy["compiled_digest"],
                    reason="Mandatory floor tightened",
                )
                reference = {
                    "tenant_id": system["tenant"],
                    "policy_id": "stronger-floor",
                    "version": 1,
                    "digest": policy["compiled_digest"],
                }
                publication = governance.publish_baseline(
                    tx, system["actors"]["tenant_admin"], [reference], 1
                )
                assert publication["revision"] == 2
                assert publication["affected_workloads"] == ["chat"]
            denied = client.post(
                "/v2/evaluations",
                json=request(
                    system,
                    stage="output",
                    context_id=context_id,
                    text="safe synthetic response",
                ),
            )
            assert_error(denied, "floor_conflict", status=409)
            after = rows(system)
            assert calls.value == 1
            assert after["contexts"] == before["contexts"]
            assert len(after["leases"]) == len(before["leases"]) == 1
            assert len(after["audit"]) == len(before["audit"]) == 1
    finally:
        cleanup(system)


@pytest.mark.parametrize("failure", ["sql_failure", "held_audit_lock"])
def test_required_shared_audit_failure_never_releases_allow_or_opens_context(
    tmp_path, failure
):
    system = fixture_system(tmp_path)
    fail = multiprocessing.get_context("fork").Value("i", 0)
    calls, factory = observe(
        system, audit_fail=fail if failure == "sql_failure" else None
    )
    try:
        with (
            serve_https(factory, tmp_path / "tls") as server,
            http(server, system["tokens"]["workload"]) as client,
        ):
            first = client.post(
                "/v2/evaluations", json=request(system, text="safe synthetic message")
            )
            assert first.status_code == 200, first.text
            assert first.json()["action"] == "allow"
            before = rows(system)
            data = request(system, text="safe synthetic audited request")
            started = time.monotonic()
            if failure == "sql_failure":
                fail.value = 1
                blocked = client.post("/v2/evaluations", json=data)
                assert_error(blocked, "audit_failed")
                fail.value = 0
            else:
                with system["repo"].transaction(system["tenant"]) as tx:
                    tx.lock("audit")
                    blocked = client.post("/v2/evaluations", json=data)
                assert blocked.status_code in (503, 504), blocked.text
                assert blocked.json()["error"]["code"] in {
                    "audit_failed",
                    "evaluation_timeout",
                    "upstream_timeout",
                }
                assert set(blocked.json()) == {"error"}
                assert "synthetic" not in blocked.text
            assert time.monotonic() - started < 0.8
            # The input detector ran successfully; only required audit failed.
            assert calls.value == 2
            deadline = time.monotonic() + 2
            while True:
                after = rows(system)
                if all(row["state"] != "active" for row in after["leases"]):
                    break
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert len(after["audit"]) == len(before["audit"]) == 1
            failed = [
                row
                for row in after["contexts"]
                if row["request_id"] == data["request_id"]
            ]
            assert len(failed) == 1 and failed[0]["state"] == "evaluating_input"
            assert first.json()["context_id"] != failed[0]["context"]["context_id"]
            replay = client.post("/v2/evaluations", json=data)
            assert_error(replay, "idempotency_conflict", status=409)
            assert calls.value == 2
            recovered = client.post(
                "/v2/evaluations", json=request(system, text="safe synthetic recovery")
            )
            assert recovered.status_code == 200, recovered.text
            assert (
                recovered.json()["action"] == "allow"
                and recovered.json()["audit_status"] == "persisted"
            )
            assert calls.value == 3
            with system["repo"].transaction(system["tenant"]) as tx:
                stored = list(
                    tx.execute(
                        "SELECT data FROM evolution.records WHERE tenant_id=:tenant",
                        {"tenant": system["tenant"]},
                    )
                )
                assert "safe synthetic" not in json.dumps([row.data for row in stored])
    finally:
        cleanup(system)


def test_two_tenant_https_runtime_keeps_reserved_worker_and_audit_available_under_neighbor_overload(
    tmp_path,
):
    """Bounded Phase2 isolation, not the separate five-minute capacity gate."""
    a = fixture_system(tmp_path / "a")
    b = fixture_system(tmp_path / "b")
    ctx = multiprocessing.get_context("fork")
    pids = {system["tenant"]: ctx.Value("i", 0) for system in (a, b)}
    calls = {system["tenant"]: ctx.Value("i", 0) for system in (a, b)}

    class ReservedRegistry(EvaluatorRegistry):
        async def start(self):
            await super().start()
            for tenant, pid in pids.items():
                pid.value = self._pools[tenant].worker_pids[0]

        async def evaluate(self, *args, **kwargs):
            counter = calls[kwargs["tenant_id"]]
            with counter.get_lock():
                counter.value += 1
            return await super().evaluate(*args, **kwargs)

    def factory():
        registry = ReservedRegistry(
            tenant_ids=tuple(sorted(pids)), workers=2, max_queued_jobs=2, timeout_ms=400
        )
        service = EvaluationRuntime(
            TenantRepository(a["dsn"]),
            config=config(a).model_copy(update={"queue_limit": 2}),
            registry=registry,
            runtime_slots={
                system["tenant"]: RuntimeSlot(system["stores"][0]) for system in (a, b)
            },
        )
        return create_app(service=service)

    try:
        # Publish an actual approved quota revision rather than bypassing quota
        # storage or injecting a smaller in-memory limit for the test.
        payload = copy.deepcopy(a["payload"])
        with a["repo"].transaction(a["tenant"]) as tx:
            old = models.Quota.model_validate_json(
                models.canonical_json_bytes(
                    tx.get(
                        "config:quotas",
                        payload["quota_ref"]["id"] + ":1",
                        required=True,
                    )
                )
            )
            quota = models.with_resource_digest(
                old.model_copy(update={"version": 2, "max_inflight": 1})
            )
            tx.insert("config:quotas", quota.quota_id + ":2", quota)
            payload["quota_ref"] = ref(quota, "quota_id")
            governance = a["service"].governance
            policy = governance.submit(tx, a["actors"]["author"], "policy", payload)
            governance.review(
                tx,
                a["actors"]["approver"],
                "policy",
                2,
                outcome="approve",
                compiled_digest=policy["compiled_digest"],
                reason="One reserved request",
            )
            reference = {
                "tenant_id": a["tenant"],
                "policy_id": "policy",
                "version": 2,
                "digest": policy["compiled_digest"],
            }
        refresh(a, state="observe", policy_ref=reference)
        with a["repo"].transaction(a["tenant"]) as tx:
            a["service"].governance.publish_baseline(
                tx, a["actors"]["tenant_admin"], [reference], 1
            )
        refresh(a)
        with (
            serve_https(factory, tmp_path / "tls") as server,
            http(server, a["tokens"]["workload"]) as client_a,
            http(server, b["tokens"]["workload"]) as client_b,
            ThreadPoolExecutor(max_workers=1) as requests,
        ):
            baseline = [
                client_b.post("/v2/evaluations", json=request(b)) for _ in range(3)
            ]
            assert all(
                response.status_code == 200 and response.json()["action"] == "allow"
                for response in baseline
            )
            assert calls[b["tenant"]].value == 3
            assert (
                pids[a["tenant"]].value > 0
                and pids[a["tenant"]].value != pids[b["tenant"]].value
            )
            os.kill(pids[a["tenant"]].value, signal.SIGSTOP)
            overloaded = requests.submit(
                client_a.post, "/v2/evaluations", json=request(a)
            )
            wait_until = time.monotonic() + 1
            while calls[a["tenant"]].value == 0:
                assert time.monotonic() < wait_until
                time.sleep(0.001)
            assert not overloaded.done()
            # A's second request must be rejected by its real inflight quota,
            # without consuming the reserved B worker or an evaluation lease.
            quota_denial = client_a.post("/v2/evaluations", json=request(a))
            assert_error(quota_denial, "quota_exceeded", status=429)
            assert 1 <= int(quota_denial.headers["retry-after"]) <= 60
            loaded = [
                client_b.post("/v2/evaluations", json=request(b)) for _ in range(3)
            ]
            assert len(loaded) == len(baseline) == 3
            for response in loaded:
                assert response.status_code == 200, response.text
                decision = response.json()
                assert decision["tenant_id"] == b["tenant"]
                assert (
                    decision["action"] == "allow"
                    and decision["would_be_action"] == "block"
                )
                assert decision["findings"][0]["category"] == "email"
                assert decision["audit_status"] == "persisted"
                assert decision["evaluation_ms"] < 500
            rejected_a = overloaded.result(timeout=2)
            assert rejected_a.status_code == 200, rejected_a.text
            assert rejected_a.json()["action"] == "block"
            assert "evaluation_timeout" in rejected_a.json()["reason_codes"]
            assert calls[a["tenant"]].value == 1 and calls[b["tenant"]].value == 6
            evidence_b = rows(b)
            assert len(evidence_b["audit"]) == 6
            assert all(
                event["actor_id"] == b["actors"]["workload"].principal_id
                for event in evidence_b["audit"]
            )
            assert len(rows(a)["leases"]) == 1
    finally:
        cleanup(a)
        cleanup(b)
