"""Adversarial validation of the actual v2 domain and config boundaries."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from guardrails.evolution.config import load_config
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import (
    AdminSession, AuditEvent, ContentPart, Credential, Decision, Destination,
    EvaluationRequest, Finding, ListSet, PolicyRef, Principal, Quota,
    ResidencyProfile, Rule, RuntimeInstall, ToolSpec, Workload, ZERO_DIGEST,
    canonical_json_bytes, strict_json_loads, validate_schema, with_resource_digest,
)

U1 = "00000000-0000-4000-8000-000000000001"
U2 = "00000000-0000-4000-8000-000000000002"
STAMP = "2026-10-10T00:00:00Z"


def rule(**updates):
    return Rule.model_validate({"rule_id": "pii", "kind": "pii", "stages": ["input"], "action": "block", "config": {"entities": ["email"]}, "evaluator_revision": "pii-v2", **updates})


def test_strict_json_rejects_duplicate_keys_nonfinite_and_extras():
    for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '{"x":{"y":1,"y":2}}'):
        with pytest.raises(ValueError):
            strict_json_loads(text)
    for values in ({"quota_id": U1, "tenant_id": "team-a", "max_inflight": True}, {"quota_id": U1, "tenant_id": "team-a", "version": "1"}, {"quota_id": U1, "tenant_id": "team-a", "secret": "canary"}):
        with pytest.raises(ValidationError):
            Quota.model_validate(values)
    with pytest.raises(ValueError):
        Quota.model_validate_json('{"quota_id":"' + U1 + '","tenant_id":"team-a","burst":1,"burst":2}')
    assert strict_json_loads('{"ok":[1,true,null]}') == {"ok": [1, True, None]}


def test_json_wire_requires_utf8_and_valid_unicode_scalar_values():
    for encoded in ('{"x":1}'.encode("utf-16"), '{"x":1}'.encode("utf-32"), b'{"x":"\\ud800"}', b'{"\\udfff":1}'):
        with pytest.raises(ValueError):
            strict_json_loads(encoded)
    assert strict_json_loads('{"greeting":"Español"}'.encode("utf-8"))["greeting"] == "Español"


def test_rule_config_discriminates_and_rejects_foreign_fields():
    for values in ({"kind": "secret"}, {"config": {"entities": ["email"], "threshold": 0.9}}, {"config": {"entities": ["email", "email"]}}, {"stages": ["input", "input"]}, {"mandatory": True, "action": "allow"}):
        with pytest.raises(ValidationError):
            rule(**values)
    assert rule().config.entities == ("email",)
    assert rule().model_dump(mode="json")["config"] == {"entities": ["email"]}


def test_domain_payload_is_recursively_immutable_and_roundtrips():
    schema = {"type": "object", "properties": {"name": {"enum": ["a", "b"]}}}
    tool = ToolSpec(tool_id=U1, tenant_id="team-a", name="read", argument_schema=schema)
    original = canonical_json_bytes(tool)
    schema["properties"]["name"]["enum"].append("external mutation")
    with pytest.raises(TypeError):
        tool.argument_schema["properties"]["name"]["enum"].append("attack")
    with pytest.raises(TypeError):
        tool.argument_schema["properties"]["name"]["type"] = "number"
    with pytest.raises(ValidationError):
        tool.name = "changed"
    assert canonical_json_bytes(tool) == original
    assert ToolSpec.model_validate_json(tool.model_dump_json()) == tool


def test_json_payload_limits_reject_depth_nodes_and_wrong_payloads():
    deep = value = {}
    for _ in range(33):
        value["nested"] = {}
        value = value["nested"]
    for payload in (deep, list(range(10001)), float("nan")):
        with pytest.raises(ValidationError):
            ContentPart(part_id="p", kind="json", role="user", value=payload)
    for values in ({"kind": "text", "text": "ok", "value": {}}, {"kind": "json"}, {"kind": "image", "data_base64": "https://host/image", "media_type": "image/png"}, {"kind": "image", "data_base64": "", "media_type": "image/png"}):
        with pytest.raises(ValidationError):
            ContentPart(part_id="p", role="user", **values)
    assert ContentPart(part_id="null", kind="json", role="user", value=None).value is None


def test_evaluation_requires_context_provider_and_aggregate_bounds():
    payload = dict(request_id=U1, correlation_id=U2, tenant_id="team-a", workload_id="chat", stage="input", parts=[dict(part_id="p", kind="text", role="user", text="safe")], provider_id=U2, deadline_ms=500)
    assert EvaluationRequest.model_validate(payload).parts[0].text == "safe"
    for override in ({"provider_id": None}, {"stage": "output"}, {"context_id": U1}, {"parts": payload["parts"] * 2}, {"parts": [dict(part_id=str(i), kind="text", role="user", text="a" * 40000) for i in range(2)]}):
        with pytest.raises(ValidationError):
            EvaluationRequest.model_validate({**payload, **override})


def test_restricted_schema_rejects_references_and_expansion():
    bad = ({"$ref": "https://forbidden/schema"}, {"pattern": ".*"}, {"type": ["string", "null"]}, {"properties": {str(i): {} for i in range(257)}}, {"minLength": True}, {"minimum": 5, "maximum": 3}, {"enum": [1, 1]}, {"required": ["a", "a"]})
    for schema in bad:
        with pytest.raises(ValueError):
            validate_schema(schema)
    deep = schema = {}
    for _ in range(33):
        schema["items"] = {}
        schema = schema["items"]
    with pytest.raises(ValueError):
        validate_schema(deep)
    valid = {"type": "object", "required": ["name"], "properties": {"name": {"type": "string", "minLength": 1}}, "additionalProperties": False}
    assert validate_schema(valid) == valid


def test_list_defaults_and_selected_content_grammar():
    assert ListSet(list_id=U1, tenant_id="team-a", kind="provider").allow_enabled is True
    assert ListSet(list_id=U1, tenant_id="team-a", kind="content").allow_enabled is False
    for kind, entries in (("content", ["topic:unknown"]), ("content", ["regex:.*"]), ("content", ["term:"]), ("provider", ["arbitrary-alias"]), ("content", ["term:x", "term:x"])):
        with pytest.raises(ValidationError):
            ListSet(list_id=U1, tenant_id="team-a", kind=kind, allow=entries)
    assert ListSet(list_id=U1, tenant_id="team-a", kind="content", deny=["term:private", "topic:medical"]).deny == ("term:private", "topic:medical")


def test_destination_rejects_credential_wildcard_and_noncanonical_routes():
    values = dict(destination_id=U1, tenant_id="team-a", name="service", host="service.local", path_prefix="/v2", cidrs=["10.0.0.0/24"], zone="local-shared", purpose="evaluation", tls_ca_ref="service-ca")
    for override in ({"host": "*.local"}, {"host": "user:pass@service.local"}, {"host": "UPPER.local"}, {"host": "127.0.0.1"}, {"host": "bad..local"}, {"scheme": "http"}, {"path_prefix": "/v2?secret=x"}, {"path_prefix": "/../admin"}, {"path_prefix": "/%2e%2e/admin"}, {"cidrs": ["10.0.0.1/24"]}):
        with pytest.raises(ValidationError):
            Destination.model_validate({**values, **override})
    assert Destination.model_validate(values).port == 443


def test_principal_scope_and_credential_session_lifetimes():
    with pytest.raises(ValidationError):
        Principal(principal_id=U1, tenant_id=None, roles=["workload"], display_name="invalid")
    with pytest.raises(ValidationError):
        Principal(principal_id=U1, tenant_id="team-a", roles=["platform_admin"], display_name="invalid")
    with pytest.raises(ValidationError):
        Credential(credential_id=U1, principal_id=U2, tenant_id="team-a", token_hash=ZERO_DIGEST, created_at=STAMP, expires_at="2026-10-12T00:00:00Z")
    with pytest.raises(ValidationError):
        AdminSession(session_id=U1, principal_id=U2, secret_hash=ZERO_DIGEST, csrf_hash=ZERO_DIGEST, created_at=STAMP, expires_at="2026-10-10T09:00:00Z", idle_expires_at="2026-10-10T00:30:00Z")
    assert Principal(principal_id=U1, tenant_id=None, roles=["platform_admin"], display_name="Platform").tenant_id is None


def test_unknown_policy_audit_failure_and_invalid_transform_cannot_allow():
    payload = dict(decision_id=U1, request_id=U2, tenant_id="team-a", workload_id="chat", stage="input", action="allow", would_be_action="allow", evaluation_ms=1.0, audit_status="persisted")
    with pytest.raises(ValidationError):
        Decision.model_validate(payload)
    policy = PolicyRef(tenant_id="team-a", policy_id="p", version=1, digest=ZERO_DIGEST)
    for overrides in ({"audit_status": "failed"}, {"action": "redact"}, {"transformed_parts": []}):
        with pytest.raises(ValidationError):
            Decision.model_validate({**payload, "policy": policy, **overrides})
    assert Decision.model_validate({**payload, "action": "block", "audit_status": "failed"}).action == "block"


def test_audit_allowlist_rejects_content_fields():
    values = dict(event_id=U1, tenant_id="team-a", event_type="policy.created", correlation_id=U2, occurred_at=STAMP, event_hash=ZERO_DIGEST)
    for metadata in ({"prompt": "SECRET_CANARY"}, {"tool_arguments": "SECRET_CANARY"}, {"reason": "SECRET_CANARY"}):
        with pytest.raises(ValidationError):
            AuditEvent(**values, metadata=metadata)
    assert AuditEvent(**values, metadata={"operation": "approve", "revision": 1}).raw_content_retained is False


def test_config_rejects_unknown_environment_and_nonfile_secrets(tmp_path):
    values = dict(mode="remote", database_url_file="/run/secrets/database", tls_cert_file="/run/tls/cert", tls_key_file="/run/tls/key", tls_ca_file="/run/tls/ca", runtime_id=U1)
    path = tmp_path / "service.json"
    path.write_text(json.dumps(values))
    assert load_config(path, {"GUARDRAILS_ZONE": "local-shared"}).text_budget_ms == 500
    for environment in ({"GUARDRAILS_TEXT_BUDGET_MS": "99999"}, {"GUARDRAILS_DATABASE_URL_FILE": "postgres://secret@db"}):
        with pytest.raises(EvolutionError) as caught:
            load_config(path, environment)
        assert caught.value.code == "validation_failed"
    path.write_text(json.dumps({**values, "audit_reserve_ms": 501}))
    with pytest.raises(EvolutionError):
        load_config(path, {})


def test_runtime_modes_have_bounded_online_defaults():
    values = dict(runtime_id=U1, tenant_id="team-a", zone="local-shared", public_key_id="key", version="1.0", telemetry="none")
    assert RuntimeInstall(**values, mode="remote").sync_interval_s == 1
    assert RuntimeInstall(**values, mode="customer").sync_interval_s == 30
    with pytest.raises(ValidationError):
        RuntimeInstall(**values, mode="remote", sync_interval_s=30)


def test_uuid_time_slugs_and_offsets_reject_noncanonical_values():
    for override in ({"tenant_id": "Team-A"}, {"quota_id": "not-a-uuid"}, {"created_at": "2026-10-10T00:00:00+00:00"}, {"version": 0}):
        with pytest.raises(ValidationError):
            Quota.model_validate({"quota_id": U1, "tenant_id": "team-a", **override})
    for interval in ({"start": 3, "end": 3}, {"start": 4, "end": 2}, {"start": 1}, {"score": float("nan")}):
        with pytest.raises(ValidationError):
            Finding(rule_id="r", category="pii", part_id="p", reason_code="pii_detected", evaluator_revision="pii-v2", **interval)
    assert Finding(rule_id="r", category="pii", part_id="p", reason_code="pii_detected", evaluator_revision="pii-v2", start=0, end=1).end == 1
