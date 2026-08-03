"""Bedrock Guardrails adapter — stubbed client, real normalization.

The adapter is the only place a Bedrock-native shape is permitted to exist. It
is here to prove the seam holds: the same policy document, resolved against a
deployment that serves `sensitive_data` with this adapter instead of the
in-process PII evaluator, must produce an identical decision apart from
`evaluator_metadata`.

`FakeBedrockClient` stands in for the managed service so the seam is exercised
without AWS credentials. Swapping it for a real client changes this file and
nothing else — no policy, no connector, no decision shape.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from ..errors import EvaluatorTimeout, EvaluatorUnavailable
from ..policy import EvaluatorSelection
from .base import Finding

# Bedrock's native vocabulary, mapped to the platform's capability vocabulary.
# This table is the whole reason CON-004 holds: no native code escapes it.
_NATIVE_TO_CAPABILITY: dict[str, str] = {
    "EMAIL": "email",
    "PHONE": "phone",
    "CREDIT_DEBIT_CARD_NUMBER": "credit_card",
    "US_SOCIAL_SECURITY_NUMBER": "national_id",
    "PROMPT_ATTACK": "injection",
}
_CAPABILITY_TO_NATIVE: dict[str, str] = {v: k for k, v in _NATIVE_TO_CAPABILITY.items()}


class BedrockClient(Protocol):
    def apply_guardrail(
        self, *, guardrail_id: str, source: str, text: str
    ) -> dict[str, Any]: ...


class FakeBedrockClient:
    """Emits Bedrock-shaped responses. Detection parity with the PII evaluator.

    `fail_with` lets a test drive the detector-outage path without patching the
    adapter itself.
    """

    _PATTERNS: dict[str, re.Pattern[str]] = {
        "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
        "PHONE": re.compile(r"\+?\d[\d\s().-]{7,}\d"),
        "CREDIT_DEBIT_CARD_NUMBER": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        "US_SOCIAL_SECURITY_NUMBER": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "PROMPT_ATTACK": re.compile(
            r"ignore (?:all )?previous instructions|disregard your system prompt",
            re.IGNORECASE,
        ),
    }

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.calls = 0

    def apply_guardrail(
        self, *, guardrail_id: str, source: str, text: str
    ) -> dict[str, Any]:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        assessments: list[dict[str, Any]] = []
        for native, pattern in self._PATTERNS.items():
            matches = [
                {"start": m.start(), "end": m.end(), "score": 0.99}
                for m in pattern.finditer(text)
            ]
            if matches:
                assessments.append({"type": native, "matches": matches})
        return {
            "guardrailId": guardrail_id,
            "source": source,
            "action": "GUARDRAIL_INTERVENED" if assessments else "NONE",
            "assessments": assessments,
        }


class BedrockGuardrailsAdapter:
    name = "bedrock-guardrails"
    version = "2026-05-01"

    def __init__(self, client: BedrockClient, guardrail_id: str = "gr-mvp") -> None:
        self._client = client
        self._guardrail_id = guardrail_id

    def evaluate(self, text: str, selection: EvaluatorSelection) -> Finding:
        native_type = _CAPABILITY_TO_NATIVE.get(selection.capability)
        if native_type is None:
            # The deployment configured this adapter for a capability it cannot
            # serve. Unavailability, not a silent pass — a missed detection must
            # never look like a clean evaluation.
            raise EvaluatorUnavailable(
                self.name, f"capability '{selection.capability}' is not served"
            )

        try:
            native = self._client.apply_guardrail(
                guardrail_id=self._guardrail_id,
                source="INPUT",
                text=text,
            )
        except (EvaluatorTimeout, EvaluatorUnavailable):
            raise
        except Exception as exc:  # transport, throttling, credentials
            raise EvaluatorUnavailable(self.name, str(exc)) from exc

        return self._normalize(native, native_type, selection)

    def _normalize(
        self, native: dict[str, Any], native_type: str, selection: EvaluatorSelection
    ) -> Finding:
        """Translate a Bedrock response into the platform's finding shape.

        Past this method no Bedrock field, code, or score scale exists.
        """
        matches: list[dict[str, Any]] = []
        for assessment in native.get("assessments", []):
            if assessment.get("type") == native_type:
                matches.extend(assessment.get("matches", []))

        spans = tuple((int(m["start"]), int(m["end"])) for m in matches)
        confidence = max((float(m.get("score", 0.0)) for m in matches), default=0.0)
        return Finding(
            family=selection.family,
            capability=selection.capability,
            category=selection.category,
            matched=bool(matches),
            confidence=confidence,
            evaluator_name=self.name,
            evaluator_version=self.version,
            spans=spans,
        )
