"""Pure observer checks: no model, network, container, or provider import."""
from __future__ import annotations

import contextvars
import hashlib
import json
import stat
import sys
import threading
import time
from types import ModuleType

import pytest

from connector import gateway_timing_probe as probe


@pytest.fixture
def observation(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "PATH", str(tmp_path / "capture"))
    monkeypatch.setattr(probe, "_RECORDER", None)
    monkeypatch.setattr(probe, "_ACTIVE", contextvars.ContextVar("test_observer", default=None))
    main = ModuleType("litellm.main")
    sdk = ModuleType("openai._base_client")
    main.completion = lambda *args, **kwargs: (args, kwargs)
    sdk.get_platform = lambda: "TestPlatform"
    monkeypatch.setitem(sys.modules, main.__name__, main)
    monkeypatch.setitem(sys.modules, sdk.__name__, sdk)
    yield main, sdk
    probe.close()


def read_frame():
    from pathlib import Path

    frame = Path(probe.PATH).read_bytes()
    assert len(frame) == probe.FRAME_BYTES
    header, rest = frame.split(b"\n", 1)
    magic, state, size, digest = header.split(b" ")
    assert magic == b"EVOGWT1"
    raw = rest[:int(size, 16)]
    assert hashlib.sha256(raw).hexdigest().encode() == digest
    assert not any(rest[len(raw):])
    value = json.loads(raw)
    assert state == {"ready": b"R", "active": b"A", "terminal": b"F"}[value["state"]]
    return value


def claim():
    token = probe.claim(probe.CORRELATION, (time.time_ns(), time.monotonic_ns()))
    probe.mark("request_validated")
    return token


def test_ready_frame_is_private_and_has_only_fixed_identity(observation):
    from pathlib import Path

    probe.initialize()
    value = read_frame()
    assert value["state"] == "ready" and value["clock"] is None
    assert value["operation"] == probe.OPERATION
    assert value["correlation_id"] == probe.CORRELATION
    assert value["hooks"] == {"completion": True, "platform": True}
    assert stat.S_IMODE(Path(probe.PATH).stat().st_mode) == 0o600
    assert all(x is None for x in value["marks_us"])


def test_other_correlation_never_claims_or_records(observation):
    probe.initialize()
    token = probe.claim("00000000-0000-0000-0000-000000000000", (1, 1))
    probe.mark("input_evaluate_enter")
    observation[0].completion("unretained-private-content")
    probe.finish("returned")
    probe.reset(token)
    assert read_frame()["state"] == "ready"
    assert not probe._RECORDER.claimed


def test_claim_is_once_and_second_request_cannot_amend_capture(observation):
    probe.initialize()
    token = claim()
    probe.finish("returned")
    probe.reset(token)
    before = read_frame()
    token = claim()
    probe.mark("platform_enter")
    probe.reset(token)
    assert read_frame() == before


def test_wrapper_preserves_identity_arguments_and_calls_once(observation):
    main, _ = observation
    result = object()
    argument = object()
    calls = []

    def original(value, *, named):
        """Original metadata."""
        calls.append((value, named))
        return result

    main.completion = original
    probe.initialize()
    token = claim()
    assert main.completion(argument, named=argument) is result
    assert calls == [(argument, argument)]
    assert main.completion.__wrapped__ is original
    assert main.completion.__name__ == original.__name__
    probe.finish("returned")
    probe.reset(token)
    value = read_frame()
    assert value["counts"][probe.PHASES.index("completion_enter")] == 1
    assert value["counts"][probe.PHASES.index("completion_return")] == 1


@pytest.mark.parametrize("error", [ValueError("private-message"), KeyboardInterrupt("private-message")])
def test_wrapper_preserves_same_exception_and_never_serializes_it(observation, error):
    main, _ = observation

    def original():
        raise error

    main.completion = original
    probe.initialize()
    token = claim()
    with pytest.raises(type(error)) as caught:
        main.completion()
    assert caught.value is error
    probe.finish("provider_error")
    probe.reset(token)
    value = read_frame()
    assert value["marks_us"][probe.PHASES.index("completion_error")] is not None
    assert "private-message" not in json.dumps(value)


