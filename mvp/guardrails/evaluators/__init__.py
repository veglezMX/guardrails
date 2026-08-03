"""Evaluator registry — the runtime capability manifest (ADR-0013).

Which implementation serves a given detection family and capability is
*deployment configuration*, not policy content. That separation is what lets one
unchanged policy document resolve to the in-process PII evaluator in one
deployment and to the Bedrock adapter in another.
"""

from __future__ import annotations

from typing import Any

from ..errors import EvaluatorUnavailable
from .base import Evaluator, Finding
from .bedrock import BedrockGuardrailsAdapter, FakeBedrockClient
from .deterministic import DeterministicRuleEvaluator
from .pii import PiiEvaluator
from .schema_validator import SchemaValidator

__all__ = [
    "BedrockGuardrailsAdapter",
    "DeterministicRuleEvaluator",
    "Evaluator",
    "EvaluatorRegistry",
    "FakeBedrockClient",
    "Finding",
    "PiiEvaluator",
    "SchemaValidator",
    "default_registry",
]


class EvaluatorRegistry:
    """Maps `family/capability` to the implementation this deployment serves."""

    def __init__(self, manifest: dict[str, Evaluator]) -> None:
        self._manifest = dict(manifest)

    @staticmethod
    def key(family: str, capability: str) -> str:
        return f"{family}/{capability}"

    def resolve(self, family: str, capability: str) -> Evaluator:
        key = self.key(family, capability)
        evaluator = self._manifest.get(key)
        if evaluator is None:
            # An unsatisfiable binding: the policy asked for a capability this
            # deployment does not serve. Treated as detector unavailability so
            # the workload's declared failure behavior decides, rather than the
            # request silently passing unevaluated.
            raise EvaluatorUnavailable("registry", f"no evaluator serves '{key}'")
        return evaluator

    def capabilities(self) -> dict[str, dict[str, str]]:
        """The body of `GET /v1/capabilities`."""
        return {
            key: {"evaluator": ev.name, "version": ev.version}
            for key, ev in sorted(self._manifest.items())
        }

    def serves(self, family: str, capability: str) -> bool:
        return self.key(family, capability) in self._manifest


def default_registry() -> EvaluatorRegistry:
    """The hosted-deployment manifest.

    Sensitive-data capabilities are served in process here. `bedrock_registry`
    below serves the same capabilities through the managed engine instead; the
    two must produce identical decisions for identical content.
    """
    pii = PiiEvaluator()
    rules = DeterministicRuleEvaluator()
    schema = SchemaValidator()
    manifest: dict[str, Evaluator] = {
        "sensitive_data/email": pii,
        "sensitive_data/phone": pii,
        "sensitive_data/credit_card": pii,
        "sensitive_data/national_id": pii,
        "organization_rules/blocked_terms": rules,
        "prompt_security/injection": rules,
        "structured_output/json_schema": schema,
    }
    return EvaluatorRegistry(manifest)


def bedrock_registry(client: Any | None = None) -> EvaluatorRegistry:
    """A deployment that serves the same capabilities through Bedrock."""
    adapter = BedrockGuardrailsAdapter(client or FakeBedrockClient())
    rules = DeterministicRuleEvaluator()
    schema = SchemaValidator()
    manifest: dict[str, Evaluator] = {
        "sensitive_data/email": adapter,
        "sensitive_data/phone": adapter,
        "sensitive_data/credit_card": adapter,
        "sensitive_data/national_id": adapter,
        "prompt_security/injection": adapter,
        "organization_rules/blocked_terms": rules,
        "structured_output/json_schema": schema,
    }
    return EvaluatorRegistry(manifest)
