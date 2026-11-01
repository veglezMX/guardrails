"""Real PostgreSQL enrollment boundaries without private fixture associations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from guardrails.evolution import models
from guardrails.evolution.audit import AuditStore
from guardrails.evolution.enrollment import RuntimeEnrollment
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.identity import IdentityService
from guardrails.evolution.storage import TenantRepository


@pytest.fixture
def system():
    path = os.environ.get("EVOLUTION_TEST_DSN_FILE")
    if not path:
        pytest.fail("EVOLUTION_TEST_DSN_FILE required: enrollment needs real PostgreSQL")
    repo = TenantRepository(Path(path).read_text().strip())
    identity, audit = IdentityService(repo), AuditStore(repo)
    tenant = "enrollment-" + uuid4().hex[:16]
    quota = models.with_resource_digest(models.Quota(tenant_id=tenant, quota_id=str(uuid4()), version=1))
    residency = models.with_resource_digest(models.ResidencyProfile(tenant_id=tenant, residency_id=str(uuid4()), version=1))
    repo.provision_tenant(models.Tenant(tenant_id=tenant, display_name="Enrollment fixture", quota_id=quota.quota_id,
                                      residency_id=residency.residency_id), residency, quota)
    principals, credentials, runtimes = {}, {}, []
    with repo.transaction(tenant) as tx:
        for name, role in (("operator", "operator"), ("viewer", "viewer"), ("a", "runtime"), ("b", "runtime")):
            principals[name] = identity.provision_principal(tx, name, (role,))
            credentials[name] = identity.issue_credential(tx, principals[name]["principal_id"])
        for name in ("chat", "other"):
            tx.insert("workloads", name, models.Workload(tenant_id=tenant, workload_id=name, display_name=name,
                mode="remote", allowed_stages=("input",), quota_id=quota.quota_id))
        for _ in range(2):
            runtime_id = str(uuid4())
            tx.insert("runtimes", runtime_id, models.RuntimeInstall(runtime_id=runtime_id, tenant_id=tenant,
                zone="local-shared", mode="remote", public_key_id="deployment-key", version="2.0.0", telemetry="none"))
            runtimes.append(runtime_id)
    yield {"repo": repo, "tenant": tenant, "identity": identity, "audit": audit,
           "service": RuntimeEnrollment(repo, identity, audit), "principals": principals,
           "credentials": credentials, "runtimes": runtimes}
    audit.close()
    repo.close()


def enroll(system, *, index=0, **changes):
    command = {"runtime_id": system["runtimes"][index],
               "principal_id": system["principals"]["a" if index == 0 else "b"]["principal_id"],
               "workload_ids": ["chat"], "expected_runtime_revision": 1,
               "expected_target_revisions": {"chat": 0}}
    token = changes.pop("operator_token", system["credentials"]["operator"]["one_time_secret"])
    command.update(changes)
    return system["service"].enroll(token, **command)


def test_enrollment_private_links_exact_wire_and_atomic_audit(system):
    first = enroll(system)
    second = enroll(system, index=1, expected_target_revisions={"chat": 1})
    assert first["runtime"]["revision"] == 2
    assert second["target_revisions"] == {"chat": 2}
    with system["repo"].transaction(system["tenant"]) as tx:
        runtime_id = system["runtimes"][0]
        install = tx.get("runtimes", runtime_id)
        assert "principal_id" not in install
        assert models.RuntimeInstall.model_validate_json(models.canonical_json_bytes(install)).revision == 2
        relation = tx.get("runtime_enrollments", runtime_id)
        assert relation["principal_id"] == system["principals"]["a"]["principal_id"]
        assert tx.get("runtime_principals", relation["principal_id"])["runtime_id"] == runtime_id
        assert tx.get("runtime_targets", "chat")["runtime_ids"] == sorted(system["runtimes"])
        links = tx.execute("SELECT target_kind,target_key FROM evolution.record_links WHERE source_kind='runtime_enrollments' AND source_key=:id", {"id": runtime_id}).all()
        assert set(links) == {("runtimes", runtime_id), ("principals", relation["principal_id"]), ("workloads", "chat")}
        assert len(tx.list("audit")) == len(tx.list("outbox")) == 2
        assert {event["metadata"]["operation"] for event in tx.list("audit")} == {"enrollment"}
        assert tx.list("approvals") == tx.list("bindings") == []
        assert len(tx.list("credentials")) == 4
    with pytest.raises(DBAPIError):
        with system["repo"].transaction(system["tenant"]) as tx:
            tx.execute("DELETE FROM evolution.records WHERE kind='runtime_enrollments'")


@pytest.mark.parametrize("role", ["viewer", "a"])
def test_enrollment_requires_current_operator_and_no_runtime_self_assignment(system, role):
    with pytest.raises(EvolutionError) as failure:
        enroll(system, operator_token=system["credentials"][role]["one_time_secret"])
    assert failure.value.code == "forbidden"
    identity = system["identity"]
    with system["repo"].transaction(system["tenant"]) as tx:
        identity.revoke_credential(tx, system["credentials"]["operator"]["credential_id"])
    with pytest.raises(EvolutionError) as failure:
        enroll(system)
    assert failure.value.code == "authentication_failed"
    with system["repo"].transaction(system["tenant"]) as tx:
        assert tx.list("runtime_enrollments") == tx.list("runtime_targets") == tx.list("audit") == []


def test_enrollment_foreign_ids_and_platform_are_not_tenant_authority(system):
    repo, identity = system["repo"], system["identity"]
    foreign_tenant = "foreign-" + uuid4().hex[:16]
    foreign_runtime = str(uuid4())
    with repo.transaction(foreign_tenant) as tx:
        tx.insert("tenants", foreign_tenant, {"tenant_id": foreign_tenant, "status": "active"})
        foreign = identity.provision_principal(tx, "Other runtime", ("runtime",))
        tx.insert("runtimes", foreign_runtime, models.RuntimeInstall(runtime_id=foreign_runtime, tenant_id=foreign_tenant,
            zone="local-shared", mode="remote", public_key_id="key", version="2.0.0", telemetry="none"))
        tx.insert("workloads", "foreign-workload", {"workload_id": "foreign-workload", "status": "active", "mode": "remote"})
    for changes in ({"runtime_id": foreign_runtime}, {"principal_id": foreign["principal_id"]},
                    {"workload_ids": ["foreign-workload"], "expected_target_revisions": {"foreign-workload": 0}}):
        with pytest.raises(EvolutionError) as failure:
            enroll(system, **changes)
        assert failure.value.code == "resource_not_found"
    with repo.transaction("_platform") as tx:
        platform = identity.provision_principal(tx, "Deployment platform", ("platform_admin",))
        token = identity.issue_credential(tx, platform["principal_id"])["one_time_secret"]
    with pytest.raises(EvolutionError) as failure:
        enroll(system, operator_token=token)
    assert failure.value.code == "forbidden"
    with repo.transaction(system["tenant"]) as tx:
        assert tx.list("runtime_enrollments") == []


@pytest.mark.parametrize("record_kind,key,field,value,code", [
    ("runtimes", 0, "status", "revoked", "forbidden"),
    ("runtimes", 0, "zone", "unapproved-zone", "residency_denied"),
    ("workloads", "chat", "mode", "customer", "validation_failed"),
    ("workloads", "chat", "status", "suspended", "forbidden"),
    ("principals", "a", "roles", ["viewer"], "forbidden"),
])
def test_enrollment_rejects_incompatible_or_inactive_targets(system, record_kind, key, field, value, code):
    if record_kind == "runtimes":
        key = system["runtimes"][key]
    if record_kind == "principals":
        key = system["principals"][key]["principal_id"]
    with system["repo"].transaction(system["tenant"]) as tx:
        original = tx.get(record_kind, key)
        tx.put(record_kind, key, dict(original, **{field: value}), expected_revision=original["revision"])
    with pytest.raises(EvolutionError) as failure:
        enroll(system, expected_runtime_revision=2 if record_kind == "runtimes" else 1)
    assert failure.value.code == code
    with system["repo"].transaction(system["tenant"]) as tx:
        assert tx.list("runtime_enrollments") == tx.list("runtime_targets") == tx.list("audit") == []


def test_enrollment_rejects_reuse_retarget_stale_cas_and_postrelease_change(system):
    enroll(system)
    for changes in ({"expected_runtime_revision": 1},
                    {"expected_runtime_revision": 2, "workload_ids": ["other"], "expected_target_revisions": {"other": 0}},
                    {"index": 1, "principal_id": system["principals"]["a"]["principal_id"]},
                    {"index": 1}):
        with pytest.raises(EvolutionError) as failure:
            enroll(system, **changes)
        assert failure.value.code == "revision_conflict"
    with system["repo"].transaction(system["tenant"]) as tx:
        tx.insert("binding_pointers", "chat", {"workload_id": "chat", "binding_revision": 1})
    with pytest.raises(EvolutionError) as failure:
        enroll(system, index=1, expected_target_revisions={"chat": 1})
    assert failure.value.code == "revision_conflict"
    with system["repo"].transaction(system["tenant"]) as tx:
        assert len(tx.list("runtime_enrollments")) == 1
        assert tx.get("runtime_targets", "other") is None
        assert tx.get("runtime_targets", "chat")["runtime_ids"] == [system["runtimes"][0]]


def test_enrollment_audit_failure_rolls_back_all_associations(system, monkeypatch):
    def fail(*args, **kwargs):
        raise EvolutionError("audit_failed")
    monkeypatch.setattr(system["audit"], "append", fail)
    with pytest.raises(EvolutionError) as failure:
        enroll(system)
    assert failure.value.code == "audit_failed"
    with system["repo"].transaction(system["tenant"]) as tx:
        for kind in ("runtime_enrollments", "runtime_principals", "runtime_targets", "audit", "outbox"):
            assert tx.list(kind) == []
        assert tx.get("runtimes", system["runtimes"][0])["revision"] == 1
        assert tx.execute("SELECT COUNT(*) FROM evolution.record_links").scalar_one() == 0


def test_enrollment_concurrent_target_cas_has_one_winner(system):
    def submit(index):
        try:
            return enroll(system, index=index)["runtime"]["runtime_id"]
        except EvolutionError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, (0, 1)))
    assert outcomes.count("revision_conflict") == 1
    winner = next(value for value in outcomes if value != "revision_conflict")
    with system["repo"].transaction(system["tenant"]) as tx:
        assert tx.get("runtime_targets", "chat")["runtime_ids"] == [winner]
        assert len(tx.list("runtime_enrollments")) == len(tx.list("runtime_principals")) == len(tx.list("audit")) == 1
