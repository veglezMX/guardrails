"""Scheduled sync dephases replicas, survives outages, and stops on cancellation."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from guardrails.evolution import runtime_api
from guardrails.evolution.errors import EvolutionError


def simulated_poll(monkeypatch, durations, failures, draws):
    clock = [100.0]
    sleeps, starts, deadlines = [], [], []
    random_values = iter(draws)
    state = SimpleNamespace(_closed=False)
    lease = object()
    slot = SimpleNamespace(store=SimpleNamespace(snapshot=lease))

    def uniform(low, high):
        value = next(random_values)
        assert low <= value <= high
        return value

    def sync_once():
        index = len(starts) - 1
        clock[0] += durations[index]
        failure = failures[index]
        if failure:
            raise failure
        return {"status": "ready"}

    slot.client = SimpleNamespace(sync_once=sync_once)

    async def metadata(tenant, operation, deadline):
        assert tenant == "team-a"
        starts.append(clock[0])
        deadlines.append(deadline)
        return operation()

    async def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay
        if len(starts) == len(durations):
            state._closed = True

    state.metadata = metadata
    monkeypatch.setattr(runtime_api, "uniform", uniform)
    monkeypatch.setattr(
        runtime_api, "time", SimpleNamespace(monotonic=lambda: clock[0])
    )
    monkeypatch.setattr(runtime_api, "asyncio", SimpleNamespace(sleep=sleep))
    asyncio.run(runtime_api.EvaluationRuntime._poll(state, "team-a", slot))
    assert slot.store.snapshot is lease
    assert all(
        deadline - start == pytest.approx(3)
        for deadline, start in zip(deadlines, starts, strict=True)
    )
    return sleeps, starts


def test_poll_cadence_is_jittered_without_adding_operation_duration(monkeypatch):
    sleeps, starts = simulated_poll(
        monkeypatch, [0.2, 0.3, 0.1], [None, None, None], [0.4, 0.9, 1.1, 1.0]
    )
    assert starts[0] == pytest.approx(100.4)
    assert starts[1] - starts[0] == pytest.approx(0.9)
    assert starts[2] - starts[1] == pytest.approx(1.1)
    assert min(sleeps) > 0


def test_poll_outage_recovers_without_catch_up_burst_or_lease_extension(monkeypatch):
    sleeps, starts = simulated_poll(
        monkeypatch,
        [0.08, 3.0, 0.02],
        [
            httpx.ConnectError("synthetic outage"),
            EvolutionError("snapshot_stale"),
            None,
        ],
        [0.2, 0.9, 1.1, 0.075, 1.0],
    )
    assert len(starts) == 3
    assert starts[1] - starts[0] == pytest.approx(0.9)
    assert starts[2] - (starts[1] + 3.0) == pytest.approx(0.075)
    assert sleeps[2] >= 0.05


def test_tenant_slots_draw_independent_startup_phases(monkeypatch):
    async def exercise():
        delays, dispatched = [], []
        both_waiting = asyncio.Event()
        blocked = asyncio.Event()
        values = iter([0.1, 0.8])

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 2:
                both_waiting.set()
            await blocked.wait()

        async def metadata(tenant, operation, deadline):
            dispatched.append(tenant)

        monkeypatch.setattr(runtime_api, "uniform", lambda low, high: next(values))
        monkeypatch.setattr(runtime_api, "asyncio", SimpleNamespace(sleep=sleep))
        state = SimpleNamespace(_closed=False, metadata=metadata)
        slot = SimpleNamespace(client=SimpleNamespace(sync_once=lambda: None))
        tasks = [
            asyncio.create_task(
                runtime_api.EvaluationRuntime._poll(state, tenant, slot)
            )
            for tenant in ("team-a", "team-b")
        ]
        await asyncio.wait_for(both_waiting.wait(), 1)
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert delays == [0.1, 0.8]
        assert dispatched == []
        assert all(isinstance(value, asyncio.CancelledError) for value in results)

    asyncio.run(exercise())


def test_cancelling_inflight_poll_does_not_schedule_another_attempt(monkeypatch):
    async def exercise():
        started, stopped = asyncio.Event(), asyncio.Event()
        attempts, sleeps = [], []

        async def sleep(delay):
            sleeps.append(delay)

        async def metadata(tenant, operation, deadline):
            attempts.append(tenant)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr(runtime_api, "uniform", lambda low, high: low)
        monkeypatch.setattr(runtime_api, "asyncio", SimpleNamespace(sleep=sleep))
        state = SimpleNamespace(_closed=False, metadata=metadata)
        slot = SimpleNamespace(client=SimpleNamespace(sync_once=lambda: None))
        task = asyncio.create_task(
            runtime_api.EvaluationRuntime._poll(state, "team-a", slot)
        )
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        assert attempts == ["team-a"]
        assert sleeps == [0]

    asyncio.run(exercise())
