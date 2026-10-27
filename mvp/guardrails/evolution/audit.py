"""Content-free audit chains, bounded customer spools and signed retention."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import BoundedSemaphore
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .errors import EvolutionError, STATUS
from .models import AUDIT_METADATA_KEYS, AuditEvent
from .storage import TenantRepository, TenantTransaction, canonical_bytes

_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
EVENT_TYPES = frozenset({"policy_submitted", "policy_approved", "policy_rejected", "policy_revoked", "baseline_published",
    "release_pending", "release_applied", "release_failed", "configuration_created", "credential_issued", "credential_rotate",
    "credential_revoke", "workload_created", "workload_updated", "runtime_registered", "evidence_received", "migration_imported",
    "tenant_created", "tenant_suspended", "principal_created", "session_created", "session_revoked", "evaluation", "audit_retention",
    "snapshot_applied", "runtime_revoked", "support_access", "backup_created", "restore_completed"})
_SAFE_REASONS = frozenset(STATUS) | {"pii_detected", "secret_detected", "content_denied", "content_not_allowed", "schema_invalid", "injection_detected", "image_text_detected", "disabled", "inspection_scope_image_text"}
_EVENT_FIELDS = frozenset({"event_id", "event_type", "actor_id", "workload_id", "correlation_id", "decision_id", "policy_digest",
                          "binding_revision", "reason_codes", "action", "metadata", "occurred_at"})


def _safe_metadata(value: dict | None) -> dict:
    result = value or {}
    if not isinstance(result, dict) or set(result) - AUDIT_METADATA_KEYS:
        raise EvolutionError("validation_failed")
    for item in result.values():
        if type(item) not in (str, int, bool) or (isinstance(item, str) and not _CODE.fullmatch(item)):
            raise EvolutionError("validation_failed")
    return dict(result)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_payload(event: dict) -> dict:
    if set(event) - (_EVENT_FIELDS | {"tenant_id"}) or event.get("event_type") not in EVENT_TYPES:
        raise EvolutionError("validation_failed")
    event = dict(event)
    event["metadata"] = _safe_metadata(event.get("metadata"))
    reasons = event.get("reason_codes", ())
    if not isinstance(reasons, (list, tuple)) or len(reasons) > 128 or any(not isinstance(x, str) or x not in _SAFE_REASONS for x in reasons):
        raise EvolutionError("validation_failed")
    event.setdefault("event_id", str(uuid4()))
    event.setdefault("correlation_id", str(uuid4()))
    event.setdefault("occurred_at", _timestamp())
    candidate = dict(event, event_hash="0" * 64, created_at=event["occurred_at"])
    try:
        # Canonical JSON parses and validates without ever including evaluation content.
        model = AuditEvent.model_validate_json(canonical_bytes(candidate))
    except (ValidationError, ValueError, TypeError):
        raise EvolutionError("validation_failed") from None
    data = model.model_dump(mode="json")
    return {k: v for k, v in data.items() if k in _EVENT_FIELDS or k == "tenant_id"}


class AuditStore:
    def __init__(self, repository: TenantRepository, *, retention_repository: TenantRepository | None = None):
        self.repository = repository
        self.retention_repository = retention_repository
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audit-metadata")
        self._available = BoundedSemaphore(2)

    def append(self, tx: TenantTransaction, event_type: str, *, actor_id: str | None = None, workload_id: str | None = None,
               correlation_id: str | None = None, decision_id: str | None = None, policy_digest: str | None = None,
               binding_revision: int | None = None, reason_codes: tuple[str, ...] = (), action: str | None = None,
               metadata: dict | None = None, event_id: str | None = None, occurred_at: str | None = None) -> dict:
        tx.lock("audit")
        event_id = event_id or str(uuid4())
        existing = tx.get("audit", event_id)
        event = _validate_payload({
            "event_id": event_id, "tenant_id": tx.tenant_id, "event_type": event_type,
            "actor_id": actor_id, "workload_id": workload_id,
            "correlation_id": correlation_id or (existing["correlation_id"] if existing else str(uuid4())),
            "decision_id": decision_id, "policy_digest": policy_digest, "binding_revision": binding_revision,
            "reason_codes": list(reason_codes), "action": action, "metadata": metadata or {},
            "occurred_at": occurred_at or (existing["occurred_at"] if existing else _timestamp()),
        })
        if existing:
            if any(existing.get(k) != value for k, value in event.items()):
                raise EvolutionError("idempotency_conflict")
            return existing
        tx.execute("INSERT INTO evolution.audit_heads(tenant_id,sequence) VALUES(:tenant,0) ON CONFLICT DO NOTHING", {"tenant": tx.tenant_id})
        head = tx.execute("SELECT sequence,event_hash FROM evolution.audit_heads WHERE tenant_id=:tenant FOR UPDATE", {"tenant": tx.tenant_id}).one()
        event.update(previous_hash=head.event_hash, raw_content_retained=False, created_at=event["occurred_at"])
        event["event_hash"] = hashlib.sha256(canonical_bytes(event)).hexdigest()
        result = tx.insert("audit", event_id, event, immutable=True, audit_sequence=head.sequence + 1)
        tx.execute("UPDATE evolution.audit_heads SET sequence=:sequence,event_hash=:hash WHERE tenant_id=:tenant",
                   {"tenant": tx.tenant_id, "sequence": head.sequence + 1, "hash": event["event_hash"]})
        return result

    @staticmethod
    def decision_metadata(decision: Any) -> dict:
        value = decision.model_dump(mode="json") if hasattr(decision, "model_dump") else decision
        # Explicit field selection happens before a persistence object exists.
        policy = value.get("policy") or {}
        return _validate_payload({"tenant_id": value["tenant_id"], "event_type": "evaluation",
            "event_id": value["decision_id"], "decision_id": value["decision_id"], "correlation_id": value["request_id"],
            "workload_id": value["workload_id"], "policy_digest": policy.get("digest"),
            "binding_revision": value.get("binding_revision"), "reason_codes": value.get("reason_codes", ()),
            "action": value["action"], "metadata": {"stage": value["stage"]}})

    def append_decision(self, tx: TenantTransaction, decision: Any) -> dict:
        event = self.decision_metadata(decision)
        if event.pop("tenant_id") != tx.tenant_id:
            raise EvolutionError("resource_not_found")
        return self.append(tx, **event)

    def persist_decision(self, decision: Any, *, spool: "MetadataSpool | None" = None, budget_ms: int = 100) -> str:
        if type(budget_ms) is not int or budget_ms <= 0:
            raise EvolutionError("audit_failed")
        deadline = time.monotonic() + budget_ms / 1000
        event = self.decision_metadata(decision)
        tenant_id = event.pop("tenant_id")
        if not self._available.acquire(blocking=False):
            raise EvolutionError("audit_failed")
        def persist():
            try:
                with self.repository.transaction(tenant_id) as tx:
                    remaining = max(1, int((deadline - time.monotonic()) * 1000))
                    if time.monotonic() >= deadline:
                        raise EvolutionError("audit_failed")
                    tx.execute("SELECT set_config('statement_timeout', :timeout, true)", {"timeout": str(remaining)})
                    self.append(tx, **event)
                return "persisted"
            except (SQLAlchemyError, OSError):
                if spool is None or time.monotonic() >= deadline:
                    raise EvolutionError("audit_failed") from None
                try:
                    spool.enqueue(dict(event, tenant_id=tenant_id))
                except (OSError, sqlite3.Error, EvolutionError):
                    raise EvolutionError("audit_failed") from None
                return "spooled"
        try:
            future = self._workers.submit(persist)
        except RuntimeError:
            self._available.release()
            raise EvolutionError("audit_failed") from None
        future.add_done_callback(lambda _: self._available.release())
        try:
            result = future.result(timeout=max(0, deadline - time.monotonic()))
        except FutureTimeout:
            # Database connect/commit and filesystem stalls cannot extend the
            # response deadline. A late metadata-only append never grants allow.
            future.cancel()
            raise EvolutionError("audit_failed") from None
        if time.monotonic() > deadline:
            raise EvolutionError("audit_failed")
        return result

    def close(self) -> None:
        self._workers.shutdown(wait=False, cancel_futures=True)

    def page(self, tenant_id: str, *, after: int = 0, limit: int = 50, workload_id: str | None = None,
             action: str | None = None) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(after) is not int or after < 0:
            raise EvolutionError("validation_failed")
        with self.repository.transaction(tenant_id) as tx:
            rows = tx.execute("""SELECT audit_sequence,data FROM evolution.records WHERE tenant_id=:tenant AND kind='audit'
                AND audit_sequence>:after AND (CAST(:workload AS text) IS NULL OR data->>'workload_id'=:workload)
                AND (CAST(:action AS text) IS NULL OR data->>'action'=:action) ORDER BY audit_sequence LIMIT :limit""",
                {"tenant": tenant_id, "after": after, "limit": limit, "workload": workload_id, "action": action})
            return [{"sequence": row.audit_sequence, "event": row.data} for row in rows]

    def verify_chain(self, tenant_id: str) -> bool:
        with self.repository.transaction(tenant_id) as tx:
            head = tx.execute("SELECT * FROM evolution.audit_heads WHERE tenant_id=:tenant", {"tenant": tenant_id}).first()
            if head is None:
                return True
            previous, expected = head.checkpoint_hash, head.checkpoint_sequence + 1
            for row in tx.execute("SELECT audit_sequence,data FROM evolution.records WHERE tenant_id=:tenant AND kind='audit' ORDER BY audit_sequence", {"tenant": tenant_id}):
                event = dict(row.data)
                claimed = event.pop("event_hash")
                if row.audit_sequence != expected or event["previous_hash"] != previous or hashlib.sha256(canonical_bytes(event)).hexdigest() != claimed:
                    return False
                expected += 1
                previous = claimed
            return expected == head.sequence + 1 and previous == head.event_hash

    def purge(self, tenant_id: str, *, before: str, signer: Callable[[bytes], bytes], key_id: str) -> dict | None:
        if self.retention_repository is None:
            raise EvolutionError("forbidden")
        try:
            cutoff = datetime.fromisoformat(before.replace("Z", "+00:00"))
            if cutoff.tzinfo is None or not _CODE.fullmatch(key_id):
                raise ValueError()
        except (AttributeError, ValueError, TypeError):
            raise EvolutionError("validation_failed") from None
        with self.retention_repository.transaction(tenant_id) as tx:
            if not tx.execute("SELECT has_function_privilege(current_user, 'evolution.purge_audit(text)', 'EXECUTE')").scalar_one():
                raise EvolutionError("forbidden")
            tx.lock("audit")
            last = None
            for row in tx.execute("SELECT audit_sequence,data FROM evolution.records WHERE tenant_id=:tenant AND kind='audit' ORDER BY audit_sequence", {"tenant": tenant_id}):
                if datetime.fromisoformat(row.data["occurred_at"].replace("Z", "+00:00")) >= cutoff:
                    break
                last = row
            if last is None:
                return None
            checkpoint = {"tenant_id": tenant_id, "checkpoint_id": str(uuid4()), "last_sequence": last.audit_sequence,
                          "last_hash": last.data["event_hash"], "purged_before": before, "created_at": _timestamp(), "key_id": key_id}
            signature = signer(canonical_bytes(checkpoint))
            if not isinstance(signature, bytes) or len(signature) != 64:
                raise EvolutionError("validation_failed")
            checkpoint["signature"] = base64.b64encode(signature).decode()
            tx.insert("checkpoints", checkpoint["checkpoint_id"], checkpoint, immutable=True)
            tx.execute("SELECT evolution.purge_audit(:checkpoint)", {"checkpoint": checkpoint["checkpoint_id"]})
            return checkpoint


class MetadataSpool:
    """SQLite is customer-local durable metadata only, never a shared DB substitute."""
    def __init__(self, path: str | Path, *, max_bytes: int = 1024 * 1024, max_events: int = 1000):
        if max_bytes < 1024 or max_events < 1:
            raise ValueError("Spool bounds must be positive")
        self.path, self.max_bytes, self.max_events = Path(path), max_bytes, max_events
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS events(tenant_id TEXT NOT NULL,event_id TEXT NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(tenant_id,event_id))")
        self.path.chmod(0o600)

    def enqueue(self, event: dict) -> None:
        safe = _validate_payload(event)
        payload = canonical_bytes(safe)
        with sqlite3.connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT payload FROM events WHERE tenant_id=? AND event_id=?", (safe["tenant_id"], safe["event_id"])).fetchone()
            if existing:
                if existing[0] != payload:
                    raise EvolutionError("idempotency_conflict")
                return
            count, total = conn.execute("SELECT COUNT(*),COALESCE(SUM(length(payload)),0) FROM events").fetchone()
            if count >= self.max_events or total + len(payload) > self.max_bytes:
                raise EvolutionError("audit_failed")
            conn.execute("INSERT INTO events VALUES(?,?,?)", (safe["tenant_id"], safe["event_id"], payload))

    def replay(self, store: AuditStore, tenant_id: str, *, limit: int = 100) -> int:
        if not 1 <= limit <= 100:
            raise EvolutionError("validation_failed")
        completed = 0
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT event_id,payload FROM events WHERE tenant_id=? ORDER BY rowid LIMIT ?", (tenant_id, limit)).fetchall()
            for event_id, payload in rows:
                event = _validate_payload(json.loads(payload))
                if event.pop("tenant_id") != tenant_id:
                    raise EvolutionError("resource_not_found")
                with store.repository.transaction(tenant_id) as tx:
                    store.append(tx, **event)
                # A crash between commit and deletion replays the exact event ID safely.
                conn.execute("DELETE FROM events WHERE tenant_id=? AND event_id=?", (tenant_id, event_id))
                conn.commit()
                completed += 1
        return completed
