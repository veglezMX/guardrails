"""Foundation properties that protect later publication and connector code."""
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from guardrails.local_contract import (
    CheckSpec, ClientConfig, Decision, DomainValidationError, Evaluation,
    FailureBehavior, Finding, GovernanceFloor, Policy, PolicyDraft, ProofConfig,
    TextBatch, TextSegment, canonical_bytes,
)


def failures():
    return FailureBehavior(**{name: "block" for name in ("evaluation_timeout", "evaluation_unavailable", "policy_unresolved", "management_unavailable", "audit_failure", "transformation_failure")})


def draft():
    return PolicyDraft(tenant_id="local-proof", policy_id="pii", version="v1", checks=[CheckSpec(check_id="pii", evaluator="local_pii", stages=["input", "output"], pii_types=["email", "phone"], requested_action="redact")], failure_behavior=failures(), retain_content=False)


def decision(**updates):
    values = dict(decision_id=uuid4(), evaluation_id=uuid4(), correlation_id=uuid4(), context_id=None, tenant_id="local-proof", workload_id="team-a", stage="input", policy_id=None, policy_version=None, policy_digest=None, binding_revision=None, snapshot_generation=None, enforcement_state=None, action="allow", would_be_action="allow", reason_codes=(), operational_codes=(), guardrail_category=None, evaluator_metadata=(), audit_id=uuid4(), findings=(), transformed_content=None, evaluation_ms=0.1, source="runtime", audit_status="persisted", client_config=None)
    values.update(updates)
    return Decision(**values)


def test_policy_defensively_copies_mutable_inputs_and_rejects_mutation():
    stages = ["input", "output"]
    checks = [CheckSpec(check_id="pii", evaluator="local_pii", stages=stages, pii_types=["email"], requested_action="redact")]
    policy = PolicyDraft(tenant_id="local-proof", policy_id="pii", version="v1", checks=checks, failure_behavior=failures(), retain_content=False)
    digest = policy.content_digest()
    stages.clear()
    checks.clear()
    assert policy.checks[0].stages == ("input", "output")
    assert policy.content_digest() == digest
    with pytest.raises(FrozenInstanceError):
        policy.checks[0].requested_action = "log_only"
    with pytest.raises(FrozenInstanceError):
        policy.failure_behavior.audit_failure = "allow_and_log"


def test_nested_floor_mapping_is_copied_and_read_only():
    ordinals = {"pii": 2}
    floor = GovernanceFloor(scope="organization", tenant_id=None, revision=1, require_binding=True, required_stages=("input",), required_pii_types=("email",), minimum_action="redact", minimum_state="observe", required_policy_id=None, minimum_policy_ordinals=ordinals, require_fail_closed=("audit_failure",), forbid_content_retention=True)
    ordinals["pii"] = 0
    assert floor.minimum_policy_ordinals["pii"] == 2
    with pytest.raises(TypeError):
        floor.minimum_policy_ordinals["pii"] = 0


def test_digest_is_canonical_and_excludes_publication_metadata():
    original = draft()
    fields = {name: getattr(original, name) for name in original.__dataclass_fields__}
    first = Policy(**fields, publication_ordinal=1, published_at=datetime.now(timezone.utc), published_by=uuid4(), digest=original.content_digest())
    second = replace(first, publication_ordinal=3, published_by=uuid4())
    assert first.digest == second.digest
    assert canonical_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert replace(original, retain_content=True).content_digest() != original.content_digest()
    with pytest.raises(DomainValidationError):
        replace(first, digest="0" * 64)


@pytest.mark.parametrize("value", ["false", 0, None])
def test_retention_requires_an_actual_boolean(value):
    with pytest.raises(DomainValidationError):
        replace(draft(), retain_content=value)


def test_batch_tracks_unicode_code_points_without_cross_message_matching():
    batch = TextBatch(segments=[TextSegment(segment_id="0", text="☃ab"), TextSegment(segment_id="1", text="c")])
    finding = Finding(check_id="pii", segment_id="0", reason_code="GR-003:local_pii:email", start=1, end=3)
    finding.validate_content(batch)
    with pytest.raises(DomainValidationError):
        replace(finding, end=4).validate_content(batch)
    with pytest.raises(DomainValidationError):
        replace(finding, segment_id="2").validate_content(batch)


@pytest.mark.parametrize("segments", [[], [TextSegment(segment_id="0", text="a"), TextSegment(segment_id="0", text="b")], [TextSegment(segment_id="0", text="☃" * 349526)]])
def test_batch_rejects_empty_duplicate_and_utf8_byte_overflow(segments):
    with pytest.raises(DomainValidationError):
        TextBatch(segments=segments)


@pytest.mark.parametrize("action,allowed", [("allow", True), ("log_only", True), ("block", False)])
def test_allowed_is_derived_not_a_second_authority(action, allowed):
    result = decision(action=action)
    assert result.allowed is allowed
    assert result.to_wire()["allowed"] is allowed
    assert result.to_wire()["transformed_content"] is None


def test_redaction_requires_complete_segment_identity_and_never_echoes_original():
    content = TextBatch(segments=[TextSegment(segment_id="0", text="secret"), TextSegment(segment_id="1", text="safe")])
    changed = TextBatch(segments=[TextSegment(segment_id="0", text="[REDACTED]")])
    with pytest.raises(DomainValidationError):
        decision(action="redact")
    with pytest.raises(DomainValidationError):
        decision(transformed_content=changed)
    with pytest.raises(DomainValidationError):
        decision(action="redact", transformed_content=changed).validate_content(content)
    assert "secret" not in json.dumps(decision(action="block").to_wire())


def test_fallback_identity_and_audit_status_cannot_fabricate_runtime_evidence():
    with pytest.raises(DomainValidationError):
        decision(source="connector")
    fallback = decision(source="connector", evaluation_id=None, audit_status="spooled", audit_id=None)
    assert fallback.to_wire()["evaluation_id"] is None
    with pytest.raises(DomainValidationError):
        replace(fallback, audit_id=uuid4())
    with pytest.raises(DomainValidationError):
        decision(audit_id=None)


def test_reason_namespaces_and_timings_are_validated():
    with pytest.raises(DomainValidationError):
        decision(reason_codes=("evaluation_timeout",))
    with pytest.raises(DomainValidationError):
        decision(evaluation_ms=float("nan"))
    with pytest.raises(DomainValidationError):
        decision(evaluation_ms=True)
    with pytest.raises(DomainValidationError):
        decision(reason_codes=("GR-003:local_pii:email",), guardrail_category=None)


def test_reserved_stage_and_context_requirements_are_explicit():
    values = dict(evaluation_id=uuid4(), tenant_id="local-proof", workload_id="team-a", stage="input", content=TextBatch(segments=[TextSegment(segment_id="0", text="")]), correlation_id=uuid4(), context_id=None, received_at=datetime.now(timezone.utc))
    Evaluation(**values)
    with pytest.raises(DomainValidationError, match="unsupported_surface"):
        Evaluation(**{**values, "stage": "tool_call"})
    with pytest.raises(DomainValidationError):
        Evaluation(**{**values, "stage": "output"})
    with pytest.raises(DomainValidationError):
        Evaluation(**{**values, "context_id": "unexpected"})


def test_configuration_does_not_coerce_numeric_boolean_or_erase_audit_reserve():
    with pytest.raises(DomainValidationError):
        ProofConfig(workers=True)
    with pytest.raises(DomainValidationError):
        ProofConfig(audit_reserve_ms=500)
    assert ProofConfig().max_http_bytes == 8388608
