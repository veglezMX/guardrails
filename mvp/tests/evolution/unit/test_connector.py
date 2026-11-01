"""Public connector contract and transport policy; real TLS/gateway tests are separate."""
import json
from uuid import uuid4

import httpx
import pytest
from connector.evolution import (
    EvaluationClient,
    TextConnector,
    Turn,
    current_turn,
    https_origin,
    normalize_messages,
    read_secret,
    validate_request,
)
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import Capability, Decision, Finding, PolicyRef
from guardrails.evolution.transformations import transform_text


def make_request(stage="input", before=None):
    turn = Turn(context_id=before.context_id if before else None)
    token = current_turn.set(turn)
    try:
        return TextConnector("team-a", "chat", str(uuid4())).normalize(
            {"messages": [{"role": "assistant" if stage == "output" else "user", "content": "Hi a@example.com"}]}, stage)
    finally:
        current_turn.reset(token)


def decision_for(request, **changes):
    fields = dict(  # noqa: C408 - fixture field names are easier to compare with the model
        decision_id=str(uuid4()), request_id=request.request_id,
        tenant_id=request.tenant_id, workload_id=request.workload_id, stage=request.stage,
        context_id=request.context_id or str(uuid4()),
        policy=PolicyRef(tenant_id=request.tenant_id, policy_id="chat-policy", version=1, digest="a" * 64),
        binding_revision=1, snapshot_generation=2,
        action="allow", would_be_action="allow", evaluation_ms=1.0,
        audit_status="persisted",
    )
    return Decision(**(fields | changes))


def test_normalization_preserves_every_supported_role_and_rejects_unsupported_shapes():
    messages = [{"role": role, "content": "á"} for role in ("system", "user", "assistant")]
    parts = normalize_messages(messages, "input")
    assert [p.part_id for p in parts] == ["m0", "m1", "m2"]
    assert [p.role for p in parts] == [m["role"] for m in messages]
    assert all(p.text == "á" for p in parts)
    for bad in ([], "text", [{"role": "developer", "content": "x"}],
                [{"role": "user", "content": [{"type": "text", "text": "x"}]}],
                [{"role": "user", "content": "x", "tool_calls": []}],
                [{"role": "tool", "content": "x"}]):
        with pytest.raises(EvolutionError, match="Unsupported"):
            normalize_messages(bad, "input")
    with pytest.raises(EvolutionError, match="Payload"):
        normalize_messages([{"role": "user", "content": "é" * 32769}], "input")
    with pytest.raises(EvolutionError):
        normalize_messages(messages, "output")


def test_raw_gateway_request_rejects_skip_identity_route_and_json_bypasses():
    base = {"model": "team-a", "messages": [{"role": "user", "content": "hello"}]}
    assert validate_request(json.dumps(base).encode(), "team-a") == base
    for field, value in (("guardrails", []), ("skip_guardrails", True), ("tenant_id", "team-b"),
                         ("api_base", "https://sink.invalid"), ("metadata", {}),
                         ("tools", []), ("stream", True), ("stream", 0), ("n", 2),
                         ("temperature", True), ("max_tokens", 257), ("model", "other")):
        with pytest.raises(EvolutionError):
            validate_request(json.dumps(base | {field: value}).encode(), "team-a")
    for raw in (b'{"model":"team-a","model":"team-a"}', b'{"x":NaN}', b'"x"', b'\xff'):
        with pytest.raises(EvolutionError):
            validate_request(raw, "team-a")


