"""Real deterministic rules/mock handler plus closed negative observation checks."""
import time

import pytest
from evolution.evaluator_cases import ENGINES, control, expected_action
from evolution.evaluator_gateway_cases import (
    REASONS,
    assert_observation,
    sample,
    transformed,
)
from guardrails.evolution import models
from guardrails.evolution.compiler import PolicyCompiler
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.rules import (
    RulesBinding,
    evaluate_rule,
    transform_rule_findings,
)
from proof.evolution_scenarios import EVALUATOR_SCENARIOS
from test_evaluator_governance import STAMP, resource, uid
from test_evaluator_governance import base as base_fixture

base = base_fixture


@pytest.mark.parametrize("engine", tuple(value for value in ENGINES if value != "injection"))
@pytest.mark.parametrize("stage", ("input", "output"))
@pytest.mark.parametrize("clean", (True, False))
def test_gateway_text_samples_exercise_actual_deterministic_rule_and_exact_transform(base, engine, stage, clean):
    prompt, _, response = sample(engine, stage, "gateway-fixture", clean=clean)
    text = prompt if stage == "input" else response
    selected = resource(models.ListSet, list_id=uid(661), kind="content", allow_enabled=False,
        deny=("topic:finance" if engine == "topic" else "term:forbidden-fixture",))
    rule = models.Rule.model_validate_json(models.canonical_json_bytes(control(engine, stage, list_id=selected.list_id)))
    candidate = models.PolicyVersion.model_validate_json(models.canonical_json_bytes({**base,
        "policy_id": "gateway-contract", "status": "candidate", "rules": [rule.model_dump(mode="json")],
        "resolved_lists": [*base["resolved_lists"], *([selected.model_dump(mode="json")] if engine in {"term", "topic"} else [])]}))
    policy = PolicyCompiler().compile(candidate)
    part = models.ContentPart(part_id="content", kind="text", role="user" if stage == "input" else "assistant", text=text)
    findings = evaluate_rule(RulesBinding(policy, stage), rule, (part,), time.monotonic() + 0.5)
    if clean:
        assert findings == ()
    else:
        assert findings and all(row.reason_code == REASONS[engine] and row.rule_id == rule.rule_id for row in findings)
        if engine in {"pii", "secret", "term"}:
            actual = transform_rule_findings(policy, stage, (part,), findings, time.monotonic() + 0.5)
            assert actual[0].text == transformed(engine, text)


def observation(base, *, engine="pii", stage="input", state="observe", clean=False):
    prompt, _, output = sample(engine, stage, "unit-marker", clean=clean)
    correlation = uid(880)
    action = "allow" if clean else expected_action(engine, state)
    blocked_input = stage == "input" and action == "block"
    events = []
    for index, current in enumerate(("input",) if blocked_input else ("input", "output")):
        event = models.AuditEvent(event_id=uid(900 + index), decision_id=uid(900 + index),
            tenant_id="team-a", workload_id="chat", actor_id=uid(810), event_type="evaluation",
            correlation_id=correlation, policy_digest=base["compiled_digest"], binding_revision=8,
            action=action if current == stage else "allow",
            reason_codes=() if clean or current != stage else (REASONS[engine],),
            occurred_at=STAMP, created_at=STAMP, event_hash="1" * 64, metadata={"stage": current})
        events.append(event.model_dump(mode="json"))
    if action == "block":
        status, response = 403, EvolutionError("forbidden").envelope(correlation)
    else:
        status = 200
        response = {"id": "chatcmpl-" + correlation, "object": "chat.completion", "model": "guarded-chat-a",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant",
                "content": transformed(engine, output) if stage == "output" and action == "redact" else output}}],
            "guardrails": {"correlation_id": correlation, "input_decision_id": uid(900), "output_decision_id": uid(901),
                "policy": {"tenant_id": "team-a", "policy_id": base["policy_id"], "version": 1, "digest": base["compiled_digest"]},
                "binding_revision": 8, "snapshot_generation": 1, "input_action": action if stage == "input" else "allow",
                "output_action": action if stage == "output" else "allow", "reason_codes": [] if clean else [REASONS[engine]],
                "audit_status": "persisted"}}
    calls = {"count": 0 if blocked_input else 1, "calls": [] if blocked_input else [
        {"model": "gpt-4o-evolution-mock", "messages": [{"role": "user", "content":
            transformed(engine, prompt) if stage == "input" and action == "redact" else prompt}]}]}
    return {"tenant": "team-a", "workload": "chat", "actor": uid(810), "policy": base, "revision": 8,
        "engine": engine, "stage": stage, "state": state, "clean": clean, "prompt": prompt,
        "model_output": output, "provider_model": "gpt-4o-evolution-mock", "alias": "guarded-chat-a",
        "correlation": correlation, "http_status": status, "response": response, "calls": calls, "events": events}


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("stage", ("input", "output"))
@pytest.mark.parametrize("state", ("observe", "warn", "redact", "block"))
def test_observation_contract_accepts_complete_clean_and_positive_metadata(base, engine, stage, state):
    for clean in (True, False):
        assert_observation(**observation(base, engine=engine, stage=stage, state=state, clean=clean))


