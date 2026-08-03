"""Shared fixtures. Keeps every test speaking the contract, not the internals."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from guardrails.audit import AuditProducer  # noqa: E402
from guardrails.binding import Binding, WorkloadIdentity  # noqa: E402
from guardrails.app import GuardrailsRuntime, RuntimeConfig  # noqa: E402
from guardrails.policy import parse_policy  # noqa: E402
from guardrails.store import PolicyCache  # noqa: E402

CREDENTIAL = "test-credential"
TENANT = "acme"
WORKLOAD = "support-summarizer"

ALL_FAILURE_KEYS: dict[str, str] = {
    "evaluation_timeout": "allow_and_log",
    "detector_unavailable": "degrade_and_log",
    "policy_unresolved": "block",
    "control_plane_unavailable": "use_cached_policy",
    "audit_delivery_failed": "allow_and_log",
    "transform_failed": "safe_fallback",
}

BASE_POLICY: dict[str, Any] = {
    "name": "test-policy",
    "version": 1,
    "stages": ["input", "output"],
    "evaluators": [
        {
            "family": "sensitive_data",
            "capability": "email",
            "stages": ["input", "output"],
            "threshold": 0.5,
            "action": "redact",
        },
        {
            "family": "sensitive_data",
            "capability": "credit_card",
            "stages": ["input", "output"],
            "threshold": 0.5,
            "action": "block",
        },
    ],
    "failure_behavior": dict(ALL_FAILURE_KEYS),
    "audit_behavior": {"retain_content": False},
    "compatibility": {"contract_version": "v1"},
}


def policy_doc(**overrides: Any) -> dict[str, Any]:
    doc = copy.deepcopy(BASE_POLICY)
    doc.update(copy.deepcopy(overrides))
    return doc


def make_policy(**overrides: Any):
    return parse_policy(policy_doc(**overrides))


def make_binding(state: str = "block", **overrides: Any) -> Binding:
    fields: dict[str, Any] = {
        "tenant_id": TENANT,
        "workload_id": WORKLOAD,
        "policy_name": "test-policy",
        "policy_version": 1,
        "enforcement_state": state,
    }
    fields.update(overrides)
    return Binding(**fields)


def make_runtime(
    *,
    state: str = "block",
    policy_overrides: dict[str, Any] | None = None,
    registry: Any = None,
    audit: AuditProducer | None = None,
) -> GuardrailsRuntime:
    policy = make_policy(**(policy_overrides or {}))
    binding = make_binding(state)
    cache = PolicyCache.empty()
    cache.put_policy(policy)
    cache.put_binding(binding)
    config = RuntimeConfig(
        unresolved_policy_behavior="block",
        identities={
            CREDENTIAL: WorkloadIdentity(CREDENTIAL, TENANT, WORKLOAD),
        },
    )
    return GuardrailsRuntime(
        cache,
        config,
        registry=registry,
        audit=audit or AuditProducer(path=None),
    )


def evaluate_body(text: str, stage: str = "input", **overrides: Any) -> bytes:
    import json

    body: dict[str, Any] = {
        "tenant_id": TENANT,
        "workload_id": WORKLOAD,
        "stage": stage,
        "content": {"kind": "text", "text": text},
        "correlation_id": "corr-1",
    }
    body.update(overrides)
    return json.dumps(body).encode("utf-8")


def auth() -> str:
    return f"Bearer {CREDENTIAL}"
