"""Signed-cache compatibility, bounded trust rotation and real capability gates."""

import asyncio
import base64
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from guardrails.evolution import models
from guardrails.evolution.compiler import policy_digest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.plugins import EvaluatorRegistry
from guardrails.evolution.runtime_apply import RuntimeSnapshotStore
from guardrails.evolution.snapshots import snapshot_digest, stamp
from test_evaluation_runtime import fixture_system


def sign(value, key):
    value = deepcopy(value)
    value.pop("signature", None)
    value["digest"] = snapshot_digest(value)
    value["signature"] = base64.b64encode(key.sign(models.canonical_json_bytes(value))).decode()
    return value


def test_historical_signed_wire_survives_added_policy_defaults_and_restart(tmp_path):
    system = fixture_system(tmp_path)
    try:
        wire = deepcopy(system["stores"][0]._snapshot_wire)
        policy = models.PolicyVersion.model_validate(wire["policies"][0]).model_copy(update={"compiler_version": "compiler-v2.1"})
        policy = policy.model_copy(update={"compiled_digest": policy_digest(policy)})
        wire["policies"] = [policy.model_dump(mode="json", exclude={"resolved_destinations"})]
        wire["bindings"][0]["policy"] = policy.reference().model_dump(mode="json")
        wire = sign(wire, system["key"])
        path = tmp_path / "historical.state"
        store = RuntimeSnapshotStore(path, system["tenant"], system["runtime_ids"][0], {"test-key": system["key"].public_key()})
        store.apply(wire)
        persisted = path.read_bytes()
        assert "resolved_destinations" not in json.loads(persisted)["snapshot"]["policies"][0]
        assert store.ready()
        restarted = RuntimeSnapshotStore(path, system["tenant"], system["runtime_ids"][0], {"test-key": system["key"].public_key()})
        assert restarted.ready()
        snapshot, binding, loaded = restarted.current("chat")
        assert snapshot.digest == wire["digest"]
        assert binding.policy == loaded.reference() == policy.reference()
        assert loaded.resolved_destinations == ()
        assert path.read_bytes() == persisted
    finally:
        system["repo"].close()


def test_two_key_overlap_expires_old_signer_without_extending_snapshot_or_rewriting_cache(tmp_path):
    old, new = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    now = [datetime.now(UTC)]
    runtime_id = str(uuid4())
    value = {"schema_version": "guardrails-v2", "runtime_id": runtime_id, "tenant_id": "rotation-tenant", "generation": 1, "issued_at": stamp(now[0]), "expires_at": stamp(now[0] + timedelta(seconds=60)), "created_at": stamp(now[0]), "bindings": [], "policies": [], "revoked_digests": [], "key_id": "old"}
    keys = {"old": old.public_key(), "new": new.public_key()}
    with pytest.raises(ValueError):
        RuntimeSnapshotStore(tmp_path / "unbounded", "rotation-tenant", runtime_id, keys)
    store = RuntimeSnapshotStore(tmp_path / "bounded", "rotation-tenant", runtime_id, keys, clock=lambda: now[0], trust_key_expiries={"old": now[0] + timedelta(seconds=1)})
    store.apply(sign(value, old))
    saved = store.path.read_bytes()
    assert store.ready()
    now[0] += timedelta(seconds=2)
    assert not store.ready()
    with pytest.raises(EvolutionError) as expired:
        store.apply(sign(value, old))
    assert expired.value.code == "forbidden"
    assert store.path.read_bytes() == saved
    value.update(generation=2, key_id="new", issued_at=stamp(now[0]), expires_at=stamp(now[0] + timedelta(seconds=60)))
    store.apply(sign(value, new))
    assert store.ready() and store.snapshot.generation == 2
    assert store.snapshot.key_id == "new"


def test_registry_rejects_unsupported_policy_before_activation_and_cache_replacement(tmp_path):
    system = fixture_system(tmp_path)
    async def exercise():
        registry = EvaluatorRegistry(tenant_ids=(system["tenant"],), workers=1)
        await registry.start()
        try:
            service = system["service"]
            payload = deepcopy(system["payload"])
            payload["rules"][0]["config"]["entities"] = ["ipv4"]
            with system["repo"].transaction(system["tenant"]) as tx:
                candidate = service.governance.submit(tx, system["actors"]["author"], "unsupported", payload)
                service.governance.review(tx, system["actors"]["approver"], "unsupported", 1, outcome="approve", compiled_digest=candidate["compiled_digest"], reason="Validate capability boundary")
            reference = {"tenant_id": system["tenant"], "policy_id": "unsupported", "version": 1, "digest": candidate["compiled_digest"]}
            service.bindings.capability_check = registry.activation_check
            with pytest.raises(EvolutionError) as missing, system["repo"].transaction(system["tenant"]) as tx:
                service.bindings.release(tx, system["actors"]["operator"], policy=reference, workload_ids=["chat"], stage="canary", state="observe", expected_revisions={"chat": 1}, reason="Must reject")
            assert missing.value.code == "capability_missing"
            with system["repo"].transaction(system["tenant"]) as tx:
                assert tx.get("binding_pointers", "chat")["binding_revision"] == 1
                assert len(tx.list("bindings")) == 1
            store = system["stores"][0]
            store.capability_check = registry.require
            saved = store.path.read_bytes()
            wire = deepcopy(store._snapshot_wire)
            wire["generation"] += 1
            wire["policies"] = [{**candidate, "status": "approved"}]
            wire["bindings"][0]["policy"] = reference
            with pytest.raises(EvolutionError) as unsupported:
                store.apply(sign(wire, system["key"]))
            assert unsupported.value.code == "capability_missing"
            assert store.path.read_bytes() == saved
            assert store.current("chat")[2].compiled_digest == system["policy_ref"]["digest"]
            assert store.ready()
        finally:
            await registry.close()
    try:
        asyncio.run(exercise())
    finally:
        system["repo"].close()
