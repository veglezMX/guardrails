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
from contextlib import AsyncExitStack, ExitStack
from pathlib import Path

import httpx
from guardrails.evolution import models
from guardrails.evolution.quotas import QuotaService
from guardrails.evolution.runtime_api import EvaluationRuntime, RuntimeSlot, create_app
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https
from test_evaluation_runtime import config, fixture_system, ref, refresh, request

WINDOW_SECONDS = 30
B_RATE = 5
A_BURST = 8
CANARY = "synthetic-fairness@example.test"
TEXT = (CANARY + " ").ljust(1024, "x")


def _restrict_concurrency(system):
    """Publish a new exact quota and independently approve a new baseline."""
    repo, service, tenant = system["repo"], system["service"], system["tenant"]
    quota_ref = system["payload"]["quota_ref"]
    with repo.transaction(tenant) as tx:
        old = tx.get("config:quotas", quota_ref["id"] + ":1", required=True)
        quota = models.with_resource_digest(models.Quota.model_validate_json(
            models.canonical_json_bytes({**old, "version": 2, "max_inflight": 1})
        ))
        tx.insert("config:quotas", quota.quota_id + ":2", quota, immutable=True)
        payload = {**system["payload"], "quota_ref": ref(quota, "quota_id")}
        policy = service.governance.submit(tx, system["actors"]["author"], "policy", payload)
        service.governance.review(
            tx, system["actors"]["approver"], "policy", 2, outcome="approve",
            compiled_digest=policy["compiled_digest"], reason="Concurrency experiment",
        )
        policy_ref = {**system["policy_ref"], "version": 2, "digest": policy["compiled_digest"]}
        service.governance.publish_baseline(tx, system["actors"]["tenant_admin"], [policy_ref], 1)
    system.update(payload=payload, policy=policy, policy_ref=policy_ref)
    refresh(system, state="observe")
    refresh(system, state="block")


def _runtime(systems, index):
    return EvaluationRuntime(
        TenantRepository(systems[0]["dsn"]), config=config(systems[0], index),
        runtime_slots={s["tenant"]: RuntimeSlot(s["stores"][index]) for s in systems},
    )


async def _call(client, system, *, target, end, replica, burst=None):
    loop = asyncio.get_running_loop()
    await asyncio.sleep(max(0, target - loop.time()))
    value = request(system, text=TEXT)
    started = loop.time()
    result = {
        "request_id": value["request_id"], "correlation_id": value["correlation_id"],
        "replica": replica, "burst": burst, "status": 0, "code": "transport_error",
        "correct": False, "decision_id": None, "context_id": None,
        "launch_lag_ms": (started - target) * 1000,
    }
    try:
        response = await client.post("/v2/evaluations", json=value)
        result["status"] = response.status_code
        if response.status_code == 200:
            try:
                decision = models.Decision.model_validate_json(response.content)
            except ValueError:
                result["code"] = "invalid_decision"
            else:
                result.update(
                    code="decision", decision_id=decision.decision_id,
                    context_id=decision.context_id, evaluation_ms=decision.evaluation_ms,
                    correct=(
                        decision.request_id == value["request_id"]
                        and decision.tenant_id == system["tenant"]
                        and decision.workload_id == "chat" and decision.stage == "input"
                        and decision.policy is not None
                        and decision.policy.model_dump(mode="json") == system["policy_ref"]
                        and decision.binding_revision == system["revision"]
                        and decision.action == decision.would_be_action == "block"
                        and decision.audit_status == "persisted"
                        and decision.evaluation_ms < 500
                        and decision.reason_codes == ("pii_detected",)
                        and decision.evaluator_revisions == ("pii-v2",)
                        and decision.transformed_parts is None
                        and len(decision.findings) == 1
                        and decision.findings[0].rule_id == "email"
                        and decision.findings[0].category == "email"
                        and decision.findings[0].part_id == "content"
                        and decision.findings[0].start == 0
                        and decision.findings[0].end == len(CANARY)
                    ),
                )
        elif response.status_code == 429:
            try:
                code = response.json()["error"]["code"]
            except (ValueError, KeyError, TypeError):
                code = None
            result["code"] = code if code in ("quota_exceeded", "queue_full") else "other_rejection"
        else:
            # Do not retain raw diagnostics, which can contain request values.
            result["code"] = "operational_error"
    except httpx.HTTPError:
        pass
    result["latency_ms"] = (loop.time() - started) * 1000
    result["within_window"] = loop.time() <= end
    return result


