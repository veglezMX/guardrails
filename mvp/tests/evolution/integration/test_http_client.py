"""Real TLS streaming responses prove byte and total-time client bounds."""

import asyncio
import gzip
import ssl
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import Response, StreamingResponse
from guardrails.evolution.http_client import BoundedJsonClient
from https_support import serve_https


def application():
    app = FastAPI()

    @app.get("/health/live")
    async def live():
        return {"status": "live"}

    @app.get("/large")
    async def large():
        async def chunks():
            for _ in range(100):
                yield b"x" * 1024
                await asyncio.sleep(0.02)

        return StreamingResponse(chunks(), media_type="application/json")

    @app.get("/slow")
    async def slow():
        async def chunks():
            for _ in range(100):
                yield b" "
                await asyncio.sleep(0.04)
            yield b"{}"

        return StreamingResponse(chunks(), media_type="application/json")

    @app.get("/failure")
    async def failure():
        async def chunks():
            yield b"untrusted-secret-echo"
            await asyncio.sleep(10)

        return StreamingResponse(chunks(), status_code=503)

    @app.get("/compressed")
    async def compressed():
        return Response(
            gzip.compress(b" " * 1024 * 1024), headers={"Content-Encoding": "gzip"}
        )

    return app


def test_https_client_caps_streamed_bytes_and_discards_error_bodies(tmp_path):
    with serve_https(application, tmp_path) as server:
        with BoundedJsonClient(
            base_url=server["url"],
            verify=ssl.create_default_context(cafile=server["ca_file"]),
            deadline=time.monotonic() + 2,
            max_bytes=128,
        ) as client:
            started = time.monotonic()
            with pytest.raises(ValueError, match="byte limit"):
                client.request("GET", "/large")
            assert time.monotonic() - started < 1
            response = client.request("GET", "/failure")
            assert response.status_code == 503
            assert response.content == b""
            with pytest.raises(ValueError, match="Compressed"):
                client.request("GET", "/compressed")


def test_https_client_cancels_slow_trickle_at_total_deadline(tmp_path):
    with serve_https(application, tmp_path) as server:
        context = ssl.create_default_context(cafile=server["ca_file"])
        started = time.monotonic()
        with BoundedJsonClient(
            base_url=server["url"], verify=context, deadline=started + 0.2
        ) as client:
            with pytest.raises(httpx.TimeoutException):
                client.request("GET", "/slow", timeout=0.15)
        assert 0.15 <= time.monotonic() - started < 0.8
