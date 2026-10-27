"""Original ASGI/pool with fake pipes only; no model, sockets or child process."""

import asyncio
import time

import pytest
from evolution import classifier_dispatch as evidence
from guardrails.evolution import classifier_contract as contract
from test_classifier_supervision import Process, install, invoke, request, scope

from services.classifier.app import (
    ATTEMPTS_HEADER,
    EPOCH_HEADER,
    MAX_DISPATCH_ATTEMPTS,
    ClassifierApplication,
    WorkerPool,
)


def observed(pool):
    return evidence.snapshot(pool.dispatch_headers())


async def ready(app, **changes):
    return await invoke(app, scope(method="GET", path="/v1/classifier/ready", **changes))


def test_authenticated_ready_headers_leave_json_identity_and_dispatch_unchanged(monkeypatch):
    async def run():
        process = Process()
        calls = install(monkeypatch, process)
        pool = WorkerPool("/model")
        await pool.start()
        try:
            app = ClassifierApplication(pool, bearer_token="x" * 40)
            before = observed(pool)
            sent = await ready(app)
            assert sent[0]["status"] == 200
            assert sent[1]["body"] == contract.canonical({"api_major": 1, "ready": True, "identity": contract.identity()})
            assert evidence.snapshot(sent[0]["headers"]) == before
            assert before["attempts"] == 0 and process.writes == [] and len(calls) == 1
            assert EPOCH_HEADER.decode() == evidence.EPOCH_HEADER
            assert ATTEMPTS_HEADER.decode() == evidence.ATTEMPTS_HEADER
        finally:
            await pool.close()
    asyncio.run(run())


@pytest.mark.parametrize("headers", [[], [(b"authorization", b"Bearer wrong")],
    [(b"authorization", b"Bearer " + b"x" * 40)] * 2])
@pytest.mark.parametrize("path", ["/v1/classifier/ready", "/v1/classifier/evaluate"])
def test_auth_refusal_exposes_no_counter_or_child_attempt(monkeypatch, headers, path):
    async def run():
        process = Process()
        install(monkeypatch, process)
        pool = WorkerPool("/model")
        await pool.start()
        try:
            before = observed(pool)
            sent = await invoke(ClassifierApplication(pool, bearer_token="x" * 40), scope(
                headers=headers, path=path, method="GET" if path.endswith("ready") else "POST"))
            assert sent[0]["status"] == 401 and process.writes == []
            assert evidence.delta(before, observed(pool)) == 0
            assert not {EPOCH_HEADER, ATTEMPTS_HEADER} & {key for key, _ in sent[0]["headers"]}
        finally:
            await pool.close()
    asyncio.run(run())


def test_original_pipe_write_attempts_increment_exactly_once_with_unchanged_payload(monkeypatch):
    async def run():
        process = Process()
        calls = install(monkeypatch, process)
        pool = WorkerPool("/model")
        await pool.start()
        try:
            before = observed(pool)
            for index in range(2):
                value = request()
                value["request_id"] = str(index) * 32
                deadline = time.monotonic() + 1
                result = await pool.evaluate(value, deadline)
                assert result["request_id"] == value["request_id"]
                assert process.writes[index] == contract.canonical({"request": value, "deadline": deadline}) + b"\n"
                assert evidence.delta(before, observed(pool)) == index + 1
            assert len(calls) == 1 and len(process.writes) == 2
        finally:
            await pool.close()
    asyncio.run(run())


