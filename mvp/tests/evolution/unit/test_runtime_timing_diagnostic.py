"""Temporary probe contract: unchanged operations, bounded content-free evidence."""
import asyncio
import hashlib
import json
import os
import time
from types import SimpleNamespace

import pytest
from guardrails.evolution import plugins, runtime_api
from guardrails.evolution.errors import EvolutionError

from mvp.tests.evolution.unit.test_deterministic_rules import rule
from mvp.tests.evolution.unit.test_runtime_execution_profiles import (
    exercise,
    fixture_runtime,
    payload,
)


@pytest.fixture
def capture(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins, "_TIMING_UID", os.getuid())
    monkeypatch.setattr(plugins, "_TIMING_DIRECTORY", str(tmp_path))
    value = plugins._TimingCapture()
    value.start()
    yield value
    value.close()


def frame(capture):
    raw = capture.mapping[:]
    assert len(raw) == 4096 and raw[:8] == b"EVOTIM1\n"
    size = int(raw[9:17], 16)
    assert 0 < size <= 2048
    encoded = raw[128:128 + size]
    assert hashlib.sha256(encoded).hexdigest().encode() == raw[17:81]
    assert encoded.isascii()
    return raw[8:9], json.loads(encoded)


def context(capture, **changes):
    now = time.monotonic_ns()
    identity = SimpleNamespace(tenant_id="team-a", workload_id="chat")
    request = SimpleNamespace(tenant_id="team-a", workload_id="chat", stage="input",
                              correlation_id=plugins._TIMING_CORRELATION, **changes)
    record = capture.claim([now, now, now], identity, request)
    assert record is not None
    return {"capture": capture, "record": record, "provisional": [now, now, now]}


def test_ready_frame_is_private_and_exclusive(capture):
    assert frame(capture)[0] == b"R"
    assert os.fstat(capture.fd).st_mode & 0o777 == 0o600
    before = capture.mapping[:]
    second = plugins._TimingCapture()
    with pytest.raises(FileExistsError):
        second.start()
    assert second.mapping is None and second.fd is None
    assert capture.mapping[:] == before


@pytest.mark.parametrize("field,value", [("tenant_id", "team-b"), ("workload_id", "other"),
                                          ("stage", "output"), ("correlation_id", "other")])
def test_unbound_requests_do_not_consume_capture(capture, field, value):
    identity = SimpleNamespace(tenant_id="team-a", workload_id="chat")
    request = SimpleNamespace(tenant_id="team-a", workload_id="chat", stage="input",
                              correlation_id=plugins._TIMING_CORRELATION)
    setattr(request, field, value)
    assert capture.claim([1, 2, 3], identity, request) is None
    assert not capture.claimed and frame(capture)[0] == b"R"


def test_foreign_identity_and_duplicate_request_cannot_claim(capture):
    request = SimpleNamespace(tenant_id="team-a", workload_id="chat", stage="input",
                              correlation_id=plugins._TIMING_CORRELATION)
    assert capture.claim([1, 2, 3], SimpleNamespace(tenant_id="team-b", workload_id="chat"), request) is None
    ctx = context(capture)
    assert capture.claim(ctx["provisional"], SimpleNamespace(tenant_id="team-a", workload_id="chat"), request) is None


