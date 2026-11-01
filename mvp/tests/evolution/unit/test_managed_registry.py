"""Managed registry admission, shared concurrency and safe worker masking."""
import asyncio
import socket
import time

import pytest
from guardrails.evolution.compiler import PolicyCompiler
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.managed_endpoint import BedrockEndpoint
from guardrails.evolution.models import ContentPart, Rule, with_resource_digest
from guardrails.evolution.plugins import EvaluatorRegistry
from test_bedrock import CREDENTIALS, FIXTURE, REGION, Transport, modify, part, policy


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("synthetic registry test attempted external connection")
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def selected(tenant="bedrock-tests"):
    value = policy()
    updates = {"tenant_id": tenant, "status": "candidate"}
    for name in ("residency", "quota"):
        updates[name] = with_resource_digest(getattr(value, name).model_copy(update={"tenant_id": tenant}))
    for name in ("providers", "resolved_destinations", "resolved_lists"):
        updates[name] = tuple(with_resource_digest(row.model_copy(update={"tenant_id": tenant}))
                              for row in getattr(value, name))
    return PolicyCompiler().compile(value.model_copy(update=updates)).model_copy(update={"status": "approved"})


def endpoint(value, transport, *, enabled=True):
    return BedrockEndpoint(provider=value.providers[0], destination=value.resolved_destinations[0],
        runtime_zone="local-shared", region=REGION, credentials=CREDENTIALS,
        transport=transport, enabled=enabled)


def run(registry, value, parts=None):
    return registry.evaluate(value.rules[0], parts or (part(),), time.monotonic() + 1,
        policy=value, stage="input", tenant_id=value.tenant_id)


def test_optional_managed_engine_has_no_ambient_credentials_or_default_dispatch():
    async def exercise():
        value, transport = selected(), Transport()
        for configured in (False, True):
            registry = EvaluatorRegistry(tenant_ids=(value.tenant_id,), workers=1,
                bedrock_endpoints={value.tenant_id: endpoint(value, transport, enabled=False)} if configured else {})
            try:
                with pytest.raises(EvolutionError):
                    registry.validate_declared(value)
                with pytest.raises(EvolutionError):
                    await run(registry, value)
                if configured:
                    assert all(row.status == "disabled" for row in registry.capabilities(value.tenant_id)
                               if row.capability_id == "managed-bedrock")
                assert not transport.calls
            finally:
                await registry.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("change", ("local-only", "provider", "destination", "tenant", "stage", "json", "compiled-digest"))
def test_managed_binding_refuses_before_dispatch(change):
    async def exercise():
        value, transport = selected(), Transport()
        registry = EvaluatorRegistry(tenant_ids=(value.tenant_id,), workers=1,
            bedrock_endpoints={value.tenant_id: endpoint(value, transport)})
        tenant, stage, parts = value.tenant_id, "input", (part(),)
        if change == "local-only":
            value = modify(value, "residency", external_processing=False)
        if change == "provider":
            value = modify(value, "providers", credential_ref="substitution")
        if change == "destination":
            value = modify(value, "resolved_destinations", cidrs=("55.0.0.0/8",))
        if change == "tenant":
            tenant = "foreign"
        if change == "stage":
            stage = "retrieval"
        if change == "json":
            parts = (part().model_copy(update={"kind": "json", "text": None, "value": {"safe": "text"}}),)
        if change == "compiled-digest":
            value = value.model_copy(update={"compiled_digest": "0" * 64})
        try:
            with pytest.raises(EvolutionError):
                await registry.evaluate(value.rules[0], parts, time.monotonic() + 1,
                    policy=value, stage=stage, tenant_id=tenant)
            assert not transport.calls
        finally:
            await registry.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("case_id,expected", (
    ("unique-unicode-mask", "🙂 mail [REDACTED:managed]!"),
    ("multi-mask", "[REDACTED:managed] emails [REDACTED:managed]"),
    ("ambiguous-repeated-mask", None), ("blocked", None),
))
def test_managed_safe_mask_uses_existing_rules_pool_and_unsafe_mask_refuses(case_id, expected):
    async def exercise():
        value = selected()
        row = next(item for item in FIXTURE["cases"] if item["id"] == case_id)
        transport = Transport(row["response"])
        registry = EvaluatorRegistry(tenant_ids=(value.tenant_id,), workers=1,
            bedrock_endpoints={value.tenant_id: endpoint(value, transport)})
        await registry.start()
        try:
            registry.require(value, stage="input", parts=(part(row["text"]),))
            pids = registry._pools[value.tenant_id].worker_pids
            parts = (part(row["text"]),)
            found = await run(registry, value, parts)
            if expected is None:
                with pytest.raises(EvolutionError) as error:
                    await registry.transform(value, parts, found, time.monotonic() + 1,
                        stage="input", tenant_id=value.tenant_id)
                assert error.value.code == "unsafe_transformation"
            else:
                output = await registry.transform(value, parts, found, time.monotonic() + 1,
                    stage="input", tenant_id=value.tenant_id)
                assert output[0].text == expected
                assert output[0].model_dump(exclude={"text"}) == parts[0].model_dump(exclude={"text"})
            assert len(transport.calls) == 1
            assert registry._pools[value.tenant_id].worker_pids == pids
            assert not registry._bedrock_jobs
        finally:
            await registry.close()
    asyncio.run(exercise())


