"""Guardrail entry points — the enforcement half of the contract.

`core` is gateway-independent; `litellm_hooks` is the first gateway binding over
it. A second gateway is a second binding, not a second contract.
"""

from .core import (
    ConnectorConfig,
    EvaluationTransportError,
    GuardedInvocation,
    GuardedResult,
    HttpEvaluationClient,
)

__all__ = [
    "ConnectorConfig",
    "EvaluationTransportError",
    "GuardedInvocation",
    "GuardedResult",
    "HttpEvaluationClient",
]
