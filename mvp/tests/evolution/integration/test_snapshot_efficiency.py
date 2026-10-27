"""Fresh signed disk reads and real HTTPS acknowledgements without duplicate work."""

import base64
import json
import multiprocessing
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from guardrails.evolution import models
from guardrails.evolution.compiler import PolicyCompiler, policy_digest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.runtime_apply import RuntimeApplyClient, RuntimeSnapshotStore
from guardrails.evolution.snapshots import snapshot_digest, stamp
from test_runtime_receipts import sync_system as _production_sync_system


@pytest.fixture(name="sync_system")
def real_sync_system(tmp_path):
    yield from _production_sync_system.__wrapped__(tmp_path)


def sign(value, key):
    value = deepcopy(value)
    value.pop("signature", None)
    value["digest"] = snapshot_digest(value)
    value["signature"] = base64.b64encode(
        key.sign(models.canonical_json_bytes(value))
    ).decode()
    return value


def cache(tmp_path, *, overlap=False):
    now = [datetime.now(UTC)]
    key = Ed25519PrivateKey.generate()
    tenant, runtime = "fresh-cache", str(uuid4())
    quota = models.with_resource_digest(
        models.Quota(tenant_id=tenant, quota_id=str(uuid4()))
    )
    residency = models.with_resource_digest(
        models.ResidencyProfile(tenant_id=tenant, residency_id=str(uuid4()))
    )
    candidate = models.PolicyVersion(
        tenant_id=tenant,
        policy_id="signed-policy",
        version=1,
        author_id=str(uuid4()),
        rules=(
            models.Rule(
                rule_id="email",
                kind="pii",
                stages=("input", "output"),
                action="block",
                mandatory=True,
                minimum_state="observe",
                evaluator_revision="pii-v2",
                config={"entities": ["email"]},
            ),
        ),
        residency=residency,
        quota=quota,
    )
    policy = (
        PolicyCompiler().compile(candidate).model_copy(update={"status": "approved"})
    )
    binding = models.Binding(
        tenant_id=tenant,
        workload_id="chat",
        policy=policy.reference(),
        revision=1,
        state="observe",
        reason="Validated signed cache",
        effective_after=stamp(now[0]),
        release_id=str(uuid4()),
        activated_by=str(uuid4()),
    )
    wire = sign(
        {
            "schema_version": "guardrails-v2",
            "tenant_id": tenant,
            "runtime_id": runtime,
            "generation": 1,
            "issued_at": stamp(now[0]),
            "expires_at": stamp(now[0] + timedelta(seconds=60)),
            "created_at": stamp(now[0]),
            "policies": [policy.model_dump(mode="json")],
            "bindings": [binding.model_dump(mode="json")],
            "revoked_digests": [],
            "key_id": "first",
        },
        key,
    )
    keys = {"first": key.public_key()}
    expiries = {}
    if overlap:
        keys["second"] = Ed25519PrivateKey.generate().public_key()
        expiries["first"] = now[0] + timedelta(seconds=10)
    store = RuntimeSnapshotStore(
        tmp_path / "runtime.state",
        tenant,
        runtime,
        keys,
        clock=lambda: now[0],
        trust_key_expiries=expiries,
    )
    store.apply(wire)
    return store, wire, key, now


def read(store, method):
    return (
        getattr(store, method)("chat")
        if method == "current"
        else getattr(store, method)()
    )


