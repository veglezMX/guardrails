"""The connector deadline bounds the whole exchange, not each read chunk."""
import asyncio
import ssl
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from connector.core import RuntimeClient, RuntimeClientError


class DripResponse:
    status_code = 200
    headers = {"content-type": "application/json"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_bytes(self):
        # Each read is shorter than the per-read timeout, but the whole body
        # would take one second without the outer monotonic deadline.
        for _ in range(50):
            await asyncio.sleep(0.02)
            yield b" "


class DripHTTPClient:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        return DripResponse()


class RuntimeDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_total_timeout_stops_a_dripping_response(self):
        client = object.__new__(RuntimeClient)
        client._url = "https://runtime.example/v1/evaluate"
        client._token = "synthetic-token"
        client._tls = ssl.create_default_context()
        started = time.monotonic()
        with patch("httpx.AsyncClient", DripHTTPClient):
            with self.assertRaises(RuntimeClientError) as error:
                await client.evaluate({}, timeout_s=0.07)
        self.assertEqual(error.exception.code, "evaluation_timeout")
        self.assertLess(time.monotonic() - started, 0.5)


class RuntimeContextTests(unittest.IsolatedAsyncioTestCase):
    async def evaluate_response(self, *, action, response_context):
        correlation = str(uuid4())
        decision = dict(
            decision_id=str(uuid4()), evaluation_id=str(uuid4()),
            correlation_id=correlation, context_id=response_context,
            tenant_id="local-proof", workload_id="gateway-a", stage="output",
            policy_id=None, policy_version=None, policy_digest=None,
            binding_revision=None, snapshot_generation=None, enforcement_state=None,
            action=action, allowed=action != "block", would_be_action=action,
            reason_codes=[], operational_codes=[], guardrail_category=None,
            evaluator_metadata=[], audit_id=str(uuid4()), findings=[],
            transformed_content=None, evaluation_ms=0.1, source="runtime",
            audit_status="persisted", client_config=None,
        )
        body = {"tenant_id": "local-proof", "workload_id": "gateway-a",
                "stage": "output", "correlation_id": correlation,
                "context_id": "synthetic-input-context"}
        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=decision))
        def client_factory(**kwargs):
            return original_client(transport=transport, **kwargs)
        client = object.__new__(RuntimeClient)
        client._url = "https://runtime.example/v1/evaluate"
        client._token = "synthetic-token"
        client._tls = ssl.create_default_context()
        with patch("httpx.AsyncClient", client_factory):
            return await client.evaluate(body)

    async def test_terminal_output_block_may_release_context(self):
        decision = await self.evaluate_response(action="block", response_context=None)
        self.assertEqual(decision["action"], "block")
        self.assertFalse(decision["allowed"])

    async def test_foreign_output_context_still_rejects_a_block(self):
        with self.assertRaises(RuntimeClientError) as error:
            await self.evaluate_response(action="block", response_context="foreign-context")
        self.assertEqual(error.exception.code, "invalid_runtime_response")

    async def test_nonblocking_output_requires_the_pinned_context(self):
        with self.assertRaises(RuntimeClientError) as error:
            await self.evaluate_response(action="allow", response_context=None)
        self.assertEqual(error.exception.code, "invalid_runtime_response")


if __name__ == "__main__":
    unittest.main()
