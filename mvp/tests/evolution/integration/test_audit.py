"""PostgreSQL audit atomicity, concurrency, retention and durable content-free spool."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import base64
import json
import os
from pathlib import Path
import sqlite3
import time
import secrets
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError, OperationalError

from guardrails.evolution.audit import AuditStore, MetadataSpool
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.storage import TenantRepository, canonical_bytes


def setup_store():
    path = os.environ.get("EVOLUTION_TEST_DSN_FILE")
    if not path:
        pytest.fail("EVOLUTION_TEST_DSN_FILE required; audit acceptance needs PostgreSQL")
    repo = TenantRepository(Path(path).read_text().strip())
    tenant = "audit-" + uuid4().hex[:16]
    with repo.transaction(tenant) as tx:
        tx.insert("tenants", tenant, {"tenant_id": tenant, "status": "active"})
    return repo, tenant, AuditStore(repo)


@pytest.fixture
def retention_repository():
    owner_file = os.environ.get("EVOLUTION_TEST_OWNER_DSN_FILE")
    if not owner_file:
        pytest.fail("Owner DSN file required to provision an isolated retention role")
    owner = create_engine(Path(owner_file).read_text().strip(), hide_parameters=True)
    role = "retention_" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    from psycopg import sql
    with owner.begin() as conn:
        raw = conn.connection.driver_connection
        raw.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOINHERIT").format(sql.Identifier(role), sql.Literal(password)))
        raw.execute(sql.SQL("GRANT USAGE ON SCHEMA evolution TO {}").format(sql.Identifier(role)))
        raw.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA evolution TO {}").format(sql.Identifier(role)))
        raw.execute(sql.SQL("GRANT EXECUTE ON FUNCTION evolution.tenant_scope(),evolution.purge_audit(text) TO {}").format(sql.Identifier(role)))
    repo = TenantRepository(owner.url.set(username=role, password=password).render_as_string(hide_password=False))
    yield repo
    repo.close()
    with owner.begin() as conn:
        conn.connection.driver_connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        conn.connection.driver_connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
    owner.dispose()


def decision(tenant):
    return {"decision_id": str(uuid4()), "request_id": str(uuid4()), "tenant_id": tenant, "workload_id": "chat",
            "stage": "input", "action": "block", "reason_codes": ["content_denied"],
            "transformed_parts": [{"text": "CANARY-RAW-PROMPT-MUST-NEVER-PERSIST"}],
            "findings": [{"matched_text": "CANARY-MATCH", "tool_arguments": {"secret": "CANARY-SECRET"}}]}


def test_concurrent_audit_hash_chain_is_complete_and_ordered():
    repo, tenant, store = setup_store()
    def append(_):
        with repo.transaction(tenant) as tx:
            return store.append(tx, "policy_submitted")["event_id"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(append, range(24)))
    assert len(set(ids)) == 24
    page = store.page(tenant, limit=100)
    assert len(page) == 24
    assert [row["sequence"] for row in page] == list(range(1, 25))
    assert store.verify_chain(tenant)
    with repo.transaction(tenant) as tx:
        assert tx.execute("SELECT sequence FROM evolution.audit_heads").scalar_one() == 24
    repo.close()


def test_decision_raw_content_stripped_and_allowlist_rejects_unknown_fields():
    repo, tenant, store = setup_store()
    value = decision(tenant)
    with repo.transaction(tenant) as tx:
        event = store.append_decision(tx, value)
    with repo.transaction(tenant) as tx:
        payload = tx.execute("SELECT canonical_bytes FROM evolution.records WHERE kind='audit'").scalar_one()
        assert b"CANARY" not in payload
        assert b"transformed_parts" not in payload and b"tool_arguments" not in payload
        assert event["raw_content_retained"] is False
    for metadata in ({"prompt": "CANARY"}, {"token": "CANARY"}, {"stage": {"text": "CANARY"}}):
        with pytest.raises(EvolutionError, match="Validation failed"):
            with repo.transaction(tenant) as tx:
                store.append(tx, "evaluation", metadata=metadata)
    with pytest.raises(EvolutionError, match="Validation failed"):
        with repo.transaction(tenant) as tx:
            store.append(tx, "evaluation", reason_codes=("CANARY",))
    assert len(store.page(tenant)) == 1
    assert store.verify_chain(tenant)
    repo.close()


def test_audit_idempotency_and_immutable_database_enforcement():
    repo, tenant, store = setup_store()
    event_id = str(uuid4())
    with repo.transaction(tenant) as tx:
        original = store.append(tx, "policy_submitted", event_id=event_id)
    with repo.transaction(tenant) as tx:
        repeated = store.append(tx, "policy_submitted", event_id=event_id)
    assert original == repeated
    with pytest.raises(EvolutionError, match="Idempotency conflict"):
        with repo.transaction(tenant) as tx:
            store.append(tx, "policy_approved", event_id=event_id)
    for statement in ("UPDATE evolution.records SET data='{}' WHERE kind='audit'", "DELETE FROM evolution.records WHERE kind='audit'"):
        with pytest.raises(DBAPIError):
            with repo.transaction(tenant) as tx:
                tx.execute(statement)
    assert len(store.page(tenant)) == 1
    assert store.verify_chain(tenant)
    repo.close()


def test_bounded_spool_is_safe_and_replay_deduplicates(tmp_path):
    repo, tenant, store = setup_store()
    path = tmp_path / "metadata.sqlite3"
    spool = MetadataSpool(path, max_events=1)
    safe = store.decision_metadata(decision(tenant))
    spool.enqueue(safe)
    spool.enqueue(safe)
    with pytest.raises(EvolutionError, match="Audit failed"):
        spool.enqueue(store.decision_metadata(decision(tenant)))
    with pytest.raises(EvolutionError, match="Validation failed"):
        spool.enqueue(dict(safe, prompt="CANARY"))
    assert b"CANARY" not in path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    # Simulate DB committed but process crashed before deleting the spool row.
    event = dict(safe)
    event.pop("tenant_id")
    with repo.transaction(tenant) as tx:
        store.append(tx, **event)
    assert spool.replay(store, tenant) == 1
    assert spool.replay(store, tenant) == 0
    assert len(store.page(tenant)) == 1 and store.verify_chain(tenant)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    repo.close()


def test_required_audit_failure_blocks_or_spools_metadata_only(tmp_path):
    repo, tenant, real = setup_store()
    class BrokenRepository:
        @contextmanager
        def transaction(self, _):
            raise OperationalError("unavailable", {}, Exception("offline"))
            yield
    store = AuditStore(BrokenRepository())
    with pytest.raises(EvolutionError, match="Audit failed"):
        store.persist_decision(decision(tenant))
    spool = MetadataSpool(tmp_path / "spool.sqlite3", max_events=1)
    assert store.persist_decision(decision(tenant), spool=spool) == "spooled"
    with pytest.raises(EvolutionError, match="Audit failed"):
        store.persist_decision(decision(tenant), spool=spool)
    assert b"CANARY" not in spool.path.read_bytes()
    assert spool.replay(real, tenant) == 1
    assert real.verify_chain(tenant)
    repo.close()


def test_retention_requires_signed_checkpoint_and_preserves_chain(retention_repository):
    repo, tenant, store = setup_store()
    with repo.transaction(tenant) as tx:
        older = store.append(tx, "policy_submitted", occurred_at="2025-01-01T00:00:00Z")
        newer = store.append(tx, "policy_approved", occurred_at="2026-10-10T00:00:00Z")
        tx.insert("legacy_audit", "import-provenance", {"source_event_id": older["event_id"]}, immutable=True)
        tx.link("legacy_audit", "import-provenance", "audit", older["event_id"])
    with pytest.raises(DBAPIError):
        with repo.transaction(tenant) as tx:
            tx.execute("SELECT evolution.purge_audit(:id)", {"id": str(uuid4())})
    key = Ed25519PrivateKey.generate()
    with pytest.raises(EvolutionError, match="Forbidden"):
        store.purge(tenant, before="2026-01-01T00:00:00Z", signer=key.sign, key_id="retention-test")
    # The serving database role cannot forge a checkpoint to gain deletion.
    with repo.transaction(tenant) as tx:
        assert not tx.execute("SELECT has_function_privilege(current_user,'evolution.purge_audit(text)','EXECUTE')").scalar_one()
    retained = AuditStore(repo, retention_repository=retention_repository)
    checkpoint = retained.purge(tenant, before="2026-01-01T00:00:00Z", signer=key.sign, key_id="retention-test")
    signed = dict(checkpoint)
    signature = base64.b64decode(signed.pop("signature"))
    key.public_key().verify(signature, canonical_bytes(signed))
    assert checkpoint["last_hash"] == older["event_hash"]
    assert checkpoint["last_sequence"] == 1
    with repo.transaction(tenant) as tx:
        assert tx.execute("SELECT count(*) FROM evolution.record_links WHERE target_kind='audit'").scalar_one() == 0
    remaining = store.page(tenant)
    assert len(remaining) == 1 and remaining[0]["event"]["event_id"] == newer["event_id"]
    assert store.verify_chain(tenant)
    with repo.transaction(tenant) as tx:
        store.append(tx, "release_applied")
        assert len(tx.list("checkpoints")) == 1
    assert store.verify_chain(tenant)
    repo.close()


def test_audit_connection_stall_cannot_exceed_response_budget(tmp_path):
    class SlowRepository:
        @contextmanager
        def transaction(self, _):
            time.sleep(0.15)
            raise OperationalError("unavailable", {}, Exception("offline"))
            yield
    store = AuditStore(SlowRepository())
    spool = MetadataSpool(tmp_path / "spool.sqlite3")
    started = time.monotonic()
    try:
        with pytest.raises(EvolutionError, match="Audit failed"):
            store.persist_decision(decision("audit-deadline"), spool=spool, budget_ms=10)
        assert time.monotonic() - started < 0.10
        with sqlite3.connect(spool.path) as conn:
            assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    finally:
        store.close()