def test_context_copy_records_worker_and_retains_late_return(observation):
    main, _ = observation
    entered, release = threading.Event(), threading.Event()
    result, actual = object(), []

    def original():
        entered.set()
        assert release.wait(2)
        return result

    main.completion = original
    probe.initialize()
    token = claim()
    context = contextvars.copy_context()
    worker = threading.Thread(target=lambda: actual.append(context.run(main.completion)))
    worker.start()
    try:
        assert entered.wait(2)
        probe.terminate("disconnect", "disconnect")
        ended = read_frame()["terminal_us"]
        probe.finish("returned")
        probe.reset(token)
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and actual == [result]
    value = read_frame()
    index = probe.PHASES.index("completion_return")
    assert value["late_marks"][index] is True
    assert value["terminal"] == "disconnect" and value["terminal_us"] == ended
    assert value["native_thread_ids"][index] != threading.get_native_id()


def test_platform_wrapper_uses_selected_context_and_preserves_value(observation):
    _, sdk = observation
    probe.initialize()
    token = claim()
    assert contextvars.copy_context().run(sdk.get_platform) == "TestPlatform"
    probe.finish("returned")
    probe.reset(token)
    value = read_frame()
    assert value["counts"][probe.PHASES.index("platform_enter")] == 1
    assert value["counts"][probe.PHASES.index("platform_return")] == 1


def test_lock_contention_is_nonblocking_and_reported(observation):
    probe.initialize()
    token = claim()
    recorder = probe._RECORDER
    assert recorder.lock.acquire(blocking=False)
    try:
        probe.mark("input_evaluate_return")
        assert recorder.loss is True
    finally:
        recorder.lock.release()
    probe.finish("returned")
    probe.reset(token)
    value = read_frame()
    assert value["observation_loss"] is True
    assert value["marks_us"][probe.PHASES.index("input_evaluate_return")] is None


def test_existing_capture_is_never_overwritten(observation):
    from pathlib import Path

    Path(probe.PATH).write_bytes(b"previous evidence")
    probe.initialize()
    assert Path(probe.PATH).read_bytes() == b"previous evidence"
    assert probe._RECORDER.fd is None


def test_symlink_is_refused_without_changing_target(observation, tmp_path):
    from pathlib import Path

    target = tmp_path / "target"
    target.write_bytes(b"unchanged")
    Path(probe.PATH).symlink_to(target)
    probe.initialize()
    assert target.read_bytes() == b"unchanged"
    assert probe._RECORDER.fd is None


def test_all_phases_and_duplicates_stay_in_one_bounded_frame(observation):
    probe.initialize()
    token = claim()
    for phase in probe.PHASES:
        for _ in range(3):
            probe.mark(phase)
    probe.finish("returned")
    probe.reset(token)
    value = read_frame()
    assert all(x is not None for x in value["marks_us"])
    assert probe._RECORDER.writes < 64
    assert value["observation_loss"] is False


def test_initialize_does_not_force_missing_dependency_imports(observation, monkeypatch):
    import builtins

    monkeypatch.delitem(sys.modules, "litellm.main")
    monkeypatch.delitem(sys.modules, "openai._base_client")
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith(("litellm", "openai"))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    probe.initialize()
    assert read_frame()["hooks"] == {"completion": False, "platform": False}


def test_observation_write_failure_does_not_replace_provider_result(observation, monkeypatch):
    main, _ = observation
    expected = object()
    main.completion = lambda: expected
    probe.initialize()
    token = claim()

    def fail(*args):
        raise OSError("unretained-private-path")

    monkeypatch.setattr(probe.os, "pwrite", fail)
    assert main.completion() is expected
    assert probe._RECORDER.loss is True
    probe.finish("returned")
    probe.reset(token)


def test_replaced_path_is_not_written(observation, tmp_path):
    from pathlib import Path

    probe.initialize()
    token = claim()
    original = Path(probe.PATH)
    moved = tmp_path / "retained"
    original.rename(moved)
    original.write_bytes(b"replacement")
    probe.mark("platform_enter")
    assert original.read_bytes() == b"replacement"
    assert probe._RECORDER.loss is True
    probe.reset(token)


