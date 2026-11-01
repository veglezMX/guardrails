"""Stateless eligibility only: network transport remains separately gated."""

from uuid import uuid4

import pytest
from guardrails.evolution.compiler import policy_digest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import (
    ContentPart,
    Destination,
    EvaluationRequest,
    ListSet,
    PolicyVersion,
    ProviderProfile,
    Quota,
    ResidencyProfile,
    ToolCall,
    ToolSpec,
    with_resource_digest,
)
from guardrails.evolution.residency import ResidencyGuard


def seal(policy):
    return policy.model_copy(update={"compiled_digest": policy_digest(policy)})


@pytest.fixture
def case():
    tenant = "residency-test"
    provider = with_resource_digest(
        ProviderProfile(
            tenant_id=tenant,
            provider_id=str(uuid4()),
            version=1,
            kind="model",
            vendor="local_rules",
            model_id="deterministic-fixture",
            zone="local-shared",
        )
    )
    provider_list = with_resource_digest(
        ListSet(
            tenant_id=tenant,
            list_id=str(uuid4()),
            version=1,
            kind="provider",
            allow=(provider.provider_id,),
            deny=(),
            allow_enabled=True,
        )
    )
    residency = with_resource_digest(
        ResidencyProfile(
            tenant_id=tenant,
            residency_id=str(uuid4()),
            version=1,
            provider_lists=(provider_list.list_id,),
        )
    )
    quota = with_resource_digest(
        Quota(tenant_id=tenant, quota_id=str(uuid4()), version=1)
    )
    policy = seal(
        PolicyVersion(
            tenant_id=tenant,
            policy_id="approved",
            version=1,
            author_id=str(uuid4()),
            rules=(),
            residency=residency,
            quota=quota,
            providers=(provider,),
            resolved_lists=(provider_list,),
        )
    )
    request = EvaluationRequest(
        request_id=str(uuid4()),
        correlation_id=str(uuid4()),
        tenant_id=tenant,
        workload_id="chat",
        stage="input",
        parts=(
            ContentPart(
                part_id="text", kind="text", role="user", text="Synthetic text"
            ),
        ),
        provider_id=provider.provider_id,
        deadline_ms=500,
    )
    workload = {
        "tenant_id": tenant,
        "workload_id": "chat",
        "status": "active",
        "mode": "remote",
        "provider_ids": [provider.provider_id],
        "allowed_stages": ["input", "output", "tool_arguments", "tool_result"],
    }
    return policy, request, workload


def check(case, *, zone="local-shared", mode="remote"):
    policy, request, workload = case
    return ResidencyGuard().check_request(
        policy, request, runtime_zone=zone, mode=mode, workload=workload
    )


def provider_change(case, **fields):
    policy, request, workload = case
    provider = with_resource_digest(policy.providers[0].model_copy(update=fields))
    return seal(policy.model_copy(update={"providers": (provider,)})), request, workload


def list_change(case, **fields):
    policy, request, workload = case
    changed = with_resource_digest(policy.resolved_lists[0].model_copy(update=fields))
    return (
        seal(policy.model_copy(update={"resolved_lists": (changed,)})),
        request,
        workload,
    )


def rejected(case, code, **options):
    with pytest.raises(EvolutionError) as failure:
        check(case, **options)
    assert failure.value.code == code


def test_exact_local_provider_and_disabled_allow_remain_subject_to_deny(case):
    assert check(case) is None
    relaxed = list_change(case, allow=(), allow_enabled=False)
    assert check(relaxed) is None
    denied = list_change(relaxed, deny=(case[0].providers[0].provider_id,))
    rejected(denied, "provider_denied")


@pytest.mark.parametrize("change", ["empty", "missing", "deny_wins", "intersection"])
def test_provider_lists_are_fail_closed_and_deny_wins(case, change):
    policy, request, workload = case
    if change == "empty":
        case = list_change(case, allow=())
    elif change == "missing":
        profile = with_resource_digest(
            policy.residency.model_copy(update={"provider_lists": ()})
        )
        case = (
            seal(
                policy.model_copy(update={"residency": profile, "resolved_lists": ()})
            ),
            request,
            workload,
        )
    elif change == "deny_wins":
        case = list_change(case, deny=(request.provider_id,))
    else:
        second = with_resource_digest(
            policy.resolved_lists[0].model_copy(
                update={"list_id": str(uuid4()), "allow": (str(uuid4()),)}
            )
        )
        profile = with_resource_digest(
            policy.residency.model_copy(
                update={
                    "provider_lists": (*policy.residency.provider_lists, second.list_id)
                }
            )
        )
        case = (
            seal(
                policy.model_copy(
                    update={
                        "residency": profile,
                        "resolved_lists": (*policy.resolved_lists, second),
                    }
                )
            ),
            request,
            workload,
        )
    rejected(case, "provider_denied")


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("enabled", False, "provider_denied"),
        ("kind", "evaluator", "provider_denied"),
        ("zone", "foreign-zone", "residency_denied"),
        ("tenant_id", "other-tenant", "forbidden"),
        ("vendor", "local_classifier", "capability_missing"),
    ],
)
def test_provider_cannot_change_scope_or_fallback(case, field, value, code):
    rejected(provider_change(case, **{field: value}), code)


