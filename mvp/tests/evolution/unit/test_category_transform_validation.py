"""The connector accepts only complete deterministic category masking."""

import pytest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import ContentPart, Finding
from guardrails.evolution.transformations import (
    transform_text,
    validate_transformed_parts,
)

PARTS = (ContentPart(part_id="p", role="user", kind="text", text="a@example.test secret"),)


def finding(start=0, end=14, category="email", **changes):
    return Finding(rule_id="rule", part_id="p", category=category, start=start, end=end,
                   reason_code="pii_detected", evaluator_revision="pii-v2", **changes)


def test_category_mask_is_accepted_and_compatibility_mask_retained():
    findings = (finding(), finding(15, 21, "term"))
    masked = transform_text(PARTS, findings, category_markers=True)
    assert masked[0].text == "[REDACTED:email] [REDACTED:term]"
    validate_transformed_parts(PARTS, findings, masked)
    legacy = transform_text(PARTS, findings)
    assert legacy[0].text == "[REDACTED] [REDACTED]"
    validate_transformed_parts(PARTS, findings, legacy)


def test_overlap_merges_different_categories_without_partial_exposure():
    findings = (finding(0, 14), finding(2, 17, "term"), finding(2, 17, "term"))
    masked = transform_text(PARTS, findings, category_markers=True)
    assert masked[0].text == "[REDACTED:sensitive]cret"
    validate_transformed_parts(PARTS, findings, masked)


@pytest.mark.parametrize("text", [
    "[REDACTED:email] secret injected",
    "[REDACTED:term] secret",
    "a@example.test [REDACTED:email]",
    "[REDACTED:email]",
])
def test_category_markers_do_not_authorize_unrelated_or_incomplete_changes(text):
    changed = (PARTS[0].model_copy(update={"text": text}),)
    with pytest.raises(EvolutionError) as error:
        validate_transformed_parts(PARTS, (finding(),), changed)
    assert error.value.code == "unsafe_transformation"


@pytest.mark.parametrize("category", ["topic", "injection", "json_schema", "content_allowlist", "arbitrary"])
def test_untransformable_or_unknown_categories_cannot_create_category_markers(category):
    with pytest.raises(EvolutionError):
        transform_text(PARTS, (finding(category=category),), category_markers=True)


def test_category_mask_cannot_change_part_metadata():
    findings = (finding(),)
    masked = transform_text(PARTS, findings, category_markers=True)
    with pytest.raises(EvolutionError):
        validate_transformed_parts(PARTS, findings, (masked[0].model_copy(update={"role": "system"}),))
