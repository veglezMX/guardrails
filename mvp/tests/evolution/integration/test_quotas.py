"""Real PostgreSQL quota races, isolation, idempotency and bounded cleanup."""

import hashlib
import importlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from guardrails.evolution.deadlines import current_deadline
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import Quota
from guardrails.evolution.quotas import QuotaExceeded, QuotaService
from guardrails.evolution.storage import TenantRepository, canonical_bytes
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 10, 6, 0, 0, tzinfo=UTC)

    def __call__(self):
        return self.value


@pytest.fixture(scope="module", autouse=True)
def migration():
    path = os.environ.get("EVOLUTION_TEST_OWNER_DSN_FILE")
    if not path:
        pytest.fail(
            "Real PostgreSQL owner DSN file required for quota forward migration"
        )
    engine = create_engine(Path(path).read_text().strip(), hide_parameters=True)
    module = importlib.import_module(
        "guardrails.evolution.migrations.versions.0003_quota_state"
    )
    try:
        with engine.begin() as conn:
            conn.connection.driver_connection.execute(module.SCHEMA_SQL)
            conn.connection.driver_connection.execute(module.SCHEMA_SQL)
    finally:
        engine.dispose()


def setup_scope(repo, tenant=None):
    tenant = tenant or "quota-" + uuid4().hex[:16]
    principal = {
        "tenant_id": tenant,
        "principal_id": str(uuid4()),
        "display_name": "Synthetic workload",
        "roles": ["workload"],
        "status": "active",
    }
    with repo.transaction(tenant) as tx:
        tx.insert("tenants", tenant, {"tenant_id": tenant, "status": "active"})
        tx.insert("principals", principal["principal_id"], principal)
        for workload in ("chat", "other"):
            tx.insert(
                "workloads",
                workload,
                {"tenant_id": tenant, "workload_id": workload, "status": "active"},
            )
    return principal


@pytest.fixture
def system():
    path = os.environ.get("EVOLUTION_TEST_DSN_FILE")
    if not path:
        pytest.fail("Real PostgreSQL non-owner DSN file required for quota integration")
    repo = TenantRepository(Path(path).read_text().strip())
    clock = Clock()
    principal = setup_scope(repo)
    quotas = QuotaService(repo, clock=clock)
    yield repo, quotas, clock, principal
    repo.close()


def quota(principal, *, workload_id=None, rpm=1000, burst=1000, inflight=20):
    return Quota(
        tenant_id=principal["tenant_id"],
        quota_id=str(uuid4()),
        version=1,
        workload_id=workload_id,
        requests_per_minute=rpm,
        burst=burst,
        max_inflight=inflight,
    )


def reserve(
    system,
    *,
    actor=None,
    workload="chat",
    request_id=None,
    tq=None,
    wq=None,
    budget=5,
    stage="input",
):
    repo, service, _, principal = system
    actor = actor or principal
    deadline = time.monotonic() + budget
    token = current_deadline.set(deadline)
    try:
        with repo.transaction(actor["tenant_id"]) as tx:
            tx.lock("governance-admission")
            return service.reserve(
                tx,
                principal=actor,
                workload_id=workload,
                request_id=request_id or str(uuid4()),
                policy_digest="a" * 64,
                stage=stage,
                tenant_quota=tq or quota(actor),
                workload_quota=wq or quota(actor, workload_id=workload),
                deadline_monotonic=deadline,
            )
    finally:
        current_deadline.reset(token)


def state(repo, principal):
    with repo.transaction(principal["tenant_id"]) as tx:
        return tx.list("quota_windows", limit=1000), tx.list("quota_leases", limit=1000)


