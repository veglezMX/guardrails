"""Actual bounded-worker registry integration with exact compiled bindings."""
import asyncio
import os
import signal

import pytest
from guardrails.evolution.compiler import PolicyCompiler
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import ContentPart, ListSet, with_resource_digest
from guardrails.evolution.plugins import EvaluatorRegistry, PiiOnlyRegistry
from test_compiler import changed, policy, resource, rule, uid


def compiled(kind="pii", *, action="block"):
    revisions = {"pii": "pii-v2", "secret": "secrets-v1", "term": "lexical-v1",
                 "topic": "lexical-v1", "json_schema": "schema-v1"}
    configs = {"pii": {"entities": ["payment_card"]},
               "secret": {"detectors": ["github_token"]},
               "term": {"list_id": uid(800)}, "topic": {"list_id": uid(800)},
               "json_schema": {"schema": {"type": "object", "required": ["ok"]}}}
    rules = [rule(kind, kind=kind, config=configs[kind], evaluator_revision=revisions[kind],
                  action=action)]
    lists = []
    if kind in {"term", "topic"}:
        lists = [resource(ListSet, tenant_id="team-a", list_id=uid(800), kind="content",
                          deny=("term:restricted" if kind == "term" else "topic:finance",))]
    return PolicyCompiler().compile(policy(rules=rules, resolved_lists=lists))


PARTS = {
    "pii": ContentPart(part_id="p", kind="text", role="system", text="4111 1111 1111 1111"),
    "secret": ContentPart(part_id="p", kind="text", role="retrieval", text="ghp_" + "a" * 36),
    "term": ContentPart(part_id="p", kind="json", role="user", value={"body": "restricted"}),
    "topic": ContentPart(part_id="p", kind="text", role="assistant", text="invoice"),
    "json_schema": ContentPart(part_id="p", kind="json", role="user", value={}),
}


@pytest.mark.parametrize("kind", tuple(PARTS))
@pytest.mark.parametrize("stage", ("input", "output"))
def test_default_registry_dispatches_each_installed_engine_in_reserved_worker(kind, stage):
    async def exercise():
        registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1)
        selected, parts = compiled(kind), (PARTS[kind],)
        registry.validate_declared(selected, stage=stage, parts=parts)
        assert len(registry.declared_capabilities()) == 10
        assert all(item.status == "enabled" for item in registry.declared_capabilities())
        with pytest.raises(EvolutionError) as missing:
            registry.require(selected, stage=stage, parts=parts)
        assert missing.value.code == "evaluator_unavailable"
        await registry.start()
        try:
            registry.require(selected, stage=stage, parts=parts)
            pids = registry._pools["team-a"].worker_pids
            found = await registry.evaluate(
                selected.rules[0], parts, asyncio.get_running_loop().time() + 0.5,
                tenant_id="team-a", policy=selected, stage=stage,
            )
            assert len(found) == 1
            assert found[0].rule_id == selected.rules[0].rule_id
            assert found[0].evaluator_revision == selected.rules[0].evaluator_revision
            assert registry._pools["team-a"].worker_pids == pids
        finally:
            await registry.close()
        assert registry.health()["workers"] == 0
    asyncio.run(exercise())


def test_zero_rule_content_lists_use_same_pool_and_inspect_every_json_leaf():
    async def exercise():
        item = resource(ListSet, tenant_id="team-a", list_id=uid(801), kind="content",
                        deny=("term:restricted",))
        candidate = policy(rules=[], resolved_lists=[item])
        residency = with_resource_digest(changed(candidate.residency, content_lists=(item.list_id,)))
        selected = PolicyCompiler().compile(changed(candidate, residency=residency))
        parts = (ContentPart(part_id="p", kind="json", role="system",
                             value={"a": ["safe", "restricted"]}),)
        registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1)
        with pytest.raises(EvolutionError) as unavailable:
            registry.require(selected)
        assert unavailable.value.code == "evaluator_unavailable"
        await registry.start()
        try:
            found = await registry.evaluate_content_lists(
                selected, parts, asyncio.get_running_loop().time() + 0.5,
                tenant_id="team-a", stage="input")
            assert len(found) == 1 and found[0].rule_id == "content-list:" + item.list_id
            assert found[0].json_pointer == "/a/1"
        finally:
            await registry.close()
    asyncio.run(exercise())


