"""Apply verified online snapshots and acknowledge only durable, loaded policy bytes.

This synchronization prerequisite exposes no evaluation endpoint. Readiness means
its signed policy lease is fresh; evaluation capability has its own later gate.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import ssl
import stat
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from .cli import _secret
from .compiler import policy_digest
from .deadlines import remaining_ms
from .errors import EvolutionError
from .http_client import BoundedJsonClient
from .models import (
    RuntimeReceipt,
    Snapshot,
    canonical_json_bytes,
    parse_time,
    strict_json_loads,
)
from .snapshots import snapshot_digest, stamp

MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024


class RuntimeSnapshotStore:
    def __init__(
        self,
        state_file,
        tenant_id,
        runtime_id,
        trust_keys,
        *,
        clock=None,
        capability_check=None,
        trust_key_expiries=None,
    ):
        self.path = Path(state_file)
        self.tenant_id, self.runtime_id = tenant_id, runtime_id
        if not 1 <= len(trust_keys) <= 2:
            raise ValueError(
                "One or two explicitly pinned snapshot trust keys are required"
            )
        self.trust_keys = dict(trust_keys)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.capability_check = capability_check
        self.trust_key_expiries = dict(trust_key_expiries or {})
        if (
            not set(self.trust_key_expiries) <= set(self.trust_keys)
            or len(self.trust_keys) == 2
            and len(self.trust_key_expiries) != 1
            or any(
                not isinstance(expiry, datetime)
                or expiry.tzinfo is None
                or expiry > self.clock() + timedelta(seconds=60)
                for expiry in self.trust_key_expiries.values()
            )
        ):
            raise ValueError(
                "A second pinned trust key requires a bounded overlap of at most 60 seconds"
            )
        self.snapshot, self.receipts = None, ()
        self._snapshot_wire = None
        with self._locked():
            self._reload()

    @contextmanager
    def _locked(self):
        # The lock has a stable inode across atomic cache replacements and is
        # shared by every process using this runtime's durable state path.
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_name(self.path.name + ".lock")
        try:
            descriptor = os.open(
                lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
            )
        except OSError:
            raise EvolutionError("validation_failed") from None
        try:
            details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_mode & 0o077
                or details.st_uid != os.getuid()
            ):
                raise EvolutionError("validation_failed")
            lock_deadline = time.monotonic() + 2
            while True:
                remaining_ms()
                if time.monotonic() >= lock_deadline:
                    raise EvolutionError("snapshot_stale")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.001)
            yield
        finally:
            os.close(descriptor)

    def _reload(self, *, allow_expired=True):
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            if self.snapshot is not None:
                raise EvolutionError("revision_conflict") from None
            return
        except OSError:
            raise EvolutionError("validation_failed") from None
        with os.fdopen(descriptor, "rb") as source:
            details = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_mode & 0o077
                or details.st_uid != os.getuid()
                or details.st_size > MAX_SNAPSHOT_BYTES
            ):
                raise EvolutionError("validation_failed")
            raw = source.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise EvolutionError("payload_too_large")
        try:
            stored = strict_json_loads(raw)
            if set(stored) != {"snapshot", "receipts"}:
                raise EvolutionError("validation_failed")
            snapshot = self.verify(stored["snapshot"], allow_expired=allow_expired)
            receipts = tuple(
                RuntimeReceipt.model_validate_json(
                    canonical_json_bytes(value)
                ).model_dump(mode="json")
                for value in stored["receipts"]
            )
            expected = {
                (binding.workload_id, binding.revision, binding.policy.digest)
                for binding in snapshot.bindings
            }
            actual = {
                (receipt["workload_id"], receipt["binding_revision"], receipt["digest"])
                for receipt in receipts
            }
            if (
                expected != actual
                or len(actual) != len(receipts)
                or any(
                    receipt["runtime_id"] != self.runtime_id
                    or receipt["tenant_id"] != self.tenant_id
                    or receipt["snapshot_generation"] != snapshot.generation
                    for receipt in receipts
                )
            ):
                raise EvolutionError("validation_failed")
        except ValidationError, ValueError, TypeError, KeyError:
            raise EvolutionError("validation_failed") from None
        self._check_generation(snapshot)
        self.snapshot, self.receipts = snapshot, receipts
        self._snapshot_wire = stored["snapshot"]

    def _check_generation(self, snapshot):
        if self.snapshot and (
            snapshot.generation < self.snapshot.generation
            or (
                snapshot.generation == self.snapshot.generation
                and snapshot.digest != self.snapshot.digest
            )
        ):
            raise EvolutionError("revision_conflict")

    def verify(self, value, *, allow_expired=False):
        try:
            payload = (
                strict_json_loads(value) if isinstance(value, (str, bytes)) else value
            )
            snapshot = Snapshot.model_validate_json(canonical_json_bytes(payload))
            if (
                snapshot.tenant_id != self.tenant_id
                or snapshot.runtime_id != self.runtime_id
            ):
                raise EvolutionError("resource_not_found")
            key = self.trust_keys.get(snapshot.key_id)
            if key is None or (
                not allow_expired
                and self.trust_key_expiries.get(
                    snapshot.key_id, self.clock() + timedelta(seconds=1)
                )
                <= self.clock()
            ):
                raise EvolutionError("forbidden")
            key.verify(
                base64.b64decode(snapshot.signature, validate=True),
                canonical_json_bytes(
                    {
                        name: value
                        for name, value in payload.items()
                        if name != "signature"
                    }
                ),
            )
            if snapshot.digest != snapshot_digest(payload):
                raise EvolutionError("validation_failed")
            now = self.clock()
            if parse_time(snapshot.issued_at) > now + timedelta(
                seconds=5
            ) or parse_time(snapshot.expires_at) - parse_time(
                snapshot.issued_at
            ) > timedelta(seconds=60):
                raise EvolutionError("snapshot_stale")
            if not allow_expired and parse_time(snapshot.expires_at) <= now:
                raise EvolutionError("snapshot_stale")
            policies = {}
            for policy in snapshot.policies:
                if (
                    policy.compiled_digest in policies
                    or policy.status != "approved"
                    or policy_digest(policy) != policy.compiled_digest
                ):
                    raise EvolutionError("approval_required")
                policies[policy.compiled_digest] = policy
            workloads = set()
            for binding in snapshot.bindings:
                policy = policies.get(binding.policy.digest)
                if (
                    binding.workload_id in workloads
                    or policy is None
                    or policy.reference() != binding.policy
                ):
                    raise EvolutionError("policy_unresolved")
                if binding.policy.digest in snapshot.revoked_digests or any(
                    parent.digest in snapshot.revoked_digests
                    for parent in policy.parents
                ):
                    raise EvolutionError("policy_revoked")
                workloads.add(binding.workload_id)
            return snapshot
        except ValidationError, ValueError, TypeError, KeyError, InvalidSignature:
            raise EvolutionError("validation_failed") from None

    def apply(self, value):
        with self._locked():
            self._reload()
            return self._apply_locked(value)

    def _apply_locked(self, value):
        wire = (
            strict_json_loads(value)
            if isinstance(value, (str, bytes))
            else strict_json_loads(canonical_json_bytes(value))
        )
        snapshot = self.verify(wire)
        self._check_generation(snapshot)
        self._require_capabilities(snapshot)
        if self.snapshot and snapshot.generation == self.snapshot.generation:
            return self.receipts
        applied_at = stamp(self.clock())
        receipts = tuple(
            RuntimeReceipt(
                runtime_id=self.runtime_id,
                tenant_id=self.tenant_id,
                workload_id=binding.workload_id,
                binding_revision=binding.revision,
                snapshot_generation=snapshot.generation,
                digest=binding.policy.digest,
                applied_at=applied_at,
                created_at=applied_at,
                health="ready",
            ).model_dump(mode="json")
            for binding in snapshot.bindings
        )
        stored = canonical_json_bytes({"snapshot": wire, "receipts": receipts})
        if len(stored) > MAX_SNAPSHOT_BYTES:
            raise EvolutionError("payload_too_large")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise EvolutionError("validation_failed")
        staged = self.path.with_name(self.path.name + "." + str(uuid4()) + ".pending")
        descriptor = os.open(
            staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(stored)
            output.flush()
            os.fsync(output.fileno())
        staged.replace(self.path)
        descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # The live pointer changes only after validated bytes and receipts are durable.
        self.snapshot, self.receipts = snapshot, receipts
        self._snapshot_wire = wire
        return receipts

    def _require_capabilities(self, snapshot):
        if self.capability_check:
            for policy in snapshot.policies:
                self.capability_check(policy)

    def _require_fresh(self, snapshot):
        # _reload has just validated these exact disk bytes and their signature.
        # Readiness additionally rejects expired leases and expired key overlap;
        # historical cache loading deliberately permits both for recovery.
        now = self.clock()
        if (
            snapshot.key_id not in self.trust_keys
            or self.trust_key_expiries.get(snapshot.key_id, now + timedelta(seconds=1))
            <= now
        ):
            raise EvolutionError("forbidden")
        if parse_time(snapshot.expires_at) <= now:
            raise EvolutionError("snapshot_stale")

    def ready(self):
        with self._locked():
            self._reload()
            if self.snapshot is None:
                return False
            try:
                self._require_fresh(self.snapshot)
                self._require_capabilities(self.snapshot)
                return True
            except EvolutionError:
                return False

    def current(self, workload_id):
        """Read lease, binding and immutable bytes under one durable-cache lock."""
        with self._locked():
            self._reload(allow_expired=False)
            if self.snapshot is None:
                raise EvolutionError("snapshot_stale")
            snapshot = self.snapshot
            binding = next(
                (item for item in snapshot.bindings if item.workload_id == workload_id),
                None,
            )
            if binding is None:
                raise EvolutionError("policy_unresolved")
            policy = next(
                item
                for item in snapshot.policies
                if item.compiled_digest == binding.policy.digest
            )
            if self.capability_check:
                self.capability_check(policy)
            return snapshot, binding, policy

    def lease(self):
        """Fresh revocation lease for an existing pinned context."""
        with self._locked():
            self._reload(allow_expired=False)
            if self.snapshot is None:
                raise EvolutionError("snapshot_stale")
            return self.snapshot

    def policy_for(self, workload_id):
        return self.current(workload_id)[2]


class RuntimeApplyClient:
    def __init__(self, url, ca_file, credential_file, store):
        origin = urlsplit(url)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path not in ("", "/")
        ):
            raise ValueError(
                "Runtime synchronization requires a configured HTTPS origin"
            )
        self.store = store
        self.credential_file = credential_file
        self.closed = False
        # Only exact server acknowledgements for the current generation survive
        # in memory. Restart always replays durable receipts to the server.
        self._acknowledged_receipts = {}
        self.client = BoundedJsonClient(
            base_url=url.rstrip("/"),
            verify=ssl.create_default_context(cafile=ca_file),
            deadline=time.monotonic(),
            max_bytes=MAX_SNAPSHOT_BYTES,
        )

    def sync_once(self):
        if self.closed:
            raise RuntimeError("Runtime synchronization client is closed")
        self.client.deadline = time.monotonic() + 3
        headers = {"Authorization": "Bearer " + _secret(self.credential_file)}
        if self.store.snapshot:
            headers["If-None-Match"] = '"' + self.store.snapshot.digest + '"'
        response = self.client.request("GET", "/v2/runtime/snapshot", headers=headers)
        unchanged = response.status_code == 304
        if response.status_code == 304:
            if not self.store.ready():
                raise EvolutionError("snapshot_stale")
        elif response.status_code == 200:
            self.store.apply(response.content)
        else:
            raise EvolutionError("policy_unresolved")
        pending = [
            (
                str(
                    uuid5(
                        NAMESPACE_URL,
                        f"{self.store.runtime_id}:{receipt['workload_id']}:{receipt['snapshot_generation']}",
                    )
                ),
                receipt,
            )
            for receipt in self.store.receipts
        ]
        current_keys = {key for key, _ in pending}
        self._acknowledged_receipts = {
            key: value
            for key, value in self._acknowledged_receipts.items()
            if key in current_keys
        }
        posted = False
        for key, receipt in pending:
            exact_receipt = canonical_json_bytes(receipt)
            if unchanged and self._acknowledged_receipts.get(key) == exact_receipt:
                continue
            response = self.client.request(
                "POST",
                "/v2/runtime/receipts",
                json=receipt,
                headers={**headers, "Idempotency-Key": key},
            )
            if response.status_code != 200 or response.json() != receipt:
                raise EvolutionError("policy_unresolved")
            posted = True
            self._acknowledged_receipts[key] = exact_receipt
        # Unchanged acknowledged polls have no intervening network work after
        # ready(). After a receipt POST, recheck because its lease may expire
        # while awaiting the server. A 304 never renews a local timestamp.
        ready = True if unchanged and not posted else self.store.ready()
        return {
            "status": "ready" if ready else "stale",
            "generation": self.store.snapshot.generation,
            "policy_digests": [
                policy.compiled_digest for policy in self.store.snapshot.policies
            ],
            "receipts": len(self.store.receipts),
        }

    def close(self):
        self.closed = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-file", type=Path, required=True)
    args = parser.parse_args(argv)
    client = None
    try:
        config = strict_json_loads(args.config_file.read_bytes())
        if set(config) != {
            "url",
            "ca_file",
            "credential_file",
            "state_file",
            "tenant_id",
            "runtime_id",
            "trust_key_files",
        }:
            raise ValueError("Invalid runtime synchronization configuration")
        keys = {
            key: Ed25519PublicKey.from_public_bytes(Path(path).read_bytes())
            for key, path in config["trust_key_files"].items()
        }
        store = RuntimeSnapshotStore(
            config["state_file"], config["tenant_id"], config["runtime_id"], keys
        )
        client = RuntimeApplyClient(
            config["url"], config["ca_file"], config["credential_file"], store
        )
        print(json.dumps(client.sync_once(), sort_keys=True))
        return 0
    except EvolutionError as exc:
        print("Runtime snapshot synchronization failed: " + exc.code, file=sys.stderr)
        return 1
    except ValueError, OSError, httpx.HTTPError:
        print("Runtime snapshot synchronization failed", file=sys.stderr)
        return 1
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
