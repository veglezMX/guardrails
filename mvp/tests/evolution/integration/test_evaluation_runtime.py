"""Real HTTPS, PostgreSQL, signed caches and isolated production detectors."""

import asyncio
import json
import os
import signal
import ssl
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from guardrails.evolution import models
from guardrails.evolution.config import ServiceConfig
from guardrails.evolution.enrollment import RuntimeEnrollment
from guardrails.evolution.management_api import ManagementApi
from guardrails.evolution.runtime_api import EvaluationRuntime, RuntimeSlot, create_app
from guardrails.evolution.runtime_apply import RuntimeSnapshotStore
from guardrails.evolution.snapshots import SnapshotDistributor
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https


def ref(resource, identity):
    return {"id": getattr(resource, identity), "version": resource.version, "digest": resource.digest}


def fixture_system(tmp_path, *, state="observe", mandatory=False, baseline=True):
    dsn = Path(os.environ["EVOLUTION_TEST_DSN_FILE"]).read_text().strip()
    repo, tenant = TenantRepository(dsn), "evaluate-" + uuid4().hex[:16]
    service = ManagementApi(repo)
    quota = models.with_resource_digest(models.Quota(quota_id=str(uuid4()), tenant_id=tenant, requests_per_minute=1000, burst=1000, max_inflight=8))
    provider = models.with_resource_digest(models.ProviderProfile(provider_id=str(uuid4()), tenant_id=tenant, kind="model", vendor="local_rules", model_id="pinned-local", zone="local-shared"))
    provider_list = models.with_resource_digest(models.ListSet(list_id=str(uuid4()), tenant_id=tenant, kind="provider", allow=(provider.provider_id,), allow_enabled=True))
    residency = models.with_resource_digest(models.ResidencyProfile(residency_id=str(uuid4()), tenant_id=tenant, provider_lists=(provider_list.list_id,)))
    repo.provision_tenant(models.Tenant(tenant_id=tenant, display_name="Runtime test", residency_id=residency.residency_id, quota_id=quota.quota_id), residency, quota)
    tokens = {}
    with repo.transaction(tenant) as tx:
        tx.insert("config:providers", provider.provider_id + ":1", provider)
        tx.insert("config:lists", provider_list.list_id + ":1", provider_list)
        tx.insert("workloads", "chat", models.Workload(tenant_id=tenant, workload_id="chat", display_name="Chat", mode="remote", allowed_stages=("input", "output"), provider_ids=(provider.provider_id,), quota_id=quota.quota_id, required_state="disabled"))
        for role in ("author", "approver", "operator", "tenant_admin", "workload", "runtime-a", "runtime-b"):
            actual = "runtime" if role.startswith("runtime-") else role
            actor = service.identity.provision_principal(tx, role, [actual])
            tokens[role] = service.identity.issue_credential(tx, actor["principal_id"], workload_id="chat" if role == "workload" else None)["one_time_secret"]
    actors = {role: service.identity.authenticate(token) for role, token in tokens.items()}
    runtime_ids = [str(uuid4()), str(uuid4())]
    with repo.transaction(tenant) as tx:
        for runtime_id in runtime_ids:
            tx.insert("runtimes", runtime_id, models.RuntimeInstall(runtime_id=runtime_id, tenant_id=tenant, zone="local-shared", mode="remote", public_key_id="test-key", version="phase2-text", telemetry="none"))
    enrollment = RuntimeEnrollment(repo, service.identity, service.audit)
    for index, runtime_id in enumerate(runtime_ids):
        enrollment.enroll(tokens["operator"], runtime_id=runtime_id, principal_id=actors[("runtime-a", "runtime-b")[index]].principal_id, workload_ids=["chat"], expected_runtime_revision=1, expected_target_revisions={"chat": index})
    payload = {"rules": [{"rule_id": "email", "kind": "pii", "stages": ["input", "output"], "action": "block", "mandatory": mandatory, "minimum_state": "disabled", "config": {"entities": ["email", "international_phone"]}, "evaluator_revision": "pii-v2"}], "residency_ref": ref(residency, "residency_id"), "quota_ref": ref(quota, "quota_id"), "list_refs": [ref(provider_list, "list_id")], "provider_refs": [ref(provider, "provider_id")]}
    with repo.transaction(tenant) as tx:
        policy = service.governance.submit(tx, actors["author"], "policy", payload)
        service.governance.review(tx, actors["approver"], "policy", 1, outcome="approve", compiled_digest=policy["compiled_digest"], reason="Reviewed")
        policy_ref = {"tenant_id": tenant, "policy_id": "policy", "version": 1, "digest": policy["compiled_digest"]}
        if baseline:
            service.governance.publish_baseline(tx, actors["tenant_admin"], [policy_ref], 0)
        service.bindings.release(tx, actors["operator"], policy=policy_ref, workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": 0}, reason="Observe first")
    key = Ed25519PrivateKey.generate()
    distributor = SnapshotDistributor(repo, service.governance, service.bindings, key, "test-key")
    stores = [RuntimeSnapshotStore(tmp_path / (runtime_id + ".json"), tenant, runtime_id, {"test-key": key.public_key()}) for runtime_id in runtime_ids]
    system = {"repo": repo, "dsn": dsn, "service": service, "tenant": tenant, "provider": provider, "payload": payload, "policy": policy, "policy_ref": policy_ref, "tokens": tokens, "actors": actors, "runtime_ids": runtime_ids, "stores": stores, "distributor": distributor, "key": key, "revision": 1}
    refresh(system)
    if state != "observe":
        refresh(system, state=state)
    return system


def refresh(system, *, state=None, policy_ref=None):
    repo, tenant, service = system["repo"], system["tenant"], system["service"]
    if state is not None:
        with repo.transaction(tenant) as tx:
            service.bindings.release(tx, system["actors"]["operator"], policy=policy_ref or system["policy_ref"], workload_ids=["chat"], stage="canary", state=state, expected_revisions={"chat": system["revision"]}, reason="Evaluated canary")
        system["revision"] += 1
    for index, store in enumerate(system["stores"]):
        actor = system["actors"][("runtime-a", "runtime-b")[index]]
        with repo.transaction(tenant) as tx:
            snapshot = system["distributor"].issue(tx, actor)
        receipts = store.apply(snapshot)
        for receipt in receipts:
            with repo.transaction(tenant) as tx:
                system["distributor"].accept_receipt(tx, actor, receipt)


def config(system, index=0):
    return ServiceConfig(mode="remote", runtime_id=system["runtime_ids"][index], database_url_file="/mounted/database", tls_cert_file="/mounted/cert", tls_key_file="/mounted/key", tls_ca_file="/mounted/ca", worker_count=2, queue_limit=4)


def runtime(system, index=0, **updates):
    return EvaluationRuntime(TenantRepository(system["dsn"]), config=config(system, index), runtime_slots={system["tenant"]: RuntimeSlot(system["stores"][index])}, **updates)


def request(system, *, text="Contact synthetic-runtime@example.test.", stage="input", context_id=None, **updates):
    return {"request_id": str(uuid4()), "correlation_id": str(uuid4()), "tenant_id": system["tenant"], "workload_id": "chat", "stage": stage, "parts": [{"part_id": "content", "kind": "text", "role": "assistant" if stage == "output" else "user", "text": text}], "context_id": context_id, "provider_id": system["provider"].provider_id, "deadline_ms": 500, "schema_version": "guardrails-v2", **updates}


def http(server, token):
    return httpx.Client(base_url=server["url"], verify=ssl.create_default_context(cafile=server["ca_file"]), trust_env=False, timeout=2, headers={"Authorization": "Bearer " + token})


@pytest.mark.parametrize("state,expected", [("disabled", "allow"), ("observe", "allow"), ("warn", "warn"), ("redact", "redact"), ("block", "block")])
def test_real_https_enforcement_state_and_safe_audit(tmp_path, state, expected):
    system = fixture_system(tmp_path, state=state)
    try:
        with serve_https(lambda: create_app(service=runtime(system)), tmp_path / "tls") as server, http(server, system["tokens"]["workload"]) as client:
            first = client.post("/v2/evaluations", json=request(system))
            assert first.status_code == 200, first.text
            decision = models.Decision.model_validate_json(first.content)
            assert decision.action == expected
            assert decision.policy.digest == system["policy_ref"]["digest"]
            assert decision.binding_revision == system["revision"]
            assert decision.audit_status == "persisted" and decision.evaluation_ms < 500
            assert len(decision.findings) == (0 if state == "disabled" else 1)
            if state == "redact":
                assert decision.transformed_parts[0].text == "Contact [REDACTED:email]."
            else:
                assert decision.transformed_parts is None
            if expected != "block":
                output = client.post("/v2/evaluations", json=request(system, stage="output", context_id=decision.context_id))
                assert output.status_code == 200, output.text
                assert output.json()["action"] == expected
                assert output.json()["policy"] == first.json()["policy"]
            with system["repo"].transaction(system["tenant"]) as tx:
                events = [item for item in tx.list("audit") if item["event_type"] == "evaluation"]
                assert len(events) == (1 if expected == "block" else 2)
                assert all(item["actor_id"] == system["actors"]["workload"].principal_id for item in events)
                rows = list(tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant", {"tenant": system["tenant"]}))
                assert "synthetic-runtime@example.test" not in json.dumps([row.data for row in rows])
    finally:
        system["service"].audit.close()
        system["repo"].close()


def test_real_https_scope_shape_quota_dedup_and_capabilities(tmp_path):
    system = fixture_system(tmp_path)
    try:
        with serve_https(lambda: create_app(service=runtime(system)), tmp_path / "tls") as server, http(server, system["tokens"]["workload"]) as client:
            assert client.get("/health/live").json() == {"status": "alive"}
            assert client.get("/health/ready").json()["ready"] is True
            capabilities = client.get("/v2/capabilities")
            assert capabilities.status_code == 200
            declared = capabilities.json()
            # The workload sees only capabilities needed by its approved PII
            # policy, including the newly supported structured part kind.
            assert len(declared) == 2
            assert {(item["capability_id"], item["stage"]) for item in declared} == {
                ("rules-pii", stage) for stage in ("input", "output")
            }
            assert all(item["part_kinds"] == ["text", "json"] and item["status"] == "enabled"
                       for item in declared)
            data = request(system)
            first = client.post("/v2/evaluations", json=data)
            assert first.status_code == 200, first.text
            replay = client.post("/v2/evaluations", json=data)
            assert replay.status_code == 409 and replay.json()["error"]["code"] == "idempotency_conflict"
            invalid = client.post("/v2/evaluations", json=request(system, tenant_id="foreign"))
            assert invalid.status_code == 404
            # Phase 3 supports text and structured JSON. Image inspection remains
            # unavailable here and must still fail before quota or detector work.
            unsupported = client.post("/v2/evaluations", json=request(system, parts=[{
                "part_id": "image", "kind": "image", "role": "user",
                "media_type": "image/png", "data_base64": "c3ludGhldGlj",
            }]))
            assert unsupported.status_code == 400 and unsupported.json()["error"]["code"] == "unsupported_surface"
            duplicate = client.post("/v2/evaluations", content='{"request_id":"bad","request_id":"bad"}', headers={"content-type": "application/json"})
            assert duplicate.status_code == 400
            unauthenticated = client.post("/v2/evaluations", content=b"malformed", headers={"Authorization": "Bearer invalid"})
            assert unauthenticated.status_code == 401
            oversized = client.post("/v2/evaluations", json=request(system, text="x" * 65537))
            assert oversized.status_code == 413 and oversized.json()["error"]["code"] == "payload_too_large"
            with system["repo"].transaction(system["tenant"]) as tx:
                assert len(tx.list("quota_leases")) == 1
                assert len([item for item in tx.list("audit") if item["event_type"] == "evaluation"]) == 1
    finally:
        system["repo"].close()


def test_two_https_replicas_keep_old_policy_context_and_reject_revocation(tmp_path):
    system = fixture_system(tmp_path)
    try:
        with serve_https(lambda: create_app(service=runtime(system, 0)), tmp_path / "tls-a") as a, serve_https(lambda: create_app(service=runtime(system, 1)), tmp_path / "tls-b") as b, http(a, system["tokens"]["workload"]) as client_a, http(b, system["tokens"]["workload"]) as client_b:
            one = client_a.post("/v2/evaluations", json=request(system))
            two = client_a.post("/v2/evaluations", json=request(system))
            assert one.status_code == two.status_code == 200
            with system["repo"].transaction(system["tenant"]) as tx:
                new = system["service"].governance.submit(tx, system["actors"]["author"], "policy", system["payload"])
                system["service"].governance.review(tx, system["actors"]["approver"], "policy", 2, outcome="approve", compiled_digest=new["compiled_digest"], reason="New revision")
            new_ref = {**system["policy_ref"], "version": 2, "digest": new["compiled_digest"]}
            refresh(system, state="observe", policy_ref=new_ref)
            output = client_b.post("/v2/evaluations", json=request(system, stage="output", context_id=one.json()["context_id"]))
            assert output.status_code == 200, output.text
            assert output.json()["policy"] == one.json()["policy"]
            assert output.json()["binding_revision"] == 1
            assert output.json()["snapshot_generation"] == one.json()["snapshot_generation"]
            new_input = client_b.post("/v2/evaluations", json=request(system))
            assert new_input.status_code == 200 and new_input.json()["policy"] == new_ref
            with system["repo"].transaction(system["tenant"]) as tx:
                system["service"].governance.review(tx, system["actors"]["approver"], "policy", 1, outcome="revoke", compiled_digest=system["policy_ref"]["digest"], reason="Revocation")
            denied = client_b.post("/v2/evaluations", json=request(system, stage="output", context_id=two.json()["context_id"]))
            assert denied.status_code == 409 and denied.json()["error"]["code"] == "policy_revoked"
    finally:
        system["repo"].close()


def test_missing_published_tenant_quota_blocks_before_detector_or_reservation(tmp_path):
    system = fixture_system(tmp_path, baseline=False)
    try:
        with serve_https(lambda: create_app(service=runtime(system)), tmp_path / "tls") as server, http(server, system["tokens"]["workload"]) as client:
            response = client.post("/v2/evaluations", json=request(system))
            assert response.status_code == 503 and response.json()["error"]["code"] == "policy_unresolved"
            with system["repo"].transaction(system["tenant"]) as tx:
                assert tx.list("quota_leases") == []
                assert tx.list("evaluation_contexts") == []
    finally:
        system["repo"].close()


def test_worker_deadline_and_cancellation_reclaim_process_and_quota(tmp_path):
    system = fixture_system(tmp_path, mandatory=True)
    async def exercise():
        service = runtime(system)
        await service.start()
        try:
            identity = service.identity.authenticate(system["tokens"]["workload"])
            pool = service.registry._pools[system["tenant"]]
            original = pool.worker_pids
            for pid in original:
                os.kill(pid, signal.SIGSTOP)
            started = time.monotonic()
            response = await service.evaluate(identity, models.EvaluationRequest.model_validate(request(system)), started=started, deadline=started + 0.5)
            assert response.action == "block" and "evaluation_timeout" in response.reason_codes
            assert time.monotonic() - started < 0.65
            async with asyncio.timeout(5):
                while pool.health()["replacements"] < 1:
                    await asyncio.sleep(0.01)
            # The other SIGSTOPped worker remains owned and is reclaimed on close.
            active = asyncio.create_task(service.evaluate(identity, models.EvaluationRequest.model_validate(request(system)), started=time.monotonic(), deadline=time.monotonic() + 0.5))
            async with asyncio.timeout(1):
                while pool.health()["admitted"] != 1:
                    await asyncio.sleep(0.001)
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
            async with asyncio.timeout(2):
                while service._cleanup:
                    await asyncio.sleep(0.01)
            with system["repo"].transaction(system["tenant"]) as tx:
                assert all(row["state"] != "active" for row in tx.list("quota_leases"))
        finally:
            await service.close()
            service.repository.close()
        assert pool.worker_pids == ()
    try:
        asyncio.run(exercise())
    finally:
        system["repo"].close()
