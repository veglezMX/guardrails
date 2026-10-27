"""Real HTTPS/PG tests prove receipt admission depends on applied signed bytes."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import multiprocessing
import os
import ssl
import subprocess
import sys
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from guardrails.evolution import models
from guardrails.evolution.enrollment import RuntimeEnrollment
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.evidence_trust import EvidenceTrust
from guardrails.evolution.management_api import (
    CANARY_CASE_IDS,
    ManagementApi,
    create_app,
)
from guardrails.evolution.runtime_apply import RuntimeSnapshotStore
from guardrails.evolution.snapshots import SnapshotDistributor, snapshot_digest, stamp
from guardrails.evolution.storage import TenantRepository
from guardrails.evolution.sync_api import mount_sync_routes
from https_support import serve_https


def protected(path, data):
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    path.chmod(0o600)
    return str(path)


@pytest.fixture
def sync_system(tmp_path):
    dsn = Path(os.environ["EVOLUTION_TEST_DSN_FILE"]).read_text().strip()
    repo = TenantRepository(dsn)
    service = ManagementApi(repo)
    tenant = "sync-" + uuid4().hex[:20]
    quota = models.with_resource_digest(
        models.Quota(quota_id=str(uuid4()), tenant_id=tenant, version=1)
    )
    residency = models.with_resource_digest(
        models.ResidencyProfile(
            residency_id=str(uuid4()),
            tenant_id=tenant,
            version=1,
            allowed_zones=("local-shared",),
        )
    )
    repo.provision_tenant(
        models.Tenant(
            tenant_id=tenant,
            display_name="Sync tenant",
            status="active",
            baseline=None,
            residency_id=residency.residency_id,
            quota_id=quota.quota_id,
        ),
        residency,
        quota,
    )
    actors, tokens, runtimes = {}, {}, []
    with repo.transaction(tenant) as tx:
        for role in ("author", "approver", "operator", "runtime-a", "runtime-b"):
            principal = service.identity.provision_principal(
                tx, role, ("runtime" if role.startswith("runtime") else role,)
            )
            tokens[role] = service.identity.issue_credential(
                tx, principal["principal_id"]
            )["one_time_secret"]
            if role.startswith("runtime"):
                runtime_id = str(uuid4())
                tx.insert(
                    "runtimes",
                    runtime_id,
                    models.RuntimeInstall(
                        runtime_id=runtime_id,
                        tenant_id=tenant,
                        zone="local-shared",
                        mode="remote",
                        public_key_id="test-key",
                        version="test-runtime-v1",
                        telemetry="metadata",
                    ),
                )
                runtimes.append(runtime_id)
        tx.insert(
            "workloads",
            "chat",
            models.Workload(
                tenant_id=tenant,
                workload_id="chat",
                display_name="Chat",
                mode="remote",
                status="active",
                allowed_stages=("input", "output"),
                provider_ids=(),
                quota_id=quota.quota_id,
                required_state="observe",
            ),
        )
    actors = {
        name: service.identity.authenticate(value) for name, value in tokens.items()
    }
    enrollment = RuntimeEnrollment(repo, service.identity, service.audit)
    for index, role in enumerate(("runtime-a", "runtime-b")):
        enrollment.enroll(
            tokens["operator"],
            runtime_id=runtimes[index],
            principal_id=actors[role].principal["principal_id"],
            workload_ids=["chat"],
            expected_runtime_revision=1,
            expected_target_revisions={"chat": index},
        )
    payload = {
        "rules": [
            {
                "rule_id": "email",
                "kind": "pii",
                "stages": ["input", "output"],
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
    with repo.transaction(tenant) as tx:
        policy = service.governance.submit(tx, actors["author"], "policy", payload)
    with repo.transaction(tenant) as tx:
        service.governance.review(
            tx,
            actors["approver"],
            "policy",
            1,
            outcome="approve",
            compiled_digest=policy["compiled_digest"],
            reason="Reviewed",
        )
    ref = {
        "tenant_id": tenant,
        "policy_id": "policy",
        "version": 1,
        "digest": policy["compiled_digest"],
    }
    with repo.transaction(tenant) as tx:
        release = service.bindings.release(
            tx,
            actors["operator"],
            policy=ref,
            workload_ids=["chat"],
            stage="canary",
            state="observe",
            expected_revisions={"chat": 0},
            reason="Canary",
        )
    key = Ed25519PrivateKey.generate()
    from evolution.run import signing_key

    evidence_key, evidence_key_id = signing_key(Path(__file__).resolve().parents[4])
    trust = EvidenceTrust(repo, identity=service.identity, audit=service.audit)
    with repo.transaction("_platform") as tx:
        bootstrap = service.identity.provision_principal(
            tx, "Evidence bootstrap", ["platform_admin"]
        )
        platform_token = service.identity.issue_credential(
            tx, bootstrap["principal_id"]
        )["one_time_secret"]
        current_trust = tx.get("evidence_trust_roots", evidence_key_id)
    if current_trust is None:
        trust.provision(
            platform_token,
            key_id=evidence_key_id,
            public_key=evidence_key.public_key().public_bytes_raw(),
        )
    else:
        assert (
            trust.resolve(evidence_key_id).public_bytes_raw()
            == evidence_key.public_key().public_bytes_raw()
        )
    public_file = protected(
        tmp_path / "snapshot.pub",
        key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )

    def factory():
        child_repo = TenantRepository(dsn)
        child = ManagementApi(child_repo)
        distributor = SnapshotDistributor(
            child_repo, child.governance, child.bindings, key, "test-key"
        )
        return mount_sync_routes(create_app(service=child), child, distributor)

    with serve_https(factory, tmp_path / "tls") as endpoint:
        configs = []
        for index, name in enumerate(("runtime-a", "runtime-b")):
            config = {
                "url": endpoint["url"],
                "ca_file": endpoint["ca_file"],
                "credential_file": protected(
                    tmp_path / (name + ".token"), tokens[name]
                ),
                "state_file": str(tmp_path / (name + ".state")),
                "tenant_id": tenant,
                "runtime_id": runtimes[index],
                "trust_key_files": {"test-key": public_file},
            }
            path = tmp_path / (name + ".json")
            protected(path, json.dumps(config))
            configs.append(path)
        yield {
            "repo": repo,
            "service": service,
            "tenant": tenant,
            "actors": actors,
            "tokens": tokens,
            "runtimes": runtimes,
            "release": release,
            "policy": policy,
            "key": key,
            "evidence_key": evidence_key,
            "endpoint": endpoint,
            "configs": configs,
            "tmp_path": tmp_path,
        }
    repo.close()


def run_runtime(path):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "guardrails.evolution.runtime_apply",
            "--config-file",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def run_canary(system):
    targets = protected(
        system["tmp_path"] / "targets.json", json.dumps({"chat": system["runtimes"]})
    )
    credential = protected(
        system["tmp_path"] / "operator.token", system["tokens"]["operator"]
    )
    args = [
        sys.executable,
        "-m",
        "evolution.canary",
        "--url",
        system["endpoint"]["url"],
        "--ca-file",
        system["endpoint"]["ca_file"],
        "--credential-file",
        credential,
        "--tenant",
        system["tenant"],
        "--release-id",
        system["release"]["release_id"],
        "--targets-file",
        targets,
        "--snapshot-key-file",
        str(system["tmp_path"] / "snapshot.pub"),
        "--snapshot-key-id",
        "test-key",
        "--submit",
    ]
    for runtime_id, name in zip(
        system["runtimes"], ("runtime-a", "runtime-b"), strict=True
    ):
        args.extend(
            [
                "--runtime-cache",
                f"{runtime_id}={system['tmp_path'] / (name + '.state')}",
            ]
        )
    return subprocess.run(args, check=False, capture_output=True, text=True, timeout=30)


def signed_empty_snapshots():
    key = Ed25519PrivateKey.generate()
    runtime_id = str(uuid4())
    now = datetime.now(UTC)
    values = []
    for generation in (1, 2):
        value = {
            "schema_version": "guardrails-v2",
            "runtime_id": runtime_id,
            "tenant_id": "cache-tenant",
            "generation": generation,
            "issued_at": stamp(now),
            "expires_at": stamp(now + timedelta(seconds=60)),
            "bindings": [],
            "policies": [],
            "revoked_digests": [],
            "created_at": stamp(now),
            "key_id": "cache-test",
        }
        value["digest"] = snapshot_digest(value)
        value["signature"] = base64.b64encode(
            key.sign(models.canonical_json_bytes(value))
        ).decode()
        values.append(value)
    return runtime_id, {"cache-test": key.public_key()}, values


def test_runtime_cache_two_instances_cannot_regress_durable_generation(tmp_path):
    runtime_id, keys, (older, newer) = signed_empty_snapshots()
    path = tmp_path / "shared.state"
    first = RuntimeSnapshotStore(path, "cache-tenant", runtime_id, keys)
    stale = RuntimeSnapshotStore(path, "cache-tenant", runtime_id, keys)
    assert first.apply(newer) == ()
    committed = path.read_bytes()
    with pytest.raises(EvolutionError) as rejected:
        stale.apply(older)
    assert rejected.value.code == "revision_conflict"
    assert path.read_bytes() == committed
    assert stale.snapshot.generation == 2
    restarted = RuntimeSnapshotStore(path, "cache-tenant", runtime_id, keys)
    assert restarted.snapshot.generation == 2
    assert restarted.apply(newer) == ()
    assert path.stat().st_mode & 0o777 == 0o600


def test_runtime_cache_concurrent_processes_cannot_regress_generation(tmp_path):
    runtime_id, keys, (older, newer) = signed_empty_snapshots()
    path = tmp_path / "shared.state"
    context = multiprocessing.get_context("fork")
    loaded = context.Barrier(3)
    newer_committed = context.Event()
    results = context.Queue()

    def apply(value):
        store = RuntimeSnapshotStore(path, "cache-tenant", runtime_id, keys)
        loaded.wait(timeout=5)
        if value["generation"] == 1 and not newer_committed.wait(timeout=5):
            results.put((1, "coordination_failed"))
            return
        try:
            store.apply(value)
            results.put((value["generation"], "applied"))
        except EvolutionError as error:
            results.put((value["generation"], error.code))
        finally:
            if value["generation"] == 2:
                newer_committed.set()

    processes = [
        context.Process(target=apply, args=(value,)) for value in (older, newer)
    ]
    try:
        for process in processes:
            process.start()
        loaded.wait(timeout=5)
        observed = dict(results.get(timeout=8) for _ in processes)
        for process in processes:
            process.join(5)
            assert process.exitcode == 0
        assert observed == {1: "revision_conflict", 2: "applied"}
        assert json.loads(path.read_bytes())["snapshot"]["generation"] == 2
        restarted = RuntimeSnapshotStore(path, "cache-tenant", runtime_id, keys)
        assert restarted.snapshot.generation == 2
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
            process.join(5)
        results.close()


def test_runtime_cache_bootstrap_revalidates_trust_scope_and_permissions(tmp_path):
    runtime_id, keys, (_, newer) = signed_empty_snapshots()
    path = tmp_path / "shared.state"
    store = RuntimeSnapshotStore(path, "cache-tenant", runtime_id, keys)
    store.apply(newer)
    with pytest.raises(EvolutionError) as wrong_scope:
        RuntimeSnapshotStore(path, "foreign-tenant", runtime_id, keys)
    assert wrong_scope.value.code == "resource_not_found"
    with pytest.raises(EvolutionError) as wrong_key:
        RuntimeSnapshotStore(
            path,
            "cache-tenant",
            runtime_id,
            {"cache-test": Ed25519PrivateKey.generate().public_key()},
        )
    assert wrong_key.value.code == "validation_failed"
    path.chmod(0o644)
    with pytest.raises(EvolutionError) as permissions:
        RuntimeSnapshotStore(path, "cache-tenant", runtime_id, keys)
    assert permissions.value.code == "validation_failed"
    path.chmod(0o600)
    alias = tmp_path / "alias.state"
    alias.symlink_to(path)
    with pytest.raises(EvolutionError) as symbolic:
        RuntimeSnapshotStore(alias, "cache-tenant", runtime_id, keys)
    assert symbolic.value.code == "validation_failed"
    assert json.loads(path.read_bytes())["snapshot"]["generation"] == 2


def test_runtime_process_stops_slow_tls_response_without_cache_or_receipt(tmp_path):
    receipt_attempts = multiprocessing.get_context("fork").Value("i", 0)

    def application():
        app = FastAPI()

        @app.get("/health/live")
        async def health():
            return {"status": "live"}

        @app.get("/v2/runtime/snapshot")
        async def slow():
            async def chunks():
                for _ in range(1000):
                    yield b" "
                    await asyncio.sleep(0.04)

            return StreamingResponse(chunks(), media_type="application/json")

        @app.post("/v2/runtime/receipts")
        async def receipt():
            with receipt_attempts.get_lock():
                receipt_attempts.value += 1
            return {}

        return app

    with serve_https(application, tmp_path / "tls") as endpoint:
        state = tmp_path / "runtime.state"
        config = {
            "url": endpoint["url"],
            "ca_file": endpoint["ca_file"],
            "credential_file": protected(
                tmp_path / "runtime.token", "synthetic-test-token"
            ),
            "state_file": str(state),
            "tenant_id": "slow-test",
            "runtime_id": str(uuid4()),
            "trust_key_files": {
                "test-key": protected(
                    tmp_path / "snapshot.pub",
                    Ed25519PrivateKey.generate().public_key().public_bytes_raw(),
                )
            },
        }
        path = Path(protected(tmp_path / "runtime.json", json.dumps(config)))
        started = time.monotonic()
        result = run_runtime(path)
        elapsed = time.monotonic() - started
        assert result.returncode == 1
        assert 2.5 < elapsed < 5
        assert not state.exists()
        assert receipt_attempts.value == 0


def test_real_https_runtime_loads_policy_before_receipt_and_requires_all_targets(
    sync_system,
):
    system = sync_system
    repo, tenant = system["repo"], system["tenant"]
    first = run_runtime(system["configs"][0])
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["policy_digests"] == [
        system["policy"]["compiled_digest"]
    ]
    stored = json.loads((system["tmp_path"] / "runtime-a.state").read_bytes())
    assert stored["snapshot"]["policies"][0]["rules"][0]["config"] == {
        "entities": ["email"]
    }
    assert (
        stored["receipts"][0]["digest"]
        == stored["snapshot"]["policies"][0]["compiled_digest"]
    )
    with repo.transaction(tenant) as tx:
        assert len(tx.list("receipts")) == 1
        assert tx.get("applied_bindings", "chat") is None
        assert (
            tx.get("releases", system["release"]["release_id"])["status"] == "pending"
        )
    incomplete = run_canary(system)
    assert incomplete.returncode == 6
    assert json.loads(incomplete.stdout)["status"] == "fail"
    with repo.transaction(tenant) as tx:
        assert tx.list("evidence") == []
        assert tx.list("canary_evidence") == []
    second = run_runtime(system["configs"][1])
    assert second.returncode == 0, second.stderr
    with repo.transaction(tenant) as tx:
        assert len(tx.list("receipts")) == 2
        assert tx.get("applied_bindings", "chat")["binding_revision"] == 1
        assert (
            tx.get("releases", system["release"]["release_id"])["status"] == "applied"
        )
    replay = run_runtime(system["configs"][0])
    assert replay.returncode == 0
    with repo.transaction(tenant) as tx:
        assert len(tx.list("receipts")) == 2

    from evolution.run import verify_report

    observed = run_canary(system)
    assert observed.returncode == 0, observed.stdout
    result = json.loads(observed.stdout)
    assert result["status"] == "pass" and result["submitted"] is True
    report = json.loads(Path(result["report"]).read_bytes())
    public = (
        system["evidence_key"]
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    verify_report(report, public)
    assert {case["case_id"] for case in report["cases"]} == CANARY_CASE_IDS
    assert all(
        case["assertions"] > 0 and case["status"] == "pass" for case in report["cases"]
    )
    artifact = next(
        value
        for value in report["artifact_digests"]
        if value["name"] == "canary-runtime-observations"
    )
    observation_path = Path(result["report"]).parent / "observations.json"
    assert (
        artifact["sha256"] == hashlib.sha256(observation_path.read_bytes()).hexdigest()
    )
    observations = json.loads(observation_path.read_bytes())
    assert observations["targets"] == {"chat": system["runtimes"]}
    with repo.transaction(tenant) as tx:
        assert {
            entry["receipt"]["runtime_id"]: entry["receipt"]
            for entry in observations["observations"]
        } == {receipt["runtime_id"]: receipt for receipt in tx.list("receipts")}
        assert tx.get("evidence", report["run_id"])["signature"] == report["signature"]
    context = ssl.create_default_context(cafile=system["endpoint"]["ca_file"])
    with httpx.Client(
        base_url=system["endpoint"]["url"], verify=context, trust_env=False
    ) as client:
        headers = {
            "Authorization": "Bearer " + system["tokens"]["operator"],
            "Idempotency-Key": str(uuid4()),
        }
        promoted = client.post(
            f"/v2/tenants/{tenant}/releases",
            json={
                "policy": system["release"]["policy"],
                "workload_ids": ["chat"],
                "stage": "full",
                "state": "observe",
                "expected_revisions": {"chat": 1},
                "reason": "Measured signed canary passed",
                "canary_evidence_id": report["run_id"],
            },
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert promoted.status_code == 202, promoted.text
        full = promoted.json()
        assert full["status"] == "pending"
    assert run_runtime(system["configs"][0]).returncode == 0
    with repo.transaction(tenant) as tx:
        assert tx.get("applied_bindings", "chat")["binding_revision"] == 1
        assert tx.get("releases", full["release_id"])["status"] == "pending"
    assert run_runtime(system["configs"][1]).returncode == 0
    with repo.transaction(tenant) as tx:
        assert tx.get("applied_bindings", "chat")["binding_revision"] == 2
        assert tx.get("releases", full["release_id"])["status"] == "applied"
        assert len(tx.list("receipts")) == 4


def test_runtime_snapshot_rejects_zone_drift_before_any_policy_or_receipt(sync_system):
    system = sync_system
    with system["repo"].transaction(system["tenant"]) as tx:
        runtime_id = system["runtimes"][0]
        install = tx.get("runtimes", runtime_id)
        tx.put(
            "runtimes",
            runtime_id,
            {**install, "zone": "forbidden-zone"},
            expected_revision=install["revision"],
        )
    result = run_runtime(system["configs"][0])
    assert result.returncode != 0
    assert not (system["tmp_path"] / "runtime-a.state").exists()
    with system["repo"].transaction(system["tenant"]) as tx:
        assert tx.list("issued_snapshots") == []
        assert tx.list("snapshot_pointers") == []
        assert tx.list("receipts") == []
        assert tx.get("applied_bindings", "chat") is None
        assert (
            tx.get("releases", system["release"]["release_id"])["status"] == "pending"
        )


def test_runtime_verification_rejects_tampering_scope_replay_and_expiry(sync_system):
    system = sync_system
    assert run_runtime(system["configs"][0]).returncode == 0
    path = system["tmp_path"] / "runtime-a.state"
    original = path.read_bytes()
    data = json.loads(original)["snapshot"]
    store = RuntimeSnapshotStore(
        path,
        system["tenant"],
        system["runtimes"][0],
        {"test-key": system["key"].public_key()},
    )
    assert (
        store.policy_for("chat").compiled_digest == system["policy"]["compiled_digest"]
    )
    tampered = {**data, "generation": data["generation"] + 1}
    with pytest.raises(EvolutionError):
        store.apply(tampered)
    forged_policy = deepcopy(data)
    forged_policy["policies"][0]["rules"][0]["config"]["entities"] = ["ipv4"]
    forged_policy["digest"] = snapshot_digest(forged_policy)
    forged_policy["signature"] = base64.b64encode(
        system["key"].sign(
            models.canonical_json_bytes(
                {k: v for k, v in forged_policy.items() if k != "signature"}
            )
        )
    ).decode()
    with pytest.raises(EvolutionError) as changed_bytes:
        store.apply(forged_policy)
    assert changed_bytes.value.code == "approval_required"
    assert path.read_bytes() == original
    foreign = RuntimeSnapshotStore(
        system["tmp_path"] / "foreign.state",
        "another-tenant",
        system["runtimes"][0],
        {"test-key": system["key"].public_key()},
    )
    with pytest.raises(EvolutionError) as rejected:
        foreign.apply(data)
    assert rejected.value.code == "resource_not_found"
    future = {**data, "generation": 2}
    future["digest"] = snapshot_digest(future)
    future["signature"] = base64.b64encode(
        system["key"].sign(
            models.canonical_json_bytes(
                {k: v for k, v in future.items() if k != "signature"}
            )
        )
    ).decode()
    store.apply(future)
    with pytest.raises(EvolutionError) as replay:
        store.apply(data)
    assert replay.value.code == "revision_conflict"
    store.clock = lambda: models.parse_time(future["expires_at"]) + timedelta(seconds=1)
    assert store.ready() is False
    with pytest.raises(EvolutionError) as stale:
        store.policy_for("chat")
    assert stale.value.code == "snapshot_stale"
    assert not (system["tmp_path"] / "foreign.state").exists()


def test_real_sync_rejects_wrong_runtime_receipt_and_revoked_policy(sync_system):
    system = sync_system
    assert run_runtime(system["configs"][0]).returncode == 0
    stored = json.loads((system["tmp_path"] / "runtime-a.state").read_bytes())
    context = ssl.create_default_context(cafile=system["endpoint"]["ca_file"])
    with httpx.Client(
        base_url=system["endpoint"]["url"], verify=context, trust_env=False
    ) as client:
        headers = {
            "Authorization": "Bearer " + system["tokens"]["runtime-b"],
            "Idempotency-Key": str(uuid4()),
        }
        rejected = client.post(
            "/v2/runtime/receipts", json=stored["receipts"][0], headers=headers
        )
        assert rejected.status_code == 404
        no_auth = client.get("/v2/runtime/snapshot")
        assert no_auth.status_code == 401
    with system["repo"].transaction(system["tenant"]) as tx:
        assert len(tx.list("receipts")) == 1
        system["service"].governance.review(
            tx,
            system["actors"]["approver"],
            "policy",
            1,
            outcome="revoke",
            compiled_digest=system["policy"]["compiled_digest"],
            reason="Revoked",
        )
    refreshed = run_runtime(system["configs"][0])
    assert refreshed.returncode == 0
    assert json.loads(refreshed.stdout)["policy_digests"] == []
    cache = json.loads((system["tmp_path"] / "runtime-a.state").read_bytes())
    assert system["policy"]["compiled_digest"] in cache["snapshot"]["revoked_digests"]
    with system["repo"].transaction(system["tenant"]) as tx:
        assert tx.get("applied_bindings", "chat") is None
        assert (
            tx.get("releases", system["release"]["release_id"])["status"] == "pending"
        )


def test_snapshot_lease_304_never_renews_and_outage_expires_loaded_state(sync_system):
    system = sync_system
    assert run_runtime(system["configs"][0]).returncode == 0
    stored = json.loads((system["tmp_path"] / "runtime-a.state").read_bytes())[
        "snapshot"
    ]
    context = ssl.create_default_context(cafile=system["endpoint"]["ca_file"])
    with httpx.Client(
        base_url=system["endpoint"]["url"], verify=context, trust_env=False
    ) as client:
        response = client.get(
            "/v2/runtime/snapshot",
            headers={
                "Authorization": "Bearer " + system["tokens"]["runtime-a"],
                "If-None-Match": '"' + stored["digest"] + '"',
            },
        )
        assert response.status_code == 304
        assert not response.content
    before = (system["tmp_path"] / "runtime-a.state").read_bytes()
    system["endpoint"]["process"].terminate()
    system["endpoint"]["process"].join(5)
    failure = run_runtime(system["configs"][0])
    assert failure.returncode != 0
    assert (system["tmp_path"] / "runtime-a.state").read_bytes() == before
    clock = lambda: models.parse_time(stored["expires_at"]) + timedelta(seconds=1)
    store = RuntimeSnapshotStore(
        system["tmp_path"] / "runtime-a.state",
        system["tenant"],
        system["runtimes"][0],
        {"test-key": system["key"].public_key()},
        clock=clock,
    )
    assert store.ready() is False
    with pytest.raises(EvolutionError):
        store.policy_for("chat")


def test_stale_and_mismatched_receipts_never_mark_binding_applied(sync_system):
    system = sync_system
    context = ssl.create_default_context(cafile=system["endpoint"]["ca_file"])
    with httpx.Client(
        base_url=system["endpoint"]["url"], verify=context, trust_env=False
    ) as client:
        headers = {"Authorization": "Bearer " + system["tokens"]["runtime-a"]}
        response = client.get("/v2/runtime/snapshot", headers=headers)
        assert response.status_code == 200
        snapshot = response.json()
        store = RuntimeSnapshotStore(
            system["tmp_path"] / "negative.state",
            system["tenant"],
            system["runtimes"][0],
            {"test-key": system["key"].public_key()},
        )
        receipt = store.apply(snapshot)[0]
        bad = client.post(
            "/v2/runtime/receipts",
            json={**receipt, "digest": "0" * 64},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert bad.status_code == 409
        missing_precondition = client.post(
            "/v2/runtime/receipts", json=receipt, headers=headers
        )
        assert missing_precondition.status_code == 428
        wrong_scope = client.post(
            "/v2/runtime/receipts",
            json={**receipt, "tenant_id": "foreign-tenant"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        )
        assert wrong_scope.status_code == 404
    expired_clock = lambda: (
        models.parse_time(snapshot["expires_at"]) + timedelta(seconds=1)
    )
    distributor = SnapshotDistributor(
        system["repo"],
        system["service"].governance,
        system["service"].bindings,
        system["key"],
        "test-key",
        clock=expired_clock,
    )
    with (
        pytest.raises(EvolutionError) as stale,
        system["repo"].transaction(system["tenant"]) as tx,
    ):
        distributor.accept_receipt(tx, system["actors"]["runtime-a"], receipt)
    assert stale.value.code == "snapshot_stale"
    with system["repo"].transaction(system["tenant"]) as tx:
        renewed = distributor.issue(tx, system["actors"]["runtime-a"])
    assert renewed["generation"] == snapshot["generation"] + 1
    assert models.parse_time(renewed["expires_at"]) > models.parse_time(
        snapshot["expires_at"]
    )
    with system["repo"].transaction(system["tenant"]) as tx:
        assert tx.list("receipts") == []
        assert tx.get("applied_bindings", "chat") is None
        assert (
            tx.get("releases", system["release"]["release_id"])["status"] == "pending"
        )


def test_large_outbox_and_revoked_runtime_cannot_replay_old_success(sync_system):
    from uuid import NAMESPACE_URL, uuid5

    system = sync_system
    with system["repo"].transaction(system["tenant"]) as tx:
        for _ in range(1001):
            tx.append("outbox", {"type": "safe_historical_event"})
    result = run_runtime(system["configs"][0])
    assert result.returncode == 0
    stored = json.loads((system["tmp_path"] / "runtime-a.state").read_bytes())
    receipt = stored["receipts"][0]
    key = str(
        uuid5(
            NAMESPACE_URL,
            f"{system['runtimes'][0]}:chat:{receipt['snapshot_generation']}",
        )
    )
    with system["repo"].transaction(system["tenant"]) as tx:
        runtime = tx.get("runtimes", system["runtimes"][0], required=True)
        tx.put(
            "runtimes",
            runtime["runtime_id"],
            {**runtime, "status": "revoked"},
            expected_revision=runtime["revision"],
        )
    context = ssl.create_default_context(cafile=system["endpoint"]["ca_file"])
    with httpx.Client(
        base_url=system["endpoint"]["url"], verify=context, trust_env=False
    ) as client:
        replay = client.post(
            "/v2/runtime/receipts",
            json=receipt,
            headers={
                "Authorization": "Bearer " + system["tokens"]["runtime-a"],
                "Idempotency-Key": key,
            },
        )
        assert replay.status_code == 403
    with system["repo"].transaction(system["tenant"]) as tx:
        assert len(tx.list("receipts")) == 1
        assert tx.get("applied_bindings", "chat") is None