def test_concurrent_replicas_admit_exact_inflight_bound(system):
    repo, service, _, actor = system
    limit = quota(actor, inflight=4)
    barrier = Barrier(16)

    def attempt(_):
        barrier.wait(timeout=5)
        try:
            # Separate service object, shared real database, no process-local lock.
            replica = QuotaService(repo, clock=system[2])
            return reserve((repo, replica, system[2], actor), tq=limit)
        except QuotaExceeded as error:
            return error

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(attempt, range(16)))
    admitted = [value for value in outcomes if not isinstance(value, QuotaExceeded)]
    assert len(admitted) == 4
    assert len({value.owner_token for value in admitted}) == 4
    assert all(
        1 <= value.retry_after_s <= 60
        for value in outcomes
        if isinstance(value, QuotaExceeded)
    )
    windows, leases = state(repo, actor)
    assert len(leases) == 4
    assert len(windows) == 4 and all(window["count"] == 4 for window in windows)
    for value in admitted:
        assert service.release(value)
    assert len(state(repo, actor)[1]) == 4


def test_tenant_and_workload_limits_both_apply_without_partial_charge(system):
    repo, service, _, actor = system
    tenant = quota(actor, inflight=2)
    workload = quota(actor, workload_id="chat", inflight=1)
    first = reserve(system, tq=tenant, wq=workload)
    before = state(repo, actor)
    with pytest.raises(QuotaExceeded):
        reserve(system, tq=tenant, wq=workload)
    assert state(repo, actor) == before
    second = reserve(system, workload="other", tq=tenant)
    before = state(repo, actor)
    with pytest.raises(QuotaExceeded):
        reserve(system, workload="other", tq=tenant)
    assert state(repo, actor) == before
    assert service.release(first) and service.release(second)
    assert reserve(system, tq=tenant, wq=workload).lease_id


@pytest.mark.parametrize("bound", ["minute", "second"])
def test_workload_rate_limit_does_not_charge_rejection_or_block_other_workload(
    system, bound
):
    repo, service, clock, actor = system
    limit = quota(
        actor,
        workload_id="chat",
        rpm=1 if bound == "minute" else 100,
        burst=1 if bound == "second" else 100,
    )
    first = reserve(system, wq=limit)
    assert service.release(first)
    before = state(repo, actor)
    with pytest.raises(QuotaExceeded) as error:
        reserve(system, wq=limit)
    assert error.value.retry_after_s == (60 if bound == "minute" else 1)
    assert state(repo, actor) == before
    other = reserve(system, workload="other")
    assert service.release(other)
    windows = state(repo, actor)[0]
    assert all(row["count"] == 2 for row in windows if row["scope"] == "tenant")
    assert all(
        row["count"] == 1 for row in windows if row["scope"].startswith("workload:")
    )
    clock.value += timedelta(seconds=60 if bound == "minute" else 1)
    assert reserve(system, wq=limit).lease_id


def test_second_minute_and_backwards_clock_windows_keep_request_counts(system):
    repo, service, clock, actor = system
    limits = quota(actor, rpm=2, burst=1)
    first = reserve(system, tq=limits)
    assert service.release(first)
    with pytest.raises(QuotaExceeded) as error:
        reserve(system, tq=limits)
    assert error.value.retry_after_s == 1
    clock.value += timedelta(seconds=1)
    second = reserve(system, tq=limits)
    assert service.release(second)
    with pytest.raises(QuotaExceeded) as error:
        reserve(system, tq=limits)
    assert error.value.retry_after_s == 59
    clock.value += timedelta(seconds=59)
    third = reserve(system, tq=limits)
    assert service.release(third)
    clock.value -= timedelta(minutes=1)
    before = state(repo, actor)
    with pytest.raises(QuotaExceeded) as error:
        reserve(system, tq=limits)
    assert error.value.retry_after_s == 60
    assert state(repo, actor) == before


