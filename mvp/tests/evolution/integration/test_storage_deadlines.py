"""Request budgets cancel PostgreSQL waits and prohibit a late transaction commit."""
import os
from pathlib import Path
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from guardrails.evolution.deadlines import current_deadline
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.storage import TenantRepository


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
            with pytest.raises(EvolutionError) as failure:
                with repo.transaction(tenant) as waiter:
                    waiter.insert("test_rows", "rolled-back", {"value": "synthetic"})
                    waiter.lock("blocked")
            assert failure.value.code == "upstream_timeout"
            assert time.monotonic() - started < 1
        finally:
            current_deadline.reset(token)
    with repo.transaction(tenant) as tx:
        assert tx.get("test_rows", "rolled-back") is None
        assert tx.execute("SHOW statement_timeout").scalar_one() == "0"


def test_expired_request_cannot_commit_successful_earlier_statement(system):
    repo, tenant, _ = system
    token = current_deadline.set(time.monotonic() + 0.1)
    try:
        with pytest.raises(EvolutionError) as failure:
            with repo.transaction(tenant) as tx:
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
                with pytest.raises(EvolutionError) as failure:
                    with repo.transaction(tenant):
                        pytest.fail("exhausted pool must not admit a transaction")
                assert failure.value.code == "upstream_timeout"
                assert time.monotonic() - started < 0.5
            finally:
                current_deadline.reset(token)
        assert engine.pool.timeout() == 0.05
    finally:
        repo.close()
