"""Versioned destination pins without rewriting historical approved bytes."""

import hashlib
import json

import pytest
from guardrails.evolution.compiler import PolicyCompiler, canonical_bytes, policy_digest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import Destination, ProviderProfile
from test_compiler import approved, changed, compiler_for, policy, resource, uid

LEGACY_DIGEST = "158d4e144751a0e1730f15ec5521efdf9c945ebc0ff7a54b57e187d5b034aa69"


def destination(number=301, owner="team-a", **updates):
    return resource(Destination, destination_id=uid(number), tenant_id=owner, name="Model endpoint", scheme="https", host="model.local", port=443, path_prefix="/api", cidrs=("10.2.0.0/16",), zone="local-shared", purpose="inference", tls_ca_ref="local-model-ca", **updates)


def network_policy(*, owner="team-a", target=None, **updates):
    target = target or destination(owner=owner)
    provider = resource(ProviderProfile, provider_id=uid(401), tenant_id=owner, kind="model", vendor="ollama", model_id="pinned-model", destination_id=target.destination_id, zone="local-shared")
    return policy(owner=owner, **{"providers": [provider], "resolved_destinations": [target], **updates})


def test_historical_compiler_bytes_keep_exact_golden_digest_and_new_compilation_upgrades():
    legacy = policy(compiler_version="compiler-v2.1", compiled_digest=LEGACY_DIGEST, status="approved")
    before = canonical_bytes(legacy)
    assert hashlib.sha256(before).hexdigest() == LEGACY_DIGEST
    assert "resolved_destinations" not in json.loads(before)
    PolicyCompiler().validate_dependencies(legacy)
    upgraded = PolicyCompiler().compile(policy(compiler_version="compiler-v2.1"))
    assert upgraded.compiler_version == "compiler-v2.2"
    assert json.loads(canonical_bytes(upgraded))["resolved_destinations"] == []
    assert upgraded.compiled_digest != LEGACY_DIGEST
    assert canonical_bytes(legacy) == before
    assert policy_digest(legacy) == LEGACY_DIGEST


def test_new_network_policy_hash_covers_exact_destination_bytes_and_version():
    one = approved(network_policy())
    updated = changed(destination(), version=2, host="replacement.local")
    from guardrails.evolution.models import with_resource_digest
    updated = with_resource_digest(updated)
    two = approved(network_policy(target=updated, version=2))
    assert one.compiled_digest != two.compiled_digest
    assert one.resolved_destinations[0].host == "model.local"
    assert two.resolved_destinations[0].host == "replacement.local"
    assert one.resolved_destinations[0].destination_id == two.resolved_destinations[0].destination_id
    changes = PolicyCompiler.diff(one, two)["changed_dependencies"]
    assert changes == [{"resource_id": uid(301), "old_digest": one.resolved_destinations[0].digest, "new_digest": updated.digest}]
    assert json.loads(canonical_bytes(one))["resolved_destinations"][0]["digest"] == one.resolved_destinations[0].digest


@pytest.mark.parametrize("case,code", [("missing", "destination_denied"), ("foreign", "resource_not_found"), ("stale", "validation_failed"), ("purpose", "destination_denied"), ("zone", "residency_denied"), ("duplicate", "validation_failed")])
def test_invalid_destination_pins_fail_compilation(case, code):
    target = destination()
    updates = {"resolved_destinations": []}
    if case == "foreign":
        target = destination(owner="team-b")
        updates = {"resolved_destinations": [target]}
    elif case == "stale":
        updates = {"resolved_destinations": [target.model_copy(update={"host": "mutated.local"})]}
    elif case in {"purpose", "zone"}:
        from guardrails.evolution.models import with_resource_digest
        target = with_resource_digest(changed(target, **({"purpose": "telemetry"} if case == "purpose" else {"zone": "external"})))
        updates = {"resolved_destinations": [target]}
    elif case == "duplicate":
        updates = {"resolved_destinations": [target, target]}
    raw = network_policy().model_dump(mode="json")
    raw.update(updates)
    with pytest.raises(EvolutionError) as denied:
        PolicyCompiler().compile(raw)
    assert denied.value.code == code


def test_inherited_destination_versions_conflict_and_same_pin_is_stable():
    parent = approved(network_policy())
    compiler = compiler_for(parent)
    inherited = compiler.compile(policy(2, parents=[parent.reference()]))
    assert inherited.resolved_destinations == parent.resolved_destinations
    assert inherited.providers == parent.providers
    from guardrails.evolution.models import with_resource_digest
    alternate = with_resource_digest(changed(destination(), version=2, cidrs=["10.2.3.0/24"]))
    with pytest.raises(EvolutionError) as conflict:
        compiler.compile(policy(2, parents=[parent.reference()], resolved_destinations=[alternate]))
    assert conflict.value.code == "composition_conflict"
    assert policy_digest(parent) == parent.compiled_digest


def test_platform_destination_bytes_require_authorized_parent_resolution():
    parent = approved(network_policy(owner="_platform"))
    compiled = compiler_for(parent).compile(policy(2, parents=[parent.reference()]))
    assert compiled.resolved_destinations == parent.resolved_destinations
    assert compiled.resolved_destinations[0].tenant_id == "_platform"
    with pytest.raises(EvolutionError) as foreign:
        PolicyCompiler().compile(policy(resolved_destinations=parent.resolved_destinations))
    assert foreign.value.code == "resource_not_found"


def test_legacy_unresolved_network_remains_readable_but_cannot_authorize_egress():
    modern = network_policy()
    legacy = changed(modern, compiler_version="compiler-v2.1", resolved_destinations=[])
    legacy = changed(legacy, compiled_digest=policy_digest(legacy), status="approved")
    before = canonical_bytes(legacy)
    with pytest.raises(EvolutionError) as denied:
        PolicyCompiler().validate_dependencies(legacy)
    assert denied.value.code == "destination_denied"
    assert canonical_bytes(legacy) == before
    # A newly approved child must explicitly supply the formerly missing pin.
    child = compiler_for(legacy).compile(policy(2, parents=[legacy.reference()], resolved_destinations=[destination()]))
    assert child.compiler_version == "compiler-v2.2"
    assert child.resolved_destinations == (destination(),)
    with pytest.raises(EvolutionError) as smuggled:
        canonical_bytes(changed(legacy, resolved_destinations=[destination()]))
    assert smuggled.value.code == "validation_failed"
