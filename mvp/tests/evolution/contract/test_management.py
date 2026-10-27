"""Behavioral management/governance checks using a real non-owner PostgreSQL role."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from guardrails.evolution import models
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.management_api import ManagementApi, create_app
from guardrails.evolution.storage import TenantRepository


@pytest.fixture
def system():
    repo = TenantRepository(Path(os.environ["EVOLUTION_TEST_DSN_FILE"]).read_text().strip())
    service = ManagementApi(repo)
    tenant = "test-" + uuid4().hex[:20]
    quota = models.with_resource_digest(models.Quota(quota_id=str(uuid4()), tenant_id=tenant, version=1))
    residency = models.with_resource_digest(models.ResidencyProfile(residency_id=str(uuid4()), tenant_id=tenant, version=1, allowed_zones=("local-shared",)))
    model = models.Tenant(tenant_id=tenant, display_name="Test tenant", status="active", baseline=None, residency_id=residency.residency_id, quota_id=quota.quota_id)
    repo.provision_tenant(model, residency, quota)
    identities, tokens = {}, {}
    with repo.transaction(tenant) as tx:
        for role, roles in {"author": ("author", "approver"), "approver": ("approver",), "operator": ("operator",), "admin": ("tenant_admin",), "auditor": ("auditor",), "viewer": ("viewer",)}.items():
            actor = service.identity.provision_principal(tx, role, roles)
            tokens[role] = service.identity.issue_credential(tx, actor["principal_id"])["one_time_secret"]
        workload = models.Workload(tenant_id=tenant, workload_id="chat", display_name="Chat", mode="remote", status="active", allowed_stages=("input", "output"), provider_ids=(), quota_id=quota.quota_id, required_state="observe")
        tx.insert("workloads", "chat", workload)
    identities = {role: service.identity.authenticate(token) for role, token in tokens.items()}
    payload = {"parents": [], "rules": [{"rule_id": "email", "kind": "pii", "stages": ["input", "output"], "action": "block", "mandatory": True, "minimum_state": "observe", "config": {"entities": ["email"]}, "evaluator_revision": "pii-v2"}],
               "residency_ref": {"id": residency.residency_id, "version": 1, "digest": residency.digest},
               "quota_ref": {"id": quota.quota_id, "version": 1, "digest": quota.digest}}
    with TestClient(create_app(service=service), base_url="https://management.test") as client:
        yield repo, service, tenant, identities, tokens, payload, client
    repo.close()


def headers(tokens, role, **extra):
    return {"Authorization": "Bearer " + tokens[role], "Idempotency-Key": str(uuid4()), **extra}


def submit(system):
    repo, service, tenant, identities, tokens, payload, client = system
    with repo.transaction(tenant) as tx:
        return service.governance.submit(tx, identities["author"], "policy", payload)


def approve(system, candidate):
    repo, service, tenant, identities, *_ = system
    with repo.transaction(tenant) as tx:
        return service.governance.review(tx, identities["approver"], "policy", candidate["version"], outcome="approve", compiled_digest=candidate["compiled_digest"], reason="Reviewed")


def reference(candidate):
    return {"tenant_id": candidate["tenant_id"], "policy_id": candidate["policy_id"], "version": candidate["version"], "digest": candidate["compiled_digest"]}


def test_self_approval_rejected_for_second_credential_and_transaction_unchanged(system):
    repo, service, tenant, identities, *_ = system
    candidate = submit(system)
    with repo.transaction(tenant) as tx:
        second = service.identity.issue_credential(tx, identities["author"].principal_id)
    identity = service.identity.authenticate(second["one_time_secret"])
    with pytest.raises(EvolutionError, match="Self approval"):
        with repo.transaction(tenant) as tx:
            service.governance.review(tx, identity, "policy", 1, outcome="approve", compiled_digest=candidate["compiled_digest"], reason="Same principal")
    with repo.transaction(tenant) as tx:
        assert tx.list("approvals") == []
        assert service.governance.get(tx, "policy", 1)["status"] == "candidate"
        assert tx.get("policies", "policy:1")["compiled_digest"] == candidate["compiled_digest"]


def test_review_race_is_serialized_and_revocation_is_final(system):
    repo, service, tenant, identities, *_ = system
    candidate = submit(system)
    def review(outcome):
        try:
            with repo.transaction(tenant) as tx:
                service.governance.review(tx, identities["approver"], "policy", 1, outcome=outcome, compiled_digest=candidate["compiled_digest"], reason="Concurrent review")
            return outcome
        except EvolutionError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(review, ("approve", "reject")))
    assert results.count("revision_conflict") == 1
    with repo.transaction(tenant) as tx:
        assert len(tx.list("approvals")) == 1
        final = service.governance.get(tx, "policy", 1)["status"]
    if final == "approved":
        with repo.transaction(tenant) as tx:
            service.governance.review(tx, identities["approver"], "policy", 1, outcome="revoke", compiled_digest=candidate["compiled_digest"], reason="Revoked")
        assert review("approve") == "policy_revoked"
    else:
        assert review("approve") == "revision_conflict"


def test_activation_requires_approval_digest_and_current_floor(system):
    repo, service, tenant, identities, *_ = system
    candidate = submit(system)
    kwargs = dict(policy=reference(candidate), workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": 0}, reason="Canary")
    with pytest.raises(EvolutionError) as rejected:
        with repo.transaction(tenant) as tx:
            service.bindings.release(tx, identities["operator"], **kwargs)
    assert rejected.value.code == "approval_required"
    approve(system, candidate)
    with repo.transaction(tenant) as tx:
        release = service.bindings.release(tx, identities["operator"], **kwargs)
        assert release["status"] == "pending"
        assert tx.get("applied_bindings", "chat") is None
    with pytest.raises(EvolutionError) as stale:
        with repo.transaction(tenant) as tx:
            service.bindings.release(tx, identities["operator"], **kwargs)
    assert stale.value.code == "revision_conflict"
    with pytest.raises(EvolutionError) as floor:
        with repo.transaction(tenant) as tx:
            service.bindings.release(tx, identities["operator"], **{**kwargs, "state": "disabled", "expected_revisions": {"chat": 1}})
    assert floor.value.code == "floor_conflict"


def test_rollout_cannot_promote_without_canary_or_rollback_revoked_policy(system):
    repo, service, tenant, identities, *_ = system
    candidate = submit(system)
    approve(system, candidate)
    kwargs = dict(policy=reference(candidate), workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": 0}, reason="Canary")
    with repo.transaction(tenant) as tx:
        service.bindings.release(tx, identities["operator"], **kwargs)
    with pytest.raises(EvolutionError):
        with repo.transaction(tenant) as tx:
            service.bindings.release(tx, identities["operator"], **{**kwargs, "stage": "full", "expected_revisions": {"chat": 1}, "canary_evidence_id": str(uuid4())})
    with repo.transaction(tenant) as tx:
        service.governance.review(tx, identities["approver"], "policy", 1, outcome="revoke", compiled_digest=candidate["compiled_digest"], reason="Revoked")
    with pytest.raises(EvolutionError) as rejected:
        with repo.transaction(tenant) as tx:
            service.bindings.rollback(tx, identities["operator"], "chat", target_revision=1, expected_revision=1, reason="Rollback")
    assert rejected.value.code == "policy_revoked"
    with repo.transaction(tenant) as tx:
        assert len(tx.list("bindings")) == 1
        assert tx.get("applied_bindings", "chat") is None


def test_http_auth_scope_strict_json_and_safe_errors(system):
    repo, service, tenant, identities, tokens, payload, client = system
    path = f"/v2/tenants/{tenant}/policies/policy/versions"
    response = client.post(path, content='{"secret-canary":', headers={"Content-Type": "application/json"})
    assert response.status_code == 401
    foreign = client.post("/v2/tenants/foreign/policies/policy/versions", json=payload, headers=headers(tokens, "author"))
    assert foreign.status_code == 404
    duplicate = client.post(path, content='{"rules":[],"rules":[],"canary":"secret-canary"}', headers=headers(tokens, "author", **{"Content-Type": "application/json"}))
    assert duplicate.status_code == 400
    assert "secret-canary" not in duplicate.text
    invalid = client.post(path, json={**payload, "secret-canary": "not-retained"}, headers=headers(tokens, "author"))
    assert invalid.status_code == 422
    with repo.transaction(tenant) as tx:
        assert tx.list("policies") == []


def test_http_idempotency_and_stale_workload_revision(system):
    repo, service, tenant, identities, tokens, payload, client = system
    path = f"/v2/tenants/{tenant}/policies/policy/versions"
    h = headers(tokens, "author")
    first = client.post(path, json=payload, headers=h)
    assert first.status_code == 201, first.text
    repeated = client.post(path, json=payload, headers=h)
    assert repeated.status_code == 201
    assert first.json() == repeated.json()
    changed = deepcopy(payload)
    changed["rules"][0]["action"] = "redact"
    conflict = client.post(path, json=changed, headers=h)
    assert conflict.status_code == 409
    workload_path = f"/v2/tenants/{tenant}/workloads/chat"
    assert client.patch(workload_path, json={"display_name": "New"}, headers=headers(tokens, "admin")).status_code == 428
    assert client.patch(workload_path, json={"display_name": "New"}, headers=headers(tokens, "admin", **{"If-Match": "1"})).status_code == 200
    assert client.patch(workload_path, json={"display_name": "Stale"}, headers=headers(tokens, "admin", **{"If-Match": "1"})).status_code == 409
    with repo.transaction(tenant) as tx:
        assert len(tx.list("policies")) == 1
        assert tx.get("workloads", "chat")["display_name"] == "New"


def test_http_cursor_binds_scope_and_viewer_cannot_read_audit(system):
    repo, service, tenant, identities, tokens, payload, client = system
    submit(system)
    with repo.transaction(tenant) as tx:
        service.governance.submit(tx, identities["author"], "second-policy", payload)
    path = f"/v2/tenants/{tenant}/policies?limit=1"
    page = client.get(path, headers=headers(tokens, "viewer"))
    assert page.status_code == 200
    assert len(page.json()["items"]) == 1
    cursor = page.json()["next_cursor"]
    assert cursor
    next_page = client.get(path + "&cursor=" + cursor, headers=headers(tokens, "viewer"))
    assert next_page.status_code == 200
    assert next_page.json()["items"][0]["policy_id"] != page.json()["items"][0]["policy_id"]
    assert client.get(path + "&cursor=" + cursor + "x", headers=headers(tokens, "viewer")).status_code == 400
    assert client.get(f"/v2/tenants/{tenant}/audit", headers=headers(tokens, "viewer")).status_code == 403
    audit = client.get(f"/v2/tenants/{tenant}/audit", headers=headers(tokens, "auditor"))
    assert audit.status_code == 200
    assert len(audit.json()["items"]) >= 2


def test_current_stricter_baseline_blocks_old_approval_and_rollback(system):
    repo, service, tenant, identities, tokens, payload, client = system
    old = submit(system)
    approve(system, old)
    with repo.transaction(tenant) as tx:
        service.bindings.release(tx, identities["operator"], policy=reference(old), workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": 0}, reason="Old approved policy")
    stronger = deepcopy(payload)
    stronger["rules"][0]["minimum_state"] = "block"
    with repo.transaction(tenant) as tx:
        current = service.governance.submit(tx, identities["author"], "stricter", stronger)
    with repo.transaction(tenant) as tx:
        service.governance.review(tx, identities["approver"], "stricter", 1, outcome="approve", compiled_digest=current["compiled_digest"], reason="Stricter floor")
        impact = service.governance.publish_baseline(tx, identities["admin"], [reference(current)], 0)
        assert impact["affected_workloads"] == ["chat"]
    with pytest.raises(EvolutionError) as rejected:
        with repo.transaction(tenant) as tx:
            service.bindings.rollback(tx, identities["operator"], "chat", target_revision=1, expected_revision=1, reason="Would weaken floor")
    assert rejected.value.code == "floor_conflict"
    with repo.transaction(tenant) as tx:
        assert tx.get("binding_pointers", "chat")["binding_revision"] == 1


def test_immutable_dependency_and_parent_revocation_cannot_reuse_approval(system):
    repo, service, tenant, identities, tokens, payload, client = system
    parent = submit(system)
    approve(system, parent)
    child_payload = {**payload, "parents": [reference(parent)]}
    with repo.transaction(tenant) as tx:
        child = service.governance.submit(tx, identities["author"], "child", child_payload)
    with repo.transaction(tenant) as tx:
        service.governance.review(tx, identities["approver"], "child", 1, outcome="approve", compiled_digest=child["compiled_digest"], reason="Reviewed resolved bytes")
    quota_ref = payload["quota_ref"]
    with pytest.raises(EvolutionError) as immutable:
        with repo.transaction(tenant) as tx:
            quota = tx.get("config:quotas", quota_ref["id"] + ":1", required=True)
            quota["requests_per_minute"] = 1000
            tx.put("config:quotas", quota_ref["id"] + ":1", quota, expected_revision=1)
    assert immutable.value.code == "revision_conflict"
    with repo.transaction(tenant) as tx:
        service.governance.review(tx, identities["approver"], "policy", 1, outcome="revoke", compiled_digest=parent["compiled_digest"], reason="Parent revoked")
    with pytest.raises(EvolutionError) as rejected:
        with repo.transaction(tenant) as tx:
            service.governance.eligible(tx, reference(child))
    assert rejected.value.code == "policy_revoked"
    with repo.transaction(tenant) as tx:
        assert tx.get("config:quotas", quota_ref["id"] + ":1")["requests_per_minute"] == 60