def test_decision_identity_pinning_and_complete_redaction_are_required():
    request = make_request()
    allowed = decision_for(request)
    TextConnector.verify(allowed, request)
    for update in ({"request_id": str(uuid4())}, {"workload_id": "other"}, {"context_id": None},
                   {"binding_revision": None}, {"snapshot_generation": None}, {"audit_status": "failed"}):
        with pytest.raises(EvolutionError):
            TextConnector.verify(allowed.model_copy(update=update), request)
    finding = Finding(rule_id="email", category="pii", part_id="m0", reason_code="pii_detected",
                      start=3, end=16, evaluator_revision="pii-v2")
    transformed = transform_text(request.parts, (finding,))
    redacted = decision_for(request, action="redact", would_be_action="redact", findings=(finding,),
                            transformed_parts=transformed, evaluator_revisions=("pii-v2",))
    TextConnector.verify(redacted, request)
    original = {"messages": [{"role": "user", "content": request.parts[0].text}]}
    result = TextConnector("team-a", "chat", request.provider_id).enforce(redacted, original)
    assert result["messages"][0]["content"] == "Hi [REDACTED]"
    assert original["messages"][0]["content"] == "Hi a@example.com"
    wrong = redacted.model_copy(update={"transformed_parts": (transformed[0].model_copy(update={"text": "injected"}),)})
    with pytest.raises(EvolutionError, match="Unsafe"):
        TextConnector.verify(wrong, request)
    with pytest.raises(EvolutionError, match="Unsafe"):
        TextConnector("team-a", "chat", request.provider_id).enforce(wrong, original)
    output = make_request("output", allowed)
    completed = decision_for(output, snapshot_generation=3)
    TextConnector.verify(completed, output, allowed)
    for change in ({"context_id": str(uuid4())}, {"snapshot_generation": 1}, {"binding_revision": 2},
                   {"policy": allowed.policy.model_copy(update={"version": 2})}):
        with pytest.raises(EvolutionError, match="Context"):
            TextConnector.verify(completed.model_copy(update=change), output, allowed)


def test_connector_scoped_context_and_operational_blocks_never_allow():
    connector = TextConnector("team-a", "chat", str(uuid4()))
    with pytest.raises(EvolutionError, match="Context"):
        connector.normalize({"messages": [{"role": "user", "content": "x"}]}, "input")
    request = make_request()
    for reasons, status in ((("pii_detected",), 403), (("evaluation_timeout",), 503), (("audit_failed",), 503)):
        denied = decision_for(request, action="block", would_be_action="block", reason_codes=reasons)
        with pytest.raises(EvolutionError) as caught:
            connector.enforce(denied, {"messages": [{"role": "user", "content": "x"}]})
        assert caught.value.status == status


def test_https_and_secret_files_reject_unsafe_configuration(tmp_path):
    assert https_origin("https://runtime.local:8443") == "https://runtime.local:8443"
    for value in ("http://runtime.local", "https://user:secret@runtime.local", "https://runtime.local?q=1",
                  "https://Runtime.local", "https://runtime.local/%2f", "https://runtime.local/../other",
                  "https://runtime.local:99999", "https://runtime.local#x"):
        with pytest.raises(ValueError):
            https_origin(value)
    secret = tmp_path / "token"
    secret.write_text("private-credential")
    secret.chmod(0o600)
    assert read_secret(str(secret)) == "private-credential"
    alias = tmp_path / "alias"
    alias.symlink_to(secret)
    with pytest.raises(OSError):
        read_secret(str(alias))
    secret.chmod(0o644)
    with pytest.raises(ValueError):
        read_secret(str(secret))