@pytest.mark.parametrize("detector_error", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_real_http_runtime_preserves_decision_audit_and_quota(capture, monkeypatch, detector_error, enabled):
    selected = [rule(rule_id="phase3-pii", stages=["input"]), rule(stages=["input"], mandatory=True)]
    service, _, calls = fixture_runtime(rules=selected, state="observe")
    original = service.registry.evaluate

    async def detector(*args, **kwargs):
        await original(*args, **kwargs)
        if detector_error:
            raise EvolutionError("evaluation_timeout")
        return ()

    service.registry.evaluate = detector
    monkeypatch.setattr(runtime_api, "_TimingCapture", lambda: capture if enabled else SimpleNamespace(mapping=None))
    response = exercise(service, payload(correlation_id=plugins._TIMING_CORRELATION,
                                        parts=[{"part_id": "body", "kind": "text", "role": "user", "text": "PRIVATE_CANARY"}]))
    assert response.status_code == 200
    decision = response.json()
    assert decision["action"] == ("block" if detector_error else "allow")
    assert decision["reason_codes"] == (["evaluation_timeout"] if detector_error else [])
    assert len(calls.finalized) == len(calls.released) == 1
    assert len(calls.detectors) == 2
    assert all(deadline - stamp <= .400 for stamp, deadline in calls.detectors)
    marker, evidence = frame(capture)
    assert marker == (b"F" if enabled else b"R")
    assert "PRIVATE_CANARY" not in capture.mapping[:].decode("ascii")
    assert not any("timing" in key or "diagnostic" in key for key in decision)
    if enabled:
        assert evidence["terminal"] == "returned" and evidence["http_status"] == 200
        assert evidence["server_ms"] == 500 and evidence["reserve_ms"] == 100
        assert evidence["marks_us"][13] <= evidence["marks_us"][14] <= evidence["marks_us"][15]
        assert evidence["correlation_id"] == plugins._TIMING_CORRELATION


@pytest.mark.parametrize("terminal", ["cancelled", "error"])
def test_asgi_exports_after_terminal_path_and_preserves_failure(capture, terminal):
    events = []

    async def app(scope, receive, send):
        ctx = plugins._TIMING_CONTEXT.get()
        ctx.update(context(capture))
        try:
            await send({"type": "http.response.start", "status": 503, "headers": []})
            if terminal == "cancelled":
                raise asyncio.CancelledError
            raise RuntimeError("PRIVATE_CANARY")
        finally:
            events.append("original-finally")
            assert frame(capture)[0] == b"R"

    async def send(message):
        events.append(message)

    async def receive():
        raise AssertionError("Probe must not read receive")

    async def run():
        error = asyncio.CancelledError if terminal == "cancelled" else RuntimeError
        with pytest.raises(error):
            await runtime_api._TimingASGI(app, capture)({"type": "http", "method": "POST", "path": "/v2/evaluations"}, receive, send)
        assert plugins._TIMING_CONTEXT.get() is None

    asyncio.run(run())
    assert events[-1] == "original-finally"
    assert frame(capture)[1]["terminal"] == terminal
    assert "PRIVATE_CANARY" not in capture.mapping[:].decode("ascii")


def test_recorder_export_failure_cannot_replace_response(capture, monkeypatch):
    async def app(scope, receive, send):
        plugins._TIMING_CONTEXT.get().update(context(capture))
        await send({"type": "http.response.body", "body": b"unchanged"})
        return "original-result"

    def broken(*args):
        raise OSError("private failure")

    monkeypatch.setattr(capture, "publish", broken)
    messages = []

    async def send(value):
        messages.append(value)

    assert asyncio.run(runtime_api._TimingASGI(app, capture)(
        {"type": "http", "method": "POST", "path": "/v2/evaluations"}, None, send)) == "original-result"
    assert messages == [{"type": "http.response.body", "body": b"unchanged"}]
    assert frame(capture)[0] == b"R" and plugins._TIMING_CONTEXT.get() is None


@pytest.mark.parametrize("fault", ["unknown_origin", "duplicate", "overflow"])
def test_malformed_record_is_unavailable_without_exporting_untrusted_values(capture, fault):
    ctx = context(capture)
    token = plugins._TIMING_CONTEXT.set(ctx)
    try:
        plugins._timing_event("rule", SimpleNamespace(rule_id="phase3-pii", kind="pii"), time.monotonic() + .4)
        if fault == "unknown_origin":
            plugins._timing_event("timeout", "PRIVATE_CANARY")
        elif fault == "duplicate":
            plugins._timing_event("step", 1)
            plugins._timing_event("step", 1)
        else:
            ctx["record"]["started"] -= 301_000_000_000
        capture.publish(ctx["record"], "returned", 200)
        assert frame(capture)[0] == b"R"
        assert "PRIVATE_CANARY" not in capture.mapping[:].decode("ascii")
    finally:
        plugins._TIMING_CONTEXT.reset(token)


@pytest.mark.parametrize("mode,origin", [("expired", "before_queue"), ("queue", "queue"),
    ("drain", "write_or_drain"), ("read", "read_response"), ("worker", "worker_reported_deadline"),
    ("cancel", "outer_cancelled"), ("success", None)])
@pytest.mark.parametrize("enabled", [False, True])
def test_pool_timeout_origin_does_not_change_cutoff_or_cleanup(capture, mode, origin, enabled):
    async def run():
        pool = plugins.PinnedPiiEvaluator(workers=1)
        pool._ready = True
        actions = []

        class Pipe:
            def write(self, raw):
                actions.append("write")

            async def drain(self):
                if mode == "drain":
                    await asyncio.sleep(1)

            async def readline(self):
                if mode in {"read", "cancel"}:
                    if mode == "cancel":
                        raise asyncio.CancelledError
                    await asyncio.sleep(1)
                return b'{"error":"evaluation_timeout"}\n' if mode == "worker" else b'{"findings":[]}\n'

        class Process:
            returncode = None
            stdin = stdout = Pipe()

            def kill(self):
                self.returncode = -9
                actions.append("kill")

        process = Process()
        pool._prepared.add(process)
        if mode != "queue":
            pool._available.put_nowait(process)

        async def replace(value):
            assert value is process
            actions.append("replace")

        pool._replace = replace
        ctx = context(capture) if enabled else None
        token = plugins._TIMING_CONTEXT.set(ctx)
        limit = time.monotonic() + (-1 if mode == "expired" else .025)
        plugins._timing_event("rule", SimpleNamespace(rule_id="phase3-pii", kind="pii"), limit)

        def decode(value):
            if "error" in value:
                raise EvolutionError(value["error"])
            return value["findings"]

        start = time.monotonic()
        try:
            if mode == "success":
                assert await pool._submit({"text": "PRIVATE_CANARY"}, limit, decode) == []
            elif mode == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await pool._submit({}, limit, decode)
            else:
                with pytest.raises(EvolutionError) as failure:
                    await pool._submit({}, limit, decode)
                assert failure.value.code == "evaluation_timeout"
            assert time.monotonic() - start < .3
            await asyncio.gather(*tuple(pool._cleanup))
            assert pool._admitted == 0 and not pool._busy and not pool._tasks
            if mode in {"drain", "read", "worker", "cancel"}:
                assert actions[-2:] == ["kill", "replace"]
            else:
                assert "kill" not in actions
            if enabled:
                assert ctx["record"]["origin"][0] == origin
                capture.publish(ctx["record"], "returned", 200)
                assert frame(capture)[1]["pool_ms"] == 400
                assert "PRIVATE_CANARY" not in capture.mapping[:].decode("ascii")
        finally:
            plugins._TIMING_CONTEXT.reset(token)

    asyncio.run(run())



def _timing_http_exchange(value, capture):
    """In-memory ASGI transport with one body and an explicit disconnect."""
    raw = json.dumps(value).encode()
    exchange = SimpleNamespace(inbox=asyncio.Queue(), messages=[], receive_calls=[],
                               receiver_finished=False)
    exchange.scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "https", "path": "/v2/evaluations",
        "raw_path": b"/v2/evaluations", "query_string": b"", "root_path": "",
        "server": ("runtime.example", 443), "client": ("127.0.0.1", 12345),
        "headers": [(b"host", b"runtime.example"),
                    (b"authorization", b"Bearer synthetic"),
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode())],
    }

    async def receive():
        exchange.receive_calls.append(asyncio.current_task())
        if len(exchange.receive_calls) == 1:
            return {"type": "http.request", "body": raw, "more_body": False}
        try:
            return await exchange.inbox.get()
        finally:
            exchange.receiver_finished = True

    async def send(message):
        exchange.messages.append(dict(message))
        exchange.send_markers.append(frame(capture)[0])

    exchange.send_markers = []
    exchange.receive, exchange.send = receive, send
    return exchange


