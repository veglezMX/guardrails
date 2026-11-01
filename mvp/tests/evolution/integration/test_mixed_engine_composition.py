"""Real Rules children, HTTPS, governed policy and required audit composition.

No classifier/managed transport fake, model inference or deployed-profile claim.
Only the parent executes these actual PostgreSQL/process integration cases.
"""
import copy

import pytest
from composition_support import (
    KEY, MAIL, SLOTS, STAGES, activate, approve, audited_isolation, clients,
    evaluate, fingerprint, fresh_pair, listing, rule, text_parts,
)


@pytest.mark.parametrize("slot", SLOTS)
@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("winner", ("block", "redact", "warn", "allow"))
def test_mixed_rule_actions_aggregate_without_clean_evaluator_override(tmp_path, slot, stage, winner):
    actions = {
        "block": ("redact", "block", "warn", "allow"),
        "redact": ("redact", "warn", "allow", "block"),
        "warn": ("warn", "allow", "allow", "block"),
        "allow": ("allow", "allow", "allow", "block"),
    }[winner]
    with fresh_pair(tmp_path / "scopes") as pair:
        for system in pair.values():
            term = listing(system, deny=("term:forbidden",))
            topic = listing(system, deny=("topic:finance",))
            chosen = (
                rule("mail", "pii", actions[0], stage, {"entities": ["email"]}),
                rule("secret", "secret", actions[1], stage, {"detectors": ["aws_access_key"]}),
                rule("term", "term", actions[2], stage, {"list_id": term.list_id}),
                rule("topic", "topic", actions[3], stage, {"list_id": topic.list_id}),
            )
            selected = approve(system, "mixed-actions", rules=chosen, lists=(term, topic))
            # Binding state is chosen explicitly. would_be_action records the
            # strongest authored action; no state semantics are redefined here.
            activate(system, selected, state="block" if winner == "allow" else winner)
        value = MAIL + " " + KEY + " forbidden" + (" invoice" if winner == "block" else "")
        with clients(pair, tmp_path / "tls") as opened:
            decision = evaluate(opened[slot], pair[slot], stage, text_parts(value, stage))
            assert decision.action == decision.would_be_action == winner
            expected_rules = {"mail", "secret", "term", "topic"} if winner == "block" else {"mail", "secret", "term"}
            assert {found.rule_id for found in decision.findings} == expected_rules
            assert len(decision.findings) == len(expected_rules)
            assert decision.reason_codes == ("content_denied", "pii_detected", "secret_detected")
            # The clean topic's declared Block action must not erase actual
            # lower actions or create a finding when its lexicon is absent.
            assert decision.evaluator_revisions == ("lexical-v1", "pii-v2", "secrets-v1")
            if winner == "redact":
                actual = [part.model_dump(mode="json", exclude_none=True) for part in decision.transformed_parts]
                expected = text_parts("[REDACTED:email] [REDACTED:aws_access_key] [REDACTED:term]", stage)
                assert fingerprint(actual) == fingerprint(expected)
            else:
                assert decision.transformed_parts is None
            audited_isolation(pair, slot, opened[slot], decision, forbidden=(MAIL, KEY))


def document_schema(*, short_mask_forbidden=False):
    mail = {"type": "string"}
    if short_mask_forbidden:
        mail["minLength"] = 24
    return {
        "type": "object", "required": ["contact", "count", "ok", "empty", "items"],
        "additionalProperties": False,
        "properties": {
            "contact": {"type": "object", "required": ["a/b", "~token"], "additionalProperties": False,
                        "properties": {"a/b": mail, "~token": {"type": "string"}}},
            "count": {"type": "integer"}, "ok": {"type": "boolean"}, "empty": {"type": "null"},
            "items": {"type": "array", "items": {"anyOf": [
                {"type": "string"}, {"type": "integer"}, {"type": "boolean"}]}},
        },
    }


