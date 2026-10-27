"""PostgreSQL tenant transactions; SQL privileges remain the isolation boundary.

Only migration processes receive an owner credential. Serving transactions use a
non-owner, NOBYPASSRLS role and a transaction-local authenticated tenant scope.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from .deadlines import current_deadline, remaining_ms
from .errors import EvolutionError

IMMUTABLE_KINDS = frozenset({"policies", "approvals", "bindings", "audit", "outbox", "snapshots", "evidence", "checkpoints"})
_SCOPE = re.compile(r"(?:[a-z0-9][a-z0-9-]{0,63}|_platform)\Z")


def canonical_bytes(value: Any) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _data(value: Any) -> dict:
    result = json.loads(canonical_bytes(value))
    if not isinstance(result, dict):
        raise EvolutionError("validation_failed")
    return result


def migrate(database_url: str) -> None:
    """Apply idempotent forward revisions using a separate owner credential."""
    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            for revision in ("0001_shared_state", "0002_config_identity", "0003_quota_state"):
                module = importlib.import_module("guardrails.evolution.migrations.versions." + revision)
                conn.connection.driver_connection.execute(module.SCHEMA_SQL)
    finally:
        engine.dispose()


class TenantRepository:
    def __init__(self, database_url: str | Engine, *, verify_role: bool = True):
        self.engine = database_url if isinstance(database_url, Engine) else create_engine(
            database_url, pool_timeout=1, pool_reset_on_return="rollback", hide_parameters=True,
            connect_args={"connect_timeout": 2},
        )
        if verify_role:
            with self.engine.connect() as conn:
                role = conn.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
                owner = conn.execute(text("SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='evolution' AND c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user))")).scalar_one()
                if role.rolsuper or role.rolbypassrls or owner:
                    raise ValueError("Serving database role must be non-owner and NOBYPASSRLS")

    @contextmanager
    def transaction(self, tenant_id: str) -> Iterator[TenantTransaction]:
        if not isinstance(tenant_id, str) or not _SCOPE.fullmatch(tenant_id):
            raise EvolutionError("forbidden")
        remaining_ms()
        try:
            with self.engine.begin() as conn:
                tx = TenantTransaction(conn, tenant_id)
                # set_config(..., true) is the parameter-safe SET LOCAL equivalent.
                tx.execute("SELECT set_config('guardrails.tenant_id', :tenant, true)", {"tenant": tenant_id})
                yield tx
                # A transport timeout must never be followed by a late commit.
                tx._apply_deadline()
        except (DBAPIError, PoolTimeout) as error:
            if current_deadline.get() is not None and (
                isinstance(error, PoolTimeout) or getattr(error.orig, "sqlstate", None) in {"57014", "55P03"}
            ):
                raise EvolutionError("upstream_timeout") from None
            raise

    def provision_tenant(self, tenant: Any, residency: Any, quota: Any, *, tx: TenantTransaction | None = None) -> dict:
        if tx is None:
            with self.transaction("_platform") as owned:
                return self.provision_tenant(tenant, residency, quota, tx=owned)
        if tx.tenant_id != "_platform":
            raise EvolutionError("forbidden")
        value, residency_value, quota_value = _data(tenant), _data(residency), _data(quota)
        tenant_id = value["tenant_id"]
        if not _SCOPE.fullmatch(tenant_id) or tenant_id == "_platform":
            raise EvolutionError("validation_failed")
        tx.lock("tenant:" + tenant_id)
        if tx.get("tenants", "_platform") is None:
            tx.insert("tenants", "_platform", {"tenant_id": "_platform", "status": "active", "display_name": "Platform"})
        tx.insert("tenant_directory", tenant_id, {"tenant_id": "_platform", "target_tenant_id": tenant_id, "display_name": value["display_name"]})
        tx.execute("SELECT set_config('guardrails.tenant_id', :tenant, true)", {"tenant": tenant_id})
        try:
            target = TenantTransaction(tx.connection, tenant_id)
            result = target.insert("tenants", tenant_id, value)
            target.insert("config:residency", f"{residency_value['residency_id']}:{residency_value['version']}", residency_value)
            target.insert("config:quotas", f"{quota_value['quota_id']}:{quota_value['version']}", quota_value)
            return result
        finally:
            # PostgreSQL rejects all SQL after a statement error; the outer owner
            # then rolls back. Never obscure that error with a failed scope reset.
            if tx.connection.connection.driver_connection.info.transaction_status.name != "INERROR":
                tx.execute("SELECT set_config('guardrails.tenant_id', '_platform', true)")

    create_tenant = provision_tenant

    def list_tenants(self) -> list[dict]:
        with self.transaction("_platform") as tx:
            rows = tx.list("tenant_directory", limit=1000)
            return [{"tenant_id": row["target_tenant_id"], "display_name": row["display_name"]} for row in rows]

    def close(self) -> None:
        self.engine.dispose()


class TenantTransaction:
    def __init__(self, connection: Connection, tenant_id: str, *, _governance_owner: str | None = None):
        self.connection = connection
        self.tenant_id = tenant_id
        self._governance_owner = _governance_owner or tenant_id
        self._held_locks: set[str] = set()
        self._lock_transaction_scope = None

    def execute(self, statement: str, parameters: dict | None = None):
        self._apply_deadline()
        return self.connection.execute(text(statement), parameters or {})

    def _apply_deadline(self):
        budget = remaining_ms()
        if budget is not None:
            self.connection.execute(text("SELECT set_config('statement_timeout', :budget, true)"), {"budget": str(budget)})
            remaining_ms()

    def lock(self, key: str) -> None:
        remaining_ms()
        transaction_scope = (self.connection.get_transaction(), self.connection.get_nested_transaction())
        if transaction_scope != self._lock_transaction_scope:
            # Rolling back a savepoint can release locks acquired inside it.
            # Reacquisition is safe even when a scope transition retained locks.
            self._held_locks.clear()
            self._lock_transaction_scope = transaction_scope
        scoped_key = self.tenant_id + ":" + key
        if scoped_key not in self._held_locks:
            self.execute("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))", {"key": scoped_key})
            self._held_locks.add(scoped_key)

    def lock_governance(self, *, exclusive: bool = False) -> None:
        """Hold organization requirements stable through the original commit.

        Scope views share the SQL connection and retain the original transaction
        owner. Only an explicit platform publication/revocation asks for the
        exclusive mode; ordinary platform admission remains shared.
        """
        remaining_ms()
        scope = (self.connection.get_transaction(), self.connection.get_nested_transaction())
        state = self.connection.info.get("evolution_governance_lock")
        if type(exclusive) is not bool or exclusive and self._governance_owner != "_platform":
            raise EvolutionError("forbidden")
        mode = "exclusive" if exclusive else "shared"
        if state is not None and state[0][0] is scope[0]:
            if state[1] == "shared" and mode == "exclusive":
                raise EvolutionError("revision_conflict")
            # A savepoint transition can release an acquisition. Reacquire the
            # strongest original mode; never downgrade or upgrade within this
            # transaction, even through a different tenant scope view.
            mode = state[1]
            if state[0] == scope:
                return
        function = "pg_advisory_xact_lock" if mode == "exclusive" else "pg_advisory_xact_lock_shared"
        self.execute(f"SELECT {function}(hashtextextended(:key,0))",
                     {"key": "guardrails:organization-governance"})
        self.connection.info["evolution_governance_lock"] = (scope, mode)

    def get(self, kind: str, key: str, *, required: bool = False, for_update: bool = False) -> dict | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant AND kind=:kind AND key=:key" + suffix,
                           {"tenant": self.tenant_id, "kind": kind, "key": str(key)}).first()
        if row is None:
            if required:
                raise EvolutionError("resource_not_found")
            return None
        return row.data

    def insert(self, kind: str, key: str, data: Any, *, immutable: bool = False, audit_sequence: int | None = None) -> dict:
        value = _data(data)
        if value.get("tenant_id", self.tenant_id) not in (self.tenant_id, None if self.tenant_id == "_platform" else self.tenant_id):
            raise EvolutionError("resource_not_found")
        if kind == "tenants":
            self.execute("INSERT INTO evolution.tenants(tenant_id) VALUES (:tenant)", {"tenant": self.tenant_id})
        value.setdefault("created_at", datetime.now(UTC).isoformat().replace("+00:00", "Z"))
        is_immutable = immutable or kind in IMMUTABLE_KINDS or kind.startswith("config:")
        if not is_immutable:
            value.setdefault("revision", 1)
        payload = canonical_bytes(value)
        stored_bytes = payload
        if kind == "policies":
            from pydantic import ValidationError

            from .compiler import canonical_bytes as policy_bytes
            from .models import PolicyVersion
            try:
                policy = PolicyVersion.model_validate_json(payload)
                stored_bytes = policy_bytes(policy)
                if hashlib.sha256(stored_bytes).hexdigest() != policy.compiled_digest:
                    raise EvolutionError("validation_failed")
            except (ValidationError, ValueError, TypeError):
                raise EvolutionError("validation_failed") from None
        try:
            self.execute("""INSERT INTO evolution.records(tenant_id,kind,key,data,canonical_bytes,digest,revision,immutable,audit_sequence)
                VALUES (:tenant,:kind,:key,CAST(:data AS jsonb),:payload,:digest,:revision,:immutable,:audit_sequence)""",
                {"tenant": self.tenant_id, "kind": kind, "key": str(key), "data": payload.decode(), "payload": stored_bytes,
                 "digest": hashlib.sha256(stored_bytes).hexdigest(), "revision": value.get("revision", 1), "immutable": is_immutable, "audit_sequence": audit_sequence})
        except IntegrityError:
            raise EvolutionError("revision_conflict") from None
        return value

    def append(self, kind: str, data: Any, *, key: str | None = None) -> dict:
        return self.insert(kind, key or str(uuid4()), data, immutable=True)

    def put(self, kind: str, key: str, data: Any, *, expected_revision: int) -> dict:
        if type(expected_revision) is not int or expected_revision < 1:
            raise EvolutionError("revision_conflict")
        value = _data(data)
        if value.get("tenant_id", self.tenant_id) not in (self.tenant_id, None if self.tenant_id == "_platform" else self.tenant_id):
            raise EvolutionError("resource_not_found")
        previous = self.get(kind, key, required=True, for_update=True)
        value.setdefault("created_at", previous["created_at"])
        value["revision"] = expected_revision + 1
        payload = canonical_bytes(value)
        row = self.execute("""UPDATE evolution.records SET data=CAST(:data AS jsonb),canonical_bytes=:payload,digest=:digest,revision=:new_revision
            WHERE tenant_id=:tenant AND kind=:kind AND key=:key AND revision=:expected AND NOT immutable RETURNING key""",
            {"tenant": self.tenant_id, "kind": kind, "key": str(key), "data": payload.decode(), "payload": payload,
             "digest": hashlib.sha256(payload).hexdigest(), "new_revision": value["revision"], "expected": expected_revision}).first()
        if row is None:
            raise EvolutionError("revision_conflict")
        return value

    def list(self, kind: str, *, limit: int = 100, after: str | None = None) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise EvolutionError("validation_failed")
        return [row.data for row in self.execute("""SELECT data FROM evolution.records
            WHERE tenant_id=:tenant AND kind=:kind AND (CAST(:after AS text) IS NULL OR key > :after)
            ORDER BY key LIMIT :limit""", {"tenant": self.tenant_id, "kind": kind, "after": after, "limit": limit})]

    def page(self, kind: str, *, limit: int = 50, after: tuple[str, str] | None = None, filters: dict | None = None) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 101:
            raise EvolutionError("validation_failed")
        query = "SELECT key,data,created_at FROM evolution.records WHERE tenant_id=:tenant AND kind=:kind AND data @> CAST(:filters AS jsonb)"
        parameters = {"tenant": self.tenant_id, "kind": kind, "filters": canonical_bytes(filters or {}).decode(), "limit": limit}
        if after is not None:
            query += " AND (created_at,key) > (CAST(:after_time AS timestamptz),:after_key)"
            parameters.update(after_time=after[0], after_key=after[1])
        query += " ORDER BY created_at,key LIMIT :limit"
        return [{"key": row.key, "data": row.data, "created_at": row.created_at.isoformat().replace("+00:00", "Z")} for row in self.execute(query, parameters)]

    def next_version(self, kind: str, resource_id: str) -> int:
        self.lock(kind + ":" + resource_id)
        return self.execute("""SELECT COALESCE(MAX((data->>'version')::integer),0)+1 FROM evolution.records
            WHERE tenant_id=:tenant AND kind=:kind AND key = :resource_id || ':' || (data->>'version')""",
            {"tenant": self.tenant_id, "kind": kind, "resource_id": resource_id}).scalar_one()

    def link(self, source_kind: str, source_key: str, target_kind: str, target_key: str) -> None:
        self.execute("""INSERT INTO evolution.record_links(tenant_id,source_kind,source_key,target_kind,target_key)
            VALUES (:tenant,:sk,:sid,:tk,:tid) ON CONFLICT DO NOTHING""",
            {"tenant": self.tenant_id, "sk": source_kind, "sid": source_key, "tk": target_kind, "tid": target_key})

    def cas_binding(self, workload_id: str, expected_revision: int, binding: Any) -> dict:
        self.lock("binding:" + workload_id)
        self.get("workloads", workload_id, required=True, for_update=True)
        current = self.get("binding_pointers", workload_id, for_update=True)
        revision = current["binding_revision"] if current else 0
        if type(expected_revision) is not int or revision != expected_revision:
            raise EvolutionError("revision_conflict")
        value = _data(binding)
        value.update(tenant_id=self.tenant_id, workload_id=workload_id, revision=revision + 1,
                     previous_revision=revision or None)
        self.insert("bindings", f"{workload_id}:{revision + 1}", value, immutable=True)
        pointer = {"tenant_id": self.tenant_id, "workload_id": workload_id, "binding_revision": revision + 1}
        if current:
            self.put("binding_pointers", workload_id, pointer, expected_revision=current["revision"])
        else:
            self.insert("binding_pointers", workload_id, pointer)
        return value
