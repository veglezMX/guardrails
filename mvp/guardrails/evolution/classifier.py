"""Policy-bound English classifier port over the existing pinned HTTPS client."""
from __future__ import annotations

import hashlib
import math
import threading
import time
from dataclasses import dataclass, field
from uuid import uuid4

from .classifier_contract import (
    DETECTOR_SECONDS,
    EVALUATOR_REVISION,
    MAX_CONTENT_BYTES,
    MAX_RESULT_BYTES,
    MAX_SEGMENTS,
    MAX_WINDOWS,
    MODEL_ID,
    STAGES,
    WEIGHTS_SHA256,
    ClassifierError,
    canonical,
    identity,
    parse,
    score_checked,
)
from .egress import AuthorizedRoute
from .errors import EvolutionError
from .models import (
    Capability,
    ContentPart,
    Destination,
    Finding,
    PolicyVersion,
    ProviderProfile,
    Rule,
    canonical_json_bytes,
)
from .residency import ResidencyGuard


def _segments(parts):
    if not isinstance(parts, tuple) or not 1 <= len(parts) <= 64 or any(not isinstance(part, ContentPart) for part in parts) or len({part.part_id for part in parts}) != len(parts):
        raise EvolutionError("validation_failed")
    result, size = [], 0

    def append(part, text, pointer):
        if len(result) >= MAX_SEGMENTS:
            raise EvolutionError("inspection_incomplete")
        result.append({"part_id": part.part_id, "role": part.role, "json_pointer": pointer, "text": text})

    def walk(part, value, pointer):
        if isinstance(value, str):
            append(part, value, pointer)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(part, item, pointer + "/" + str(index))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(part, item, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))

    for part in parts:
        try:
            checked = ContentPart.model_validate_json(canonical_json_bytes(part.model_dump(mode="json", warnings="error")))
        except (ValueError, TypeError, RecursionError):
            raise EvolutionError("validation_failed") from None
        if checked.kind not in {"text", "json"}:
            raise EvolutionError("unsupported_surface")
        size += len(checked.text.encode("utf-8") if checked.kind == "text" else canonical_json_bytes(checked.value))
        if size > MAX_CONTENT_BYTES:
            raise EvolutionError("payload_too_large")
        if checked.kind == "text":
            append(checked, checked.text, None)
        else:
            walk(checked, checked.value, "")
    return result


@dataclass(frozen=True, init=False, repr=False)
class ClassifierEndpointConfig:
    """Operator-owned exact binding. Neither repr nor errors include credentials."""

    provider_bytes: bytes
    destination_bytes: bytes
    runtime_zone: str
    language_profile: str
    bearer_token: str = field(repr=False)
    binding_key: str

    def __init__(self, *, provider, destination, runtime_zone, language_profile, bearer_token):
        if not isinstance(provider, ProviderProfile) or not isinstance(destination, Destination) or language_profile != "en":
            raise EvolutionError("capability_missing")
        try:
            provider_bytes, destination_bytes = canonical_json_bytes(provider), canonical_json_bytes(destination)
            checked_provider = ProviderProfile.model_validate_json(provider_bytes)
            checked_destination = Destination.model_validate_json(destination_bytes)
            AuthorizedRoute(checked_provider, checked_destination, external_processing=False).validate()
            if checked_provider.vendor != "local_classifier" or checked_provider.kind != "evaluator" or checked_provider.model_id != MODEL_ID or checked_provider.artifact_digest != WEIGHTS_SHA256 or checked_provider.zone != runtime_zone or checked_provider.credential_ref is None:
                raise EvolutionError("capability_missing")
            if type(bearer_token) is not str or not 32 <= len(bearer_token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in bearer_token):
                raise EvolutionError("evaluator_unavailable")
        except (ValueError, TypeError):
            raise EvolutionError("policy_unresolved") from None
        token_digest = hashlib.sha256(bearer_token.encode("ascii")).hexdigest()
        key = hashlib.sha256(canonical({"provider": provider_bytes.decode(), "destination": destination_bytes.decode(), "zone": runtime_zone, "language": language_profile, "identity": identity(), "credential_sha256": token_digest})).hexdigest()
        for name, value in (("provider_bytes", provider_bytes), ("destination_bytes", destination_bytes), ("runtime_zone", runtime_zone), ("language_profile", language_profile), ("bearer_token", bearer_token), ("binding_key", key)):
            object.__setattr__(self, name, value)

    def route(self):
        return AuthorizedRoute(ProviderProfile.model_validate_json(self.provider_bytes), Destination.model_validate_json(self.destination_bytes), external_processing=False)


