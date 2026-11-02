"""Request budgets cancel PostgreSQL waits and prohibit a late transaction commit."""
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from guardrails.evolution.deadlines import current_deadline
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.storage import TenantRepository
from sqlalchemy import create_engine


@pytest.fixture
def system():
    path = os.environ.get("EVOLUTION_TEST_DSN_FILE")
    if not path:
        pytest.fail("EVOLUTION_TEST_DSN_FILE required for PostgreSQL deadline checks")
    dsn = Path(path).read_text().strip()
    repo = TenantRepository(dsn)
    tenant = "deadline-" + uuid4().hex[:16]
    with repo.transaction(tenant) as tx:
        tx.insert("tenants", tenant, {"tenant_id": tenant, "status": "active"})
    yield repo, tenant, dsn
    repo.close()


def test_postgres_advisory_wait_obeys_request_deadline_and_resets_pool(system):
    repo, tenant, _ = system
    with repo.transaction(tenant) as holder:
        holder.lock("blocked")
        token = current_deadline.set(time.monotonic() + 0.15)
        started = time.monotonic()
        try:
            with pytest.raises(EvolutionError) as failure, repo.transaction(tenant) as waiter:
                waiter.insert("test_rows", "rolled-back", {"value": "synthetic"})
                waiter.lock("blocked")
            assert failure.value.code == "upstream_timeout"
            assert time.monotonic() - started < 1
        finally:
            current_deadline.reset(token)
    with repo.transaction(tenant) as tx:
        assert tx.get("test_rows", "rolled-back") is None
        assert tx.execute("SHOW statement_timeout").scalar_one() == "0"
        assert tx.execute("SHOW transaction_timeout").scalar_one() == "0"


def test_server_deadline_bounds_cumulative_statements_and_replaces_expired_connection(system):
    repo, tenant, _ = system
    token = current_deadline.set(time.monotonic() + 0.2)
    started = time.monotonic()
    try:
        with pytest.raises(EvolutionError) as failure, repo.transaction(tenant) as tx:
            tx.insert("test_rows", "cumulative", {"value": "synthetic"})
            # Each statement fits separately. Bypass the client-side check
            # to prove the server also bounds their cumulative duration.
            for _ in range(10):
                tx.connection.exec_driver_sql("SELECT pg_sleep(0.05)")
        assert failure.value.code == "upstream_timeout"
        assert time.monotonic() - started < 1
    finally:
        current_deadline.reset(token)
    with repo.transaction(tenant) as tx:
        assert tx.get("test_rows", "cumulative") is None
        assert tx.execute("SHOW statement_timeout").scalar_one() == "0"
        assert tx.execute("SHOW transaction_timeout").scalar_one() == "0"
        tx.insert("test_rows", "after-timeout", {"value": "synthetic"})


@pytest.mark.parametrize("savepoint", (False, True))
def test_shortened_deadline_still_bounds_database_wait_after_scope_change(system, savepoint):
    repo, tenant, _ = system
    token = current_deadline.set(time.monotonic() + 2)
    try:
        with pytest.raises(EvolutionError) as failure, repo.transaction(tenant) as tx:
            tx.insert("test_rows", "shortened", {"value": "synthetic"})
            if savepoint:
                nested = tx.connection.begin_nested()
                tx.execute("SELECT 1")
                nested.rollback()
            current_deadline.set(time.monotonic() + 0.1)
            started = time.monotonic()
            tx.execute("SELECT pg_sleep(1)")
        assert failure.value.code == "upstream_timeout"
        assert time.monotonic() - started < 0.5
    finally:
        current_deadline.reset(token)
    with repo.transaction(tenant) as tx:
        assert tx.get("test_rows", "shortened") is None
        assert tx.execute("SHOW transaction_timeout").scalar_one() == "0"


def test_expired_request_cannot_commit_successful_earlier_statement(system):
    repo, tenant, _ = system
    token = current_deadline.set(time.monotonic() + 0.1)
    try:
        with pytest.raises(EvolutionError) as failure, repo.transaction(tenant) as tx:
            tx.insert("test_rows", "late", {"value": "synthetic"})
            # Deterministic expiry after successful SQL but before __exit__.
            current_deadline.set(time.monotonic() - 1)
        assert failure.value.code == "upstream_timeout"
    finally:
        current_deadline.reset(token)
    with repo.transaction(tenant) as tx:
        assert tx.get("test_rows", "late") is None
        tx.insert("test_rows", "ordinary", {"value": "synthetic"})
    with repo.transaction(tenant) as tx:
        assert tx.get("test_rows", "ordinary")["value"] == "synthetic"


def test_pool_timeout_is_safe_and_external_engine_configuration_is_preserved(system):
    _, tenant, dsn = system
    engine = create_engine(dsn, pool_size=1, max_overflow=0, pool_timeout=0.05, hide_parameters=True)
    repo = TenantRepository(engine, verify_role=False)
    assert repo.engine is engine and engine.pool.timeout() == 0.05
    try:
        with engine.connect():
            token = current_deadline.set(time.monotonic() + 1)
            started = time.monotonic()
            try:
                with pytest.raises(EvolutionError) as failure, repo.transaction(tenant):
                    pytest.fail("exhausted pool must not admit a transaction")
                assert failure.value.code == "upstream_timeout"
                assert time.monotonic() - started < 0.5
            finally:
                current_deadline.reset(token)
        assert engine.pool.timeout() == 0.05
    finally:
        repo.close()
