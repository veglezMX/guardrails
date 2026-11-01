"""Operator-only credential hydration across actual management safe_view."""
import copy

import pytest
from evolution.evaluator_governance import (
    checked_policy,
    operator_provider,
    payload_from_approved,
)
from guardrails.evolution import models
from guardrails.evolution.compiler import PolicyCompiler
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.management_api import safe_view
from test_evaluator_governance import PolicyPort, Port, extra_rule, journey, versions
from test_evaluator_governance import base as base_fixture

base = base_fixture


def recompiled(base, provider, destination=None):
    body = {**base, "status": "candidate", "providers": [base["providers"][0], provider],
            "resolved_destinations": [base["resolved_destinations"][0], destination or base["resolved_destinations"][1]]}
    model = models.PolicyVersion.model_validate_json(models.canonical_json_bytes(body))
    return PolicyCompiler().compile(model).model_copy(update={"status": "approved"}).model_dump(mode="json")


def private_old(base):
    parsed = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(base["providers"][1]))
    provider = models.with_resource_digest(parsed.model_copy(update={"credential_ref": "previous-operator-reference"}))
    return recompiled(base, provider.model_dump(mode="json"))


def test_actual_safe_view_v1_read_and_v2_create_restore_only_submitted_private_reference(base):
    base = private_old(base)
    port = Port(base)
    wires = []
    def safe_port(*args, **kwargs):
        value = safe_view(port(*args, **kwargs))
        wires.append(copy.deepcopy(value))
        return value
    result = versions(journey(safe_port), base)
    assert all("credential_ref" not in row for row in wires)
    assert result["provider"]["credential_ref"] == "classifier-shared-operator"
    parsed = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(result["provider"]))
    assert result["provider"]["digest"] == models.resource_digest(parsed)
    assert sum(call[0] == "POST" for call in port.calls) == 2
    assert base["providers"][1]["credential_ref"] == "previous-operator-reference"


def setup_candidate(base):
    new = versions(journey(Port(base)), base)
    selected = recompiled(base, new["provider"], new["destination"])
    payload = payload_from_approved(selected, extra_rule())
    return new, selected, payload


def test_actual_safe_view_submit_and_approved_get_keep_canonical_policy_digest(base):
    new, selected, payload = setup_candidate(base)
    port = PolicyPort(selected)
    safe_responses = []
    def safe_port(*args, **kwargs):
        value = safe_view(port(*args, **kwargs))
        safe_responses.append(copy.deepcopy(value))
        return value
    approved = journey(safe_port, operator_providers=(new["provider"],)).approve(payload)
    assert "credential_ref" not in safe_responses[0]["providers"][1]
    assert "credential_ref" not in safe_responses[-1]["policy"]["providers"][1]
    assert approved["providers"][1]["credential_ref"] == new["provider"]["credential_ref"]
    assert approved["compiled_digest"] == port.candidate["compiled_digest"]
    assert safe_view(approved)["providers"] == safe_responses[-1]["policy"]["providers"]
    assert [entry[0] for entry in port.calls] == ["POST", "POST", "GET"]


@pytest.mark.parametrize("fault", ("missing-pin", "different-version", "different-id", "different-digest",
                                    "conflicting-reference", "changed-untrusted-field"))
def test_safe_policy_rejects_unknown_or_conflicting_operator_material_before_review(base, fault):
    new, selected, payload = setup_candidate(base)
    pin = new["provider"]
    port = PolicyPort(selected)
    def safe_port(*args, **kwargs):
        value = safe_view(port(*args, **kwargs))
        if "providers" in value:
            target = value["providers"][1]
            if fault == "different-version":
                target["version"] += 1
            elif fault == "different-id":
                target["provider_id"] = "00000000-0000-0000-0000-000000000999"
            elif fault == "different-digest":
                target["digest"] = "0" * 64
            elif fault == "conflicting-reference":
                target["credential_ref"] = "untrusted-reference"
            elif fault == "changed-untrusted-field":
                target["model_id"] = "substituted-model"
        return value
    with pytest.raises((EvolutionError, ValueError)):
        journey(safe_port, operator_providers=() if fault == "missing-pin" else (pin,)).approve(payload)
    assert len(port.calls) == 1 and port.calls[0][0] == "POST"


@pytest.mark.parametrize("field", ("tenant_id", "provider_id", "version", "digest", "credential_ref", "model_id"))
def test_original_resource_hydration_cannot_adopt_changed_identity_or_other_fields(base, field):
    pin = private_old(base)["providers"][1]
    response = safe_view(pin)
    response[field] = {"tenant_id": "team-b", "provider_id": "00000000-0000-0000-0000-000000000999",
                       "version": 2, "digest": "0" * 64, "credential_ref": "other", "model_id": "other"}[field]
    with pytest.raises((EvolutionError, ValueError)):
        operator_provider(response, credential_ref=pin["credential_ref"], expected=pin)


def test_approved_safe_policy_read_uses_exact_operator_pin_without_changing_digest(base):
    new, selected, _ = setup_candidate(base)
    actual = checked_policy(safe_view(selected), "team-a", status="approved", operator_providers=(new["provider"],))
    assert actual == selected