@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled"])
@pytest.mark.parametrize("stop", ["task_cancel", "outer_timeout", "disconnect"])
def test_assembled_asgi_cancellation_joins_fake_pool_before_publication(
        capture, monkeypatch, stop, enabled):
    selected = [rule(rule_id="phase3-pii", stages=["input"]),
                rule(stages=["input"], mandatory=True)]
    service, _, calls = fixture_runtime(rules=selected, state="observe")
    monkeypatch.setattr(runtime_api, "_TimingCapture",
                        lambda: capture if enabled else SimpleNamespace(mapping=None))

    async def run():
        pool = plugins.PinnedPiiEvaluator(workers=1)
        pool._ready = True
        entered, blocked = asyncio.Event(), asyncio.Event()
        actions, detector_tasks, publications, restored = [], [], [], []
        sentinel = {"outside_request": "PRIVATE_SENTINEL"}
        token = plugins._TIMING_CONTEXT.set(sentinel)
        control = SimpleNamespace(timeout=None)

        class Pipe:
            def write(self, raw):
                actions.append("write")

            async def drain(self):
                return None

            async def readline(self):
                entered.set()
                await blocked.wait()
                raise AssertionError("Only cancellation may end the fake read")

        class Process:
            returncode = None
            stdin = stdout = Pipe()

            def kill(self):
                self.returncode = -9
                actions.append("kill")

        process = Process()
        pool._prepared.add(process)
        pool._available.put_nowait(process)

        async def replace(value):
            assert value is process
            actions.append("replace")

        pool._replace = replace

        async def detector(selected_rule, parts, deadline, **kwargs):
            calls.detectors.append((time.monotonic(), deadline))
            detector_tasks.append(asyncio.current_task())
            return await pool._submit({"probe": "PRIVATE_CANARY"}, deadline,
                                      lambda result: tuple(result["findings"]))

        service.registry.evaluate = detector
        app = runtime_api.create_app(service=service)
        exchange = _timing_http_exchange(payload(correlation_id=plugins._TIMING_CORRELATION), capture)
        original_publish = capture.publish

        def publish(record, outcome, status):
            # Recorder exceptions are swallowed: assert these observations outside it.
            publications.append({"detectors_done": all(task.done() for task in detector_tasks),
                                 "receiver_done": exchange.receiver_finished,
                                 "admitted": pool._admitted, "busy": len(pool._busy),
                                 "tasks": len(pool._tasks), "jobs": len(service._jobs),
                                 "marker_before": frame(capture)[0]})
            original_publish(record, outcome, status)

        monkeypatch.setattr(capture, "publish", publish)

        async def invoke():
            try:
                if stop == "outer_timeout":
                    # A caller-owned timeout cancels the task; the HTTP 500ms
                    # budget and pool 400ms cutoff remain untouched.
                    async with asyncio.timeout(None) as limit:
                        control.timeout = limit
                        await app(exchange.scope, exchange.receive, exchange.send)
                else:
                    await app(exchange.scope, exchange.receive, exchange.send)
            finally:
                restored.append(plugins._TIMING_CONTEXT.get() is sentinel)

        request_task = asyncio.create_task(invoke())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert service.config.text_budget_ms == 500
            assert service.config.audit_reserve_ms == 100 and pool.timeout_ms == 400
            assert len(calls.detectors) == 1 and calls.leases[0][0] == calls.leases[0][1]
            began, cutoff = calls.detectors[0]
            assert 0 < cutoff - began <= .4
            assert cutoff == pytest.approx(calls.leases[0][1] - .1, rel=0, abs=1e-9)
            assert frame(capture)[0] == b"R"
            if stop == "disconnect":
                exchange.inbox.put_nowait({"type": "http.disconnect"})
                await asyncio.wait_for(request_task, 1)
                assert [item["status"] for item in exchange.messages
                        if item["type"] == "http.response.start"] == [503]
                response = json.loads(b"".join(item.get("body", b"") for item in exchange.messages))
                assert response["error"]["code"] == "evaluator_unavailable"
                assert exchange.send_markers and set(exchange.send_markers) == {b"R"}
            else:
                if stop == "task_cancel":
                    assert request_task.cancel()
                    error = asyncio.CancelledError
                else:
                    assert control.timeout is not None
                    control.timeout.reschedule(asyncio.get_running_loop().time())
                    error = TimeoutError
                with pytest.raises(error):
                    await asyncio.wait_for(request_task, 1)
                assert exchange.messages == []
            await asyncio.gather(*tuple(pool._cleanup), *tuple(service._cleanup))
            assert calls.finalized == [] and len(calls.released) == 1
            assert actions == ["write", "kill", "replace"]
            assert pool._admitted == 0 and not pool._busy and not pool._tasks and not service._jobs
            assert len(exchange.receive_calls) == 2 and exchange.receiver_finished
            assert restored == [True] and plugins._TIMING_CONTEXT.get() is sentinel
            marker, evidence = frame(capture)
            assert marker == (b"F" if enabled else b"R")
            if enabled:
                assert publications == [{"detectors_done": True, "receiver_done": True,
                                         "admitted": 0, "busy": 0, "tasks": 0, "jobs": 0,
                                         "marker_before": b"R"}]
                assert evidence["terminal"] == ("returned" if stop == "disconnect" else "cancelled")
                assert evidence["http_status"] == (503 if stop == "disconnect" else None)
                assert evidence["timeout_origin"] == ["outer_cancelled", None]
                assert evidence["server_ms"] == 500 and evidence["reserve_ms"] == 100
                assert evidence["pool_ms"] == 400
                assert all(evidence["marks_us"][index] is not None for index in range(8))
                assert evidence["marks_us"][8:15] == [None] * 7
            else:
                assert publications == []
            assert "PRIVATE_" not in capture.mapping[:].decode("ascii")
        finally:
            if not request_task.done():
                request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
            await asyncio.gather(*tuple(pool._cleanup), *tuple(service._cleanup), return_exceptions=True)
            plugins._TIMING_CONTEXT.reset(token)

    try:
        asyncio.run(run())
    finally:
        service._executors["team-a"].shutdown(wait=True, cancel_futures=True)


