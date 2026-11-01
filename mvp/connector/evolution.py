"""Public v2 connector contract, transient turn state and bounded HTTPS client.

This module imports no repositories, policy compiler or LiteLLM internals. The
native LiteLLM adapter and protective HTTP boundary use the same validation.
"""
from __future__ import annotations

import asyncio
import os
import ssl
import stat
import time
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import (
    UUID,
    Capability,
    ContentPart,
    Decision,
    DomainModel,
    EvaluationRequest,
    Identifier,
    Slug,
    canonical_json_bytes,
    strict_json_loads,
)
from pydantic import Field, model_validator

MAX_WIRE_BYTES = 1024 * 1024


def https_origin(value: str) -> str:
    """Require an explicit canonical HTTPS endpoint without URL authority tricks."""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.netloc != parsed.netloc.lower()
                or "\\" in value or "%" in value
                or any(part in {".", ".."} for part in parsed.path.split("/"))
                or "//" in parsed.path
                or any(c.isspace() for c in value)
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError()
        value.encode("ascii")
    except (ValueError, UnicodeError):
        raise ValueError("Invalid HTTPS endpoint") from None
    return value.rstrip("/")


class GatewaySettings(DomainModel):
    """Operator-mounted adapter settings; no request may override these values."""

    tenant_id: Slug
    workload_id: Slug
    provider_id: UUID
    model_alias: Identifier
    model_route: Identifier
    model_base_url: str
    runtime_url: str
    workload_token_file: str
    gateway_token_file: str
    model_token_file: str | None = None
    ca_file: str
    tls_cert_file: str
    tls_key_file: str
    litellm_config_file: str
    approved_routes_file: str
    profile: Literal["deterministic_mock", "local_model"]
    port: int = Field(default=8443, ge=1024, le=65535)

    @model_validator(mode="after")
    def routes(self):
        for name in ("runtime_url", "model_base_url"):
            if https_origin(getattr(self, name)) != getattr(self, name):
                raise ValueError("Endpoint must be canonical without trailing slash")
        if urlsplit(self.runtime_url).path:
            raise ValueError("Runtime endpoint must be an origin")
        adapter, separator, model = self.model_route.partition("/")
        required = "openai" if self.profile == "deterministic_mock" else "ollama_chat"
        if not separator or not model or adapter != required:
            raise ValueError("Model adapter does not match the prepared profile")
        return self

    @property
    def model_timeout(self) -> float:
        return 10.0 if self.profile == "deterministic_mock" else 45.0

    @classmethod
    def from_file(cls, path: str):
        raw = Path(path).read_bytes()
        if len(raw) > 16384:
            raise ValueError("Gateway configuration exceeds byte limit")
        return cls.model_validate_json(raw)


def read_secret(path: str) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_size > 4096):
            raise ValueError("Credential file ownership or permissions are invalid")
        value = os.read(descriptor, 4097).decode("ascii").strip()
        if not value or any(c.isspace() for c in value):
            raise ValueError("Credential file is invalid")
        return value
    finally:
        os.close(descriptor)


@dataclass
class Turn:
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
    context_id: str | None = None
    input_decision: Decision | None = None
    output_decision: Decision | None = None
    input_parts: tuple[ContentPart, ...] = ()
    output_parts: tuple[ContentPart, ...] = ()
    error: EvolutionError | None = None
    error_status: int = 503
    route_verified: bool = False
    model_dispatched: bool = False
    native_http_handler: Any = None


current_turn: ContextVar[Turn | None] = ContextVar("evolution_gateway_turn", default=None)


def normalize_messages(messages: Any, stage: str) -> tuple[ContentPart, ...]:
    if stage not in {"input", "output"} or not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise EvolutionError("unsupported_surface")
    parts = []
    for index, message in enumerate(messages):
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message.get("role") not in {"system", "user", "assistant"}
                or type(message.get("content")) is not str):
            raise EvolutionError("unsupported_surface")
        if stage == "output" and (len(messages) != 1 or message["role"] != "assistant"):
            raise EvolutionError("unsupported_surface")
        try:
            size = len(message["content"].encode("utf-8"))
        except UnicodeError:
            raise EvolutionError("unsupported_surface") from None
        if size > 65536:
            raise EvolutionError("payload_too_large")
        try:
            parts.append(ContentPart(part_id=f"m{index}", kind="text", role=message["role"], text=message["content"]))
        except ValueError:
            raise EvolutionError("unsupported_surface") from None
    if sum(len(part.text.encode("utf-8")) for part in parts) > 65536:
        raise EvolutionError("payload_too_large")
    return tuple(parts)


