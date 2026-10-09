"""Evaluation contract wire types.

Normative source: ADR-0005 (wire format), ADR-0004 (enforcement ladder),
ADR-0003 (reason-code taxonomy), INTEGRATION-ARCHITECTURE.md section 3.

Nothing in this module knows about a gateway, a vendor, or a deployment mode.
It is the one logical contract AGR-006 requires, expressed once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# --- Closed vocabularies (ADR-0005 section 7: strict on input, no coercion) ---

CONTRACT_VERSION = "v1"

STAGES: tuple[str, ...] = ("input", "output")
CONTENT_KINDS: tuple[str, ...] = ("text",)
ACTIONS: tuple[str, ...] = ("allow", "log_only", "redact", "block")
ENFORCEMENT_STATES: tuple[str, ...] = (
    "disabled",
    "observe",
    "warn",
    "redact",
    "block",
)

# ADR-0004: severity order, and the ceiling each ladder state imposes.
ACTION_SEVERITY: dict[str, int] = {"allow": 0, "log_only": 1, "redact": 2, "block": 3}
CEILING: dict[str, str] = {
    "disabled": "allow",
    "observe": "log_only",
    "warn": "log_only",
    "redact": "redact",
    "block": "block",
}

# ADR-0003: reason codes are `GR-0NN:<family>:<code>`. GR-000 is the reserved
# platform namespace for conditions that are not guardrail findings.
PLATFORM_CATEGORY = "GR-000"

# Detection family -> guardrail category (GUARDRAIL-CATALOG.md).
FAMILY_CATEGORY: dict[str, str] = {
    "prompt_security": "GR-001",
    "content_safety": "GR-002",
    "sensitive_data": "GR-003",
    "structured_output": "GR-007",
    "organization_rules": "GR-018",
}


def reason_code(category: str, family: str, code: str) -> str:
    """Build a canonical reason code (ADR-0003)."""
    return f"{category}:{family}:{code}"


def platform_code(code: str) -> str:
    return reason_code(PLATFORM_CATEGORY, "platform", code)


def clamp(action: str, enforcement_state: str) -> str:
    """Clamp an action to the binding's ceiling.

    ADR-0004: the state is a ceiling, never a selector. A policy calling for a
    less severe action than the ceiling permits is never escalated.
    """
    ceiling = CEILING[enforcement_state]
    if ACTION_SEVERITY[action] > ACTION_SEVERITY[ceiling]:
        return ceiling
    return action


# --- Request ------------------------------------------------------------------


@dataclass(frozen=True)
class Content:
    """Discriminated union tagged by `kind`. One member in Phase 1: text."""

    kind: str
    text: str

    def to_wire(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text}


@dataclass(frozen=True)
class EvaluationRequest:
    tenant_id: str
    workload_id: str
    stage: str
    content: Content
    correlation_id: str
    # Advisory only. The runtime resolves and authorizes the effective policy
    # server-side; a client cannot obtain a weaker policy by asserting one.
    policy_ref: dict[str, Any] | None = None


# --- Decision -----------------------------------------------------------------


@dataclass(frozen=True)
class EvaluatorMetadata:
    name: str
    version: str

    def to_wire(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}


@dataclass(frozen=True)
class Decision:
    action: str
    allowed: bool
    enforcement_state: str
    reason_codes: tuple[str, ...]
    guardrail_category: str | None
    policy_name: str
    policy_version: int
    evaluator_metadata: tuple[EvaluatorMetadata, ...]
    correlation_id: str
    audit_id: str
    evaluation_latency_ms: float
    transformed_content: Content | None = None
    # Not on the wire: which AGR-012 failure path produced this decision, if
    # any. Carried into the audit record so a substitute decision is always
    # distinguishable from a normal one.
    failure_path: str | None = field(default=None, compare=True)

    def to_wire(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "action": self.action,
            "allowed": self.allowed,
            "enforcement_state": self.enforcement_state,
            "reason_codes": list(self.reason_codes),
            "guardrail_category": self.guardrail_category,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "evaluator_metadata": [m.to_wire() for m in self.evaluator_metadata],
            "correlation_id": self.correlation_id,
            "audit_id": self.audit_id,
            "evaluation_latency_ms": self.evaluation_latency_ms,
        }
        # ADR-0005 section 5: present when and only when action is redact.
        if self.action == "redact":
            assert self.transformed_content is not None
            body["transformed_content"] = self.transformed_content.to_wire()
        return body

    def to_json(self) -> str:
        return json.dumps(self.to_wire(), sort_keys=True)


def decision_from_wire(body: dict[str, Any]) -> Decision:
    """Parse a Decision as a client would. Used by the connector and tests."""
    transformed = body.get("transformed_content")
    return Decision(
        action=body["action"],
        allowed=body["allowed"],
        enforcement_state=body["enforcement_state"],
        reason_codes=tuple(body["reason_codes"]),
        guardrail_category=body.get("guardrail_category"),
        policy_name=body["policy_name"],
        policy_version=body["policy_version"],
        evaluator_metadata=tuple(
            EvaluatorMetadata(m["name"], m["version"])
            for m in body["evaluator_metadata"]
        ),
        correlation_id=body["correlation_id"],
        audit_id=body["audit_id"],
        evaluation_latency_ms=body["evaluation_latency_ms"],
        transformed_content=(
            Content(transformed["kind"], transformed["text"]) if transformed else None
        ),
    )


# Local-proof-v1 uses the strict immutable batch contract. Legacy single-text
# names remain above solely for the staged prototype migration.
from .local_contract import (  # noqa: E402,F401
    ClientConfig,
    FailureBehavior,
    TextBatch,
    TextSegment,
    Decision as ProofDecision,
    Evaluation as ProofEvaluation,
    Policy as ProofPolicy,
)
