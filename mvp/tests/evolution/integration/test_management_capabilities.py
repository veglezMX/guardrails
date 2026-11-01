"""Default control-plane capability admission against real TLS and PostgreSQL.

Installed declarations authorize a pending rollout, not an applied receipt or
live evaluator health. No worker pool is started by these management services.
"""

import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from guardrails.evolution import models
from guardrails.evolution.bindings import BindingService
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.management_api import create_app
from guardrails.evolution.plugins import PINNED_ARTIFACTS
from guardrails.evolution.snapshots import SnapshotDistributor
from guardrails.evolution.storage import TenantRepository
from https_support import serve_https
from test_evaluation_runtime import fixture_system, ref
from test_evaluation_runtime import http as runtime_http


@pytest.fixture
def system(tmp_path):
    value = fixture_system(tmp_path)
    try:
        yield value
    finally:
        value["service"].audit.close()
        value["repo"].close()


def persisted(system):
    """Compare all tenant records except the intentional HTTP attempt counter."""
    with system["repo"].transaction(system["tenant"]) as tx:
        records = [dict(row._mapping) for row in tx.execute(
            "SELECT * FROM evolution.records WHERE tenant_id=:tenant "
            "AND kind <> 'management_rate' ORDER BY kind,key",
            {"tenant": system["tenant"]},
        )]
        heads = [dict(row._mapping) for row in tx.execute(
            "SELECT * FROM evolution.audit_heads WHERE tenant_id=:tenant",
            {"tenant": system["tenant"]},
        )]
    return records, heads


def approved(system, feature="supported"):
    payload = deepcopy(system["payload"])
    with system["repo"].transaction(system["tenant"]) as tx:
        if feature == "ipv4":
            payload["rules"][0]["config"]["entities"] = ["ipv4"]
        elif feature == "content-list":
            content = models.with_resource_digest(models.ListSet(
                tenant_id=system["tenant"], list_id=str(uuid4()), kind="content",
                deny=("term:synthetic-forbidden",), allow_enabled=False,
            ))
            tx.insert("config:lists", content.list_id + ":1", content)
            old = tx.get("config:residency", payload["residency_ref"]["id"] + ":1", required=True)
            residency = models.with_resource_digest(models.ResidencyProfile.model_validate({
                **old, "version": 2, "content_lists": [content.list_id],
            }))
            tx.insert("config:residency", residency.residency_id + ":2", residency)
            payload["residency_ref"] = ref(residency, "residency_id")
            payload["list_refs"].append(ref(content, "list_id"))
        service = system["service"]
        candidate = service.governance.submit(tx, system["actors"]["author"], feature, payload)
        approval = service.governance.review(
            tx, system["actors"]["approver"], feature, 1, outcome="approve",
            compiled_digest=candidate["compiled_digest"], reason="Review exact immutable policy",
        )
        reference = {"tenant_id": system["tenant"], "policy_id": feature,
                     "version": 1, "digest": candidate["compiled_digest"]}
        assert approval["outcome"] == "approve"
        assert service.governance.eligible(tx, reference)["compiled_digest"] == candidate["compiled_digest"]
    return reference


def release_body(reference):
    return {"policy": reference, "workload_ids": ["chat"], "stage": "canary",
            "state": "observe", "expected_revisions": {"chat": 1},
            "reason": "Check installed support before desired state changes"}


def management_http(server, token):
    client = runtime_http(server, token)
    # Management has a five-second server deadline; the runtime fixture's
    # two-second transport timeout must not preempt that separate contract.
    client.timeout = 6.0
    return client


@pytest.mark.parametrize("feature", ["ipv4", "content-list"])
def test_default_https_release_rejects_approved_unsupported_policy_without_writes(system, tmp_path, feature):
    reference = approved(system, feature)
    before = persisted(system)
    with serve_https(lambda: create_app(TenantRepository(system["dsn"])), tmp_path / "https") as server, management_http(server, system["tokens"]["operator"]) as client:
        response = client.post(f"/v2/tenants/{system['tenant']}/releases",
                               json=release_body(reference),
                               headers={"Idempotency-Key": str(uuid4())})
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "capability_missing"
        assert response.headers["cache-control"] == "no-store"
    assert persisted(system) == before
    with system["repo"].transaction(system["tenant"]) as tx:
        assert tx.get("binding_pointers", "chat")["binding_revision"] == 1
        assert tx.get("applied_bindings", "chat")["binding_revision"] == 1
        assert len(tx.list("bindings")) == len(tx.list("releases")) == 1


