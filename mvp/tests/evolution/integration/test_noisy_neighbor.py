"""Phase 2 tenant fairness on real HTTPS/PG and production detector processes.

This bounded concurrency experiment is separate from Phase 4's five-minute,
6-CPU/8-GiB capacity drill. No detector delay or clock is injected.
"""

import asyncio
import json
import math
import os
import ssl
import stat
import time
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from evolution.measurements import FAILURE_SAMPLE_LIMIT, RESPONSE_BINS
from guardrails.evolution import models
from guardrails.evolution.errors import STATUS, EvolutionError
from guardrails.evolution.quotas import QuotaService
from guardrails.evolution.runtime_api import EvaluationRuntime, RuntimeSlot, create_app
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https
from runtime_stage_observation import (
    SharedStages,
    StageBoundary,
    frozen_sources,
    instrument,
    record_all,
)
from runtime_stage_observation import (
    record as record_stages,
)
from test_evaluation_runtime import config, fixture_system, ref, refresh, request

WINDOW_SECONDS = 30
B_RATE = 5
A_BURST = 8
CANARY = "synthetic-fairness@example.test"
TEXT = (CANARY + " ").ljust(1024, "x")
OPERATIONAL_REASONS = {
    "evaluation_timeout",
    "evaluator_unavailable",
    "inspection_incomplete",
    "queue_full",
}
OVERLOAD_ERRORS = {
    429: {"quota_exceeded", "queue_full"},
    504: {"evaluation_timeout", "upstream_timeout"},
}


