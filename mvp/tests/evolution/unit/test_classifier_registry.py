"""Private configured endpoints and live policy-bound classifier readiness."""
import asyncio
import time

import pytest
from guardrails.evolution.classifier import ClassifierEndpoint, ClassifierEndpointConfig
from guardrails.evolution.compiler import PolicyCompiler
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import with_resource_digest
from guardrails.evolution.plugins import EvaluatorRegistry
from test_classifier_contract import Transport, part, policy
from test_compiler import policy as pii_policy


def selected():
    candidate = policy()
    rules = tuple(item.model_copy(update={"stages": ("input", "output")})
                  for item in candidate.rules)
    return PolicyCompiler().compile(candidate.model_copy(
        update={"status": "candidate", "rules": rules}))


def configured(approved, transport):
    config = ClassifierEndpointConfig(provider=approved.providers[0],
        destination=approved.resolved_destinations[0], runtime_zone="local-shared",
        language_profile="en", bearer_token="x" * 40)
    return ClassifierEndpoint(config, transport)


def test_classifier_registry_binds_fixed_private_slot_and_never_creates_request_endpoints():
    async def exercise():
        approved, transport = selected(), Transport()
        endpoint = configured(approved, transport)
        mapping = {"team-a": endpoint}
        registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1,
                                     classifier_endpoints=mapping)
        mapping.clear()
        registry.validate_declared(approved)
        assert len(registry.declared_capabilities()) == 12
        assert len(registry._classifier_endpoints) == 1
        with pytest.raises(EvolutionError) as cold:
            registry.require(approved)
        assert cold.value.code == "evaluator_unavailable"
        await registry.start()
        try:
            registry.require(approved, stage="input", parts=(part(),))
            pids = registry._pools["team-a"].worker_pids
            for stage in ("input", "output"):
                found = await registry.evaluate(approved.rules[0], (part(),),
                    time.monotonic() + 3, policy=approved, stage=stage, tenant_id="team-a")
                assert len(found) == 1 and found[0].category == "injection"
            assert sum(call[1]["method"] == "POST" for call in transport.calls) == 2
            assert registry._pools["team-a"].worker_pids == pids
            assert tuple(registry._classifier_endpoints) == ("team-a",)
            with pytest.raises(EvolutionError) as missing:
                registry.capabilities("foreign")
            assert missing.value.code == "resource_not_found"
        finally:
            await registry.close()
        assert not endpoint.health_snapshot().ready and registry._classifier_monitor_task is None
    asyncio.run(exercise())


@pytest.mark.parametrize("change", ("missing", "provider", "destination", "tenant", "stage"))
def test_unconfigured_or_mismatched_classifier_policy_never_dispatches(change):
    async def exercise():
        approved, transport = selected(), Transport()
        endpoint = configured(approved, transport)
        registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1,
            classifier_endpoints={} if change == "missing" else {"team-a": endpoint})
        tenant, stage = "team-a", "input"
        if change == "provider":
            provider = with_resource_digest(approved.providers[0].model_copy(
                update={"credential_ref": "changed-auth"}))
            approved = PolicyCompiler().compile(approved.model_copy(
                update={"status": "candidate", "providers": (provider,)}))
        if change == "destination":
            destination = with_resource_digest(approved.resolved_destinations[0].model_copy(
                update={"host": "changed.internal"}))
            approved = PolicyCompiler().compile(approved.model_copy(
                update={"status": "candidate", "resolved_destinations": (destination,)}))
        if change == "tenant":
            tenant = "team-b"
        if change == "stage":
            stage = "retrieval"
        with pytest.raises(EvolutionError):
            await registry.evaluate(approved.rules[0], (part(),), time.monotonic() + 3,
                policy=approved, stage=stage, tenant_id=tenant)
        assert not transport.calls and registry.health()["workers"] == 0
        await registry.close()
    asyncio.run(exercise())


