"""Bounded administrative HTTP reads with an absolute cancellation deadline."""

from __future__ import annotations

import asyncio
import time

import httpx


class BoundedJsonClient:
    """Synchronous command adapter; each request owns its async transport lifetime."""

    def __init__(self, *, base_url, verify, deadline, max_bytes=8 * 1024 * 1024):
        self.base_url, self.verify = base_url, verify
        self.deadline, self.max_bytes = deadline, max_bytes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def request(
        self, method, path, *, headers=None, json=None, params=None, timeout=None
    ):
        request_headers = {**(headers or {}), "Accept-Encoding": "identity"}

        async def fetch():
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise httpx.TimeoutException("Management deadline exceeded")
            try:
                async with asyncio.timeout(remaining):
                    async with httpx.AsyncClient(
                        base_url=self.base_url,
                        verify=self.verify,
                        follow_redirects=False,
                        trust_env=False,
                        timeout=min(remaining, timeout or remaining),
                    ) as client:
                        async with client.stream(
                            method,
                            path,
                            headers=request_headers,
                            json=json,
                            params=params,
                        ) as response:
                            # Failed intermediaries may echo credential values. Do not consume them.
                            if response.status_code >= 300:
                                return httpx.Response(response.status_code)
                            if (
                                response.headers.get(
                                    "content-encoding", "identity"
                                ).lower()
                                != "identity"
                            ):
                                raise ValueError(
                                    "Compressed management responses are unsupported"
                                )
                            body = bytearray()
                            async for chunk in response.aiter_bytes():
                                if len(body) + len(chunk) > self.max_bytes:
                                    raise ValueError(
                                        "Management response exceeds byte limit"
                                    )
                                body.extend(chunk)
                            return httpx.Response(
                                response.status_code, content=bytes(body)
                            )
            except TimeoutError:
                raise httpx.TimeoutException("Management deadline exceeded") from None

        return asyncio.run(fetch())
