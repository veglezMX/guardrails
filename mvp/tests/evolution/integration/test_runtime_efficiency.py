"""Real PostgreSQL checks for transaction-scoped runtime work reduction."""

import asyncio
import os
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from guardrails.evolution import models
from guardrails.evolution.deadlines import current_deadline
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.storage import TenantRepository
from sqlalchemy import event
from test_evaluation_runtime import fixture_system, request, runtime


@pytest.fixture
def repository():
    repo = TenantRepository(Path(os.environ["EVOLUTION_TEST_DSN_FILE"]).read_text().strip())
    try:
        yield repo
    finally:
        repo.close()


def observe_queries(engine):
    queries = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        queries.append((statement, dict(parameters)))

    event.listen(engine, "before_cursor_execute", capture)
    return queries, capture


def acquired_keys(queries):
    return [parameters["key"] for sql, parameters in queries if sql.startswith("SELECT pg_advisory_xact_lock(")]


def try_lock(tx, key):
    return tx.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))", {"key": key}).scalar_one()


def test_repeated_lock_stays_exclusive_through_commit_and_reacquires_next_transaction(repository):
    tenant = "lock-" + uuid4().hex[:16]
    scoped = tenant + ":governance-admission"
    queries, capture = observe_queries(repository.engine)
    try:
        with repository.transaction(tenant) as first:
            first.lock("governance-admission")
            first.lock("governance-admission")
            with repository.transaction(tenant) as contender:
                assert try_lock(contender, scoped) is False
            with repository.transaction("other-" + tenant) as other:
                other.lock("governance-admission")
            assert acquired_keys(queries) == [scoped, "other-" + scoped]
        with repository.transaction(tenant) as next_transaction:
            next_transaction.lock("governance-admission")
            next_transaction.lock("governance-admission")
            with repository.transaction(tenant) as contender:
                assert try_lock(contender, scoped) is False
        with repository.transaction(tenant) as after_commit:
            assert try_lock(after_commit, scoped) is True
        assert acquired_keys(queries) == [scoped, "other-" + scoped, scoped]
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)


def test_repeated_lock_cache_uses_full_tenant_scope(repository):
    first_scope, second_scope = "one-" + uuid4().hex[:16], "two-" + uuid4().hex[:16]
    queries, capture = observe_queries(repository.engine)
    try:
        with repository.transaction(first_scope) as tx:
            tx.lock("governance-admission")
            # Exercise the trusted connection scope-switch primitive directly;
            # no principal or public request can select these scopes.
            tx.execute("SELECT set_config('guardrails.tenant_id', :tenant, true)", {"tenant": second_scope})
            tx.tenant_id = second_scope
            tx.lock("governance-admission")
            tx.lock("governance-admission")
            tx.execute("SELECT set_config('guardrails.tenant_id', :tenant, true)", {"tenant": first_scope})
            tx.tenant_id = first_scope
            tx.lock("governance-admission")
            with repository.transaction(first_scope) as contender:
                assert try_lock(contender, first_scope + ":governance-admission") is False
                assert try_lock(contender, second_scope + ":governance-admission") is False
        assert acquired_keys(queries) == [first_scope + ":governance-admission", second_scope + ":governance-admission"]
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)


def test_cached_lock_still_rejects_expired_request_budget(repository):
    with repository.transaction("deadline-" + uuid4().hex[:16]) as tx:
        tx.lock("governance-admission")
        token = current_deadline.set(time.monotonic() - 1)
        try:
            with pytest.raises(EvolutionError) as denied:
                tx.lock("governance-admission")
            assert denied.value.code == "upstream_timeout"
        finally:
            current_deadline.reset(token)


def test_savepoint_rollback_invalidates_lock_memo_and_reacquires_exclusive_lock(repository):
    tenant = "savepoint-" + uuid4().hex[:16]
    scoped = tenant + ":governance-admission"
    queries, capture = observe_queries(repository.engine)
    try:
        with repository.transaction(tenant) as tx:
            nested = tx.connection.begin_nested()
            tx.lock("governance-admission")
            tx.lock("governance-admission")
            with repository.transaction(tenant) as contender:
                assert try_lock(contender, scoped) is False
            nested.rollback()
            with repository.transaction(tenant) as contender:
                assert try_lock(contender, scoped) is True
            tx.lock("governance-admission")
            tx.lock("governance-admission")
            with repository.transaction(tenant) as contender:
                assert try_lock(contender, scoped) is False
        assert acquired_keys(queries) == [scoped, scoped]
    finally:
        event.remove(repository.engine, "before_cursor_execute", capture)


