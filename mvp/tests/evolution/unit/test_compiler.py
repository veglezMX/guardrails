"""Compiler tests exercise immutable bytes, resource references and floor order."""

import hashlib
import json
from uuid import UUID

import pytest
from hypothesis import given, strategies as st

from guardrails.evolution.compiler import PolicyCompiler, canonical_bytes, effective_lists, policy_digest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import ListSet, PolicyRef, PolicyVersion, Quota, ResidencyProfile, Rule, ZERO_DIGEST, with_resource_digest

STAMP = "2026-10-10T00:00:00Z"


def uid(number):
    return str(UUID(int=number))


def resource(cls, **values):
    return with_resource_digest(cls(created_at=STAMP, **values))


def rule(identifier="pii", **changes):
    values = dict(rule_id=identifier, kind="pii", stages=["input", "output"], action="block", mandatory=True, minimum_state="block", config={"entities": ["email"]}, evaluator_revision="pii-v2")
    return Rule.model_validate({**values, **changes})


def policy(number=1, owner="team-a", **changes):
    values = dict(tenant_id=owner, policy_id=f"policy-{number}", version=1, author_id=uid(1), created_at=STAMP, rules=[rule()], residency=resource(ResidencyProfile, tenant_id=owner, residency_id=uid(100 + number)), quota=resource(Quota, tenant_id=owner, quota_id=uid(200 + number)))
    return PolicyVersion.model_validate({**values, **changes})


def approved(candidate, compiler=None):
    compiled = (compiler or PolicyCompiler()).compile(candidate)
    return PolicyVersion.model_validate({**compiled.model_dump(mode="json"), "status": "approved"})


def compiler_for(*policies):
    registry = {(p.tenant_id, p.policy_id, p.version): p for p in policies}
    return PolicyCompiler(resolver=lambda ref: registry[(ref.tenant_id, ref.policy_id, ref.version)], baseline_resolver=lambda ref: registry[(ref.tenant_id, ref.policy_id, ref.version)])


def changed(model, **changes):
    return type(model).model_validate({**model.model_dump(mode="json"), **changes})


def test_canonical_digest_is_exact_immutable_and_lifecycle_independent():
    candidate = policy(rules=[rule("b"), rule("a")])
    compiled = PolicyCompiler().compile(candidate)
    second = PolicyCompiler().compile(changed(candidate, rules=list(reversed(candidate.rules))))
    assert compiled.compiled_digest == hashlib.sha256(canonical_bytes(compiled)).hexdigest()
    assert compiled.compiled_digest == second.compiled_digest
    assert canonical_bytes(compiled) == canonical_bytes(changed(compiled, status="approved"))
    content = json.loads(canonical_bytes(compiled))
    assert content["schema_version"] == "guardrails-v2"
    assert "status" not in content and "compiled_digest" not in content
    assert content["rules"][0]["rule_id"] == "a"
    assert content["residency"]["digest"] == compiled.residency.digest
    assert compiled.rules[0].rule_id == "a"


@given(st.permutations(["email", "international_phone", "payment_card", "ipv4"]))
def test_detector_set_order_cannot_change_compiled_digest(entities):
    baseline = policy(rules=[rule(config={"entities": ["email", "international_phone", "payment_card", "ipv4"]})])
    alternate = changed(baseline, rules=[rule(config={"entities": list(entities)})])
    assert PolicyCompiler().compile(baseline).compiled_digest == PolicyCompiler().compile(alternate).compiled_digest


def test_stale_dependency_digest_and_mutated_model_copy_reject():
    candidate = policy()
    stale = candidate.residency.model_copy(update={"allowed_zones": ("external",)})
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler().compile(candidate.model_copy(update={"residency": stale}))
    assert caught.value.code == "validation_failed"
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler().compile(candidate.model_copy(update={"version": -1}))
    assert caught.value.code == "validation_failed"