def test_same_request_race_and_terminal_replays_never_reserve_twice(system):
    repo, service, clock, actor = system
    request_id = str(uuid4())
    barrier = Barrier(8)

    def attempt(_):
        barrier.wait(timeout=5)
        try:
            return reserve(system, request_id=request_id)
        except EvolutionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    winners = [value for value in outcomes if not isinstance(value, str)]
    assert len(winners) == 1 and outcomes.count("idempotency_conflict") == 7
    assert service.release(winners[0])
    clock.value += timedelta(minutes=2)
    before = state(repo, actor)
    for stage in ("input", "output"):
        with pytest.raises(EvolutionError) as error:
            reserve(system, request_id=request_id, stage=stage)
        assert error.value.code == "idempotency_conflict"
    assert state(repo, actor) == before
    assert all(window["count"] == 1 for window in before[0])
    assert (
        reserve(system, request_id=request_id, workload="other").lease_id
        != winners[0].lease_id
    )
    other_actor = {**actor, "principal_id": str(uuid4())}
    with repo.transaction(actor["tenant_id"]) as tx:
        tx.insert("principals", other_actor["principal_id"], other_actor)
    assert (
        reserve(system, actor=other_actor, request_id=request_id).lease_id
        != winners[0].lease_id
    )


def test_owner_handle_is_private_and_release_is_idempotent_under_race(system):
    repo, service, _, actor = system
    reservation = reserve(system)
    windows, leases = state(repo, actor)
    assert reservation.owner_token not in repr(reservation)
    assert reservation.owner_token.encode() not in canonical_bytes(leases)
    assert (
        leases[0]["owner_hash"]
        == hashlib.sha256(reservation.owner_token.encode()).hexdigest()
    )
    for forged in (
        replace(reservation, owner_token="forged"),
        replace(reservation, lease_id=str(uuid4())),
    ):
        with pytest.raises(EvolutionError) as error:
            service.release(forged)
        assert error.value.code == "forbidden"
    barrier = Barrier(4)

    def release(_):
        barrier.wait(timeout=5)
        return service.release(reservation)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(release, range(4)))
    assert results.count(True) == 1 and results.count(False) == 3
    assert state(repo, actor)[0] == windows
    assert state(repo, actor)[1][0]["state"] == "released"


def test_expired_leases_free_capacity_and_bounded_reconcile_commutes_with_release(
    system,
):
    repo, service, clock, actor = system
    leases = [reserve(system) for _ in range(4)]
    clock.value += timedelta(seconds=15)
    assert reserve(system, tq=quota(actor, inflight=1)).lease_id
    assert service.reconcile(actor["tenant_id"], bounded_batch=2) == 2
    assert sum(row["state"] == "active" for row in state(repo, actor)[1]) == 3
    barrier = Barrier(2)

    def cleanup():
        barrier.wait(timeout=5)
        return service.reconcile(actor["tenant_id"])

    def release():
        barrier.wait(timeout=5)
        return service.release(leases[-1])

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(cleanup), pool.submit(release)
        a.result(timeout=5)
        b.result(timeout=5)
    assert service.reconcile(actor["tenant_id"]) == 0
    rows = state(repo, actor)[1]
    assert sum(row["state"] == "expired" for row in rows) == 4
    assert sum(row["state"] == "active" for row in rows) == 1
    assert not service.release(leases[-1])
    with pytest.raises(EvolutionError) as error:
        reserve(system, request_id=leases[-1].request_id)
    assert error.value.code == "idempotency_conflict"