def test_default_https_catalog_and_supported_release_are_static_and_pending(system, tmp_path):
    reference = approved(system)
    # The same default constructor is used by create_app; no injected registry
    # or capability callback and no start() call are needed for declarations.
    app = create_app(TenantRepository(system["dsn"]))
    service = app.state.service
    try:
        assert service.registry.health() == {"ready": False, "workers": 0, "admitted": 0}
        with serve_https(lambda: app, tmp_path / "https") as server, management_http(server, system["tokens"]["operator"]) as client:
            catalog = client.get(f"/v2/tenants/{system['tenant']}/capabilities")
            assert catalog.status_code == 200, catalog.text
            items = catalog.json()["items"]
            assert len(items) == 2
            assert {item["stage"] for item in items} == {"input", "output"}
            assert all(item["status"] == "enabled" for item in items)
            assert all(item["capability_id"] == "pii-email-phone" for item in items)
            assert all(item["evaluator_revision"] == "pii-v2" for item in items)
            assert all(item["zone"] == "local-shared" for item in items)
            assert all(item["part_kinds"] == ["text"] for item in items)
            assert all(set(item["actions"]) == {"allow", "warn", "redact", "block"} for item in items)
            assert all(item["max_input_bytes"] == 65536 and item["timeout_ms"] == 400 for item in items)
            unauthorized = client.get(f"/v2/tenants/{system['tenant']}/capabilities",
                                      headers={"Authorization": "Bearer invalid"})
            assert unauthorized.status_code == 401
            wrong_scope = client.get("/v2/tenants/unowned-tenant/capabilities")
            assert wrong_scope.status_code == 404
            wrong_role = client.get(f"/v2/tenants/{system['tenant']}/capabilities",
                                    headers={"Authorization": "Bearer " + system["tokens"]["workload"]})
            assert wrong_role.status_code == 403
            response = client.post(f"/v2/tenants/{system['tenant']}/releases",
                                   json=release_body(reference),
                                   headers={"Idempotency-Key": str(uuid4())})
            assert response.status_code == 202, response.text
            assert response.json()["status"] == "pending"
            release_id = response.json()["release_id"]
            status = client.get(f"/v2/tenants/{system['tenant']}/releases/{release_id}")
            assert status.status_code == 200
            assert status.json()["release"]["status"] == "pending"
            assert status.json()["receipts"] == []
            # The Linux service process handling the real HTTPS requests
            # has no evaluator children, not merely an idle parent copy.
            pid = server["process"].pid
            children = Path(f"/proc/{pid}/task/{pid}/children")
            assert children.read_text().strip() == ""
        with system["repo"].transaction(system["tenant"]) as tx:
            assert tx.get("binding_pointers", "chat")["binding_revision"] == 2
            assert tx.get("applied_bindings", "chat")["binding_revision"] == 1
            binding = tx.get("bindings", "chat:2", required=True)
            assert binding["policy"] == reference and binding["status"] == "pending"
            assert all(item["binding_revision"] == 1 for item in tx.list("receipts"))
            events = [item for item in tx.list("audit")
                      if item.get("policy_digest") == reference["digest"]
                      and item["event_type"].startswith("release_")]
            assert [item["event_type"] for item in events] == ["release_pending"]
            raw = tx.get("policies", "supported:1", required=True)
        # Catalog status is installed support, and cannot be used as live health.
        assert service.registry.health()["workers"] == 0
        assert {item.status for item in service.registry.capabilities()} == {"unavailable"}
        with pytest.raises(EvolutionError) as stopped:
            service.registry.require(models.PolicyVersion.model_validate(raw))
        assert stopped.value.code == "evaluator_unavailable"
    finally:
        service.audit.close()
        service.repository.close()


