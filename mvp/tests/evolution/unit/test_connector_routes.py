"""Exact installed route pins cannot follow mutable IDs or alias remapping."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from connector.evolution_routes import ApprovedModelRoute, LocalService, RouteManifest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import (
    Destination,
    PolicyRef,
    ProviderProfile,
    with_resource_digest,
)
from pydantic import ValidationError


def route_fixture():
    destination = with_resource_digest(Destination(destination_id=str(uuid4()), tenant_id="team-a", name="model", host="model.local", port=8443, path_prefix="/api", cidrs=("10.2.3.4/32",), zone="local-shared", purpose="inference", tls_ca_ref="model-ca"))
    provider = with_resource_digest(ProviderProfile(provider_id=str(uuid4()), tenant_id="team-a", kind="model", vendor="ollama", model_id="qwen2.5:0.5b", destination_id=destination.destination_id, zone="local-shared"))
    policy = PolicyRef(tenant_id="team-a", policy_id="policy", version=1, digest="a" * 64)
    route = ApprovedModelRoute(policy=policy, provider=provider, destination=destination)
    manifest = RouteManifest(tenant_id="team-a", workload_id="chat", routes=(route,), ca_files={"model-ca": "/trusted/ca.pem"}, local_services={destination.destination_id: LocalService(hostname="model.local", addresses=("10.2.3.4",))})
    settings = SimpleNamespace(tenant_id="team-a", workload_id="chat", provider_id=provider.provider_id,
        model_base_url="https://model.local:8443", model_route="ollama_chat/qwen2.5:0.5b", profile="local_model")
    return manifest, route, settings


def test_installed_route_requires_exact_policy_identity_and_endpoint_before_dispatch():
    manifest, route, settings = route_fixture()
    decision = SimpleNamespace(policy=route.policy)
    assert manifest.select(decision, settings) == route
    for field, value in (("tenant_id", "team-b"), ("workload_id", "other"), ("provider_id", str(uuid4())),
                         ("model_base_url", "https://remapped.local:8443"), ("model_base_url", "https://model.local:9443"),
                         ("model_base_url", "https://model.local:8443/wrong"), ("model_route", "ollama_chat/other")):
        changed = SimpleNamespace(**(vars(settings) | {field: value}))
        with pytest.raises(EvolutionError):
            manifest.select(decision, changed)
    for update in ({"digest": "b" * 64}, {"version": 2}, {"tenant_id": "team-b"}):
        with pytest.raises(EvolutionError):
            manifest.select(SimpleNamespace(policy=route.policy.model_copy(update=update)), settings)


def test_route_manifest_rejects_tampered_bytes_and_untrusted_file(tmp_path):
    manifest, route, settings = route_fixture()
    path = tmp_path / "routes.json"
    path.write_text(manifest.model_dump_json())
    path.chmod(0o600)
    assert RouteManifest.from_file(path).select(SimpleNamespace(policy=route.policy), settings) == route
    raw = route.model_dump(mode="json")
    raw["destination"]["host"] = "changed.local"
    with pytest.raises(ValidationError):
        ApprovedModelRoute.model_validate(raw)
    path.chmod(0o666)
    with pytest.raises(ValueError):
        RouteManifest.from_file(path)
    path.chmod(0o600)
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    with pytest.raises(OSError):
        RouteManifest.from_file(alias)
