"""Final gateway socket admission independent of native SDK callback order."""

import asyncio
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from connector.evolution import MAX_WIRE_BYTES, Turn, current_turn
from connector.evolution_gateway import ModelTransport
from connector.evolution_routes import ApprovedModelRoute, LocalService, RouteManifest
from guardrails.evolution.egress import PinnedHttpsTransport, PinnedResponse
from guardrails.evolution.models import (
    ContentPart,
    Decision,
    Destination,
    PolicyRef,
    ProviderProfile,
    canonical_json_bytes,
    with_resource_digest,
)


class CountingPinnedTransport:
    """No network: precisely count requests admitted through the real bridge."""

    validate_url = staticmethod(PinnedHttpsTransport.validate_url)

    def __init__(self):
        self.calls = []

    async def request(self, route, **options):
        self.calls.append((route, options))
        return PinnedResponse(
            200, (("content-type", "application/json"),), b'{"ok":true}'
        )


@pytest.fixture
def boundary(tmp_path):
    destination = with_resource_digest(
        Destination(
            tenant_id="team-a",
            destination_id=str(uuid4()),
            name="model",
            host="model.local",
            port=8443,
            path_prefix="/api",
            cidrs=("10.2.3.4/32",),
            zone="local-shared",
            purpose="inference",
            tls_ca_ref="model-ca",
        )
    )
    provider = with_resource_digest(
        ProviderProfile(
            tenant_id="team-a",
            provider_id=str(uuid4()),
            kind="model",
            vendor="ollama",
            model_id="approved-model",
            destination_id=destination.destination_id,
            zone="local-shared",
        )
    )
    ref = PolicyRef(
        tenant_id="team-a", policy_id="approved", version=1, digest="a" * 64
    )
    route = ApprovedModelRoute(policy=ref, provider=provider, destination=destination)
    manifest = RouteManifest(
        tenant_id="team-a",
        workload_id="chat",
        routes=(route,),
        ca_files={"model-ca": "/unused/trusted-ca.pem"},
        local_services={
            destination.destination_id: LocalService(
                hostname="model.local", addresses=("10.2.3.4",)
            )
        },
    )
    path = tmp_path / "approved-routes.json"
    path.write_text(manifest.model_dump_json())
    path.chmod(0o600)
    settings = SimpleNamespace(
        tenant_id="team-a",
        workload_id="chat",
        provider_id=provider.provider_id,
        model_alias="public-alias",
        model_route="ollama_chat/approved-model",
        model_base_url="https://model.local:8443",
        approved_routes_file=str(path),
        profile="local_model",
        model_timeout=0.15,
    )
    decision = Decision(
        decision_id=str(uuid4()),
        request_id=str(uuid4()),
        context_id=str(uuid4()),
        tenant_id="team-a",
        workload_id="chat",
        stage="input",
        policy=ref,
        binding_revision=1,
        snapshot_generation=1,
        action="allow",
        would_be_action="allow",
        evaluation_ms=1.0,
        audit_status="persisted",
    )
    part = ContentPart(
        part_id="m0", kind="text", role="user", text="inspected synthetic text"
    )
    turn = Turn(input_decision=decision, input_parts=(part,), route_verified=True)
    transport = ModelTransport(settings)
    transport.transport = CountingPinnedTransport()
    payload = {
        "model": "approved-model",
        "messages": [{"role": "user", "content": part.text}],
        "stream": False,
    }
    return transport, turn, payload


def request(payload=None, *, stream=None):
    return httpx.Request(
        "POST",
        "https://model.local:8443/api/chat",
        content=canonical_json_bytes(payload) if stream is None else None,
        stream=stream,
    )


