"""Production detector equivalence, scoped identities and actual worker cleanup."""
import asyncio
import json
import os
from pathlib import Path
import signal
from uuid import uuid4

import pytest

from guardrails.evolution import models
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.management_api import ManagementApi
from guardrails.evolution.plugins import PinnedPiiEvaluator
from guardrails.evolution.storage import TenantRepository


def rule(**updates):
    return models.Rule.model_validate({"rule_id": "contact-pii", "kind": "pii", "stages": ["input", "output"], "action": "block", "mandatory": True, "minimum_state": "observe", "config": {"entities": ["email", "international_phone"]}, "evaluator_revision": "pii-v2", **updates})


def parts():
    return (models.ContentPart(part_id="content", kind="text", role="user", text="Contact synthetic-canary@example.test or +525512345678."),
            models.ContentPart(part_id="boundary", kind="text", role="user", text="Negative boundaries: éalpha@example.test 00+525512345678"))


async def recovered(evaluator, old_pid):
    async with asyncio.timeout(5):
        while evaluator.health()["replacements"] == 0 or old_pid in evaluator.worker_pids:
            await asyncio.sleep(0.01)
    with pytest.raises(ProcessLookupError):
        os.kill(old_pid, 0)
    findings = await evaluator.evaluate(rule(), parts(), asyncio.get_running_loop().time() + 1)
    assert len(findings) == 2
    assert evaluator.health()["admitted"] == 0


def test_two_authenticated_teams_share_normalized_findings_and_isolate_attribution():
    repo = TenantRepository(Path(os.environ["EVOLUTION_TEST_DSN_FILE"]).read_text().strip())
    service = ManagementApi(repo)
    teams = []
    try:
        for _ in range(2):
            tenant = "detector-" + uuid4().hex[:20]
            quota = models.with_resource_digest(models.Quota(quota_id=str(uuid4()), tenant_id=tenant))
            residency = models.with_resource_digest(models.ResidencyProfile(residency_id=str(uuid4()), tenant_id=tenant))
            repo.provision_tenant(models.Tenant(tenant_id=tenant, display_name="Detector team", residency_id=residency.residency_id, quota_id=quota.quota_id), residency, quota)
            tokens = {}
            with repo.transaction(tenant) as tx:
                tx.insert("workloads", "chat", models.Workload(tenant_id=tenant, workload_id="chat", display_name="Chat", mode="remote", allowed_stages=("input", "output"), provider_ids=(), quota_id=quota.quota_id, required_state="observe"))
                for role in ("author", "approver", "workload"):
                    actor = service.identity.provision_principal(tx, role, [role])
                    tokens[role] = service.identity.issue_credential(tx, actor["principal_id"], workload_id="chat" if role == "workload" else None)["one_time_secret"]
            author, approver, workload = (service.identity.authenticate(tokens[role]) for role in ("author", "approver", "workload"))
            command = {"rules": [rule().model_dump(mode="json")], "residency_ref": {"id": residency.residency_id, "version": 1, "digest": residency.digest}, "quota_ref": {"id": quota.quota_id, "version": 1, "digest": quota.digest}}
            with repo.transaction(tenant) as tx:
                candidate = service.governance.submit(tx, author, "shared-controls", command)
                service.governance.review(tx, approver, "shared-controls", 1, outcome="approve", compiled_digest=candidate["compiled_digest"], reason="Same detector controls")
            teams.append((tenant, workload, candidate))

        async def exercise():
            evaluator = PinnedPiiEvaluator(workers=2)
            await evaluator.start()
            pids = evaluator.worker_pids
            try:
                assert len(pids) == 2 and all(pid != os.getpid() for pid in pids)
                assert {cap.stage for cap in evaluator.capabilities()} == {"input", "output"}
                normalized = []
                for tenant, identity, candidate in teams:
                    service.identity.authorize(identity, {"workload"}, tenant_id=tenant, workload_id="chat")
                    policy_ref = {"tenant_id": tenant, "policy_id": candidate["policy_id"], "version": 1, "digest": candidate["compiled_digest"]}
                    with repo.transaction(identity.tenant_id) as tx:
                        policy = service.governance.eligible(tx, policy_ref)
                    findings = await evaluator.evaluate(models.Rule.model_validate(policy["rules"][0]), parts(), asyncio.get_running_loop().time() + 1)
                    normalized.append([finding.model_dump(mode="json") for finding in findings])
                    assert [finding.category for finding in findings] == ["email", "international_phone"]
                    assert all(finding.reason_code == "pii_detected" and finding.evaluator_revision == "pii-v2" for finding in findings)
                    with repo.transaction(identity.tenant_id) as tx:
                        service.audit.append(tx, "evaluation", actor_id=identity.principal_id, workload_id="chat", policy_digest=policy["compiled_digest"], reason_codes=("pii_detected",), action="block", metadata={"stage": "input", "evaluator_revision": "pii-v2", "event_count": len(findings)})
                assert normalized[0] == normalized[1]
                assert models.canonical_json_bytes(teams[0][2]["rules"]) == models.canonical_json_bytes(teams[1][2]["rules"])
                assert teams[0][2]["compiled_digest"] != teams[1][2]["compiled_digest"]
                assert teams[0][1].principal_id != teams[1][1].principal_id
                with pytest.raises(EvolutionError) as denied:
                    service.identity.authorize(teams[0][1], {"workload"}, tenant_id=teams[1][0], workload_id="chat")
                assert denied.value.code == "resource_not_found"
            finally:
                await evaluator.close()
            assert evaluator.worker_pids == ()
            for pid in pids:
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)

        asyncio.run(exercise())
        for tenant, identity, candidate in teams:
            with repo.transaction(tenant) as tx:
                events = [event for event in tx.list("audit") if event["event_type"] == "evaluation"]
                assert len(events) == 1
                assert events[0]["tenant_id"] == tenant and events[0]["actor_id"] == identity.principal_id
                assert events[0]["policy_digest"] == candidate["compiled_digest"]
                assert events[0]["raw_content_retained"] is False
                rows = tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant", {"tenant": tenant})
                persisted = json.dumps([row.data for row in rows])
                assert "synthetic-canary@example.test" not in persisted
                assert "+525512345678" not in persisted
    finally:
        repo.close()