@pytest.mark.parametrize("method", ("current", "lease", "ready"))
def test_every_cache_read_revalidates_fresh_disk_signature_once(
    tmp_path, monkeypatch, method
):
    store, wire, _, _ = cache(tmp_path)
    original = store.verify
    calls = []

    def verified(value, *, allow_expired=False):
        calls.append(allow_expired)
        return original(value, allow_expired=allow_expired)

    monkeypatch.setattr(store, "verify", verified)
    saved = store.path.read_bytes()
    assert read(store, method)
    assert len(calls) == 1
    assert store.path.read_bytes() == saved
    # A warmed process must still inspect disk, including a same-length change.
    changed = json.loads(saved)
    changed["snapshot"]["generation"] = 2
    store.path.write_bytes(models.canonical_json_bytes(changed))
    with pytest.raises(EvolutionError) as refused:
        read(store, method)
    assert refused.value.code == "validation_failed"
    assert len(calls) == 2
    store.path.write_bytes(saved)
    # An altered in-memory wire copy cannot replace freshly verified disk bytes.
    store._snapshot_wire["policies"][0]["rules"][0]["config"]["entities"] = ["ipv4"]
    assert read(store, method)
    assert store.snapshot.policies[0].rules[0].config.entities == ("email",)
    assert (
        policy_digest(store.snapshot.policies[0])
        == wire["policies"][0]["compiled_digest"]
    )
    assert len(calls) == 3


@pytest.mark.parametrize("method", ("current", "lease", "ready"))
@pytest.mark.parametrize("boundary", ("lease", "key_overlap"))
def test_current_lease_and_readiness_enforce_fresh_expiry_without_rewriting(
    tmp_path, method, boundary
):
    store, wire, _, now = cache(tmp_path, overlap=boundary == "key_overlap")
    saved = store.path.read_bytes()
    assert read(store, method)
    now[0] = (
        models.parse_time(wire["expires_at"])
        if boundary == "lease"
        else store.trust_key_expiries["first"]
    )
    if method == "ready":
        assert read(store, method) is False
    else:
        with pytest.raises(EvolutionError) as refused:
            read(store, method)
        assert refused.value.code == (
            "snapshot_stale" if boundary == "lease" else "forbidden"
        )
    assert store.path.read_bytes() == saved
    restarted = RuntimeSnapshotStore(
        store.path,
        store.tenant_id,
        store.runtime_id,
        store.trust_keys,
        clock=lambda: now[0],
        trust_key_expiries=store.trust_key_expiries,
    )
    assert restarted.snapshot.generation == 1
    assert restarted.ready() is False
    assert store.path.read_bytes() == saved


def apply_in_process(path, tenant, runtime, public, wire):
    store = RuntimeSnapshotStore(
        path, tenant, runtime, {"first": Ed25519PublicKey.from_public_bytes(public)}
    )
    store.apply(wire)


def test_current_reads_other_process_commit_and_rejects_durable_generation_replay(
    tmp_path,
):
    store, wire, key, _ = cache(tmp_path)
    older = store.path.read_bytes()
    newer = sign({**wire, "generation": 2}, key)
    process = multiprocessing.get_context("spawn").Process(
        target=apply_in_process,
        args=(
            store.path,
            store.tenant_id,
            store.runtime_id,
            key.public_key().public_bytes_raw(),
            newer,
        ),
    )
    try:
        process.start()
        process.join(10)
        assert process.exitcode == 0
        snapshot, binding, policy = store.current("chat")
        assert snapshot.generation == 2
        assert binding.policy == policy.reference()
        assert store.lease().digest == newer["digest"]
        store.path.write_bytes(older)
        for method in ("current", "lease", "ready"):
            with pytest.raises(EvolutionError) as replay:
                read(store, method)
            assert replay.value.code == "revision_conflict"
        assert store.snapshot.generation == 2
    finally:
        if process.is_alive():
            process.kill()
        process.join(3)


def client_for(system):
    config = json.loads(system["configs"][0].read_bytes())
    store = RuntimeSnapshotStore(
        config["state_file"],
        system["tenant"],
        system["runtimes"][0],
        {"test-key": system["key"].public_key()},
    )
    client = RuntimeApplyClient(
        config["url"], config["ca_file"], config["credential_file"], store
    )
    return client


def trace(client):
    actual = client.client.request
    calls = []

    def request(method, path, **kwargs):
        response = actual(method, path, **kwargs)
        calls.append(
            (
                method,
                response.status_code,
                kwargs.get("headers", {}).get("Idempotency-Key"),
            )
        )
        return response

    client.client.request = request
    return calls, request