def test_classifier_health_failure_and_recovery_do_not_disable_pure_rules():
    class Flaky(Transport):
        unavailable = False

        async def request(self, route, **kwargs):
            if self.unavailable:
                raise EvolutionError("evaluator_unavailable")
            return await super().request(route, **kwargs)

    async def exercise():
        approved, transport = selected(), Flaky()
        endpoint = configured(approved, transport)
        registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1,
                                     classifier_endpoints={"team-a": endpoint})
        await registry.start()
        try:
            registry.require(approved)
            transport.unavailable = True
            async with asyncio.timeout(1):
                while endpoint.health_snapshot().ready:
                    await asyncio.sleep(0.01)
            with pytest.raises(EvolutionError) as unavailable:
                registry.require(approved)
            assert unavailable.value.code == "evaluator_unavailable"
            registry.require(PolicyCompiler().compile(pii_policy()), stage="input", parts=(part(),))
            classifier = [item for item in registry.capabilities("team-a")
                          if item.capability_id == "prompt-injection-en"]
            assert len(classifier) == 2 and all(item.status == "unavailable" for item in classifier)
            transport.unavailable = False
            async with asyncio.timeout(1):
                while not endpoint.health_snapshot().ready:
                    await asyncio.sleep(0.01)
            registry.require(approved)
        finally:
            await registry.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("mapping", ("foreign", "wrong-owner", "unbounded", "wrong-type"))
def test_endpoint_configuration_is_finite_and_scoped_before_start(mapping):
    approved, transport = selected(), Transport()
    endpoint = configured(approved, transport)
    values = {"team-a": endpoint}
    if mapping in {"foreign", "wrong-owner"}:
        values = {"team-b": endpoint}
    elif mapping == "unbounded":
        values = {f"team-{i}": endpoint for i in range(5)}
    else:
        values = []
    with pytest.raises(EvolutionError):
        EvaluatorRegistry(tenant_ids=("team-a",) if mapping != "wrong-owner" else (),
                          workers=1, classifier_endpoints=values)
    assert not transport.calls


def test_spanless_classifier_redact_is_not_an_activatable_capability():
    approved, transport = selected(), Transport()
    endpoint = configured(approved, transport)
    registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1,
                                 classifier_endpoints={"team-a": endpoint})
    declared = [row for row in registry.declared_capabilities()
                if row.capability_id == "prompt-injection-en"]
    assert len(declared) == 2
    assert all(set(row.actions) == {"allow", "warn", "block"} for row in declared)
    rule = approved.rules[0].model_copy(update={"action": "redact"})
    with pytest.raises(EvolutionError) as refused:
        PolicyCompiler().compile(approved.model_copy(
            update={"status": "candidate", "rules": (rule,)}))
    assert refused.value.code == "unsafe_transformation"
    assert not transport.calls


def test_platform_provider_cannot_route_background_health_to_foreign_tenant_destination():
    approved, transport = selected(), Transport()
    provider = with_resource_digest(approved.providers[0].model_copy(
        update={"tenant_id": "_platform"}))
    destination = with_resource_digest(approved.resolved_destinations[0].model_copy(
        update={"tenant_id": "team-b"}))
    endpoint = ClassifierEndpoint(ClassifierEndpointConfig(
        provider=provider, destination=destination, runtime_zone="local-shared",
        language_profile="en", bearer_token="x" * 40), transport)
    with pytest.raises(EvolutionError) as refused:
        EvaluatorRegistry(tenant_ids=("team-a",), workers=1,
                          classifier_endpoints={"team-a": endpoint})
    assert refused.value.code == "validation_failed"
    assert not transport.calls


def test_registry_close_cancels_inflight_classifier_transport_and_refuses_new_admission():
    class Pending(Transport):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = False

        async def request(self, route, **kwargs):
            if kwargs["method"] == "GET":
                return await super().request(route, **kwargs)
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def exercise():
        approved, transport = selected(), Pending()
        endpoint = configured(approved, transport)
        registry = EvaluatorRegistry(tenant_ids=("team-a",), workers=1,
                                     classifier_endpoints={"team-a": endpoint})
        await registry.start()
        task = asyncio.create_task(registry.evaluate(approved.rules[0], (part(),),
            time.monotonic() + 3, policy=approved, stage="input", tenant_id="team-a"))
        async with asyncio.timeout(1):
            await transport.started.wait()
            await registry.close()
        assert task.cancelled() and transport.cancelled
        assert not registry._classifier_jobs
        assert not endpoint.health_snapshot().ready
        with pytest.raises(EvolutionError) as refused:
            await registry.evaluate(approved.rules[0], (part(),), time.monotonic() + 3,
                policy=approved, stage="input", tenant_id="team-a")
        assert refused.value.code == "evaluator_unavailable"
    asyncio.run(exercise())


