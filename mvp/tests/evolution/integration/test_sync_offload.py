"""Bounded real workers and TLS/PG regressions for runtime synchronization."""

import asyncio
import ssl
import threading
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar

import httpx
import pytest
from fastapi import FastAPI
from guardrails.evolution.deadlines import current_deadline
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.sync_api import SyncExecutor, mount_sync_routes
from test_runtime_receipts import sync_system  # noqa: F401 -- actual TLS/PG fixture


async def eventually(predicate, budget=3):
    deadline = time.monotonic() + budget
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("bounded worker condition was not reached")
        await asyncio.sleep(0.005)


def test_cancelled_sync_requests_retain_real_worker_and_queue_capacity():
    async def exercise():
        pool = SyncExecutor(workers=2, max_pending=4)
        release = threading.Event()
        lock = threading.Lock()
        active = peak = started = 0

        def blocked():
            nonlocal active, peak, started
            with lock:
                active += 1
                started += 1
                peak = max(peak, active)
            try:
                if not release.wait(3):
                    raise RuntimeError("test_worker_release_missing")
            finally:
                with lock:
                    active -= 1
            return "finished"

        tasks = []
        try:
            tasks = [asyncio.create_task(pool.run(blocked)) for _ in range(4)]
            await eventually(lambda: started == 2 and pool.pending == 4)
            assert pool._queue.qsize() == 2
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(value, asyncio.CancelledError) for value in results)
            for _ in range(20):
                with pytest.raises(EvolutionError) as full:
                    await pool.run(lambda: None)
                assert full.value.code == "queue_full"
            assert peak == active == started == 2
            assert pool.pending == 4 and pool._queue.qsize() == 2
            release.set()
            await eventually(lambda: pool.pending == 0)
            assert started == 2  # Cancelled queued operations never execute.
            assert await pool.run(lambda: "recovered") == "recovered"
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await pool.close()
        assert not any(thread.is_alive() for thread in pool._threads)
        with pytest.raises(EvolutionError) as closed:
            await pool.run(lambda: None)
        assert closed.value.code == "policy_unresolved"

    asyncio.run(exercise())


def test_sync_worker_copies_context_and_expired_queued_job_never_executes():
    marker = ContextVar("sync_test_marker", default="outside")

    async def exercise():
        pool = SyncExecutor(workers=1, max_pending=2)
        release, started = threading.Event(), threading.Event()
        called = []

        def blocked():
            started.set()
            if not release.wait(3):
                raise RuntimeError("test_worker_release_missing")

        first = asyncio.create_task(pool.run(blocked))
        try:
            await eventually(started.is_set)
            deadline = time.monotonic() + 0.05
            token = current_deadline.set(deadline)
            try:
                with pytest.raises(EvolutionError) as expired:
                    await pool.run(lambda: called.append("unexpected"))
                assert expired.value.code == "upstream_timeout"
            finally:
                current_deadline.reset(token)
            assert pool.pending == 2 and called == []
            release.set()
            await first
            await eventually(lambda: pool.pending == 0)
            token, scope = (
                current_deadline.set(time.monotonic() + 1),
                marker.set("inside"),
            )
            try:
                expected = current_deadline.get()
                copied = await pool.run(
                    lambda: (
                        marker.get(),
                        current_deadline.get(),
                        threading.get_ident(),
                    )
                )
            finally:
                marker.reset(scope)
                current_deadline.reset(token)
            assert copied[:2] == ("inside", expected)
            assert copied[2] != threading.get_ident()
            assert marker.get() == "outside" and current_deadline.get() is None
            assert called == []
        finally:
            release.set()
            await asyncio.gather(first, return_exceptions=True)
            await pool.close()

    asyncio.run(exercise())


def test_asyncio_timeouts_cannot_release_running_or_cancelled_queued_sync_capacity():
    async def exercise():
        pool = SyncExecutor(workers=1, max_pending=2)
        release, started = threading.Event(), threading.Event()
        invoked = []

        def blocked():
            invoked.append(threading.get_ident())
            started.set()
            if not release.wait(3):
                raise RuntimeError("test_worker_release_missing")

        try:
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05):
                    await pool.run(blocked)
            assert started.is_set() and pool.pending == 1
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.05):
                    await pool.run(blocked)
            assert pool.pending == 2 and pool._queue.qsize() == 1
            for _ in range(10):
                with pytest.raises(EvolutionError) as full:
                    await pool.run(blocked)
                assert full.value.code == "queue_full"
            assert len(invoked) == 1
            release.set()
            await eventually(lambda: pool.pending == 0)
            assert len(invoked) == 1
        finally:
            release.set()
            await pool.close()
        assert not any(thread.is_alive() for thread in pool._threads)

    asyncio.run(exercise())


