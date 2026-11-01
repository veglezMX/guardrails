"""Safe application failures shared by the v2 service adapters."""

STATUS = {
    "invalid_json": 400,
    "invalid_cursor": 400,
    "unsupported_surface": 400,
    "authentication_failed": 401,
    "session_expired": 401,
    "forbidden": 403,
    "self_approval": 403,
    "residency_denied": 403,
    "destination_denied": 403,
    "provider_denied": 403,
    "tool_denied": 403,
    "resource_not_found": 404,
    "revision_conflict": 409,
    "idempotency_conflict": 409,
    "approval_required": 409,
    "policy_revoked": 409,
    "floor_conflict": 409,
    "capability_missing": 409,
    "payload_too_large": 413,
    "validation_failed": 422,
    "composition_conflict": 422,
    "inspection_incomplete": 422,
    "precondition_required": 428,
    "quota_exceeded": 429,
    "queue_full": 429,
    "policy_unresolved": 503,
    "snapshot_stale": 503,
    "evaluator_unavailable": 503,
    "audit_failed": 503,
    "context_invalid": 503,
    "unsafe_transformation": 503,
    "evaluation_timeout": 504,
    "upstream_timeout": 504,
}


class EvolutionError(Exception):
    """Only finite codes and static safe messages cross the wire."""

    def __init__(self, code: str, message: str | None = None):
        if code not in STATUS:
            raise ValueError("unknown error code")
        self.code = code
        self.status = STATUS[code]
        # Never use exception/input strings in a public error response.
        self.message = code.replace("_", " ").capitalize()
        super().__init__(self.message)

    def envelope(self, correlation_id: str) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "correlation_id": correlation_id,
                "retryable": self.status in (429, 503),
                "details": {},
            }
        }
