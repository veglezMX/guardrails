"""Operator client ceilings cannot be selected by a chat request."""

import asyncio
import time
from uuid import uuid4

import httpx
import pytest
from connector.evolution import (
    EvaluationClient,
    TextConnector,
    Turn,
    current_turn,
    validate_request,
)
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import canonical_json_bytes
from test_connector import decision_for, make_request


def client(tmp_path, handler, profile="text"):
    token = tmp_path / "token"
    token.write_text("synthetic-credential")
    token.chmod(0o600)
    return EvaluationClient(
        "https://runtime.local", str(token), "/etc/ssl/certs/ca-certificates.crt",
        transport=httpx.MockTransport(handler), evaluation_profile=profile,
    )


@pytest.mark.parametrize("field", ["evaluation_profile", "deadline_ms", "classifier_budget_ms"])
def test_chat_body_cannot_select_client_ceiling(field):
    payload = {"model": "public", "messages": [{"role": "user", "content": "hello"}]}
    with pytest.raises(EvolutionError) as error:
        validate_request(canonical_json_bytes(payload | {field: "classifier"}), "public")
    assert error.value.code == "unsupported_surface"


@pytest.mark.parametrize("profile", ["image", "ClassIfier", None, True, 3000])
def test_unknown_operator_profile_fails_before_reading_credentials(tmp_path, profile):
    with pytest.raises(ValueError, match="Unknown evaluation profile"):
        EvaluationClient("https://runtime.local", str(tmp_path / "missing"), "missing",
                         evaluation_profile=profile)


@pytest.mark.parametrize("profile,deadline", [("text", 500), ("classifier", 3000)])
def test_normalizer_uses_mounted_profile_for_both_turn_boundaries(profile, deadline):
    connector = TextConnector("team-a", "chat", str(uuid4()), evaluation_profile=profile)
    token = current_turn.set(Turn(context_id=str(uuid4())))
    try:
        for stage, role in (("input", "user"), ("output", "assistant")):
            request = connector.normalize({"messages": [{"role": role, "content": "hello"}]}, stage)
            assert request.deadline_ms == deadline
    finally:
        current_turn.reset(token)


@pytest.mark.asyncio
async def test_classifier_completion_past_text_ceiling_is_delivered_once(tmp_path):
    request, seen = make_request(), []

    async def handler(wire):
        seen.append(wire)
        await asyncio.sleep(1.6)
        return httpx.Response(200, json=decision_for(request).model_dump(mode="json"))

    transport = client(tmp_path, handler, "classifier")
    try:
        assert (await transport.evaluate(request)).action == "allow"
        assert len(seen) == 1
        assert seen[0].extensions["timeout"] == {
            "connect": .25, "read": 4.0, "write": 4.0, "pool": 4.0,
        }
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_request_deadline_cannot_enlarge_default_client_budget(tmp_path):
    request, seen = make_request().model_copy(update={"deadline_ms": 3000}), []
    cancelled = asyncio.Event()

    async def handler(wire):
        seen.append(wire)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    transport = client(tmp_path, handler)
    started = time.monotonic()
    try:
        with pytest.raises(EvolutionError) as error:
            await transport.evaluate(request)
        assert error.value.code == "evaluator_unavailable"
        assert cancelled.is_set() and len(seen) == 1
        assert 1.4 <= time.monotonic() - started < 2.5
        assert seen[0].extensions["timeout"]["read"] == 1.5
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_classifier_stalled_body_is_cancelled_at_its_ceiling_without_replay(tmp_path):
    request, seen = make_request(), []
    cancelled = asyncio.Event()

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"partial":'
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    def handler(wire):
        seen.append(wire)
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=Body())

    transport = client(tmp_path, handler, "classifier")
    started = time.monotonic()
    try:
        with pytest.raises(EvolutionError):
            await transport.evaluate(request)
        assert cancelled.is_set() and len(seen) == 1
        assert 3.8 <= time.monotonic() - started < 5.0
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_classifier_metadata_keeps_short_separate_budget(tmp_path):
    seen = []

    async def handler(wire):
        seen.append(wire)
        await asyncio.Event().wait()

    transport = client(tmp_path, handler, "classifier")
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            await transport.capabilities()
        assert 1.4 <= time.monotonic() - started < 2.5
        assert len(seen) == 1 and seen[0].extensions["timeout"]["read"] == 1.5
    finally:
        await transport.close()