@dataclass(frozen=True)
class ClassifierHealthSnapshot:
    ready: bool
    observed_at: float
    expires_at: float
    generation: int


class ClassifierEndpoint:
    """One registry-owned endpoint; shared health is safe in metadata threads.

    The registry creates a bounded set from operator configuration, refreshes
    each at <=500 ms, and never creates endpoint cache entries from requests.
    """

    def __init__(self, config: ClassifierEndpointConfig, transport):
        if not isinstance(config, ClassifierEndpointConfig):
            raise EvolutionError("validation_failed")
        self.config, self._transport = config, transport
        self._lock = threading.Lock()
        self._closed = False
        self._health = ClassifierHealthSnapshot(False, 0.0, 0.0, 0)

    @property
    def binding_key(self):
        return self.config.binding_key

    def health_snapshot(self):
        with self._lock:
            row = self._health
            return ClassifierHealthSnapshot(row.ready and not self._closed and time.monotonic() < row.expires_at, row.observed_at, row.expires_at, row.generation)

    def _begin_observation(self):
        with self._lock:
            if self._closed:
                raise EvolutionError("evaluator_unavailable")
            return self._health.generation

    def _unavailable(self):
        with self._lock:
            self._health = ClassifierHealthSnapshot(False, time.monotonic(), 0.0, self._health.generation + 1)

    def _observed_ready(self, generation):
        with self._lock:
            if not self._closed and generation == self._health.generation:
                now = time.monotonic()
                self._health = ClassifierHealthSnapshot(True, now, now + 1.0, generation)

    def capabilities(self):
        status = "enabled" if self.health_snapshot().ready else "unavailable"
        # Retrieval/tool stage enablement belongs to the later boundary gates.
        return tuple(Capability(capability_id="prompt-injection-en", stage=stage, part_kinds=("text", "json"), actions=("allow", "warn", "block"), evaluator_revision=EVALUATOR_REVISION, max_input_bytes=MAX_CONTENT_BYTES, timeout_ms=2900, zone=self.config.runtime_zone, status=status) for stage in ("input", "output"))

    def bind(self, policy, stage):
        return LocalClassifierEvaluator(policy=policy, stage=stage, endpoint=self)

    async def refresh_readiness(self, deadline_monotonic):
        generation = self._begin_observation()
        try:
            response = await self._transport.request(self.config.route(), method="GET", path="/v1/classifier/ready", body=b"", headers={"authorization": "Bearer " + self.config.bearer_token}, deadline_monotonic=deadline_monotonic, max_response_bytes=4096)
            body = parse(response.content, limit=4096)
            if response.status_code != 200 or type(body.get("api_major")) is not int or body.get("ready") is not True or body != {"api_major": 1, "ready": True, "identity": identity()}:
                raise EvolutionError("evaluator_unavailable")
        except ClassifierError:
            self._unavailable()
            raise EvolutionError("evaluator_unavailable") from None
        except BaseException:
            self._unavailable()
            raise
        self._observed_ready(generation)

    async def close(self):
        with self._lock:
            self._closed = True
            self._health = ClassifierHealthSnapshot(False, time.monotonic(), 0.0, self._health.generation + 1)


