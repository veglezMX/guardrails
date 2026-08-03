"""RT-6 — failure-behavior handler.

The only component permitted to select a failure path, and only from the
behaviors the workload's policy declared. There is no branch in here that
invents a behavior the policy did not state.

Two behaviors are not substitute actions and are handled where they occur:
`degrade_and_log` continues the evaluation with the remaining detectors, and
`use_cached_policy` is a resolver concern.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contract import platform_code

# A behavior that terminates the evaluation maps to exactly one action.
# `safe_fallback` and `require_manual_approval` both stop the content: the
# difference is what the *client* does next, which the reason code carries.
_TERMINAL_ACTIONS: dict[str, str] = {
    "allow_and_log": "allow",
    "block": "block",
    "safe_fallback": "block",
    "require_manual_approval": "block",
}

CONTINUING_BEHAVIORS: frozenset[str] = frozenset(
    {"degrade_and_log", "use_cached_policy"}
)


@dataclass(frozen=True)
class Substitute:
    """A decision produced by a failure path rather than by evaluation."""

    action: str
    reason_code: str
    failure_path: str
    behavior: str


class FailureHandler:
    def substitute(self, mode: str, behavior: str) -> Substitute:
        action = _TERMINAL_ACTIONS.get(behavior)
        if action is None:
            raise ValueError(
                f"failure mode '{mode}' resolved to behavior '{behavior}', which "
                "is not a terminal behavior; the caller must handle it in place"
            )
        return Substitute(
            action=action,
            reason_code=platform_code(mode),
            failure_path=mode,
            behavior=behavior,
        )

    def annotation(self, mode: str) -> str:
        """The reason code for a failure that did not terminate the evaluation."""
        return platform_code(mode)