def test_runtime_zone_workload_registration_and_mode_are_trusted(case):
    rejected(case, "residency_denied", zone="request-declared-zone")
    rejected(case, "forbidden", mode="customer")
    policy, request, workload = case
    for fields, code in [
        ({"provider_ids": []}, "provider_denied"),
        ({"status": "suspended"}, "forbidden"),
        ({"tenant_id": "other"}, "forbidden"),
        ({"workload_id": "other"}, "forbidden"),
        ({"allowed_stages": []}, "unsupported_surface"),
    ]:
        rejected((policy, request, {**workload, **fields}), code)


def test_missing_provider_foreign_policy_and_tampered_resource_fail(case):
    policy, request, workload = case
    rejected(
        (seal(policy.model_copy(update={"providers": ()})), request, workload),
        "provider_denied",
    )
    rejected(
        (policy.model_copy(update={"tenant_id": "other"}), request, workload),
        "forbidden",
    )
    changed = policy.providers[0].model_copy(
        update={"model_id": "changed-without-resource-digest"}
    )
    rejected(
        (seal(policy.model_copy(update={"providers": (changed,)})), request, workload),
        "policy_unresolved",
    )
    rejected(
        (policy.model_copy(update={"compiled_digest": "0" * 64}), request, workload),
        "policy_unresolved",
    )


def test_registered_allowed_network_destination_still_requires_approved_pins(case):
    policy, request, workload = case
    destination_id = str(uuid4())
    provider = with_resource_digest(
        policy.providers[0].model_copy(
            update={"vendor": "ollama", "destination_id": destination_id}
        )
    )
    destinations = with_resource_digest(
        ListSet(
            tenant_id=policy.tenant_id,
            list_id=str(uuid4()),
            version=1,
            kind="destination",
            allow=(destination_id,),
            deny=(),
            allow_enabled=True,
        )
    )
    profile = with_resource_digest(
        policy.residency.model_copy(
            update={
                "destination_lists": (destinations.list_id,),
                "external_processing": True,
            }
        )
    )
    policy = seal(
        policy.model_copy(
            update={
                "providers": (provider,),
                "residency": profile,
                "resolved_lists": (*policy.resolved_lists, destinations),
            }
        )
    )
    rejected((policy, request, workload), "destination_denied")


def tool_case(case, *, stage="tool_arguments"):
    policy, request, workload = case
    tool = with_resource_digest(
        ToolSpec(
            tenant_id=policy.tenant_id,
            tool_id=str(uuid4()),
            version=1,
            name="lookup",
            argument_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
            result_schema={
                "type": "object",
                "properties": {"found": {"type": "boolean"}},
                "required": ["found"],
                "additionalProperties": False,
            },
        )
    )
    allowed = with_resource_digest(
        ListSet(
            tenant_id=policy.tenant_id,
            list_id=str(uuid4()),
            version=1,
            kind="tool",
            allow=(tool.name,),
            deny=(),
            allow_enabled=True,
        )
    )
    profile = with_resource_digest(
        policy.residency.model_copy(update={"tool_lists": (allowed.list_id,)})
    )
    policy = seal(
        policy.model_copy(
            update={
                "tools": (tool,),
                "residency": profile,
                "resolved_lists": (*policy.resolved_lists, allowed),
            }
        )
    )
    call = ToolCall(
        call_id="call",
        name="lookup",
        arguments={"id": "synthetic"},
        tool_digest=tool.digest,
        result_for_call_id="call" if stage == "tool_result" else None,
    )
    part = ContentPart(
        part_id="tool",
        kind="json",
        role="tool",
        value={"found": True} if stage == "tool_result" else call.arguments,
        tool=call,
    )
    request = request.model_copy(
        update={"stage": stage, "context_id": str(uuid4()), "parts": (part,)}
    )
    return policy, request, workload


@pytest.mark.parametrize("stage", ["tool_arguments", "tool_result"])
def test_tool_identity_and_schema_checked_before_dispatch(case, stage):
    case = tool_case(case, stage=stage)
    assert check(case) is None
    policy, request, workload = case
    part = request.parts[0]
    for fields in (
        {"name": "Lookup"},
        {"tool_digest": "0" * 64},
        {"arguments": {"unapproved": "value"}},
    ):
        call = part.tool.model_copy(update=fields)
        rejected(
            (
                policy,
                request.model_copy(
                    update={"parts": (part.model_copy(update={"tool": call}),)}
                ),
                workload,
            ),
            "tool_denied",
        )
    rejected(
        (
            policy,
            request.model_copy(
                update={
                    "parts": (part.model_copy(update={"value": {"different": True}}),)
                }
            ),
            workload,
        ),
        "tool_denied",
    )
    if stage == "tool_result":
        call = part.tool.model_copy(update={"result_for_call_id": None})
        rejected(
            (
                policy,
                request.model_copy(
                    update={"parts": (part.model_copy(update={"tool": call}),)}
                ),
                workload,
            ),
            "context_invalid",
        )