def test_pinned_evaluator_rejects_unsupported_revision_entities_and_surface_before_admission():
    async def exercise():
        evaluator = PinnedPiiEvaluator(workers=1)
        await evaluator.start()
        pids = evaluator.worker_pids
        try:
            for incompatible in (rule(evaluator_revision="arbitrary.module"), rule(config={"entities": ["payment_card"]}), rule(stages=["tool_result"])):
                with pytest.raises(EvolutionError) as rejected:
                    await evaluator.evaluate(incompatible, parts(), asyncio.get_running_loop().time() + 1)
                assert rejected.value.code == "capability_missing"
            with pytest.raises(EvolutionError) as unsupported:
                await evaluator.evaluate(rule(), (models.ContentPart(part_id="json", kind="json", role="user", value={"text": "not supported"}),), asyncio.get_running_loop().time() + 1)
            assert unsupported.value.code == "unsupported_surface"
            assert evaluator.worker_pids == pids
            assert evaluator.health()["admitted"] == 0
            assert len(await evaluator.evaluate(rule(), parts(), asyncio.get_running_loop().time() + 1)) == 2
        finally:
            await evaluator.close()
    asyncio.run(exercise())


def test_evaluator_artifact_pin_and_api_major_fail_before_worker_spawn(monkeypatch):
    from guardrails.evolution import plugins
    with pytest.raises(EvolutionError) as mismatch:
        PinnedPiiEvaluator(api_major=2)
    assert mismatch.value.code == "capability_missing"
    monkeypatch.setitem(plugins.PINNED_ARTIFACTS, "evaluators/pii.py", "0" * 64)
    async def exercise():
        evaluator = PinnedPiiEvaluator(workers=1)
        with pytest.raises(EvolutionError) as invalid:
            await evaluator.start()
        assert invalid.value.code == "evaluator_unavailable"
        assert evaluator.worker_pids == ()
        assert all(cap.status == "unavailable" for cap in evaluator.capabilities())
        await evaluator.close()
    asyncio.run(exercise())


