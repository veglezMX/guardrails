"""Synthetic subprocess boundary tests; no database, network or model access."""
import asyncio
import math
import os
import signal
import time
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest
from guardrails.evolution import rules as pure
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import Finding, canonical_json_bytes
from guardrails.evolution.rules import RulesBinding
from guardrails.evolution.rules_process import MAX_WIRE_BYTES, RulesProcessPool
from guardrails.evolution.rules_worker import execute
from test_deterministic_rules import content_list, policy, rule, structured, text


def run(function):
    return asyncio.run(function())


async def using_pool(body, **settings):
    pool = RulesProcessPool(workers=1, **settings)
    try:
        await pool.start()
        return await body(pool)
    finally:
        await pool.close()
        assert not pool.worker_pids


@pytest.mark.parametrize("kind,part,lists,category", [
    ("pii", text("a@example.test"), (), "email"),
    ("secret", text("AKIAABCDEFGHIJKLMNOP"), (), "aws_access_key"),
    ("term", structured({"a/b": ["blocked"]}), (content_list(deny=("term:blocked",)),), "term"),
    ("topic", text("budget"), (content_list(deny=("topic:finance",)),), "topic"),
    ("json_schema", text("[]"), (), "json_schema"),
])
def test_each_deterministic_engine_executes_in_real_worker(kind, part, lists, category):
    selected = rule(kind)
    binding = RulesBinding(policy(selected, lists=lists), "input")

    async def scenario(pool):
        # Fresh child imports the real functions; parent CPU must not run them.
        with patch.object(pure, "evaluate_rule", side_effect=AssertionError("parent detector")):
            found = await pool.bound(binding).evaluate(selected, (part,), time.monotonic() + 1)
        assert len(found) == 1 and found[0].category == category
        assert found[0].rule_id == selected.rule_id
        assert found[0].json_pointer == ("/a~1b/0" if kind == "term" else None)

    run(lambda: using_pool(scenario))


def test_bound_requests_share_fixed_pool_and_deeply_immutable_policy():
    selected = rule(config={"entities": ["email"]})
    source = policy(selected)
    binding = RulesBinding(source, "input")

    async def scenario(pool):
        first = pool.bound(binding)
        second = pool.bound(RulesBinding(policy(rule(config={"entities": ["payment_card"]})), "output"))
        ids = pool.worker_pids
        with pytest.raises(FrozenInstanceError):
            first.binding = second.binding
        source.rules[0].config.__dict__["entities"] = ("payment_card",)
        original = binding.policy().rules[0]
        assert [x.category for x in await first.evaluate(original, (text("a@example.test"),), time.monotonic()+1)] == ["email"]
        assert [x.category for x in await second.evaluate(second.binding.policy().rules[0], (text("4111111111111111"),), time.monotonic()+1)] == ["payment_card"]
        assert pool.worker_pids == ids and first.pool is second.pool
        assert pool.replacements == 0

    run(lambda: using_pool(scenario))


def test_zero_rule_content_constraints_and_transform_use_same_worker():
    item = content_list(deny=("term:blocked",))
    binding = RulesBinding(policy(lists=(item,), content_ids=(item.list_id,)), "input")

    async def scenario(pool):
        bound = pool.bound(binding)
        before = pool.worker_pids
        parts = (structured({"leaf": "blocked", "number": 3}),)
        with patch.object(pure, "evaluate_content_lists", side_effect=AssertionError("parent list detector")), patch.object(pure, "transform_rule_findings", side_effect=AssertionError("parent transform")):
            found = await bound.content_lists(parts, time.monotonic()+1)
            transformed = await bound.transform(parts, found, time.monotonic()+1)
        assert found[0].rule_id == "content-list:" + item.list_id
        assert transformed[0].value == {"leaf": "[REDACTED:term]", "number": 3}
        assert parts[0].value["leaf"] == "blocked"
        assert before == pool.worker_pids

    run(lambda: using_pool(scenario))


def test_schema_revalidation_rejects_unsafe_transform_inside_worker():
    detector = rule(config={"entities": ["email"]})
    schema = rule("json_schema", {"schema": {"type": "object", "properties": {"mail": {"type": "string", "const": "a@example.test"}}}})
    binding = RulesBinding(policy(detector, schema), "input")

    async def scenario(pool):
        bound = pool.bound(binding)
        parts = (structured({"mail": "a@example.test"}),)
        found = await bound.evaluate(detector, parts, time.monotonic()+1)
        with pytest.raises(EvolutionError, match="Unsafe transformation"):
            await bound.transform(parts, found, time.monotonic()+1)
        assert parts[0].value == {"mail": "a@example.test"}

    run(lambda: using_pool(scenario))