def test_parent_resolution_inherits_mandatory_rules_and_preserves_bytes():
    baseline = approved(policy())
    candidate = policy(2, parents=[baseline.reference()], rules=[])
    compiler = compiler_for(baseline)
    compiled = compiler.compile(candidate)
    assert compiled.rules == baseline.rules
    assert compiled.parents == (baseline.reference(),)
    assert candidate.rules == ()
    assert canonical_bytes(baseline) == canonical_bytes(approved(policy()))


def test_removal_weakened_floor_and_incomparable_override_reject():
    baseline = approved(policy())
    compiler = compiler_for(baseline)
    for weaker in (changed(rule(), mandatory=False), changed(rule(), minimum_state="observe"), changed(rule(), action="warn"), changed(rule(), stages=["input"]), changed(rule(), config={"entities": ["ipv4"]})):
        with pytest.raises(EvolutionError) as caught:
            compiler.compile(policy(2, parents=[baseline.reference()], rules=[weaker]))
        assert caught.value.code == "composition_conflict"
    missing = PolicyCompiler().compile(policy(2, rules=[]))
    with pytest.raises(EvolutionError) as caught:
        compiler.validate_current(missing, baseline)
    assert caught.value.code == "floor_conflict"
    assert compiler.compatible(missing, baseline) is False


@given(st.integers(min_value=1, max_value=59))
def test_quota_strengthening_is_monotonic(limit):
    baseline = approved(policy())
    child = policy(2, parents=[baseline.reference()])
    quota = with_resource_digest(changed(child.quota, requests_per_minute=limit))
    compiled = compiler_for(baseline).compile(changed(child, quota=quota))
    assert compiled.quota.requests_per_minute <= baseline.quota.requests_per_minute
    assert compiler_for(baseline).compatible(compiled, baseline)


def test_quota_residency_and_fail_open_weakenings_reject():
    baseline = approved(policy())
    child = policy(2, parents=[baseline.reference()])
    alternatives = [changed(child, quota=with_resource_digest(changed(child.quota, burst=100))), changed(child, residency=with_resource_digest(changed(child.residency, allowed_zones=["local-shared", "external"]))), changed(child, residency=with_resource_digest(changed(child.residency, external_processing=True))), changed(child, failure_behavior="observe")]
    for candidate in alternatives:
        with pytest.raises(EvolutionError) as caught:
            compiler_for(baseline).compile(candidate)
        assert caught.value.code == "floor_conflict"


def test_cycles_depth_missing_and_cross_tenant_references_reject():
    candidate = policy()
    cyclic = changed(candidate, parents=[candidate.reference()])
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler().compile(cyclic)
    assert caught.value.code == "composition_conflict"
    foreign = approved(policy(owner="team-b"))
    with pytest.raises(EvolutionError) as caught:
        compiler_for(foreign).compile(policy(2, parents=[foreign.reference()]))
    assert caught.value.code == "resource_not_found"
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler().compile(policy(2, parents=[candidate.reference()]))
    assert caught.value.code == "resource_not_found"
    chain = [approved(policy())]
    for number in range(2, 5):
        chain.append(approved(policy(number, parents=[chain[-1].reference()]), compiler_for(*chain)))
    with pytest.raises(EvolutionError) as caught:
        compiler_for(*chain).compile(policy(5, parents=[chain[-1].reference()]))
    assert caught.value.code == "composition_conflict"


def test_platform_parent_requires_authorized_publication_port():
    baseline = approved(policy(owner="_platform"))
    candidate = policy(2, parents=[baseline.reference()])
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler(resolver=lambda _: baseline).compile(candidate)
    assert caught.value.code == "resource_not_found"
    assert compiler_for(baseline).compile(candidate).rules == baseline.rules