def test_evaluator_deadline_kills_stopped_worker_and_recovers():
    async def exercise():
        evaluator = PinnedPiiEvaluator(workers=1)
        await evaluator.start()
        old_pid = evaluator.worker_pids[0]
        try:
            os.kill(old_pid, signal.SIGSTOP)
            started = asyncio.get_running_loop().time()
            with pytest.raises(EvolutionError) as expired:
                await evaluator.evaluate(rule(), parts(), started + 0.05)
            assert expired.value.code == "evaluation_timeout"
            assert asyncio.get_running_loop().time() - started < 1
            await recovered(evaluator, old_pid)
        finally:
            await evaluator.close()
    asyncio.run(exercise())


def test_evaluator_cancellation_and_queue_bound_reclaim_actual_worker():
    async def exercise():
        evaluator = PinnedPiiEvaluator(workers=1, max_queued_jobs=1, timeout_ms=500)
        await evaluator.start()
        old_pid = evaluator.worker_pids[0]
        try:
            os.kill(old_pid, signal.SIGSTOP)
            running = asyncio.create_task(evaluator.evaluate(rule(), parts(), asyncio.get_running_loop().time() + 1))
            waiting = asyncio.create_task(evaluator.evaluate(rule(), parts(), asyncio.get_running_loop().time() + 1))
            async with asyncio.timeout(1):
                while evaluator.health()["admitted"] != 2:
                    await asyncio.sleep(0.001)
            with pytest.raises(EvolutionError) as saturated:
                await evaluator.evaluate(rule(), parts(), asyncio.get_running_loop().time() + 1)
            assert saturated.value.code == "queue_full"
            waiting.cancel()
            running.cancel()
            results = await asyncio.gather(waiting, running, return_exceptions=True)
            assert all(isinstance(result, asyncio.CancelledError) for result in results)
            await recovered(evaluator, old_pid)
        finally:
            await evaluator.close()
        assert evaluator.worker_pids == ()
    asyncio.run(exercise())


def test_evaluator_crash_recovery_preserves_other_worker_and_pending_jobs():
    async def exercise():
        evaluator = PinnedPiiEvaluator(workers=2)
        await evaluator.start()
        killed_pid, survivor_pid = evaluator.worker_pids
        try:
            os.kill(killed_pid, signal.SIGKILL)
            await asyncio.sleep(0.02)
            with pytest.raises(EvolutionError) as crashed:
                await evaluator.evaluate(rule(), parts(), asyncio.get_running_loop().time() + 1)
            assert crashed.value.code == "evaluator_unavailable"
            assert survivor_pid in evaluator.worker_pids
            assert len(await evaluator.evaluate(rule(), parts(), asyncio.get_running_loop().time() + 1)) == 2
            await recovered(evaluator, killed_pid)
            assert survivor_pid in evaluator.worker_pids
        finally:
            await evaluator.close()
    asyncio.run(exercise())


def test_evaluator_reports_unavailable_when_every_worker_dies_and_recovers():
    async def exercise():
        evaluator = PinnedPiiEvaluator(workers=1)
        await evaluator.start()
        old_pid = evaluator.worker_pids[0]
        try:
            os.kill(old_pid, signal.SIGKILL)
            async with asyncio.timeout(1):
                while evaluator.worker_pids:
                    await asyncio.sleep(0.001)
            assert evaluator.health()["ready"] is False
            assert evaluator.health()["workers"] == evaluator.health()["available_workers"] == 0
            assert all(capability.status == "unavailable" for capability in evaluator.capabilities())
            with pytest.raises(EvolutionError) as crashed:
                await evaluator.evaluate(rule(), parts(), asyncio.get_running_loop().time() + 1)
            assert crashed.value.code == "evaluator_unavailable"
            await recovered(evaluator, old_pid)
            assert evaluator.health()["ready"] is True
            assert all(capability.status == "enabled" for capability in evaluator.capabilities())
        finally:
            await evaluator.close()
    asyncio.run(exercise())