def test_sync_lifespan_preserves_state_and_drains_workers_before_prior_cleanup():
    events = []

    @asynccontextmanager
    async def original(app):
        events.append("start")
        try:
            yield {"existing_state": "preserved"}
        finally:
            events.append("finish")

    async def exercise():
        app = mount_sync_routes(FastAPI(lifespan=original), object(), object())
        pool = app.state.runtime_sync_executor
        release, started = threading.Event(), threading.Event()

        def blocked():
            started.set()
            if not release.wait(3):
                raise RuntimeError("test_worker_release_missing")

        task = None
        try:
            async with app.router.lifespan_context(app) as state:
                assert state == {"existing_state": "preserved"}
                task = asyncio.create_task(pool.run(blocked))
                await eventually(started.is_set)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

                async def unblock():
                    await asyncio.sleep(0.05)
                    assert events == ["start"] and pool.pending == 1
                    release.set()

                unblock_task = asyncio.create_task(unblock())
            await unblock_task
            assert events == ["start", "finish"]
            assert pool.pending == 0
            assert not any(thread.is_alive() for thread in pool._threads)
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await pool.close()

    asyncio.run(exercise())


def test_real_tls_blocked_sync_database_keeps_health_responsive_and_cannot_commit_late(
    sync_system,  # noqa: F811 -- imported shared pytest fixture
):
    system = sync_system
    repo, tenant = system["repo"], system["tenant"]
    endpoint = system["endpoint"]
    headers = {"Authorization": "Bearer " + system["tokens"]["runtime-a"]}

    async def exercise():
        async with httpx.AsyncClient(
            base_url=endpoint["url"],
            verify=ssl.create_default_context(cafile=endpoint["ca_file"]),
            trust_env=False,
            timeout=4,
        ) as client:
            with repo.transaction(tenant) as holder:
                before = len(holder.list("issued_snapshots"))
                holder.lock("governance-admission")
                pending = asyncio.create_task(
                    client.get("/v2/runtime/snapshot", headers=headers)
                )
                try:
                    # Observe an actual PostgreSQL advisory-lock wait, not an
                    # injected sleep or a fake sync dependency.
                    deadline = time.monotonic() + 1.5
                    while True:
                        with repo.transaction(tenant) as observer:
                            waiting = observer.execute(
                                "SELECT count(*) FROM pg_stat_activity WHERE usename=current_user AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND wait_event='advisory'"
                            ).scalar_one()
                        if waiting:
                            break
                        if time.monotonic() >= deadline:
                            raise AssertionError(
                                "sync transaction did not reach actual database lock"
                            )
                        await asyncio.sleep(0.01)
                    assert not pending.done()
                    started = time.monotonic()
                    alive = await client.get("/health/live", timeout=0.5)
                    assert alive.status_code == 200 and alive.json() == {
                        "status": "live"
                    }
                    assert time.monotonic() - started < 0.5
                    denied = await pending
                    assert denied.status_code == 504
                    assert denied.json()["error"]["code"] == "upstream_timeout"
                    assert len(holder.list("issued_snapshots")) == before
                finally:
                    if not pending.done():
                        pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
            # Release the lock after the HTTP deadline: cancelled work must not
            # resume and publish a late signed snapshot.
            await asyncio.sleep(0.1)
            with repo.transaction(tenant) as after:
                assert len(after.list("issued_snapshots")) == before
            recovered = await client.get("/v2/runtime/snapshot", headers=headers)
            assert recovered.status_code == 200
            with repo.transaction(tenant) as after:
                assert len(after.list("issued_snapshots")) == before + 1

    asyncio.run(exercise())


def test_real_tls_concurrent_sync_receipts_commit_once_and_reject_changed_replay(
    sync_system,  # noqa: F811 -- imported shared pytest fixture
):
    from uuid import uuid4

    from guardrails.evolution.runtime_apply import RuntimeSnapshotStore

    system = sync_system

    async def exercise():
        headers = {"Authorization": "Bearer " + system["tokens"]["runtime-a"]}
        async with httpx.AsyncClient(
            base_url=system["endpoint"]["url"],
            verify=ssl.create_default_context(cafile=system["endpoint"]["ca_file"]),
            trust_env=False,
            timeout=4,
        ) as client:
            issued = await client.get("/v2/runtime/snapshot", headers=headers)
            assert issued.status_code == 200
            store = RuntimeSnapshotStore(
                system["tmp_path"] / "concurrent.state",
                system["tenant"],
                system["runtimes"][0],
                {"test-key": system["key"].public_key()},
            )
            receipt = store.apply(issued.json())[0]
            headers["Idempotency-Key"] = str(uuid4())
            replies = await asyncio.gather(
                *(
                    client.post("/v2/runtime/receipts", headers=headers, json=receipt)
                    for _ in range(4)
                )
            )
            assert [response.status_code for response in replies] == [200] * 4
            assert all(response.json() == receipt for response in replies)
            conflict = await client.post(
                "/v2/runtime/receipts",
                headers=headers,
                json={**receipt, "digest": "0" * 64},
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "idempotency_conflict"
        with system["repo"].transaction(system["tenant"]) as tx:
            assert len(tx.list("receipts")) == 1
            assert len(tx.list("sync_idempotency")) == 1
            assert tx.get("applied_bindings", "chat") is None
            assert (
                tx.get("releases", system["release"]["release_id"])["status"]
                == "pending"
            )

    asyncio.run(exercise())