@pytest.mark.parametrize("fault", ("unexpected-dispatch", "boolean-counter", "missing-event", "duplicate-stage",
    "foreign-model", "different-content", "foreign-tenant", "foreign-workload", "foreign-actor", "foreign-policy",
    "foreign-correlation", "foreign-revision", "wrong-action", "wrong-reason", "spooled", "wrong-response-policy",
    "wrong-response-revision", "wrong-decision", "different-output", "wrong-alias", "extra-dispatch"))
def test_observation_never_inferrs_success_from_http_status_alone(base, fault):
    value = observation(base, state="block" if fault == "unexpected-dispatch" else "observe")
    if fault == "unexpected-dispatch":
        value["calls"] = {"count": 1, "calls": [{"model": "unexpected", "messages": []}]}
    elif fault == "boolean-counter":
        value["calls"]["count"] = True
    elif fault == "missing-event":
        value["events"].pop()
    elif fault == "duplicate-stage":
        value["events"][1]["metadata"]["stage"] = "input"
    elif fault == "foreign-model":
        value["calls"]["calls"][0]["model"] = "other"
    elif fault == "different-content":
        value["calls"]["calls"][0]["messages"][0]["content"] = "uninspected"
    elif fault == "extra-dispatch":
        value["calls"]["count"] += 1
        value["calls"]["calls"] *= 2
    elif fault in {"foreign-tenant", "foreign-workload", "foreign-actor", "foreign-policy", "foreign-correlation", "foreign-revision", "wrong-action", "wrong-reason"}:
        key, changed = {"foreign-tenant": ("tenant_id", "team-b"), "foreign-workload": ("workload_id", "other"),
            "foreign-actor": ("actor_id", uid(811)), "foreign-policy": ("policy_digest", "0" * 64),
            "foreign-correlation": ("correlation_id", uid(881)), "foreign-revision": ("binding_revision", 9),
            "wrong-action": ("action", "warn"), "wrong-reason": ("reason_codes", [])}[fault]
        value["events"][0][key] = changed
    elif fault == "different-output":
        value["response"]["choices"][0]["message"]["content"] = "different uninspected output"
    elif fault == "wrong-alias":
        value["response"]["model"] = "wrong-tenant-alias"
    else:
        key, changed = {"spooled": ("audit_status", "spooled"), "wrong-response-policy": ("policy", {}),
            "wrong-response-revision": ("binding_revision", 9), "wrong-decision": ("input_decision_id", uid(999))}[fault]
        value["response"]["guardrails"][key] = changed
    with pytest.raises(AssertionError):
        assert_observation(**value)


@pytest.mark.parametrize("engine", ("pii", "secret", "term"))
@pytest.mark.parametrize("stage", ("input", "output"))
def test_redaction_requires_exact_inspected_safe_bytes(base, engine, stage):
    value = observation(base, engine=engine, stage=stage, state="redact")
    if stage == "input":
        value["calls"]["calls"][0]["messages"][0]["content"] = value["prompt"]
    else:
        value["response"]["choices"][0]["message"]["content"] = value["model_output"]
    with pytest.raises(AssertionError):
        assert_observation(**value)


def test_fixed_scenarios_use_existing_real_mock_auth_and_closed_selection(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from proof import mock_model
    control_token, inference_token = "c" * 40, "i" * 40
    for name, token in (("control", control_token), ("inference", inference_token)):
        (tmp_path / name).write_text(token)
    monkeypatch.setenv("GUARDRAILS_PROOF_PROFILE", "integration")
    monkeypatch.setenv("GUARDRAILS_PROOF_CONTROL_TOKEN_FILE", str(tmp_path / "control"))
    monkeypatch.setenv("GUARDRAILS_MOCK_TOKEN_FILE", str(tmp_path / "inference"))
    monkeypatch.delenv("GUARDRAILS_MOCK_CORPUS", raising=False)
    # Exercise the existing handler with the exact proposed static import/update
    # seam; this does not claim the production image contains that patch yet.
    monkeypatch.setattr(mock_model, "SCENARIOS", {**mock_model.SCENARIOS, **EVALUATOR_SCENARIOS})
    control_headers, inference_headers = {"Authorization": "Bearer " + control_token}, {"Authorization": "Bearer " + inference_token}
    with TestClient(mock_model.create_app()) as client:
        assert client.put("/proof/scenario", headers=inference_headers, json={"scenario": "phase3-secret"}).status_code == 401
        assert client.put("/proof/scenario", headers=control_headers, json={"scenario": "unknown"}).status_code == 422
        assert client.put("/proof/scenario", headers=control_headers, json={"scenario": "clean", "response": "arbitrary"}).status_code == 422
        for name, expected in EVALUATOR_SCENARIOS.items():
            assert client.put("/proof/scenario", headers=control_headers, json={"scenario": name}).status_code == 200
            actual = client.post("/v1/chat/completions", headers=inference_headers,
                json={"model": "gpt-4o-evolution-mock", "messages": [{"role": "user", "content": "safe input"}]})
            assert actual.status_code == 200
            assert actual.json()["choices"][0]["message"]["content"] == expected
        calls = client.get("/proof/calls", headers=control_headers).json()
        assert calls["count"] == len(calls["calls"]) == len(EVALUATOR_SCENARIOS)


def test_budget_profiles_remain_original_text_and_classifier_limits():
    from connector.evolution import client_profile
    assert client_profile("text") == (500, 1.5)
    assert client_profile("classifier") == (3000, 4.0)
