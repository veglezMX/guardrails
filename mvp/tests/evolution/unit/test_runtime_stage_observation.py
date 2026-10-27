"""Proposed observer proofs; staged unexecuted while the gate is frozen."""

import asyncio
import copy
import hashlib
import importlib.util
import json
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from evolution.registry import EvidenceError
from guardrails.evolution.runtime_api import EvaluationRuntime

_spec = importlib.util.spec_from_file_location(
    "runtime_stage_observation_under_test",
    Path(__file__).resolve().parents[1] / "integration/runtime_stage_observation.py",
)
observation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(observation)


def source_catalog(tmp_path):
    sources = {}
    for name in observation.SOURCES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def public_fixture():\n    return 1\n")
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return sources


def result(correlation, *, status=504, correct=False):
    return {"correlation_id": correlation, "replica": 0, "status": status, "correct": correct}


def experiment(correlation):
    valid = result(str(uuid4()), status=200, correct=True)
    baseline = {"b": [copy.copy(valid) for _ in range(150)]}
    contention = {"b": [result(correlation)] + [copy.copy(valid) for _ in range(149)], "a": [copy.copy(valid) for _ in range(240)]}
    measurement = {"schema_version": "evolution-noisy-neighbor-v3"}
    for name, rows in (("baseline_b", baseline["b"]), ("contention_b", contention["b"]), ("contention_a", contention["a"])):
        failed = [{"ordinal": i, "replica": r["replica"], "status": r["status"]} for i, r in enumerate(rows) if not r["correct"]]
        measurement[name] = {"scheduled": len(rows), "correct_terminal": len(rows) - len(failed), "failure_samples": {"rows": failed[:8]}}
    return baseline, contention, measurement


def record_auth(stages, correlation):
    index = stages.claim(correlation)
    number = stages.begin(index, "metadata")
    stages.job(index, number, phase=1, worker_start="now")
    stages.job(index, number, worker_end="now", worker_outcome=1)
    stages.job(index, number, await_end="now", await_outcome=1)
    stages.change(index, lambda row, now: (setattr(row, "status", 504), setattr(row, "finished", now)))
    return index


def validate(stages, correlation, tmp_path):
    baseline, contention, measurement = experiment(correlation)
    value = observation.project([stages, observation.SharedStages()], baseline, contention)
    observation.validate(value, case_id=observation.CASE, root=tmp_path, sources=source_catalog(tmp_path), measurement=measurement)
    return value, measurement


def test_real_fork_shared_timing_is_visible_without_retained_uuid(tmp_path):
    stages, correlation = observation.SharedStages(), str(uuid4())
    child = multiprocessing.get_context("fork").Process(target=record_auth, args=(stages, correlation))
    child.start()
    child.join(2)
    try:
        assert child.exitcode == 0
        value, _ = validate(stages, correlation, tmp_path)
        assert value["rows"][0]["observation"] == "complete"
        assert value["rows"][0]["jobs"][0]["phase"] == "authenticate"
        assert correlation not in json.dumps(value)
        assert hashlib.sha256(correlation.encode()).hexdigest() not in json.dumps(value)
    finally:
        if child.is_alive():
            child.kill()
            child.join(2)


def fixture_service(stages, authenticate, *, admit=None):
    # Use the actual production bounded executor method with inert operation ports.
    service = EvaluationRuntime.__new__(EvaluationRuntime)
    service._executors = {"tenant": ThreadPoolExecutor(max_workers=1)}
    service._pending = {"tenant": 0}
    service.config = SimpleNamespace(queue_limit=2)
    service.slots = {"tenant": None}
    service.identity = SimpleNamespace(authenticate=authenticate)
    service.quotas = SimpleNamespace(release=lambda value: value)
    async def detector(*args, **kwargs):
        return ()
    service.registry = SimpleNamespace(evaluate=detector)
    service._admit = admit if admit is not None else lambda *args: None
    service._finalize = lambda *args: None
    return observation.instrument(service, stages)