@pytest.mark.parametrize("part", [text("-----BEGIN PRIVATE KEY-----\nunfinished"), structured({"key": "-----BEGIN PRIVATE KEY-----\nunfinished"})], ids=("text", "json"))
def test_incomplete_private_key_retains_finding_but_refuses_transformation(part):
    selected = rule("secret", {"detectors": ["private_key"]}, action="warn")
    binding = RulesBinding(policy(selected), "input")

    async def scenario(pool):
        bound = pool.bound(binding)
        found = await bound.evaluate(selected, (part,), time.monotonic()+1)
        assert len(found) == 1 and found[0].category == "private_key"
        assert found[0].start is None and found[0].end is None
        assert found[0].reason_code == "secret_detected"
        assert found[0].json_pointer == ("/key" if part.kind == "json" else None)
        with pytest.raises(EvolutionError, match="Unsafe transformation"):
            await bound.transform((part,), found, time.monotonic()+1)
    run(lambda: using_pool(scenario))


@pytest.mark.parametrize("change", [{"rule_id": "other"}, {"action": "warn"}, {"evaluator_revision": "pii-v1"}])
def test_worker_rejects_unbound_rule_without_changing_public_signature(change):
    selected = rule()
    binding = RulesBinding(policy(selected), "input")

    async def scenario(pool):
        with pytest.raises(EvolutionError, match="Policy unresolved"):
            await pool.bound(binding).evaluate(selected.model_copy(update=change), (text("safe"),), time.monotonic()+1)

    run(lambda: using_pool(scenario))


def test_capabilities_remain_input_output_only_and_fail_closed_before_start():
    pool = RulesProcessPool(workers=1)
    capabilities = pool.capabilities()
    assert len(capabilities) == 10
    assert {row.capability_id for row in capabilities} == {"rules-pii", "rules-secret", "rules-term", "rules-topic", "rules-json-schema"}
    for row in capabilities:
        assert row.stage in {"input", "output"} and row.part_kinds == ("text", "json")
        assert row.status == "unavailable" and row.max_input_bytes == 65536
        assert ("redact" in row.actions) == (row.capability_id not in {"rules-topic", "rules-json-schema"})
    with pytest.raises(EvolutionError, match="Capability missing"):
        pool.bound(RulesBinding(policy(), "retrieval"))
    assert pool.worker_pids == ()


def test_pool_without_binding_cannot_fall_back_to_legacy_detector():
    async def scenario():
        with pytest.raises(EvolutionError, match="Policy unresolved"):
            await RulesProcessPool(workers=1).evaluate(rule(), (text("a@example.test"),), time.monotonic()+1)
    run(scenario)


@pytest.mark.parametrize("bad", [True, None, math.inf, math.nan])
def test_invalid_deadline_refuses_without_starting_process(bad):
    async def scenario():
        pool = RulesProcessPool(workers=1)
        with pytest.raises(EvolutionError, match="Validation failed"):
            await pool.bound(RulesBinding(policy(rule()), "input")).evaluate(rule(), (text("safe"),), bad)
        assert not pool.worker_pids
    run(scenario)


def test_timeout_kills_exact_stopped_worker_and_replaces_with_usable_worker():
    async def scenario(pool):
        selected = rule()
        bound = pool.bound(RulesBinding(policy(selected), "input"))
        old = pool.worker_pids[0]
        os.kill(old, signal.SIGSTOP)
        with pytest.raises(EvolutionError, match="Evaluation timeout"):
            await bound.evaluate(selected, (text("safe"),), time.monotonic()+0.04)
        async with asyncio.timeout(5):
            while pool.replacements != 1:
                await asyncio.sleep(0.01)
        assert old not in pool.worker_pids
        assert await bound.evaluate(selected, (text("safe"),), time.monotonic()+1) == ()
    run(lambda: using_pool(scenario))


