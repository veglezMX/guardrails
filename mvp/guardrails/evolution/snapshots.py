"""Signed tenant snapshots from committed desired state and authenticated receipts."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .errors import EvolutionError
from .governance import lock_admission, require_role
from .models import (
    PolicyVersion,
    RuntimeReceipt,
    Snapshot,
    canonical_json_bytes,
    parse_time,
)


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def snapshot_digest(payload: dict) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                key: value
                for key, value in payload.items()
                if key not in {"digest", "signature"}
            }
        )
    ).hexdigest()


def records(tx, kind, *, filters=None):
    """Walk the repository's stable tenant-scoped cursor, without truncation."""
    after = None
    while True:
        page = tx.page(kind, limit=100, after=after, filters=filters)
        for row in page:
            yield row["data"]
        if len(page) < 100:
            return
        after = (page[-1]["created_at"], page[-1]["key"])


class SnapshotDistributor:
    def __init__(
        self,
        repository,
        governance,
        bindings,
        signing_key: Ed25519PrivateKey,
        key_id: str,
        *,
        clock=None,
        ttl_s=60,
        capability_check=None,
    ):
        if type(ttl_s) is not int or not 1 <= ttl_s <= 60 or not key_id:
            raise ValueError("Invalid snapshot signing configuration")
        self.repository, self.governance, self.bindings = (
            repository,
            governance,
            bindings,
        )
        self.signing_key, self.key_id = signing_key, key_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.ttl_s = ttl_s
        if capability_check is None:
            from .plugins import EvaluatorRegistry
            capability_check = EvaluatorRegistry(workers=1).validate_declared
        self.capability_check = capability_check

    def runtime(self, tx, identity):
        actor = require_role(identity, {"runtime"}, tx.tenant_id)
        if tx.get("tenants", tx.tenant_id, required=True)["status"] != "active":
            raise EvolutionError("forbidden")
        relation = tx.get("runtime_principals", actor["principal_id"], required=True)
        install = tx.get("runtimes", relation["runtime_id"], required=True)
        if install["status"] == "revoked":
            raise EvolutionError("forbidden")
        return install

    def issue(self, tx, identity):
        lock_admission(tx)
        install = self.runtime(tx, identity)
        runtime_id = install["runtime_id"]
        tx.lock("snapshot:" + runtime_id)
        if tx.get("tenants", tx.tenant_id, required=True)["status"] != "active":
            raise EvolutionError("forbidden")
        # Read only committed binding/outbox transactions; never synthesize bindings.
        targets = records(tx, "runtime_targets", filters={"runtime_ids": [runtime_id]})
        bindings, policies = [], {}
        revoked = {row["digest"] for row in records(tx, "revocations")}
        for target in targets:
            if runtime_id not in target["runtime_ids"]:
                continue
            workload_id = target["workload_id"]
            pointer = tx.get("binding_pointers", workload_id)
            if not pointer:
                continue
            binding = tx.get(
                "bindings",
                f"{workload_id}:{pointer['binding_revision']}",
                required=True,
            )
            if not tx.page(
                "outbox",
                limit=1,
                filters={
                    "type": "binding_desired",
                    "release_id": binding["release_id"],
                },
            ):
                raise EvolutionError("policy_unresolved")
            try:
                policy = self.governance.eligible(tx, binding["policy"])
            except EvolutionError as exc:
                if exc.code not in {
                    "policy_revoked",
                    "floor_conflict",
                    "composition_conflict",
                    "approval_required",
                }:
                    raise
                revoked.add(binding["policy"]["digest"])
                continue
            if install["zone"] not in policy["residency"]["allowed_zones"]:
                raise EvolutionError("residency_denied")
            parsed_policy = PolicyVersion.model_validate_json(canonical_json_bytes(policy))
            if self.capability_check:
                self.capability_check(parsed_policy)
            bindings.append(binding)
            # Add current additive model defaults BEFORE signing the envelope.
            # Immutable old policy digests retain their versioned canonical form.
            policies[policy["compiled_digest"]] = parsed_policy.model_dump(mode="json")
        bindings.sort(key=lambda item: item["workload_id"])
        configuration = {
            "bindings": bindings,
            "policies": sorted(
                policies.values(), key=lambda item: item["compiled_digest"]
            ),
            "revoked_digests": sorted(revoked),
        }
        fingerprint = hashlib.sha256(canonical_json_bytes(configuration)).hexdigest()
        current = tx.get("snapshot_pointers", runtime_id)
        now = self.clock()
        if current and current["fingerprint"] == fingerprint:
            previous = tx.get(
                "issued_snapshots",
                f"{runtime_id}:{current['generation']}",
                required=True,
            )
            # Renew before expiry: a poll crossing this boundary can consume
            # its shared 3-second budget, then pause 0.1 seconds before the next
            # 3-second sync. Ten seconds leaves scheduling headroom. Short
            # leases retain at least half their TTL without generation churn.
            # This issues fresh signed bytes; it never extends an old lease.
            renewal_lead = timedelta(seconds=min(10, self.ttl_s / 2))
            if (
                parse_time(previous["expires_at"]) > now + renewal_lead
                and previous["key_id"] == self.key_id
            ):
                return previous
        generation = current["generation"] + 1 if current else 1
        payload = {
            "schema_version": "guardrails-v2",
            "runtime_id": runtime_id,
            "tenant_id": tx.tenant_id,
            "generation": generation,
            "issued_at": stamp(now),
            "expires_at": stamp(now + timedelta(seconds=self.ttl_s)),
            **configuration,
            "created_at": stamp(now),
            "key_id": self.key_id,
        }
        payload["digest"] = snapshot_digest(payload)
        payload["signature"] = base64.b64encode(
            self.signing_key.sign(canonical_json_bytes(payload))
        ).decode()
        snapshot = Snapshot.model_validate_json(
            canonical_json_bytes(payload)
        ).model_dump(mode="json")
        tx.insert(
            "issued_snapshots", f"{runtime_id}:{generation}", snapshot, immutable=True
        )
        pointer = {"generation": generation, "fingerprint": fingerprint}
        if current:
            tx.put(
                "snapshot_pointers",
                runtime_id,
                pointer,
                expected_revision=current["revision"],
            )
        else:
            tx.insert("snapshot_pointers", runtime_id, pointer)
        return snapshot

    def accept_receipt(self, tx, identity, value):
        lock_admission(tx)
        receipt = RuntimeReceipt.model_validate_json(
            canonical_json_bytes(value)
        ).model_dump(mode="json")
        runtime = self.runtime(tx, identity)
        if (
            receipt["tenant_id"] != tx.tenant_id
            or receipt["runtime_id"] != runtime["runtime_id"]
        ):
            raise EvolutionError("resource_not_found")
        issued = tx.get(
            "issued_snapshots",
            f"{runtime['runtime_id']}:{receipt['snapshot_generation']}",
            required=True,
        )
        pointer = tx.get("snapshot_pointers", runtime["runtime_id"], required=True)
        now = self.clock()
        if (
            parse_time(issued["expires_at"]) <= now
            or parse_time(receipt["applied_at"]) < parse_time(issued["issued_at"])
            or parse_time(receipt["applied_at"]) > now + timedelta(seconds=5)
        ):
            raise EvolutionError("snapshot_stale")
        if pointer["generation"] != receipt["snapshot_generation"]:
            raise EvolutionError("revision_conflict")
        tx.lock("workload:" + receipt["workload_id"])
        current = tx.get("binding_pointers", receipt["workload_id"], required=True)
        if current["binding_revision"] != receipt["binding_revision"]:
            raise EvolutionError("revision_conflict")
        target = tx.get("runtime_targets", receipt["workload_id"], required=True)
        if runtime["runtime_id"] not in target["runtime_ids"]:
            raise EvolutionError("forbidden")
        return self.bindings.receipt(tx, identity, receipt)
