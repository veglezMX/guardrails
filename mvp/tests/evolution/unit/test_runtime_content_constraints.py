"""Policy-wide content constraints run even without authored detector rules."""

import pytest
from guardrails.evolution.compiler import PolicyCompiler
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import Finding, ListSet, with_resource_digest
from test_compiler import policy, resource, rule, uid
from test_runtime_execution_profiles import exercise, fixture_runtime


def fixture(*, state="block", failure=None, authored=False, transform_failure=False):
    service, _, records = fixture_runtime(state=state, rules=[rule()] if authored else [])
    selected = resource(ListSet, tenant_id="team-a", list_id=uid(990),
                        kind="content", deny=("term:synthetic",))
    candidate = policy(rules=[rule()] if authored else [], resolved_lists=(selected,))
    residency = with_resource_digest(candidate.residency.model_copy(
        update={"content_lists": (selected.list_id,)}))
    candidate = PolicyCompiler().compile(candidate.model_copy(update={"residency": residency}))
    original = service._policy

    def resolve(*args, **kwargs):
        snapshot, binding, _ = original(*args, **kwargs)
        return snapshot, binding, candidate

    service._policy = resolve
    records.content = []
    records.transformed = []

    async def content(policy, parts, deadline, *, stage, tenant_id):
        assert policy == candidate and stage == "input" and tenant_id == "team-a"
        records.content.append((parts, deadline))
        if failure:
            raise EvolutionError(failure)
        finding = Finding(
            rule_id="content-list:" + selected.list_id, part_id="body", category="term",
            start=0, end=9, reason_code="content_denied", evaluator_revision="lexical-v1",
        )
        # A repeated identical port result cannot inflate final findings.
        return finding, finding

    async def transform(policy, parts, findings, deadline, *, stage, tenant_id):
        assert policy == candidate and stage == "input" and tenant_id == "team-a"
        records.transformed.append((findings, deadline))
        if transform_failure:
            raise EvolutionError("unsafe_transformation")
        return (parts[0].model_copy(update={"text": "[REDACTED:term]"}),)

    service.registry.evaluate_content_lists = content
    service.registry.transform = transform
    return service, records


@pytest.mark.parametrize("state,action", [
    ("observe", "allow"), ("warn", "warn"), ("redact", "redact"), ("block", "block"),
])
def test_zero_authored_rules_still_enforce_each_binding_state(state, action):
    service, records = fixture(state=state)
    response = exercise(service)
    assert response.status_code == 200
    body = response.json()
    assert body["action"] == action and body["would_be_action"] == "block"
    assert body["reason_codes"] == ["content_denied"]
    assert body["evaluator_revisions"] == ["lexical-v1"]
    assert len(body["findings"]) == len(records.content) == 1
    assert body["findings"][0]["rule_id"] == "content-list:" + uid(990)
    assert records.detectors == [] and len(records.finalized) == 1
    assert len(records.transformed) == int(state == "redact")
    if state == "redact":
        assert body["transformed_parts"][0]["text"] == "[REDACTED:term]"


def test_disabled_keeps_admission_and_never_runs_content_constraints():
    service, records = fixture(state="disabled")
    response = exercise(service)
    assert response.status_code == 200
    assert response.json()["action"] == "allow"
    assert response.json()["reason_codes"] == ["disabled"]
    assert records.content == records.detectors == records.transformed == []
    assert records.order == ["lock", "eligible", "reserve", "commit"]
    assert len(records.finalized) == 1


@pytest.mark.parametrize("failure", [
    "evaluation_timeout", "evaluator_unavailable", "inspection_incomplete", "queue_full",
])
def test_failed_policy_constraint_is_audited_block_even_in_observe(failure):
    service, records = fixture(state="observe", failure=failure)
    response = exercise(service)
    assert response.status_code == 200
    assert response.json()["action"] == response.json()["would_be_action"] == "block"
    assert response.json()["reason_codes"] == [failure]
    assert len(records.content) == len(records.finalized) == 1
    assert response.json()["findings"] == []


def test_clean_explicit_detector_cannot_clear_policy_constraint():
    service, records = fixture(authored=True)
    response = exercise(service)
    assert response.status_code == 200
    assert response.json()["action"] == "block"
    assert len(records.content) == len(records.detectors) == 1
    assert response.json()["evaluator_revisions"] == ["lexical-v1", "pii-v2"]
    assert len(response.json()["findings"]) == 1


def test_failed_constraint_mask_never_releases_partial_transform():
    service, records = fixture(state="redact", transform_failure=True)
    response = exercise(service)
    assert response.status_code == 200
    assert response.json()["action"] == "block"
    assert response.json()["transformed_parts"] is None
    assert response.json()["reason_codes"] == ["content_denied", "unsafe_transformation"]
    assert len(records.transformed) == len(records.finalized) == 1
