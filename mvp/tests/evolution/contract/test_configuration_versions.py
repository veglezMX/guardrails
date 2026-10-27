"""Immutable configuration version workflows over HTTPS and real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from guardrails.evolution import models
from guardrails.evolution.compiler import canonical_bytes
from guardrails.evolution.management_api import RESOURCES, ManagementApi, create_app

from test_management import system, headers, reference


CONFIGURATIONS = {
    "lists": ("author", {"kind": "content", "deny": ["term:first"], "allow_enabled": False}, {"deny": ["term:second"]}),
    "residency": ("admin", {"allowed_zones": ["local-shared"], "metadata_retention_days": 30}, {"metadata_retention_days": 14}),
    "destinations": ("admin", {"name": "inference", "host": "model.internal.test", "path_prefix": "/v1", "cidrs": ["10.0.0.0/24"], "zone": "local-shared", "purpose": "inference", "tls_ca_ref": "test-ca"}, {"path_prefix": "/v2"}),
    "providers": ("admin", {"kind": "evaluator", "vendor": "local_rules", "model_id": "pii-v2", "zone": "local-shared", "enabled": True}, {"enabled": False}),
    "tools": ("author", {"name": "calculator", "argument_schema": {"type": "object", "properties": {"term": {"type": "string"}}, "required": ["term"], "additionalProperties": False}}, {"sensitive_paths": ["/term"]}),
    "quotas": ("admin", {"requests_per_minute": 60, "burst": 10, "max_inflight": 4}, {"requests_per_minute": 120}),
}


def stored(tx, family, resource_id, version):
    row = tx.execute("SELECT data, canonical_bytes, immutable FROM evolution.records WHERE tenant_id=:tenant AND kind=:kind AND key=:key", {"tenant": tx.tenant_id, "kind": "config:" + family, "key": f"{resource_id}:{version}"}).one()
    return row.data, bytes(row.canonical_bytes), row.immutable


@pytest.mark.parametrize("family", tuple(CONFIGURATIONS))
def test_configuration_versions_are_immutable_replayable_and_linked(system, family):
    repo, service, tenant, identities, tokens, payload, client = system
    role, original, change = CONFIGURATIONS[family]
    model_name, id_name = RESOURCES[family]
    base = f"/v2/tenants/{tenant}/{family}"
    created = client.post(base, json=original, headers=headers(tokens, role))
    assert created.status_code == 201, created.text
    resource_id = created.json()[id_name]
    assert created.headers["Location"] == f"{base}/{resource_id}/versions/1"
    with repo.transaction(tenant) as tx:
        first = stored(tx, family, resource_id, 1)
    command = {**original, **change}
    route, authorization = f"{base}/{resource_id}/versions", headers(tokens, role)
    revised = client.post(route, json=command, headers=authorization)
    assert revised.status_code == 201, revised.text
    assert revised.json()[id_name] == resource_id
    assert revised.json()["version"] == 2
    assert revised.json()["digest"] != created.json()["digest"]
    assert revised.headers["Location"] == f"{route}/2"
    replay = client.post(route, json=command, headers=authorization)
    assert replay.status_code == 201 and replay.json() == revised.json()
    assert replay.headers["Location"] == revised.headers["Location"]
    conflict = client.post(route, json=original, headers=authorization)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    third = client.post(route, json=original, headers=headers(tokens, role))
    assert third.status_code == 201 and third.json()["version"] == 3
    assert client.get(created.headers["Location"], headers=headers(tokens, "viewer")).json() == created.json()
    assert client.get(revised.headers["Location"], headers=headers(tokens, "viewer")).json() == revised.json()
    with repo.transaction(tenant) as tx:
        assert stored(tx, family, resource_id, 1) == first
        for version in (1, 2, 3):
            data, encoded, immutable = stored(tx, family, resource_id, version)
            assert immutable is True
            assert encoded == models.canonical_json_bytes(data)
            assert models.resource_digest(getattr(models, model_name).model_validate_json(encoded)) == data["digest"]
        revisions = tx.page("config:" + family, filters={id_name: resource_id}, limit=100)
        assert {row["data"]["version"] for row in revisions} == {1, 2, 3}
        assert tx.list("bindings") == [] and tx.list("approvals") == [] and tx.list("outbox") == []
        assert len([event for event in tx.list("audit") if event["metadata"].get("resource_id") == resource_id]) == 3


def test_concurrent_configuration_versions_allocate_distinct_contiguous_versions(system):
    repo, service, tenant, identities, tokens, payload, client = system
    original = CONFIGURATIONS["lists"][1]
    created = client.post(f"/v2/tenants/{tenant}/lists", json=original, headers=headers(tokens, "author")).json()
    route = f"/v2/tenants/{tenant}/lists/{created['list_id']}/versions"

    def create(number, authorization=None):
        with TestClient(create_app(service=service), base_url="https://management.test") as worker:
            return worker.post(route, json={**original, "deny": [f"term:concurrent-{number}"]}, headers=authorization or headers(tokens, "author"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    assert [result.status_code for result in results] == [201] * 4
    assert {result.json()["version"] for result in results} == {2, 3, 4, 5}
    assert len({result.json()["digest"] for result in results}) == 4
    shared = headers(tokens, "author")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [future.result() for future in (pool.submit(create, 5, shared), pool.submit(create, 5, shared))]
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json() and first.json()["version"] == 6
    assert first.headers["Location"] == second.headers["Location"]
    with repo.transaction(tenant) as tx:
        rows = tx.page("config:lists", filters={"list_id": created["list_id"]}, limit=100)
        assert len(rows) == 6
        assert stored(tx, "lists", created["list_id"], 1)[0]["deny"] == ["term:first"]


@pytest.mark.parametrize("family", tuple(CONFIGURATIONS))
def test_configuration_version_scope_roles_and_server_fields_are_enforced(system, family):
    repo, service, tenant, identities, tokens, payload, client = system
    role, original, change = CONFIGURATIONS[family]
    _, id_name = RESOURCES[family]
    base = f"/v2/tenants/{tenant}/{family}"
    created = client.post(base, json=original, headers=headers(tokens, role)).json()
    route = f"{base}/{created[id_name]}/versions"
    denied = client.post(route, json=original, headers=headers(tokens, "viewer"))
    assert denied.status_code == 403
    wrong_role = "admin" if role == "author" else "author"
    forbidden_role = client.post(route, json=original, headers=headers(tokens, wrong_role))
    assert forbidden_role.status_code == 403
    for field, value in ((id_name, str(uuid4())), ("tenant_id", tenant), ("version", 55), ("digest", "0" * 64), ("created_at", models.utc_now()), ("revision", 4)):
        response = client.post(route, json={**original, field: value}, headers=headers(tokens, role))
        assert response.status_code == 422
    unknown = client.post(f"{base}/{uuid4()}/versions", json=original, headers=headers(tokens, role))
    assert unknown.status_code == 404
    malformed_id = client.post(f"{base}/%25/versions", json=original, headers=headers(tokens, role))
    assert malformed_id.status_code == 404
    malformed_body = client.post(route, json={**original, "unknown": "not permitted"}, headers=headers(tokens, role))
    assert malformed_body.status_code == 422
    foreign = "foreign-" + uuid4().hex[:16]
    outside = client.post(f"/v2/tenants/{foreign}/{family}/{created[id_name]}/versions", json=original, headers=headers(tokens, role))
    assert outside.status_code == 404
    with repo.transaction(tenant) as tx:
        rows = tx.page("config:" + family, filters={id_name: created[id_name]}, limit=100)
        assert len(rows) == 1 and rows[0]["data"]["version"] == 1


def test_foreign_configuration_identity_cannot_be_versioned_in_local_tenant(system):
    repo, service, tenant, identities, tokens, payload, client = system
    other = "foreign-" + uuid4().hex[:16]
    quota = models.with_resource_digest(models.Quota(quota_id=str(uuid4()), tenant_id=other))
    residency = models.with_resource_digest(models.ResidencyProfile(residency_id=str(uuid4()), tenant_id=other))
    repo.provision_tenant(models.Tenant(tenant_id=other, display_name="Other", residency_id=residency.residency_id, quota_id=quota.quota_id), residency, quota)
    response = client.post(f"/v2/tenants/{tenant}/quotas/{quota.quota_id}/versions", json={"requests_per_minute": 120}, headers=headers(tokens, "admin"))
    assert response.status_code == 404
    with repo.transaction(other) as tx:
        assert stored(tx, "quotas", quota.quota_id, 1)[0]["requests_per_minute"] == 60
        assert tx.get("config:quotas", f"{quota.quota_id}:2") is None
    with repo.transaction(tenant) as tx:
        assert tx.get("config:quotas", f"{quota.quota_id}:1") is None
        assert tx.get("config:quotas", f"{quota.quota_id}:2") is None


def test_dependency_version_requires_new_policy_review_and_preserves_active_bytes(system):
    repo, service, tenant, identities, tokens, payload, client = system
    # Use a supported Phase 2 restriction; active content controls are admitted
    # only once their evaluator is installed. Both pinned provider denies execute.
    original = {"kind": "provider", "allow_enabled": False, "allow": [], "deny": [str(uuid4())]}
    created = client.post(f"/v2/tenants/{tenant}/lists", json=original, headers=headers(tokens, "author")).json()
    residency = client.post(f"/v2/tenants/{tenant}/residency/{payload['residency_ref']['id']}/versions", json={"allowed_zones": ["local-shared"], "provider_lists": [created["list_id"]]}, headers=headers(tokens, "admin"))
    assert residency.status_code == 201, residency.text
    command = {**payload, "residency_ref": {"id": residency.json()["residency_id"], "version": 2, "digest": residency.json()["digest"]}, "list_refs": [{"id": created["list_id"], "version": 1, "digest": created["digest"]}]}
    response = client.post(f"/v2/tenants/{tenant}/policies/policy/versions", json=command, headers=headers(tokens, "author"))
    assert response.status_code == 201, response.text
    candidate = response.json()
    approval = client.post(f"/v2/tenants/{tenant}/policies/policy/versions/1/reviews", json={"compiled_digest": candidate["compiled_digest"], "reason": "Review old dependency", "outcome": "approve"}, headers=headers(tokens, "approver"))
    assert approval.status_code == 200
    release = client.post(f"/v2/tenants/{tenant}/releases", json={"policy": reference(candidate), "workload_ids": ["chat"], "stage": "canary", "state": "observe", "expected_revisions": {"chat": 0}, "reason": "Observe approved version"}, headers=headers(tokens, "operator"))
    assert release.status_code == 202
    with repo.transaction(tenant) as tx:
        old_bytes = canonical_bytes(models.PolicyVersion.model_validate_json(models.canonical_json_bytes(tx.get("policies", "policy:1"))))
        old_binding = tx.get("bindings", "chat:1")
        old_pointer = tx.get("binding_pointers", "chat")
    updated = client.post(f"/v2/tenants/{tenant}/lists/{created['list_id']}/versions", json={**original, "deny": [*original["deny"], str(uuid4())]}, headers=headers(tokens, "author")).json()
    new_command = {**command, "list_refs": [{"id": created["list_id"], "version": 2, "digest": updated["digest"]}]}
    revised = client.post(f"/v2/tenants/{tenant}/policies/policy/versions", json=new_command, headers=headers(tokens, "author")).json()
    assert revised["status"] == "candidate" and revised["compiled_digest"] != candidate["compiled_digest"]
    premature = client.post(f"/v2/tenants/{tenant}/releases", json={"policy": reference(revised), "workload_ids": ["chat"], "stage": "canary", "state": "observe", "expected_revisions": {"chat": 1}, "reason": "Unreviewed dependency"}, headers=headers(tokens, "operator"))
    assert premature.status_code == 409 and premature.json()["error"]["code"] == "approval_required"
    with repo.transaction(tenant) as tx:
        assert canonical_bytes(models.PolicyVersion.model_validate_json(models.canonical_json_bytes(tx.get("policies", "policy:1")))) == old_bytes
        assert tx.get("bindings", "chat:1") == old_binding
        assert tx.get("binding_pointers", "chat") == old_pointer
        assert tx.get("applied_bindings", "chat") is None
        assert len(tx.list("approvals")) == 1
        assert service.governance.eligible(tx, reference(candidate))["resolved_lists"][0]["version"] == 1
    reviewed = client.post(f"/v2/tenants/{tenant}/policies/policy/versions/2/reviews", json={"compiled_digest": revised["compiled_digest"], "reason": "Review new dependency", "outcome": "approve"}, headers=headers(tokens, "approver"))
    assert reviewed.status_code == 200
    with repo.transaction(tenant) as tx:
        assert service.governance.eligible(tx, reference(revised))["resolved_lists"][0]["version"] == 2
        assert tx.get("binding_pointers", "chat") == old_pointer


def test_configuration_version_openapi_reuses_strict_create_shape_and_location():
    document = create_app(service=ManagementApi(object())).openapi()
    for family, (model_name, id_name) in RESOURCES.items():
        create = document["paths"][f"/v2/tenants/{{t}}/{family}"]["post"]
        version = document["paths"][f"/v2/tenants/{{t}}/{family}/{{id}}/versions"]["post"]
        assert create["requestBody"] == version["requestBody"]
        assert "Location" in create["responses"]["201"]["headers"]
        assert "Location" in version["responses"]["201"]["headers"]
        assert version["responses"]["201"]["content"]["application/json"]["schema"] == {"$ref": f"#/components/schemas/{model_name}"}
        schema = document["components"]["schemas"][model_name + "Create"]
        assert schema["additionalProperties"] is False
        assert not {id_name, "version", "digest", "tenant_id", "created_at", "revision"} & set(schema["properties"])
