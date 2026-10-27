"""Real asyncio cancellation/admission with fake pipes; no subprocess or ML."""
import asyncio
import time

import pytest
from guardrails.evolution import classifier_contract as c

from services.classifier.app import ClassifierApplication, WorkerPool


def request():
    return {"api_major": 1, "identity": c.identity(), "request_id": "a" * 32, "budget_ms": 2900, "segments": [{"part_id": "p", "role": "system", "json_pointer": None, "text": "synthetic"}]}


def result(value):
    return {"api_major": 1, "identity": c.identity(), "request_id": value["request_id"], "complete": True, "windows": 1, "scores": [{"part_id": "p", "json_pointer": None, "score": 0.95, "windows": 1}]}


class Pipe:
    def __init__(self, process):
        self.process = process

    def write(self, raw):
        self.process.writes.append(raw)
        value = c.parse(raw[:-1])["request"]
        if not self.process.hang:
            self.process.queue.put_nowait(c.canonical(result(value)) + b"\n")

    async def drain(self):
        return None

    async def readline(self):
        return await self.process.queue.get()


class Process:
    def __init__(self, *, hang=False, ready=True):
        self.returncode = None
        self.hang = hang
        self.writes, self.kills, self.reaps = [], 0, 0
        self.queue = asyncio.Queue()
        self.queue.put_nowait(c.canonical({"api_major": 1, "ready": True, "identity": c.identity()}) + b"\n" if ready else b"wrong\n")
        self.stdin = self.stdout = Pipe(self)

    def kill(self):
        self.kills += 1
        self.returncode = -9
        self.queue.put_nowait(b"")

    async def communicate(self):
        self.reaps += 1
        return b"", b""


def install(monkeypatch, *processes):
    calls = []
    remaining = iter(processes)
    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return next(remaining)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return calls


def test_no_content_in_child_arguments_or_environment_and_exact_single_dispatch(monkeypatch):
    async def run():
        process = Process()
        calls = install(monkeypatch, process)
        pool = WorkerPool("/model")
        await pool.start()
        assert pool.ready()
        output = await pool.evaluate(request(), time.monotonic() + 1)
        assert output["complete"] and len(process.writes) == 1 and len(calls) == 1
        assert "synthetic" not in repr(calls)
        assert calls[0][1]["stderr"] is asyncio.subprocess.DEVNULL
        assert calls[0][1]["env"]["HF_HUB_OFFLINE"] == "1"
        assert calls[0][0][1:4] == ("-B", "-s", "-m")
        await pool.close()
        assert not pool.ready() and process.kills == 1 and process.reaps == 1
    asyncio.run(run())


def test_bad_startup_identity_never_ready_and_is_reaped(monkeypatch):
    async def run():
        process = Process(ready=False)
        install(monkeypatch, process)
        pool = WorkerPool("/model")
        with pytest.raises(c.ClassifierError):
            await pool.start()
        assert not pool.ready() and process.kills == 1 and process.reaps == 1
    asyncio.run(run())


@pytest.mark.parametrize("cause", ["timeout", "cancel"])
def test_inflight_timeout_or_cancel_kills_actual_worker_and_hides_readiness_during_reload(monkeypatch, cause):
    async def run():
        original, replacement = Process(hang=True), Process()
        replacement.queue.get_nowait()  # Hold the replacement's load handshake.
        calls = install(monkeypatch, original, replacement)
        pool = WorkerPool("/model")
        await pool.start()
        job = asyncio.create_task(pool.evaluate(request(), time.monotonic() + (0.01 if cause == "timeout" else 1)))
        while not original.writes:
            await asyncio.sleep(0)
        if cause == "cancel":
            job.cancel()
        with pytest.raises(c.ClassifierError if cause == "timeout" else asyncio.CancelledError):
            await job
        assert original.kills == 1 and not pool.ready() and pool._admitted == 0
        while len(calls) != 2:
            await asyncio.sleep(0)
        assert not pool.ready()
        replacement.queue.put_nowait(c.canonical({"api_major": 1, "ready": True, "identity": c.identity()}) + b"\n")
        await asyncio.gather(*tuple(pool._cleanup))
        assert pool.ready() and original.reaps == 1 and len(original.writes) == 1
        await pool.close()
    asyncio.run(run())


