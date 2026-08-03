"""Organization-authored deterministic rules.

The rule content comes from the policy's `params`, never from this module — the
evaluator supplies the mechanism, the policy supplies the meaning.
"""

from __future__ import annotations

import re

from ..policy import EvaluatorSelection
from .base import Finding


class DeterministicRuleEvaluator:
    name = "deterministic-rules"
    version = "2026-08-01"

    def evaluate(self, text: str, selection: EvaluatorSelection) -> Finding:
        spans: list[tuple[int, int]] = []

        for phrase in selection.params.get("phrases", []):
            for match in re.finditer(re.escape(str(phrase)), text, re.IGNORECASE):
                spans.append((match.start(), match.end()))

        for pattern in selection.params.get("patterns", []):
            for match in re.finditer(str(pattern), text, re.IGNORECASE):
                spans.append((match.start(), match.end()))

        spans.sort()
        return Finding(
            family=selection.family,
            capability=selection.capability,
            category=selection.category,
            matched=bool(spans),
            confidence=1.0 if spans else 0.0,
            evaluator_name=self.name,
            evaluator_version=self.version,
            spans=tuple(spans),
        )