def validate_request(raw: bytes, model_alias: str) -> dict:
    if len(raw) > MAX_WIRE_BYTES:
        raise EvolutionError("payload_too_large")
    try:
        body = strict_json_loads(raw)
    except (ValueError, TypeError):
        raise EvolutionError("invalid_json") from None
    if not isinstance(body, dict):
        raise EvolutionError("invalid_json")
    if set(body) & {"guardrails", "guardrail", "metadata", "litellm_metadata", "policy_id", "policy_version",
                    "tenant_id", "workload_id", "api_key", "api_base", "skip_guardrails"}:
        raise EvolutionError("forbidden")
    if set(body) - {"model", "messages", "stream", "n", "temperature", "max_tokens"}:
        raise EvolutionError("unsupported_surface")
    if body.get("model") != model_alias:
        raise EvolutionError("provider_denied")
    if type(body.get("stream", False)) is not bool or body.get("stream", False):
        raise EvolutionError("unsupported_surface")
    for name, default in (("n", 1), ("temperature", 0), ("max_tokens", 128)):
        value = body.get(name, default)
        if type(value) is not int or (not 1 <= value <= 256 if name == "max_tokens" else value != default):
            raise EvolutionError("unsupported_surface")
    normalize_messages(body.get("messages"), "input")
    return body


class TextConnector:
    def __init__(self, tenant_id: str, workload_id: str, provider_id: str):
        self.tenant_id, self.workload_id, self.provider_id = tenant_id, workload_id, provider_id

    def normalize(self, payload: dict, stage: str) -> EvaluationRequest:
        turn = current_turn.get()
        if turn is None:
            raise EvolutionError("context_invalid")
        parts = normalize_messages(payload.get("messages"), stage)
        try:
            return EvaluationRequest(
                request_id=str(uuid4()), correlation_id=turn.correlation_id,
                tenant_id=self.tenant_id, workload_id=self.workload_id, stage=stage,
                parts=parts, context_id=turn.context_id if stage != "input" else None,
                provider_id=self.provider_id, deadline_ms=500,
            )
        except ValueError:
            raise EvolutionError("context_invalid") from None

    @staticmethod
    def verify(decision: Decision, request: EvaluationRequest, before: Decision | None = None) -> None:
        # Reparse even an injected object so model_copy cannot bypass validation.
        try:
            decision = Decision.model_validate(decision.model_dump(mode="json"))
        except ValueError:
            raise EvolutionError("inspection_incomplete") from None
        if (decision.request_id != request.request_id or decision.tenant_id != request.tenant_id
                or decision.workload_id != request.workload_id or decision.stage != request.stage):
            raise EvolutionError("context_invalid")
        if decision.action != "block" and (decision.context_id is None or decision.policy is None
                or decision.binding_revision is None or decision.snapshot_generation is None
                or decision.audit_status not in {"persisted", "spooled"}):
            raise EvolutionError("inspection_incomplete")
        if request.stage != "input" and (
                    before is None or decision.context_id != request.context_id
                    or decision.context_id != before.context_id or decision.policy != before.policy
                    or decision.binding_revision != before.binding_revision
                    or decision.snapshot_generation is None or before.snapshot_generation is None
                    or decision.snapshot_generation < before.snapshot_generation):
            raise EvolutionError("context_invalid")
        part_map = {part.part_id: part for part in request.parts}
        if {f.evaluator_revision for f in decision.findings} - set(decision.evaluator_revisions):
            raise EvolutionError("inspection_incomplete")
        for finding in decision.findings:
            part = part_map.get(finding.part_id)
            if (part is None or finding.json_pointer is not None
                    or finding.start is not None and finding.end > len(part.text)):
                raise EvolutionError("unsafe_transformation")
        if decision.action == "redact":
            from guardrails.evolution.transformations import validate_transformed_parts
            validate_transformed_parts(request.parts, decision.findings, decision.transformed_parts)

    def enforce(self, decision: Decision, payload: dict) -> dict:
        if decision.action == "block":
            # The boundary distinguishes completed content blocks from unavailable
            # decisions, without adding new public error codes.
            operational = set(decision.reason_codes) & {
                "evaluator_unavailable", "evaluation_timeout", "audit_failed", "snapshot_stale",
                "context_invalid", "unsafe_transformation", "inspection_incomplete", "policy_unresolved",
            }
            error = EvolutionError(min(operational) if operational else "forbidden")
            error.status = 503 if operational else 403
            raise error
        result = dict(payload)
        messages = payload.get("messages")
        if decision.action == "redact":
            from guardrails.evolution.transformations import validate_transformed_parts
            validate_transformed_parts(normalize_messages(messages, decision.stage), decision.findings, decision.transformed_parts)
            if not isinstance(messages, list) or len(messages) != len(decision.transformed_parts):
                raise EvolutionError("unsafe_transformation")
            result["messages"] = [dict(message, content=part.text) for message, part in zip(messages, decision.transformed_parts, strict=True)]
        return result