def test_real_metadata_cancel_keeps_late_worker_completion_distinct(tmp_path):
    stages, correlation = observation.SharedStages(), str(uuid4())
    entered, release = threading.Event(), threading.Event()
    count = []
    def authenticate(token):
        count.append(1)
        entered.set()
        if not release.wait(1):
            raise RuntimeError("fixture worker not released")
        return "ok"
    service = fixture_service(stages, authenticate, admit=authenticate)
    async def exercise():
        index = stages.claim(correlation)
        token = observation._REQUEST.set((stages, index))
        try:
            task = asyncio.create_task(service.metadata("tenant", lambda: service._admit("private-unused"), time.monotonic() + 1))
            async with asyncio.timeout(1):
                while not entered.is_set():
                    await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            first = stages.snapshot()[0][0].jobs[0]
            assert first.await_outcome == observation.OUTCOMES.index("cancelled")
            assert first.worker_end == 0 and first.worker_start > 0
            release.set()
            async with asyncio.timeout(1):
                while service._pending["tenant"]:
                    await asyncio.sleep(0)
            last = stages.snapshot()[0][0].jobs[0]
            assert last.worker_end >= last.await_end
            assert last.worker_outcome == observation.OUTCOMES.index("returned")
            assert last.await_outcome == observation.OUTCOMES.index("cancelled")
            assert count == [1]
            value, _ = validate(stages, correlation, tmp_path)
            assert value["rows"][0]["observation"] == "partial"  # No ASGI completion was fabricated.
            stages.change(index, lambda row, now: (setattr(row, "status", 504), setattr(row, "finished", now)))
            complete, _ = validate(stages, correlation, tmp_path)
            assert complete["rows"][0]["observation"] == "complete"
            assert [j["phase"] for j in complete["rows"][0]["jobs"]] == ["admit"]
            # A late returned callback was never delivered as a reservation;
            # production intentionally relies on expiry, not scheduled cleanup.
        finally:
            release.set()
            observation._REQUEST.reset(token)
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        service._executors["tenant"].shutdown(wait=True, cancel_futures=True)


def test_existing_header_associates_auth_without_reading_body_twice(tmp_path):
    stages, correlation = observation.SharedStages(), str(uuid4())
    calls, received, sent = [], [], []
    service = fixture_service(stages, lambda token: calls.append(token) or "ok")
    async def app(scope, receive, send):
        assert await service.metadata("tenant", lambda: service.identity.authenticate("secret-canary"), time.monotonic() + 1) == "ok"
        event = await receive()
        assert event == {"type": "http.request", "body": b"content-canary", "more_body": False}
        await send({"type": "http.response.start", "status": 504, "headers": []})
        await send({"type": "http.response.body", "body": b"error-canary"})
    async def exercise():
        async def receive():
            received.append(1)
            return {"type": "http.request", "body": b"content-canary", "more_body": False}
        async def send(message):
            sent.append(message)
        await observation.StageBoundary(app, stages)({"type": "http", "path": "/v2/evaluations", "headers": [(b"x-request-id", correlation.encode())]}, receive, send)
    try:
        asyncio.run(exercise())
        assert calls == ["secret-canary"] and received == [1]
        assert len(sent) == 2
        value, _ = validate(stages, correlation, tmp_path)
        assert value["rows"][0]["observation"] == "complete"
        assert all(v not in json.dumps(value) for v in (correlation, "secret-canary", "content-canary", "error-canary"))
    finally:
        service._executors["tenant"].shutdown(wait=True, cancel_futures=True)


def test_cancelled_queue_has_no_fabricated_worker_start_or_phase(tmp_path):
    stages, correlation = observation.SharedStages(), str(uuid4())
    release = threading.Event()
    called = []
    service = fixture_service(stages, lambda token: called.append(1))
    blocker = service._executors["tenant"].submit(release.wait, 1)
    async def exercise():
        index = stages.claim(correlation)
        token = observation._REQUEST.set((stages, index))
        try:
            task = asyncio.create_task(service.metadata("tenant", lambda: service.identity.authenticate("unused"), time.monotonic() + 1))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            value, _ = validate(stages, correlation, tmp_path)
            row = value["rows"][0]
            assert row["observation"] == "partial"
            assert row["jobs"][0]["phase"] == "unknown"
            assert row["jobs"][0]["worker_start_ms"] is None
            assert row["jobs"][0]["await_outcome"] == "cancelled"
            assert called == []
        finally:
            observation._REQUEST.reset(token)
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        blocker.result(timeout=2)
        service._executors["tenant"].shutdown(wait=True, cancel_futures=True)


