"""In-process sensitive-data evaluator.

Basic sensitive-data handling, which is what the MVP scopes. This is not a DLP
platform and must not be described as one.
"""

from __future__ import annotations

import re

from ..policy import EvaluatorSelection
from .base import Finding

PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
    "phone": re.compile(r"\+?\d[\d\s().-]{7,}\d"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "national_id": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}


class PiiEvaluator:
    name = "pii-evaluator"
    version = "2026-08-01"

    def evaluate(self, text: str, selection: EvaluatorSelection) -> Finding:
        pattern = PATTERNS.get(selection.capability)
        spans = (
            tuple((m.start(), m.end()) for m in pattern.finditer(text))
            if pattern
            else ()
        )
        return Finding(
            family=selection.family,
            capability=selection.capability,
            category=selection.category,
            matched=bool(spans),
            # A regex match is exact for the pattern it encodes; confidence is
            # reported so the policy's threshold has something to compare.
            confidence=1.0 if spans else 0.0,
            evaluator_name=self.name,
            evaluator_version=self.version,
            spans=spans,
        )