def test_queue_full_and_cancelled_waiter_do_not_consume_or_kill_busy_worker(monkeypatch):
    async def run():
        process, replacement = Process(hang=True), Process()
        install(monkeypatch, process, replacement)
        pool = WorkerPool("/model", max_queued_jobs=1)
        await pool.start()
        running = asyncio.create_task(pool.evaluate(request(), time.monotonic() + 1))
        while not process.writes:
            await asyncio.sleep(0)
        queued = asyncio.create_task(pool.evaluate(request(), time.monotonic() + 1))
        await asyncio.sleep(0)
        with pytest.raises(c.ClassifierError, match="queue_full"):
            await pool.evaluate(request(), time.monotonic() + 1)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert process.kills == 0 and len(process.writes) == 1 and pool._admitted == 1
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await pool.close()
    asyncio.run(run())


def test_idle_worker_death_immediately_removes_readiness_without_dispatch(monkeypatch):
    async def run():
        process = Process()
        install(monkeypatch, process)
        pool = WorkerPool("/model")
        await pool.start()
        process.returncode = 7
        assert not pool.ready()
        with pytest.raises(c.ClassifierError, match="evaluator_unavailable"):
            await pool.evaluate(request(), time.monotonic() + 1)
        assert process.writes == []
        await pool.close()
    asyncio.run(run())


def test_idle_dead_worker_is_replaced_once_and_reusable_after_verified_load(monkeypatch):
    async def run():
        original, replacement = Process(), Process()
        replacement.queue.get_nowait()
        calls = install(monkeypatch, original, replacement)
        pool = WorkerPool("/model")
        await pool.start()
        original.returncode = 7
        async with asyncio.timeout(1):
            while len(calls) != 2:
                await asyncio.sleep(0)
        assert not pool.ready() and original.writes == []
        with pytest.raises(c.ClassifierError, match="evaluator_unavailable"):
            await pool.evaluate(request(), time.monotonic() + 1)
        replacement.queue.put_nowait(c.canonical({"api_major": 1, "ready": True, "identity": c.identity()}) + b"\n")
        await asyncio.gather(*tuple(pool._cleanup))
        assert pool.ready() and original.reaps == 1
        assert (await pool.evaluate(request(), time.monotonic() + 1))["complete"]
        assert len(replacement.writes) == 1 and len(calls) == 2
        await pool.close()
        assert pool._monitor_task is None and not pool._cleanup and not pool._replacing
    asyncio.run(run())


def test_idle_monitor_cannot_duplicate_inflight_failure_replacement(monkeypatch):
    async def run():
        original, replacement = Process(hang=True), Process()
        calls = install(monkeypatch, original, replacement)
        pool = WorkerPool("/model")
        await pool.start()
        job = asyncio.create_task(pool.evaluate(request(), time.monotonic() + 1))
        while not original.writes:
            await asyncio.sleep(0)
        # The worker is dead but still owned by the waiting request, not idle.
        original.returncode = 7
        await asyncio.sleep(0.12)
        assert len(calls) == 1 and not pool._cleanup
        original.queue.put_nowait(b"")
        with pytest.raises(c.ClassifierError, match="evaluator_unavailable"):
            await job
        await asyncio.gather(*tuple(pool._cleanup))
        assert len(calls) == 2 and original.reaps == 1 and pool.ready()
        await pool.close()
    asyncio.run(run())


