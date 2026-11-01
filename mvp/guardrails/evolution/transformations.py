"""Pure text transformations shared by the runtime and protective connector."""

from __future__ import annotations

from .errors import EvolutionError
from .models import ContentPart, Finding

REDACTION_MARKER = "[REDACTED]"
CATEGORY_MARKERS = frozenset({
    "email", "international_phone", "payment_card", "ipv4",
    "private_key", "aws_access_key", "github_token", "term", "managed",
})


def transform_text(parts: tuple[ContentPart, ...], findings: tuple[Finding, ...], *,
                   category_markers: bool = False) -> tuple[ContentPart, ...]:
    """Validate all original codepoint spans, merge overlaps, then replace once."""
    if type(category_markers) is not bool or not isinstance(parts, tuple) or not isinstance(findings, tuple) or not 1 <= len(parts) <= 64 or not 1 <= len(findings) <= 10000:
        raise EvolutionError("unsafe_transformation")
    if any(not isinstance(part, ContentPart) or part.kind != "text" or part.tool is not None for part in parts):
        raise EvolutionError("unsafe_transformation")
    by_id = {part.part_id: part for part in parts}
    if len(by_id) != len(parts):
        raise EvolutionError("unsafe_transformation")
    spans = {part.part_id: [] for part in parts}
    for finding in findings:
        if not isinstance(finding, Finding) or finding.part_id not in by_id or finding.json_pointer is not None:
            raise EvolutionError("unsafe_transformation")
        start, end = finding.start, finding.end
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(by_id[finding.part_id].text):
            raise EvolutionError("unsafe_transformation")
        if category_markers and finding.category not in CATEGORY_MARKERS:
            raise EvolutionError("unsafe_transformation")
        spans[finding.part_id].append((start, end, finding.category))
    output = []
    for part in parts:
        merged = []
        for start, end, category in sorted(set(spans[part.part_id])):
            if merged and start < merged[-1][1]:
                old_start, old_end, old_category = merged[-1]
                merged[-1] = (old_start, max(old_end, end),
                              category if category == old_category else "sensitive")
            else:
                merged.append((start, end, category))
        if not merged:
            output.append(part)
            continue
        pieces, offset = [], 0
        for start, end, category in merged:
            marker = "[REDACTED:" + category + "]" if category_markers else REDACTION_MARKER
            pieces.extend((part.text[offset:start], marker))
            offset = end
        pieces.append(part.text[offset:])
        output.append(part.model_copy(update={"text": "".join(pieces)}))
    if sum(len(part.text.encode("utf-8")) for part in output) > 65536:
        raise EvolutionError("unsafe_transformation")
    return tuple(output)


def validate_transformed_parts(parts: tuple[ContentPart, ...], findings: tuple[Finding, ...], transformed_parts: tuple[ContentPart, ...]) -> None:
    """Accept only the exact deterministic transform, including all metadata."""
    if not isinstance(transformed_parts, tuple) or any(not isinstance(part, ContentPart) for part in transformed_parts):
        raise EvolutionError("unsafe_transformation")
    # The compatibility profile retains the original marker. Both accepted
    # encodings must replace every original span and preserve all other bytes.
    if (transformed_parts != transform_text(parts, findings)
            and transformed_parts != transform_text(parts, findings, category_markers=True)):
        raise EvolutionError("unsafe_transformation")


class TextTransformation:
    def transform(self, parts, findings):
        return transform_text(parts, findings)
