"""RFC 9457 problem responses and internal signals.

ADR-0005 section 6: the runtime never returns 5xx to express an evaluation
outcome. A problem response means no evaluation happened, and the client's own
configured failure behavior applies.
"""

from __future__ import annotations

from typing import Any

from .contract import platform_code

PROBLEM_BASE = "https://guardrails.example.com/problems"


class ProblemError(Exception):
    """A condition the runtime declined to evaluate. Serialized as RFC 9457."""

    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str,
        reason_code: str,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.slug = slug
        self.title = title
        self.detail = detail
        self.reason_code = reason_code

    def to_wire(self, correlation_id: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"{PROBLEM_BASE}/{self.slug}",
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "reason_code": self.reason_code,
        }
        # Present on every problem response so a rejected request stays traceable.
        body["correlation_id"] = correlation_id
        return body


def malformed(detail: str) -> ProblemError:
    return ProblemError(
        400, "malformed-body", "Malformed request body", detail,
        platform_code("malformed_body"),
    )


def unauthenticated(detail: str) -> ProblemError:
    return ProblemError(
        401, "unauthenticated", "Authentication required", detail,
        platform_code("unauthenticated"),
    )


def forbidden(detail: str) -> ProblemError:
    return ProblemError(
        403, "forbidden", "Caller is not authorized for this workload", detail,
        platform_code("forbidden"),
    )


def content_too_large(size: int, ceiling: int) -> ProblemError:
    return ProblemError(
        413,
        "content-too-large",
        "Content exceeds the configured size ceiling",
        f"Content of {size} bytes exceeds the ceiling of {ceiling} bytes.",
        platform_code("content_too_large"),
    )


def unprocessable(detail: str, code: str) -> ProblemError:
    return ProblemError(
        422, "invalid-value", "Well-formed but invalid", detail, platform_code(code)
    )


class PolicyValidationError(Exception):
    """A policy document that must never be published (ADR-0009)."""


class EvaluatorUnavailable(Exception):
    """An evaluation engine could not respond. Routed to RT-6."""

    def __init__(self, evaluator: str, detail: str) -> None:
        super().__init__(f"{evaluator}: {detail}")
        self.evaluator = evaluator


class EvaluatorTimeout(EvaluatorUnavailable):
    """An evaluation engine exceeded its stage timeout (CON-016)."""


class AuditDeliveryError(Exception):
    """The audit record could not be delivered. Routed to RT-6."""
