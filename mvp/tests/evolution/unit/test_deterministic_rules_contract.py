"""Worker-boundary privacy, stage and transform adversarial contracts."""
import time
from unittest.mock import patch

import pytest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import ContentPart
from guardrails.evolution.rules import (
    RulesBinding,
    evaluate_content_lists,
    evaluate_rule,
    transform_rule_findings,
)
from test_deterministic_rules import (
    content_list,
    evaluate,
    policy,
    rule,
    structured,
    text,
)


@pytest.mark.parametrize("stage", ["input", "output", "retrieval", "tool_arguments", "tool_result"])
@pytest.mark.parametrize("kind", ["pii", "secret", "term", "topic", "json_schema"])
def test_pure_detectors_bind_each_explicit_stage(stage, kind):
    selected = rule(kind)
    listing = content_list(deny=("term:private", "topic:finance"))
    inputs = {"pii": "a@example.test", "secret": "AKIA0000000000000000", "term": "private", "topic": "invoice", "json_schema": "not-json"}
    found = evaluate(selected, text(inputs[kind]), stage=stage, lists=(listing,) if kind in {"term", "topic"} else ())
    assert found and all(item.rule_id == selected.rule_id and item.evaluator_revision == selected.evaluator_revision for item in found)


@pytest.mark.parametrize("value", ["invoice\u0338", "\u0301invoice", "source code\u0338", "repository_", "ébudget"])
def test_lexical_topic_does_not_match_inside_unicode_words(value):
    listing = content_list(deny=("topic:finance", "topic:software"))
    assert evaluate(rule("topic"), text(value), lists=(listing,)) == ()


def test_literal_term_has_no_regex_interpretation():
    listing = content_list(deny=("term:(a+)+$",))
    selected = rule("term")
    assert evaluate(selected, text("a" * 10000 + "!"), lists=(listing,)) == ()
    found = evaluate(selected, text("match (a+)+$ literally"), lists=(listing,))
    assert [(item.start, item.end) for item in found] == [(6, 12)]


def test_structured_root_string_has_empty_pointer_and_masks_safely():
    selected, part = rule(), structured("a@example.test")
    found = evaluate(selected, part)
    assert found[0].json_pointer == ""
    result = transform_rule_findings(policy(selected), "output", (part,), found, time.monotonic() + 2)
    assert result[0].kind == "json" and result[0].value == "[REDACTED:email]"


def test_schema_text_masks_only_string_content_and_preserves_metadata():
    pii, schema = rule(), rule("json_schema")
    original = ContentPart(part_id="p", kind="text", role="assistant", source_id="source", text='{"mail":"a@example.test","count":3,"flag":true}')
    compiled = policy(pii, schema)
    found = evaluate_rule(RulesBinding(compiled, "output"), pii, (original,), time.monotonic() + 2)
    result = transform_rule_findings(compiled, "output", (original,), found, time.monotonic() + 2)[0]
    assert result.text == '{"mail":"[REDACTED:email]","count":3,"flag":true}'
    assert result.role == original.role and result.source_id == original.source_id and result.part_id == original.part_id


def test_schema_text_numeric_field_never_becomes_a_string():
    pii, schema = rule(), rule("json_schema")
    original = text('{"card":4111111111111111}')
    compiled = policy(pii, schema)
    found = evaluate_rule(RulesBinding(compiled, "output"), pii, (original,), time.monotonic() + 2)
    assert found
    with pytest.raises(EvolutionError, match="Unsafe transformation"):
        transform_rule_findings(compiled, "output", (original,), found, time.monotonic() + 2)


def test_content_allow_failure_cannot_be_redacted_or_expose_literal():
    listing = content_list(allow=("term:PRIVATE-POLICY-LITERAL",), allow_enabled=True)
    compiled = policy(lists=(listing,), content_ids=(listing.list_id,))
    parts = (text("unrelated"),)
    found = evaluate_content_lists(RulesBinding(compiled, "output"), parts, time.monotonic() + 2)
    assert found[0].start is None and found[0].reason_code == "content_not_allowed"
    assert "PRIVATE-POLICY-LITERAL" not in found[0].model_dump_json()
    with pytest.raises(EvolutionError, match="Unsafe transformation"):
        transform_rule_findings(compiled, "output", parts, found, time.monotonic() + 2)


