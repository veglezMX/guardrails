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


# The versioned proof adapter is deliberately separate from the legacy patterns.
# Scan maximal tokens once, then validate bounded candidates: never salvage a
# valid suffix from a malformed/overlong mailbox or telephone number.
_EMAIL_TOKEN = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~@-]+", re.ASCII)
_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*", re.ASCII)
_LABEL = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', re.ASCII)
_PHONE_TOKEN = re.compile(r'[+0-9]+', re.ASCII)
_PHONE = re.compile(r'\+[1-9][0-9]{7,14}', re.ASCII)


class LocalPIIEvaluator:
    @staticmethod
    def capabilities():
        from ..local_contract import EvaluatorCapabilities
        return EvaluatorCapabilities()

    def evaluate(self, content, check):
        from ..local_contract import Finding as BatchFinding
        findings = []
        for segment in content.segments:
            text = segment.text
            if 'email' in check.pii_types:
                for match in _EMAIL_TOKEN.finditer(text):
                    token = match.group()
                    # One terminal sentence period is punctuation. Repeated
                    # periods remain a malformed token and are not shortened.
                    if token.endswith('.') and not token.endswith('..'):
                        token = token[:-1]
                    if len(token) > 254 or token.count('@') != 1:
                        continue
                    local, domain = token.split('@')
                    labels = domain.split('.')
                    if (not 1 <= len(local) <= 64 or not _LOCAL.fullmatch(local)
                            or len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels)
                            or not re.fullmatch(r'[A-Za-z]{2,63}', labels[-1], re.ASCII)):
                        continue
                    before = text[match.start() - 1] if match.start() else ''
                    after = text[match.end()] if match.end() < len(text) else ''
                    if any(c and (c.isalnum() or c == '_') for c in (before, after)):
                        continue
                    findings.append(BatchFinding(check_id=check.check_id, segment_id=segment.segment_id,
                                                 reason_code='GR-003:local_pii:email', start=match.start(), end=match.start() + len(token)))
            if 'phone' in check.pii_types:
                for match in _PHONE_TOKEN.finditer(text):
                    if not _PHONE.fullmatch(match.group()):
                        continue
                    before = text[match.start() - 1] if match.start() else ''
                    after = text[match.end()] if match.end() < len(text) else ''
                    if any(c and (c.isdigit() or c == '+') for c in (before, after)):
                        continue
                    findings.append(BatchFinding(check_id=check.check_id, segment_id=segment.segment_id,
                                                 reason_code='GR-003:local_pii:phone', start=match.start(), end=match.end()))
        return tuple(sorted(findings, key=lambda f: (f.segment_id, f.check_id, f.start, f.end)))
