"""Actual sample-client observations through TLS and explicit transport ports."""

import asyncio
import importlib.util
import json
import multiprocessing
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from guardrails.evolution.errors import EvolutionError
from https_support import serve_https, tls_files

ROOT = Path(__file__).resolve().parents[4]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


support = module("real_model_observation_support", ROOT / "mvp/tests/evolution/e2e/cluster_support.py")
sample = module("real_model_observation_client", ROOT / "examples/evolution/client.py")


def observation():
    return {"call_ordinal": 0, "tenant_side": "A", "http_status": 0,
            "wire_code": None, "elapsed_ms": 0.0, "sample_completed": False}


def app(mode, calls):
    application = FastAPI()

    @application.get("/health/live")
    def live():
        return {"live": True}

    @application.post("/v1/chat/completions")
    async def complete(request: Request):
        with calls.get_lock():
            calls.value += 1
        correlation = request.headers["x-request-id"]
        headers = {"X-Request-ID": correlation, "Cache-Control": "no-store"}
        value = EvolutionError("evaluation_timeout").envelope(correlation)
        status = 503  # Actual gateway mapping need not equal the internal code's status.
        if mode == "unknown":
            value["error"]["code"] = "private-code-canary"
        elif mode == "message":
            value["error"]["message"] = "private-message-canary"
        elif mode == "extra":
            value["private"] = "private-body-canary"
        elif mode == "correlation":
            value["error"]["correlation_id"] = str(uuid4())
        elif mode == "header":
            headers["X-Request-ID"] = str(uuid4())
        elif mode == "compressed":
            headers["Content-Encoding"] = "gzip"
        elif mode == "oversized":
            return Response(b"x" * 4097, status_code=status, media_type="application/json", headers=headers)
        elif mode == "malformed":
            return Response(b'{"error":', status_code=status, media_type="application/json", headers=headers)
        elif mode == "content-type":
            return Response(b"private-body-canary", status_code=status, media_type="text/plain", headers=headers)
        elif mode.startswith("status-"):
            status = int(mode.removeprefix("status-"))
            code = {401: "authentication_failed", 403: "forbidden", 429: "quota_exceeded"}[status]
            value = EvolutionError(code).envelope(correlation)
        elif mode.startswith("delayed-"):
            status = int(mode.removeprefix("delayed-"))
            async def body():
                await asyncio.sleep(1)
                yield b"private-delayed-canary"
            return StreamingResponse(body(), status_code=status, media_type="application/json", headers=headers)
        elif mode in {"success", "bad-attribution"}:
            payload = await request.json()
            metadata = {
                "policy": {"tenant_id": "team-a", "policy_id": "sample", "version": 1, "digest": "a" * 64},
                "correlation_id": correlation, "input_decision_id": str(uuid4()), "output_decision_id": str(uuid4()),
                "binding_revision": 1, "snapshot_generation": 1,
                "input_action": "allow", "output_action": "allow", "audit_status": "persisted",
            }
            if mode == "bad-attribution":
                metadata.pop("output_decision_id")
            return JSONResponse({"model": payload["model"], "guardrails": metadata,
                                 "choices": [{"message": {"role": "assistant", "content": "private-success-canary"}}]}, headers=headers)
        return JSONResponse(value, status_code=status, headers=headers)

    return application


