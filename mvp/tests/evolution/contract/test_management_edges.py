"""Security and lifecycle gaps checked through the real API and PostgreSQL."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import base64
from uuid import uuid4

import pytest

from guardrails.evolution import models
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.identity import SESSION_COOKIE
from guardrails.evolution.management_api import CANARY_CASE_IDS, ManagementApi, create_app

from test_management import system, headers, submit, approve, reference


def test_configuration_create_retains_exact_immutable_versions(system):
    repo, service, tenant, identities, tokens, payload, client = system
    path = f"/v2/tenants/{tenant}/lists"
    body = {"kind": "content", "deny": ["term:confidential"], "allow": [], "allow_enabled": False}
    first = client.post(path, json=body, headers=headers(tokens, "author"))
    second = client.post(path, json={**body, "deny": ["term:restricted"]}, headers=headers(tokens, "author"))
    assert first.status_code == second.status_code == 201
    assert first.json()["list_id"] != second.json()["list_id"]
    assert first.json()["version"] == second.json()["version"] == 1
    forbidden = client.post(path, json={**body, "list_id": first.json()["list_id"]}, headers=headers(tokens, "author"))
    assert forbidden.status_code == 422
    with repo.transaction(tenant) as tx:
        previous = tx.get("config:lists", first.json()["list_id"] + ":1", required=True)
        assert previous["deny"] == ["term:confidential"]
        assert models.resource_digest(models.ListSet.model_validate(previous)) == previous["digest"]


def test_reader_never_receives_nested_provider_credential_references(system):
    repo, service, tenant, identities, tokens, payload, client = system
    provider_body = {"kind": "evaluator", "vendor": "local_classifier", "model_id": "frozen", "zone": "local-shared", "credential_ref": "PRIVATE_REFERENCE_CANARY", "enabled": True}
    provider = client.post(f"/v2/tenants/{tenant}/providers", json=provider_body, headers=headers(tokens, "admin"))
    assert provider.status_code == 201, provider.text
    assert "PRIVATE_REFERENCE_CANARY" not in provider.text
    provider = provider.json()
    command = {**payload, "provider_refs": [{"id": provider["provider_id"], "version": provider["version"], "digest": provider["digest"]}]}
    created = client.post(f"/v2/tenants/{tenant}/policies/private/versions", json=command, headers=headers(tokens, "author"))
    assert created.status_code == 201, created.text
    assert "PRIVATE_REFERENCE_CANARY" not in created.text
    detail = client.get(f"/v2/tenants/{tenant}/policies/private/versions/1", headers=headers(tokens, "viewer"))
    assert detail.status_code == 200
    assert "credential_ref" not in detail.text
    with repo.transaction(tenant) as tx:
        exact = tx.get("policies", "private:1", required=True)
        assert exact["providers"][0]["credential_ref"] == "PRIVATE_REFERENCE_CANARY"


def test_credential_replay_conflicts_without_second_secret_or_row(system):
    repo, service, tenant, identities, tokens, payload, client = system
    principal_id = identities["viewer"].principal_id
    path = f"/v2/tenants/{tenant}/principals/{principal_id}/credentials"
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    auth = headers(tokens, "admin")
    first = client.post(path, json={"expires_at": expiry}, headers=auth)
    assert first.status_code == 201
    token = first.json()["one_time_secret"]
    replay = client.post(path, json={"expires_at": expiry}, headers=auth)
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "idempotency_conflict"
    assert token not in replay.text
    with repo.transaction(tenant) as tx:
        rows = [row for row in tx.list("credentials") if row["credential_id"] == first.json()["credential_id"]]
        assert len(rows) == 1
        assert token not in json.dumps(tx.list("idempotency"))
        assert token not in json.dumps(rows)


def test_release_and_resource_reference_types_are_not_coerced(system):
    repo, service, tenant, identities, tokens, payload, client = system
    candidate = submit(system)
    approve(system, candidate)
    release = {"policy": reference(candidate), "workload_ids": ["chat"], "stage": "canary", "state": "observe", "expected_revisions": {"chat": False}, "reason": "Test"}
    bad = client.post(f"/v2/tenants/{tenant}/releases", json=release, headers=headers(tokens, "operator"))
    assert bad.status_code == 422
    bad_ref = deepcopy(payload)
    bad_ref["quota_ref"]["version"] = "1"
    invalid = client.post(f"/v2/tenants/{tenant}/policies/new/versions", json=bad_ref, headers=headers(tokens, "author"))
    assert invalid.status_code == 422
    with repo.transaction(tenant) as tx:
        assert tx.list("bindings") == []
        assert len(tx.list("policies")) == 1


def test_policy_listing_uses_latest_version_and_current_status(system):
    repo, service, tenant, identities, tokens, payload, client = system
    submit(system)
    latest = submit(system)
    approve(system, latest)
    with repo.transaction(tenant) as tx:
        other = service.governance.submit(tx, identities["author"], "other", payload)
    page = client.get(f"/v2/tenants/{tenant}/policies", headers=headers(tokens, "viewer"))
    assert page.status_code == 200
    assert len(page.json()["items"]) == 2
    summaries = {row["policy_id"]: row for row in page.json()["items"]}
    assert summaries["policy"]["version"] == 2
    assert summaries["policy"]["status"] == "approved"
    filtered = client.get(f"/v2/tenants/{tenant}/policies?status=candidate", headers=headers(tokens, "viewer"))
    assert filtered.json()["items"] == [{"policy_id": "other", "version": 1, "status": "candidate"}]


def test_audit_time_window_is_enforced_and_cursor_binds_filters(system):
    repo, service, tenant, identities, tokens, payload, client = system
    submit(system)
    submit(system)
    path = f"/v2/tenants/{tenant}/audit"
    bad = client.get(path, params={"from": "2026-01-01T00:00:00Z", "until": "2026-02-01T00:00:00Z"}, headers=headers(tokens, "auditor"))
    assert bad.status_code == 422
    empty = client.get(path, params={"from": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}, headers=headers(tokens, "auditor"))
    assert empty.status_code == 200 and empty.json()["items"] == []
    page = client.get(path, params={"limit": 1}, headers=headers(tokens, "auditor"))
    assert page.status_code == 200 and page.json()["next_cursor"]
    forged = client.get(path, params={"limit": 1, "cursor": page.json()["next_cursor"], "action": "block"}, headers=headers(tokens, "auditor"))
    assert forged.status_code == 400


def test_browser_session_secure_cookie_csrf_origin_and_revocation(system):
    repo, service, tenant, identities, tokens, payload, client = system
    login = client.post("/admin/session", json={"credential": tokens["viewer"]}, headers={"Origin": "https://management.test"})
    assert login.status_code == 201, login.text
    cookie = login.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert tokens["viewer"] not in login.text
    session_secret = client.cookies.get(SESSION_COOKIE)
    rejected = client.delete("/admin/session", headers={"Origin": "https://management.test"})
    assert rejected.status_code == 403
    cross = client.delete("/admin/session", headers={"Origin": "https://foreign.test", "X-CSRF-Token": login.json()["csrf_token"]})
    assert cross.status_code == 403
    logout = client.delete("/admin/session", headers={"Origin": "https://management.test", "X-CSRF-Token": login.json()["csrf_token"]})
    assert logout.status_code == 200
    with pytest.raises(EvolutionError) as denied:
        service.identity.authenticate_session(session_secret)
    assert denied.value.code == "session_expired"


def test_session_login_throttles_failed_handle_without_secret_storage(system):
    repo, service, tenant, identities, tokens, payload, client = system
    bad_token = tokens["viewer"][:-1] + ("a" if tokens["viewer"][-1] != "a" else "b")
    results = [client.post("/admin/session", json={"credential": bad_token}, headers={"Origin": "https://management.test"}) for _ in range(6)]
    assert [result.status_code for result in results] == [401, 401, 401, 401, 401, 429]
    assert results[-1].headers["retry-after"] == "60"
    with repo.transaction("_platform") as tx:
        assert bad_token not in json.dumps(tx.list("login_rate", limit=1000))


def test_workload_cannot_reference_foreign_or_missing_config(system):
    repo, service, tenant, identities, tokens, payload, client = system
    command = {"workload_id": "unknown", "display_name": "Unknown", "mode": "remote", "allowed_stages": ["input"], "provider_ids": [], "quota_id": str(uuid4()), "required_state": "observe"}
    response = client.post(f"/v2/tenants/{tenant}/workloads", json=command, headers=headers(tokens, "admin"))
    assert response.status_code == 404
    with repo.transaction(tenant) as tx:
        assert tx.get("workloads", "unknown") is None


def test_baseline_publication_marks_current_binding_blocked_atomically(system):
    repo, service, tenant, identities, tokens, payload, client = system
    old = submit(system)
    approve(system, old)
    with repo.transaction(tenant) as tx:
        service.bindings.release(tx, identities["operator"], policy=reference(old), workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": 0}, reason="Observe")
    stronger = deepcopy(payload)
    stronger["rules"][0]["minimum_state"] = "block"
    with repo.transaction(tenant) as tx:
        new = service.governance.submit(tx, identities["author"], "stronger", stronger)
    with repo.transaction(tenant) as tx:
        service.governance.review(tx, identities["approver"], "stronger", 1, outcome="approve", compiled_digest=new["compiled_digest"], reason="Floor")
        service.governance.publish_baseline(tx, identities["admin"], [reference(new)], 0)
    with repo.transaction(tenant) as tx:
        blocked = tx.get("binding_blocks", "chat", required=True)
        assert blocked["reason_code"] == "floor_conflict"
        assert blocked["binding_revision"] == 1
        assert any(row["type"] == "baseline_published" and row["blocked_workload_ids"] == ["chat"] for row in tx.list("outbox"))


def _runtime(system, candidate, revisions):
    repo, service, tenant, identities, *_ = system
    runtime_id = str(uuid4())
    with repo.transaction(tenant) as tx:
        actor = service.identity.provision_principal(tx, "Runtime", ["runtime"])
        secret = service.identity.issue_credential(tx, actor["principal_id"])["one_time_secret"]
        tx.insert("runtimes", runtime_id, {"runtime_id": runtime_id, "principal_id": actor["principal_id"], "status": "ready", "zone": "local-shared", "mode": "remote"})
        tx.insert("runtime_principals", actor["principal_id"], {"runtime_id": runtime_id, "principal_id": actor["principal_id"], "tenant_id": tenant}, immutable=True)
        tx.insert("runtime_enrollments", runtime_id, {"runtime_id": runtime_id, "principal_id": actor["principal_id"], "tenant_id": tenant}, immutable=True)
        tx.insert("runtime_targets", "chat", {"runtime_ids": [runtime_id]})
        for revision in revisions:
            binding = tx.get("bindings", f"chat:{revision}", required=True)
            tx.insert("issued_snapshots", f"{runtime_id}:{revision}", {"bindings": [binding], "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")})
        tx.insert("snapshot_pointers", runtime_id, {"generation": max(revisions)})
    return runtime_id, service.identity.authenticate(secret)


def _receipt(tenant, runtime_id, candidate, revision, **updates):
    return {"runtime_id": runtime_id, "tenant_id": tenant, "workload_id": "chat", "binding_revision": revision, "snapshot_generation": revision, "digest": candidate["compiled_digest"], "applied_at": models.utc_now(), "health": "ready", **updates}


def test_receipt_replay_and_old_generation_never_regress_applied_state(system):
    repo, service, tenant, identities, *_ = system
    candidate = submit(system)
    approve(system, candidate)
    with repo.transaction(tenant) as tx:
        for previous in (0, 1):
            service.bindings.release(tx, identities["operator"], policy=reference(candidate), workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": previous}, reason="Canary")
    runtime_id, runtime_identity = _runtime(system, candidate, [1, 2])
    second = _receipt(tenant, runtime_id, candidate, 2)
    with repo.transaction(tenant) as tx:
        service.bindings.receipt(tx, runtime_identity, second)
    with repo.transaction(tenant) as tx:
        service.bindings.receipt(tx, runtime_identity, second)
        service.bindings.receipt(tx, runtime_identity, _receipt(tenant, runtime_id, candidate, 1))
    with repo.transaction(tenant) as tx:
        assert tx.get("applied_bindings", "chat")["binding_revision"] == 2
        assert len(tx.list("receipts")) == 2


def test_failed_runtime_receipt_preserves_old_applied_pointer(system):
    repo, service, tenant, identities, *_ = system
    candidate = submit(system)
    approve(system, candidate)
    with repo.transaction(tenant) as tx:
        release = service.bindings.release(tx, identities["operator"], policy=reference(candidate), workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": 0}, reason="Canary")
    runtime_id, runtime_identity = _runtime(system, candidate, [1])
    with repo.transaction(tenant) as tx:
        service.bindings.receipt(tx, runtime_identity, _receipt(tenant, runtime_id, candidate, 1, health="failed"))
    with repo.transaction(tenant) as tx:
        assert tx.get("applied_bindings", "chat") is None
        assert tx.get("releases", release["release_id"])["status"] == "failed"


def test_initial_binding_requires_observe_when_mandatory_floor_permits(system):
    repo, service, tenant, identities, tokens, payload, client = system
    candidate = submit(system)
    approve(system, candidate)
    for state in ("warn", "redact", "block"):
        command = {"policy": reference(candidate), "workload_ids": ["chat"], "stage": "canary", "state": state, "expected_revisions": {"chat": 0}, "reason": "Onboarding"}
        response = client.post(f"/v2/tenants/{tenant}/releases", json=command, headers=headers(tokens, "operator"))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "floor_conflict"
    with repo.transaction(tenant) as tx:
        assert tx.list("bindings") == []


def test_platform_tenant_create_replays_atomically_and_provisions_scoped_admin(system):
    repo, service, tenant, identities, tokens, payload, client = system
    with repo.transaction("_platform") as tx:
        actor = service.identity.provision_principal(tx, "Platform", ["platform_admin"])
        secret = service.identity.issue_credential(tx, actor["principal_id"])["one_time_secret"]
    platform = {"Authorization": "Bearer " + secret, "Idempotency-Key": str(uuid4())}
    new_tenant = "new-" + uuid4().hex[:20]
    command = {"tenant_id": new_tenant, "display_name": "New tenant"}
    first = client.post("/v2/tenants", json=command, headers=platform)
    assert first.status_code == 201, first.text
    second = client.post("/v2/tenants", json=command, headers=platform)
    assert second.status_code == 201 and first.json() == second.json()
    support = {**platform, "Idempotency-Key": str(uuid4())}
    created = client.post(f"/v2/tenants/{new_tenant}/principals", json={"display_name": "First admin", "roles": ["tenant_admin"]}, headers=support)
    assert created.status_code == 201, created.text
    forbidden = client.get(f"/v2/tenants/{new_tenant}/audit", headers=platform)
    assert forbidden.status_code == 404
    with repo.transaction(new_tenant) as tx:
        assert len(tx.list("principals")) == 1
        assert len(tx.list("config:quotas")) == 1
        assert len(tx.list("config:residency")) == 1
    with repo.transaction("_platform") as tx:
        audit = [row for row in tx.list("audit", limit=1000) if row["event_type"] == "tenant_created" and row["metadata"]["resource_id"] == new_tenant]
        assert len(audit) == 1


def test_openapi_contains_strict_commands_real_responses_and_security():
    from jsonschema import Draft202012Validator

    schema = create_app(service=ManagementApi(object()), distributor=object()).openapi()
    components = schema["components"]
    for path, operations in schema["paths"].items():
        if path == "/health/live":
            continue
        for method, operation in operations.items():
            if method in {"post", "patch"}:
                assert operation["requestBody"]["required"] is True
            assert "security" in operation
            success = [code for code in operation["responses"] if code.startswith("2")]
            assert len(success) == 1
            assert operation["responses"][success[0]]["content"]["application/json"]["schema"]["$ref"].startswith("#/components/schemas/")
    candidate = {"$ref": "#/components/schemas/PolicyCandidateCommand", "components": components}
    validator = Draft202012Validator(candidate)
    invalid = {"rules": [], "residency_ref": {"id": str(uuid4()), "version": True, "digest": "0" * 64}, "quota_ref": {"id": str(uuid4()), "version": 1, "digest": "0" * 64}, "unknown": "secret"}
    assert list(validator.iter_errors(invalid))
    assert "credential_ref" not in components["schemas"]["ProviderProfile"]["properties"]
    assert "credential_ref" in components["schemas"]["ProviderProfileCreate"]["properties"]


def test_signed_canary_evidence_allows_full_release_only_after_real_receipt(system):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi.testclient import TestClient
    from guardrails.evolution.snapshots import SnapshotDistributor

    repo, service, tenant, identities, tokens, payload, original_client = system
    candidate = submit(system)
    approve(system, candidate)
    command = {"policy": reference(candidate), "workload_ids": ["chat"], "stage": "canary", "state": "observe", "expected_revisions": {"chat": 0}, "reason": "Canary"}
    release_response = original_client.post(f"/v2/tenants/{tenant}/releases", json=command, headers=headers(tokens, "operator"))
    assert release_response.status_code == 202
    release = release_response.json()
    runtime_id = str(uuid4())
    with repo.transaction(tenant) as tx:
        actor = service.identity.provision_principal(tx, "Runtime", ["runtime"])
        token = service.identity.issue_credential(tx, actor["principal_id"])["one_time_secret"]
        tx.insert("runtimes", runtime_id, {"runtime_id": runtime_id, "tenant_id": tenant, "principal_id": actor["principal_id"], "status": "ready", "zone": "local-shared", "mode": "remote"})
        tx.insert("runtime_principals", actor["principal_id"], {"runtime_id": runtime_id, "principal_id": actor["principal_id"], "tenant_id": tenant}, immutable=True)
        tx.insert("runtime_enrollments", runtime_id, {"runtime_id": runtime_id, "principal_id": actor["principal_id"], "tenant_id": tenant}, immutable=True)
        tx.insert("runtime_targets", "chat", {"workload_id": "chat", "runtime_ids": [runtime_id]})
        tx.insert("workloads", "other", {**tx.get("workloads", "chat", required=True), "workload_id": "other"})
        # Historical noise must not hide the one current target acknowledgement.
        for number in range(120):
            tx.insert("receipts", f"unrelated:{number}", {"runtime_id": str(uuid4()), "workload_id": "other", "binding_revision": 1, "snapshot_generation": 1, "digest": "0" * 64, "health": "ready"}, immutable=True)
    key = Ed25519PrivateKey.generate()
    evidence_key = Ed25519PrivateKey.generate()
    service.evidence_keys["trusted-runner"] = evidence_key.public_key()
    distributor = SnapshotDistributor(repo, service.governance, service.bindings, key, "snapshot-key")
    with TestClient(create_app(service=service, distributor=distributor), base_url="https://management.test") as client:
        runtime_headers = {"Authorization": "Bearer " + token}
        snapshot_response = client.get("/v2/runtime/snapshot", headers=runtime_headers)
        assert snapshot_response.status_code == 200, snapshot_response.text
        snapshot = snapshot_response.json()
        key.public_key().verify(base64.b64decode(snapshot["signature"]), models.canonical_json_bytes({name: value for name, value in snapshot.items() if name != "signature"}))
        receipt = _receipt(tenant, runtime_id, candidate, 1, snapshot_generation=snapshot["generation"])
        acknowledged = client.post("/v2/runtime/receipts", json=receipt, headers={**runtime_headers, "Idempotency-Key": str(uuid4())})
        assert acknowledged.status_code == 200, acknowledged.text
        stamp = models.utc_now()
        report = {"run_id": str(uuid4()), "created_at": stamp, "source_commit": "a" * 40, "source_digest": "b" * 64, "lock_digest": "c" * 64, "profile": "canary", "phase": 1, "started_at": stamp, "finished_at": stamp, "cases": [{"case_id": identifier, "requirement_id": "P1.7", "status": "pass", "duration_ms": 1.0, "assertions": 1, "reason_codes": []} for identifier in sorted(CANARY_CASE_IDS)], "artifact_digests": [], "status": "pass", "environment": {"tenant_id": tenant, "release_id": release["release_id"], "policy_digest": candidate["compiled_digest"]}, "key_id": "trusted-runner"}
        malformed = {**report, "run_id": str(uuid4()), "cases": [{**report["cases"][0], "case_id": "unrelated-case"}]}
        malformed["signature"] = base64.b64encode(evidence_key.sign(models.canonical_json_bytes(malformed))).decode()
        denied = client.post(f"/v2/tenants/{tenant}/evidence", json=malformed, headers=headers(tokens, "operator"))
        assert denied.status_code == 409
        report["signature"] = base64.b64encode(evidence_key.sign(models.canonical_json_bytes(report))).decode()
        evidence = client.post(f"/v2/tenants/{tenant}/evidence", json=report, headers=headers(tokens, "operator"))
        assert evidence.status_code == 201, evidence.text
        with repo.transaction(tenant) as tx:
            old = tx.get("snapshot_pointers", runtime_id, required=True)
            tx.put("snapshot_pointers", runtime_id, {**old, "generation": snapshot["generation"] + 1}, expected_revision=old["revision"])
        premature = client.post(f"/v2/tenants/{tenant}/releases", json={**command, "stage": "full", "expected_revisions": {"chat": 1}, "canary_evidence_id": report["run_id"]}, headers=headers(tokens, "operator"))
        assert premature.status_code == 503
        assert premature.json()["error"]["code"] == "snapshot_stale"
        with repo.transaction(tenant) as tx:
            old = tx.get("snapshot_pointers", runtime_id, required=True)
            tx.put("snapshot_pointers", runtime_id, {**old, "generation": snapshot["generation"]}, expected_revision=old["revision"])
        promoted = client.post(f"/v2/tenants/{tenant}/releases", json={**command, "stage": "full", "expected_revisions": {"chat": 1}, "canary_evidence_id": report["run_id"]}, headers=headers(tokens, "operator"))
        assert promoted.status_code == 202, promoted.text
        assert promoted.json()["stage"] == "full" and promoted.json()["status"] == "pending"
        stale_report = {**report, "run_id": str(uuid4())}
        stale_report.pop("signature")
        stale_report["signature"] = base64.b64encode(evidence_key.sign(models.canonical_json_bytes(stale_report))).decode()
        stale = client.post(f"/v2/tenants/{tenant}/evidence", json=stale_report, headers=headers(tokens, "operator"))
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "revision_conflict"
    with repo.transaction(tenant) as tx:
        assert tx.get("releases", release["release_id"])["status"] == "applied"
        assert tx.get("applied_bindings", "chat")["binding_revision"] == 1
        assert tx.get("binding_pointers", "chat")["binding_revision"] == 2


def test_cursor_rejects_noncanonical_encoding_and_unknown_filters(system):
    repo, service, tenant, identities, tokens, payload, client = system
    submit(system)
    with repo.transaction(tenant) as tx:
        service.governance.submit(tx, identities["author"], "other", payload)
    path = f"/v2/tenants/{tenant}/policies"
    page = client.get(path, params={"limit": 1}, headers=headers(tokens, "viewer"))
    cursor = page.json()["next_cursor"]
    for suffix in ("!", "=", "\n"):
        response = client.get(path, params={"limit": 1, "cursor": cursor + suffix}, headers=headers(tokens, "viewer"))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_cursor"
    response = client.get(f"/v2/tenants/{tenant}/workloads", params={"forged_tenant": "foreign"}, headers=headers(tokens, "viewer"))
    assert response.status_code == 422


def test_workload_principal_cannot_read_management_tenant_records(system):
    repo, service, tenant, identities, tokens, payload, client = system
    with repo.transaction(tenant) as tx:
        actor = service.identity.provision_principal(tx, "Application", ["workload"])
        token = service.identity.issue_credential(tx, actor["principal_id"], workload_id="chat")["one_time_secret"]
    response = client.get("/v2/tenants", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 403
    assert "residency_id" not in response.text


def test_rollout_cannot_apply_using_expired_or_revoked_peer_runtime(system):
    repo, service, tenant, identities, tokens, payload, client = system
    candidate = submit(system)
    approve(system, candidate)
    for index, invalidation in enumerate(("expired", "revoked", "new-generation")):
        with repo.transaction(tenant) as tx:
            # Each scenario gets its own target set; restore the previous set
            # before issuing the next release so the release itself is eligible.
            for runtime_id in (tx.get("runtime_targets", "chat") or {}).get("runtime_ids", []):
                old = tx.get("runtimes", runtime_id, required=True)
                if old["status"] == "revoked":
                    tx.put("runtimes", runtime_id, {**old, "status": "ready"}, expected_revision=old["revision"])
            release = service.bindings.release(tx, identities["operator"], policy=reference(candidate), workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": index}, reason="Peer check")
        runtime_ids, runtime_identities = [], []
        for number in range(2):
            runtime_id = str(uuid4())
            with repo.transaction(tenant) as tx:
                actor = service.identity.provision_principal(tx, f"Runtime {number}", ["runtime"])
                token = service.identity.issue_credential(tx, actor["principal_id"])["one_time_secret"]
                tx.insert("runtimes", runtime_id, {"runtime_id": runtime_id, "status": "registered", "zone": "local-shared", "mode": "remote"})
                tx.insert("runtime_principals", actor["principal_id"], {"runtime_id": runtime_id, "principal_id": actor["principal_id"], "tenant_id": tenant}, immutable=True)
                tx.insert("runtime_enrollments", runtime_id, {"runtime_id": runtime_id, "principal_id": actor["principal_id"], "tenant_id": tenant}, immutable=True)
                binding = tx.get("bindings", f"chat:{index + 1}", required=True)
                tx.insert("issued_snapshots", f"{runtime_id}:1", {"bindings": [binding], "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")})
                tx.insert("snapshot_pointers", runtime_id, {"generation": 1})
            runtime_ids.append(runtime_id)
            runtime_identities.append(service.identity.authenticate(token))
        with repo.transaction(tenant) as tx:
            old = tx.get("runtime_targets", "chat")
            targets = {"workload_id": "chat", "runtime_ids": runtime_ids}
            if old:
                tx.put("runtime_targets", "chat", targets, expected_revision=old["revision"])
            else:
                tx.insert("runtime_targets", "chat", targets)
            service.bindings.receipt(tx, runtime_identities[0], _receipt(tenant, runtime_ids[0], candidate, index + 1, snapshot_generation=1))
        with repo.transaction(tenant) as tx:
            if invalidation == "expired":
                old = tx.get("issued_snapshots", f"{runtime_ids[0]}:1", required=True)
                tx.put("issued_snapshots", f"{runtime_ids[0]}:1", {**old, "expires_at": "2020-01-01T00:00:00Z"}, expected_revision=old["revision"])
            elif invalidation == "revoked":
                old = tx.get("runtimes", runtime_ids[0], required=True)
                tx.put("runtimes", runtime_ids[0], {**old, "status": "revoked"}, expected_revision=old["revision"])
            else:
                old = tx.get("snapshot_pointers", runtime_ids[0], required=True)
                tx.put("snapshot_pointers", runtime_ids[0], {**old, "generation": 2}, expected_revision=old["revision"])
        with repo.transaction(tenant) as tx:
            service.bindings.receipt(tx, runtime_identities[1], _receipt(tenant, runtime_ids[1], candidate, index + 1, snapshot_generation=1))
            assert tx.get("applied_bindings", "chat") is None
            assert tx.get("releases", release["release_id"])["status"] == "pending"


def _platform_token(system):
    repo, service, *_ = system
    with repo.transaction("_platform") as tx:
        actor = service.identity.provision_principal(tx, "Evidence bootstrap", ["platform_admin"])
        return service.identity.issue_credential(tx, actor["principal_id"])["one_time_secret"]


def _signed_local_report(key, key_id):
    stamp = models.utc_now()
    report = {"run_id": str(uuid4()), "created_at": stamp, "source_commit": "a" * 40, "source_digest": "b" * 64, "lock_digest": "c" * 64, "profile": "local", "phase": 1, "started_at": stamp, "finished_at": stamp,
              "cases": [{"case_id": "contract-proof", "requirement_id": "P1.8", "status": "pass", "duration_ms": 1.0, "assertions": 1, "reason_codes": []}], "artifact_digests": [], "status": "pass", "environment": {"manifest_sha256": "d" * 64}, "key_id": key_id}
    report["signature"] = base64.b64encode(key.sign(models.canonical_json_bytes(report))).decode()
    return report


def test_evidence_trust_bootstrap_requires_platform_and_retains_public_bytes_only(system):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from guardrails.evolution.evidence_trust import EvidenceTrust

    repo, service, tenant, identities, tokens, *_ = system
    trust = EvidenceTrust(repo)
    key = Ed25519PrivateKey.generate()
    key_id = "runner-" + uuid4().hex
    for role in ("admin", "operator"):
        with pytest.raises(EvolutionError) as denied:
            trust.provision(tokens[role], key_id=key_id, public_key=key.public_key().public_bytes_raw())
        assert denied.value.code == "forbidden"
    platform = _platform_token(system)
    for invalid in (key, key.private_bytes_raw() + b"private", "not-public-bytes"):
        with pytest.raises(EvolutionError) as invalid_key:
            trust.provision(platform, key_id=key_id, public_key=invalid)
        assert invalid_key.value.code == "validation_failed"
    registered = trust.provision(platform, key_id=key_id, public_key=key.public_key().public_bytes_raw())
    assert registered["revision"] == 1
    with repo.transaction("_platform") as tx:
        row = tx.get("evidence_trust_roots", key_id, required=True)
        assert base64.b64decode(row["public_key_base64"]) == key.public_key().public_bytes_raw()
        assert base64.b64encode(key.private_bytes_raw()).decode() not in json.dumps(row)
        events = [item for item in tx.list("audit", limit=1000) if item["metadata"].get("resource_id") == key_id]
        assert len(events) == 1
        assert events[0]["event_type"] == "configuration_created"
    with repo.transaction(tenant) as tx:
        assert tx.get("evidence_trust_roots", key_id) is None
        trusted = trust.resolve(key_id, tx=tx)
        assert trusted.public_bytes_raw() == key.public_key().public_bytes_raw()
        assert tx.execute("SELECT current_setting('guardrails.tenant_id')").scalar_one() == tenant
        assert tx.get("evidence_trust_roots", key_id) is None


def test_assembled_api_verifies_persisted_evidence_roots_and_rejects_revoked_key(system):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi.testclient import TestClient
    from guardrails.evolution.evidence_trust import EvidenceTrust

    repo, service, tenant, identities, tokens, *_ = system
    key, key_id = Ed25519PrivateKey.generate(), "runner-" + uuid4().hex
    trust, platform = EvidenceTrust(repo), _platform_token(system)
    with TestClient(create_app(repository=repo), base_url="https://management.test") as client:
        missing = _signed_local_report(key, key_id)
        response = client.post(f"/v2/tenants/{tenant}/evidence", json=missing, headers=headers(tokens, "operator"))
        assert response.status_code == 403
        trust.provision(platform, key_id=key_id, public_key=key.public_key().public_bytes_raw())
        report = _signed_local_report(key, key_id)
        accepted = client.post(f"/v2/tenants/{tenant}/evidence", json=report, headers=headers(tokens, "operator"))
        assert accepted.status_code == 201, accepted.text
        assert accepted.json() == {"run_id": report["run_id"], "status": "pass"}
        with repo.transaction(tenant) as tx:
            assert tx.get("evidence", report["run_id"], required=True)["source_digest"] == "b" * 64
            assert tx.get("evidence", missing["run_id"]) is None
        revoked = trust.revoke(platform, key_id=key_id, expected_revision=1)
        assert revoked["status"] == "revoked" and revoked["revision"] == 2
        rejected_report = _signed_local_report(key, key_id)
        denied = client.post(f"/v2/tenants/{tenant}/evidence", json=rejected_report, headers=headers(tokens, "operator"))
        assert denied.status_code == 403
        with repo.transaction(tenant) as tx:
            assert tx.get("evidence", rejected_report["run_id"]) is None
        with pytest.raises(EvolutionError) as immutable_id:
            trust.provision(platform, key_id=key_id, public_key=Ed25519PrivateKey.generate().public_key().public_bytes_raw())
        assert immutable_id.value.code == "revision_conflict"


def test_release_validates_target_policy_residency_and_mode_atomically(system):
    repo, service, tenant, identities, tokens, payload, client = system
    candidate = submit(system)
    approve(system, candidate)
    runtime_id = str(uuid4())
    with repo.transaction(tenant) as tx:
        install = models.RuntimeInstall(runtime_id=runtime_id, tenant_id=tenant, zone="forbidden-zone", mode="remote", public_key_id="test", version="1", telemetry="none")
        tx.insert("runtimes", runtime_id, install)
        tx.insert("runtime_targets", "chat", {"runtime_ids": [runtime_id]})
    command = {"policy": reference(candidate), "workload_ids": ["chat"], "stage": "canary", "state": "observe", "expected_revisions": {"chat": 0}, "reason": "Residency"}
    denied = client.post(f"/v2/tenants/{tenant}/releases", json=command, headers=headers(tokens, "operator"))
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "residency_denied"
    with repo.transaction(tenant) as tx:
        old = tx.get("runtimes", runtime_id)
        tx.put("runtimes", runtime_id, {**old, "zone": "local-shared", "mode": "gateway"}, expected_revision=old["revision"])
    incompatible = client.post(f"/v2/tenants/{tenant}/releases", json=command, headers=headers(tokens, "operator"))
    assert incompatible.status_code == 409 and incompatible.json()["error"]["code"] == "capability_missing"
    with repo.transaction(tenant) as tx:
        assert tx.list("bindings") == []
        assert tx.list("releases") == []
        assert tx.list("outbox") == []


def test_release_detail_pages_all_receipts_and_excludes_unrelated_history(system):
    repo, service, tenant, identities, tokens, payload, client = system
    candidate = submit(system)
    approve(system, candidate)
    with repo.transaction(tenant) as tx:
        release = service.bindings.release(tx, identities["operator"], policy=reference(candidate), workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": 0}, reason="Receipt pagination")
        for index in range(1001):
            tx.insert("receipts", f"a-unrelated-{index:04}", {"workload_id": "chat", "binding_revision": 2, "runtime_id": str(uuid4()), "snapshot_generation": 1}, immutable=True)
        for index in range(101):
            tx.insert("receipts", f"z-current-{index:04}", {"workload_id": "chat", "binding_revision": 1, "runtime_id": str(uuid4()), "snapshot_generation": index + 1}, immutable=True)
    response = client.get(f"/v2/tenants/{tenant}/releases/{release['release_id']}", headers=headers(tokens, "viewer"))
    assert response.status_code == 200
    receipts = response.json()["receipts"]
    assert len(receipts) == 101
    assert {item["snapshot_generation"] for item in receipts} == set(range(1, 102))
    assert {item["binding_revision"] for item in receipts} == {1}


def test_multi_workload_release_rechecks_earlier_workload_lease_before_applied(system):
    repo, service, tenant, identities, tokens, payload, client = system
    candidate = submit(system)
    approve(system, candidate)
    runtimes = {}
    with repo.transaction(tenant) as tx:
        tx.insert("workloads", "second", {**tx.get("workloads", "chat"), "workload_id": "second"})
        release = service.bindings.release(tx, identities["operator"], policy=reference(candidate), workload_ids=["chat", "second"], stage="canary", state="observe", expected_revisions={"chat": 0, "second": 0}, reason="Whole release freshness")
        for workload_id in ("chat", "second"):
            runtime_id = str(uuid4())
            actor = service.identity.provision_principal(tx, "Runtime", ["runtime"])
            secret = service.identity.issue_credential(tx, actor["principal_id"])["one_time_secret"]
            tx.insert("runtimes", runtime_id, {"runtime_id": runtime_id, "status": "registered", "zone": "local-shared", "mode": "remote"})
            tx.insert("runtime_principals", actor["principal_id"], {"runtime_id": runtime_id, "principal_id": actor["principal_id"], "tenant_id": tenant}, immutable=True)
            tx.insert("runtime_enrollments", runtime_id, {"runtime_id": runtime_id, "principal_id": actor["principal_id"], "tenant_id": tenant}, immutable=True)
            tx.insert("runtime_targets", workload_id, {"runtime_ids": [runtime_id]})
            tx.insert("issued_snapshots", f"{runtime_id}:1", {"bindings": [tx.get("bindings", f"{workload_id}:1")], "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")})
            tx.insert("snapshot_pointers", runtime_id, {"generation": 1})
            runtimes[workload_id] = runtime_id, secret
    first_id, first_secret = runtimes["chat"]
    with repo.transaction(tenant) as tx:
        service.bindings.receipt(tx, service.identity.authenticate(first_secret), _receipt(tenant, first_id, candidate, 1))
        assert tx.get("applied_bindings", "chat")["binding_revision"] == 1
        old = tx.get("issued_snapshots", f"{first_id}:1")
        tx.put("issued_snapshots", f"{first_id}:1", {**old, "expires_at": "2020-01-01T00:00:00Z"}, expected_revision=old["revision"])
    second_id, second_secret = runtimes["second"]
    with repo.transaction(tenant) as tx:
        service.bindings.receipt(tx, service.identity.authenticate(second_secret), _receipt(tenant, second_id, candidate, 1, workload_id="second"))
        assert tx.get("applied_bindings", "second")["binding_revision"] == 1
        assert tx.get("releases", release["release_id"])["status"] == "pending"
    with repo.transaction(tenant) as tx:
        pointer = tx.get("snapshot_pointers", first_id)
        tx.put("snapshot_pointers", first_id, {**pointer, "generation": 2}, expected_revision=pointer["revision"])
        tx.insert("issued_snapshots", f"{first_id}:2", {"bindings": [tx.get("bindings", "chat:1")], "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")})
        service.bindings.receipt(tx, service.identity.authenticate(first_secret), _receipt(tenant, first_id, candidate, 1, snapshot_generation=2))
        assert tx.get("releases", release["release_id"])["status"] == "applied"
        assert tx.get("applied_bindings", "chat")["revision"] == 1
