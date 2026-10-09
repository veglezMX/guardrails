"""Strict external boundary and privacy regressions for local-proof-v1."""
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from guardrails.transport import (
    ClientConfigModel, ContractProblem, DecisionModel, EvaluationRequestModel, PolicyValidationModel,
    contract_openapi, install_exception_handlers, parse_decision, parse_evaluation,
    parse_model, parse_policy, problem_document, strict_json_loads,
)


def request():
    return {"schema_version": "local-proof-v1", "tenant_id": "local-proof", "workload_id": "team-a", "stage": "input", "content": {"segments": [{"segment_id": "0", "text": "authored fixture"}]}, "correlation_id": str(uuid4()), "context_id": None}


def policy():
    return {"schema_version": "local-proof-v1", "tenant_id": "local-proof", "policy_id": "pii", "version": "v1", "checks": [{"check_id": "pii", "evaluator": "local_pii", "stages": ["input", "output"], "pii_types": ["email", "phone"], "requested_action": "redact"}], "failure_behavior": {name: "block" for name in ("evaluation_timeout", "evaluation_unavailable", "policy_unresolved", "management_unavailable", "audit_failure", "transformation_failure")}, "retain_content": False, "detector_format_version": "pii-ascii-v1"}


def decision():
    return dict(decision_id=str(uuid4()), evaluation_id=str(uuid4()), correlation_id=str(uuid4()), context_id=None, tenant_id="local-proof", workload_id="team-a", stage="input", policy_id=None, policy_version=None, policy_digest=None, binding_revision=None, snapshot_generation=None, enforcement_state=None, action="allow", allowed=True, would_be_action="allow", reason_codes=[], operational_codes=[], guardrail_category=None, evaluator_metadata=[], audit_id=str(uuid4()), findings=[], transformed_content=None, evaluation_ms=0.1, source="runtime", audit_status="persisted", client_config=None)


def raw(value):
    return json.dumps(value).encode()


def test_valid_request_round_trip_and_domain_creation():
    original = request()
    model = parse_evaluation(raw(original))
    assert model.model_dump(mode="json") == original
    assert model.to_domain().content.segments[0].text == "authored fixture"
    assert model.to_domain().received_at.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("payload", [b'{"secret":"private","secret":"second"}', b'{"content":{"secret":1,"secret":2}}', b'{"n":NaN}', b'{"n":Infinity}', b'{"n":-Infinity}', b'{"n":1e999}', b'{"bad":"\\ud800"}', b'{"bad":"\xff"}', b'{'])
def test_malformed_json_never_contains_input_in_public_failure(payload):
    with pytest.raises(ContractProblem) as result:
        strict_json_loads(payload)
    assert result.value.status == 400
    response = json.dumps(problem_document(result.value, uuid4()))
    assert "private" not in response and "secret" not in response
    assert "private" not in str(result.value)


def test_raw_byte_limit_precedes_json_parsing():
    with pytest.raises(ContractProblem) as result:
        strict_json_loads(b"not-json", max_bytes=1)
    assert result.value.status == 413


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_policy_retention_rejects_coercion(value):
    body = policy()
    body["retain_content"] = value
    with pytest.raises(ContractProblem) as result:
        parse_policy(raw(body))
    assert result.value.status == 422


def test_policy_digest_and_missing_explicit_failure_behavior():
    parsed = parse_policy(raw(policy()))
    assert len(parsed.to_domain().content_digest()) == 64
    body = policy()
    del body["failure_behavior"]["audit_failure"]
    with pytest.raises(ContractProblem):
        parse_policy(raw(body))


def test_unsupported_stage_has_distinct_code():
    body = request()
    body["stage"] = "retrieval"
    with pytest.raises(ContractProblem) as result:
        parse_evaluation(raw(body))
    assert result.value.code == "unsupported_surface"


@pytest.mark.parametrize("where", ["root", "nested", "private-field-name"])
def test_unknown_fields_cannot_echo_keys_or_values(where):
    body = request()
    target = body if where != "nested" else body["content"]["segments"][0]
    target["private-field-name"] = "SECRET-CONTENT"
    with pytest.raises(ContractProblem) as result:
        parse_evaluation(raw(body))
    public = json.dumps(problem_document(result.value, uuid4()))
    assert "SECRET-CONTENT" not in public
    assert "private-field-name" not in public


def test_all_required_decision_fields_and_null_semantics_survive_round_trip():
    original = decision()
    model = parse_decision(raw(original))
    assert model.model_dump(mode="json") == original
    for field in DecisionModel.model_fields:
        incomplete = dict(original)
        del incomplete[field]
        with pytest.raises(ContractProblem):
            parse_decision(raw(incomplete))


@pytest.mark.parametrize("field,value", [("allowed", False), ("allowed", "true"), ("evaluation_ms", True), ("action", "unknown"), ("evaluation_id", None), ("source", "connector"), ("audit_id", None), ("policy_id", "pii")])
def test_invalid_decision_is_protocol_failure(field, value):
    body = decision()
    body[field] = value
    with pytest.raises(ContractProblem):
        parse_decision(raw(body))


def test_non_redaction_cannot_return_original_content():
    body = decision()
    body["transformed_content"] = request()["content"]
    with pytest.raises(ContractProblem):
        parse_decision(raw(body))


def test_fallback_decision_is_distinct_from_runtime_decision():
    body = decision()
    body.update(source="connector", evaluation_id=None, audit_status="spooled", audit_id=None)
    model = parse_decision(raw(body))
    assert model.to_domain().source == "connector"


def test_client_config_is_not_a_policy_distribution_surface():
    body = dict(schema_version="local-proof-v1", tenant_id="local-proof", workload_id="team-a", generation=1, input_timeout_ms=1500, output_timeout_ms=1500, failure_behavior=policy()["failure_behavior"], retrieved_at=datetime.now(timezone.utc).isoformat())
    parse_model(raw(body), ClientConfigModel)
    body["checks"] = policy()["checks"]
    with pytest.raises(ContractProblem):
        parse_model(raw(body), ClientConfigModel)
    del body["checks"]
    body["input_timeout_ms"] = True
    with pytest.raises(ContractProblem):
        parse_model(raw(body), ClientConfigModel)


def test_schema_is_generated_from_strict_live_models():
    schema = contract_openapi()
    models = schema["components"]["schemas"]
    assert models["EvaluationRequestModel"]["additionalProperties"] is False
    assert set(models["DecisionModel"]["required"]) == set(DecisionModel.model_fields)
    assert schema["paths"]["/v1/evaluate"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("DecisionModel")


def test_fastapi_validation_handler_removes_submitted_values():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    install_exception_handlers(app)

    @app.post("/check")
    def check(body: EvaluationRequestModel):
        return {"ok": True}

    payload = request()
    payload["correlation_id"] = "SECRET-CONTENT"
    result = TestClient(app).post("/check", json=payload)
    assert result.status_code == 422
    assert result.headers["content-type"] == "application/problem+json"
    assert "SECRET-CONTENT" not in result.text
    assert set(result.json()) == {"type", "title", "status", "code", "correlation_id", "errors"}


@pytest.mark.parametrize("value", [1, 1.0, "true", False])
def test_validation_success_marker_requires_literal_boolean_true(value):
    body = {"valid": value, "canonical_digest": "0" * 64, "capabilities_version": "local-proof-v1"}
    with pytest.raises(ContractProblem):
        parse_model(raw(body), PolicyValidationModel)
    body["valid"] = True
    assert parse_model(raw(body), PolicyValidationModel).valid is True