def test_cancellation_reclaims_worker_and_queue_admission_is_bounded():
    async def scenario(pool):
        selected = rule()
        bound = pool.bound(RulesBinding(policy(selected), "input"))
        old = pool.worker_pids[0]
        os.kill(old, signal.SIGSTOP)
        task = asyncio.create_task(bound.evaluate(selected, (text("safe"),), time.monotonic()+1))
        try:
            while not pool._busy:
                await asyncio.sleep(0)
            with pytest.raises(EvolutionError, match="Queue full"):
                await bound.content_lists((text("safe"),), time.monotonic()+1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with asyncio.timeout(5):
                while pool.replacements != 1:
                    await asyncio.sleep(0.01)
            assert old not in pool.worker_pids and pool.health()["admitted"] == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    run(lambda: using_pool(scenario, max_queued_jobs=0))


def payload():
    selected = rule()
    bound = RulesProcessPool(workers=1).bound(RulesBinding(policy(selected), "input"))
    result = bound._payload("evaluate", (text("safe"),), time.monotonic()+1)
    result["rule"] = selected.model_dump(mode="json")
    return result


@pytest.mark.parametrize("change", [{"extra": "secret"}, {"operation": "unknown"}, {"stage": "retrieval"}, {"deadline": True}, {"parts": []}, {"policy": "{}"}])
def test_worker_protocol_rejects_closed_schema_and_unsupported_stages(change):
    with pytest.raises((EvolutionError, ValueError)):
        execute({**payload(), **change})


def test_worker_refuses_equivalent_noncanonical_policy_bytes():
    value = payload()
    value["policy"] = " " + value["policy"]
    with pytest.raises(EvolutionError, match="Policy unresolved"):
        execute(value)


@pytest.mark.parametrize("change", [{"rule_id": "foreign"}, {"part_id": "foreign"}, {"category": "invented"}, {"score": 0.5}, {"json_pointer": "/missing"}, {"end": 999}])
def test_parent_decoder_rejects_malformed_findings_without_detector_execution(change):
    selected = rule()
    bound = RulesProcessPool(workers=1).bound(RulesBinding(policy(selected), "input"))
    found = Finding(rule_id=selected.rule_id, part_id="p", category="email", reason_code="pii_detected", start=0, end=14, score=1.0, evaluator_revision="pii-v2")
    result = {"findings": [{**found.model_dump(mode="json"), **change}]}
    with pytest.raises(EvolutionError, match="Inspection incomplete"):
        bound._findings(result, (text("a@example.test"),), selected)


def test_json_decoder_checks_actual_leaf_and_pointer_escaping():
    selected = rule()
    bound = RulesProcessPool(workers=1).bound(RulesBinding(policy(selected), "input"))
    found = Finding(rule_id=selected.rule_id, part_id="p", category="email", reason_code="pii_detected", start=0, end=14, json_pointer="/a~1b/0", score=1.0, evaluator_revision="pii-v2")
    assert bound._findings({"findings": [found.model_dump(mode="json")]}, (structured({"a/b": ["a@example.test"]}),), selected) == (found,)
    with pytest.raises(EvolutionError):
        bound._findings({"findings": [found.model_dump(mode="json")]}, (structured({"a/b": [14]}),), selected)


def test_rules_wire_limit_can_carry_maximum_findings_above_legacy_limit():
    value = payload()
    value["operation"] = "transform"
    del value["rule"]
    found = Finding(rule_id="pii", part_id="p", category="email", reason_code="pii_detected", start=0, end=14, score=1.0, evaluator_revision="pii-v2")
    value["findings"] = [found.model_dump(mode="json")] * 4096
    size = len(canonical_json_bytes(value))
    assert 512*1024 < size < MAX_WIRE_BYTES
    assert RulesProcessPool._max_wire_bytes == RulesProcessPool._max_result_bytes == MAX_WIRE_BYTES


def test_actual_transport_carries_4096_finding_transform_without_legacy_limit():
    async def scenario(pool):
        selected = rule(config={"entities": ["email"]})
        bound = pool.bound(RulesBinding(policy(selected), "input"))
        parts = (text("a@example.test"),)
        found = await bound.evaluate(selected, parts, time.monotonic()+1)
        transformed = await bound.transform(parts, found*4096, time.monotonic()+1)
        assert transformed[0].text == "[REDACTED:email]"
    run(lambda: using_pool(scenario))


@pytest.mark.parametrize("raw", [b'{"duplicate":1,"duplicate":"private-marker"}\n', b'{"secret":"private-marker"}\n', b'NaN\n', b'[]\n'])
def test_actual_worker_malformed_protocol_emits_only_finite_error(raw):
    async def scenario():
        process = await asyncio.create_subprocess_exec(*RulesProcessPool._worker_command(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(5):
                assert await process.stdout.readline() == b'{"api_major":1,"ready":true}\n'
                process.stdin.write(raw)
                await process.stdin.drain()
                result = await process.stdout.readline()
                assert result == b'{"error":"validation_failed"}\n'
                assert b"private-marker" not in result
        finally:
            if process.returncode is None:
                process.kill()
            await process.communicate()
    run(scenario)


def test_artifact_mismatch_refuses_before_spawn(monkeypatch):
    import guardrails.evolution.rules_process as module

    monkeypatch.setattr(module, "PINNED_RULE_ARTIFACTS", {"evolution/rules_worker.py": "0"*64})
    async def scenario():
        pool = RulesProcessPool(workers=1)
        with pytest.raises(EvolutionError, match="Evaluator unavailable"):
            await pool.start()
        assert not pool.worker_pids
    run(scenario)