def test_real_https_acknowledged_304_skips_posts_restart_replays_and_new_generation_posts(
    sync_system,
):
    system = sync_system
    client = client_for(system)
    calls, _ = trace(client)
    try:
        first = client.sync_once()
        saved = client.store.path.read_bytes()
        assert first["status"] == "ready"
        assert [(m, s) for m, s, _ in calls] == [("GET", 200), ("POST", 200)]
        key = calls[-1][2]
        with system["repo"].transaction(system["tenant"]) as tx:
            initial = tx.get("runtimes", system["runtimes"][0])["last_seen_at"]
            assert len(tx.list("receipts")) == 1
        calls.clear()
        assert client.sync_once()["status"] == "ready"
        assert [(m, s) for m, s, _ in calls] == [("GET", 304)]
        assert client.store.path.read_bytes() == saved
        restarted = client_for(system)
        restarted_calls, _ = trace(restarted)
        try:
            assert restarted.sync_once()["status"] == "ready"
            assert [(m, s) for m, s, _ in restarted_calls] == [
                ("GET", 304),
                ("POST", 200),
            ]
            assert restarted_calls[-1][2] == key
        finally:
            restarted.close()
        with system["repo"].transaction(system["tenant"]) as tx:
            assert len(tx.list("receipts")) == 1
            assert tx.get("runtimes", system["runtimes"][0])["last_seen_at"] == initial
            system["service"].bindings.release(
                tx,
                system["actors"]["operator"],
                policy=system["release"]["policy"],
                workload_ids=["chat"],
                stage="canary",
                state="observe",
                expected_revisions={"chat": 1},
                reason="Refresh exact receipt generation",
            )
        calls.clear()
        second = client.sync_once()
        assert second["generation"] > first["generation"]
        assert [(m, s) for m, s, _ in calls] == [("GET", 200), ("POST", 200)]
        assert calls[-1][2] != key
        assert len(client._acknowledged_receipts) == 1
        with system["repo"].transaction(system["tenant"]) as tx:
            assert len(tx.list("receipts")) == 2
            assert tx.get("applied_bindings", "chat") is None
    finally:
        client.close()


@pytest.mark.parametrize("failure", ("lost_response", "mismatched_response"))
def test_real_https_failed_acknowledgement_remains_retryable_without_duplicate_receipt(
    sync_system, failure
):
    system = sync_system
    client = client_for(system)
    calls, actual = trace(client)
    failed = False

    def response_failure(method, path, **kwargs):
        nonlocal failed
        response = actual(method, path, **kwargs)
        if method == "POST" and not failed:
            failed = True
            if failure == "lost_response":
                raise httpx.ReadError("Synthetic response delivery failure")
            receipt = response.json()
            receipt["health"] = "failed"
            return httpx.Response(200, json=receipt)
        return response

    client.client.request = response_failure
    try:
        with pytest.raises((httpx.ReadError, EvolutionError)):
            client.sync_once()
        assert client._acknowledged_receipts == {}
        assert [(m, s) for m, s, _ in calls] == [("GET", 200), ("POST", 200)]
        key = calls[-1][2]
        calls.clear()
        client.client.request = actual
        assert client.sync_once()["status"] == "ready"
        assert [(m, s) for m, s, _ in calls] == [("GET", 304), ("POST", 200)]
        assert calls[-1][2] == key
        assert len(client._acknowledged_receipts) == 1
        with system["repo"].transaction(system["tenant"]) as tx:
            assert len(tx.list("receipts")) == 1
    finally:
        client.close()


def test_real_https_304_does_not_extend_expired_cached_lease_or_send_receipts(
    sync_system,
):
    client = client_for(sync_system)
    calls, _ = trace(client)
    try:
        assert client.sync_once()["status"] == "ready"
        saved = client.store.path.read_bytes()
        expiry = models.parse_time(client.store.snapshot.expires_at)
        client.store.clock = lambda: expiry
        calls.clear()
        with pytest.raises(EvolutionError) as expired:
            client.sync_once()
        assert expired.value.code == "snapshot_stale"
        assert [(m, s) for m, s, _ in calls] == [("GET", 304)]
        assert client.store.path.read_bytes() == saved
    finally:
        client.close()
