import asyncio
import os
import signal

import pytest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import ContentPart, ListSet
from guardrails.evolution.plugins import PiiOnlyRegistry as EvaluatorRegistry
from test_compiler import policy, resource, rule, uid

PARTS = (ContentPart(part_id="text", kind="text", role="user", text="example@example.test"),)


def test_registry_checks_exact_revision_surface_zone_and_live_capability():
    async def exercise():
        registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1)
        with pytest.raises(EvolutionError) as unavailable:
            registry.require(policy())
        assert unavailable.value.code == "evaluator_unavailable"
        await registry.start()
        try:
            registry.require(policy(), stage="input", parts=PARTS, zone="local-shared")
            assert {value.stage for value in registry.capabilities("team-a")} == {"input", "output"}
            for candidate in (policy(rules=[rule(evaluator_revision="dynamic-import")]), policy(rules=[rule(config={"entities": ["ipv4"]})])):
                with pytest.raises(EvolutionError) as missing:
                    registry.require(candidate)
                assert missing.value.code == "capability_missing"
            for controls in ({"deny": ("term:restricted",)}, {"allow_enabled": True, "allow": ("topic:software",)}):
                active_list = resource(ListSet, tenant_id="team-a", list_id=uid(800), kind="content", **controls)
                with pytest.raises(EvolutionError) as unsupported_list:
                    registry.require(policy(resolved_lists=[active_list]))
                assert unsupported_list.value.code == "capability_missing"
            with pytest.raises(EvolutionError) as foreign:
                registry.require(policy(owner="team-b"))
            assert foreign.value.code == "resource_not_found"
            with pytest.raises(EvolutionError) as surface:
                registry.require(policy(), stage="tool_result", parts=PARTS)
            assert surface.value.code == "unsupported_surface"
            with pytest.raises(EvolutionError) as zone:
                registry.require(policy(), zone="external")
            assert zone.value.code == "residency_denied"
            findings = await registry.evaluate(rule(), PARTS, asyncio.get_running_loop().time() + 1, tenant_id="team-a")
            assert len(findings) == 1 and findings[0].category == "email"
        finally:
            await registry.close()
        assert registry.health()["workers"] == 0
    asyncio.run(exercise())


def test_tenant_reserved_worker_survives_other_tenant_saturation_and_idle_crash_recovers():
    async def exercise():
        registry = EvaluatorRegistry(tenant_ids=("team-a", "team-b"), workers=2, max_queued_jobs=0, timeout_ms=400)
        await registry.start()
        a, b = registry._pools["team-a"], registry._pools["team-b"]
        assert len(a.worker_pids) == len(b.worker_pids) == 1
        assert registry.health()["workers"] == 2
        stopped = a.worker_pids[0]
        os.kill(stopped, signal.SIGSTOP)
        busy = asyncio.create_task(registry.evaluate(rule(), PARTS, asyncio.get_running_loop().time() + 1, tenant_id="team-a"))
        try:
            async with asyncio.timeout(1):
                while a.health()["admitted"] != 1:
                    await asyncio.sleep(0.001)
            with pytest.raises(EvolutionError) as full:
                await registry.evaluate(rule(), PARTS, asyncio.get_running_loop().time() + 1, tenant_id="team-a")
            assert full.value.code == "queue_full"
            findings = await registry.evaluate(rule(), PARTS, asyncio.get_running_loop().time() + 1, tenant_id="team-b")
            assert len(findings) == 1
            assert a.health()["admitted"] == 1
            busy.cancel()
            await asyncio.gather(busy, return_exceptions=True)
            killed = b.worker_pids[0]
            os.kill(killed, signal.SIGKILL)
            async with asyncio.timeout(5):
                while killed in b.worker_pids or not b.health()["ready"] or b.health()["replacements"] == 0:
                    await asyncio.sleep(0.01)
            registry.require(policy(owner="team-b"))
            assert killed not in b.worker_pids
            assert len(await registry.evaluate(rule(), PARTS, asyncio.get_running_loop().time() + 1, tenant_id="team-b")) == 1
        finally:
            busy.cancel()
            await asyncio.gather(busy, return_exceptions=True)
            await registry.close()
        assert not a.worker_pids and not b.worker_pids
    asyncio.run(exercise())


def test_registry_refuses_dynamic_tenant_growth_and_invalid_total_capacity():
    for options in ({"tenant_ids": ("team-a", "team-b"), "workers": 1}, {"tenant_ids": ("team-a", "team-a")}, {"workers": 5}, {"max_queued_jobs": 33}, {"api_major": 2}):
        with pytest.raises(EvolutionError):
            EvaluatorRegistry(**options)
    registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1)
    with pytest.raises(EvolutionError) as foreign:
        registry.capabilities("not-configured")
    assert foreign.value.code == "resource_not_found"
    assert registry.health()["workers"] == 0
