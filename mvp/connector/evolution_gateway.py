"""Bounded v2 HTTP boundary around the real pinned LiteLLM application."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
import time
from typing import Any
from uuid import UUID

import httpx
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import canonical_json_bytes, strict_json_loads

from .evolution import (
    MAX_WIRE_BYTES,
    GatewaySettings,
    Turn,
    current_turn,
    normalize_messages,
    read_secret,
    validate_request,
)


def validate_output(value: Any, *, proxy_metadata: bool = False) -> dict:
    """Reject unsupported raw provider fields before SDK parsing can discard them."""
    try:
        if not isinstance(value, dict):
            raise TypeError()
        choices = value["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError()
        choice = choices[0]
        if not isinstance(choice, dict) or choice.get("finish_reason") not in {"stop", "length"}:
            raise ValueError()
        message = choice["message"]
        if not isinstance(message, dict) or message.get("role") != "assistant" or type(message.get("content")) is not str:
            raise ValueError()
        optional_null = {"tool_calls", "function_call", "refusal", "audio", "annotations", "reasoning_content"}
        sdk_fields = {"provider_specific_fields"} if proxy_metadata else set()
        if set(message) - {"role", "content"} - optional_null - sdk_fields:
            raise ValueError()
        if any(message.get(key) is not None for key in optional_null):
            raise ValueError()
        if len(message["content"].encode("utf-8")) > 65536:
            raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise EvolutionError("unsupported_surface") from None
    # Provider metadata is uninspected input too. Preserve only bounded counters;
    # the boundary supplies trusted response identity and the configured alias.
    result = {}
    usage = value.get("usage")
    counters = ("prompt_tokens", "completion_tokens", "total_tokens")
    if isinstance(usage, dict) and all(type(usage.get(key)) is int and 0 <= usage[key] <= 1_000_000_000 for key in counters):
        result["usage"] = {key: usage[key] for key in counters}
    result["choices"] = [{"index": 0, "message": {"role": "assistant", "content": message["content"]},
                           "finish_reason": choice["finish_reason"]}]
    return result


class ModelTransport(httpx.AsyncBaseTransport):
    """Final request boundary, after native route checks and before socket writes."""

    def __init__(self, settings: GatewaySettings):
        from guardrails.evolution.egress import PinnedHttpsTransport

        from .evolution_routes import RouteManifest
        self.settings = settings
        self.manifest = RouteManifest.from_file(settings.approved_routes_file)
        self.transport = PinnedHttpsTransport(
            ca_files=self.manifest.ca_files,
            local_services={key: (row.hostname, row.addresses) for key, row in self.manifest.local_services.items()},
        )

    async def handle_async_request(self, request):
        turn = current_turn.get()
        base = self.settings.model_base_url
        expected = base + ("/chat/completions" if self.settings.profile == "deterministic_mock" else "/api/chat")
        if (turn is None or turn.input_decision is None or not turn.route_verified
                or turn.model_dispatched or request.method != "POST" or str(request.url) != expected):
            if turn is not None:
                turn.error = EvolutionError("destination_denied")
                turn.error_status = 403
            raise httpx.ConnectError("Model route is not authorized", request=request)
        try:
            from guardrails.evolution.egress import AuthorizedRoute
            approved = self.manifest.select(turn.input_decision, self.settings)
            route = AuthorizedRoute(approved.provider, approved.destination, approved.external_processing)
            path = self.transport.validate_url(route, str(request.url))
            # Reserve this attempt before the first await: concurrent native
            # callbacks sharing a turn cannot both dispatch.
            turn.model_dispatched = True
            deadline = time.monotonic() + self.settings.model_timeout
            body = bytearray()
            async with asyncio.timeout_at(deadline):
                async for chunk in request.stream:
                    if len(body) + len(chunk) > MAX_WIRE_BYTES:
                        raise EvolutionError("payload_too_large")
                    body.extend(chunk)
            try:
                payload = strict_json_loads(bytes(body))
                if (not isinstance(payload, dict) or payload.get("model") != approved.provider.model_id
                        or payload.get("stream", False) is not False):
                    raise ValueError()
                if self.settings.profile == "deterministic_mock":
                    if set(payload) - {"model", "messages", "stream", "temperature", "n", "max_tokens", "max_completion_tokens"}:
                        raise ValueError()
                    if type(payload.get("n", 1)) is not int or payload.get("n", 1) != 1:
                        raise ValueError()
                    options = {"temperature": payload.get("temperature", 0),
                               "num_predict": payload.get("max_tokens", payload.get("max_completion_tokens", 128))}
                    if "max_tokens" in payload and "max_completion_tokens" in payload:
                        raise ValueError()
                else:
                    if set(payload) - {"model", "messages", "stream", "options"}:
                        raise ValueError()
                    options = payload.get("options", {})
                if not isinstance(options, dict) or set(options) - {"temperature", "num_predict"}:
                    raise ValueError()
                if type(options.get("temperature", 0)) not in (int, float) or options.get("temperature", 0) != 0:
                    raise ValueError()
                if type(options.get("num_predict", 128)) is not int or not 1 <= options.get("num_predict", 128) <= 256:
                    raise ValueError()
                messages = payload.get("messages")
                if self.settings.profile == "local_model" and isinstance(messages, list):
                    normalized = []
                    for message in messages:
                        if isinstance(message, dict) and "images" in message:
                            # Pinned OllamaChatConfig emits images=[] even for
                            # plain text. Only this empty native metadata may be
                            # omitted from the exact inspected-text comparison.
                            if type(message["images"]) is not list or message["images"]:
                                raise ValueError()
                            message = {key: value for key, value in message.items() if key != "images"}
                        normalized.append(message)
                    messages = normalized
                actual_parts = normalize_messages(messages, "input")
                expected_parts = turn.input_decision.transformed_parts if turn.input_decision.action == "redact" else turn.input_parts
                if actual_parts != expected_parts:
                    raise ValueError()
            except (ValueError, TypeError):
                raise EvolutionError("inspection_incomplete") from None
            headers = {key: value for key, value in request.headers.items()
                       if key.lower() not in {"host", "content-length", "transfer-encoding", "connection", "accept-encoding"}}
            result = await self.transport.request(route, method=request.method, path=path, body=bytes(body),
                headers=headers, deadline_monotonic=deadline,
                max_response_bytes=MAX_WIRE_BYTES)
            return httpx.Response(result.status_code, headers=result.headers, content=result.content, request=request)
        except TimeoutError:
            turn.error, turn.error_status = EvolutionError("upstream_timeout"), 504
            raise httpx.ReadTimeout("Model dispatch deadline exceeded", request=request) from None
        except EvolutionError as error:
            turn.error, turn.error_status = error, error.status
            raise httpx.ConnectError("Model dispatch failed", request=request) from None

    async def aclose(self):
        pass  # Each pinned dispatch owns and closes its one-shot connection pool.


class GatewayBoundary:
    def __init__(self, proxy, settings: GatewaySettings, *, callbacks=None, native_http_handler=None):
        self.proxy, self.settings = proxy, settings
        self.gateway_token = read_secret(settings.gateway_token_file)
        self.callbacks = callbacks
        self.native_http_handler = native_http_handler

    def guards(self):
        if self.callbacks is not None:
            return tuple(self.callbacks())
        import litellm

        from .evolution_guardrail import EvolutionGuardrail
        return tuple(callback for callback in litellm.callbacks if isinstance(callback, EvolutionGuardrail)
                     and callback.default_on and callback.settings == self.settings)

    @staticmethod
    async def respond(send, status, body, request_id):
        raw = canonical_json_bytes(body)
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()),
                   (b"cache-control", b"no-store"), (b"x-request-id", request_id.encode())]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": raw})

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            async def lifecycle(message):
                if message["type"] == "lifespan.shutdown.complete":
                    for callback in self.guards():
                        await callback.client.close()
                    import litellm
                    if self.native_http_handler is not None:
                        await self.native_http_handler.close()
                    elif litellm.aclient_session is not None:
                        await litellm.aclient_session.aclose()
                await send(message)
            await self.proxy(scope, receive, lifecycle)
            return
        if scope["type"] != "http":
            return
        turn = Turn(native_http_handler=self.native_http_handler)
        token = current_turn.set(turn)
        try:
            if scope["path"] == "/health/live" and scope["method"] == "GET":
                await self.respond(send, 200, {"live": True}, turn.correlation_id)
                return
            headers = {}
            for key, value in scope["headers"]:
                key = key.lower()
                if key in headers:
                    raise EvolutionError("invalid_json")
                headers[key] = value
            if not hmac.compare_digest(headers.get(b"authorization", b""), ("Bearer " + self.gateway_token).encode()):
                raise EvolutionError("authentication_failed")
            if scope.get("query_string"):
                raise EvolutionError("unsupported_surface")
            for name in (b"x-request-id", b"x-correlation-id"):
                if name in headers:
                    try:
                        value = headers[name].decode("ascii")
                        if str(UUID(value)) != value:
                            raise ValueError()
                        turn.correlation_id = value
                    except (UnicodeError, ValueError):
                        raise EvolutionError("invalid_json") from None
            guards = self.guards()
            if len(guards) != 1:
                raise EvolutionError("capability_missing")
            if scope["path"] == "/health/ready" and scope["method"] == "GET":
                capabilities = await guards[0].client.capabilities()
                ready = all(any(c.stage == stage and c.status == "enabled" and "text" in c.part_kinds
                                for c in capabilities) for stage in ("input", "output"))
                await self.respond(send, 200 if ready else 503, {"ready": ready}, turn.correlation_id)
                return
            if scope["path"] != "/v1/chat/completions" or scope["method"] != "POST":
                raise EvolutionError("unsupported_surface")
            if headers.get(b"content-type", b"").split(b";", 1)[0].lower() != b"application/json":
                raise EvolutionError("invalid_json")
            if headers.get(b"content-encoding", b"identity").lower() != b"identity":
                raise EvolutionError("unsupported_surface")
            raw = bytearray()
            async with asyncio.timeout(1.5):
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    chunk = event.get("body", b"")
                    if len(raw) + len(chunk) > MAX_WIRE_BYTES:
                        raise EvolutionError("payload_too_large")
                    raw.extend(chunk)
                    if not event.get("more_body", False):
                        break
            payload = validate_request(bytes(raw), self.settings.model_alias)
            for name, value in (("stream", False), ("n", 1), ("temperature", 0), ("max_tokens", 128)):
                payload.setdefault(name, value)
            body = canonical_json_bytes(payload)
            raw.clear()
            consumed = False
            disconnected = asyncio.Event()

            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                await disconnected.wait()
                return {"type": "http.disconnect"}

            async def watch_disconnect():
                while True:
                    if (await receive())["type"] == "http.disconnect":
                        disconnected.set()
                        return

            status = 502
            captured = bytearray()

            async def capture(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                elif message["type"] == "http.response.body":
                    chunk = message.get("body", b"")
                    if len(captured) + len(chunk) > MAX_WIRE_BYTES:
                        raise EvolutionError("payload_too_large")
                    captured.extend(chunk)

            proxy_scope = dict(scope)
            # The proxy receives exactly the validated bytes and a matching length.
            proxy_scope["headers"] = [(key, value) for key, value in scope["headers"]
                                      if key.lower() not in {b"content-length", b"transfer-encoding"}]
            proxy_scope["headers"].append((b"content-length", str(len(body)).encode()))
            try:
                async with asyncio.timeout(self.settings.model_timeout + 2 * self.settings.evaluation_timeout + .5):
                    proxy_task = asyncio.create_task(self.proxy(proxy_scope, replay, capture))
                    disconnect_task = asyncio.create_task(watch_disconnect())
                    try:
                        await asyncio.wait((proxy_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED)
                        if disconnected.is_set():
                            return
                        await proxy_task
                    finally:
                        for task in (proxy_task, disconnect_task):
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(proxy_task, disconnect_task, return_exceptions=True)
            except TimeoutError:
                turn.error = turn.error or EvolutionError("upstream_timeout")
                turn.error_status = 504
            except Exception as failure:  # noqa: BLE001 - provider exceptions must not expose raw content
                # Upstream exception strings may contain raw content or credentials.
                status = 502
                if os.environ.get("EVOLUTION_CONNECTOR_DIAGNOSTICS") == "1":
                    print(json.dumps({"event": "gateway_failure", "type": type(failure).__name__}), file=sys.stderr, flush=True)
            if os.environ.get("EVOLUTION_CONNECTOR_DIAGNOSTICS") == "1":
                print(json.dumps({"event": "gateway_progress", "status": status,
                                  "input": turn.input_decision is not None, "route": turn.route_verified,
                                  "dispatch": turn.model_dispatched, "output": turn.output_decision is not None,
                                  "error": turn.error.code if turn.error else None}), file=sys.stderr, flush=True)
            if turn.error is not None:
                await self.respond(send, turn.error_status, turn.error.envelope(turn.correlation_id), turn.correlation_id)
                return
            if status != 200:
                await self.respond(send, 502, EvolutionError("evaluator_unavailable").envelope(turn.correlation_id), turn.correlation_id)
                return
            before, after = turn.input_decision, turn.output_decision
            if before is None or after is None or not turn.route_verified or not turn.model_dispatched:
                raise EvolutionError("inspection_incomplete")
            try:
                value = strict_json_loads(bytes(captured))
                if os.environ.get("EVOLUTION_CONNECTOR_DIAGNOSTICS") == "1":
                    message = value.get("choices", [{}])[0].get("message", {})
                    known = ("role", "content", "tool_calls", "function_call", "refusal", "audio", "annotations", "reasoning_content", "provider_specific_fields", "reasoning", "thinking", "content_blocks")
                    print(json.dumps({"event": "gateway_response_shape", "fields": {name: type(message[name]).__name__ for name in known if name in message}, "unknown_count": len(set(message) - set(known))}), file=sys.stderr, flush=True)
                result = validate_output(value, proxy_metadata=True)
            except (ValueError, TypeError):
                raise EvolutionError("inspection_incomplete") from None
            # A callback cannot inspect one response then let another response out.
            expected_text = (after.transformed_parts[0].text if after.action == "redact" else turn.output_parts[0].text)
            if result["choices"][0]["message"]["content"] != expected_text:
                raise EvolutionError("unsafe_transformation")
            result.update(id="chatcmpl-" + turn.correlation_id, object="chat.completion",
                          created=int(time.time()), model=self.settings.model_alias)
            result["guardrails"] = {
                "correlation_id": turn.correlation_id,
                "input_decision_id": before.decision_id, "output_decision_id": after.decision_id,
                "policy": after.policy.model_dump(mode="json"), "binding_revision": after.binding_revision,
                "snapshot_generation": after.snapshot_generation,
                "input_action": before.action, "output_action": after.action,
                "reason_codes": sorted(set(before.reason_codes) | set(after.reason_codes)),
                "audit_status": "spooled" if "spooled" in {before.audit_status, after.audit_status} else "persisted",
            }
            await self.respond(send, 200, result, turn.correlation_id)
        except EvolutionError as error:
            await self.respond(send, error.status, error.envelope(turn.correlation_id), turn.correlation_id)
        except (TimeoutError, httpx.HTTPError):
            await self.respond(send, 503, EvolutionError("evaluator_unavailable").envelope(turn.correlation_id), turn.correlation_id)
        finally:
            turn.input_parts = turn.output_parts = ()
            turn.input_decision = turn.output_decision = None
            current_turn.reset(token)


def create_app():
    settings = GatewaySettings.from_file(os.environ["EVOLUTION_GATEWAY_CONFIG_FILE"])
    os.environ["LITELLM_MASTER_KEY"] = read_secret(settings.gateway_token_file)
    if settings.model_token_file:
        os.environ["EVOLUTION_MODEL_KEY"] = read_secret(settings.model_token_file)
    os.environ["CONFIG_FILE_PATH"] = settings.litellm_config_file
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["DISABLE_AIOHTTP_TRANSPORT"] = "True"
    factory = logging.getLogRecordFactory()

    def safe_record(*args, **kwargs):
        record = factory(*args, **kwargs)
        if record.name.lower().startswith(("litellm", "openai", "httpx", "httpcore")):
            record.msg, record.args = "gateway_dependency_event", ()
            record.exc_info = record.exc_text = record.stack_info = None
        return record

    logging.setLogRecordFactory(safe_record)
    import litellm

    async def inspect_response(response):
        if response.status_code != 200:
            return
        try:
            async with asyncio.timeout(settings.model_timeout):
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_WIRE_BYTES:
                        raise EvolutionError("payload_too_large")
                    raw.extend(chunk)
                if settings.profile == "deterministic_mock":
                    validate_output(strict_json_loads(bytes(raw)))
                else:
                    value = strict_json_loads(bytes(raw))
                    # Native Ollama JSON is normalized here only for shape checking.
                    validate_output({"choices": [{"message": value["message"],
                                      "finish_reason": "stop" if value.get("done") is True else None}]})
                response._content = bytes(raw)
        except (ValueError, KeyError, TypeError, EvolutionError, TimeoutError):
            turn = current_turn.get()
            if turn is not None:
                turn.error, turn.error_status = EvolutionError("inspection_incomplete"), 503
            raise ValueError("Unsupported model response") from None

    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

    class BoundNativeHTTPHandler(AsyncHTTPHandler):
        """Keep both native providers on one owned client without hidden retries."""

        def create_client(self, *args, **kwargs):
            if getattr(self, "_created", False):
                raise httpx.ConnectError("Native HTTP client replacement is forbidden")
            self._created = True
            return super().create_client(*args, **kwargs)

    native_http_handler = BoundNativeHTTPHandler(
        transport=ModelTransport(settings), follow_redirects=False,
        timeout=httpx.Timeout(settings.model_timeout, connect=.25),
        event_hooks={"response": [inspect_response]},
    )
    # OpenAI consumes the raw HTTPX client; Ollama requires its native wrapper.
    # Both therefore execute exactly the same final approved transport and hook.
    litellm.aclient_session = native_http_handler.client
    litellm.register_model({settings.model_route.split("/", 1)[1]: {
        "litellm_provider": settings.model_route.split("/", 1)[0], "mode": "chat",
        "supports_system_messages": True, "input_cost_per_token": 0,
        "output_cost_per_token": 0, "max_tokens": 256,
    }})
    from litellm.proxy.proxy_server import app as proxy
    return GatewayBoundary(proxy, settings, native_http_handler=native_http_handler)


def main():
    import uvicorn
    settings = GatewaySettings.from_file(os.environ["EVOLUTION_GATEWAY_CONFIG_FILE"])
    uvicorn.run(create_app(), host="0.0.0.0", port=settings.port,
                ssl_certfile=settings.tls_cert_file, ssl_keyfile=settings.tls_key_file,
                access_log=False, log_level="warning")


if __name__ == "__main__":
    # Native callbacks import this module canonically for strict class checks.
    # Construct the app there even when Python executes this file as __main__.
    from connector.evolution_gateway import main as canonical_main

    canonical_main()
