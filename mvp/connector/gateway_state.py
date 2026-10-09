"""Ephemeral state shared only by one ASGI request and its native hooks."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GatewayTurn:
    correlation_id: str
    context_id: str | None = None
    input_decision: dict[str, Any] | None = None
    output_decision: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    error_status: int = 503
    output_timeout_s: float = 1.5
    client_config: Any = None


current_turn: ContextVar[GatewayTurn | None] = ContextVar("local_proof_turn", default=None)
