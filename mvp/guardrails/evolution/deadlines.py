"""Request-scoped absolute budget shared by transport and transactional ports."""

from contextvars import ContextVar
import math
import time

from .errors import EvolutionError

current_deadline: ContextVar[float | None] = ContextVar(
    "evolution_deadline", default=None
)


def remaining_ms() -> int | None:
    deadline = current_deadline.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EvolutionError("upstream_timeout")
    return max(1, math.ceil(remaining * 1000))