async def _window(systems, clients, *, contention):
    loop = asyncio.get_running_loop()
    start = loop.time() + 0.25
    end = start + WINDOW_SECONDS
    tasks_b = [asyncio.create_task(_call(
        clients[1, n % 2], systems[1], target=start + n / B_RATE,
        end=end, replica=n % 2,
    )) for n in range(WINDOW_SECONDS * B_RATE)]
    tasks_a = [asyncio.create_task(_call(
        clients[0, n % 2], systems[0], target=start + burst,
        end=end, replica=n % 2, burst=burst,
    )) for burst in range(WINDOW_SECONDS) for n in range(A_BURST)] if contention else []
    try:
        # Open-loop arrival times do not slow B to match a struggling server.
        await asyncio.sleep(max(0, end - loop.time()))
        async with asyncio.timeout(3):
            return {"b": list(await asyncio.gather(*tasks_b)),
                    "a": list(await asyncio.gather(*tasks_a))}
    finally:
        for task in tasks_b + tasks_a:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks_b, *tasks_a, return_exceptions=True)


async def _experiment(systems, servers):
    async with AsyncExitStack() as stack:
        clients = {}
        for tenant_index, system in enumerate(systems):
            for index, server in enumerate(servers):
                client = await stack.enter_async_context(httpx.AsyncClient(
                    base_url=server["url"], trust_env=False, timeout=2,
                    verify=ssl.create_default_context(cafile=server["ca_file"]),
                    limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
                    headers={"Authorization": "Bearer " + system["tokens"]["workload"]},
                ))
                ready = await client.get("/health/ready")
                assert ready.status_code == 200
                assert ready.json()["ready"] is True
                clients[tenant_index, index] = client
        baseline = await _window(systems, clients, contention=False)
        # Signed leases last at most 60s. Renew with the genuine issuer/receiver,
        # outside both timed windows; no fake expiry or injected polling delay.
        for system in systems:
            refresh(system)
        contention = await _window(systems, clients, contention=True)
    return baseline, contention


def _persisted(system, results):
    """Match every response to exact durable audit, context, and released lease."""
    admitted = {row["request_id"]: row for row in results if row["status"] == 200}
    with system["repo"].transaction(system["tenant"]) as tx:
        rows = list(tx.execute(
            "SELECT kind,data FROM evolution.records WHERE tenant_id=:tenant",
            {"tenant": system["tenant"]},
        ))
    canary_retained = CANARY in json.dumps([row.data for row in rows])
    assert canary_retained is False
    audits = [row.data for row in rows if row.kind == "audit" and row.data["event_type"] == "evaluation"]
    contexts = {row.data["request_id"]: row.data for row in rows if row.kind == "evaluation_contexts"}
    leases = {row.data["request_id"]: row.data for row in rows if row.kind == "quota_leases"}
    assert len(audits) == len(admitted)
    assert {row["decision_id"] for row in audits} == {row["decision_id"] for row in admitted.values()}
    assert set(contexts) == set(leases) == set(admitted)
    by_decision = {row["decision_id"]: row for row in admitted.values()}
    for event in audits:
        result = by_decision[event["decision_id"]]
        assert event["tenant_id"] == system["tenant"]
        assert event["actor_id"] == system["actors"]["workload"].principal_id
        assert event["correlation_id"] == result["correlation_id"]
        assert event["workload_id"] == "chat" and event["metadata"] == {"stage": "input"}
        assert event["policy_digest"] == system["policy_ref"]["digest"]
        assert event["binding_revision"] == system["revision"]
        assert event["action"] == "block" and event["reason_codes"] == ["pii_detected"]
        assert event["raw_content_retained"] is False
        context, lease = contexts[result["request_id"]], leases[result["request_id"]]
        assert context["state"] == "blocked"
        assert context["context"]["context_id"] == result["context_id"]
        assert lease["state"] in {"released", "expired"}
        assert lease["principal_id"] == system["actors"]["workload"].principal_id
        assert lease["policy_digest"] == system["policy_ref"]["digest"]
    assert system["service"].audit.verify_chain(system["tenant"])


def _summary(results):
    latency = sorted(row["latency_ms"] for row in results)
    return {
        "scheduled": len(results),
        "correct_terminal": sum(row["correct"] for row in results),
        "correct_within_window": sum(row["correct"] and row["within_window"] for row in results),
        "quota_rejections": sum(row["status"] == 429 and row["code"] == "quota_exceeded" for row in results),
        "queue_rejections": sum(row["status"] == 429 and row["code"] == "queue_full" for row in results),
        "operational_errors": sum(not row["correct"] and row["code"] not in ("quota_exceeded", "queue_full") for row in results),
        "client_ms": {f"p{p}": round(latency[math.ceil(len(latency) * p / 100) - 1], 3) for p in (50, 95, 99)},
        "max_launch_lag_ms": round(max(row["launch_lag_ms"] for row in results), 3),
    }


