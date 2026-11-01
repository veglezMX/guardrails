"""Onboarding requires genuine application of signed bytes by all runtimes."""

import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from guardrails.evolution import models
from guardrails.evolution.audit import AuditStore
from guardrails.evolution.bindings import BindingService
from guardrails.evolution.enrollment import RuntimeEnrollment
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.governance import PolicyGovernance
from guardrails.evolution.identity import IdentityService
from guardrails.evolution.runtime_apply import RuntimeSnapshotStore
from guardrails.evolution.snapshots import SnapshotDistributor
from guardrails.evolution.storage import TenantRepository
from sqlalchemy.exc import DBAPIError


@pytest.fixture
def system(tmp_path):
    path = os.environ.get("EVOLUTION_TEST_DSN_FILE")
    if not path:
        pytest.fail("Real PostgreSQL required for onboarding")
    repo = TenantRepository(Path(path).read_text().strip())
    identity, audit = IdentityService(repo), AuditStore(repo)
    governance = PolicyGovernance(repo, audit)
    bindings = BindingService(repo, governance, audit)
    tenant = "onboard-" + uuid4().hex[:16]
    quota = models.with_resource_digest(
        models.Quota(tenant_id=tenant, quota_id=str(uuid4()), version=1)
    )
    residency = models.with_resource_digest(
        models.ResidencyProfile(tenant_id=tenant, residency_id=str(uuid4()), version=1)
    )
    repo.provision_tenant(
        models.Tenant(
            tenant_id=tenant,
            display_name="Onboarding test",
            quota_id=quota.quota_id,
            residency_id=residency.residency_id,
        ),
        residency,
        quota,
    )
    tokens, runtimes = {}, []
    with repo.transaction(tenant) as tx:
        for role in ("author", "approver", "operator", "runtime-a", "runtime-b"):
            person = identity.provision_principal(
                tx, role, ["runtime" if role.startswith("runtime") else role]
            )
            tokens[role] = identity.issue_credential(tx, person["principal_id"])[
                "one_time_secret"
            ]
        for workload in ("chat", "other"):
            tx.insert(
                "workloads",
                workload,
                models.Workload(
                    tenant_id=tenant,
                    workload_id=workload,
                    display_name=workload,
                    mode="remote",
                    allowed_stages=("input",),
                    quota_id=quota.quota_id,
                    required_state="observe",
                ),
            )
        for _ in range(2):
            runtime_id = str(uuid4())
            tx.insert(
                "runtimes",
                runtime_id,
                models.RuntimeInstall(
                    runtime_id=runtime_id,
                    tenant_id=tenant,
                    zone="local-shared",
                    mode="remote",
                    public_key_id="onboarding-key",
                    version="2.0.0",
                    telemetry="none",
                ),
            )
            runtimes.append(runtime_id)
    actors = {name: identity.authenticate(token) for name, token in tokens.items()}
    enrollment = RuntimeEnrollment(repo, identity, audit)
    for index, name in enumerate(("runtime-a", "runtime-b")):
        enrollment.enroll(
            tokens["operator"],
            runtime_id=runtimes[index],
            principal_id=actors[name].principal_id,
            workload_ids=["chat", "other"],
            expected_runtime_revision=1,
            expected_target_revisions={"chat": index, "other": index},
        )
    payload = {
        "rules": [
            {
                "rule_id": "email",
                "kind": "pii",
                "stages": ["input"],
                "action": "block",
                "mandatory": True,
                "minimum_state": "observe",
                "config": {"entities": ["email"]},
                "evaluator_revision": "pii-v2",
            }
        ],
        "residency_ref": {
            "id": residency.residency_id,
            "version": 1,
            "digest": residency.digest,
        },
        "quota_ref": {"id": quota.quota_id, "version": 1, "digest": quota.digest},
    }
    key = Ed25519PrivateKey.generate()
    distributor = SnapshotDistributor(repo, governance, bindings, key, "onboarding-key")
    stores = [
        RuntimeSnapshotStore(
            tmp_path / f"runtime-{index}.state",
            tenant,
            runtime_id,
            {"onboarding-key": key.public_key()},
        )
        for index, runtime_id in enumerate(runtimes)
    ]
    fixture = {
        "repo": repo,
        "identity": identity,
        "audit": audit,
        "governance": governance,
        "bindings": bindings,
        "tenant": tenant,
        "actors": actors,
        "payload": payload,
        "runtimes": runtimes,
        "distributor": distributor,
        "stores": stores,
    }
    fixture["policy"] = approve(fixture)
    yield fixture
    audit.close()
    repo.close()