def _clock_sample():
    # Bracket UTC with monotonic reads. A retry avoids mistaking preemption
    # between adjacent reads for a clock step; use the narrowest bracket.
    samples = []
    for _ in range(3):
        before = time.monotonic_ns()
        wall = time.time_ns()
        after = time.monotonic_ns()
        samples.append((after - before, wall, (before + after) // 2))
        if after - before < 1_000_000:
            break
    _, wall, monotonic = min(samples)
    return wall, monotonic


@asynccontextmanager
async def _clock_interval(records, name):
    start_wall, start_monotonic = _clock_sample()
    record = {
        "wall_elapsed_ms": 0.0,
        "monotonic_elapsed_ms": 0.0,
        "max_divergence_ms": 0.0,
        "stable": True,
    }
    records[name] = record

    def sample():
        wall, monotonic = _clock_sample()
        elapsed_wall = (wall - start_wall) / 1_000_000
        elapsed_monotonic = (monotonic - start_monotonic) / 1_000_000
        record["wall_elapsed_ms"] = round(elapsed_wall, 3)
        record["monotonic_elapsed_ms"] = round(elapsed_monotonic, 3)
        record["max_divergence_ms"] = round(
            max(record["max_divergence_ms"], abs(elapsed_wall - elapsed_monotonic)),
            3,
        )
        record["stable"] = record["max_divergence_ms"] <= 100

    async def observe():
        while True:
            await asyncio.sleep(0.25)
            sample()

    observer = asyncio.create_task(observe())
    try:
        yield
    finally:
        observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)
        sample()


def _wire_error(response, correlation, allowed):
    """Only the exact finite public envelope may account for a failed request."""
    try:
        value = models.strict_json_loads(response.content)
        if not isinstance(value, dict) or set(value) != {"error"}:
            return None
        error = models.Error.model_validate_json(
            models.canonical_json_bytes(value["error"])
        )
        expected = EvolutionError(error.code)
        if (
            error.code not in allowed
            or response.status_code != expected.status
            or value != expected.envelope(correlation)
            or response.headers.get("x-request-id") != correlation
        ):
            return None
        return error.code
    except ValueError, KeyError, TypeError:
        return None


def _restrict_concurrency(system):
    """Publish a new exact quota and independently approve a new baseline."""
    repo, service, tenant = system["repo"], system["service"], system["tenant"]
    quota_ref = system["payload"]["quota_ref"]
    with repo.transaction(tenant) as tx:
        old = tx.get("config:quotas", quota_ref["id"] + ":1", required=True)
        quota = models.with_resource_digest(
            models.Quota.model_validate_json(
                models.canonical_json_bytes({**old, "version": 2, "max_inflight": 1})
            )
        )
        tx.insert("config:quotas", quota.quota_id + ":2", quota, immutable=True)
        payload = {**system["payload"], "quota_ref": ref(quota, "quota_id")}
        policy = service.governance.submit(
            tx, system["actors"]["author"], "policy", payload
        )
        service.governance.review(
            tx,
            system["actors"]["approver"],
            "policy",
            2,
            outcome="approve",
            compiled_digest=policy["compiled_digest"],
            reason="Concurrency experiment",
        )
        policy_ref = {
            **system["policy_ref"],
            "version": 2,
            "digest": policy["compiled_digest"],
        }
        service.governance.publish_baseline(
            tx, system["actors"]["tenant_admin"], [policy_ref], 1
        )
    system.update(payload=payload, policy=policy, policy_ref=policy_ref)
    refresh(system, state="observe")
    refresh(system, state="block")


def _runtime(systems, index):
    return EvaluationRuntime(
        TenantRepository(systems[0]["dsn"]),
        config=config(systems[0], index),
        runtime_slots={s["tenant"]: RuntimeSlot(s["stores"][index]) for s in systems},
    )


async def _call(client, system, *, target, end, replica, burst=None):
    loop = asyncio.get_running_loop()
    await asyncio.sleep(max(0, target - loop.time()))
    value = request(system, text=TEXT)
    started = loop.time()
    result = {
        "request_id": value["request_id"],
        "correlation_id": value["correlation_id"],
        "replica": replica,
        "burst": burst,
        "status": 0,
        "code": "transport_error",
        "correct": False,
        "safe_error": False,
        "operational_block": False,
        "reason_codes": (),
        "decision_id": None,
        "context_id": None,
        "launch_lag_ms": (started - target) * 1000,
    }
    try:
        response = await client.post(
            "/v2/evaluations",
            json=value,
            headers={"X-Request-ID": value["correlation_id"]},
        )
        result["status"] = response.status_code
        if response.status_code == 200:
            try:
                decision = models.Decision.model_validate_json(response.content)
            except ValueError:
                result["code"] = "invalid_decision"
            else:
                common = (
                    decision.request_id == value["request_id"]
                    and decision.tenant_id == system["tenant"]
                    and decision.workload_id == "chat"
                    and decision.stage == "input"
                    and decision.policy is not None
                    and decision.policy.model_dump(mode="json") == system["policy_ref"]
                    and decision.binding_revision == system["revision"]
                    and decision.action == decision.would_be_action == "block"
                    and decision.audit_status == "persisted"
                    and decision.evaluation_ms < 500
                    and decision.evaluator_revisions == ("pii-v2",)
                    and decision.transformed_parts is None
                )
                result.update(
                    code="decision",
                    decision_id=decision.decision_id,
                    context_id=decision.context_id,
                    evaluation_ms=decision.evaluation_ms,
                    reason_codes=decision.reason_codes,
                    correct=(
                        common
                        and decision.reason_codes == ("pii_detected",)
                        and len(decision.findings) == 1
                        and decision.findings[0].rule_id == "email"
                        and decision.findings[0].category == "email"
                        and decision.findings[0].part_id == "content"
                        and decision.findings[0].start == 0
                        and decision.findings[0].end == len(CANARY)
                    ),
                    operational_block=(
                        common
                        and not decision.findings
                        and bool(decision.reason_codes)
                        and set(decision.reason_codes) <= OPERATIONAL_REASONS
                    ),
                )
        else:
            code = _wire_error(
                response,
                value["correlation_id"],
                STATUS,
            )
            result["code"] = code or "invalid_error"
            result["safe_error"] = code in OVERLOAD_ERRORS.get(
                response.status_code, set()
            )
    except httpx.HTTPError:
        pass
    result["latency_ms"] = (loop.time() - started) * 1000
    result["within_window"] = loop.time() <= end
    return result


async def _window(systems, clients, *, contention):
    loop = asyncio.get_running_loop()
    start = loop.time() + 0.25
    end = start + WINDOW_SECONDS
    tasks_b = [
        asyncio.create_task(
            _call(
                clients[1, n % 2],
                systems[1],
                target=start + n / B_RATE,
                end=end,
                replica=n % 2,
            )
        )
        for n in range(WINDOW_SECONDS * B_RATE)
    ]
    tasks_a = (
        [
            asyncio.create_task(
                _call(
                    clients[0, n % 2],
                    systems[0],
                    target=start + burst,
                    end=end,
                    replica=n % 2,
                    burst=burst,
                )
            )
            for burst in range(WINDOW_SECONDS)
            for n in range(A_BURST)
        ]
        if contention
        else []
    )
    try:
        # Open-loop arrival times do not slow B to match a struggling server.
        await asyncio.sleep(max(0, end - loop.time()))
        async with asyncio.timeout(3):
            return {
                "b": list(await asyncio.gather(*tasks_b)),
                "a": list(await asyncio.gather(*tasks_a)),
            }
    finally:
        for task in tasks_b + tasks_a:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks_b, *tasks_a, return_exceptions=True)