def _record(record_property, evidence):
    serialized = json.dumps(evidence, sort_keys=True)
    record_property("noisy_neighbor_measurements", serialized)
    if directory := os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR"):
        path = Path(directory)
        info = path.lstat()
        assert path.is_absolute() and stat.S_ISDIR(info.st_mode)
        assert info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700
        descriptor = os.open(path / "noisy-neighbor.json",
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(serialized + "\n")


def test_two_real_replicas_preserve_tenant_b_throughput_during_tenant_a_saturation(tmp_path, record_property):
    systems = []
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
            assert system["policy"]["quota"]["requests_per_minute"] > WINDOW_SECONDS * max(A_BURST, B_RATE * 2)
            assert system["policy"]["quota"]["burst"] > A_BURST + B_RATE
        with ExitStack() as stack:
            servers = [stack.enter_context(serve_https(
                lambda index=index: create_app(service=_runtime(systems, index)),
                tmp_path / ("tls-" + str(index)),
            )) for index in range(2)]
            assert servers[0]["process"].pid != servers[1]["process"].pid
            for system in systems:
                refresh(system)
            baseline, contention = asyncio.run(_experiment(systems, servers))
            rejection_bursts = {row["burst"] for row in contention["a"] if row["code"] == "quota_exceeded"}
            evidence = {
                "schema_version": "evolution-noisy-neighbor-v1", "window_seconds": WINDOW_SECONDS,
                "b_evaluations_per_second": B_RATE, "a_concurrent_attempts_per_burst": A_BURST,
                "a_bursts": WINDOW_SECONDS, "text_bytes": 1024, "runtime_budget_ms": 500,
                "baseline_b": _summary(baseline["b"]), "contention_b": _summary(contention["b"]),
                "contention_a": _summary(contention["a"]), "quota_rejection_bursts": len(rejection_bursts),
                "b_throughput_ratio": 0.0, "audit_checked": False,
                "audit_consistent": False, "leases_terminal": False,
            }
            baseline_count = evidence["baseline_b"]["correct_within_window"]
            contention_count = evidence["contention_b"]["correct_within_window"]
            if baseline_count:
                evidence["b_throughput_ratio"] = contention_count / baseline_count
            try:
                for results in (baseline["b"], contention["b"]):
                    assert len(results) == WINDOW_SECONDS * B_RATE
                    assert all(row["status"] == 200 and row["correct"] for row in results)
                    assert {row["replica"] for row in results if row["correct"]} == {0, 1}
                    assert max(row["launch_lag_ms"] for row in results) < 100
                assert baseline_count >= math.ceil(WINDOW_SECONDS * B_RATE * 0.95)
                assert contention_count >= math.ceil(baseline_count * 0.90)
                assert len(contention["a"]) == WINDOW_SECONDS * A_BURST
                assert len(rejection_bursts) >= 20
                assert any(row["correct"] for row in contention["a"])
                assert all(row["correct"] or (row["status"] == 429 and row["code"] in ("quota_exceeded", "queue_full")) for row in contention["a"])
                # Cleanup is asynchronous but bounded independently of the response.
                deadline = time.monotonic() + 2
                while True:
                    active = 0
                    for system in systems:
                        with system["repo"].transaction(system["tenant"]) as tx:
                            active += tx.execute("SELECT count(*) FROM evolution.records WHERE tenant_id=:tenant AND kind='quota_leases' AND data->>'state'='active'", {"tenant": system["tenant"]}).scalar_one()
                    if not active or time.monotonic() >= deadline:
                        break
                    time.sleep(0.025)
                # Expired active rows are already uncharged by admission, and
                # production reconciliation gives them a durable terminal state.
                # Never erase or accept a still-charged future-expiry lease.
                for system in systems:
                    with system["repo"].transaction(system["tenant"]) as tx:
                        charged = tx.execute("SELECT count(*) FROM evolution.records WHERE tenant_id=:tenant AND kind='quota_leases' AND data->>'state'='active' AND (data->>'expires_epoch')::numeric > extract(epoch FROM clock_timestamp())", {"tenant": system["tenant"]}).scalar_one()
                    assert charged == 0
                    if active:
                        QuotaService(system["repo"]).reconcile(system["tenant"], bounded_batch=1000)
                evidence["audit_checked"] = True
                _persisted(systems[0], contention["a"])
                _persisted(systems[1], baseline["b"] + contention["b"])
                evidence["audit_consistent"] = evidence["leases_terminal"] = True
            finally:
                _record(record_property, evidence)
    finally:
        for system in systems:
            system["service"].audit.close()
            system["repo"].close()