def approve(system, *, floor="observe", policy_id="policy"):
    payload = deepcopy(system["payload"])
    payload["rules"][0]["minimum_state"] = floor
    with system["repo"].transaction(system["tenant"]) as tx:
        policy = system["governance"].submit(
            tx, system["actors"]["author"], policy_id, payload
        )
        system["governance"].review(
            tx,
            system["actors"]["approver"],
            policy_id,
            policy["version"],
            outcome="approve",
            compiled_digest=policy["compiled_digest"],
            reason="Onboarding review",
        )
    return {
        "tenant_id": system["tenant"],
        "policy_id": policy_id,
        "version": policy["version"],
        "digest": policy["compiled_digest"],
    }


def release(system, state, expected, *, policy=None, workloads=("chat",)):
    with system["repo"].transaction(system["tenant"]) as tx:
        return system["bindings"].release(
            tx,
            system["actors"]["operator"],
            policy=policy or system["policy"],
            workload_ids=list(workloads),
            stage="canary",
            state=state,
            expected_revisions={workload: expected for workload in workloads},
            reason="Onboarding transition",
        )


def apply(system, index, *, acknowledge=("chat", "other")):
    actor = system["actors"]["runtime-a" if index == 0 else "runtime-b"]
    with system["repo"].transaction(system["tenant"]) as tx:
        snapshot = system["distributor"].issue(tx, actor)
    receipts = system["stores"][index].apply(snapshot)
    assert system["stores"][index].ready()
    for receipt in receipts:
        assert (
            system["stores"][index].policy_for(receipt["workload_id"]).compiled_digest
            == receipt["digest"]
        )
        if receipt["workload_id"] in acknowledge:
            with system["repo"].transaction(system["tenant"]) as tx:
                system["distributor"].accept_receipt(tx, actor, receipt)
    return receipts


def persistent_state(system):
    with system["repo"].transaction(system["tenant"]) as tx:
        return {
            kind: tx.list(kind, limit=1000)
            for kind in (
                "bindings",
                "binding_pointers",
                "applied_bindings",
                "releases",
                "outbox",
                "audit",
                "enforcement_history",
            )
        }


@pytest.mark.parametrize("state", ["warn", "redact", "block"])
def test_first_enforcement_requires_all_actual_observe_application_receipts(
    system, state
):
    release(system, "observe", 0)
    before = persistent_state(system)
    with pytest.raises(EvolutionError) as failure:
        release(system, state, 1)
    assert failure.value.code == "floor_conflict"
    assert persistent_state(system) == before
    apply(system, 0)
    before = persistent_state(system)
    assert before["applied_bindings"] == []
    with pytest.raises(EvolutionError) as failure:
        release(system, state, 1)
    assert failure.value.code == "floor_conflict"
    assert persistent_state(system) == before
    apply(system, 1)
    observed = persistent_state(system)
    assert observed["applied_bindings"][0]["binding_revision"] == 1
    assert observed["releases"][0]["status"] == "applied"
    enforcing = release(system, state, 1)
    assert enforcing["status"] == "pending" and enforcing["state"] == state
    after = persistent_state(system)
    assert after["applied_bindings"] == observed["applied_bindings"]
    assert after["enforcement_history"] == []