async def _experiment_body(systems, servers, clocks):
    async with AsyncExitStack() as stack:
        clients = {}
        for tenant_index, system in enumerate(systems):
            for index, server in enumerate(servers):
                client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        base_url=server["url"],
                        trust_env=False,
                        timeout=2,
                        verify=ssl.create_default_context(cafile=server["ca_file"]),
                        limits=httpx.Limits(
                            max_connections=4, max_keepalive_connections=4
                        ),
                        headers={
                            "Authorization": "Bearer " + system["tokens"]["workload"]
                        },
                    )
                )
                ready = await client.get("/health/ready")
                assert ready.status_code == 200
                assert ready.json()["ready"] is True
                clients[tenant_index, index] = client
        for system in systems:
            for store in system["stores"]:
                assert (
                    models.parse_time(store.lease().expires_at) - datetime.now(UTC)
                ).total_seconds() > WINDOW_SECONDS + 1
        async with _clock_interval(clocks, "baseline"):
            baseline = await _window(systems, clients, contention=False)
        # The issuer deliberately reuses unchanged leases until their actual
        # expiry. Wait outside both measured windows, then obtain genuinely new
        # signed generations; calling refresh on an unexpired lease cannot renew it.
        async with _clock_interval(clocks, "lease_wait"):
            previous = {
                (system["tenant"], index): store.lease()
                for system in systems
                for index, store in enumerate(system["stores"])
            }
            expiry = max(
                models.parse_time(snapshot.expires_at) for snapshot in previous.values()
            )
            delay = max(0, (expiry - datetime.now(UTC)).total_seconds()) + 0.05
            assert delay <= 60.1
            await asyncio.sleep(delay)
            for system in systems:
                refresh(system)
            for system in systems:
                for index, store in enumerate(system["stores"]):
                    renewed = store.lease()
                    assert (
                        renewed.generation
                        > previous[system["tenant"], index].generation
                    )
                    assert (
                        models.parse_time(renewed.expires_at) - datetime.now(UTC)
                    ).total_seconds() > WINDOW_SECONDS + 1
        async with _clock_interval(clocks, "contention"):
            contention = await _window(systems, clients, contention=True)
    return baseline, contention, clocks


async def _experiment(systems, servers, record_property):
    clocks = {}
    try:
        result = await _experiment_body(systems, servers, clocks)
    except Exception:
        # A clock step can invalidate a lease before the next window starts.
        # Keep that a finite environment failure; never fabricate observations
        # for the unstarted window to fill the closed measurement schema.
        if any(not interval["stable"] for interval in clocks.values()):
            record_property("evolution_environment_failure", "clock_discontinuity")
            raise AssertionError("environment_clock_discontinuity") from None
        raise
    if any(not interval["stable"] for interval in clocks.values()):
        record_property("evolution_environment_failure", "clock_discontinuity")
    return result