def test_rejected_revoked_and_stale_parent_bytes_reject():
    baseline = approved(policy())
    child = policy(2, parents=[baseline.reference()])
    for status, code in (("candidate", "approval_required"), ("rejected", "approval_required"), ("revoked", "policy_revoked")):
        with pytest.raises(EvolutionError) as caught:
            compiler_for(changed(baseline, status=status)).compile(child)
        assert caught.value.code == code
    altered = changed(baseline, rules=[])
    with pytest.raises(EvolutionError) as caught:
        compiler_for(altered).compile(child)
    assert caught.value.code == "validation_failed"


def test_allow_intersections_deny_union_and_empty_intersection_reject():
    first = resource(ListSet, list_id=uid(301), tenant_id="team-a", kind="content", allow=["term:alpha", "term:beta"], deny=["term:private"], allow_enabled=True)
    second = resource(ListSet, list_id=uid(302), tenant_id="team-a", kind="content", allow=["term:alpha", "term:gamma"], deny=["term:secret"], allow_enabled=True)
    allow, deny = effective_lists((first, second), "content")
    assert allow == frozenset({"term:alpha"})
    assert deny == frozenset({"term:private", "term:secret"})
    candidate = policy()
    residency = with_resource_digest(changed(candidate.residency, content_lists=[first.list_id, second.list_id]))
    compiled = PolicyCompiler().compile(changed(candidate, resolved_lists=[second, first], residency=residency))
    assert compiled.resolved_lists == (first, second)
    denied = with_resource_digest(changed(second, deny=["term:alpha"]))
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler().compile(changed(candidate, resolved_lists=[first, denied], residency=residency))
    assert caught.value.code == "composition_conflict"
    empty = with_resource_digest(changed(first, allow=[]))
    with pytest.raises(EvolutionError):
        PolicyCompiler().compile(changed(candidate, resolved_lists=[empty, second], residency=residency))


def test_dependency_versions_cannot_be_replaced_across_layers():
    first = resource(ListSet, list_id=uid(301), tenant_id="team-a", kind="content", deny=["term:first"])
    candidate = policy()
    residency = with_resource_digest(changed(candidate.residency, content_lists=[first.list_id]))
    baseline = approved(changed(candidate, resolved_lists=[first], residency=residency))
    second = with_resource_digest(changed(first, version=2, deny=["term:second"]))
    child = policy(2, parents=[baseline.reference()], resolved_lists=[second])
    child = changed(child, residency=with_resource_digest(changed(child.residency, content_lists=[first.list_id])))
    with pytest.raises(EvolutionError) as caught:
        compiler_for(baseline).compile(child)
    assert caught.value.code == "composition_conflict"


def test_missing_list_and_unregistered_evaluator_reject():
    candidate = policy()
    missing = with_resource_digest(changed(candidate.residency, provider_lists=[uid(300)]))
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler().compile(changed(candidate, residency=missing))
    assert caught.value.code == "validation_failed"
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler().compile(changed(candidate, rules=[changed(rule(), evaluator_revision="uninstalled-v1")]))
    assert caught.value.code == "capability_missing"


def test_untransformable_engine_never_accepts_redaction():
    injection = Rule(rule_id="injection", kind="injection", stages=["input"], action="redact", config={}, evaluator_revision="deberta-v2")
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler(registered_revisions={"injection": {"deberta-v2"}}).compile(policy(rules=[injection]))
    assert caught.value.code == "unsafe_transformation"


def test_diff_reports_ids_and_digests_without_content_samples():
    before = PolicyCompiler().compile(policy())
    content = resource(ListSet, list_id=uid(301), tenant_id="team-a", kind="content", deny=["term:SECRET_CANARY"])
    residency = with_resource_digest(changed(before.residency, content_lists=[content.list_id]))
    after = PolicyCompiler().compile(changed(before, version=2, rules=[rule("new")], residency=residency, resolved_lists=[content]))
    diff = PolicyCompiler.diff(before, after)
    assert diff["changed_rule_ids"] == ["new", "pii"]
    assert diff["changed_dependencies"]
    assert "SECRET_CANARY" not in json.dumps(diff)
    assert diff["old_digest"] != diff["new_digest"]