def assembled_boundary(tmp_path, proxy, *, correlation=None, authenticated=True):
    """Use the actual boundary; only its proxy and ASGI ports are fake."""
    from types import SimpleNamespace
    from connector.evolution_gateway import GatewayBoundary

    secret = "test-only-" + "x" * 48
    path = tmp_path / "token"
    path.write_text(secret)
    path.chmod(0o600)
    settings = SimpleNamespace(gateway_token_file=str(path), model_alias="test-alias",
                               model_timeout=10.0, evaluation_timeout=1.5)
    boundary = GatewayBoundary(proxy, settings, callbacks=lambda: [object()])
    scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions",
             "headers": [(b"authorization", ("Bearer " + (secret if authenticated else "wrong")).encode()),
                         (b"content-type", b"application/json"),
                         (b"x-request-id", (correlation or probe.CORRELATION).encode())]}
    body = json.dumps({"model": "test-alias", "messages": [{"role": "user", "content": "synthetic"}]}).encode()
    return boundary, scope, body


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["cancel", "timeout"])
async def test_actual_boundary_cancellation_and_timeout_cleanup(observation, tmp_path, monkeypatch, cause):
    import asyncio
    from types import SimpleNamespace
    from connector import evolution_gateway as gateway
    from connector.evolution import current_turn

    entered, stopped, disconnect_stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def proxy(scope, receive, send):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    boundary, scope, body = assembled_boundary(tmp_path, proxy)
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body}
        try:
            await asyncio.Future()
        finally:
            disconnect_stopped.set()

    output = []

    async def send(message):
        output.append(message)

    if cause == "timeout":
        observed_budgets = []

        def fake_timeout(seconds):
            observed_budgets.append(seconds)
            return asyncio.timeout(0.02 if seconds == 13.5 else seconds)

        replacement = SimpleNamespace(**{name: getattr(asyncio, name) for name in
            ("CancelledError", "Event", "create_task", "wait", "FIRST_COMPLETED", "gather")}, timeout=fake_timeout)
        monkeypatch.setattr(gateway, "asyncio", replacement)

    probe.initialize()
    timing_sentinel, turn_sentinel = object(), object()

    async def invoke():
        timing_token = probe._ACTIVE.set(timing_sentinel)
        turn_token = current_turn.set(turn_sentinel)
        try:
            await boundary(scope, receive, send)
        finally:
            assert probe._ACTIVE.get() is timing_sentinel
            assert current_turn.get() is turn_sentinel
            probe._ACTIVE.reset(timing_token)
            current_turn.reset(turn_token)

    task = asyncio.create_task(invoke())
    await asyncio.wait_for(entered.wait(), 1)
    if cause == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert output == []
    else:
        await asyncio.wait_for(task, 1)
        assert output[0]["status"] == 504
        assert observed_budgets == [1.5, 13.5]
    assert stopped.is_set() and disconnect_stopped.is_set()
    value = read_frame()
    assert value["terminal"] == {"cancel": "cancelled", "timeout": "timeout"}[cause]
    assert value["marks_us"][probe.PHASES.index("boundary_finally")] is not None
    assert value["marks_us"][probe.PHASES.index("cancel_requested")] is not None


@pytest.mark.asyncio
async def test_actual_boundary_disconnect_preserves_late_worker(observation, tmp_path):
    import asyncio

    main, _ = observation
    started, release = threading.Event(), threading.Event()
    disconnect, proxy_entered, proxy_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    worker = None

    def original():
        started.set()
        assert release.wait(2)
        return "synthetic"

    main.completion = original
    probe.initialize()

    async def proxy(scope, receive, send):
        nonlocal worker
        context = contextvars.copy_context()
        worker = threading.Thread(target=lambda: context.run(main.completion))
        worker.start()
        proxy_entered.set()
        try:
            await asyncio.Future()
        finally:
            proxy_closed.set()

    boundary, scope, body = assembled_boundary(tmp_path, proxy)
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        pytest.fail("disconnect must not gain a response")

    task = asyncio.create_task(boundary(scope, receive, send))
    try:
        await asyncio.wait_for(proxy_entered.wait(), 1)
        assert started.wait(1)
        disconnect.set()
        await asyncio.wait_for(task, 1)
        value = read_frame()
        terminal_us = value["terminal_us"]
        assert value["terminal"] == "disconnect" and proxy_closed.is_set()
        assert value["marks_us"][probe.PHASES.index("completion_return")] is None
    finally:
        release.set()
        if worker is not None:
            worker.join(2)
    assert not worker.is_alive()
    value = read_frame()
    assert value["late_marks"][probe.PHASES.index("completion_return")]
    assert value["terminal_us"] == terminal_us and value["terminal"] == "disconnect"