def _persisted(system, results, *, overloaded=False):
    """Delivered decisions require exact audit; extra committed work stays scoped."""
    attempts = {row["request_id"]: row for row in results}
    by_correlation = {row["correlation_id"]: row for row in results}
    delivered = {key: row for key, row in attempts.items() if row["status"] == 200}
    assert len(attempts) == len(by_correlation) == len(results)
    with system["repo"].transaction(system["tenant"]) as tx:
        rows = list(
            tx.execute(
                "SELECT kind,data FROM evolution.records WHERE tenant_id=:tenant",
                {"tenant": system["tenant"]},
            )
        )
    canary_retained = CANARY in json.dumps([row.data for row in rows])
    assert canary_retained is False
    audits = [
        row.data
        for row in rows
        if row.kind == "audit" and row.data["event_type"] == "evaluation"
    ]
    context_rows = [row.data for row in rows if row.kind == "evaluation_contexts"]
    lease_rows = [row.data for row in rows if row.kind == "quota_leases"]
    contexts = {row["request_id"]: row for row in context_rows}
    leases = {row["request_id"]: row for row in lease_rows}
    assert len(context_rows) == len(contexts)
    assert len(lease_rows) == len(leases)
    audit_by_correlation = {event["correlation_id"]: event for event in audits}
    assert len(audit_by_correlation) == len(audits)
    assert set(contexts) == set(leases) <= set(attempts)
    assert set(delivered) <= set(contexts)
    assert set(audit_by_correlation) <= set(by_correlation)
    if not overloaded:
        assert set(contexts) == set(delivered) == set(attempts)
        assert len(audits) == len(delivered)
    for result in delivered.values():
        event = audit_by_correlation[result["correlation_id"]]
        assert event["decision_id"] == result["decision_id"]
        assert event["reason_codes"] == list(result["reason_codes"])
        assert (
            contexts[result["request_id"]]["context"]["context_id"]
            == result["context_id"]
        )
    for event in audits:
        result = by_correlation[event["correlation_id"]]
        assert event["tenant_id"] == system["tenant"]
        assert event["actor_id"] == system["actors"]["workload"].principal_id
        assert event["workload_id"] == "chat" and event["metadata"] == {
            "stage": "input"
        }
        assert event["policy_digest"] == system["policy_ref"]["digest"]
        assert event["binding_revision"] == system["revision"]
        assert event["action"] == "block"
        assert event["reason_codes"] == ["pii_detected"] or (
            overloaded
            and bool(event["reason_codes"])
            and set(event["reason_codes"]) <= OPERATIONAL_REASONS
        )
        assert event["raw_content_retained"] is False
        assert contexts[result["request_id"]]["state"] == "blocked"
        if result["status"] != 200:
            assert overloaded and result["status"] == 504 and result["safe_error"]
    for request_id, row in contexts.items():
        context = models.EvaluationContext.model_validate_json(
            models.canonical_json_bytes(row["context"])
        )
        lease, result = leases[request_id], attempts[request_id]
        assert row["principal_id"] == system["actors"]["workload"].principal_id
        assert context.tenant_id == system["tenant"] and context.workload_id == "chat"
        assert context.policy.model_dump(mode="json") == system["policy_ref"]
        assert context.binding_revision == system["revision"]
        assert context.provider_id == system["provider"].provider_id
        assert row["state"] in {"blocked", "evaluating_input"}
        assert lease["tenant_id"] == system["tenant"] and lease["workload_id"] == "chat"
        assert lease["principal_id"] == system["actors"]["workload"].principal_id
        assert (
            lease["policy_digest"] == system["policy_ref"]["digest"]
            and lease["stage"] == "input"
        )
        assert lease["state"] in {"released", "expired"}
        if row["state"] == "evaluating_input":
            # Admission may commit before an undelivered timeout. This state is
            # never authorized for output, and its reservation must have expired.
            assert overloaded and result["safe_error"]
            assert result["status"] == 504 or (
                result["status"] == 429 and result["code"] == "queue_full"
            )
            assert result["correlation_id"] not in audit_by_correlation
            assert lease["expires_epoch"] <= time.time()
        else:
            assert result["correlation_id"] in audit_by_correlation
    assert system["service"].audit.verify_chain(system["tenant"])
    return contexts


def _failed_admission_cannot_replay(system, results, contexts, servers):
    candidates = [
        row
        for row in results
        if row["request_id"] in contexts
        and (
            row["status"] == 504
            or (row["status"] == 429 and row["code"] == "queue_full")
        )
    ]
    if not candidates:
        return
    row = candidates[0]
    # Use the other actual replica, proving the durable admission is shared.
    server = servers[1 - row["replica"]]
    with httpx.Client(
        base_url=server["url"],
        trust_env=False,
        timeout=2,
        verify=ssl.create_default_context(cafile=server["ca_file"]),
        headers={"Authorization": "Bearer " + system["tokens"]["workload"]},
    ) as client:
        value = request(
            system,
            text=TEXT,
            request_id=row["request_id"],
            correlation_id=row["correlation_id"],
        )
        replay = client.post(
            "/v2/evaluations",
            json=value,
            headers={"X-Request-ID": value["correlation_id"]},
        )
        assert (
            _wire_error(replay, value["correlation_id"], {"idempotency_conflict"})
            == "idempotency_conflict"
        )
        value = request(
            system,
            text=TEXT,
            stage="output",
            context_id=contexts[row["request_id"]]["context"]["context_id"],
        )
        output = client.post(
            "/v2/evaluations",
            json=value,
            headers={"X-Request-ID": value["correlation_id"]},
        )
        assert (
            _wire_error(output, value["correlation_id"], {"context_invalid"})
            == "context_invalid"
        )