class EvaluationClient:
    """No content cache, no after-delivery retry, and a metadata-only breaker probe."""

    def __init__(self, runtime_url: str, token_file: str, ca_file: str, *,
                 clock: Callable[[], float] = time.monotonic, transport=None):
        self.runtime_url = https_origin(runtime_url)
        self.token = read_secret(token_file)
        self.clock = clock
        self.failures: deque[float] = deque()
        self.opened_at: float | None = None
        self.probing = False
        self.client = httpx.AsyncClient(
            base_url=self.runtime_url, verify=ssl.create_default_context(cafile=ca_file),
            trust_env=False, follow_redirects=False, transport=transport,
            timeout=httpx.Timeout(1.5, connect=.25),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )

    async def close(self):
        await self.client.aclose()

    def _transport_failure(self):
        now = self.clock()
        while self.failures and self.failures[0] < now - 30:
            self.failures.popleft()
        self.failures.append(now)
        if len(self.failures) >= 5:
            self.opened_at = now

    async def _request(self, method: str, path: str, *, body: bytes | None = None,
                       request_id: str | None = None) -> bytes:
        headers = {"Authorization": "Bearer " + self.token, "Accept-Encoding": "identity"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        async with self.client.stream(method, path, headers=headers, content=body) as response:
            if response.status_code != 200:
                # A completed structural denial is not a transport failure.
                self.failures.clear()
                # Intermediaries may echo input or credentials. Never consume errors.
                if response.status_code == 401:
                    raise EvolutionError("authentication_failed")
                if response.status_code == 429:
                    raise EvolutionError("quota_exceeded")
                raise EvolutionError("evaluator_unavailable")
            if (response.headers.get("content-encoding", "identity").lower() != "identity"
                    or response.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json"):
                self.failures.clear()
                raise EvolutionError("inspection_incomplete")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > MAX_WIRE_BYTES:
                    self.failures.clear()
                    raise EvolutionError("inspection_incomplete")
                data.extend(chunk)
            # Headers alone cannot reset the breaker: the body may still time out.
            self.failures.clear()
            return bytes(data)

    async def capabilities(self) -> tuple[Capability, ...]:
        async with asyncio.timeout(1.5):
            raw = await self._request("GET", "/v2/capabilities")
        try:
            value = strict_json_loads(raw)
            if not isinstance(value, list) or len(value) > 256:
                raise ValueError()
            return tuple(Capability.model_validate(item) for item in value)
        except ValueError:
            raise EvolutionError("inspection_incomplete") from None

    async def _admit(self):
        if self.opened_at is None:
            return
        if self.clock() - self.opened_at < 10 or self.probing:
            raise EvolutionError("evaluator_unavailable")
        self.probing = True
        try:
            capabilities = await self.capabilities()
            if not any(c.status == "enabled" for c in capabilities):
                raise EvolutionError("evaluator_unavailable")
            self.failures.clear()
            self.opened_at = None
        except BaseException:
            self.opened_at = self.clock()
            raise
        finally:
            self.probing = False

    async def evaluate(self, request: EvaluationRequest) -> Decision:
        try:
            async with asyncio.timeout(1.5):
                await self._admit()
                body = canonical_json_bytes(request)
                for attempt in range(2):
                    try:
                        raw = await self._request("POST", "/v2/evaluations", body=body, request_id=request.request_id)
                        break
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        # These failures occur before the request body is delivered.
                        self._transport_failure()
                        if attempt or self.opened_at is not None:
                            raise EvolutionError("evaluator_unavailable") from None
                try:
                    decision = Decision.model_validate_json(raw)
                except ValueError:
                    raise EvolutionError("inspection_incomplete") from None
                self.failures.clear()
                return decision
        except (TimeoutError, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            self._transport_failure()
            raise EvolutionError("evaluator_unavailable") from None
