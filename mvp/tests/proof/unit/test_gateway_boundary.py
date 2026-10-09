"""Boundary regressions; real LiteLLM behavior is the integration smoke gate."""
import asyncio
import json
import unittest
from uuid import uuid4

from connector.gateway_app import BoundaryError, ProofGatewayBoundary, validate_request
from connector.gateway_state import current_turn


def request(**changes):
    value = {"model": "proof-a", "messages": [{"role": "user", "content": "hello"}]}
    value.update(changes)
    return json.dumps(value).encode()


class GatewayRequestTests(unittest.TestCase):
    def test_supported_messages_are_kept_separate(self):
        raw = request(messages=[{"role": "system", "content": "rules"}, {"role": "user", "content": "hello"}])
        self.assertEqual(len(validate_request(raw, "proof-a")["messages"]), 2)

    def test_overrides_and_unsupported_shapes_reject(self):
        for changes in (
            {"model": "other"}, {"stream": True}, {"n": 2},
            {"guardrails": []}, {"metadata": {"workload_id": "other"}},
            {"api_key": "unused"}, {"messages": [{"role": "user", "content": []}]},
            {"messages": [{"role": "tool", "content": "result"}]},
        ):
            with self.subTest(changes=changes), self.assertRaises(BoundaryError):
                validate_request(request(**changes), "proof-a")

    def test_duplicate_keys_and_non_json_numbers_reject(self):
        for raw in (b'{"model":"proof-a","model":"other"}', request(temperature=float("nan")), b'"scalar"'):
            with self.subTest(raw=raw), self.assertRaises(BoundaryError):
                validate_request(raw, "proof-a")

    def test_strict_scalar_types_and_content_limit(self):
        for changes in ({"n": True}, {"stream": "false"}, {"max_tokens": "128"}):
            with self.subTest(changes=changes), self.assertRaises(BoundaryError) as error:
                validate_request(request(**changes), "proof-a")
            self.assertEqual(error.exception.status, 422)
        with self.assertRaises(BoundaryError) as error:
            validate_request(request(messages=[{"role": "user", "content": "x" * (1024 * 1024 + 1)}]), "proof-a")
        self.assertEqual(error.exception.status, 413)


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, proxy, *, registered=True, raw=None, key="gateway-test"):
        boundary = ProofGatewayBoundary(proxy, "proof-a", "gateway-test")
        boundary._registered = lambda: registered
        scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": [(b"authorization", ("Bearer " + key).encode())]}
        received = False
        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": raw or request(), "more_body": False}
        output = []
        async def send(message):
            output.append(message)
        await boundary(scope, receive, send)
        return output[0]["status"], json.loads(output[1]["body"])

    async def test_missing_registration_and_bad_auth_never_dispatch(self):
        called = False
        async def proxy(*args):
            nonlocal called
            called = True
        status, body = await self.invoke(proxy, registered=False)
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "guardrail_hook_missing")
        status, _ = await self.invoke(proxy, key="wrong")
        self.assertEqual(status, 401)
        self.assertFalse(called)

    async def test_success_without_completed_hooks_is_withheld(self):
        async def proxy(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b'{"unsafe":"do not deliver"}'})
        status, body = await self.invoke(proxy)
        self.assertEqual(status, 503)
        self.assertNotIn("unsafe", json.dumps(body))

    async def test_block_envelope_discards_upstream_text(self):
        decision_id = str(uuid4())
        async def proxy(scope, receive, send):
            turn = current_turn.get()
            turn.error_status = 403
            turn.error = {"code": "guardrail_blocked", "message": "Request blocked by configured guardrails", "correlation_id": turn.correlation_id, "decision_id": decision_id}
            await send({"type": "http.response.start", "status": 403, "headers": []})
            await send({"type": "http.response.body", "body": b'{"upstream_error":"synthetic-sensitive-value"}'})
        status, body = await self.invoke(proxy)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["decision_id"], decision_id)
        self.assertEqual(set(body["error"]), {"code", "message", "correlation_id", "decision_id"})
        self.assertNotIn("synthetic-sensitive-value", json.dumps(body))

    async def test_context_is_isolated_for_concurrent_calls(self):
        correlations = []
        async def proxy(scope, receive, send):
            turn = current_turn.get()
            correlations.append(turn.correlation_id)
            await asyncio.sleep(0)
            self.assertIs(turn, current_turn.get())
            turn.error_status = 503
            turn.error = {"code": "evaluation_unavailable", "correlation_id": turn.correlation_id}
        results = await asyncio.gather(self.invoke(proxy), self.invoke(proxy))
        self.assertEqual(len(set(correlations)), 2)
        self.assertEqual({body["error"]["correlation_id"] for _, body in results}, set(correlations))
        self.assertIsNone(current_turn.get())


if __name__ == "__main__":
    unittest.main()
