"""Narrow public ASGI boundary around the actual pinned LiteLLM proxy.

This owns the local proof's request subset and response/error envelope. Native
CustomGuardrail hooks still perform both runtime calls and mutate the real
LiteLLM messages/response. No upstream source or provider dispatcher is replaced.
"""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .gateway_state import GatewayTurn, current_turn


class BoundaryError(Exception):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code, self.status = code, status


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BoundaryError("invalid_request")
        result[key] = value
    return result


def validate_request(raw: bytes, model_alias: str) -> dict[str, Any]:
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(BoundaryError("invalid_request")))
    except (UnicodeError, ValueError, RecursionError):
        raise BoundaryError("invalid_request") from None
    if not isinstance(body, dict):
        raise BoundaryError("invalid_request", 422)
    if set(body) - {"model", "messages", "stream", "n", "temperature", "max_tokens"}:
        raise BoundaryError("unsupported_surface")
    if body.get("model") != model_alias:
        raise BoundaryError("unsupported_surface")
    if "stream" in body and type(body["stream"]) is not bool:
        raise BoundaryError("invalid_request", 422)
    if body.get("stream", False):
        raise BoundaryError("unsupported_surface")
    for name, default in (("n", 1), ("temperature", 0), ("max_tokens", 128)):
        value = body.get(name, default)
        if type(value) is not int:
            raise BoundaryError("invalid_request", 422)
        if (name == "max_tokens" and not 1 <= value <= 256) or (name != "max_tokens" and value != default):
            raise BoundaryError("unsupported_surface")
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise BoundaryError("invalid_request", 422)
    has_user = False
    size = 0
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise BoundaryError("unsupported_surface")
        if message["role"] not in ("system", "developer", "user", "assistant") or type(message["content"]) is not str:
            raise BoundaryError("unsupported_surface")
        has_user |= message["role"] == "user"
        try:
            size += len(message["content"].encode("utf-8"))
        except UnicodeError:
            raise BoundaryError("invalid_request", 422) from None
    if not has_user:
        raise BoundaryError("invalid_request", 422)
    if size > 1024 * 1024:
        raise BoundaryError("request_too_large", 413)
    return body


def response_metadata(turn: GatewayTurn) -> dict[str, Any]:
    before, after = turn.input_decision, turn.output_decision
    if before is None or after is None:
        raise BoundaryError("guardrail_hook_missing", 503)
    warnings = sorted({code for item in (before, after) if item["enforcement_state"] == "warn" for code in item["reason_codes"]})
    return {
        "correlation_id": turn.correlation_id,
        "input_decision_id": before["decision_id"], "output_decision_id": after["decision_id"],
        "input_source": before["source"], "output_source": after["source"],
        "policy_id": after["policy_id"], "policy_version": after["policy_version"],
        "binding_revision": after["binding_revision"], "snapshot_generation": after["snapshot_generation"],
        "enforcement_state": after["enforcement_state"],
        "input_action": before["action"], "output_action": after["action"],
        "warning_codes": warnings,
        "operational_codes": sorted(set(before["operational_codes"] + after["operational_codes"])),
        "audit_status": after["audit_status"],
    }


class ProofGatewayBoundary:
    def __init__(self, proxy: Any, model_alias: str, gateway_token: str) -> None:
        self.proxy, self.model_alias, self.gateway_token = proxy, model_alias, gateway_token

    @staticmethod
    def _registered() -> bool:
        import litellm
        from .native_guardrail import LocalProofGuardrail
        return any(isinstance(callback, LocalProofGuardrail) and callback.default_on for callback in litellm.callbacks)

    @staticmethod
    async def _json(send: Any, status: int, body: dict) -> None:
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
        await send({"type": "http.response.body", "body": data})

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.proxy(scope, receive, send)
            return
        turn = GatewayTurn(str(uuid4()))
        token = current_turn.set(turn)
        try:
            path = scope.get("path")
            if path == "/health/live" and scope["method"] == "GET":
                await self._json(send, 200, {"live": True})
                return
            if path == "/health/ready" and scope["method"] == "GET":
                ready = self._registered()
                await self._json(send, 200 if ready else 503, {"ready": ready})
                return
            if path != "/v1/chat/completions" or scope["method"] != "POST":
                raise BoundaryError("unsupported_surface")
            headers = {key.lower(): value for key, value in scope["headers"]}
            if not hmac.compare_digest(headers.get(b"authorization", b""), ("Bearer " + self.gateway_token).encode()):
                raise BoundaryError("authentication_failed", 401)
            if not self._registered():
                raise BoundaryError("guardrail_hook_missing", 503)
            body = bytearray()
            while True:
                chunk = await receive()
                if chunk["type"] == "http.disconnect":
                    return
                body.extend(chunk.get("body", b""))
                if len(body) > 8 * 1024 * 1024:
                    raise BoundaryError("request_too_large", 413)
                if not chunk.get("more_body", False):
                    break
            validate_request(bytes(body), self.model_alias)
            consumed = False
            async def replay() -> dict:
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()
            status = 502
            response_body = bytearray()
            overflow = False
            async def capture(message: dict) -> None:
                nonlocal status, overflow
                if message["type"] == "http.response.start":
                    status = message["status"]
                elif message["type"] == "http.response.body":
                    part = message.get("body", b"")
                    if len(response_body) + len(part) > 8 * 1024 * 1024:
                        overflow = True
                    elif not overflow:
                        response_body.extend(part)
            try:
                await self.proxy(scope, replay, capture)
            except Exception:
                # Upstream detail can contain prompt/provider text. The public
                # boundary always emits fixed safe failure fields instead.
                status = 502
            if turn.error is not None:
                await self._json(send, turn.error_status, {"error": turn.error})
                return
            if status != 200 or overflow:
                raise BoundaryError("upstream_failed", 504 if status == 504 else 502)
            try:
                response = json.loads(response_body)
                if not isinstance(response, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise BoundaryError("upstream_failed", 502) from None
            response["guardrails"] = response_metadata(turn)
            await self._json(send, 200, response)
        except BoundaryError as error:
            await self._json(send, error.status, {"error": {"code": error.code, "message": "Request cannot be processed", "correlation_id": turn.correlation_id}})
        finally:
            current_turn.reset(token)


def create_app() -> ProofGatewayBoundary:
    gateway_token = Path(os.environ["PROOF_GATEWAY_TOKEN_FILE"]).read_text().strip()
    if not gateway_token or any(character.isspace() for character in gateway_token):
        raise ValueError("invalid gateway credential file")
    os.environ["LITELLM_MASTER_KEY"] = gateway_token
    os.environ["PROOF_MOCK_API_KEY"] = Path(os.environ["PROOF_MOCK_TOKEN_FILE"]).read_text().strip()
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from litellm.proxy.proxy_server import app as proxy
    return ProofGatewayBoundary(proxy, os.environ.get("PROOF_GATEWAY_MODEL", "proof-a"), gateway_token)


def main() -> None:
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PROOF_PORT", "4000")),
                ssl_certfile=os.environ["PROOF_TLS_CERT"], ssl_keyfile=os.environ["PROOF_TLS_KEY"],
                access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