def test_overlapping_asgi_requests_publish_only_matching_winner_and_restore_context(capture, monkeypatch):
    selected = [rule(rule_id="phase3-pii", stages=["input"]),
                rule(stages=["input"], mandatory=True)]
    service, _, calls = fixture_runtime(rules=selected, state="observe")
    monkeypatch.setattr(runtime_api, "_TimingCapture", lambda: capture)

    async def run():
        labels = ("unrelated", "winner", "duplicate")
        authenticate_release = {label: asyncio.Event() for label in labels}
        detector_entered = {label: asyncio.Event() for label in labels}
        detector_release = {label: asyncio.Event() for label in labels}
        all_authenticating = asyncio.Event()
        auth_contexts, detector_contexts, restored, publications = {}, {}, {}, []
        sentinel = {"outside_request": "PRIVATE_SENTINEL"}
        token = plugins._TIMING_CONTEXT.set(sentinel)
        original_authenticate, original_publish = service.authenticate, capture.publish

        async def authenticate(credential, deadline):
            identity = await original_authenticate(credential, deadline)
            label = asyncio.current_task().get_name()
            auth_contexts[label] = plugins._TIMING_CONTEXT.get()
            if len(auth_contexts) == len(labels):
                all_authenticating.set()
            await authenticate_release[label].wait()
            return identity

        async def detector(selected_rule, parts, deadline, **kwargs):
            label = parts[0].part_id
            calls.detectors.append((time.monotonic(), deadline))
            detector_contexts[label] = plugins._TIMING_CONTEXT.get()
            detector_entered[label].set()
            await detector_release[label].wait()
            if label != "winner":
                raise EvolutionError("capability_missing" if label == "unrelated" else "policy_unresolved")
            return ()

        def publish(record, outcome, status):
            publications.append((record, outcome, status))
            original_publish(record, outcome, status)

        service.authenticate, service.registry.evaluate = authenticate, detector
        monkeypatch.setattr(capture, "publish", publish)
        app = runtime_api.create_app(service=service)
        exchanges = {
            label: _timing_http_exchange(payload(
                correlation_id="86eda564-bc16-4dd7-a8de-a9d9d4b2b005" if label == "unrelated" else plugins._TIMING_CORRELATION,
                parts=[{"part_id": label, "kind": "text", "role": "user", "text": "PRIVATE_" + label.upper()}]), capture)
            for label in labels
        }

        async def invoke(label):
            exchange = exchanges[label]
            try:
                await app(exchange.scope, exchange.receive, exchange.send)
            finally:
                restored[label] = plugins._TIMING_CONTEXT.get() is sentinel

        tasks = {label: asyncio.create_task(invoke(label), name=label) for label in labels}
        try:
            await asyncio.wait_for(all_authenticating.wait(), 1)
            assert not capture.claimed and len({id(value) for value in auth_contexts.values()}) == 3
            # All three wrappers are active before any request can claim capture.
            for label in labels:
                authenticate_release[label].set()
                await asyncio.wait_for(detector_entered[label].wait(), 1)
                assert detector_contexts[label] is auth_contexts[label]
                assert capture.claimed is (label != "unrelated")
            winner_record = detector_contexts["winner"]["record"]
            assert winner_record is not None
            assert detector_contexts["unrelated"]["record"] is None
            assert detector_contexts["duplicate"]["record"] is None
            assert frame(capture)[0] == b"R" and publications == []
            frozen = None
            for label, status, code in (("unrelated", 409, "capability_missing"),
                                        ("winner", 200, None), ("duplicate", 503, "policy_unresolved")):
                detector_release[label].set()
                await asyncio.wait_for(tasks[label], 1)
                exchange = exchanges[label]
                assert [item["status"] for item in exchange.messages
                        if item["type"] == "http.response.start"] == [status]
                response = json.loads(b"".join(item.get("body", b"") for item in exchange.messages))
                assert len(exchange.receive_calls) == 2 and exchange.receiver_finished
                if code is not None:
                    assert response["error"]["code"] == code
                else:
                    assert response["action"] == "allow" and response["reason_codes"] == []
                if label == "unrelated":
                    assert frame(capture)[0] == b"R" and publications == []
                elif label == "winner":
                    assert exchange.send_markers and set(exchange.send_markers) == {b"R"}
                    assert frame(capture)[0] == b"F"
                    frozen = capture.mapping[:]
                else:
                    assert capture.mapping[:] == frozen
            await asyncio.gather(*tuple(service._cleanup))
            assert publications == [(winner_record, "returned", 200)]
            assert restored == dict.fromkeys(labels, True)
            assert plugins._TIMING_CONTEXT.get() is sentinel
            assert sentinel == {"outside_request": "PRIVATE_SENTINEL"}
            assert len(calls.finalized) == 1 and len(calls.released) == 3 and not service._jobs
            evidence = frame(capture)[1]
            assert evidence["http_status"] == 200 and evidence["terminal"] == "returned"
            assert evidence["correlation_id"] == plugins._TIMING_CORRELATION
            assert evidence["marks_us"][5] <= evidence["marks_us"][9]
            assert evidence["marks_us"][13] <= evidence["marks_us"][14] <= evidence["marks_us"][15]
            assert "PRIVATE_" not in capture.mapping[:].decode("ascii")
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            await asyncio.gather(*tuple(service._cleanup), return_exceptions=True)
            plugins._TIMING_CONTEXT.reset(token)

    try:
        asyncio.run(run())
    finally:
        service._executors["team-a"].shutdown(wait=True, cancel_futures=True)
