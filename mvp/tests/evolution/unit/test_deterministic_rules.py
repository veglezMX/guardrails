"""Exact, synthetic worker-side detector and immutable policy contracts."""
import json
import time
from pathlib import Path
from uuid import UUID

import pytest
from guardrails.evolution.compiler import policy_digest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import (
    ContentPart,
    ListSet,
    PolicyVersion,
    Quota,
    ResidencyProfile,
    Rule,
    with_resource_digest,
)
from guardrails.evolution.rules import (
    RulesBinding,
    evaluate_content_lists,
    evaluate_rule,
    schema_valid,
    string_leaves,
    transform_rule_findings,
)

STAMP = "2026-10-12T00:00:00Z"
FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/deterministic-secrets.json").read_text())


def uid(number):
    return str(UUID(int=number))


def resource(cls, **values):
    return with_resource_digest(cls(created_at=STAMP, tenant_id="team-a", **values))


def rule(kind="pii", config=None, **changes):
    configurations = {"pii": {"entities": ["email", "international_phone", "payment_card"]}, "secret": {"detectors": ["private_key", "aws_access_key", "github_token"]}, "term": {"list_id": uid(301)}, "topic": {"list_id": uid(301)}, "json_schema": {"schema": {"type": "object"}}}
    revisions = {"pii": "pii-v2", "secret": "secrets-v1", "term": "lexical-v1", "topic": "lexical-v1", "json_schema": "schema-v1"}
    values = {"rule_id": kind, "kind": kind, "stages": ["input", "output", "retrieval", "tool_arguments", "tool_result"], "action": "block", "config": config or configurations[kind], "evaluator_revision": revisions[kind]}
    return Rule.model_validate({**values, **changes})


def content_list(number=301, **values):
    return resource(ListSet, list_id=uid(number), kind="content", **values)


def policy(*rules, lists=(), content_ids=()):
    value = PolicyVersion(tenant_id="team-a", policy_id="rules", version=1, author_id=uid(1), created_at=STAMP, rules=rules, resolved_lists=lists, residency=resource(ResidencyProfile, residency_id=uid(101), content_lists=content_ids), quota=resource(Quota, quota_id=uid(201)), status="approved")
    return value.model_copy(update={"compiled_digest": policy_digest(value)})


def text(value, part_id="p"):
    return ContentPart(part_id=part_id, kind="text", role="user", text=value)


def structured(value):
    return ContentPart(part_id="p", kind="json", role="tool", value=value)


def evaluate(selected, part, *, lists=(), content_ids=(), stage="input"):
    approved = policy(selected, lists=lists, content_ids=content_ids)
    return evaluate_rule(RulesBinding(approved, stage), selected, (part,), time.monotonic() + 2)


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda row: row["id"])
def test_frozen_secret_grammars(case):
    findings = evaluate(rule("secret", {"detectors": [case["detector"]]}), text(case["text"]))
    assert [(found.category, found.start, found.end) for found in findings] == [(item["category"], item["start"], item["end"]) for item in case["expected"]]
    assert all(case["text"] not in found.model_dump_json() for found in findings)


@pytest.mark.parametrize("value,expected", [
    ("mail=a+b@example.test.", [("email", 0, 21)]),
    ("étest@example.test", []), ("test@example.testé", []),
    ("a..b@example.test", []), ("x@example..test", []),
    ("+525512345678", [("international_phone", 0, 13)]),
    ("++525512345678", []), ("+025512345678", []),
    ("+525512345678９", []), ("+1234567890123456", []),
    ("4111 1111 1111 1111", [("payment_card", 0, 19)]),
    ("4111111111111112", []), ("0000000000000000", []),
    ("1111111111111111111", []), ("x4111111111111111", []),
    ("4111111111111111é", []), ("94111111111111111111", []),
])
def test_pii_exact_grammar(value, expected):
    assert [(item.category, item.start, item.end) for item in evaluate(rule(), text(value))] == expected


@pytest.mark.parametrize("value", ["4222222222222", "40000000000002", "400000000000006", "4000000000000002", "40000000000000006", "400000000000000002", "4000000000000000006"])
def test_each_payment_card_candidate_length(value):
    found = evaluate(rule(config={"entities": ["payment_card"]}), text(value))
    assert [(item.start, item.end) for item in found] == [(0, len(value))]


def test_overlapping_card_and_phone_mask_once_without_partial_exposure():
    selected, part = rule(), text("+4222222222222")
    found = evaluate(selected, part)
    assert {item.category for item in found} == {"payment_card", "international_phone"}
    result = transform_rule_findings(policy(selected), "input", (part,), found, time.monotonic() + 2)
    assert result[0].text == "[REDACTED:sensitive]"