def test_cleanup_after_response_retains_original_request_context(tmp_path):
    stages, correlation = observation.SharedStages(), str(uuid4())
    service = fixture_service(stages, lambda token: "ok")
    calls = []
    original_release = service.quotas.release
    def released(value):
        calls.append(value)
        return original_release(value)
    service.quotas.release = released
    async def exercise():
        release_cleanup = asyncio.Event()
        async def cleanup():
            await release_cleanup.wait()
            await service.metadata("tenant", lambda: service.quotas.release("owner-canary"), time.monotonic() + 1)
        async def app(scope, receive, send):
            await service.metadata("tenant", lambda: service._admit(), time.monotonic() + 1)
            task = asyncio.create_task(cleanup())
            await send({"type": "http.response.start", "status": 504, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return task
        async def unused_receive():
            raise AssertionError("observer must not receive")
        async def send(message):
            return None
        task = await observation.StageBoundary(app, stages)({"type": "http", "path": "/v2/evaluations", "headers": [(b"x-request-id", correlation.encode())]}, unused_receive, send)
        before, _ = validate(stages, correlation, tmp_path)
        assert before["rows"][0]["observation"] == "partial"
        release_cleanup.set()
        await task
        after, _ = validate(stages, correlation, tmp_path)
        row = after["rows"][0]
        assert row["observation"] == "complete"
        assert row["jobs"][-1]["phase"] == "cleanup"
        assert row["jobs"][-1]["submitted_ms"] >= row["response_ms"]
        assert calls == ["owner-canary"] and "owner-canary" not in json.dumps(after)
    try:
        asyncio.run(exercise())
    finally:
        service._executors["tenant"].shutdown(wait=True, cancel_futures=True)


def test_nonblocking_busy_update_reports_unavailable(tmp_path):
    stages, correlation = observation.SharedStages(), str(uuid4())
    index = record_auth(stages, correlation)
    stages.lock.acquire()
    try:
        started = time.monotonic()
        assert stages.begin(index, "metadata") is None
        assert time.monotonic() - started < 0.1
    finally:
        stages.lock.release()
    value, _ = validate(stages, correlation, tmp_path)
    assert value["rows"][0]["reason"] == "dropped_update"
    assert value["rows"][0]["jobs"] == []


def test_capacity_never_overwrites_first_original_request(tmp_path):
    stages, correlation = observation.SharedStages(), str(uuid4())
    record_auth(stages, correlation)
    for _ in range(observation.CAPACITY - 1):
        assert stages.claim(str(uuid4())) is not None
    assert stages.claim(str(uuid4())) is None
    assert stages.full.value == 1
    value, _ = validate(stages, correlation, tmp_path)
    assert value["rows"][0]["observation"] == "complete"


def test_duplicate_correlation_never_selects_a_convenient_attempt(tmp_path):
    stages, correlation = observation.SharedStages(), str(uuid4())
    record_auth(stages, correlation)
    record_auth(stages, correlation)
    value, _ = validate(stages, correlation, tmp_path)
    assert value["rows"][0]["reason"] == "duplicate"


@pytest.mark.parametrize("mutation", ["extra", "nan", "bool", "false_complete", "wrong_status", "wrong_omitted", "worker_before_submit", "observation_container", "reason_container", "phase_container"])
def test_closed_validator_rejects_unsafe_or_inconsistent_projection(tmp_path, mutation):
    stages, correlation = observation.SharedStages(), str(uuid4())
    record_auth(stages, correlation)
    value, measurement = validate(stages, correlation, tmp_path)
    row = value["rows"][0]
    if mutation == "extra":
        row["content-canary"] = "private-canary"
    elif mutation == "nan":
        row["response_ms"] = float("nan")
    elif mutation == "bool":
        row["jobs"][0]["submitted_ms"] = True
    elif mutation == "false_complete":
        row["jobs"][0]["worker_end_ms"] = None
        row["jobs"][0]["worker_outcome"] = "pending"
    elif mutation == "wrong_status":
        row["status"] = 200
    elif mutation == "wrong_omitted":
        value["omitted"]["contention_b"] = 1
    elif mutation == "observation_container":
        row["observation"] = []
    elif mutation == "reason_container":
        row.update(observation="unavailable", reason={}, response_ms=None, jobs=[])
    elif mutation == "phase_container":
        row["jobs"][0]["phase"] = {}
    else:
        row["jobs"][0]["submitted_ms"] = row["jobs"][0]["worker_start_ms"] + 1
    with pytest.raises(EvidenceError):
        observation.validate(value, case_id=observation.CASE, root=tmp_path, sources=source_catalog(tmp_path), measurement=measurement)


@pytest.mark.parametrize("change", ["missing_catalog", "changed_file", "wrong_case", "symlink_ancestor"])
def test_frozen_source_and_case_required_before_retention(tmp_path, change):
    stages, correlation = observation.SharedStages(), str(uuid4())
    record_auth(stages, correlation)
    value, measurement = validate(stages, correlation, tmp_path)
    sources = source_catalog(tmp_path)
    case = observation.CASE
    if change == "missing_catalog":
        sources.pop(observation.SOURCES[0])
    elif change == "changed_file":
        (tmp_path / observation.SOURCES[0]).write_text("def private_canary():\n    return 'private-canary'\n")
    elif change == "symlink_ancestor":
        (tmp_path / "mvp").rename(tmp_path / "actual")
        (tmp_path / "mvp").symlink_to(tmp_path / "actual", target_is_directory=True)
    else:
        case = "UNRELATED-CASE"
    with pytest.raises(EvidenceError):
        observation.validate(value, case_id=case, root=tmp_path, sources=sources, measurement=measurement)


@pytest.mark.parametrize("primary", [False, True])
def test_both_observers_attempted_and_first_operational_error_preserved(primary):
    original, first, second = RuntimeError("original"), ValueError("first"), KeyError("second")
    called = []
    def fail(error):
        called.append(error)
        raise error
    with pytest.raises(Exception) as caught:
        try:
            if primary:
                raise original
        finally:
            observation.record_all(lambda: fail(first), lambda: fail(second))
    assert caught.value is (original if primary else first)
    assert called == [first, second]


@pytest.mark.parametrize("method,terminal", [("begin", "returned"), ("job", "error"), ("mark", "cancelled"), ("classification", "returned"), ("classification", "error"), ("classification", "cancelled")])
def test_recorder_failure_preserves_original_metadata_result_error_and_cancel(tmp_path, monkeypatch, method, terminal):
    stages, correlation = observation.SharedStages(), str(uuid4())
    original = ValueError("original-private-canary")
    entered, release = threading.Event(), threading.Event()
    called = []
    def operation(token):
        called.append(1)
        entered.set()
        if terminal == "cancelled" and not release.wait(1):
            raise RuntimeError("fixture worker not released")
        if terminal == "error":
            raise original
        return "original-result"
    service = fixture_service(stages, operation)
    def broken(*args, **kwargs):
        raise RuntimeError("recorder-private-canary")
    if method == "classification":
        monkeypatch.setattr(observation, "_outcome", broken)
    else:
        setattr(stages, method, broken)
    async def exercise():
        index = stages.claim(correlation)
        token = observation._REQUEST.set((stages, index))
        try:
            task = asyncio.create_task(service.metadata("tenant", lambda: service.identity.authenticate("unused"), time.monotonic() + 1))
            if terminal == "returned":
                assert await task == "original-result"
            elif terminal == "error":
                with pytest.raises(ValueError) as caught:
                    await task
                assert caught.value is original
            else:
                async with asyncio.timeout(1):
                    while not entered.is_set():
                        await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                release.set()
                async with asyncio.timeout(1):
                    while service._pending["tenant"]:
                        await asyncio.sleep(0)
            assert called == [1]
            value, _ = validate(stages, correlation, tmp_path)
            assert value["rows"][0]["reason"] == "recorder_error"
            assert "private-canary" not in json.dumps(value)
        finally:
            release.set()
            observation._REQUEST.reset(token)
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        service._executors["tenant"].shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize("method", ["claim", "change"])
def test_boundary_recorder_failure_does_not_change_original_asgi_messages(tmp_path, method):
    stages, correlation = observation.SharedStages(), str(uuid4())
    sent, called = [], []
    expected = [{"type": "http.response.start", "status": 504, "headers": []},
                {"type": "http.response.body", "body": b"original-private-canary"}]
    def broken(*args, **kwargs):
        raise RuntimeError("observer-private-canary")
    setattr(stages, method, broken)
    async def app(scope, receive, send):
        called.append(1)
        for message in expected:
            await send(message)
        return "original-result"
    async def exercise():
        async def receive():
            raise AssertionError("observer must not read body")
        async def send(message):
            sent.append(message)
        result = await observation.StageBoundary(app, stages)(
            {"type": "http", "path": "/v2/evaluations", "headers": [(b"x-request-id", correlation.encode())]}, receive, send)
        assert result == "original-result"
    asyncio.run(exercise())
    assert called == [1] and sent == expected
    value, _ = validate(stages, correlation, tmp_path)
    assert value["rows"][0]["reason"] == "recorder_error"
    assert "private-canary" not in json.dumps(value)