def test_close_cancels_pending_idle_replacement_load_and_reaps_it(monkeypatch):
    async def run():
        original, replacement = Process(), Process()
        replacement.queue.get_nowait()
        calls = install(monkeypatch, original, replacement)
        pool = WorkerPool("/model")
        await pool.start()
        original.returncode = 7
        async with asyncio.timeout(1):
            while len(calls) != 2:
                await asyncio.sleep(0)
        async with asyncio.timeout(1):
            await pool.close()
        assert not pool.ready() and pool._monitor_task is None
        assert not pool._cleanup and not pool._replacing
        assert replacement.kills == 1 and replacement.reaps >= 1 and len(calls) == 2
    asyncio.run(run())


class FakePool:
    max_queued_jobs = 0
    def __init__(self):
        self.calls = []
        self.block = None
        self.cancelled = False
    def ready(self):
        return True
    async def evaluate(self, value, deadline):
        self.calls.append((value, deadline))
        try:
            if self.block:
                await self.block.wait()
            return result(value)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def scope(**changes):
    return {"type": "http", "method": "POST", "path": "/v1/classifier/evaluate", "query_string": b"", "headers": [(b"authorization", b"Bearer " + b"x" * 40)], **changes}


async def invoke(app, incoming, *, events=None):
    queue = asyncio.Queue()
    for event in events or [{"type": "http.request", "body": c.canonical(request())}]:
        queue.put_nowait(event)
    sent = []
    async def send(value):
        sent.append(value)
    await app(incoming, queue.get, send)
    return sent


@pytest.mark.parametrize("headers", [[], [(b"authorization", b"Bearer wrong")], [(b"authorization", b"Bearer " + b"x" * 40)] * 2])
def test_http_auth_refuses_before_body_or_worker(headers):
    async def run():
        pool = FakePool()
        sent = await invoke(ClassifierApplication(pool, bearer_token="x" * 40), scope(headers=headers))
        assert sent[0]["status"] == 401 and pool.calls == []
    asyncio.run(run())


def test_http_body_and_queue_time_are_inside_arrival_budget():
    async def run():
        pool = FakePool()
        app = ClassifierApplication(pool, bearer_token="x" * 40)
        value = request()
        value["budget_ms"] = 10
        count, sent = 0, []
        async def receive():
            nonlocal count
            count += 1
            if count == 1:
                await asyncio.sleep(0.025)
                return {"type": "http.request", "body": c.canonical(value)}
            await asyncio.Event().wait()
        async def send(row):
            sent.append(row)
        await app(scope(), receive, send)
        assert sent[0]["status"] == 504 and pool.calls == [] and app._admitted == 0
    asyncio.run(run())


def test_http_disconnect_cancels_inference_and_releases_admission():
    async def run():
        pool = FakePool()
        pool.block = asyncio.Event()
        app = ClassifierApplication(pool, bearer_token="x" * 40)
        queue = asyncio.Queue()
        queue.put_nowait({"type": "http.request", "body": c.canonical(request())})
        async def send(_):
            pytest.fail("disconnected response")
        task = asyncio.create_task(app(scope(), queue.get, send))
        while not pool.calls:
            await asyncio.sleep(0)
        queue.put_nowait({"type": "http.disconnect"})
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.cancelled and app._admitted == 0
    asyncio.run(run())


def test_http_body_limit_refuses_without_worker_and_no_raw_echo():
    async def run():
        pool = FakePool()
        sent = await invoke(ClassifierApplication(pool, bearer_token="x" * 40), scope(), events=[{"type": "http.request", "body": b"private" + b"x" * c.MAX_WIRE_BYTES}])
        assert sent[0]["status"] == 413 and pool.calls == []
        assert b"private" not in sent[1]["body"]
    asyncio.run(run())


@pytest.mark.parametrize("role", [[], {}, None])
def test_http_unhashable_or_wrong_role_has_finite_error_without_dispatch(role):
    async def run():
        pool = FakePool()
        value = request()
        value["segments"][0]["role"] = role
        sent = await invoke(ClassifierApplication(pool, bearer_token="x" * 40), scope(), events=[{"type": "http.request", "body": c.canonical(value)}])
        assert sent[0]["status"] == 400 and pool.calls == []
        assert sent[1]["body"] == b'{"error":"validation_failed"}'

    asyncio.run(run())