def test_tenant_saturation_and_held_lock_do_not_block_other_tenant(system):
    repo, _, _, actor = system
    other = setup_scope(repo)
    reserve(system, tq=quota(actor, inflight=1))
    with pytest.raises(QuotaExceeded):
        reserve(system, tq=quota(actor, inflight=1))
    with repo.transaction(actor["tenant_id"]) as holder:
        holder.lock("quota")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(reserve, system, actor=other, budget=1)
            admitted = future.result(timeout=2)
    assert admitted.tenant_id == other["tenant_id"]
    with repo.transaction(actor["tenant_id"]) as tx:
        rows = tx.execute(
            "SELECT tenant_id FROM evolution.records WHERE kind IN ('quota_windows','quota_leases')"
        ).all()
        assert {row.tenant_id for row in rows} == {actor["tenant_id"]}
        assert (
            tx.execute(
                "SELECT data FROM evolution.records WHERE tenant_id=:other AND kind='quota_leases'",
                {"other": other["tenant_id"]},
            ).all()
            == []
        )
        assert not tx.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).scalar_one()
    with pytest.raises(DBAPIError), repo.transaction(actor["tenant_id"]) as tx:
        tx.execute(
            "UPDATE evolution.records SET tenant_id=:other WHERE kind='quota_leases'",
            {"other": other["tenant_id"]},
        )


@pytest.mark.parametrize(
    "kind,status",
    [("tenants", "suspended"), ("workloads", "suspended"), ("principals", "revoked")],
)
def test_revoked_or_suspended_scope_cannot_reserve(system, kind, status):
    repo, _, _, actor = system
    key = {
        "tenants": actor["tenant_id"],
        "principals": actor["principal_id"],
        "workloads": "chat",
    }[kind]
    with repo.transaction(actor["tenant_id"]) as tx:
        old = tx.get(kind, key)
        tx.put(kind, key, {**old, "status": status}, expected_revision=old["revision"])
    with pytest.raises(EvolutionError) as error:
        reserve(system)
    assert error.value.code == "forbidden"
    assert state(repo, actor) == ([], [])


def test_deadline_held_lock_rolls_back_and_expired_request_still_releases(system):
    repo, service, _, actor = system
    with repo.transaction(actor["tenant_id"]) as holder:
        holder.lock("quota")
        started = time.monotonic()
        with pytest.raises(EvolutionError) as error:
            reserve(system, budget=0.15)
        assert error.value.code == "upstream_timeout"
        assert time.monotonic() - started < 0.8
    assert state(repo, actor) == ([], [])
    reservation = reserve(system)
    expired = time.monotonic() - 1
    token = current_deadline.set(expired)
    try:
        assert service.release(reservation, outcome="cancelled")
        assert current_deadline.get() == expired
    finally:
        current_deadline.reset(token)
    assert state(repo, actor)[1][0]["state"] == "released"


def test_expiry_after_reserve_cannot_commit_or_dispatch(system):
    repo, service, _, actor = system
    deadline = time.monotonic() + 2
    token = current_deadline.set(deadline)
    dispatched = []
    try:
        with pytest.raises(EvolutionError) as error:
            with repo.transaction(actor["tenant_id"]) as tx:
                tx.lock("governance-admission")
                service.reserve(
                    tx,
                    principal=actor,
                    workload_id="chat",
                    request_id=str(uuid4()),
                    policy_digest="a" * 64,
                    stage="input",
                    tenant_quota=quota(actor),
                    workload_quota=quota(actor),
                    deadline_monotonic=deadline,
                )
                current_deadline.set(time.monotonic() - 1)
            dispatched.append("must not dispatch")
        assert error.value.code == "upstream_timeout"
    finally:
        current_deadline.reset(token)
    assert dispatched == []
    assert state(repo, actor) == ([], [])


def test_nonowner_malformed_quota_state_is_rejected(system):
    repo, _, _, actor = system
    reservation = reserve(system)
    for statement in (
        "UPDATE evolution.records SET data=jsonb_set(data,'{count}','-1') WHERE kind='quota_windows'",
        "UPDATE evolution.records SET data=jsonb_set(data,'{state}','\"unknown\"') WHERE kind='quota_leases'",
        "UPDATE evolution.records SET data=data || '{\"raw_content\":\"synthetic-canary\"}'::jsonb WHERE kind='quota_leases'",
        "UPDATE evolution.records SET data=data-'expires_epoch' WHERE kind='quota_leases'",
        "UPDATE evolution.records SET data=jsonb_set(data,'{request_id}','\"wrong\"') WHERE kind='quota_leases'",
    ):
        with pytest.raises(DBAPIError), repo.transaction(actor["tenant_id"]) as tx:
            tx.execute(statement)
    windows, leases = state(repo, actor)
    assert len(windows) == 4 and all(row["count"] == 1 for row in windows)
    assert len(leases) == 1 and leases[0]["request_id"] == reservation.request_id
    assert b"synthetic-canary" not in canonical_bytes(leases)


