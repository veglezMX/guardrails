"""Structured-output validation.

Validates that the evaluated content parses as JSON and carries the keys and
types the policy requires. A validation failure is a finding like any other —
what happens next is the policy's action, not this evaluator's decision.

A schema failure cannot be localized to a span, so a policy that pairs this
family with a `redact` action will meet the `transform_failed` failure mode
rather than a silent downgrade. That is the intended behavior.
"""

from __future__ import annotations

import json
from typing import Any

from ..policy import EvaluatorSelection
from .base import Finding

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


class SchemaValidator:
    name = "schema-validator"
    version = "2026-08-01"

    def evaluate(self, text: str, selection: EvaluatorSelection) -> Finding:
        violated = self._violates(text, selection.params)
        return Finding(
            family=selection.family,
            capability=selection.capability,
            category=selection.category,
            matched=violated,
            confidence=1.0 if violated else 0.0,
            evaluator_name=self.name,
            evaluator_version=self.version,
            spans=(),
        )

    def _violates(self, text: str, params: dict[str, Any]) -> bool:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return True

        if not isinstance(parsed, dict):
            return bool(params.get("require_object", True))

        for key in params.get("required_keys", []):
            if key not in parsed:
                return True

        for key, expected in params.get("types", {}).items():
            if key not in parsed:
                continue
            python_type = _JSON_TYPES.get(str(expected))
            if python_type is None:
                return True
            if isinstance(parsed[key], bool) and python_type is not bool:
                return True
            if not isinstance(parsed[key], python_type):
                return True

        return False
