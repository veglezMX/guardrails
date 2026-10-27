"""Real PostgreSQL clock acquisition latency must not undercharge live work."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from guardrails.evolution.deadlines import current_deadline
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.quotas import QuotaExceeded, QuotaService
from guardrails.evolution.storage import TenantRepository
from test_quotas import migration as migration
from test_quotas import quota, setup_scope, state


pytestmark = pytest.mark.usefixtures("migration")


class ObservedClockQuota(QuotaService):
    """Keep the real DB UTC result; inject only one bounded server-side delay."""

    def __init__(self, repository, *, delay=0, **kwargs):
        super().__init__(repository, **kwargs)
        self.delay = delay
        self.sample = None
        self.after_delay = None

    def _now(self, tx):
        now = super()._now(tx)
        if self.sample is None:
            self.sample = now
            if self.delay:
                tx.execute("SELECT pg_sleep(:delay)", {"delay": self.delay})
            self.after_delay = time.monotonic()
        return now


@pytest.fixture
def repositories():
    path = os.environ.get("EVOLUTION_TEST_DSN_FILE")
    if not path:
        pytest.fail("Real PostgreSQL non-owner DSN file required")
    dsn = Path(path).read_text().strip()
    first = TenantRepository(dsn)
    second = TenantRepository(dsn)
    try:
        actor = setup_scope(first)
        yield first, second, actor
    finally:
        second.close()
        first.close()


def reserve(repository, service, actor, request_id, deadline):
    limit = quota(actor, inflight=1)
    token = current_deadline.set(deadline)
    try:
        with repository.transaction(actor["tenant_id"]) as tx:
            tx.lock("governance-admission")
            return service.reserve(
                tx,
                principal=actor,
                workload_id="chat",
                request_id=request_id,
                policy_digest="a" * 64,
                stage="input",
                tenant_quota=limit,
                workload_quota=limit,
                deadline_monotonic=deadline,
            )
    finally:
        current_deadline.reset(token)


def test_delayed_db_clock_keeps_inflight_charge_before_original_deadline(repositories):
    first, second, actor = repositories
    delayed = ObservedClockQuota(first, delay=0.2)
    competitor = ObservedClockQuota(second)
    request_id = str(uuid4())
    competing_id = str(uuid4())
    deadline = time.monotonic() + 0.5
    reservation = reserve(first, delayed, actor, request_id, deadline)
    before = state(first, actor)
    # The former implementation paired the sampled UTC with a remaining budget
    # calculated even later than after_delay. Its expiry cannot exceed this bound.
    old_expiry_upper = delayed.sample + timedelta(seconds=deadline - delayed.after_delay)

    def compete():
        # Leave the first owner unreleased, just as work between admission and
        # finalization retains its handle. Use a different connection/service.
        time.sleep(max(0, deadline - 0.1 - time.monotonic()))
        with pytest.raises(QuotaExceeded) as error:
            reserve(second, competitor, actor, competing_id, time.monotonic() + 0.5)
        return error.value

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            rejected = pool.submit(compete).result(timeout=2)
        assert time.monotonic() < deadline
        assert competitor.sample > old_expiry_upper
        assert competitor.sample < reservation.expires_at
        assert rejected.code == "quota_exceeded"
        assert state(first, actor) == before
        assert delayed.release(reservation)
        admitted = reserve(second, competitor, actor, competing_id, time.monotonic() + 0.5)
        assert admitted.request_id == competing_id
        assert competitor.release(admitted)
    finally:
        delayed.release(reservation)


def test_db_clock_delay_past_request_deadline_rolls_back_without_dispatch(repositories):
    first, _, actor = repositories
    delayed = ObservedClockQuota(first, delay=0.8)
    dispatched = []
    started = time.monotonic()
    with pytest.raises(EvolutionError) as error:
        reservation = reserve(first, delayed, actor, str(uuid4()), started + 0.5)
        dispatched.append(reservation.lease_id)
    assert error.value.code == "upstream_timeout"
    assert time.monotonic() - started < 1.0
    assert dispatched == []
    assert state(first, actor) == ([], [])


def test_delayed_db_clock_preserves_short_lease_cap_and_abandoned_reconciliation(repositories):
    first, second, actor = repositories
    capped = ObservedClockQuota(first, delay=0.025, lease_max_seconds=0.12)
    deadline = time.monotonic() + 0.5
    abandoned = reserve(first, capped, actor, str(uuid4()), deadline)
    assert (abandoned.expires_at - capped.sample).total_seconds() <= 0.12
    time.sleep(0.15)
    service = QuotaService(second)
    replacement = reserve(second, service, actor, str(uuid4()), time.monotonic() + 0.5)
    try:
        assert replacement.lease_id != abandoned.lease_id
        assert service.reconcile(actor["tenant_id"]) == 1
        leases = state(first, actor)[1]
        states = {row["lease_id"]: row["state"] for row in leases}
        assert states[abandoned.lease_id] == "expired"
        assert states[replacement.lease_id] == "active"
        assert service.release(abandoned) is False
    finally:
        service.release(replacement)