def test_superseded_snapshot_ack_cannot_unlock_first_enforcement(system):
    release(system, "observe", 0)
    apply(system, 0)
    apply(system, 1)
    # Issue a genuinely new signed lease without acknowledging it; old receipts
    # may remain in history but no longer prove current runtime readiness.
    system["distributor"].key_id = "rotated-key"
    with system["repo"].transaction(system["tenant"]) as tx:
        newer = system["distributor"].issue(tx, system["actors"]["runtime-a"])
        assert newer["generation"] == 2
    before = persistent_state(system)
    with pytest.raises(EvolutionError) as failure:
        release(system, "block", 1)
    assert failure.value.code == "snapshot_stale"
    assert persistent_state(system) == before


def test_pending_observe_revision_cannot_reuse_earlier_observed_digest(system):
    release(system, "observe", 0)
    apply(system, 0)
    apply(system, 1)
    release(system, "observe", 1)
    before = persistent_state(system)
    with pytest.raises(EvolutionError) as failure:
        release(system, "block", 2)
    assert failure.value.code == "snapshot_stale"
    assert persistent_state(system) == before
    apply(system, 0)
    apply(system, 1)
    assert release(system, "block", 2)["status"] == "pending"


def test_initial_mandatory_block_floor_does_not_require_weakened_observe(system):
    mandatory = approve(system, floor="block", policy_id="mandatory")
    with pytest.raises(EvolutionError) as failure:
        release(system, "observe", 0, policy=mandatory)
    assert failure.value.code == "floor_conflict"
    assert persistent_state(system)["bindings"] == []
    assert release(system, "block", 0, policy=mandatory)["status"] == "pending"
    apply(system, 0)
    apply(system, 1)
    state = persistent_state(system)
    assert state["applied_bindings"][0]["binding_revision"] == 1
    assert state["bindings"][0]["state"] == "block"
    assert len(state["enforcement_history"]) == 1


def test_applied_enforcement_history_allows_recovery_but_never_revoked_policy(system):
    release(system, "observe", 0)
    apply(system, 0)
    apply(system, 1)
    release(system, "block", 1)
    apply(system, 0)
    assert persistent_state(system)["enforcement_history"] == []
    apply(system, 1)
    history = persistent_state(system)["enforcement_history"]
    assert len(history) == 1 and history[0]["binding_revision"] == 2
    assert len(history[0]["receipt_keys"]) == 2
    with pytest.raises(DBAPIError), system["repo"].transaction(system["tenant"]) as tx:
        tx.execute("DELETE FROM evolution.records WHERE kind='enforcement_history'")
    release(system, "observe", 2)
    # Desired Observe is not yet acknowledged. Existing enforced application is
    # durable proof for safe recovery, unlike an initial pending Observe.
    assert release(system, "block", 3)["status"] == "pending"
    assert persistent_state(system)["enforcement_history"] == history
    with system["repo"].transaction(system["tenant"]) as tx:
        system["governance"].review(
            tx,
            system["actors"]["approver"],
            "policy",
            1,
            outcome="revoke",
            compiled_digest=system["policy"]["digest"],
            reason="Revocation still applies",
        )
    before = persistent_state(system)
    with pytest.raises(EvolutionError) as failure:
        release(system, "block", 4)
    assert failure.value.code == "policy_revoked"
    assert persistent_state(system) == before


def test_multi_workload_first_enforcement_failure_rolls_back_earlier_workload(system):
    release(system, "observe", 0, workloads=("chat", "other"))
    apply(system, 0, acknowledge=("chat",))
    apply(system, 1, acknowledge=("chat",))
    before = persistent_state(system)
    assert [item["binding_revision"] for item in before["applied_bindings"]] == [1]
    with pytest.raises(EvolutionError) as failure:
        release(system, "block", 1, workloads=("chat", "other"))
    assert failure.value.code == "floor_conflict"
    assert persistent_state(system) == before