class LocalClassifierEvaluator:
    """Lightweight immutable policy/stage adapter; shares no mutable policy."""

    def __init__(self, *, policy, stage, endpoint: ClassifierEndpoint):
        if not isinstance(policy, PolicyVersion) or stage not in STAGES or not isinstance(endpoint, ClassifierEndpoint):
            raise EvolutionError("capability_missing")
        self._policy_bytes = canonical_json_bytes(policy.model_dump(mode="json", warnings="error"))
        self.stage, self.zone, self._endpoint = stage, endpoint.config.runtime_zone, endpoint
        self._closed = False
        self._route()

    def _route(self):
        config = self._endpoint.config
        configured = config.route()
        route = ResidencyGuard().authorize_provider(PolicyVersion.model_validate_json(self._policy_bytes), configured.provider.provider_id, runtime_zone=self.zone, purpose="evaluation")
        if canonical_json_bytes(route.provider) != config.provider_bytes or canonical_json_bytes(route.destination) != config.destination_bytes:
            raise EvolutionError("provider_denied")
        # A policy may separately allow Bedrock. This local route never admits a
        # public destination even when that independent permission is present.
        return configured

    def capabilities(self):
        return tuple(row.model_copy(update={"status": "unavailable"}) if self._closed else row for row in self._endpoint.capabilities() if row.stage == self.stage)

    async def refresh_readiness(self, deadline_monotonic):
        if self._closed:
            raise EvolutionError("evaluator_unavailable")
        await self._endpoint.refresh_readiness(deadline_monotonic)

    async def evaluate(self, rule: Rule, parts: tuple[ContentPart, ...], deadline_monotonic: float) -> tuple[Finding, ...]:
        if self._closed:
            raise EvolutionError("evaluator_unavailable")
        generation = self._endpoint._begin_observation()
        if type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic):
            raise EvolutionError("validation_failed")
        cutoff = min(deadline_monotonic, time.monotonic() + DETECTOR_SECONDS)
        if cutoff <= time.monotonic():
            raise EvolutionError("evaluation_timeout")
        policy = PolicyVersion.model_validate_json(self._policy_bytes)
        selected = next((item for item in policy.rules if isinstance(rule, Rule) and item.rule_id == rule.rule_id), None)
        if selected is None or canonical_json_bytes(selected) != canonical_json_bytes(rule) or rule.kind != "injection" or rule.evaluator_revision != EVALUATOR_REVISION or self.stage not in rule.stages:
            raise EvolutionError("capability_missing")
        segments = _segments(parts)
        budget = min(2900, int((cutoff - time.monotonic()) * 1000))
        if budget < 1:
            raise EvolutionError("evaluation_timeout")
        request_id = uuid4().hex
        payload = {"api_major": 1, "identity": identity(), "request_id": request_id, "budget_ms": budget, "segments": segments}
        try:
            response = await self._endpoint._transport.request(self._route(), method="POST", path="/v1/classifier/evaluate", body=canonical(payload), headers={"authorization": "Bearer " + self._endpoint.config.bearer_token, "content-type": "application/json"}, deadline_monotonic=cutoff, max_response_bytes=MAX_RESULT_BYTES)
            result = parse(response.content, limit=MAX_RESULT_BYTES)
            if response.status_code != 200:
                errors = {422: "inspection_incomplete", 429: "queue_full", 503: "evaluator_unavailable", 504: "evaluation_timeout"}
                if response.status_code in errors and result == {"error": errors[response.status_code]}:
                    raise EvolutionError(errors[response.status_code])
                raise EvolutionError("evaluator_unavailable")
            if set(result) != {"api_major", "identity", "request_id", "complete", "windows", "scores"} or type(result["api_major"]) is not int or result["api_major"] != 1 or result["identity"] != identity() or result["request_id"] != request_id or result["complete"] is not True or type(result["windows"]) is not int or not 0 <= result["windows"] <= MAX_WINDOWS or type(result["scores"]) is not list or len(result["scores"]) != len(segments):
                raise EvolutionError("inspection_incomplete")
            findings, count = [], 0
            for expected, row in zip(segments, result["scores"], strict=True):
                if type(row) is not dict or set(row) != {"part_id", "json_pointer", "score", "windows"} or row["part_id"] != expected["part_id"] or row["json_pointer"] != expected["json_pointer"] or not score_checked(row["score"]) or type(row["windows"]) is not int or not 1 <= row["windows"] <= MAX_WINDOWS:
                    raise EvolutionError("inspection_incomplete")
                count += row["windows"]
                if row["score"] >= rule.config.threshold:
                    findings.append(Finding(rule_id=rule.rule_id, category="injection", part_id=row["part_id"], reason_code="injection_detected", json_pointer=row["json_pointer"], score=row["score"], evaluator_revision=EVALUATOR_REVISION))
            if count != result["windows"] or time.monotonic() >= cutoff:
                raise EvolutionError("evaluation_timeout" if time.monotonic() >= cutoff else "inspection_incomplete")
            if self._closed:
                raise EvolutionError("evaluator_unavailable")
            self._endpoint._begin_observation()  # Refuse a concurrent endpoint shutdown.
            self._endpoint._observed_ready(generation)
            return tuple(findings)
        except ClassifierError:
            self._endpoint._unavailable()
            raise EvolutionError("evaluator_unavailable") from None
        except BaseException:
            self._endpoint._unavailable()
            raise

    async def close(self):
        self._closed = True
