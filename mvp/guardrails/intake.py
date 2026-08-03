"""RT-1 — request intake and validation, and RT-7 — correlation propagation.

Nothing reaches an evaluator until the caller is authenticated, the body
conforms to the contract, the content is inside the size ceiling, and a
correlation identifier exists. Closed vocabularies are rejected on input rather
than coerced: an unknown stage or content kind is a 422, never a best guess.
"""

from __future__ import annotations

import uuid
from typing import Any

from .binding import WorkloadIdentity
from .contract import CONTENT_KINDS, STAGES, Content, EvaluationRequest
from .errors import (
    ProblemError,
    content_too_large,
    forbidden,
    malformed,
    unauthenticated,
    unprocessable,
)

DEFAULT_MAX_CONTENT_BYTES = 1_048_576


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def authenticate(
    header: str | None, identities: dict[str, WorkloadIdentity]
) -> WorkloadIdentity:
    """Authenticate the caller against the identity CP-1 issued.

    The hosted deployment authenticates SigV4 against IAM; this MVP uses a
    bearer credential bound to exactly one (tenant, workload). Either way, the
    identifiers in the body are never trusted on their own.
    """
    if not header or not header.lower().startswith("bearer "):
        raise unauthenticated("A bearer workload credential is required.")
    credential = header.split(" ", 1)[1].strip()
    identity = identities.get(credential)
    if identity is None:
        raise unauthenticated("Unknown workload credential.")
    return identity


def parse_request(
    body: Any,
    identity: WorkloadIdentity,
    *,
    max_content_bytes: int = DEFAULT_MAX_CONTENT_BYTES,
) -> EvaluationRequest:
    if not isinstance(body, dict):
        raise malformed("Request body must be a JSON object.")

    for key in ("tenant_id", "workload_id", "stage", "content"):
        if key not in body:
            raise malformed(f"Missing required field '{key}'.")

    tenant_id = body["tenant_id"]
    workload_id = body["workload_id"]
    if (tenant_id, workload_id) != (identity.tenant_id, identity.workload_id):
        raise forbidden(
            "The credential is not issued for the tenant/workload in the request."
        )

    stage = body["stage"]
    if stage not in STAGES:
        raise unprocessable(f"Unknown stage '{stage}'.", "unknown_stage")

    content = body["content"]
    if not isinstance(content, dict) or "kind" not in content:
        raise malformed("Field 'content' must be an object carrying 'kind'.")
    kind = content["kind"]
    if kind not in CONTENT_KINDS:
        raise unprocessable(f"Unknown content kind '{kind}'.", "unknown_content_kind")
    text = content.get("text")
    if not isinstance(text, str):
        raise malformed("Field 'content.text' must be a string.")

    size = len(text.encode("utf-8"))
    if size > max_content_bytes:
        raise content_too_large(size, max_content_bytes)

    correlation_id = body.get("correlation_id") or new_correlation_id()
    if not isinstance(correlation_id, str):
        raise malformed("Field 'correlation_id' must be a string.")

    policy_ref = body.get("policy_ref")
    if policy_ref is not None and not isinstance(policy_ref, dict):
        raise malformed("Field 'policy_ref' must be an object.")

    return EvaluationRequest(
        tenant_id=tenant_id,
        workload_id=workload_id,
        stage=stage,
        content=Content(kind=kind, text=text),
        correlation_id=correlation_id,
        policy_ref=policy_ref,
    )


def correlation_id_of(body: Any) -> str | None:
    """Best-effort correlation id for a request that failed validation."""
    if isinstance(body, dict):
        value = body.get("correlation_id")
        if isinstance(value, str):
            return value
    return None


__all__ = [
    "DEFAULT_MAX_CONTENT_BYTES",
    "ProblemError",
    "authenticate",
    "correlation_id_of",
    "new_correlation_id",
    "parse_request",
]