@pytest.mark.parametrize("slot", SLOTS)
@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("scenario", (
    "safe-leaves", "overlap-original-spans", "schema-revalidation",
    "unmatched-private-key", "numeric-card-remains-number", "invalid-original-schema",
))
def test_mixed_structured_masks_preserve_types_and_never_release_partial_output(tmp_path, slot, stage, scenario):
    original = {"contact": {"a/b": MAIL, "~token": KEY}, "count": 7, "ok": True,
                "empty": None, "items": ["ordinary", 9, False]}
    if scenario == "numeric-card-remains-number":
        original["count"] = 4111111111111111
    if scenario == "invalid-original-schema":
        original["count"] = "not-an-integer"
    if scenario == "unmatched-private-key":
        original["contact"]["~token"] = "-----BEGIN PRIVATE KEY-----\nsynthetic-incomplete"
    with fresh_pair(tmp_path / "scopes") as pair:
        for system in pair.values():
            chosen = [
                rule("mail", "pii", "redact", stage, {"entities": ["email", "payment_card"]}),
                rule("secret", "secret", "redact", stage, {"detectors": ["aws_access_key", "private_key"]}),
                rule("schema", "json_schema", "block", stage,
                     {"schema": document_schema(short_mask_forbidden=scenario == "schema-revalidation")}),
            ]
            lists = ()
            if scenario == "overlap-original-spans":
                term = listing(system, deny=("term:composition-private",))
                lists = (term,)
                chosen.append(rule("term", "term", "redact", stage, {"list_id": term.list_id}))
            activate(system, approve(system, "mixed-structured", rules=chosen, lists=lists), state="redact")
        part = {"part_id": "structured", "kind": "json", "role": "assistant" if stage == "output" else "user",
                "source_id": "synthetic-source", "value": original}
        with clients(pair, tmp_path / "tls") as opened:
            decision = evaluate(opened[slot], pair[slot], stage, [part])
            failure = scenario in {"schema-revalidation", "unmatched-private-key", "invalid-original-schema"}
            assert decision.action == ("block" if failure else "redact")
            assert decision.would_be_action == ("block" if scenario == "invalid-original-schema" else "redact")
            expected_revisions = ("lexical-v1", "pii-v2", "schema-v1", "secrets-v1") if scenario == "overlap-original-spans" else ("pii-v2", "schema-v1", "secrets-v1")
            assert decision.evaluator_revisions == expected_revisions
            email = [found for found in decision.findings if found.rule_id == "mail"]
            assert len(email) == 1
            assert (email[0].part_id, email[0].json_pointer, email[0].start, email[0].end) == (
                "structured", "/contact/a~1b", 0, len(MAIL))
            secrets = [found for found in decision.findings if found.rule_id == "secret"]
            assert len(secrets) == 1 and secrets[0].json_pointer == "/contact/~0token"
            assert all(found.category != "payment_card" for found in decision.findings)
            if failure:
                assert decision.transformed_parts is None
                assert "unsafe_transformation" in decision.reason_codes
                if scenario == "unmatched-private-key":
                    assert secrets[0].start is secrets[0].end is None
                if scenario == "invalid-original-schema":
                    assert "schema_invalid" in decision.reason_codes
                    assert len([found for found in decision.findings if found.rule_id == "schema"]) == 1
                if scenario == "schema-revalidation":
                    # Original passes; only the actual replacement makes the
                    # declared minimum string length fail. No synthetic schema
                    # response or transform port is substituted.
                    assert "schema_invalid" not in decision.reason_codes
                    assert all(found.rule_id != "schema" for found in decision.findings)
            else:
                expected = copy.deepcopy(original)
                expected["contact"]["a/b"] = "[REDACTED:sensitive]" if scenario == "overlap-original-spans" else "[REDACTED:email]"
                expected["contact"]["~token"] = "[REDACTED:aws_access_key]"
                assert len(decision.transformed_parts) == 1
                transformed = decision.transformed_parts[0]
                assert (transformed.part_id, transformed.kind, transformed.role, transformed.source_id) == (
                    part["part_id"], part["kind"], part["role"], part["source_id"])
                actual = transformed.model_dump(mode="json")["value"]
                assert fingerprint(actual) == fingerprint(expected)
                assert type(actual["count"]) is int and actual["count"] == original["count"]
                assert actual["ok"] is True and actual["empty"] is None
                assert type(actual["items"][1]) is int and actual["items"][2] is False
                assert "unsafe_transformation" not in decision.reason_codes
            audited_isolation(pair, slot, opened[slot], decision, forbidden=(MAIL, KEY, "synthetic-incomplete"))