def test_managed_limit_is_per_tenant_across_request_bindings_and_shutdown_cancels_dispatch():
    class Pending(Transport):
        cancelled = 0

        async def request(self, route, **kwargs):
            self.calls.append((route, kwargs))
            if route.provider.tenant_id == "team-b":
                return await Transport().request(route, **kwargs)
            try:
                await asyncio.Future()
            finally:
                self.cancelled += 1

    async def exercise():
        a, b, transport = selected(), selected("team-b"), Pending()
        endpoints = {value.tenant_id: endpoint(value, transport) for value in (a, b)}
        registry = EvaluatorRegistry(tenant_ids=(a.tenant_id, b.tenant_id), workers=2,
                                     bedrock_endpoints=endpoints)
        endpoints.clear()
        tasks = [asyncio.create_task(run(registry, a)) for _ in range(4)]
        async with asyncio.timeout(0.4):
            while len(transport.calls) < 4:
                await asyncio.sleep(0)
        with pytest.raises(EvolutionError) as full:
            await run(registry, a)
        assert full.value.code == "queue_full" and len(transport.calls) == 4
        assert await run(registry, b) == ()
        await registry.close()
        assert all(task.cancelled() for task in tasks)
        assert transport.cancelled == 4 and not registry._bedrock_jobs
        with pytest.raises(EvolutionError) as closed:
            await run(registry, a)
        assert closed.value.code == "evaluator_unavailable"
        assert len(registry._bedrock_endpoints) == 2
    asyncio.run(exercise())


def test_managed_destination_ownership_is_checked_before_configuration_is_admitted():
    value, transport = selected(), Transport()
    foreign = with_resource_digest(value.resolved_destinations[0].model_copy(update={"tenant_id": "foreign"}))
    with pytest.raises(EvolutionError):
        EvaluatorRegistry(tenant_ids=(value.tenant_id,), workers=1,
            bedrock_endpoints={value.tenant_id: BedrockEndpoint(provider=value.providers[0],
                destination=foreign, runtime_zone="local-shared", region=REGION,
                credentials=CREDENTIALS, transport=transport, enabled=True)})
    assert not transport.calls


def test_managed_only_policy_requires_the_masking_worker_during_admission():
    async def exercise():
        value, transport = selected(), Transport()
        registry = EvaluatorRegistry(tenant_ids=(value.tenant_id,), workers=1,
            bedrock_endpoints={value.tenant_id: endpoint(value, transport)})
        registry.validate_declared(value)
        with pytest.raises(EvolutionError) as cold:
            registry.require(value)
        assert cold.value.code == "evaluator_unavailable"
        await registry.start()
        try:
            registry.require(value)
            await registry._pools[value.tenant_id].close()
            with pytest.raises(EvolutionError) as stopped:
                registry.require(value)
            assert stopped.value.code == "evaluator_unavailable"
            assert not transport.calls
        finally:
            await registry.close()
    asyncio.run(exercise())


def test_output_only_managed_rule_does_not_reject_rules_json_input():
    async def exercise():
        value, transport = selected(), Transport()
        pii = Rule(rule_id="input-pii", kind="pii", stages=("input",), action="redact",
            evaluator_revision="pii-v2", config={"entities": ("email",)})
        value = PolicyCompiler().compile(value.model_copy(update={"status": "candidate", "rules": (
            value.rules[0].model_copy(update={"stages": ("output",)}), pii,
        )})).model_copy(update={"status": "approved"})
        registry = EvaluatorRegistry(tenant_ids=(value.tenant_id,), workers=1,
            bedrock_endpoints={value.tenant_id: endpoint(value, transport)})
        parts = (ContentPart(part_id="json", kind="json", role="user", value={"email": "alice@example.test"}),)
        await registry.start()
        try:
            registry.require(value, stage="input", parts=parts)
            findings = await registry.evaluate(pii, parts, time.monotonic() + 1,
                policy=value, stage="input", tenant_id=value.tenant_id)
            assert len(findings) == 1 and findings[0].json_pointer == "/email"
            with pytest.raises(EvolutionError) as output:
                registry.require(value, stage="output", parts=parts)
            assert output.value.code == "unsupported_surface"
            assert not transport.calls
        finally:
            await registry.close()
    asyncio.run(exercise())