def client(tmp_path, handler, **kwargs):
    token = tmp_path / (uuid4().hex + ".token")
    token.write_text("private-credential")
    token.chmod(0o600)
    return EvaluationClient("https://runtime.local", str(token), "/etc/ssl/certs/ca-certificates.crt",
                            transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.asyncio
async def test_transport_retries_only_predelivery_connect_once_with_same_request_id(tmp_path):
    request = make_request()
    deliveries = []
    def handler(wire):
        deliveries.append((wire.headers["x-request-id"], wire.content))
        if len(deliveries) == 1:
            raise httpx.ConnectError("private-untrusted-message")
        return httpx.Response(200, json=decision_for(request).model_dump(mode="json"))
    transport = client(tmp_path, handler)
    try:
        assert (await transport.evaluate(request)).action == "allow"
        assert len(deliveries) == 2 and deliveries[0] == deliveries[1]
    finally:
        await transport.close()
    for failure in (httpx.ReadTimeout, httpx.WriteError, httpx.ReadError):
        seen = []
        def failed(wire, seen=seen, failure=failure):
            seen.append(wire)
            raise failure("private-untrusted-message")
        transport = client(tmp_path, failed)
        try:
            with pytest.raises(EvolutionError) as caught:
                await transport.evaluate(request)
            assert len(seen) == 1
            assert "private" not in str(caught.value)
        finally:
            await transport.close()


@pytest.mark.asyncio
async def test_breaker_uses_metadata_only_probe_and_excludes_structural_denials(tmp_path):
    now = [0.0]
    request = make_request()
    seen = []
    failure = [True]
    capability = Capability(capability_id="pii", stage="input", part_kinds=("text",),
                            actions=("allow", "block"), evaluator_revision="pii-v2",
                            max_input_bytes=65536, timeout_ms=500, zone="local-shared", status="enabled")
    def handler(wire):
        seen.append((wire.method, wire.url.path, wire.content))
        if failure[0]:
            raise httpx.ConnectError("offline")
        if wire.method == "GET":
            return httpx.Response(200, json=[capability.model_dump(mode="json")])
        return httpx.Response(200, json=decision_for(request).model_dump(mode="json"))
    transport = client(tmp_path, handler, clock=lambda: now[0])
    try:
        for _ in range(3):
            with pytest.raises(EvolutionError):
                await transport.evaluate(request)
        assert len(seen) == 5
        with pytest.raises(EvolutionError):
            await transport.evaluate(request)
        assert len(seen) == 5
        now[0] = 11
        failure[0] = False
        assert (await transport.evaluate(request)).action == "allow"
        assert seen[5] == ("GET", "/v2/capabilities", b"")
        assert seen[6][1] == "/v2/evaluations"
        assert transport.opened_at is None
    finally:
        await transport.close()
    denied = client(tmp_path, lambda wire: httpx.Response(403, content=b"private-credential-echo"))
    try:
        for _ in range(6):
            with pytest.raises(EvolutionError):
                await denied.evaluate(request)
        assert denied.opened_at is None and not denied.failures
    finally:
        await denied.close()


@pytest.mark.asyncio
async def test_wire_client_rejects_malformed_compressed_oversized_and_redirected_success(tmp_path):
    request = make_request()
    for response in (httpx.Response(200, content=b'{}', headers={"content-type": "application/json"}),
                     httpx.Response(200, content=b'{}', headers={"content-type": "text/html"}),
                     httpx.Response(200, content=b'x' * (1024 * 1024 + 1), headers={"content-type": "application/json"}),
                     httpx.Response(302, headers={"location": "https://denied.invalid"})):
        calls = []
        def handler(wire, calls=calls, response=response):
            calls.append(wire)
            return response
        transport = client(tmp_path, handler)
        try:
            with pytest.raises(EvolutionError):
                await transport.evaluate(request)
            assert len(calls) == 1
        finally:
            await transport.close()


def test_raw_provider_metadata_is_rejected_and_proxy_metadata_never_returned():
    from connector.evolution_gateway import validate_output
    raw = {"id": "untrusted-metadata", "usage": {"prompt_tokens": "private", "completion_tokens": 1, "total_tokens": 2},
           "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "inspected",
                         "provider_specific_fields": {"private": "uninspected-metadata"}}}]}
    with pytest.raises(EvolutionError):
        validate_output(raw)
    safe = validate_output(raw, proxy_metadata=True)
    assert safe == {"choices": [{"index": 0, "message": {"role": "assistant", "content": "inspected"}, "finish_reason": "stop"}]}