def _response_bin(row):
    if row["correct"]:
        return "correct_decision"
    if row["operational_block"]:
        return "operational_block"
    return "incorrect_decision" if row["code"] == "decision" else row["code"]


def _failure_samples(results):
    # gather preserves scheduled order. Never retain IDs, text, credentials,
    # exception strings or response bodies; keep all aggregate counts separately.
    failed = [(ordinal, row) for ordinal, row in enumerate(results) if not row["correct"]]
    return {
        "rows": [
            {
                "ordinal": ordinal,
                "replica": row["replica"],
                "status": row["status"],
                "response_bin": _response_bin(row),
                "elapsed_ms": round(row["latency_ms"], 3),
                "launch_lag_ms": round(row["launch_lag_ms"], 3),
            }
            for ordinal, row in failed[:FAILURE_SAMPLE_LIMIT]
        ],
        "omitted": max(0, len(failed) - FAILURE_SAMPLE_LIMIT),
    }


def _summary(results):
    latency = sorted(row["latency_ms"] for row in results)
    responses = dict.fromkeys(RESPONSE_BINS, 0)
    for row in results:
        responses[_response_bin(row)] += 1
    return {
        "scheduled": len(results),
        "correct_terminal": sum(row["correct"] for row in results),
        "correct_within_window": sum(
            row["correct"] and row["within_window"] for row in results
        ),
        "quota_rejections": sum(
            row["status"] == 429 and row["code"] == "quota_exceeded" for row in results
        ),
        "queue_rejections": sum(
            row["status"] == 429 and row["code"] == "queue_full" for row in results
        ),
        "operational_errors": sum(
            not row["correct"] and row["code"] not in ("quota_exceeded", "queue_full")
            for row in results
        ),
        "client_ms": {
            f"p{p}": round(latency[math.ceil(len(latency) * p / 100) - 1], 3)
            for p in (50, 95, 99)
        },
        "max_launch_lag_ms": round(max(row["launch_lag_ms"] for row in results), 3),
        "response_counts": responses,
        "failure_samples": _failure_samples(results),
    }


