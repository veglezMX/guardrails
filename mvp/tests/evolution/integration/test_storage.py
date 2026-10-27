"""Real PostgreSQL acceptance: these tests fail (never skip) without configured DB."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import importlib
import os
from uuid import uuid4

import pytest
from psycopg.errors import CheckViolation
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from guardrails.evolution.audit import AuditStore
from guardrails.evolution.compiler import policy_digest
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import PolicyVersion, ResidencyProfile, Quota
from guardrails.evolution.storage import TenantRepository, canonical_bytes, migrate


def repository():
    path = os.environ.get("EVOLUTION_TEST_DSN_FILE")
    if not path:
        pytest.fail("EVOLUTION_TEST_DSN_FILE must point to a real PostgreSQL18 non-owner DSN")
    return TenantRepository(Path(path).read_text().strip())


def scope(repo):
    tenant = "storage-" + uuid4().hex[:16]
    with repo.transaction(tenant) as tx:
        tx.insert("tenants", tenant, {"tenant_id": tenant, "display_name": "Synthetic tenant", "status": "active"})
    return tenant


def candidate(tenant):
    policy = PolicyVersion(tenant_id=tenant, policy_id="safe", version=1, author_id=str(uuid4()), rules=(),
        residency=ResidencyProfile(tenant_id=tenant, residency_id=str(uuid4()), version=1),
        quota=Quota(tenant_id=tenant, quota_id=str(uuid4()), version=1))
    return policy.model_copy(update={"compiled_digest": policy_digest(policy)}).model_dump(mode="json")


def test_postgres18_nonowner_forced_rls_and_pool_reuse():
    repo = repository()
    a, b = scope(repo), scope(repo)
    for tenant in (a, b):
        with repo.transaction(tenant) as tx:
            tx.insert("test_rows", "identical-id", {"tenant_id": tenant, "value": tenant})
    with repo.transaction(a) as tx:
        assert tx.execute("SHOW server_version_num").scalar_one().startswith("18")
        assert not tx.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user").scalar_one()
        assert all(row.relrowsecurity and row.relforcerowsecurity for row in tx.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='evolution' AND c.relkind='r'"))
        # Deliberately omit tenant SQL filters: PostgreSQL itself isolates rows.
        rows = tx.execute("SELECT tenant_id FROM evolution.records WHERE kind='test_rows'").all()
        assert {row.tenant_id for row in rows} == {a}
        assert tx.get("test_rows", "identical-id")["value"] == a
        assert tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:other", {"other": b}).all() == []
    with repo.transaction(b) as tx:
        assert tx.get("test_rows", "identical-id")["value"] == b
    with repo.engine.connect() as conn:
        assert conn.execute(text("SELECT current_setting('guardrails.tenant_id',true)")).scalar_one() in (None, "")
        with pytest.raises(DBAPIError):
            conn.execute(text("SELECT * FROM evolution.records")).all()
    repo.close()


def test_cross_tenant_insert_and_composite_foreign_key_rejected():
    repo = repository()
    a, b = scope(repo), scope(repo)
    principal_id = str(uuid4())
    with repo.transaction(b) as tx:
        tx.insert("principals", principal_id, {"principal_id": principal_id, "tenant_id": b, "roles": ["viewer"], "status": "active"})
    with pytest.raises(EvolutionError, match="Resource not found"):
        with repo.transaction(a) as tx:
            tx.insert("test_rows", "foreign", {"tenant_id": b})
    with pytest.raises(EvolutionError, match="Revision conflict"):
        with repo.transaction(a) as tx:
            tx.insert("credentials", "foreign-id", {"tenant_id": a, "principal_id": principal_id})
    with pytest.raises(DBAPIError):
        with repo.transaction(a) as tx:
            tx.execute("INSERT INTO evolution.tenants(tenant_id) VALUES (:other)", {"other": "foreign-" + uuid4().hex[:12]})
    with repo.transaction(a) as tx:
        assert tx.list("credentials") == []
        assert tx.get("principals", principal_id) is None
    repo.close()


def test_immutable_bytes_and_atomic_audit_outbox_rollback():
    repo = repository()
    tenant = scope(repo)
    policy = candidate(tenant)
    audit = AuditStore(repo)
    with repo.transaction(tenant) as tx:
        tx.insert("policies", "safe:1", policy)
        payload = tx.execute("SELECT canonical_bytes FROM evolution.records WHERE kind='policies'").scalar_one()
        assert b'"status"' not in payload
        assert b'"compiled_digest"' not in payload
        assert b'"schema_version":"guardrails-v2"' in payload
    for statement in ("UPDATE evolution.records SET canonical_bytes='x' WHERE kind='policies'", "DELETE FROM evolution.records WHERE kind='policies'", "UPDATE evolution.records SET immutable=false WHERE kind='policies'"):
        with pytest.raises(DBAPIError):
            with repo.transaction(tenant) as tx:
                tx.execute(statement)
    with pytest.raises(RuntimeError):
        with repo.transaction(tenant) as tx:
            audit.append(tx, "policy_submitted", policy_digest=policy["compiled_digest"])
            tx.append("outbox", {"operation": "publish"}, key="rolled-back")
            tx.insert("test_rows", "rolled-back", {})
            raise RuntimeError("Synthetic rollback")
    with repo.transaction(tenant) as tx:
        assert tx.list("audit") == []
        assert tx.list("outbox") == []
        assert tx.get("test_rows", "rolled-back") is None
        assert tx.get("policies", "safe:1")["compiled_digest"] == policy["compiled_digest"]
    repo.close()


def test_concurrent_binding_compare_and_swap_has_one_winner():
    repo = repository()
    tenant = scope(repo)
    policy = candidate(tenant)
    with repo.transaction(tenant) as tx:
        tx.insert("policies", "safe:1", policy)
        tx.insert("workloads", "chat", {"tenant_id": tenant, "workload_id": "chat", "status": "active"})
    binding = {"policy": {"tenant_id": tenant, "policy_id": "safe", "version": 1, "digest": policy["compiled_digest"]}, "state": "block"}
    def activate(_):
        try:
            with repo.transaction(tenant) as tx:
                tx.cas_binding("chat", 0, binding)
                AuditStore(repo).append(tx, "release_pending")
                tx.append("outbox", {"operation": "binding"})
            return "won"
        except EvolutionError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(activate, range(2)))
    assert sorted(results) == ["revision_conflict", "won"]
    with repo.transaction(tenant) as tx:
        assert len(tx.list("bindings")) == 1
        assert len(tx.list("audit")) == 1
        assert len(tx.list("outbox")) == 1
        assert tx.get("binding_pointers", "chat")["binding_revision"] == 1
    repo.close()


def test_keyset_pages_and_version_locks_preserve_all_records():
    repo = repository()
    tenant = scope(repo)
    with repo.transaction(tenant) as tx:
        for index in range(105):
            tx.insert("test_rows", f"id-{index:03d}", {"status": "active"})
        first = tx.page("test_rows", limit=100, filters={"status": "active"})
        second = tx.page("test_rows", limit=100, after=(first[-1]["created_at"], first[-1]["key"]))
        assert len(first) == 100 and len(second) == 5
        assert len({row["key"] for row in first + second}) == 105
    def version(_):
        with repo.transaction(tenant) as tx:
            number = tx.next_version("test_version", "policy")
            tx.insert("test_version", f"policy:{number}", {"version": number})
            return number
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(version, range(8))) == list(range(1, 9))
    repo.close()


def test_migration_fresh_database_and_owner_rejected():
    path = os.environ.get("EVOLUTION_TEST_OWNER_DSN_FILE")
    if not path:
        pytest.fail("EVOLUTION_TEST_OWNER_DSN_FILE required for isolated migration proof")
    owner = create_engine(Path(path).read_text().strip(), hide_parameters=True, isolation_level="AUTOCOMMIT")
    name = "evolution_migration_" + uuid4().hex[:12]
    with owner.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    migration_url = owner.url.set(database=name).render_as_string(hide_password=False)
    try:
        migrate(migration_url)
        migrate(migration_url)
        with pytest.raises(ValueError, match="non-owner"):
            TenantRepository(migration_url)
        check = create_engine(migration_url, hide_parameters=True)
        with check.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='evolution' AND relkind='r' AND relrowsecurity AND relforcerowsecurity")).scalar_one() == 4
            assert conn.execute(text("SELECT count(*) FROM pg_constraint WHERE connamespace='evolution'::regnamespace AND contype='f'")).scalar_one() >= 7
            assert conn.execute(text("SELECT convalidated FROM pg_constraint WHERE conrelid='evolution.records'::regclass AND conname='records_config_identity'")).scalar_one()
        check.dispose()
    finally:
        with owner.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        owner.dispose()


def test_tenant_provisioning_joins_platform_transaction_and_restores_scope():
    repo = repository()
    tenant = "atomic-" + uuid4().hex[:16]
    residency = {"residency_id": str(uuid4()), "tenant_id": tenant, "version": 1}
    quota = {"quota_id": str(uuid4()), "tenant_id": tenant, "version": 1}
    data = {"tenant_id": tenant, "display_name": "Atomic tenant", "status": "active"}
    with pytest.raises(RuntimeError):
        with repo.transaction("_platform") as tx:
            repo.provision_tenant(data, residency, quota, tx=tx)
            assert tx.execute("SELECT evolution.tenant_scope()").scalar_one() == "_platform"
            tx.insert("idempotency", "create-" + tenant, {"operation": "tenant_create"})
            AuditStore(repo).append(tx, "tenant_created")
            raise RuntimeError("Synthetic failure before outer commit")
    with repo.transaction(tenant) as tx:
        assert tx.get("tenants", tenant) is None
        assert tx.list("config:quotas") == []
    with repo.transaction("_platform") as tx:
        assert tx.get("tenant_directory", tenant) is None
        assert tx.get("idempotency", "create-" + tenant) is None
    with repo.transaction("_platform") as tx:
        repo.provision_tenant(data, residency, quota, tx=tx)
        assert tx.execute("SELECT evolution.tenant_scope()").scalar_one() == "_platform"
        assert tx.get("tenant_directory", tenant)["target_tenant_id"] == tenant
    with repo.transaction(tenant) as tx:
        assert tx.get("tenants", tenant)["display_name"] == "Atomic tenant"
        assert len(tx.list("config:residency")) == 1 and len(tx.list("config:quotas")) == 1
    repo.close()


@pytest.mark.parametrize("family,id_field", [
    ("lists", "list_id"), ("residency", "residency_id"),
    ("destinations", "destination_id"), ("providers", "provider_id"),
    ("tools", "tool_id"), ("quotas", "quota_id"),
])
def test_config_identity_constraint_rejects_nonowner_malformed_keys(family, id_field):
    repo = repository()
    tenant, other_tenant = scope(repo), scope(repo)
    resource_id = str(uuid4())
    value = {"tenant_id": tenant, id_field: resource_id, "version": 1}

    def direct_insert(tx, key, body):
        payload = canonical_bytes(body)
        tx.execute("""INSERT INTO evolution.records(tenant_id,kind,key,data,canonical_bytes,digest,immutable)
            VALUES(:tenant,:kind,:key,CAST(:data AS jsonb),:bytes,:digest,true)""", {
            "tenant": tx.tenant_id, "kind": "config:" + family, "key": key,
            "data": payload.decode(), "bytes": payload, "digest": hashlib.sha256(payload).hexdigest(),
        })

    with repo.transaction(tenant) as tx:
        assert not tx.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user").scalar_one()
        direct_insert(tx, resource_id + ":1", value)
    # Alternate keys cannot create duplicate logical versions, and NULL must not
    # bypass SQL CHECK evaluation when an identity component is absent.
    malformed = [
        (resource_id + ":2", value),
        (str(uuid4()) + ":1", value),
        (resource_id + ":01", value),
        (resource_id + ":1", {k: v for k, v in value.items() if k != id_field}),
        (resource_id + ":1", {k: v for k, v in value.items() if k != "tenant_id"}),
        (resource_id + ":1", {**value, "version": "1"}),
        (resource_id + ":0", {**value, "version": 0}),
        (resource_id + ":1.0", {**value, "version": 1.0}),
        (resource_id.upper() + ":1", {**value, id_field: resource_id.upper()}),
    ]
    for key, body in malformed:
        with pytest.raises(DBAPIError) as failure:
            with repo.transaction(tenant) as tx:
                direct_insert(tx, key, body)
        assert failure.value.orig.sqlstate == "23514"
        assert failure.value.orig.diag.constraint_name == "records_config_identity"
    with pytest.raises(DBAPIError) as duplicate:
        with repo.transaction(tenant) as tx:
            direct_insert(tx, resource_id + ":1", value)
    assert duplicate.value.orig.sqlstate == "23505"
    # The same ID/version belongs to a different composite identity in another
    # tenant; the DB must permit it while RLS hides it from this tenant.
    with repo.transaction(other_tenant) as tx:
        direct_insert(tx, resource_id + ":1", {**value, "tenant_id": other_tenant})
    with repo.transaction(tenant) as tx:
        rows = tx.list("config:" + family)
        assert len(rows) == 1 and rows[0] == value
        assert tx.execute("SELECT canonical_bytes FROM evolution.records WHERE kind=:kind", {"kind": "config:" + family}).scalar_one() == canonical_bytes(value)
    repo.close()


def test_exact_policy_id_allocation_is_independent_under_concurrency():
    repo = repository()
    tenant = scope(repo)
    identifiers = ("a_b", "aXb", "a", "a:child")

    def allocate(policy_id):
        with repo.transaction(tenant) as tx:
            version = tx.next_version("policies", policy_id)
            value = {**candidate(tenant), "policy_id": policy_id, "version": version}
            model = PolicyVersion.model_validate_json(canonical_bytes(value))
            value["compiled_digest"] = policy_digest(model)
            tx.insert("policies", f"{policy_id}:{version}", value)
            return policy_id, version

    assert allocate("aXb") == ("aXb", 1)
    assert allocate("a_b") == ("a_b", 1)
    assert allocate("a:child") == ("a:child", 1)
    assert allocate("a") == ("a", 1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        allocated = list(pool.map(allocate, identifiers * 3))
    for policy_id in identifiers:
        assert sorted(version for actual, version in allocated if actual == policy_id) == [2, 3, 4]
    with repo.transaction(tenant) as tx:
        assert len(tx.list("policies")) == 16
        assert all(tx.next_version("policies", policy_id) == 5 for policy_id in identifiers)
    repo.close()


def test_config_identity_upgrade_rejects_malformed_history_without_rewriting():
    path = os.environ.get("EVOLUTION_TEST_OWNER_DSN_FILE")
    if not path:
        pytest.fail("EVOLUTION_TEST_OWNER_DSN_FILE required for isolated upgrade proof")
    owner = create_engine(Path(path).read_text().strip(), hide_parameters=True, isolation_level="AUTOCOMMIT")
    name = "evolution_upgrade_" + uuid4().hex[:12]
    with owner.connect() as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    url = owner.url.set(database=name).render_as_string(hide_password=False)
    previous = create_engine(url, hide_parameters=True)
    resource_id = str(uuid4())
    payload = canonical_bytes({"tenant_id": "prior", "quota_id": resource_id, "version": 1})
    try:
        initial = importlib.import_module("guardrails.evolution.migrations.versions.0001_shared_state")
        with previous.begin() as conn:
            conn.connection.driver_connection.execute(initial.SCHEMA_SQL)
            conn.execute(text("SELECT set_config('guardrails.tenant_id','prior',true)"))
            conn.execute(text("INSERT INTO evolution.tenants(tenant_id) VALUES('prior')"))
            conn.execute(text("""INSERT INTO evolution.records(tenant_id,kind,key,data,canonical_bytes,digest,immutable)
                VALUES('prior','config:quotas','incorrect-historical-key',CAST(:data AS jsonb),:bytes,:digest,true)"""),
                {"data": payload.decode(), "bytes": payload, "digest": hashlib.sha256(payload).hexdigest()})
        with pytest.raises(CheckViolation) as failure:
            migrate(url)
        assert failure.value.sqlstate == "23514"
        with previous.begin() as conn:
            conn.execute(text("SELECT set_config('guardrails.tenant_id','prior',true)"))
            assert conn.execute(text("SELECT canonical_bytes FROM evolution.records WHERE key='incorrect-historical-key'")).scalar_one() == payload
            assert conn.execute(text("SELECT COUNT(*) FROM pg_constraint WHERE conrelid='evolution.records'::regclass AND conname='records_config_identity'")).scalar_one() == 0
    finally:
        previous.dispose()
        with owner.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        owner.dispose()
