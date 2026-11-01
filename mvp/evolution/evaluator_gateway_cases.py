"""Finite gateway expectations shared by deployed cases and isolated contract checks."""
from uuid import UUID

from guardrails.evolution import models
from guardrails.evolution.errors import EvolutionError
from proof.evolution_scenarios import EVALUATOR_SCENARIOS

from .evaluator_cases import ENGINES, content, expected_action
from .evaluator_governance import policy_reference

REASONS = {"pii": "pii_detected", "secret": "secret_detected", "term": "content_denied",
           "topic": "content_denied", "json_schema": "schema_invalid", "injection": "injection_detected"}
TOKENS = {"pii": ("4111111111111111", "[REDACTED:payment_card]"),
          "secret": ("AKIAABCDEFGHIJKLMNOP", "[REDACTED:aws_access_key]"),
          "term": ("forbidden-fixture", "[REDACTED:term]")}


def sample(engine, stage, marker, *, clean):
    assert engine in ENGINES and stage in {"input", "output"} and type(clean) is bool
    if stage == "input":
        part = content(engine, stage, marker, clean=clean)
        prompt = part["text"] if part["kind"] == "text" else models.canonical_json_bytes(part["value"]).decode()
        return prompt, "clean", "A clean mock response."
    prompt = "Describe a clear blue sky. " + marker
    if clean:
        name = "phase3-json-schema-clean" if engine == "json_schema" else "clean"
    else:
        name = "phase3-" + engine.replace("_", "-")
    return prompt, name, EVALUATOR_SCENARIOS[name] if name != "clean" else "A clean mock response."


def transformed(engine, text):
    before, after = TOKENS[engine]
    assert text.count(before) == 1
    return text.replace(before, after)


def assert_observation(*, tenant, workload, actor, policy, revision, engine, stage, state,
                       clean, prompt, model_output, provider_model, alias, correlation,
                       http_status, response, calls, events):
    """Require real caller-supplied transport/counter/audit observations to agree."""
    action = "allow" if clean else expected_action(engine, state)
    blocked_input = stage == "input" and action == "block"
    expected_count = 0 if blocked_input else 1
    assert set(calls) == {"count", "calls"}
    assert type(calls["count"]) is int and calls["count"] == expected_count
    assert type(calls["calls"]) is list and len(calls["calls"]) == expected_count
    if expected_count:
        inspected = transformed(engine, prompt) if stage == "input" and action == "redact" else prompt
        assert calls["calls"][0] == {"model": provider_model, "messages": [{"role": "user", "content": inspected}]}
    assert len(events) == (1 if blocked_input else 2)
    event_by_stage = {row["metadata"]["stage"]: row for row in events}
    assert len(event_by_stage) == len(events)
    assert set(event_by_stage) == ({"input"} if blocked_input else {"input", "output"})
    for current_stage, event in event_by_stage.items():
        parsed = models.AuditEvent.model_validate_json(models.canonical_json_bytes(event))
        assert parsed.tenant_id == tenant and parsed.workload_id == workload
        assert parsed.actor_id == actor and parsed.correlation_id == correlation
        assert parsed.event_type == "evaluation" and parsed.event_id == parsed.decision_id
        assert parsed.policy_digest == policy["compiled_digest"] and parsed.binding_revision == revision
        assert parsed.action == (action if current_stage == stage else "allow")
        assert parsed.reason_codes == (() if clean or current_stage != stage else (REASONS[engine],))
        assert parsed.raw_content_retained is False
    if action == "block":
        assert http_status == 403 and response == EvolutionError("forbidden").envelope(correlation)
        # Blocked content must not escape in the canonical error envelope.
        assert b"phase3-output-fixture" not in models.canonical_json_bytes(response)
        if engine in TOKENS:
            assert TOKENS[engine][0].encode() not in models.canonical_json_bytes(response)
        return
    assert http_status == 200
    assert response["model"] == alias and response["object"] == "chat.completion"
    assert response["id"] == "chatcmpl-" + correlation
    assert len(response["choices"]) == 1 and response["choices"][0]["finish_reason"] == "stop"
    message = response["choices"][0]["message"]
    assert message["role"] == "assistant"
    output = transformed(engine, model_output) if stage == "output" and action == "redact" else model_output
    assert message["content"] == output
    details = response["guardrails"]
    assert set(details) == {"correlation_id", "input_decision_id", "output_decision_id", "policy",
                           "binding_revision", "snapshot_generation", "input_action", "output_action",
                           "reason_codes", "audit_status"}
    assert details["correlation_id"] == correlation and details["policy"] == policy_reference(policy)
    assert details["binding_revision"] == revision and type(details["snapshot_generation"]) is int
    assert details["snapshot_generation"] >= 1 and details["audit_status"] == "persisted"
    assert details["input_action"] == (action if stage == "input" else "allow")
    assert details["output_action"] == (action if stage == "output" else "allow")
    assert details["reason_codes"] == ([] if clean else [REASONS[engine]])
    for current_stage in ("input", "output"):
        identity = details[current_stage + "_decision_id"]
        assert identity == str(UUID(identity)) == event_by_stage[current_stage]["decision_id"]
    assert details["input_decision_id"] != details["output_decision_id"]
