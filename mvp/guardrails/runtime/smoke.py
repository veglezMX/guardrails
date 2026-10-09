"""Phase-one fixture evaluator, not a management snapshot or production runtime.

The seed is loaded once and never edited by an API. Scenario controls select a
fixture binding state; output contexts keep the input state. Durable management,
snapshot distribution and supervised execution belong to subsequent phases.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from guardrails.transport import parse_policy, strict_json_loads

SCHEMA_VERSION = "local-proof-v1"
STATES = ("disabled", "observe", "warn", "redact", "block")
ACTIONS = ("allow", "log_only", "redact", "block")
# Token extraction prevents matching a valid suffix inside a malformed address.
_EMAIL_TOKEN = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_\x60{|}~@-]+")
_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_\x60{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_\x60{|}~-]+)*")
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_TLD = re.compile(r"[A-Za-z]{2,63}")
_PHONE = re.compile(r"(?<![\w+])\+[1-9][0-9]{7,14}(?![\w+])", re.ASCII)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def strict_json(raw: bytes) -> Any:
    return strict_json_loads(raw)


def detect(text: str, pii_types: tuple[str, ...]) -> list[tuple[str, int, int]]:
    """Return code-point spans for the proof's explicitly limited text formats."""
    results: list[tuple[str, int, int]] = []
    if "email" in pii_types:
        for match in _EMAIL_TOKEN.finditer(text):
            token = match.group()
            # Sentence-final punctuation is a delimiter; leading/consecutive dots
            # are never stripped, preventing malformed-local-part suffix matches.
            candidate = token.rstrip(".")
            if candidate.count("@") != 1 or len(candidate) > 254:
                continue
            local, domain = candidate.split("@")
            labels = domain.split(".")
            if not (1 <= len(local) <= 64 and _LOCAL.fullmatch(local)):
                continue
            if len(labels) < 2 or not _TLD.fullmatch(labels[-1]):
                continue
            if not all(_LABEL.fullmatch(label) for label in labels):
                continue
            start, end = match.start(), match.start() + len(candidate)
            if (start and (text[start - 1].isalnum() or text[start - 1] in "_@")) or (
                end < len(text) and (text[end].isalnum() or text[end] in "_@")
            ):
                continue
            results.append(("email", start, end))
    if "phone" in pii_types:
        for match in _PHONE.finditer(text):
            # Explicit Unicode boundary checks supplement the ASCII expression.
            start, end = match.span()
            if (start and text[start - 1].isalnum()) or (
                end < len(text) and text[end].isalnum()
            ):
                continue
            results.append(("phone", start, end))
    return sorted(results, key=lambda item: (item[1], item[2], item[0]))