@pytest.mark.parametrize("value,expected", [("192.0.2.1", True), ("192.000.2.1", False), ("256.1.2.3", False), ("1.2.3.4.5", False), ("x1.2.3.4", False), ("1.2.3.4é", False), ("1.2.3.4.", False)])
def test_ipv4_is_opt_in_and_exact(value, expected):
    assert evaluate(rule(), text(value)) == ()
    assert bool(evaluate(rule(config={"entities": ["ipv4"]}), text(value))) is expected


@pytest.mark.parametrize("key_type", ["PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "OPENSSH PRIVATE KEY"])
def test_unmatched_or_nested_private_key_is_untransformable(key_type):
    value = f"-----BEGIN {key_type}-----\nsynthetic-only"
    selected = rule("secret")
    found = evaluate(selected, text(value))
    assert len(found) == 1 and found[0].category == "private_key"
    assert found[0].start is None and found[0].end is None
    with pytest.raises(EvolutionError, match="Unsafe transformation"):
        transform_rule_findings(policy(selected), "input", (text(value),), found, time.monotonic() + 2)


@pytest.mark.parametrize("value", [
    "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END RSA PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----\n-----BEGIN RSA PRIVATE KEY-----\nsynthetic\n-----END RSA PRIVATE KEY-----\n-----END PRIVATE KEY-----",
])
def test_mismatched_or_nested_key_delimiters_never_release_partial_redaction(value):
    selected = rule("secret")
    found = evaluate(selected, text(value))
    assert any(item.category == "private_key" and item.start is None for item in found)
    with pytest.raises(EvolutionError, match="Unsafe transformation"):
        transform_rule_findings(policy(selected), "input", (text(value),), found, time.monotonic() + 2)


@pytest.mark.parametrize("value,literal,span", [
    ("xxStraße!", "STRASSE", (2, 8)),
    ("x e\u0301 y", "É", (2, 4)),
    ("x ﬃ y", "FFI", (2, 3)),
    ("x ＡＢＣ y", "abc", (2, 5)),
    ("x 각 y", "각", (2, 5)),
    ("😀SECRET", "secret", (1, 7)),
    ("x ﬃ y", "fi", (None, None)),
    ("x ß y", "s", (None, None)),
])
def test_normalization_original_offsets_and_ambiguous_expansion(value, literal, span):
    selected = rule("term")
    lists = (content_list(deny=("term:" + literal,)),)
    found = evaluate(selected, text(value), lists=lists)
    assert found and {(item.start, item.end) for item in found} == {span}
    if span[0] is None:
        with pytest.raises(EvolutionError, match="Unsafe transformation"):
            transform_rule_findings(policy(selected, lists=lists), "input", (text(value),), found, time.monotonic() + 2)
    else:
        transformed = transform_rule_findings(policy(selected, lists=lists), "input", (text(value),), found, time.monotonic() + 2)
        assert transformed[0].text == value[:span[0]] + "[REDACTED:term]" + value[span[1]:]


@pytest.mark.parametrize("topic,terms", [("finance", ("invoice", "budget", "payment")), ("medical", ("diagnosis", "prescription", "treatment")), ("software", ("source code", "compiler", "repository"))])
def test_exact_topic_lexicons_and_word_boundaries(topic, terms):
    selected, lists = rule("topic"), (content_list(deny=("topic:" + topic,)),)
    for term in terms:
        found = evaluate(selected, text("(" + term.upper() + ")"), lists=lists)
        assert len(found) == 1 and found[0].category == "topic" and found[0].start is None
        assert evaluate(selected, text("prefix" + term + "suffix"), lists=lists) == ()
        assert evaluate(selected, text("_" + term + "_"), lists=lists) == ()


def test_content_constraints_without_rules_intersect_allow_and_union_deny():
    first = content_list(301, allow=("term:alpha", "term:beta"), deny=("term:secret",), allow_enabled=True)
    second = content_list(302, allow=("term:alpha", "term:gamma"), deny=("term:alpha",), allow_enabled=True)
    bound = RulesBinding(policy(lists=(first, second), content_ids=(first.list_id, second.list_id)), "retrieval")
    deadline = time.monotonic() + 2
    found = evaluate_content_lists(bound, (text("beta gamma", "first"), text("alpha", "second"), text("", "blank")), deadline)
    assert [(item.part_id, item.reason_code) for item in found].count(("first", "content_not_allowed")) == 2
    assert [(item.part_id, item.reason_code) for item in found].count(("blank", "content_not_allowed")) == 2
    assert [(item.part_id, item.reason_code) for item in found].count(("second", "content_denied")) == 1
    assert all(item.rule_id in {"content-list:" + first.list_id, "content-list:" + second.list_id} for item in found)


def test_policy_only_list_redaction_and_empty_enabled_allow():
    listing = content_list(deny=("term:private",), allow_enabled=False)
    compiled = policy(lists=(listing,), content_ids=(listing.list_id,))
    parts = (text("a private note"),)
    findings = evaluate_content_lists(RulesBinding(compiled, "input"), parts, time.monotonic() + 2)
    assert transform_rule_findings(compiled, "input", parts, findings, time.monotonic() + 2)[0].text == "a [REDACTED:term] note"
    empty = content_list(allow_enabled=True)
    bound = RulesBinding(policy(lists=(empty,), content_ids=(empty.list_id,)), "input")
    assert evaluate_content_lists(bound, (text("anything"),), time.monotonic() + 2)[0].reason_code == "content_not_allowed"


def test_allowed_content_never_exempts_pii():
    selected = rule()
    allowed = content_list(allow=("term:example",), allow_enabled=True)
    bound = RulesBinding(policy(selected, lists=(allowed,), content_ids=(allowed.list_id,)), "input")
    parts = (text("a@example.test"),)
    assert evaluate_content_lists(bound, parts, time.monotonic() + 2) == ()
    assert len(evaluate_rule(bound, selected, parts, time.monotonic() + 2)) == 1


def test_structured_leaves_escape_pointers_and_preserve_non_string_values():
    selected = rule()
    part = structured({"a/b~c": ["x@example.test", 4111111111111111, True, None], "x@example.test": "clean"})
    leaves = string_leaves((part,))
    assert [(pointer, value) for _, pointer, value in leaves] == [("/a~1b~0c/0", "x@example.test"), ("/x@example.test", "clean")]
    found = evaluate(selected, part)
    assert len(found) == 1 and found[0].json_pointer == "/a~1b~0c/0"
    result = transform_rule_findings(policy(selected), "tool_result", (part,), found, time.monotonic() + 2)[0]
    assert result.value["a/b~c"] == ["[REDACTED:email]", 4111111111111111, True, None]
    assert result.role == part.role and result.part_id == part.part_id
    assert part.value["a/b~c"][0] == "x@example.test"


@pytest.mark.parametrize("value,schema,valid", [
    (True, {"type": "integer"}, False), (1.0, {"type": "integer"}, True),
    (True, {"enum": [1]}, False), (1, {"const": True}, False),
    (1.0, {"enum": [1]}, True), ({"n": 1}, {"const": {"n": True}}, False),
    ("é", {"minLength": 1, "maxLength": 1}, True),
    ([1, 2], {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 2}, "minItems": 2, "maxItems": 2}, True),
    ([1, 3], {"items": {"maximum": 2}}, False),
    ({"x": "yes"}, {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"], "additionalProperties": False}, True),
    ({"y": "no"}, {"required": ["x"]}, False),
    ({"x": 1}, {"additionalProperties": False}, False),
    ("yes", {"anyOf": [{"type": "integer"}, {"const": "yes"}]}, True),
    (None, {"anyOf": [{"type": "integer"}, {"type": "string"}]}, False),
])
def test_schema_dialect_exact_semantics(value, schema, valid):
    assert schema_valid(value, schema, time.monotonic() + 2) is valid


@pytest.mark.parametrize("value", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', 'not json'])
def test_schema_text_rejects_duplicate_nonfinite_and_invalid_json(value):
    found = evaluate(rule("json_schema"), text(value))
    assert len(found) == 1 and found[0].reason_code == "schema_invalid" and found[0].start is None


def test_schema_revalidation_refuses_length_breaking_redaction_without_partial_output():
    pii = rule()
    schema = rule("json_schema", {"schema": {"type": "object", "properties": {"mail": {"type": "string", "maxLength": 14}}, "additionalProperties": False}})
    original = structured({"mail": "a@b.test"})
    compiled = policy(pii, schema)
    found = evaluate_rule(RulesBinding(compiled, "output"), pii, (original,), time.monotonic() + 2)
    with pytest.raises(EvolutionError, match="Unsafe transformation"):
        transform_rule_findings(compiled, "output", (original,), found, time.monotonic() + 2)
    assert original.value == {"mail": "a@b.test"}


def test_schema_valid_string_leaf_redaction_revalidates():
    pii = rule()
    schema = rule("json_schema", {"schema": {"type": "object", "properties": {"mail": {"type": "string"}, "count": {"const": 2}, "flag": {"const": True}}, "required": ["mail", "count", "flag"], "additionalProperties": False}})
    original = structured({"mail": "a@b.test", "count": 2, "flag": True})
    compiled = policy(pii, schema)
    found = evaluate_rule(RulesBinding(compiled, "output"), pii, (original,), time.monotonic() + 2)
    transformed = transform_rule_findings(compiled, "output", (original,), found, time.monotonic() + 2)[0]
    assert transformed.value == {"mail": "[REDACTED:email]", "count": 2, "flag": True}
    assert schema_valid(transformed.value, schema.config.schema, time.monotonic() + 2)


@pytest.mark.parametrize("schema", [{"$ref": "https://example.test/remote"}, {"pattern": "(.+)+"}, {"properties": {str(i): {} for i in range(257)}}, {"anyOf": [{}] * 65}])
def test_unsupported_or_expanded_schema_rejects_before_evaluation(schema):
    with pytest.raises((ValueError, EvolutionError)):
        rule("json_schema", {"schema": schema})


def test_schema_depth_limit_exact():
    accepted = {"type": "string"}
    for _ in range(31):
        accepted = {"items": accepted}
    rule("json_schema", {"schema": accepted})
    with pytest.raises(ValueError):
        rule("json_schema", {"schema": {"items": accepted}})


def test_schema_text_redaction_cannot_change_object_key():
    pii, schema = rule(), rule("json_schema")
    part = text('{"a@b.test":"clean"}')
    compiled = policy(pii, schema)
    found = evaluate_rule(RulesBinding(compiled, "output"), pii, (part,), time.monotonic() + 2)
    with pytest.raises(EvolutionError, match="Unsafe transformation"):
        transform_rule_findings(compiled, "output", (part,), found, time.monotonic() + 2)


def test_binding_keeps_bytes_and_rejects_mutated_rule_or_wrong_stage():
    selected = rule(stages=("input",))
    compiled = policy(selected)
    bound = RulesBinding(compiled, "input")
    assert type(bound.policy_bytes) is bytes
    forged = selected.model_copy(update={"evaluator_revision": "other"})
    with pytest.raises(EvolutionError, match="Policy unresolved"):
        evaluate_rule(bound, forged, (text("clean"),), time.monotonic() + 2)
    with pytest.raises(EvolutionError, match="Policy unresolved"):
        evaluate_rule(RulesBinding(compiled, "output"), selected, (text("clean"),), time.monotonic() + 2)
    object.__setattr__(compiled, "rules", ())
    assert bound.policy().rules == (selected,)


def test_binding_rejects_stale_policy_or_list_digest():
    selected, listing = rule("term"), content_list(deny=("term:private",))
    compiled = policy(selected, lists=(listing,))
    with pytest.raises(EvolutionError, match="Policy unresolved"):
        RulesBinding(compiled.model_copy(update={"compiled_digest": "0" * 64}), "input")
    bad = listing.model_copy(update={"deny": ("term:changed",)})
    with pytest.raises(EvolutionError, match="Policy unresolved"):
        RulesBinding(policy(selected, lists=(bad,)), "input")


def test_unresolved_content_reference_and_forged_findings_refuse():
    selected = rule()
    with pytest.raises(EvolutionError, match="Policy unresolved"):
        RulesBinding(policy(selected, content_ids=(uid(999),)), "input")
    part = text("a@example.test")
    found = evaluate(selected, part)
    forged = found[0].model_copy(update={"end": 1})
    with pytest.raises(EvolutionError, match="Unsafe transformation"):
        transform_rule_findings(policy(selected), "input", (part,), (forged,), time.monotonic() + 2)


def test_duplicate_part_ids_and_findings_limit_refuse():
    selected = rule()
    bound = RulesBinding(policy(selected), "input")
    with pytest.raises(EvolutionError, match="Validation failed"):
        evaluate_rule(bound, selected, (text("a@b.test"), text("c@d.test")), time.monotonic() + 2)
    with pytest.raises(EvolutionError, match="Inspection incomplete"):
        evaluate_rule(bound, selected, (text("a@b.co " * 4097),), time.monotonic() + 2)


@pytest.mark.parametrize("deadline", [0, -1, float("inf"), float("nan"), True])
def test_deadline_validation_and_expiry(deadline):
    selected = rule()
    with pytest.raises(EvolutionError) as caught:
        evaluate_rule(RulesBinding(policy(selected), "input"), selected, (text("a@example.test"),), deadline)
    assert caught.value.code == ("evaluation_timeout" if type(deadline) is int else "validation_failed")