def test_deadline_is_checked_during_normalization_and_no_partial_findings_escape():
    selected, listing = rule("term"), content_list(deny=("term:private",))
    compiled = policy(selected, lists=(listing,))
    bound = RulesBinding(compiled, "input")
    with patch("guardrails.evolution.rules.time.monotonic", side_effect=[0, 0, 0, 2]), pytest.raises(EvolutionError, match="Evaluation timeout"):
        evaluate_rule(bound, selected, (text("private " * 200),), 1)


def test_deadline_is_not_reset_per_part_or_policy_constraint():
    selected = rule()
    bound = RulesBinding(policy(selected), "input")
    with patch("guardrails.evolution.rules.time.monotonic", side_effect=[0, 0, 0, 2]), pytest.raises(EvolutionError, match="Evaluation timeout"):
        evaluate_rule(bound, selected, (text("a@b.test", "one"), text("c@d.test", "two")), 1)


def test_binding_and_evaluation_return_no_private_content_on_errors():
    selected = rule()
    compiled = policy(selected)
    with pytest.raises(EvolutionError) as caught:
        evaluate_rule(RulesBinding(compiled, "input"), selected.model_copy(update={"config": {"private": "CONTENT-CANARY"}}), (text("CONTENT-CANARY"),), time.monotonic() + 2)
    assert "CONTENT-CANARY" not in str(caught.value)


@pytest.mark.parametrize("stage", [None, [], {}, True, "unknown"])
def test_untrusted_stage_is_a_typed_refusal(stage):
    with pytest.raises(EvolutionError, match="Validation failed"):
        RulesBinding(policy(rule()), stage)


def test_forged_rule_stage_and_payload_cannot_emit_content_warning(recwarn):
    selected = rule()
    binding = RulesBinding(policy(selected), "input")
    with pytest.raises(EvolutionError, match="Policy unresolved"):
        evaluate_rule(binding, selected.model_copy(update={"stages": None}), (text("CONTENT-CANARY"),), time.monotonic() + 2)
    part = text("clean").model_copy(update={"text": {"private": "CONTENT-CANARY"}})
    with pytest.raises(EvolutionError, match="Validation failed"):
        evaluate_rule(binding, selected, (part,), time.monotonic() + 2)
    assert not recwarn


def test_mutating_source_schema_cannot_change_bound_bytes():
    selected = rule("json_schema")
    compiled = policy(selected)
    bound = RulesBinding(compiled, "output")
    # Deliberately bypass model mutation guards, as a caller could do in-process.
    dict.__setitem__(selected.config.schema, "type", "string")
    original_rule = bound.policy().rules[0]
    assert original_rule.config.schema == {"type": "object"}
    assert evaluate_rule(bound, original_rule, (structured({}),), time.monotonic() + 2) == ()
    with pytest.raises(EvolutionError, match="Policy unresolved"):
        evaluate_rule(bound, selected, (structured({}),), time.monotonic() + 2)


def test_unsupported_image_refuses_without_decoding_or_partial_output():
    part = ContentPart(part_id="i", kind="image", role="user", media_type="image/png", data_base64="eA==")
    with pytest.raises(EvolutionError, match="Unsupported surface"):
        evaluate(rule(), part)


def test_tool_metadata_is_not_treated_as_detectable_or_rewritable_content():
    # The boundary SDK owns normalized tool metadata and argument consistency.
    # This pure helper inspects the content payload only and preserves metadata.
    selected = rule()
    original = structured({"value": "a@example.test", "flag": True})
    found = evaluate(selected, original)
    result = transform_rule_findings(policy(selected), "tool_arguments", (original,), found, time.monotonic() + 2)[0]
    assert result.model_dump(exclude={"value"}) == original.model_dump(exclude={"value"})
