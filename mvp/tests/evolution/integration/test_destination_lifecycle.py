"""Exact endpoint approvals exercised through TLS management and signed sync."""

from copy import deepcopy
from uuid import uuid4

from guardrails.evolution.compiler import canonical_bytes
from guardrails.evolution.management_api import ManagementApi, create_app
from guardrails.evolution.models import PolicyVersion
from guardrails.evolution.snapshots import SnapshotDistributor
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https
from test_evaluation_runtime import fixture_system, http


def reference(value, identity):
    return {"id": value[identity], "version": value["version"], "digest": value["digest"]}


def app(system):
    repository = TenantRepository(system["dsn"])
    service = ManagementApi(repository)
    distributor = SnapshotDistributor(repository, service.governance, service.bindings, system["key"], "test-key")
    return create_app(service=service, distributor=distributor)


def test_https_exact_destination_version_approval_release_and_ancestor_revocation(tmp_path):
    system = fixture_system(tmp_path, baseline=False)
    prefix = "/v2/tenants/" + system["tenant"]
    try:
        with serve_https(lambda: app(system), tmp_path / "management-tls") as server:
            clients = {role: http(server, token) for role, token in system["tokens"].items()}
            for client in clients.values():
                client.timeout = 10
            try:
                def post(path, body, role, expected):
                    result = clients[role].post(prefix + path, json=body, headers={"Idempotency-Key": str(uuid4())})
                    assert result.status_code == expected, result.text
                    return result

                endpoint = {"name": "model-endpoint", "host": "model-old.example.test", "path_prefix": "/api", "cidrs": ["10.2.0.0/16"], "zone": "local-shared", "purpose": "inference", "tls_ca_ref": "model-ca"}
                first = post("/destinations", endpoint, "tenant_admin", 201)
                destination = first.json()
                assert clients["author"].get(first.headers["Location"]).json() == destination
                provider = post("/providers/" + system["provider"].provider_id + "/versions", {"kind": "model", "vendor": "ollama", "model_id": "pinned-model", "destination_id": destination["destination_id"], "zone": "local-shared"}, "tenant_admin", 201).json()
                allowed = post("/lists", {"kind": "destination", "allow": [destination["destination_id"]], "allow_enabled": True, "deny": []}, "author", 201).json()
                old_residency = system["policy"]["residency"]
                residency_body = {key: value for key, value in old_residency.items() if key not in {"residency_id", "tenant_id", "version", "digest", "created_at", "revision"}}
                residency_body["destination_lists"] = [allowed["list_id"]]
                residency = post("/residency/" + old_residency["residency_id"] + "/versions", residency_body, "tenant_admin", 201).json()
                payload = {**deepcopy(system["payload"]), "provider_refs": [reference(provider, "provider_id")], "residency_ref": reference(residency, "residency_id"), "destination_refs": [reference(destination, "destination_id")]}
                payload["list_refs"].append(reference(allowed, "list_id"))
                policy = post("/policies/network/versions", payload, "author", 201).json()
                assert policy["compiler_version"] == "compiler-v2.2"
                assert policy["resolved_destinations"] == [destination]
                saved = canonical_bytes(PolicyVersion.model_validate(policy))
                policy_ref = {"tenant_id": system["tenant"], "policy_id": "network", "version": 1, "digest": policy["compiled_digest"]}
                release_body = {"policy": policy_ref, "workload_ids": ["chat"], "stage": "canary", "state": "observe", "expected_revisions": {"chat": 1}, "reason": "Exact endpoint rollout"}
                denied = post("/releases", release_body, "operator", 409)
                assert denied.json()["error"]["code"] == "approval_required"
                post("/policies/network/versions/1/reviews", {"outcome": "approve", "compiled_digest": policy["compiled_digest"], "reason": "Reviewed exact endpoint bytes"}, "approver", 200)
                release = post("/releases", release_body, "operator", 202).json()
                for index, role in enumerate(("runtime-a", "runtime-b")):
                    snapshot = clients[role].get("/v2/runtime/snapshot")
                    assert snapshot.status_code == 200, snapshot.text
                    approved = next(item for item in snapshot.json()["policies"] if item["policy_id"] == "network")
                    assert approved["resolved_destinations"] == [destination]
                    for receipt in system["stores"][index].apply(snapshot.json()):
                        accepted = clients[role].post("/v2/runtime/receipts", json=receipt, headers={"Idempotency-Key": str(uuid4())})
                        assert accepted.status_code == 200, accepted.text
                applied = clients["operator"].get(prefix + "/releases/" + release["release_id"])
                assert applied.status_code == 200
                assert applied.json()["release"]["status"] == "applied"
                assert {item["runtime_id"] for item in applied.json()["receipts"]} == set(system["runtime_ids"])

                second = post("/destinations/" + destination["destination_id"] + "/versions", {**endpoint, "host": "model-new.example.test"}, "tenant_admin", 201).json()
                assert second["version"] == 2 and second["digest"] != destination["digest"]
                assert clients["author"].get(first.headers["Location"]).json() == destination
                old_policy = clients["author"].get(prefix + "/policies/network/versions/1").json()["policy"]
                assert canonical_bytes(PolicyVersion.model_validate(old_policy)) == saved
                assert old_policy["resolved_destinations"] == [destination]
                with system["repo"].transaction(system["tenant"]) as tx:
                    assert tx.get("bindings", "chat:2")["policy"] == policy_ref
                    assert tx.get("binding_pointers", "chat")["binding_revision"] == 2
                newer = post("/policies/network/versions", {**payload, "destination_refs": [reference(second, "destination_id")]}, "author", 201).json()
                assert newer["version"] == 2 and newer["compiled_digest"] != policy["compiled_digest"]
                assert newer["resolved_destinations"] == [second]
                denied = post("/releases", {**release_body, "policy": {**policy_ref, "version": 2, "digest": newer["compiled_digest"]}, "expected_revisions": {"chat": 2}}, "operator", 409)
                assert denied.json()["error"]["code"] == "approval_required"

                child = post("/policies/child/versions", {"parents": [policy_ref], "rules": [], "residency_ref": payload["residency_ref"], "quota_ref": payload["quota_ref"]}, "author", 201).json()
                assert child["resolved_destinations"] == [destination]
                post("/policies/child/versions/1/reviews", {"outcome": "approve", "compiled_digest": child["compiled_digest"], "reason": "Review inherited exact endpoint"}, "approver", 200)
                post("/policies/network/versions/1/revoke", {"compiled_digest": policy["compiled_digest"], "reason": "Revoke ancestor"}, "approver", 200)
                denied = post("/releases", {**release_body, "policy": {**policy_ref, "policy_id": "child", "digest": child["compiled_digest"]}, "expected_revisions": {"chat": 2}}, "operator", 409)
                assert denied.json()["error"]["code"] == "policy_revoked"
                with system["repo"].transaction(system["tenant"]) as tx:
                    assert tx.get("binding_pointers", "chat")["binding_revision"] == 2
                    assert tx.get("releases", release["release_id"])["status"] == "applied"
            finally:
                for client in clients.values():
                    client.close()
    finally:
        system["service"].audit.close()
        system["repo"].close()


