"""Actual PostgreSQL lease ownership outlives the shorter admission transaction."""
import time
from uuid import uuid4

import pytest
from guardrails.evolution.deadlines import current_deadline
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.quotas import QuotaExceeded
from test_quota_clock_anchor import ObservedClockQuota, reserve
from test_quota_clock_anchor import repositories as repositories  # noqa: PLC0414 — pytest fixture export
from test_quotas import migration as migration  # noqa: PLC0414 — pytest fixture export
from test_quotas import quota, state

pytestmark = pytest.mark.usefixtures("migration")


def reserve_profile(repository, service, actor, request_id, admission, operation):
    limit = quota(actor, inflight=1)
    token = current_deadline.set(admission)
    try:
        with repository.transaction(actor["tenant_id"]) as tx:
            tx.lock("governance-admission")
            return service.reserve(tx, principal=actor, workload_id="chat",
                                   request_id=request_id, policy_digest="a" * 64,
                                   stage="input", tenant_quota=limit, workload_quota=limit,
                                   deadline_monotonic=operation)
    finally:
        current_deadline.reset(token)


def test_classifier_charge_remains_after_metadata_admission_deadline(repositories):
    first, second, actor = repositories
    owner, competitor = ObservedClockQuota(first), ObservedClockQuota(second)
    start = time.monotonic()
    admitted = reserve_profile(first, owner, actor, str(uuid4()), start + .5, start + 3)
    before = state(first, actor)
    try:
        time.sleep(max(0, start + .65 - time.monotonic()))
        assert time.monotonic() < start + 3
        with pytest.raises(QuotaExceeded):
            reserve(second, competitor, actor, str(uuid4()), time.monotonic() + .5)
        assert competitor.sample < admitted.expires_at
        assert state(first, actor) == before
        assert owner.release(admitted)
        next_owner = reserve(second, competitor, actor, str(uuid4()), time.monotonic() + .5)
        assert competitor.release(next_owner)
    finally:
        owner.release(admitted)


def test_classifier_profile_does_not_extend_admission_database_wait(repositories):
    first, _, actor = repositories
    delayed = ObservedClockQuota(first, delay=.8)
    start = time.monotonic()
    with pytest.raises(EvolutionError) as error:
        reserve_profile(first, delayed, actor, str(uuid4()), start + .5, start + 3)
    assert error.value.code == "upstream_timeout"
    assert time.monotonic() - start < 1
    assert state(first, actor) == ([], [])

