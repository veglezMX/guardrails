"""Original runtime call failures expose only closed, request-local metadata."""

import asyncio
import json
import logging
from uuid import uuid4

import httpx
import pytest
from connector import evolution
from guardrails.evolution.errors import EvolutionError
from guardrails.evolution.models import (
    ContentPart,
    Decision,
    EvaluationRequest,
    PolicyRef,
)

CANARY = "private-content-credential-exception-canary"


def request(stage="input"):
    return EvaluationRequest(
        request_id=str(uuid4()), correlation_id=str(uuid4()), tenant_id="team-a",
        workload_id="chat", stage=stage, provider_id=str(uuid4()), deadline_ms=500,
        context_id=str(uuid4()) if stage == "output" else None,
        parts=(ContentPart(part_id="m0", kind="text", role="user" if stage == "input" else "assistant", text=CANARY),),
    )


def decision(value):
    return Decision(
        decision_id=str(uuid4()), request_id=value.request_id, context_id=value.context_id or str(uuid4()),
        tenant_id=value.tenant_id, workload_id=value.workload_id, stage=value.stage,
        policy=PolicyRef(tenant_id="team-a", policy_id="test", version=1, digest="a" * 64),
        binding_revision=1, snapshot_generation=1, action="allow", would_be_action="allow",
        evaluation_ms=1.0, audit_status="persisted",
    )


def client(tmp_path, handler, **kwargs):
    token = tmp_path / "token"
    token.write_text(CANARY)
    token.chmod(0o600)
    return evolution.EvaluationClient("https://runtime.test", str(token), "/etc/ssl/certs/ca-certificates.crt",
                                      transport=httpx.MockTransport(handler), **kwargs)


def records(caplog, *requests):
    rows = []
    for record in caplog.records:
        if record.name != "connector.evaluation":
            continue
        raw = record.getMessage()
        assert len(raw.encode()) <= 512
        assert CANARY not in raw and "https://" not in raw
        for value in requests:
            assert value.request_id not in raw and value.correlation_id not in raw
        value = json.loads(raw)
        assert set(value) == {"event", "version", "stage", "attempts", "http_status", "outcome", "elapsed_ms", "elapsed_capped"}
        assert value["event"] == "runtime_evaluation_failure" and value["version"] == 1
        assert 0 <= value["elapsed_ms"] <= 60000 and type(value["elapsed_capped"]) is bool
        rows.append(value)
    return rows


class UnreadBody(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise AssertionError(CANARY)
        yield b""  # pragma: no cover


class FailedBody(httpx.AsyncByteStream):
    def __init__(self, error):
        self.error = error

    async def __aiter__(self):
        yield b'{"private":'
        raise self.error(CANARY)


class WaitingBody(httpx.AsyncByteStream):
    def __init__(self, entered):
        self.entered = entered

    async def __aiter__(self):
        self.entered.set()
        await asyncio.Event().wait()
        yield b""  # pragma: no cover


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401, "authentication_failed"), (429, "quota_exceeded"), (500, "evaluator_unavailable"), (504, "evaluator_unavailable")])
async def test_original_non200_status_survives_flattening_without_reading_body(tmp_path, caplog, status, code):
    value, calls = request("output"), []

    def handler(wire):
        calls.append(wire)
        return httpx.Response(status, stream=UnreadBody())

    c = client(tmp_path, handler)
    try:
        with pytest.raises(EvolutionError) as failure:
            await c.evaluate(value)
        assert failure.value.code == code and len(calls) == 1 and not c.failures
        [row] = records(caplog, value)
        assert (row["stage"], row["http_status"], row["outcome"], row["attempts"]) == ("output", status, "http_non_200", 1)
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_permitted_connect_retry_success_emits_nothing(tmp_path, caplog):
    value, calls = request(), []

    def handler(wire):
        calls.append(wire)
        if len(calls) == 1:
            raise httpx.ConnectError(CANARY)
        return httpx.Response(200, json=decision(value).model_dump(mode="json"))

    c = client(tmp_path, handler)
    try:
        assert (await c.evaluate(value)).action == "allow"
        assert len(calls) == 2 and calls[0].content == calls[1].content
        assert calls[0].headers["x-request-id"] == calls[1].headers["x-request-id"] == value.request_id
        assert not c.failures and records(caplog, value) == []
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_terminal_predelivery_connect_failure_reports_two_attempts_once(tmp_path, caplog):
    value, calls = request(), []

    def handler(wire):
        calls.append(wire)
        raise httpx.ConnectTimeout(CANARY)

    c = client(tmp_path, handler)
    try:
        with pytest.raises(EvolutionError) as failure:
            await c.evaluate(value)
        assert failure.value.code == "evaluator_unavailable" and len(calls) == 2 and len(c.failures) == 2
        [row] = records(caplog, value)
        assert (row["attempts"], row["http_status"], row["outcome"]) == (2, 0, "connect_timeout")
    finally:
        await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error,outcome", [(httpx.ReadTimeout, "transport_timeout"), (httpx.ReadError, "network_error"), (httpx.RemoteProtocolError, "protocol_error")])