def test_exact_inspected_request_bridge_preserves_response_and_consumes_turn(boundary):
    transport, turn, payload = boundary

    async def run():
        token = current_turn.set(turn)
        try:
            response = await transport.handle_async_request(request(payload))
            assert response.status_code == 200
            assert response.content == b'{"ok":true}'
            assert response.headers["content-type"] == "application/json"
            with pytest.raises(httpx.ConnectError):
                await transport.handle_async_request(request(payload))
        finally:
            current_turn.reset(token)

    asyncio.run(run())
    assert turn.model_dispatched is True
    assert len(transport.transport.calls) == 1
    assert transport.transport.calls[0][1]["body"] == canonical_json_bytes(payload)


def test_concurrent_stream_reads_cannot_reuse_the_same_input_decision(boundary):
    transport, turn, payload = boundary

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        class PausedBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await release.wait()
                yield canonical_json_bytes(payload)

        token = current_turn.set(turn)
        try:
            first = asyncio.create_task(
                transport.handle_async_request(request(stream=PausedBody()))
            )
            await entered.wait()
            second = asyncio.create_task(
                transport.handle_async_request(request(payload))
            )
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
            assert sum(isinstance(value, httpx.Response) for value in results) == 1
            assert sum(isinstance(value, httpx.ConnectError) for value in results) == 1
        finally:
            current_turn.reset(token)

    asyncio.run(run())
    assert len(transport.transport.calls) == 1


@pytest.mark.parametrize(
    "change", ["model", "text", "role", "additional", "reordered", "duplicate_json_key"]
)
def test_wire_model_and_all_messages_must_match_inspected_parts(boundary, change):
    transport, turn, payload = boundary
    if change == "model":
        payload["model"] = "different-unapproved-model"
    elif change == "text":
        payload["messages"][0]["content"] = "uninspected injected content"
    elif change == "role":
        payload["messages"][0]["role"] = "system"
    elif change == "additional":
        payload["messages"].append(
            {"role": "user", "content": "extra uninspected content"}
        )
    elif change == "reordered":
        second = ContentPart(
            part_id="m1", kind="text", role="assistant", text="second approved message"
        )
        turn.input_parts += (second,)
        payload["messages"].insert(0, {"role": second.role, "content": second.text})
    wire = request(payload)
    if change == "duplicate_json_key":
        raw = canonical_json_bytes(payload)
        wire = httpx.Request(
            "POST",
            "https://model.local:8443/api/chat",
            content=b'{"model":"unapproved",' + raw[1:],
        )

    async def run():
        token = current_turn.set(turn)
        try:
            with pytest.raises(httpx.ConnectError):
                await transport.handle_async_request(wire)
        finally:
            current_turn.reset(token)

    asyncio.run(run())
    assert len(transport.transport.calls) == 0
    assert turn.error is not None


def test_redaction_requires_exact_transformed_messages_without_original_leak(boundary):
    transport, turn, payload = boundary
    transformed = turn.input_parts[0].model_copy(update={"text": "[REDACTED]"})
    turn.input_decision = turn.input_decision.model_copy(
        update={
            "action": "redact",
            "would_be_action": "redact",
            "transformed_parts": (transformed,),
        }
    )

    async def run():
        token = current_turn.set(turn)
        try:
            with pytest.raises(httpx.ConnectError):
                await transport.handle_async_request(request(payload))
            assert len(transport.transport.calls) == 0
            # Independent new turn demonstrates the authorized sanitized body.
            clean = Turn(
                input_decision=turn.input_decision,
                input_parts=turn.input_parts,
                route_verified=True,
            )
            current_turn.set(clean)
            payload["messages"][0]["content"] = transformed.text
            assert (
                await transport.handle_async_request(request(payload))
            ).status_code == 200
        finally:
            current_turn.reset(token)

    asyncio.run(run())
    assert len(transport.transport.calls) == 1
    assert b"inspected synthetic text" not in transport.transport.calls[0][1]["body"]