@pytest.mark.asyncio
async def test_actual_boundary_overlapping_requests_have_one_capture(observation, tmp_path):
    import asyncio
    from connector.evolution import current_turn

    probe.initialize()
    ports = [asyncio.Event() for _ in range(3)]
    entered = [asyncio.Event() for _ in range(3)]
    captures = []

    async def run(index, correlation):
        async def proxy(scope, receive, send):
            captures.append((index, probe._ACTIVE.get()))
            entered[index].set()
            await ports[index].wait()
            probe.mark("input_accepted")
            await send({"type": "http.response.start", "status": 502})

        boundary, scope, body = assembled_boundary(tmp_path, proxy, correlation=correlation)
        consumed = False

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body}
            await asyncio.Future()

        async def send(message):
            pass

        sentinel = object()
        token = probe._ACTIVE.set(sentinel)
        turn_token = current_turn.set(sentinel)
        try:
            await boundary(scope, receive, send)
        finally:
            assert probe._ACTIVE.get() is sentinel and current_turn.get() is sentinel
            probe._ACTIVE.reset(token)
            current_turn.reset(turn_token)

    tasks = []
    for index, correlation in enumerate((probe.CORRELATION, "00000000-0000-0000-0000-000000000000", probe.CORRELATION)):
        tasks.append(asyncio.create_task(run(index, correlation)))
        await asyncio.wait_for(entered[index].wait(), 1)
    assert captures[0][1] is probe._RECORDER
    assert captures[1][1] is captures[2][1] is None
    ports[0].set()
    await asyncio.wait_for(tasks[0], 1)
    before = read_frame()
    ports[1].set()
    ports[2].set()
    await asyncio.wait_for(asyncio.gather(*tasks[1:]), 1)
    assert read_frame() == before


@pytest.mark.asyncio
async def test_unauthenticated_fixed_correlation_never_claims(observation, tmp_path):
    async def proxy(*args):
        pytest.fail("unauthenticated request reached proxy")

    boundary, scope, body = assembled_boundary(tmp_path, proxy, authenticated=False)
    probe.initialize()
    output = []

    async def receive():
        pytest.fail("unauthenticated request read body")

    async def send(message):
        output.append(message)

    sentinel = object()
    token = probe._ACTIVE.set(sentinel)
    try:
        await boundary(scope, receive, send)
        assert probe._ACTIVE.get() is sentinel
    finally:
        probe._ACTIVE.reset(token)
    assert output[0]["status"] == 401
    assert read_frame()["state"] == "ready"


@pytest.mark.asyncio
async def test_early_rejection_cannot_mark_an_existing_claim(observation, tmp_path):
    async def proxy(*args):
        pytest.fail("unauthenticated request reached proxy")

    boundary, scope, _ = assembled_boundary(tmp_path, proxy, authenticated=False)
    probe.initialize()
    token = claim()
    before = read_frame()

    async def receive():
        pytest.fail("unauthenticated request read body")

    async def send(message):
        pass

    try:
        await boundary(scope, receive, send)
        assert probe._ACTIVE.get() is probe._RECORDER
        assert read_frame() == before
    finally:
        probe.reset(token)


@pytest.mark.asyncio
async def test_transport_reservation_mark_cannot_delay_deadline_start(observation, monkeypatch):
    import httpx
    from types import SimpleNamespace
    from connector import evolution_gateway as gateway
    from connector.evolution import Turn, current_turn, normalize_messages

    order = []
    real_clock = time.monotonic
    messages = [{"role": "user", "content": "synthetic"}]
    parts = normalize_messages(messages, "input")
    approved = SimpleNamespace(provider=SimpleNamespace(model_id="test-model"), destination=object(), external_processing=False)

    def clock():
        order.append("deadline")
        return real_clock()

    def mark(phase):
        if phase == "transport_reserved":
            assert order == ["deadline"]
            order.append(phase)

    class FakeTransport:
        def validate_url(self, route, url):
            return "/v1/chat/completions"

        async def request(self, route, **kwargs):
            assert order == ["deadline", "transport_reserved"]
            assert kwargs["deadline_monotonic"] > real_clock()
            return SimpleNamespace(status_code=200, headers={}, content=b"synthetic")

    transport = object.__new__(gateway.ModelTransport)
    transport.settings = SimpleNamespace(model_base_url="https://model.test/v1", profile="deterministic_mock", model_timeout=10.0)
    transport.manifest = SimpleNamespace(select=lambda *_: approved)
    transport.transport = FakeTransport()
    turn = Turn(input_decision=SimpleNamespace(action="allow"), input_parts=parts, route_verified=True)
    token = current_turn.set(turn)
    monkeypatch.setattr(gateway, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(gateway.timing_probe, "mark", mark)
    try:
        response = await transport.handle_async_request(httpx.Request("POST", "https://model.test/v1/chat/completions",
            json={"model": "test-model", "messages": messages}))
        assert response.status_code == 200 and turn.model_dispatched
    finally:
        current_turn.reset(token)
