"""Real stored identity, rotation, separation of duties and browser boundaries."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.identity import IdentityService
from guardrails.evolution.storage import TenantRepository


def setup_identity(roles=("author", "approver")):
    path = os.environ.get("EVOLUTION_TEST_DSN_FILE")
    if not path:
        pytest.fail("EVOLUTION_TEST_DSN_FILE required; identity acceptance needs PostgreSQL")
    repo = TenantRepository(Path(path).read_text().strip())
    tenant = "identity-" + uuid4().hex[:16]
    now = [datetime(2026, 10, 10, tzinfo=timezone.utc)]
    service = IdentityService(repo, clock=lambda: now[0])
    with repo.transaction(tenant) as tx:
        tx.insert("tenants", tenant, {"tenant_id": tenant, "status": "active"})
        principal = service.provision_principal(tx, "Synthetic administrator", roles)
    return repo, tenant, now, service, principal


def test_hash_only_rotation_expiry_and_revocation():
    repo, tenant, now, identity, principal = setup_identity()
    with repo.transaction(tenant) as tx:
        issued = identity.issue_credential(tx, principal["principal_id"])
        stored = tx.get("credentials", issued["credential_id"])
        assert stored["token_hash"] == hashlib.sha256(issued["one_time_secret"].encode()).hexdigest()
        assert issued["one_time_secret"] not in json.dumps(stored)
        assert "one_time_secret" not in stored
    assert identity.authenticate(issued["one_time_secret"]).principal_id == principal["principal_id"]
    with repo.transaction(tenant) as tx:
        rotated = identity.rotate_credential(tx, issued["credential_id"], overlap_seconds=60)
    assert identity.authenticate(issued["one_time_secret"]).principal_id == identity.authenticate(rotated["one_time_secret"]).principal_id
    now[0] += timedelta(seconds=61)
    with pytest.raises(EvolutionError, match="Authentication failed"):
        identity.authenticate(issued["one_time_secret"])
    with repo.transaction(tenant) as tx:
        identity.revoke_credential(tx, rotated["credential_id"])
    with pytest.raises(EvolutionError, match="Authentication failed"):
        identity.authenticate(rotated["one_time_secret"])
    with repo.transaction(tenant) as tx:
        expiring = identity.issue_credential(tx, principal["principal_id"], expires_at=now[0] + timedelta(seconds=1))
    now[0] += timedelta(seconds=1)
    with pytest.raises(EvolutionError, match="Authentication failed"):
        identity.authenticate(expiring["one_time_secret"])
    repo.close()


def test_workload_scope_suspension_and_invalid_lifetime():
    repo, tenant, now, identity, principal = setup_identity(("workload",))
    with repo.transaction(tenant) as tx:
        tx.insert("workloads", "chat", {"tenant_id": tenant, "workload_id": "chat", "status": "active"})
        with pytest.raises(EvolutionError, match="Forbidden"):
            identity.issue_credential(tx, principal["principal_id"])
        with pytest.raises(EvolutionError, match="Validation failed"):
            identity.issue_credential(tx, principal["principal_id"], workload_id="chat", expires_at=now[0] + timedelta(days=31))
        issued = identity.issue_credential(tx, principal["principal_id"], workload_id="chat")
    actor = identity.authenticate(issued["one_time_secret"])
    assert actor.workload_id == "chat" and actor.tenant_id == tenant
    with pytest.raises(EvolutionError, match="Resource not found"):
        identity.authorize(actor, ["workload"], tenant_id="other-tenant", workload_id="chat")
    with pytest.raises(EvolutionError, match="Forbidden"):
        identity.create_session(issued["one_time_secret"])
    with repo.transaction(tenant) as tx:
        existing = tx.get("tenants", tenant)
        tx.put("tenants", tenant, dict(existing, status="suspended"), expected_revision=existing["revision"])
    with pytest.raises(EvolutionError, match="Authentication failed"):
        identity.authenticate(issued["one_time_secret"])
    repo.close()


def test_same_principal_two_credentials_cannot_self_approve():
    repo, tenant, now, identity, principal = setup_identity()
    with repo.transaction(tenant) as tx:
        one = identity.issue_credential(tx, principal["principal_id"])
        two = identity.issue_credential(tx, principal["principal_id"])
        reviewer = identity.provision_principal(tx, "Independent reviewer", ["approver"])
        three = identity.issue_credential(tx, reviewer["principal_id"])
    author = identity.authenticate(one["one_time_secret"])
    assert author.principal_id == identity.authenticate(two["one_time_secret"]).principal_id
    with pytest.raises(EvolutionError, match="Self approval"):
        identity.require_distinct_approver(identity.authenticate(two["one_time_secret"]), author.principal_id)
    identity.require_distinct_approver(identity.authenticate(three["one_time_secret"]), author.principal_id)
    assert reviewer["principal_id"] != author.principal_id
    repo.close()


def test_session_csrf_origin_idle_absolute_and_revocation():
    repo, tenant, now, identity, principal = setup_identity()
    with repo.transaction(tenant) as tx:
        issued = identity.issue_credential(tx, principal["principal_id"])
    session = identity.create_session(issued["one_time_secret"])
    assert session["cookie_options"] == {"secure": True, "httponly": True, "samesite": "strict", "path": "/", "max_age": 28800}
    with repo.transaction(tenant) as tx:
        stored = tx.get("sessions", session["session_id"])
        serialized = json.dumps(stored)
        assert session["session_secret"] not in serialized and session["csrf_token"] not in serialized
    for csrf, origin in [(None, "https://guardrails.local"), ("wrong", "https://guardrails.local"), (session["csrf_token"], "https://attacker.local")]:
        with pytest.raises(EvolutionError, match="Forbidden"):
            identity.authenticate_session(session["session_secret"], mutation=True, csrf_token=csrf, origin=origin, expected_origin="https://guardrails.local")
    actor = identity.authenticate_session(session["session_secret"], mutation=True, csrf_token=session["csrf_token"], origin="https://guardrails.local", expected_origin="https://guardrails.local")
    assert actor.principal_id == principal["principal_id"]
    now[0] += timedelta(minutes=30)
    with pytest.raises(EvolutionError, match="Session expired"):
        identity.authenticate_session(session["session_secret"])
    active = identity.create_session(issued["one_time_secret"])
    for _ in range(16):
        now[0] += timedelta(minutes=29)
        identity.authenticate_session(active["session_secret"])
    now[0] += timedelta(minutes=17)
    with pytest.raises(EvolutionError, match="Session expired"):
        identity.authenticate_session(active["session_secret"])
    logout = identity.create_session(issued["one_time_secret"])
    identity.revoke_session(logout["session_secret"])
    with pytest.raises(EvolutionError, match="Session expired"):
        identity.authenticate_session(logout["session_secret"])
    repo.close()


def test_platform_privileges_never_imply_auditor_or_operator():
    repo, tenant, now, identity, principal = setup_identity(("tenant_admin",))
    with repo.transaction(tenant) as tx:
        with pytest.raises(EvolutionError, match="Forbidden"):
            identity.provision_principal(tx, "Escalation", ["platform_admin"])
        token = identity.issue_credential(tx, principal["principal_id"])
    actor = identity.authenticate(token["one_time_secret"])
    for roles in (["auditor"], ["operator"], ["workload"]):
        with pytest.raises(EvolutionError, match="Forbidden"):
            identity.authorize(actor, roles, tenant_id=tenant)
    with repo.transaction(tenant) as tx:
        tx.put("principals", principal["principal_id"], dict(principal, status="revoked"), expected_revision=principal["revision"])
    with pytest.raises(EvolutionError, match="Authentication failed"):
        identity.authenticate(token["one_time_secret"])
    assert actor.tenant_id == tenant
    repo.close()