def test_default_https_unknown_evaluator_revision_cannot_be_submitted(system, tmp_path):
    payload = deepcopy(system["payload"])
    payload["rules"][0]["evaluator_revision"] = "pii-uninstalled"
    before = persisted(system)
    with serve_https(lambda: create_app(TenantRepository(system["dsn"])), tmp_path / "https") as server, management_http(server, system["tokens"]["author"]) as client:
        response = client.post(f"/v2/tenants/{system['tenant']}/policies/unknown-revision/versions",
                               json=payload, headers={"Idempotency-Key": str(uuid4())})
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "capability_missing"
    assert persisted(system) == before


@pytest.mark.parametrize("feature", ["ipv4", "content-list"])
def test_default_distributor_rejects_unsupported_desired_policy_and_retains_signed_pointer(system, feature):
    reference = approved(system, feature)
    service = system["service"]
    # Simulate a desired record persisted before the default management hook
    # existed, using the genuine transactional binding service and approvals.
    historical_writer = BindingService(system["repo"], service.governance, service.audit)
    with system["repo"].transaction(system["tenant"]) as tx:
        historical_writer.release(tx, system["actors"]["operator"], **release_body(reference))
    before = persisted(system)
    cache_bytes = [store.path.read_bytes() for store in system["stores"]]
    distributor = SnapshotDistributor(system["repo"], service.governance, service.bindings,
                                      system["key"], "test-key")
    for index, actor in enumerate(("runtime-a", "runtime-b")):
        with pytest.raises(EvolutionError) as missing, system["repo"].transaction(system["tenant"]) as tx:
            distributor.issue(tx, system["actors"][actor])
        assert missing.value.code == "capability_missing"
        with system["repo"].transaction(system["tenant"]) as tx:
            pointer = tx.get("snapshot_pointers", system["runtime_ids"][index], required=True)
            old = tx.get("issued_snapshots", f"{system['runtime_ids'][index]}:{pointer['generation']}", required=True)
            assert pointer["generation"] == 1
            assert old["bindings"][0]["policy"] == system["policy_ref"]
            assert old["digest"] == system["stores"][index].snapshot.digest
        assert system["stores"][index].path.read_bytes() == cache_bytes[index]
        assert system["stores"][index].ready()
    assert persisted(system) == before


_TAMPER_ADMISSION = """
import json, sys
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.management_api import ManagementApi
from guardrails.evolution.storage import TenantRepository
data = json.load(sys.stdin)
repo = TenantRepository(data['dsn'])
service = ManagementApi(repo)
try:
    actor = service.identity.authenticate(data['token'])
    code = None
    try:
        with repo.transaction(data['tenant']) as tx:
            service.bindings.release(tx, actor, **data['release'])
    except EvolutionError as error:
        code = error.code
    print(json.dumps({'error': code, 'health': service.registry.health(),
                     'declared': [item.status for item in service.registry.declared_capabilities()]}))
finally:
    service.audit.close()
    repo.close()
"""


@pytest.mark.parametrize("artifact", sorted(PINNED_ARTIFACTS))
def test_tampered_installed_artifact_denies_default_admission_without_workers_or_writes(system, tmp_path, artifact):
    reference = approved(system)
    before = persisted(system)
    # Import in a fresh process from real altered package bytes. Never mutate
    # the shared checkout or replace the hash verifier/capability callbacks.
    package = Path(models.__file__).resolve().parents[1]
    copied = tmp_path / "isolated" / "guardrails"
    shutil.copytree(package, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    target = copied / artifact
    target.write_bytes(target.read_bytes() + b"\n# Deliberate installed artifact byte change.\n")
    environment = {**os.environ, "PYTHONPATH": str(copied.parent), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", _TAMPER_ADMISSION],
                            input=json.dumps({"dsn": system["dsn"], "tenant": system["tenant"],
                                              "token": system["tokens"]["operator"],
                                              "release": release_body(reference)}),
                            cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["error"] == "evaluator_unavailable"
    assert report["health"] == {"ready": False, "workers": 0, "admitted": 0}
    assert report["declared"] == ["unavailable", "unavailable"]
    assert persisted(system) == before