def test_real_storage_connection_loss_denies_admission_without_mutations(system):
    repo, service, _, actor = system
    owner = create_engine(
        Path(os.environ["EVOLUTION_TEST_OWNER_DSN_FILE"]).read_text().strip(),
        hide_parameters=True,
    )
    deadline = time.monotonic() + 2
    token = current_deadline.set(deadline)
    dispatched = []
    try:
        with pytest.raises(EvolutionError) as error:
            with repo.transaction(actor["tenant_id"]) as tx:
                pid = tx.execute("SELECT pg_backend_pid()").scalar_one()
                with owner.begin() as admin:
                    from sqlalchemy import text

                    assert admin.execute(
                        text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                    ).scalar_one()
                service.reserve(
                    tx,
                    principal=actor,
                    workload_id="chat",
                    request_id=str(uuid4()),
                    policy_digest="a" * 64,
                    stage="input",
                    tenant_quota=quota(actor),
                    workload_quota=quota(actor),
                    deadline_monotonic=deadline,
                )
            dispatched.append("forbidden")
        assert error.value.code == "evaluator_unavailable"
        assert "terminat" not in str(error.value).lower()
    finally:
        current_deadline.reset(token)
        owner.dispose()
    assert dispatched == []
    assert state(repo, actor) == ([], [])


def test_cleanup_timeout_preserves_charged_lease_until_expiry(system):
    repo, _, clock, actor = system
    service = QuotaService(repo, clock=clock, cleanup_budget_seconds=0.1)
    reservation = reserve(system, tq=quota(actor, inflight=1))
    with repo.transaction(actor["tenant_id"]) as holder:
        holder.lock("quota")
        started = time.monotonic()
        with pytest.raises(EvolutionError) as error:
            service.release(reservation)
        assert error.value.code == "upstream_timeout"
        assert time.monotonic() - started < 0.8
    assert state(repo, actor)[1][0]["state"] == "active"
    with pytest.raises(QuotaExceeded):
        reserve(system, tq=quota(actor, inflight=1))
    clock.value += timedelta(seconds=11)
    assert reserve(system, tq=quota(actor, inflight=1)).lease_id
    assert service.reconcile(actor["tenant_id"]) == 1


def test_scope_mismatch_invalid_deadline_and_foreign_limits_fail_before_writes(system):
    repo, service, _, actor = system
    foreign = setup_scope(repo)
    with pytest.raises(EvolutionError) as error:
        reserve(system, tq=quota(foreign))
    assert error.value.code == "forbidden"
    with pytest.raises(EvolutionError) as error:
        reserve(system, wq=quota(actor, workload_id="other"))
    assert error.value.code == "forbidden"
    with repo.transaction(actor["tenant_id"]) as tx:
        with pytest.raises(EvolutionError) as error:
            service.reserve(
                tx,
                principal=actor,
                workload_id="chat",
                request_id=str(uuid4()),
                policy_digest="a" * 64,
                stage="input",
                tenant_quota=quota(actor),
                workload_quota=quota(actor),
                deadline_monotonic=time.monotonic() + 1,
            )
        assert error.value.code == "validation_failed"
    forged = {**actor, "principal_id": str(uuid4())}
    with pytest.raises(EvolutionError) as error:
        reserve(system, actor=forged)
    assert error.value.code == "forbidden"
    assert state(repo, actor) == ([], [])
