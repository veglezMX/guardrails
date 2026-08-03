"""Policy document schema and validation (ADR-0009).

Two rules in here are load-bearing and deliberately unforgiving:

* Every failure-behavior key is required and no key has a default. A policy
  missing one fails validation and is never published, because a default would
  be undocumented fallback behavior from the author's point of view — the exact
  condition AGR-012 forbids.
* Evaluators are selected by detection family and capability, never by vendor.
  A vendor name anywhere in an evaluator selection is a schema violation, so
  AGR-005 portability is checkable rather than reviewable.

A policy carries no tenant, no workload, and no enforcement mode. Those live on
the binding, which is what lets one policy version serve many workloads sitting
at different points on the enforcement ladder.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import ACTIONS, CONTRACT_VERSION, FAMILY_CATEGORY, STAGES
from .errors import PolicyValidationError

# AGR-012 enumerates six failure modes. Every one is a required key.
FAILURE_MODES: tuple[str, ...] = (
    "evaluation_timeout",
    "detector_unavailable",
    "policy_unresolved",
    "control_plane_unavailable",
    "audit_delivery_failed",
    "transform_failed",
)

# The behaviors INTEGRATION-ARCHITECTURE.md section 4.6 names, per mode. A
# behavior outside a mode's set is rejected rather than silently accepted.
FAILURE_BEHAVIORS: dict[str, tuple[str, ...]] = {
    "evaluation_timeout": ("allow_and_log", "block", "use_cached_policy"),
    "detector_unavailable": ("degrade_and_log", "block"),
    "policy_unresolved": ("block", "require_manual_approval"),
    "control_plane_unavailable": ("use_cached_policy", "block"),
    "audit_delivery_failed": ("allow_and_log", "block"),
    "transform_failed": ("safe_fallback", "block"),
}

# Names that make a policy non-portable. Detected at validation time, which is
# what turns CON-004 from a review finding into a build failure.
VENDOR_TOKENS: tuple[str, ...] = (
    "bedrock",
    "aws",
    "amazon",
    "azure",
    "openai",
    "anthropic",
    "gcp",
    "google",
    "vertex",
    "litellm",
    "presidio",
    "comprehend",
)


@dataclass(frozen=True)
class EvaluatorSelection:
    """What to detect, at which stages, and what a match produces."""

    family: str
    capability: str
    stages: tuple[str, ...]
    threshold: float
    action: str
    params: dict[str, Any]

    @property
    def category(self) -> str:
        return FAMILY_CATEGORY[self.family]


@dataclass(frozen=True)
class Policy:
    name: str
    version: int
    stages: tuple[str, ...]
    evaluators: tuple[EvaluatorSelection, ...]
    failure_behavior: dict[str, str]
    retain_content: bool
    required_capabilities: tuple[str, ...]
    contract_version: str

    def selections_for(self, stage: str) -> tuple[EvaluatorSelection, ...]:
        """Only the evaluators this policy configures for this stage run.

        Applying every configured evaluator unconditionally is the latency risk
        the risk register names; scoping by stage is the design intent.
        """
        return tuple(s for s in self.evaluators if stage in s.stages)


def _require(doc: dict[str, Any], key: str, where: str) -> Any:
    if key not in doc:
        raise PolicyValidationError(f"{where}: missing required key '{key}'")
    return doc[key]


def _reject_vendor(value: str, where: str) -> None:
    lowered = value.lower()
    for token in VENDOR_TOKENS:
        if token in lowered:
            raise PolicyValidationError(
                f"{where}: '{value}' names a vendor ('{token}'). Evaluators are "
                "selected by capability, never by implementation (AGR-005, CON-004)."
            )


def parse_policy(doc: Any) -> Policy:
    """Validate a policy document, or refuse it. Never returns a partial policy."""
    if not isinstance(doc, dict):
        raise PolicyValidationError("policy: document must be an object")

    name = _require(doc, "name", "policy")
    version = _require(doc, "version", "policy")
    if not isinstance(name, str) or not name:
        raise PolicyValidationError("policy: 'name' must be a non-empty string")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PolicyValidationError("policy: 'version' must be a positive integer")

    raw_stages = _require(doc, "stages", f"policy '{name}'")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise PolicyValidationError(f"policy '{name}': 'stages' must be a non-empty list")
    for stage in raw_stages:
        if stage not in STAGES:
            raise PolicyValidationError(
                f"policy '{name}': stage '{stage}' is outside the reserved "
                f"stage vocabulary {list(STAGES)}"
            )

    raw_evaluators = _require(doc, "evaluators", f"policy '{name}'")
    if not isinstance(raw_evaluators, list) or not raw_evaluators:
        raise PolicyValidationError(
            f"policy '{name}': 'evaluators' must list at least one selection"
        )
    evaluators = tuple(
        _parse_selection(sel, name, tuple(raw_stages), i)
        for i, sel in enumerate(raw_evaluators)
    )

    failure_behavior = _parse_failure_behavior(
        _require(doc, "failure_behavior", f"policy '{name}'"), name
    )

    # AGR-014: retention is off unless the policy opts in. Unlike failure
    # behavior this section may default, because here the default *is* the
    # agreement rather than an invention on the author's behalf.
    audit = doc.get("audit_behavior", {})
    if not isinstance(audit, dict):
        raise PolicyValidationError(f"policy '{name}': 'audit_behavior' must be an object")
    retain_content = bool(audit.get("retain_content", False))

    compat = _require(doc, "compatibility", f"policy '{name}'")
    if not isinstance(compat, dict):
        raise PolicyValidationError(f"policy '{name}': 'compatibility' must be an object")
    contract_version = compat.get("contract_version", CONTRACT_VERSION)
    if contract_version != CONTRACT_VERSION:
        raise PolicyValidationError(
            f"policy '{name}': requires contract version '{contract_version}', "
            f"this runtime serves '{CONTRACT_VERSION}'"
        )
    required_capabilities = tuple(compat.get("required_capabilities", []))
    for cap in required_capabilities:
        _reject_vendor(str(cap), f"policy '{name}' compatibility.required_capabilities")

    return Policy(
        name=name,
        version=version,
        stages=tuple(raw_stages),
        evaluators=evaluators,
        failure_behavior=failure_behavior,
        retain_content=retain_content,
        required_capabilities=required_capabilities,
        contract_version=contract_version,
    )


def _parse_selection(
    sel: Any, policy_name: str, policy_stages: tuple[str, ...], index: int
) -> EvaluatorSelection:
    where = f"policy '{policy_name}' evaluators[{index}]"
    if not isinstance(sel, dict):
        raise PolicyValidationError(f"{where}: must be an object")

    family = _require(sel, "family", where)
    capability = _require(sel, "capability", where)
    _reject_vendor(str(family), f"{where}.family")
    _reject_vendor(str(capability), f"{where}.capability")
    if family not in FAMILY_CATEGORY:
        raise PolicyValidationError(
            f"{where}: unknown detection family '{family}'. Known families: "
            f"{sorted(FAMILY_CATEGORY)}"
        )

    stages = tuple(sel.get("stages", policy_stages))
    for stage in stages:
        if stage not in policy_stages:
            raise PolicyValidationError(
                f"{where}: stage '{stage}' is not among the policy's stages "
                f"{list(policy_stages)}"
            )

    action = _require(sel, "action", where)
    if action not in ACTIONS:
        raise PolicyValidationError(
            f"{where}: action '{action}' is outside the active action vocabulary "
            f"{list(ACTIONS)}. Reserved-but-inactive actions are not selectable."
        )

    threshold = sel.get("threshold", 0.5)
    if not isinstance(threshold, (int, float)) or not 0.0 <= float(threshold) <= 1.0:
        raise PolicyValidationError(f"{where}: 'threshold' must be between 0.0 and 1.0")

    params = sel.get("params", {})
    if not isinstance(params, dict):
        raise PolicyValidationError(f"{where}: 'params' must be an object")
    for key, value in params.items():
        if isinstance(value, str):
            _reject_vendor(value, f"{where}.params.{key}")

    return EvaluatorSelection(
        family=family,
        capability=capability,
        stages=stages,
        threshold=float(threshold),
        action=action,
        params=params,
    )


def _parse_failure_behavior(raw: Any, policy_name: str) -> dict[str, str]:
    where = f"policy '{policy_name}' failure_behavior"
    if not isinstance(raw, dict):
        raise PolicyValidationError(f"{where}: must be an object")

    behavior: dict[str, str] = {}
    for mode in FAILURE_MODES:
        if mode not in raw:
            raise PolicyValidationError(
                f"{where}: missing required key '{mode}'. Every failure mode must "
                "be stated; there is no default (AGR-012)."
            )
        value = raw[mode]
        allowed = FAILURE_BEHAVIORS[mode]
        if value not in allowed:
            raise PolicyValidationError(
                f"{where}.{mode}: '{value}' is not a documented behavior for this "
                f"mode. Allowed: {list(allowed)}"
            )
        behavior[mode] = value

    unknown = set(raw) - set(FAILURE_MODES)
    if unknown:
        raise PolicyValidationError(f"{where}: unknown failure mode(s) {sorted(unknown)}")
    return behavior


def load_policy_file(path: Path) -> Policy:
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise PolicyValidationError(f"{path}: not valid JSON — {exc}") from exc
    return parse_policy(doc)
