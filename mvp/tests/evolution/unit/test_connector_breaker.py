"""Transport failure counting includes response streams, never replays content."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from connector.evolution import MAX_WIRE_BYTES, EvaluationClient
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import (
    ContentPart,
    Decision,
    EvaluationRequest,
    PolicyRef,
)


class FailedBody(httpx.AsyncByteStream):
    def __init__(self, error=httpx.ReadTimeout):
        self.error = error

    async def __aiter__(self):
        yield b'{"partial":'
        raise self.error("untrusted-secret-echo")


class UnreadErrorBody(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise AssertionError("HTTP denial bodies must never be read")
        yield b""  # pragma: no cover


def failed_response(error=httpx.ReadTimeout):
    return httpx.Response(
        200, headers={"content-type": "application/json"}, stream=FailedBody(error)
    )


@pytest.fixture
def harness(tmp_path):
    token = tmp_path / "workload-token"
    token.write_text("private-test-credential")
    token.chmod(0o600)
    request = EvaluationRequest(
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
        tenant_id="team-a",
        workload_id="chat",
        stage="input",
        parts=(
            ContentPart(
                part_id="m0", kind="text", role="user", text="synthetic content"
            ),
        ),
        provider_id=str(uuid4()),
        deadline_ms=500,
    )
    decision = Decision(
        decision_id=str(uuid4()),
        request_id=request.request_id,
        tenant_id=request.tenant_id,
        workload_id=request.workload_id,
        context_id=str(uuid4()),
        stage="input",
        policy=PolicyRef(
            tenant_id="team-a", policy_id="test", version=1, digest="a" * 64
        ),
        binding_revision=1,
        snapshot_generation=1,
        action="allow",
        would_be_action="allow",
        evaluation_ms=1.0,
        audit_status="persisted",
    )
    now = [0.0]
    seen = []
    response = [failed_response]

    def handler(wire):
        seen.append((wire.method, wire.url.path, wire.content))
        return response[0]()

    client = EvaluationClient(
        "https://runtime.test",
        str(token),
        "/etc/ssl/certs/ca-certificates.crt",
        clock=lambda: now[0],
        transport=httpx.MockTransport(handler),
    )
    return client, request, decision, now, seen, response


async def fail(client, request):
    with pytest.raises(EvolutionError) as failure:
        await client.evaluate(request)
    assert "untrusted" not in str(failure.value)
    return failure.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError]
)
async def test_five_after_header_stream_failures_open_without_replaying_body(
    harness, error
):
    client, request, _, now, seen, response = harness
    response[0] = lambda: failed_response(error)
    try:
        for index in range(5):
            now[0] = float(index)
            assert (await fail(client, request)).code == "evaluator_unavailable"
            assert len(seen) == index + 1
            assert len(client.failures) == index + 1
        assert client.opened_at == 4.0
        assert (await fail(client, request)).code == "evaluator_unavailable"
        assert len(seen) == 5
        assert all(
            method == "POST" and path == "/v2/evaluations" for method, path, _ in seen
        )
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denial",
    [
        "auth",
        "forbidden",
        "structural_status",
        "quota",
        "wrong_type",
        "compression",
        "oversize",
        "invalid_json",
        "content_block",
    ],
)
async def test_intentional_denials_break_consecutive_transport_failure_sequence(
    harness, denial
):
    client, request, decision, _, seen, response = harness
    try:
        for _ in range(4):
            await fail(client, request)
        assert len(client.failures) == 4
        if denial in {"auth", "forbidden", "structural_status", "quota"}:
            status = {
                "auth": 401,
                "forbidden": 403,
                "structural_status": 422,
                "quota": 429,
            }[denial]
            response[0] = lambda: httpx.Response(status, stream=UnreadErrorBody())
        elif denial == "wrong_type":
            response[0] = lambda: httpx.Response(
                200,
                content=b"untrusted-secret-echo",
                headers={"content-type": "text/html"},
            )
        elif denial == "compression":
            response[0] = lambda: httpx.Response(
                200,
                stream=UnreadErrorBody(),
                headers={
                    "content-type": "application/json",
                    "content-encoding": "gzip",
                },
            )
        elif denial == "oversize":
            response[0] = lambda: httpx.Response(
                200,
                content=b"x" * (MAX_WIRE_BYTES + 1),
                headers={"content-type": "application/json"},
            )
        elif denial == "invalid_json":
            response[0] = lambda: httpx.Response(
                200, content=b"{malformed", headers={"content-type": "application/json"}
            )
        else:
            blocked = decision.model_copy(
                update={"action": "block", "would_be_action": "block"}
            )
            response[0] = lambda: httpx.Response(
                200, json=blocked.model_dump(mode="json")
            )
        if denial == "content_block":
            assert (await client.evaluate(request)).action == "block"
        else:
            await fail(client, request)
        assert client.opened_at is None
        assert len(client.failures) == 0
        response[0] = failed_response
        await fail(client, request)
        assert client.opened_at is None
        assert len(client.failures) == 1
        assert len(seen) == 6
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_successful_complete_body_resets_failure_sequence(harness):
    client, request, decision, _, seen, response = harness
    try:
        for _ in range(4):
            await fail(client, request)
        response[0] = lambda: httpx.Response(200, json=decision.model_dump(mode="json"))
        assert (await client.evaluate(request)).action == "allow"
        assert len(client.failures) == 0 and client.opened_at is None
        response[0] = failed_response
        for _ in range(4):
            await fail(client, request)
        assert len(client.failures) == 4 and client.opened_at is None
        assert len(seen) == 9
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", ["stream_failure", "auth", "no_enabled_capability"])
async def test_failed_half_open_metadata_probe_restarts_cooldown_without_content(
    harness, probe
):
    client, request, _, now, seen, response = harness
    try:
        for _ in range(5):
            await fail(client, request)
        assert client.opened_at == 0
        now[0] = 11
        if probe == "auth":
            response[0] = lambda: httpx.Response(401, stream=UnreadErrorBody())
        elif probe == "no_enabled_capability":
            response[0] = lambda: httpx.Response(200, json=[])
        await fail(client, request)
        assert seen[-1] == ("GET", "/v2/capabilities", b"")
        assert len(seen) == 6
        assert client.opened_at == 11
        assert client.probing is False
        now[0] = 12
        await fail(client, request)
        assert len(seen) == 6
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_only_one_half_open_probe_is_admitted_for_concurrent_callers(harness):
    client, request, _, now, seen, response = harness
    entered, release = asyncio.Event(), asyncio.Event()

    class ProbeBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await release.wait()
            raise httpx.ReadTimeout("untrusted-probe-error")
            yield b""  # pragma: no cover

    try:
        for _ in range(5):
            await fail(client, request)
        now[0] = 11
        response[0] = lambda: httpx.Response(
            200, headers={"content-type": "application/json"}, stream=ProbeBody()
        )
        first = asyncio.create_task(fail(client, request))
        await entered.wait()
        assert client.probing is True
        await fail(client, request)
        assert len(seen) == 6
        release.set()
        await first
        assert seen[-1] == ("GET", "/v2/capabilities", b"")
        assert client.opened_at == 11 and client.probing is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_failures_older_than_thirty_seconds_do_not_open_breaker(harness):
    client, request, _, now, seen, _ = harness
    try:
        for _ in range(4):
            await fail(client, request)
        now[0] = 31
        await fail(client, request)
        assert tuple(client.failures) == (31,)
        assert client.opened_at is None
        assert len(seen) == 5
    finally:
        await client.close()