def test_https_missing_stale_and_foreign_destination_refs_do_not_create_policy(tmp_path):
    system = fixture_system(tmp_path, baseline=False)
    foreign = fixture_system(tmp_path / "foreign", baseline=False)
    endpoint = {"name": "model-endpoint", "host": "model.example.test", "path_prefix": "/api", "cidrs": ["10.2.0.0/16"], "zone": "local-shared", "purpose": "inference", "tls_ca_ref": "model-ca"}
    try:
        with serve_https(lambda: app(system), tmp_path / "management-tls") as server, http(server, system["tokens"]["tenant_admin"]) as admin, http(server, foreign["tokens"]["tenant_admin"]) as other, http(server, system["tokens"]["author"]) as author:
            for client in (admin, other, author):
                client.timeout = 10
            prefix = "/v2/tenants/" + system["tenant"]
            def post(client, path, body, expected):
                result = client.post(path, json=body, headers={"Idempotency-Key": str(uuid4())})
                assert result.status_code == expected, result.text
                return result.json()
            destination = post(admin, prefix + "/destinations", endpoint, 201)
            other_destination = post(other, "/v2/tenants/" + foreign["tenant"] + "/destinations", endpoint, 201)
            provider = post(admin, prefix + "/providers/" + system["provider"].provider_id + "/versions", {"kind": "model", "vendor": "ollama", "model_id": "pinned-model", "destination_id": destination["destination_id"], "zone": "local-shared"}, 201)
            payload = {**deepcopy(system["payload"]), "provider_refs": [reference(provider, "provider_id")]}
            for refs, expected, code in (([], 403, "destination_denied"), ([{**reference(destination, "destination_id"), "digest": "f" * 64}], 422, "composition_conflict"), ([{**reference(destination, "destination_id"), "version": 2}], 404, "resource_not_found"), ([reference(other_destination, "destination_id")], 404, "resource_not_found")):
                result = post(author, prefix + "/policies/denied/versions", {**payload, "destination_refs": refs}, expected)
                assert result["error"]["code"] == code
            with system["repo"].transaction(system["tenant"]) as tx:
                assert tx.get("policies", "denied:1") is None
                assert tx.get("binding_pointers", "chat")["binding_revision"] == 1
                assert len(tx.list("releases")) == 1
    finally:
        for item in (system, foreign):
            item["service"].audit.close()
            item["repo"].close()