def cluster(server, tmp_path, created, *, short_deadline=False):
    token = tmp_path / "token"
    token.write_text("synthetic-private-token")
    token.chmod(0o600)

    class Client(sample.TeamClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            assert self.timeout == 50
            # Only this deterministic timeout regression uses a shorter injected
            # caller budget. Production construction and other cases remain50s.
            if short_deadline:
                self.timeout = 0.1
            created.append(self)

    result = object.__new__(support.Cluster)  # No deployment constructor or DB.
    result.access = {"ca_file": server["ca_file"], "tenants": {"team-a": {
        "gateway_url": server["url"], "gateway_token_file": str(token), "model_alias": "sample-model",
    }}}
    result.sample, result.profile = SimpleNamespace(TeamClient=Client), "local_model"
    return result


@pytest.mark.parametrize("mode", ["safe", "unknown", "message", "extra", "correlation", "header", "compressed", "oversized", "malformed", "content-type"])
def test_sample_observer_keeps_finite_non200_metadata_and_original_mapping(tmp_path, mode):
    calls, created, row = multiprocessing.Value("i", 0), [], observation()
    with serve_https(lambda: app(mode, calls), tmp_path / "tls") as server:
        client = cluster(server, tmp_path, created)
        with pytest.raises(EvolutionError) as caught:
            client.sample_complete("team-a", "private-input-canary", observation=row)
        assert caught.value.code == "evaluator_unavailable"
        assert calls.value == 1 and len(created) == 1
        assert created[0].client.is_closed
    assert row["http_status"] == 503 and row["sample_completed"] is False
    assert row["wire_code"] == ("evaluation_timeout" if mode == "safe" else None)
    assert row["elapsed_ms"] > 0
    assert "private" not in json.dumps(row)


@pytest.mark.parametrize("mode", ["success", "bad-attribution"])
def test_sample_observer_leaves_success_body_and_attribution_to_actual_client(tmp_path, mode):
    calls, created, row = multiprocessing.Value("i", 0), [], observation()
    with serve_https(lambda: app(mode, calls), tmp_path / "tls") as server:
        client = cluster(server, tmp_path, created)
        if mode == "success":
            result = client.sample_complete("team-a", "private-input-canary", observation=row)
            assert result["content"] == "private-success-canary"
        else:
            with pytest.raises(EvolutionError) as caught:
                client.sample_complete("team-a", "private-input-canary", observation=row)
            assert caught.value.code == "inspection_incomplete"
        assert calls.value == 1 and created[0].client.is_closed
    assert row["http_status"] == 200 and row["wire_code"] is None
    assert row["sample_completed"] is (mode == "success")
    assert "private" not in json.dumps(row)


@pytest.mark.parametrize("status,code", [(401, "authentication_failed"), (403, "forbidden"),
                                         (429, "quota_exceeded"), (503, "evaluator_unavailable")])
def test_sample_observer_error_body_cannot_extend_absolute_deadline_or_retry(tmp_path, monkeypatch, status, code):
    # This explicit transport port isolates the existing absolute body deadline
    # from cold TLS/header delivery. Real TLS mappings have separate coverage.
    calls, created, row = [], [], observation()

    class Stream(httpx.AsyncByteStream):
        reading = False
        cancelled = False
        closed = False

        async def __aiter__(self):
            self.reading = True
            try:
                await asyncio.sleep(1)
                yield b"private-delayed-canary"
            except asyncio.CancelledError:
                self.cancelled = True
                raise

        async def aclose(self):
            self.closed = True

    stream = Stream()

    async def headers(request):
        # No await before headers: the only suspended operation is body reading.
        calls.append(request.method)
        return httpx.Response(status, headers={"content-type": "application/json",
                              "x-request-id": request.headers["x-request-id"], "cache-control": "no-store"},
                              request=request, stream=stream)

    def transport_client(**kwargs):
        return httpx.AsyncClient(**kwargs, transport=httpx.MockTransport(headers))

    monkeypatch.setattr(sample, "httpx", SimpleNamespace(
        AsyncClient=transport_client, URL=httpx.URL, Timeout=httpx.Timeout, HTTPError=httpx.HTTPError,
    ))
    files = tls_files(tmp_path / "tls")
    client = cluster({"url": "https://fixture.invalid", "ca_file": str(files["ca"])},
                     tmp_path, created, short_deadline=True)
    with pytest.raises(EvolutionError) as caught:
        client.sample_complete("team-a", "private-input-canary", observation=row)
    assert caught.value.code == code
    assert calls == ["POST"] and len(created) == 1 and created[0].client.is_closed
    assert stream.reading and stream.cancelled and stream.closed
    assert row["http_status"] == status and row["wire_code"] is None
    assert row["sample_completed"] is False and 90 <= row["elapsed_ms"] < 750
    assert "private" not in json.dumps(row)


@pytest.mark.parametrize("status,code", [(401, "authentication_failed"), (403, "forbidden"),
                                         (429, "quota_exceeded")])
def test_sample_observer_preserves_real_tls_header_mapping_at_original_client_budget(tmp_path, status, code):
    calls, created, row = multiprocessing.Value("i", 0), [], observation()
    with serve_https(lambda: app(f"status-{status}", calls), tmp_path / "tls") as server:
        client = cluster(server, tmp_path, created)
        with pytest.raises(EvolutionError) as caught:
            client.sample_complete("team-a", "private-input-canary", observation=row)
        assert caught.value.code == code
        assert calls.value == 1 and len(created) == 1 and created[0].client.is_closed
        assert created[0].timeout == 50
    assert row["http_status"] == status and row["wire_code"] == code
    assert row["sample_completed"] is False and row["elapsed_ms"] > 0
    assert "private" not in json.dumps(row)


def test_sample_observer_stops_chunked_error_at_bound_without_decoding():
    class Stream(httpx.AsyncByteStream):
        reads = 0
        async def __aiter__(self):
            for _ in range(100):
                self.reads += 1
                yield b"x" * 4097

    async def exercise():
        stream, row = Stream(), observation()
        response = httpx.Response(503, headers={"content-type": "application/json"},
                                  stream=stream, request=httpx.Request("POST", "https://fixture.invalid"))
        try:
            await support.observe_sample_response(response, row)
        finally:
            await response.aclose()
        assert stream.reads == 1 and row["wire_code"] is None
        assert row["http_status"] == 503 and response.is_closed

    asyncio.run(exercise())


@pytest.mark.parametrize("length", [b"9" * 5000, "١".encode()], ids=["overlong", "non-ascii"])
def test_sample_observer_rejects_pathological_length_before_conversion_or_read(length):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("body must not be read")
            yield b""

    async def exercise():
        row = observation()
        response = httpx.Response(403, headers=[(b"content-type", b"application/json"),
                                               (b"content-length", length)],
                                  stream=Stream(), request=httpx.Request("POST", "https://fixture.invalid"))
        try:
            await support.observe_sample_response(response, row)
        finally:
            await response.aclose()
        assert row["http_status"] == 403 and row["wire_code"] is None

    asyncio.run(exercise())


def test_sample_observer_propagates_external_cancellation():
    async def exercise():
        reading = asyncio.Event()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                reading.set()
                await asyncio.Event().wait()
                yield b""

        row = observation()
        response = httpx.Response(401, headers={"content-type": "application/json"},
                                  stream=Stream(), request=httpx.Request("POST", "https://fixture.invalid"))
        task = asyncio.create_task(support.observe_sample_response(response, row))
        try:
            async with asyncio.timeout(1):
                await reading.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            await response.aclose()
        assert row["http_status"] == 401 and row["wire_code"] is None

    asyncio.run(exercise())


def test_real_model_fixture_retains_empty_attachment_when_setup_fails(tmp_path, monkeypatch):
    directory = tmp_path / "evidence"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("EVOLUTION_TEST_EVIDENCE_DIR", str(directory))
    monkeypatch.setitem(sys.modules, "cluster_support", support)
    fixture = module("observed_real_model_fixture", ROOT / "mvp/tests/evolution/e2e/test_real_model.py")

    def fail(_profile):
        raise RuntimeError("private-setup-exception-canary")

    monkeypatch.setattr(fixture, "Cluster", fail)
    with pytest.raises(RuntimeError):
        fixture.test_twenty_real_ollama_calls_preserve_exact_policy_audit_and_private_content()
    path = directory / "real-model-observation.json"
    assert json.loads(path.read_text()) == {
        "schema_version": "evolution-real-model-observation-v1", "expected_calls": 20,
        "actual_attempts": 0, "completed": 0, "rows": [],
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert "private" not in path.read_text()
    with pytest.raises(FileExistsError):
        support.record_real_model_observation([])
