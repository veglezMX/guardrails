from itertools import permutations

import pytest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import ContentPart, Finding
from guardrails.evolution.transformations import (
    transform_text,
    validate_transformed_parts,
)


def finding(start=1, end=3, **updates):
    return Finding.model_validate({"rule_id": "pii", "category": "email", "part_id": "text", "reason_code": "pii_detected", "evaluator_revision": "pii-v2", "start": start, "end": end, **updates})


def parts():
    return (ContentPart(part_id="text", kind="text", role="user", text="🙂αβγδεζ", source_id="original"), ContentPart(part_id="system", kind="text", role="system", text="Untouched instructions"))


def test_transform_uses_original_codepoints_merges_overlap_and_preserves_metadata():
    original = parts()
    findings = (finding(1, 4), finding(3, 6), finding(1, 4))
    for order in permutations(findings):
        result = transform_text(original, order)
        assert result[0].text == "🙂[REDACTED]ζ"
        assert result[0].model_dump(exclude={"text"}) == original[0].model_dump(exclude={"text"})
        assert result[1] == original[1]
        validate_transformed_parts(original, order, result)
    assert original[0].text == "🙂αβγδεζ"


def test_transform_separate_and_adjacent_spans_do_not_consume_unmatched_characters():
    result = transform_text(parts(), (finding(1, 2), finding(2, 3), finding(5, 6)))
    assert result[0].text == "🙂[REDACTED][REDACTED]γδ[REDACTED]ζ"
    assert result[1].text == "Untouched instructions"


@pytest.mark.parametrize("invalid", [finding(0, 100), finding(part_id="unknown"), finding(json_pointer="/text"), finding().model_copy(update={"start": -1}), finding().model_copy(update={"start": True}), finding().model_copy(update={"end": None})])
def test_invalid_finding_rejects_complete_transform_without_partial_mutation(invalid):
    original = parts()
    with pytest.raises(EvolutionError) as denied:
        transform_text(original, (finding(), invalid))
    assert denied.value.code == "unsafe_transformation"
    assert original == parts()


def test_connector_validation_rejects_arbitrary_text_roles_metadata_and_part_reordering():
    original, findings = parts(), (finding(),)
    expected = transform_text(original, findings)
    for corrupted in ((expected[0].model_copy(update={"text": "Injected"}), expected[1]), (expected[0].model_copy(update={"role": "system"}), expected[1]), (expected[0].model_copy(update={"source_id": "changed"}), expected[1]), tuple(reversed(expected)), expected[:1]):
        with pytest.raises(EvolutionError) as denied:
            validate_transformed_parts(original, findings, corrupted)
        assert denied.value.code == "unsafe_transformation"
    assert expected[0].text == "🙂[REDACTED]γδεζ"


def test_transform_rejects_nontext_empty_findings_and_duplicate_identity():
    for content, findings in (((ContentPart(part_id="text", kind="json", role="user", value={}),), (finding(),)), (parts(), ()), ((parts()[0], parts()[0]), (finding(),))):
        with pytest.raises(EvolutionError) as denied:
            transform_text(content, findings)
        assert denied.value.code == "unsafe_transformation"
