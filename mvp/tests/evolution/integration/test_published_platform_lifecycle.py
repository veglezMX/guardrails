"""One committed organization lifecycle in a parent-provisioned isolated DB.

Never falls back to EVOLUTION_TEST_DSN_FILE. The parent creates/migrates/grants a
fresh evolution_platform_<unique> database for every whole attempt and retains
it afterward. This test deliberately retains its published floor there.
"""
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest
from guardrails.evolution import models
from guardrails.evolution.compiler import canonical_bytes
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.governance import lock_admission
from guardrails.evolution.published_baselines import PublishedBaselines
from guardrails.evolution.runtime_api import create_app
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https
from test_evaluation_runtime import fixture_system, http, refresh, request, runtime


def resource_ref(value, field):
    return {"id": value[field], "version": value["version"], "digest": value["digest"]}


def policy_ref(value):
    return {key: value[key] for key in ("tenant_id", "policy_id", "version")} | {"digest": value["compiled_digest"]}


def config(service, scope, actor, family, body):
    with service.repository.transaction(scope) as tx:
        value, status = service.dispatch(tx, actor, "POST", [family], body, {}, None)
    assert status == 201 and value["tenant_id"] == scope
    return value


def approve(service, scope, author, approver, name, payload):
    with service.repository.transaction(scope) as tx:
        value = service.governance.submit(tx, author, name, payload)
    with service.repository.transaction(scope) as tx:
        proof = service.governance.review(tx, approver, name, value["version"], outcome="approve",
            compiled_digest=value["compiled_digest"], reason="Independent organization-floor fixture review")
        assert proof["actor_id"] != value["author_id"]
    return value


def blocked(repo, tenant, waiting, holder):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        with repo.transaction(tenant) as tx:
            if holder in tx.execute("SELECT pg_blocking_pids(:pid)", {"pid": waiting}).scalar_one():
                return True
        time.sleep(0.01)
    return False