def redact(text: str, spans: list[tuple[int, int]]) -> str:
    """Validate first, then union touching/overlapping ranges in original text."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError("unsafe_transformation")
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    parts: list[str] = []
    position = 0
    for start, end in merged:
        parts.extend((text[position:start], "[REDACTED]"))
        position = end
    parts.append(text[position:])
    return "".join(parts)


@dataclass(frozen=True)
class SmokeContext:
    tenant_id: str
    workload_id: str
    correlation_id: str
    state: str
    revision: int
    expires_at: float
    client_config_json: bytes


class SmokeState:
    def __init__(self, config_path: Path, audit_path: Path):
        config = strict_json(config_path.read_bytes())
        expected = {
            "schema_version", "profile", "snapshot_generation", "workloads",
            "policy", "enforcement_state", "control_token_sha256",
        }
        if not isinstance(config, dict) or set(config) != expected:
            raise ValueError("invalid_smoke_configuration")
        if config["schema_version"] != SCHEMA_VERSION or config["profile"] != "smoke":
            raise ValueError("invalid_smoke_profile")
        if type(config["snapshot_generation"]) is not int or config["snapshot_generation"] != 1:
            raise ValueError("invalid_smoke_generation")
        self.policy = parse_policy(json.dumps(config["policy"]).encode("utf-8"))
        self.policy_document = config["policy"]
        if self.policy_document["retain_content"] is not False:
            raise ValueError("smoke_retention_must_be_disabled")
        if any(value != "block" for value in self.policy_document["failure_behavior"].values()):
            raise ValueError("smoke_failures_must_be_closed")
        # The full runtime will consume a published digest. The smoke fixture
        # derives the same content hash without fabricating a publication record.
        self.policy_digest = self.policy.to_domain().content_digest()
        self.workloads: dict[str, tuple[str, str]] = {}
        for item in config["workloads"]:
            if set(item) != {"tenant_id", "workload_id", "token_sha256"}:
                raise ValueError("invalid_smoke_identity")
            if item["tenant_id"] != self.policy_document["tenant_id"]:
                raise ValueError("invalid_smoke_tenant")
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", item["workload_id"]):
                raise ValueError("invalid_smoke_workload")
            if not re.fullmatch(r"[0-9a-f]{64}", item["token_sha256"]):
                raise ValueError("invalid_smoke_token_hash")
            identity = (item["tenant_id"], item["workload_id"])
            if item["token_sha256"] in self.workloads or identity in self.workloads.values():
                raise ValueError("duplicate_smoke_identity")
            self.workloads[item["token_sha256"]] = identity
        if not self.workloads:
            raise ValueError("missing_smoke_identity")
        self.control_hash = config["control_token_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", self.control_hash):
            raise ValueError("invalid_control_hash")
        if self.control_hash in self.workloads:
            raise ValueError("control_and_workload_credentials_must_differ")
        self.state = config["enforcement_state"]
        if self.state not in STATES:
            raise ValueError("invalid_smoke_state")
        self.binding_revision = 1
        self.contexts: dict[str, SmokeContext] = {}
        self.audit_path = audit_path

    def authenticate(self, token: str) -> tuple[str, str] | None:
        incoming = digest_token(token)
        for token_hash, identity in self.workloads.items():
            if hmac.compare_digest(incoming, token_hash):
                return identity
        return None

    def control_authenticated(self, token: str) -> bool:
        return hmac.compare_digest(digest_token(token), self.control_hash)

    def client_config(self, identity: tuple[str, str]) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "tenant_id": identity[0],
            "workload_id": identity[1], "generation": 1,
            "input_timeout_ms": 1500, "output_timeout_ms": 1500,
            "failure_behavior": dict(self.policy_document["failure_behavior"]),
            "retrieved_at": utc_now(),
        }

    def discard_expired(self) -> None:
        now = time.monotonic()
        for token in tuple(self.contexts):
            if self.contexts[token].expires_at <= now:
                del self.contexts[token]

    def context(self, identity: tuple[str, str], correlation_id: str) -> tuple[str, SmokeContext]:
        self.discard_expired()
        if len(self.contexts) >= 1024:
            raise ValueError("capacity_exceeded")
        token = secrets.token_urlsafe(32)
        value = SmokeContext(
            *identity, correlation_id, self.state, self.binding_revision,
            time.monotonic() + 120, json.dumps(self.client_config(identity)).encode("utf-8"),
        )
        self.contexts[token] = value
        return token, value

    def write_audit(self, decision: dict[str, Any]) -> None:
        """Content-free smoke journal; replace with the Phase-three repository."""
        metadata_keys = (
            "decision_id", "evaluation_id", "policy_id", "policy_version",
            "policy_digest", "binding_revision", "snapshot_generation",
            "enforcement_state", "stage", "action", "would_be_action",
            "reason_codes", "operational_codes", "guardrail_category", "allowed",
            "evaluator_metadata", "audit_id", "findings", "evaluation_ms",
        )
        event = {
            "event_id": decision["audit_id"], "source": "runtime",
            "occurred_at": utc_now(), "tenant_id": decision["tenant_id"],
            "workload_id": decision["workload_id"], "actor_id": None,
            "event_type": "smoke_evaluation", "correlation_id": decision["correlation_id"],
            "metadata": {key: decision[key] for key in metadata_keys}, "content": None,
        }
        self.audit_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            payload = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
            with os.fdopen(descriptor, "ab", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def evaluate(self, request: Any, identity: tuple[str, str]) -> dict[str, Any]:
        started = time.perf_counter()
        correlation_id = str(request.correlation_id)
        if request.stage == "input":
            context_id, context = self.context(identity, correlation_id)
        else:
            context_id = request.context_id
            self.discard_expired()
            context = self.contexts.get(context_id)
            if context is None:
                raise ValueError("context_invalid")
            if (context.tenant_id, context.workload_id) != identity or context.correlation_id != correlation_id:
                raise ValueError("context_invalid")
            del self.contexts[context_id]
        findings: list[dict[str, Any]] = []
        requested = "allow"
        evaluation_started = time.perf_counter()
        if context.state != "disabled":
            for check in self.policy_document["checks"]:
                if request.stage not in check["stages"]:
                    continue
                for segment in request.content.segments:
                    for pii_type, start, end in detect(segment.text, tuple(check["pii_types"])):
                        findings.append({
                            "check_id": check["check_id"], "segment_id": segment.segment_id,
                            "reason_code": f"GR-003:local_pii:{pii_type}", "start": start, "end": end,
                        })
                        if ACTIONS.index(check["requested_action"]) > ACTIONS.index(requested):
                            requested = check["requested_action"]
        elapsed = max(0.0, (time.perf_counter() - evaluation_started) * 1000)
        ceiling = {"disabled": "allow", "observe": "log_only", "warn": "log_only", "redact": "redact", "block": "block"}[context.state]
        action = ACTIONS[min(ACTIONS.index(requested), ACTIONS.index(ceiling))]
        transformed = None
        if action == "redact":
            transformed = {"segments": [
                {
                    "segment_id": segment.segment_id,
                    "text": redact(segment.text, [
                        (finding["start"], finding["end"]) for finding in findings
                        if finding["segment_id"] == segment.segment_id
                    ]),
                } for segment in request.content.segments
            ]}
        if action == "block":
            self.contexts.pop(context_id, None)
        reasons = sorted({finding["reason_code"] for finding in findings})
        decision = {
            "decision_id": str(uuid4()), "evaluation_id": str(uuid4()),
            "correlation_id": correlation_id,
            "context_id": context_id if action != "block" else None,
            "tenant_id": identity[0], "workload_id": identity[1], "stage": request.stage,
            "policy_id": self.policy_document["policy_id"], "policy_version": self.policy_document["version"],
            "policy_digest": self.policy_digest, "binding_revision": context.revision,
            "snapshot_generation": 1, "enforcement_state": context.state,
            "action": action, "allowed": action != "block", "would_be_action": requested,
            "reason_codes": reasons, "operational_codes": ["disabled"] if context.state == "disabled" else [],
            "guardrail_category": "GR-003" if reasons else None,
            "evaluator_metadata": [] if context.state == "disabled" else [
                {"evaluator_id": "local_pii", "detector_format_version": "pii-ascii-v1", "elapsed_ms": elapsed}
            ],
            "audit_id": str(uuid4()),
            "findings": sorted(findings, key=lambda item: (item["segment_id"], item["check_id"], item["start"], item["end"])),
            "transformed_content": transformed,
            "evaluation_ms": max(0.0, (time.perf_counter() - started) * 1000),
            "source": "runtime", "audit_status": "persisted",
            "client_config": strict_json(context.client_config_json) if request.stage == "input" else None,
        }
        try:
            self.write_audit(decision)
        except OSError:
            # This profile is explicitly fail-closed. Never fabricate durability
            # or weaken a prior block/redaction after an audit write failure.
            decision.update(
                action="block", allowed=False, transformed_content=None,
                audit_id=None, audit_status="failed", context_id=None,
            )
            decision["operational_codes"].append("audit_write_failed")
            self.contexts.pop(context_id, None)
        return decision
