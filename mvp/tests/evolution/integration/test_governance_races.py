"""Deterministic PostgreSQL interleavings for admission versus revocation/floors."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from pathlib import Path
from threading import Event
import time
from uuid import uuid4

import pytest

from guardrails.evolution import models
from guardrails.evolution.audit import AuditStore
from guardrails.evolution.bindings import BindingService
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.governance import PolicyGovernance, lock_admission
from guardrails.evolution.identity import IdentityService
from guardrails.evolution.storage import TenantRepository


@pytest.fixture
def system():
    path = os.environ.get("EVOLUTION_TEST_DSN_FILE")
    if not path:
        pytest.fail("EVOLUTION_TEST_DSN_FILE required for real PostgreSQL race tests")
    repo = TenantRepository(Path(path).read_text().strip())
    tenant = "race-" + uuid4().hex[:20]
    audit, identity = AuditStore(repo), IdentityService(repo)
    governance = PolicyGovernance(repo, audit)
    bindings = BindingService(repo, governance, audit)
    quota = models.with_resource_digest(models.Quota(tenant_id=tenant, quota_id=str(uuid4()), version=1))
    residency = models.with_resource_digest(models.ResidencyProfile(tenant_id=tenant, residency_id=str(uuid4()), version=1))
    repo.provision_tenant(models.Tenant(tenant_id=tenant, display_name="Race tenant", quota_id=quota.quota_id,
                                      residency_id=residency.residency_id), residency, quota)
    actors = {}
    with repo.transaction(tenant) as tx:
        for role in ("author", "approver", "operator", "tenant_admin"):
            actors[role] = identity.provision_principal(tx, role, (role,))
        tx.insert("workloads", "chat", models.Workload(tenant_id=tenant, workload_id="chat", display_name="Chat",
            mode="remote", allowed_stages=("input",), quota_id=quota.quota_id, required_state="observe"))
    payload = {"rules": [{"rule_id": "email", "kind": "pii", "stages": ["input"], "action": "block", "mandatory": True,
                         "minimum_state": "observe", "config": {"entities": ["email"]}, "evaluator_revision": "pii-v2"}],
               "residency_ref": {"id": residency.residency_id, "version": 1, "digest": residency.digest},
               "quota_ref": {"id": quota.quota_id, "version": 1, "digest": quota.digest}}
    policies = {}
    for policy_id in ("candidate", "stronger"):
        body = deepcopy(payload)
        if policy_id == "stronger":
            body["rules"][0]["minimum_state"] = "block"
        with repo.transaction(tenant) as tx:
            policies[policy_id] = governance.submit(tx, actors["author"], policy_id, body)
        with repo.transaction(tenant) as tx:
            governance.review(tx, actors["approver"], policy_id, 1, outcome="approve",
                              compiled_digest=policies[policy_id]["compiled_digest"], reason="Synthetic review")
    yield {"repo": repo, "tenant": tenant, "audit": audit, "governance": governance,
           "bindings": bindings, "actors": actors, "policies": policies}
    audit.close()
    repo.close()


def reference(system, name="candidate"):
    policy = system["policies"][name]
    return {"tenant_id": system["tenant"], "policy_id": name, "version": 1, "digest": policy["compiled_digest"]}


def release(system, tx):
    return system["bindings"].release(tx, system["actors"]["operator"], policy=reference(system),
        workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": 0}, reason="Concurrent canary")


def change_governance(system, tx, operation):
    if operation == "revoke":
        return system["governance"].review(tx, system["actors"]["approver"], "candidate", 1, outcome="revoke",
            compiled_digest=system["policies"]["candidate"]["compiled_digest"], reason="Concurrent revocation")
    return system["governance"].publish_baseline(tx, system["actors"]["tenant_admin"], [reference(system, "stronger")], 0)


def blocked_by(repo, tenant, blocked_pid, blocker_pid):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with repo.transaction(tenant) as tx:
            blockers = tx.execute("SELECT pg_blocking_pids(:pid)", {"pid": blocked_pid}).scalar_one()
        if blocker_pid in blockers:
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("operation", ["revoke", "baseline"])
def test_release_then_governance_change_is_serialized_through_commit(system, monkeypatch, operation):
    repo, tenant = system["repo"], system["tenant"]
    paused, resume, second_started = Event(), Event(), Event()
    pids = {}
    eligible = system["governance"].eligible

    def pause_after_validation(tx, ref):
        result = eligible(tx, ref)
        if tx.execute("SELECT pg_backend_pid()").scalar_one() == pids.get("release") and ref["policy_id"] == "candidate":
            paused.set()
            assert resume.wait(5), "release test barrier timed out"
        return result
    monkeypatch.setattr(system["governance"], "eligible", pause_after_validation)

    def first():
        with repo.transaction(tenant) as tx:
            pids["release"] = tx.execute("SELECT pg_backend_pid()").scalar_one()
            result = release(system, tx)
        return result

    def second():
        with repo.transaction(tenant) as tx:
            pids["change"] = tx.execute("SELECT pg_backend_pid()").scalar_one()
            second_started.set()
            result = change_governance(system, tx, operation)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(first)
        try:
            assert paused.wait(3)
            second_result = pool.submit(second)
            assert second_started.wait(3)
            assert blocked_by(repo, tenant, pids["change"], pids["release"])
            assert not second_result.done()
        finally:
            resume.set()
        assert first_result.result(timeout=5)["status"] == "pending"
        second_result.result(timeout=5)
    with repo.transaction(tenant) as tx:
        events = [row[0] for row in tx.execute("SELECT data->>'event_type' FROM evolution.records WHERE kind='audit' ORDER BY audit_sequence")]
        assert events.index("release_pending") < events.index("policy_revoked" if operation == "revoke" else "baseline_published")
        assert len(tx.list("bindings")) == 1
        if operation == "revoke":
            assert system["governance"].get(tx, "candidate", 1)["status"] == "revoked"
        else:
            assert tx.get("binding_blocks", "chat")["binding_revision"] == 1
            assert tx.get("baseline", "current")["policies"] == [reference(system, "stronger")]


@pytest.mark.parametrize("operation,code", [("revoke", "policy_revoked"), ("baseline", "floor_conflict")])
def test_committing_governance_change_rejects_waiting_release_atomically(system, operation, code):
    repo, tenant = system["repo"], system["tenant"]
    changed, commit, second_started = Event(), Event(), Event()
    pids = {}

    def first():
        with repo.transaction(tenant) as tx:
            pids["change"] = tx.execute("SELECT pg_backend_pid()").scalar_one()
            change_governance(system, tx, operation)
            changed.set()
            assert commit.wait(5), "change test barrier timed out"

    def second():
        try:
            with repo.transaction(tenant) as tx:
                pids["release"] = tx.execute("SELECT pg_backend_pid()").scalar_one()
                second_started.set()
                release(system, tx)
        except EvolutionError as error:
            return error.code
        return "unexpected_success"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(first)
        try:
            assert changed.wait(3)
            second_result = pool.submit(second)
            assert second_started.wait(3)
            assert blocked_by(repo, tenant, pids["release"], pids["change"])
        finally:
            commit.set()
        first_result.result(timeout=5)
        assert second_result.result(timeout=5) == code
    with repo.transaction(tenant) as tx:
        assert tx.list("bindings") == tx.list("releases") == tx.list("binding_pointers") == []
        assert not any(item["type"] == "binding_desired" for item in tx.list("outbox"))
        assert not any(item["event_type"] == "release_pending" for item in tx.list("audit"))


def test_platform_admission_lock_does_not_block_other_tenant(system):
    with system["repo"].transaction("_platform") as platform_tx:
        lock_admission(platform_tx)
        with ThreadPoolExecutor(max_workers=1) as pool:
            def own_tenant():
                with system["repo"].transaction(system["tenant"]) as tx:
                    return release(system, tx)
            result = pool.submit(own_tenant).result(timeout=3)
        assert result["tenant_id"] == system["tenant"] and result["status"] == "pending"
        assert platform_tx.get("releases", result["release_id"]) is None