async def test_after_header_transport_failures_preserve_status_and_never_retry(tmp_path, caplog, error, outcome):
    value, calls = request(), []

    def handler(wire):
        calls.append(wire)
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=FailedBody(error))

    c = client(tmp_path, handler)
    try:
        with pytest.raises(EvolutionError) as failure:
            await c.evaluate(value)
        assert failure.value.code == "evaluator_unavailable" and len(calls) == 1 and len(c.failures) == 1
        [row] = records(caplog, value)
        assert (row["http_status"], row["outcome"], row["attempts"]) == (200, outcome, 1)
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_original_whole_deadline_is_distinct_from_httpx_timeout(tmp_path, caplog):
    value, entered = request(), asyncio.Event()
    c = client(tmp_path, lambda _: httpx.Response(200, headers={"content-type": "application/json"}, stream=WaitingBody(entered)))
    try:
        with pytest.raises(EvolutionError) as failure:
            await c.evaluate(value)
        assert failure.value.code == "evaluator_unavailable" and entered.is_set() and len(c.failures) == 1
        [row] = records(caplog, value)
        assert row["outcome"] == "whole_timeout" and row["http_status"] == 200 and row["attempts"] == 1
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_external_cancellation_and_emitter_failure_preserve_original(tmp_path, monkeypatch):
    value, entered, seen = request(), asyncio.Event(), []

    def broken(record):
        seen.append(json.loads(record))
        raise RuntimeError(CANARY)

    monkeypatch.setattr(evolution._EVALUATION_LOG, "warning", broken)
    c = client(tmp_path, lambda _: httpx.Response(200, headers={"content-type": "application/json"}, stream=WaitingBody(entered)))
    task = asyncio.create_task(c.evaluate(value))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel(CANARY)
        with pytest.raises(asyncio.CancelledError) as failure:
            await task
        assert failure.value.args == (CANARY,) and not c.failures
        assert len(seen) == 1 and seen[0]["outcome"] == "cancelled" and seen[0]["http_status"] == 200
        assert CANARY not in json.dumps(seen) and value.request_id not in json.dumps(seen)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await c.close()


@pytest.mark.asyncio
async def test_logger_factory_failure_does_not_replace_non200_error(tmp_path, monkeypatch):
    value = request()
    original = logging.getLogRecordFactory()

    def broken(*args, **kwargs):
        if args[0] == "connector.evaluation":
            raise RuntimeError(CANARY)
        return original(*args, **kwargs)

    c = client(tmp_path, lambda _: httpx.Response(503, stream=UnreadBody()))
    monkeypatch.setattr(logging, "_logRecordFactory", broken)
    try:
        with pytest.raises(EvolutionError) as failure:
            await c.evaluate(value)
        assert failure.value.code == "evaluator_unavailable" and not c.failures
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_unexpected_exception_identity_and_safe_category_are_preserved(tmp_path, caplog):
    value, original = request(), ArithmeticError(CANARY)

    def handler(_):
        raise original

    c = client(tmp_path, handler)
    try:
        with pytest.raises(ArithmeticError) as failure:
            await c.evaluate(value)
        assert failure.value is original and not c.failures
        [row] = records(caplog, value)
        assert (row["attempts"], row["http_status"], row["outcome"]) == (1, 0, "other_error")
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_invalid_decision_reports_only_finite_response_rejection(tmp_path, caplog):
    value = request()
    c = client(tmp_path, lambda _: httpx.Response(200, json={"private": CANARY}))
    try:
        with pytest.raises(EvolutionError) as failure:
            await c.evaluate(value)
        assert failure.value.code == "inspection_incomplete" and not c.failures
        [row] = records(caplog, value)
        assert (row["attempts"], row["http_status"], row["outcome"]) == (1, 200, "response_rejected")
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_shared_client_concurrent_evaluations_never_mix_metadata(tmp_path, caplog):
    first, second, entered = request(), request("output"), asyncio.Event()

    async def handler(wire):
        if json.loads(wire.content)["stage"] == "input":
            await entered.wait()
            return httpx.Response(504, stream=UnreadBody())
        entered.set()
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=FailedBody(httpx.ReadTimeout))

    c = client(tmp_path, handler)
    try:
        results = await asyncio.gather(c.evaluate(first), c.evaluate(second), return_exceptions=True)
        assert all(type(error) is EvolutionError and error.code == "evaluator_unavailable" for error in results)
        rows = records(caplog, first, second)
        assert len(rows) == 2 and {r["stage"] for r in rows} == {"input", "output"}
        by_stage = {r["stage"]: r for r in rows}
        assert (by_stage["input"]["http_status"], by_stage["input"]["outcome"]) == (504, "http_non_200")
        assert (by_stage["output"]["http_status"], by_stage["output"]["outcome"]) == (200, "transport_timeout")
        assert all(r["attempts"] == 1 for r in rows)
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_half_open_capabilities_status_is_not_reported_as_evaluation_status(tmp_path, caplog):
    value, calls = request(), []

    def handler(wire):
        calls.append((wire.method, wire.url.path))
        return httpx.Response(503, stream=UnreadBody())

    c = client(tmp_path, handler, clock=lambda: 20.0)
    c.opened_at = 0.0
    try:
        with pytest.raises(EvolutionError):
            await c.evaluate(value)
        assert calls == [("GET", "/v2/capabilities")] and c.opened_at == 20.0
        [row] = records(caplog, value)
        assert (row["attempts"], row["http_status"], row["outcome"]) == (0, 0, "admission_rejected")
    finally:
        await c.close()


def test_nonfinite_clock_and_unknown_metadata_cannot_escape_closed_record(monkeypatch, caplog):
    value = request()
    observation = {"attempts": 1, "http_status": 504, "outcome": None}
    for invalid in (float("nan"), float("inf")):
        with monkeypatch.context() as patch:
            patch.setattr(evolution.time, "monotonic", lambda captured=invalid: captured)
            evolution._record_evaluation_failure(value, EvolutionError("evaluator_unavailable"), observation, 0.0)
    for update in ({"http_status": CANARY}, {"outcome": CANARY}, {"attempts": CANARY}):
        evolution._record_evaluation_failure(value, RuntimeError(CANARY), {**observation, **update}, evolution.time.monotonic())
    assert records(caplog, value) == []