def test_tool_deny_wins_and_missing_structured_identity_rejects(case):
    policy, request, workload = tool_case(case)
    lists = (
        *policy.resolved_lists[:-1],
        with_resource_digest(
            policy.resolved_lists[-1].model_copy(update={"deny": ("lookup",)})
        ),
    )
    rejected(
        (seal(policy.model_copy(update={"resolved_lists": lists})), request, workload),
        "tool_denied",
    )
    part = request.parts[0].model_copy(update={"tool": None})
    rejected(
        (policy, request.model_copy(update={"parts": (part,)}), workload), "tool_denied"
    )


def pinned_case(case):
    policy, request, workload = case
    destination = with_resource_digest(
        Destination(
            tenant_id=policy.tenant_id,
            destination_id=str(uuid4()),
            version=1,
            name="approved-model",
            host="model.test",
            path_prefix="/api",
            cidrs=("10.1.2.3/32",),
            zone="local-shared",
            purpose="inference",
            tls_ca_ref="operator-ca",
        )
    )
    provider = with_resource_digest(
        policy.providers[0].model_copy(
            update={"vendor": "ollama", "destination_id": destination.destination_id}
        )
    )
    destination_list = with_resource_digest(
        ListSet(
            tenant_id=policy.tenant_id,
            list_id=str(uuid4()),
            version=1,
            kind="destination",
            allow=(destination.destination_id,),
            deny=(),
            allow_enabled=True,
        )
    )
    residency = with_resource_digest(
        policy.residency.model_copy(
            update={"destination_lists": (destination_list.list_id,)}
        )
    )
    policy = seal(
        policy.model_copy(
            update={
                "compiler_version": "compiler-v2.2",
                "providers": (provider,),
                "resolved_destinations": (destination,),
                "residency": residency,
                "resolved_lists": (*policy.resolved_lists, destination_list),
            }
        )
    )
    return policy, request, workload


def test_exact_pinned_destination_returns_approved_bytes_without_lookup(case):
    case = pinned_case(case)
    assert check(case) is None
    policy, request, _ = case
    route = ResidencyGuard().authorize_provider(
        policy, request.provider_id, runtime_zone="local-shared", purpose="inference"
    )
    assert route.destination == policy.resolved_destinations[0]
    assert route.provider == policy.providers[0]
    assert route.external_processing is False
    assert route.destination.digest == policy.resolved_destinations[0].digest


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "destination_denied"),
        ("purpose", "destination_denied"),
        ("zone", "residency_denied"),
        ("tenant", "forbidden"),
        ("digest", "policy_unresolved"),
        ("denied", "destination_denied"),
        ("unlisted", "destination_denied"),
    ],
)
def test_network_pin_scope_digest_purpose_and_deny_are_independent(case, change, code):
    policy, request, workload = pinned_case(case)
    destination = policy.resolved_destinations[0]
    if change == "missing":
        policy = policy.model_copy(update={"resolved_destinations": ()})
    elif change == "denied":
        deny = with_resource_digest(
            policy.resolved_lists[-1].model_copy(
                update={"deny": (destination.destination_id,)}
            )
        )
        policy = policy.model_copy(
            update={"resolved_lists": (*policy.resolved_lists[:-1], deny)}
        )
    elif change == "unlisted":
        residency = with_resource_digest(
            policy.residency.model_copy(update={"destination_lists": ()})
        )
        policy = policy.model_copy(update={"residency": residency})
    else:
        fields = {
            "purpose": {"purpose": "evaluation"},
            "zone": {"zone": "other"},
            "tenant": {"tenant_id": "other"},
            "digest": {"port": 444},
        }[change]
        destination = destination.model_copy(update=fields)
        if change != "digest":
            destination = with_resource_digest(destination)
        policy = policy.model_copy(update={"resolved_destinations": (destination,)})
    policy = seal(policy)
    rejected((policy, request, workload), code)
    with pytest.raises(EvolutionError) as failure:
        ResidencyGuard().authorize_provider(
            policy,
            request.provider_id,
            runtime_zone="local-shared",
            purpose="inference",
        )
    assert failure.value.code == code


def test_authorize_provider_rejects_wrong_purpose_zone_and_modified_policy(case):
    policy, request, _ = pinned_case(case)
    guard = ResidencyGuard()
    for fields, options, code in [
        ({}, {"purpose": "evaluation"}, "provider_denied"),
        ({}, {"purpose": "telemetry"}, "destination_denied"),
        ({}, {"runtime_zone": "undeclared"}, "residency_denied"),
        ({"compiled_digest": "0" * 64}, {}, "policy_unresolved"),
    ]:
        with pytest.raises(EvolutionError) as failure:
            guard.authorize_provider(
                policy.model_copy(update=fields),
                request.provider_id,
                **{"runtime_zone": "local-shared", "purpose": "inference", **options},
            )
        assert failure.value.code == code
