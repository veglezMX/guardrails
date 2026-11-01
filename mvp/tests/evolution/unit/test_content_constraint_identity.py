"""Policy-wide constraint identity cannot be impersonated by an authored rule."""
import pytest
from guardrails.evolution.compiler import PolicyCompiler, policy_digest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import ListSet, with_resource_digest
from test_compiler import policy, resource, rule, uid


def listed(*, enabled=False, deny=(), rule_id=None):
    listing=resource(ListSet, tenant_id="team-a", list_id=uid(940), kind="content",
                     allow_enabled=enabled, allow=("term:approved",) if enabled else (), deny=deny)
    base=policy(rules=[] if rule_id is None else [rule(rule_id)], resolved_lists=[listing])
    residency=with_resource_digest(base.residency.model_copy(update={"content_lists": (listing.list_id,)}))
    return base.model_copy(update={"residency": residency})


@pytest.mark.parametrize("controls", [{"enabled": True}, {"deny": ("term:private",)}])
@pytest.mark.parametrize("identifier", ["content-list:" + uid(940), "content-list:other"])
def test_active_content_constraints_reserve_attribution_namespace(controls,identifier):
    candidate=listed(rule_id=identifier, **controls)
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler().compile(candidate)
    assert caught.value.code=="validation_failed"
    # An otherwise correctly hashed old/submitted object cannot bypass
    # eligibility validation merely by supplying its own compiled digest.
    forged=candidate.model_copy(update={"compiled_digest":policy_digest(candidate)})
    with pytest.raises(EvolutionError) as caught:
        PolicyCompiler().validate_dependencies(forged)
    assert caught.value.code=="validation_failed"


def test_policy_wide_lists_compile_without_any_detector_rule():
    compiled=PolicyCompiler().compile(listed(deny=("term:private",)))
    assert compiled.rules==()
    assert compiled.residency.content_lists==(uid(940),)
    assert compiled.compiled_digest==policy_digest(compiled)


def test_inactive_list_does_not_rewrite_legacy_authored_rule_identity():
    compiled=PolicyCompiler().compile(listed(rule_id="content-list:legacy"))
    assert compiled.rules[0].rule_id=="content-list:legacy"
    assert compiled.compiled_digest==policy_digest(compiled)