def _record(record_property, evidence):
    serialized = json.dumps(evidence, sort_keys=True)
    record_property("noisy_neighbor_measurements", serialized)
    if directory := os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR"):
        path = Path(directory)
        info = path.lstat()
        assert path.is_absolute() and stat.S_ISDIR(info.st_mode)
        assert info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700
        descriptor = os.open(
            path / "noisy-neighbor.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "w") as output:
            output.write(serialized + "\n")


def test_two_real_replicas_preserve_tenant_b_throughput_during_tenant_a_saturation(
    tmp_path, record_property
):
    systems = []
    sources = frozen_sources()
    stages = [SharedStages(), SharedStages()]
    try:
        systems.append(fixture_system(tmp_path / "a"))
        _restrict_concurrency(systems[0])
        systems.append(fixture_system(tmp_path / "b", state="block"))
        assert len(TEXT.encode("utf-8")) == 1024
        assert systems[0]["policy"]["quota"]["max_inflight"] == 1
        assert systems[1]["policy"]["quota"]["max_inflight"] == 8
        # Total attempts are below both rate ceilings, so A's quota rejections
        # demonstrate inflight contention, not exhaustion of a minute/burst meter.
        for system in systems:
            assert system["policy"]["quota"][
                "requests_per_minute"
            ] > WINDOW_SECONDS * max(A_BURST, B_RATE * 2)
            assert system["policy"]["quota"]["burst"] > A_BURST + B_RATE
        with ExitStack() as stack:
            servers = [
                stack.enter_context(
                    serve_https(
                        lambda index=index: StageBoundary(
                            create_app(service=instrument(_runtime(systems, index), stages[index])),
                            stages[index],
                        ),
                        tmp_path / ("tls-" + str(index)),
                    )
                )
                for index in range(2)
            ]
            assert servers[0]["process"].pid != servers[1]["process"].pid
            for system in systems:
                refresh(system)
            baseline, contention, clocks = asyncio.run(
                _experiment(systems, servers, record_property)
            )
            rejection_bursts = {
                row["burst"]
                for row in contention["a"]
                if row["code"] == "quota_exceeded"
            }
            evidence = {
                "schema_version": "evolution-noisy-neighbor-v3",
                "window_seconds": WINDOW_SECONDS,
                "b_evaluations_per_second": B_RATE,
                "a_concurrent_attempts_per_burst": A_BURST,
                "a_bursts": WINDOW_SECONDS,
                "text_bytes": 1024,
                "runtime_budget_ms": 500,
                "baseline_b": _summary(baseline["b"]),
                "contention_b": _summary(contention["b"]),
                "contention_a": _summary(contention["a"]),
                "quota_rejection_bursts": len(rejection_bursts),
                "b_throughput_ratio": 0.0,
                "audit_checked": False,
                "audit_consistent": False,
                "leases_terminal": False,
                "clock_intervals": clocks,
            }
            baseline_count = evidence["baseline_b"]["correct_within_window"]
            contention_count = evidence["contention_b"]["correct_within_window"]
            if baseline_count:
                evidence["b_throughput_ratio"] = contention_count / baseline_count
            try:
                assert all(interval["stable"] for interval in clocks.values()), (
                    "environment_clock_discontinuity"
                )
                for results in (baseline["b"], contention["b"]):
                    assert len(results) == WINDOW_SECONDS * B_RATE
                    assert all(
                        row["status"] == 200 and row["correct"] for row in results
                    )
                    assert {row["replica"] for row in results if row["correct"]} == {
                        0,
                        1,
                    }
                    assert max(row["launch_lag_ms"] for row in results) < 100
                assert baseline_count >= math.ceil(WINDOW_SECONDS * B_RATE * 0.95)
                assert contention_count >= math.ceil(baseline_count * 0.90)
                assert len(contention["a"]) == WINDOW_SECONDS * A_BURST
                assert len(rejection_bursts) >= 20
                assert any(row["correct"] for row in contention["a"])
                assert all(
                    row["correct"] or row["operational_block"] or row["safe_error"]
                    for row in contention["a"]
                )
                # Cleanup is asynchronous but bounded independently of the response.
                deadline = time.monotonic() + 2
                while True:
                    active = 0
                    for system in systems:
                        with system["repo"].transaction(system["tenant"]) as tx:
                            active += tx.execute(
                                "SELECT count(*) FROM evolution.records WHERE tenant_id=:tenant AND kind='quota_leases' AND data->>'state'='active'",
                                {"tenant": system["tenant"]},
                            ).scalar_one()
                    if not active or time.monotonic() >= deadline:
                        break
                    time.sleep(0.025)
                # Expired active rows are already uncharged by admission, and
                # production reconciliation gives them a durable terminal state.
                # Never erase or accept a still-charged future-expiry lease.
                for system in systems:
                    with system["repo"].transaction(system["tenant"]) as tx:
                        charged = tx.execute(
                            "SELECT count(*) FROM evolution.records WHERE tenant_id=:tenant AND kind='quota_leases' AND data->>'state'='active' AND (data->>'expires_epoch')::numeric > extract(epoch FROM clock_timestamp())",
                            {"tenant": system["tenant"]},
                        ).scalar_one()
                    assert charged == 0
                    if active:
                        QuotaService(system["repo"]).reconcile(
                            system["tenant"], bounded_batch=1000
                        )
                evidence["audit_checked"] = True
                contexts = _persisted(systems[0], contention["a"], overloaded=True)
                _persisted(systems[1], baseline["b"] + contention["b"])
                _failed_admission_cannot_replay(
                    systems[0], contention["a"], contexts, servers
                )
                _persisted(systems[0], contention["a"], overloaded=True)
                evidence["audit_consistent"] = evidence["leases_terminal"] = True
            finally:
                # Project after the measured windows, before scenario/process cleanup.
                # Neither observer may replace the original operational assertion.
                record_all(
                    lambda: _record(record_property, evidence),
                    lambda: record_stages(stages, baseline, contention, evidence, sources),
                )
    finally:
        for system in systems:
            system["service"].audit.close()
            system["repo"].close()