def test_queued_cancel_and_overload_have_zero_attempts_before_original_reload(monkeypatch):
    async def run():
        process, replacement = Process(hang=True), Process()
        install(monkeypatch, process, replacement)
        pool = WorkerPool("/model", max_queued_jobs=1)
        await pool.start()
        jobs = []
        try:
            jobs.append(asyncio.create_task(pool.evaluate(request(), time.monotonic() + 2)))
            async with asyncio.timeout(1):
                while not process.writes:
                    await asyncio.sleep(0)
            baseline = observed(pool)
            jobs.append(asyncio.create_task(pool.evaluate(request(), time.monotonic() + 2)))
            await asyncio.sleep(0)
            with pytest.raises(contract.ClassifierError, match="queue_full"):
                await pool.evaluate(request(), time.monotonic() + 2)
            jobs[1].cancel()
            with pytest.raises(asyncio.CancelledError):
                await jobs[1]
            assert evidence.delta(baseline, observed(pool)) == 0
            assert len(process.writes) == 1 and process.kills == 0
            jobs[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await jobs[0]
            await asyncio.gather(*tuple(pool._cleanup))
            assert pool.ready() and observed(pool)["attempts"] == 0
            with pytest.raises(evidence.DispatchEvidenceError):
                evidence.delta(baseline, observed(pool))
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await pool.close()
    asyncio.run(run())


def test_failed_original_pipe_write_counts_attempt_before_safe_error_and_epoch_change(monkeypatch):
    async def run():
        process, replacement = Process(), Process()
        install(monkeypatch, process, replacement)
        pool = WorkerPool("/model")
        await pool.start()
        recorded = []
        def fail_write(raw):
            recorded.append(observed(pool))
            raise BrokenPipeError("synthetic-private-error")
        monkeypatch.setattr(process.stdin, "write", fail_write)
        try:
            before = observed(pool)
            with pytest.raises(contract.ClassifierError, match="evaluator_unavailable") as failed:
                await pool.evaluate(request(), time.monotonic() + 1)
            assert "synthetic-private-error" not in str(failed.value)
            assert len(recorded) == 1 and evidence.delta(before, recorded[0]) == 1
            await asyncio.gather(*tuple(pool._cleanup))
            assert pool.ready() and process.kills == process.reaps == 1
            with pytest.raises(evidence.DispatchEvidenceError):
                evidence.delta(before, observed(pool))
        finally:
            await pool.close()
    asyncio.run(run())


def test_idle_death_hides_headers_until_new_handshake_and_changes_zero_epoch(monkeypatch):
    async def run():
        process, replacement = Process(), Process()
        replacement.queue.get_nowait()
        calls = install(monkeypatch, process, replacement)
        pool = WorkerPool("/model")
        await pool.start()
        try:
            before = observed(pool)
            process.returncode = 7
            assert pool.dispatch_headers() == ()
            async with asyncio.timeout(1):
                while len(calls) != 2:
                    await asyncio.sleep(0)
            app = ClassifierApplication(pool, bearer_token="x" * 40)
            sent = await ready(app)
            assert sent[0]["status"] == 503 and pool.dispatch_headers() == ()
            assert not {EPOCH_HEADER, ATTEMPTS_HEADER} & {key for key, _ in sent[0]["headers"]}
            replacement.queue.put_nowait(contract.canonical({"api_major": 1, "ready": True, "identity": contract.identity()}) + b"\n")
            await asyncio.gather(*tuple(pool._cleanup))
            assert observed(pool)["attempts"] == before["attempts"] == 0
            with pytest.raises(evidence.DispatchEvidenceError):
                evidence.delta(before, observed(pool))
        finally:
            await pool.close()
        assert pool.dispatch_headers() == ()
    asyncio.run(run())


@pytest.mark.parametrize("body", [b"invalid-json", b"{\"private\":\"synthetic\"}"])
def test_bad_body_is_not_counted_or_echoed(monkeypatch, body):
    async def run():
        process = Process()
        install(monkeypatch, process)
        pool = WorkerPool("/model")
        await pool.start()
        try:
            before = observed(pool)
            sent = await invoke(ClassifierApplication(pool, bearer_token="x" * 40), scope(),
                                events=[{"type": "http.request", "body": body}])
            assert sent[0]["status"] == 400 and process.writes == []
            assert evidence.delta(before, observed(pool)) == 0
            assert b"synthetic" not in sent[1]["body"]
        finally:
            await pool.close()
    asyncio.run(run())


def test_exhausted_header_number_does_not_change_original_inference_or_ready_json(monkeypatch):
    async def run():
        process = Process()
        install(monkeypatch, process)
        pool = WorkerPool("/model")
        await pool.start()
        try:
            pool._dispatch_attempts = MAX_DISPATCH_ATTEMPTS
            assert observed(pool)["attempts"] == MAX_DISPATCH_ATTEMPTS
            assert (await pool.evaluate(request(), time.monotonic() + 1))["complete"] is True
            assert len(process.writes) == 1 and pool.dispatch_headers() == ()
            sent = await ready(ClassifierApplication(pool, bearer_token="x" * 40))
            assert sent[0]["status"] == 200
            assert sent[1]["body"] == contract.canonical({"api_major": 1, "ready": True, "identity": contract.identity()})
            with pytest.raises(evidence.DispatchEvidenceError):
                evidence.snapshot(sent[0]["headers"])
        finally:
            await pool.close()
    asyncio.run(run())


@pytest.mark.parametrize("count", [b"", b"-1", b"+1", b"00", b"01", b"1.0", b"1e1", b"true",
    b"1\n", b" 1", b"9223372036854775808", b"1" * 33, b"\xff"])
def test_numeric_headers_are_closed_canonical_and_bounded(count):
    with pytest.raises(evidence.DispatchEvidenceError):
        evidence.snapshot([(EPOCH_HEADER, b"a" * 32), (ATTEMPTS_HEADER, count)])


@pytest.mark.parametrize("kind", ["absent", "duplicate-epoch", "duplicate-count", "upper-epoch",
    "short-epoch", "private-epoch", "tuple-shape", "string-header", "too-many"])
def test_missing_or_ambiguous_headers_never_mean_zero(kind):
    rows = [(EPOCH_HEADER, b"a" * 32), (ATTEMPTS_HEADER, b"0")]
    if kind == "absent":
        rows.pop()
    elif kind == "duplicate-epoch":
        rows.append((EPOCH_HEADER.upper(), b"b" * 32))
    elif kind == "duplicate-count":
        rows.append((ATTEMPTS_HEADER.upper(), b"1"))
    elif kind in {"upper-epoch", "short-epoch", "private-epoch"}:
        rows[0] = (EPOCH_HEADER, {"upper-epoch": b"A" * 32, "short-epoch": b"a" * 31,
                                "private-epoch": b"synthetic-private"}[kind])
    elif kind == "tuple-shape":
        rows.append((b"foreign",))
    elif kind == "string-header":
        rows[0] = (EPOCH_HEADER.decode(), "a" * 32)
    else:
        rows.extend([(b"unused", b"private")] * 63)
    with pytest.raises(evidence.DispatchEvidenceError) as refused:
        evidence.snapshot(rows)
    assert str(refused.value) == "classifier_dispatch_observation_invalid"


@pytest.mark.parametrize("change", ["restart", "regression", "extra", "boolean", "negative", "overflow", "bad-epoch"])
def test_delta_refuses_invalid_brackets(change):
    before = {"epoch": "a" * 32, "attempts": 1}
    after = dict(before)
    if change == "restart":
        after["epoch"] = "b" * 32
    elif change == "regression":
        after["attempts"] = 0
    elif change == "extra":
        after["private"] = "synthetic"
    elif change == "bad-epoch":
        after["epoch"] = "private"
    else:
        after["attempts"] = {"boolean": True, "negative": -1, "overflow": 2**63}[change]
    with pytest.raises(evidence.DispatchEvidenceError):
        evidence.delta(before, after)


def test_safe_projector_discards_foreign_headers_and_distinguishes_zero_from_attempts():
    before = evidence.snapshot([(EPOCH_HEADER.upper(), b"a" * 32), (ATTEMPTS_HEADER, b"0"),
                                (b"set-cookie", b"synthetic-private")])
    assert before == {"epoch": "a" * 32, "attempts": 0}
    assert evidence.delta(before, before) == 0
    assert evidence.delta(before, {**before, "attempts": 2}) == 2
    assert "synthetic-private" not in repr(before)