def test_category_transformation_runs_through_the_same_reserved_pool():
    async def exercise():
        selected = compiled(action="redact")
        parts = (PARTS["pii"],)
        registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1)
        await registry.start()
        try:
            deadline = asyncio.get_running_loop().time() + 0.5
            found = await registry.evaluate(selected.rules[0], parts, deadline,
                tenant_id="team-a", policy=selected, stage="input")
            transformed = await registry.transform(selected, parts, found, deadline,
                tenant_id="team-a", stage="input")
            assert transformed[0].text == "[REDACTED:payment_card]"
            assert transformed[0].role == "system"
        finally:
            await registry.close()
    asyncio.run(exercise())


def test_bindings_refuse_uncompiled_foreign_or_changed_policy_before_dispatch():
    async def exercise():
        registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1)
        selected = compiled()
        cases = [
            (None, "input", "team-a", "policy_unresolved"),
            (selected, "input", "team-b", "policy_unresolved"),
            (selected, "retrieval", "team-a", "policy_unresolved"),
            (selected.model_copy(update={"compiled_digest": "0" * 64}),
             "input", "team-a", "policy_unresolved"),
        ]
        for candidate, stage, tenant, code in cases:
            with pytest.raises(EvolutionError) as refused:
                await registry.evaluate(selected.rules[0], (PARTS["pii"],),
                    asyncio.get_running_loop().time() + 1, tenant_id=tenant,
                    policy=candidate, stage=stage)
            assert refused.value.code == code
        assert registry.health()["workers"] == 0
    asyncio.run(exercise())


def test_minimal_pii_catalog_remains_explicit_and_unexpanded():
    assert set(PiiOnlyRegistry.registered_revisions) == {"pii"}
    assert set(EvaluatorRegistry.registered_revisions) == set(PARTS) | {"injection", "bedrock"}
    registry = PiiOnlyRegistry(tenant_ids=("team-a",), workers=1)
    with pytest.raises(EvolutionError) as missing:
        registry.validate_declared(compiled())
    assert missing.value.code == "capability_missing"


def test_other_tenant_keeps_its_worker_when_a_rules_job_is_cancelled():
    async def exercise():
        registry = EvaluatorRegistry(tenant_ids=("team-a", "team-b"), workers=2,
                                     max_queued_jobs=0)
        await registry.start()
        first = compiled()
        second = PolicyCompiler().compile(policy(owner="team-b"))
        parts = (ContentPart(part_id="p", kind="text", role="user",
                             text="example@example.test"),)
        a, b = registry._pools["team-a"], registry._pools["team-b"]
        os.kill(a.worker_pids[0], signal.SIGSTOP)
        pending = asyncio.create_task(registry.evaluate(first.rules[0], (PARTS["pii"],),
            asyncio.get_running_loop().time() + 1, tenant_id="team-a",
            policy=first, stage="input"))
        try:
            async with asyncio.timeout(1):
                while not a.health()["admitted"]:
                    await asyncio.sleep(0.001)
            with pytest.raises(EvolutionError) as full:
                await registry.evaluate(first.rules[0], (PARTS["pii"],),
                    asyncio.get_running_loop().time() + 1, tenant_id="team-a",
                    policy=first, stage="input")
            assert full.value.code == "queue_full"
            found = await registry.evaluate(second.rules[0], parts,
                asyncio.get_running_loop().time() + 1, tenant_id="team-b",
                policy=second, stage="input")
            assert len(found) == 1 and b.health()["workers"] == 1
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await registry.close()
        assert not a.worker_pids and not b.worker_pids
    asyncio.run(exercise())