@pytest.mark.parametrize("failure", ["oversize", "slow"])
def test_outbound_stream_body_has_cap_and_absolute_budget_before_dispatch(
    boundary, failure
):
    transport, turn, payload = boundary
    yielded = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            if failure == "slow":
                await asyncio.sleep(0.5)
                yield canonical_json_bytes(payload)
            else:
                for index in range(100):
                    yielded.append(index)
                    yield b"x" * (MAX_WIRE_BYTES // 4)

    async def run():
        token = current_turn.set(turn)
        try:
            with pytest.raises(
                httpx.ReadTimeout if failure == "slow" else httpx.ConnectError
            ):
                await transport.handle_async_request(request(stream=Body()))
        finally:
            current_turn.reset(token)

    started = time.monotonic()
    asyncio.run(run())
    assert len(transport.transport.calls) == 0
    assert turn.error is not None
    if failure == "slow":
        assert time.monotonic() - started < 0.4
        assert turn.error.code == "upstream_timeout"
    else:
        assert len(yielded) <= 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("tools", [{"type": "function", "function": {"name": "uninspected"}}]),
        ("functions", [{"name": "uninspected"}]),
        ("prompt", "uninspected content"),
        ("system", "uninspected system instruction"),
        ("images", ["uninspected image"]),
        ("context", [1, 2, 3]),
        ("format", {"description": "uninspected generation instruction"}),
        ("options", "uninspected options"),
        ("options", {"system": "uninspected instruction"}),
        ("options", {"temperature": "uninspected instruction"}),
        ("options", {"num_predict": -1}),
        ("options", {"num_predict": 1000000}),
        ("stream", True),
    ],
)
def test_uninspected_wire_fields_and_unbounded_generation_options_reject(
    boundary, field, value
):
    transport, turn, payload = boundary
    payload[field] = value

    async def run():
        token = current_turn.set(turn)
        try:
            with pytest.raises(httpx.ConnectError):
                await transport.handle_async_request(request(payload))
        finally:
            current_turn.reset(token)

    asyncio.run(run())
    assert transport.transport.calls == []
    assert turn.error is not None


def test_native_ollama_empty_images_preserves_exact_inspected_wire(boundary):
    transport, turn, payload = boundary
    payload["messages"][0]["images"] = []
    wire = canonical_json_bytes(payload)

    async def run():
        token = current_turn.set(turn)
        try:
            result = await transport.handle_async_request(request(payload))
            assert result.status_code == 200
        finally:
            current_turn.reset(token)

    asyncio.run(run())
    assert turn.model_dispatched is True
    assert transport.transport.calls[0][1]["body"] == wire
    assert len(transport.transport.calls) == 1
    # Empty native metadata does not broaden the public caller surface.
    from connector.evolution import validate_request
    from guardrails.evolution.errors import EvolutionError
    with pytest.raises(EvolutionError):
        validate_request(canonical_json_bytes(payload | {"model": "public-alias"}), "public-alias")


@pytest.mark.parametrize("change", [
    "nonempty", "null", "false", "mapping", "text", "tool-calls", "thinking", "changed-text", "openai-empty",
])
def test_native_ollama_metadata_never_hides_uninspected_surface(boundary, change):
    transport, turn, payload = boundary
    values = {"nonempty": ["synthetic-uninspected"], "null": None, "false": False, "mapping": {}, "text": ""}
    payload["messages"][0]["images"] = values.get(change, [])
    if change == "tool-calls":
        payload["messages"][0]["tool_calls"] = []
    elif change == "thinking":
        payload["messages"][0]["thinking"] = ""
    elif change == "changed-text":
        payload["messages"][0]["content"] = "uninspected replacement"
    elif change == "openai-empty":
        transport.settings.profile = "deterministic_mock"
        transport.settings.model_base_url = "https://model.local:8443/api"
    wire = request(payload)
    if change == "openai-empty":
        wire = httpx.Request("POST", "https://model.local:8443/api/chat/completions", content=canonical_json_bytes(payload))

    async def run():
        token = current_turn.set(turn)
        try:
            with pytest.raises(httpx.ConnectError):
                await transport.handle_async_request(wire)
        finally:
            current_turn.reset(token)

    asyncio.run(run())
    assert len(transport.transport.calls) == 0
    assert turn.error is not None