def test_published_platform_governance_signed_https_and_commit_order(tmp_path, monkeypatch):
    private = os.environ.get("EVOLUTION_PLATFORM_TEST_DSN_FILE")
    if not private:
        pytest.fail("Explicit parent-provisioned EVOLUTION_PLATFORM_TEST_DSN_FILE required")
    isolated = Path(private)
    assert isolated.is_absolute() and isolated.resolve() == isolated and isolated.is_file()
    metadata = isolated.lstat()
    assert metadata.st_uid == os.getuid() and metadata.st_nlink == 1
    assert stat.S_IMODE(metadata.st_mode) == 0o600 and 0 < metadata.st_size <= 16384
    # Inspect the server's actual database name before any mutation or fixture.
    guard = TenantRepository(isolated.read_text().strip())
    try:
        with guard.transaction("_platform") as tx:
            name = tx.execute("SELECT current_database()").scalar_one()
            assert name.startswith("evolution_platform_") and len(name) > len("evolution_platform_")
            assert tx.get("baseline", "current") is None, "Fresh database required; never replay a whole lifecycle"
            assert tx.page("tenant_directory", limit=1) == []
    finally:
        guard.close()
    monkeypatch.setenv("EVOLUTION_TEST_DSN_FILE", str(isolated))
    systems = []
    try:
        for index in range(2):
            systems.append(fixture_system(tmp_path / f"tenant-{index}", baseline=False))
        first, second = systems
        service, repo = first["service"], first["repo"]
        assert service.governance.baseline_resolver is None
        with repo.transaction("_platform") as tx:
            author = service.identity.provision_principal(tx, "Platform author", ["platform_admin"])
            reviewer = service.identity.provision_principal(tx, "Independent platform reviewer", ["platform_admin", "approver"])
        quota = config(service, "_platform", author, "quotas", {"requests_per_minute": 1000, "burst": 1000, "max_inflight": 8})
        deny = config(service, "_platform", author, "lists", {"kind": "content", "allow_enabled": False, "deny": ["term:organization forbidden"]})
        residency = config(service, "_platform", author, "residency", {"content_lists": [deny["list_id"]]})
        core_residency = config(service, "_platform", author, "residency", {})
        payload = {"rules": [{"rule_id": "email", "kind": "pii", "stages": ["input", "output"],
            "action": "block", "mandatory": True, "minimum_state": "observe", "config": {"entities": ["email", "international_phone"]},
            "evaluator_revision": "pii-v2"}], "residency_ref": resource_ref(core_residency, "residency_id"),
            "quota_ref": resource_ref(quota, "quota_id")}
        core = approve(service, "_platform", author, reviewer, "organization-core", payload)
        floor_payload = {**deepcopy(payload), "parents": [policy_ref(core)],
            "residency_ref": resource_ref(residency, "residency_id"), "list_refs": [resource_ref(deny, "list_id")]}
        floor = approve(service, "_platform", author, reviewer, "organization-floor", floor_payload)
        weaker_payload = {**deepcopy(floor_payload), "parents": [], "rules": []}
        weaker = approve(service, "_platform", author, reviewer, "weaker-before-publication", weaker_payload)
        # An approved private platform policy is still unavailable to a tenant.
        with repo.transaction(first["tenant"]) as tx:
            assert tx.get("policies", "organization-floor:1") is None
            assert {row.tenant_id for row in tx.execute("SELECT tenant_id FROM evolution.records")} == {first["tenant"]}
            with pytest.raises(EvolutionError, check=lambda error: error.code == "resource_not_found"):
                PublishedBaselines(tx).get_policy(policy_ref(floor))
            assert tx.execute("SELECT current_setting('guardrails.tenant_id')").scalar_one() == first["tenant"]
        with repo.transaction("_platform") as tx:
            saved_core = tx.get("policies", "organization-core:1", required=True)
            saved_floor = tx.get("policies", "organization-floor:1", required=True)
            # Ordinary platform admission still shares the global lock.
            lock_admission(tx)
            with ThreadPoolExecutor(max_workers=1) as pool:
                def ordinary_tenant():
                    with repo.transaction(first["tenant"]) as other:
                        return service.governance.eligible(other, first["policy_ref"])
                assert pool.submit(ordinary_tenant).result(timeout=3)["status"] == "approved"
        held, finish, attempting = Event(), Event(), Event()
        pids = {}
        def admitted():
            with repo.transaction(first["tenant"]) as tx:
                pids["admit"] = tx.execute("SELECT pg_backend_pid()").scalar_one()
                service.governance.eligible(tx, first["policy_ref"])
                held.set()
                assert finish.wait(5)
        def publish():
            with repo.transaction("_platform") as tx:
                pids["publish"] = tx.execute("SELECT pg_backend_pid()").scalar_one()
                attempting.set()
                return service.governance.publish_baseline(tx, author, [policy_ref(floor)], 0)
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(admitted)
            try:
                assert held.wait(3)
                two = pool.submit(publish)
                assert attempting.wait(3) and blocked(repo, first["tenant"], pids["publish"], pids["admit"])
                assert not two.done()
            finally:
                finish.set()
            one.result(timeout=5)
            assert two.result(timeout=5) == {"revision": 1, "affected_workloads": ["chat"]}
        for system in systems:
            with repo.transaction(system["tenant"]) as tx:
                assert tx.get("binding_blocks", "chat", required=True)["reason_code"] == "floor_conflict"
                assert service.governance.current_baselines(tx)[0].compiled_digest == floor["compiled_digest"]
                assert tx.execute("SELECT current_setting('guardrails.tenant_id')").scalar_one() == system["tenant"]
                assert tx.get("policies", "organization-floor:1") is None
                with pytest.raises(EvolutionError, check=lambda error: error.code == "floor_conflict"):
                    service.governance.eligible(tx, system["policy_ref"])
            # Explicit tenant and workload parents inherit the published immutable
            # platform list/rule, then add independent tenant/workload deny lists.
            own_list = config(system["service"], system["tenant"], system["actors"]["author"], "lists",
                {"kind": "content", "allow_enabled": False, "deny": ["term:tenant forbidden"]})
            workload_list = config(system["service"], system["tenant"], system["actors"]["author"], "lists",
                {"kind": "content", "allow_enabled": False, "deny": ["term:workload forbidden"]})
            body = deepcopy(system["payload"])
            body.update(parents=[policy_ref(floor)], rules=[])
            body["list_refs"].append(resource_ref(own_list, "list_id"))
            own_residency = config(system["service"], system["tenant"], system["actors"]["tenant_admin"], "residency",
                {"provider_lists": [body["list_refs"][0]["id"]], "content_lists": [deny["list_id"], own_list["list_id"]]})
            body["residency_ref"] = resource_ref(own_residency, "residency_id")
            tenant_policy = approve(system["service"], system["tenant"], system["actors"]["author"], system["actors"]["approver"], "tenant-floor", body)
            with repo.transaction(system["tenant"]) as tx:
                system["service"].governance.publish_baseline(tx, system["actors"]["tenant_admin"], [policy_ref(tenant_policy)], 0)
            body["parents"] = [policy_ref(tenant_policy)]
            body["list_refs"].append(resource_ref(workload_list, "list_id"))
            workload_residency = config(system["service"], system["tenant"], system["actors"]["tenant_admin"], "residency",
                {"provider_lists": [body["list_refs"][0]["id"]], "content_lists": [deny["list_id"], own_list["list_id"], workload_list["list_id"]]})
            body["residency_ref"] = resource_ref(workload_residency, "residency_id")
            applied = approve(system["service"], system["tenant"], system["actors"]["author"], system["actors"]["approver"], "workload-policy", body)
            system.update(policy=applied, policy_ref=policy_ref(applied))
            # The previously applied Observe policy is now below the published
            # floors. First apply this eligible policy in Observe, then enforce.
            refresh(system, state="observe")
            with repo.transaction(system["tenant"]) as tx:
                observed = tx.get("applied_bindings", "chat", required=True)
                assert observed["policy"] == system["policy_ref"]
                binding = tx.get("bindings", f"chat:{observed['binding_revision']}", required=True)
                assert binding["state"] == "observe" and binding["policy"] == system["policy_ref"]
                receipts = [row for row in tx.list("receipts") if row["binding_revision"] == observed["binding_revision"]]
                assert len(receipts) == 2 and {row["runtime_id"] for row in receipts} == set(system["runtime_ids"])
                assert all(row["digest"] == system["policy_ref"]["digest"] and row["health"] == "ready" for row in receipts)
            refresh(system, state="block")
        with ExitStack() as stack:
            clients = []
            for index, system in enumerate(systems):
                server = stack.enter_context(serve_https(lambda system=system: create_app(service=runtime(system)), tmp_path / f"https-{index}"))
                client = stack.enter_context(http(server, system["tokens"]["workload"]))
                clients.append(client)
                assert client.get("/health/ready").status_code == 200
                for text, category in (("organization forbidden", "term"), ("tenant forbidden", "term"),
                                       ("workload forbidden", "term"), ("organization-test@example.test", "email")):
                    response = client.post("/v2/evaluations", json=request(system, text=text))
                    assert response.status_code == 200
                    decision = models.Decision.model_validate_json(response.content)
                    assert decision.action == decision.would_be_action == "block"
                    assert decision.audit_status == "persisted" and decision.evaluation_ms < 500
                    assert decision.policy.digest == system["policy_ref"]["digest"]
                    assert any(item.category == category for item in decision.findings)
                    with repo.transaction(system["tenant"]) as tx:
                        events = [row for row in tx.list("audit") if row.get("decision_id") == decision.decision_id]
                        assert len(events) == 1 and events[0]["tenant_id"] == system["tenant"]
                        assert events[0]["action"] == "block" and events[0]["policy_digest"] == decision.policy.digest
                clean = client.post("/v2/evaluations", json=request(system, text="Ordinary harmless text."))
                assert clean.status_code == 200 and clean.json()["action"] == "allow"
            changed, commit, waiting = Event(), Event(), Event()
            def revoke():
                with repo.transaction("_platform") as tx:
                    pids["revoke"] = tx.execute("SELECT pg_backend_pid()").scalar_one()
                    service.governance.review(tx, author, "organization-core", 1, outcome="revoke",
                        compiled_digest=core["compiled_digest"], reason="Emergency ancestor revoke")
                    changed.set()
                    assert commit.wait(5)
            def waiting_admission():
                with repo.transaction(second["tenant"]) as tx:
                    pids["wait"] = tx.execute("SELECT pg_backend_pid()").scalar_one()
                    waiting.set()
                    with pytest.raises(EvolutionError, check=lambda error: error.code == "policy_revoked"):
                        service.governance.eligible(tx, second["policy_ref"])
            with ThreadPoolExecutor(max_workers=2) as pool:
                one = pool.submit(revoke)
                try:
                    assert changed.wait(3)
                    two = pool.submit(waiting_admission)
                    assert waiting.wait(3) and blocked(repo, second["tenant"], pids["wait"], pids["revoke"])
                finally:
                    commit.set()
                one.result(timeout=5)
                two.result(timeout=5)
            for system, client in zip(systems, clients, strict=True):
                response = client.post("/v2/evaluations", json=request(system, text="Ordinary harmless text."))
                assert response.status_code == 409 and response.json()["error"]["code"] == "policy_revoked"
            with pytest.raises(EvolutionError, check=lambda error: error.code == "floor_conflict"), repo.transaction("_platform") as tx:
                service.governance.publish_baseline(tx, author, [policy_ref(weaker)], 1)
            replacement = {**deepcopy(floor_payload), "parents": []}
            replacement["rules"][0]["config"]["entities"].append("payment_card")
            stronger = approve(service, "_platform", author, reviewer, "replacement", replacement)
            with repo.transaction("_platform") as tx:
                result = service.governance.publish_baseline(tx, author, [policy_ref(stronger)], 1)
                assert result["revision"] == 2
                assert tx.get("policies", "organization-core:1") == saved_core
                assert tx.get("policies", "organization-floor:1") == saved_floor
                assert canonical_bytes(models.PolicyVersion.model_validate(saved_floor)) == canonical_bytes(PublishedBaselines(tx).get_policy(policy_ref(floor)))
            for system in systems:
                with repo.transaction(system["tenant"]) as tx:
                    # Global replacement is healthy, but old descendant policies
                    # remain revoked until separately authored/approved/released.
                    assert PublishedBaselines(tx).current()[0].compiled_digest == stronger["compiled_digest"]
                    with pytest.raises(EvolutionError, check=lambda error: error.code == "policy_revoked"):
                        service.governance.eligible(tx, system["policy_ref"])
                    assert tx.execute("SELECT current_setting('guardrails.tenant_id')").scalar_one() == system["tenant"]
    finally:
        for system in systems:
            system["service"].audit.close()
            system["repo"].close()