def test_input_output_each_resolve_baseline_once_per_transaction_with_real_detector_and_audit(tmp_path):
    system = fixture_system(tmp_path)

    async def exercise():
        service = runtime(system)
        await service.start()
        queries, capture = observe_queries(service.repository.engine)
        try:
            identity = service.identity.authenticate(system["tokens"]["workload"])
            context_id = None
            for stage in ("input", "output"):
                queries.clear()
                value = models.EvaluationRequest.model_validate(request(system, stage=stage, context_id=context_id))
                started = time.monotonic()
                decision = await service.evaluate(identity, value, started=started, deadline=started + 0.5)
                context_id = decision.context_id
                assert decision.action == "allow" and decision.would_be_action == "block"
                assert decision.findings and decision.evaluator_revisions == ("pii-v2",)
                assert decision.audit_status == "persisted" and decision.evaluation_ms < 500
                assert sum(parameters.get("kind") == "baseline" and parameters.get("key") == "current" for _, parameters in queries) == 2
                assert acquired_keys(queries).count(system["tenant"] + ":governance-admission") == 2
                async with asyncio.timeout(2):
                    while service._cleanup:
                        await asyncio.sleep(0.005)
            with system["repo"].transaction(system["tenant"]) as tx:
                assert tx.get("evaluation_contexts", context_id)["state"] == "completed"
                assert len([item for item in tx.list("audit") if item["event_type"] == "evaluation"]) == 2
                assert all(item["state"] != "active" for item in tx.list("quota_leases"))
        finally:
            event.remove(service.repository.engine, "before_cursor_execute", capture)
            await service.close()
            service.repository.close()

    try:
        asyncio.run(exercise())
    finally:
        system["service"].audit.close()
        system["repo"].close()


@pytest.mark.parametrize("change,code", [("revoke", "policy_revoked"), ("baseline", "floor_conflict")])
def test_finalization_rereads_governance_after_real_detector_completes(tmp_path, monkeypatch, change, code):
    system = fixture_system(tmp_path)
    service = system["service"]
    stronger_ref = None
    if change == "baseline":
        body = deepcopy(system["payload"])
        body["rules"][0].update(mandatory=True, minimum_state="block")
        with system["repo"].transaction(system["tenant"]) as tx:
            stronger = service.governance.submit(tx, system["actors"]["author"], "stronger", body)
            service.governance.review(tx, system["actors"]["approver"], "stronger", 1, outcome="approve", compiled_digest=stronger["compiled_digest"], reason="Stricter baseline")
        stronger_ref = {"tenant_id": system["tenant"], "policy_id": "stronger", "version": 1, "digest": stronger["compiled_digest"]}

    def change_after_admission():
        with system["repo"].transaction(system["tenant"]) as tx:
            if change == "revoke":
                service.governance.review(tx, system["actors"]["approver"], "policy", 1, outcome="revoke", compiled_digest=system["policy_ref"]["digest"], reason="Revoked while evaluating")
            else:
                service.governance.publish_baseline(tx, system["actors"]["tenant_admin"], [stronger_ref], 1)

    async def exercise():
        instance = runtime(system)
        await instance.start()
        detected = []
        actual_evaluate = instance.registry.evaluate

        async def evaluate_then_change(*args, **kwargs):
            findings = await actual_evaluate(*args, **kwargs)
            detected.append(findings)
            await asyncio.to_thread(change_after_admission)
            return findings

        monkeypatch.setattr(instance.registry, "evaluate", evaluate_then_change)
        try:
            identity = instance.identity.authenticate(system["tokens"]["workload"])
            value = models.EvaluationRequest.model_validate(request(system))
            started = time.monotonic()
            with pytest.raises(EvolutionError) as denied:
                await instance.evaluate(identity, value, started=started, deadline=started + 0.5)
            assert denied.value.code == code
            assert len(detected) == 1 and detected[0]
            assert time.monotonic() - started < 0.5
            async with asyncio.timeout(2):
                while instance._cleanup:
                    await asyncio.sleep(0.005)
            with system["repo"].transaction(system["tenant"]) as tx:
                assert not [item for item in tx.list("audit") if item["event_type"] == "evaluation"]
                assert [item["state"] for item in tx.list("evaluation_contexts")] == ["evaluating_input"]
                assert all(item["state"] != "active" for item in tx.list("quota_leases"))
        finally:
            await instance.close()
            instance.repository.close()

    try:
        asyncio.run(exercise())
    finally:
        service.audit.close()
        system["repo"].close()
