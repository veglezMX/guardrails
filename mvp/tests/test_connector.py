"""The connector — enforcement mechanics, and the fail-closed rules."""

from __future__ import annotations

import threading
import unittest
from typing import Any

from support import CREDENTIAL, TENANT, WORKLOAD, make_runtime

from connector.core import (
    ConnectorConfig,
    EvaluationTransportError,
    GuardedInvocation,
    HttpEvaluationClient,
)
from guardrails.app import make_server

CARD = "card 4111 1111 1111 1111"
EMAIL = "mail me at dana.reyes@example.com"
CLEAN = "summarise the ticket"


def config(**overrides: Any) -> ConnectorConfig:
    fields: dict[str, Any] = {
        "endpoint": "http://unused",
        "credential": CREDENTIAL,
        "tenant_id": TENANT,
        "workload_id": WORKLOAD,
        "on_evaluation_unavailable": "block",
    }
    fields.update(overrides)
    return ConnectorConfig(**fields)


def decision(action: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "action": action,
        "allowed": action != "block",
        "enforcement_state": "block",
        "reason_codes": [],
        "guardrail_category": None,
        "policy_name": "p",
        "policy_version": 1,
        "evaluator_metadata": [],
        "correlation_id": "corr-1",
        "audit_id": "a",
        "evaluation_latency_ms": 1.0,
    }
    body.update(overrides)
    return body


class ScriptedClient:
    """Returns one canned decision per stage, and records what it was asked."""

    def __init__(self, by_stage: dict[str, Any]) -> None:
        self.by_stage = by_stage
        self.requests: list[dict[str, Any]] = []

    def evaluate(self, body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        self.requests.append(body)
        outcome = self.by_stage[body["stage"]]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Enforcement(unittest.TestCase):
    def _run(self, client: ScriptedClient, prompt: str = CLEAN, response: str = "ok"):
        calls: list[str] = []

        def invoke_model(text: str) -> str:
            calls.append(text)
            return response

        result = GuardedInvocation(config(), client).run(prompt, invoke_model)
        return result, calls

    def test_input_block_never_calls_the_model(self) -> None:
        client = ScriptedClient({"input": decision("block")})
        result, calls = self._run(client)
        self.assertEqual(calls, [])
        self.assertFalse(result.allowed)
        self.assertEqual(result.blocked_at, "input")
        self.assertFalse(result.model_called)

    def test_input_redact_calls_the_model_with_the_transformed_content(self) -> None:
        client = ScriptedClient(
            {
                "input": decision(
                    "redact",
                    transformed_content={"kind": "text", "text": "mail me at [REDACTED]"},
                ),
                "output": decision("allow"),
            }
        )
        result, calls = self._run(client, prompt=EMAIL)
        self.assertEqual(calls, ["mail me at [REDACTED]"])
        self.assertTrue(result.allowed)

    def test_output_block_returns_a_safe_fallback(self) -> None:
        client = ScriptedClient(
            {"input": decision("allow"), "output": decision("block")}
        )
        result, calls = self._run(client)
        self.assertEqual(len(calls), 1)  # the model did run
        self.assertFalse(result.allowed)
        self.assertEqual(result.blocked_at, "output")
        self.assertEqual(result.text, config().safe_fallback_text)

    def test_output_redact_returns_the_transformed_response(self) -> None:
        client = ScriptedClient(
            {
                "input": decision("allow"),
                "output": decision(
                    "redact", transformed_content={"kind": "text", "text": "[REDACTED]"}
                ),
            }
        )
        result, _ = self._run(client)
        self.assertEqual(result.text, "[REDACTED]")

    def test_log_only_passes_the_original_content_through(self) -> None:
        client = ScriptedClient(
            {"input": decision("log_only"), "output": decision("log_only")}
        )
        result, calls = self._run(client, prompt=CARD, response="model said this")
        self.assertEqual(calls, [CARD])
        self.assertEqual(result.text, "model said this")
        self.assertTrue(result.allowed)

    def test_one_correlation_id_spans_both_stages(self) -> None:
        client = ScriptedClient(
            {"input": decision("allow"), "output": decision("allow")}
        )
        result, _ = self._run(client)
        ids = {r["correlation_id"] for r in client.requests}
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids.pop(), result.correlation_id)

    def test_warn_surfaces_the_reason_codes_to_the_caller(self) -> None:
        client = ScriptedClient(
            {
                "input": decision(
                    "log_only",
                    enforcement_state="warn",
                    reason_codes=["GR-003:sensitive_data:email"],
                ),
                "output": decision("allow"),
            }
        )
        result, _ = self._run(client)
        self.assertIn("GR-003:sensitive_data:email", result.warnings)

    def test_observe_stays_silent(self) -> None:
        client = ScriptedClient(
            {
                "input": decision(
                    "log_only",
                    enforcement_state="observe",
                    reason_codes=["GR-003:sensitive_data:email"],
                ),
                "output": decision("allow"),
            }
        )
        result, _ = self._run(client)
        self.assertEqual(result.warnings, ())


class FailClosed(unittest.TestCase):
    """The most dangerous misreading of the contract, tested directly."""

    def _run(self, client: ScriptedClient, behavior: str):
        calls: list[str] = []
        invocation = GuardedInvocation(
            config(on_evaluation_unavailable=behavior), client
        )
        return invocation.run(CLEAN, lambda text: calls.append(text) or "ok"), calls

    def test_an_unrecognized_action_is_never_treated_as_allow(self) -> None:
        client = ScriptedClient({"input": decision("discard")})
        result, calls = self._run(client, "block")
        self.assertFalse(result.allowed)
        self.assertEqual(calls, [])

    def test_an_unrecognized_action_applies_the_configured_behavior(self) -> None:
        client = ScriptedClient({"input": decision("discard")})
        result, calls = self._run(client, "allow_and_log")
        self.assertTrue(result.allowed)
        self.assertEqual(calls, [CLEAN])
        self.assertTrue(result.model_called)
        self.assertEqual(result.text, 'ok')
        self.assertTrue(result.warnings)

    def test_an_unreachable_runtime_blocks_when_configured_to(self) -> None:
        client = ScriptedClient(
            {"input": EvaluationTransportError("connection refused")}
        )
        result, calls = self._run(client, "block")
        self.assertFalse(result.allowed)
        self.assertEqual(calls, [])

    def test_an_unreachable_runtime_allows_only_when_configured_to(self) -> None:
        client = ScriptedClient(
            {"input": EvaluationTransportError("connection refused")}
        )
        result, calls = self._run(client, "allow_and_log")
        self.assertTrue(result.allowed)
        self.assertEqual(calls, [CLEAN])
        self.assertTrue(result.model_called)
        self.assertEqual(result.text, 'ok')

    def test_a_bad_client_failure_behavior_is_refused_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            config(on_evaluation_unavailable="pretend_nothing_happened")


class EndToEnd(unittest.TestCase):
    """Real connector, real HTTP, real runtime. The model is the only stub."""

    def setUp(self) -> None:
        self.runtime = make_runtime(state="block")
        self.server = make_server(self.runtime, "127.0.0.1", 0)
        host, port = self.server.server_address[0], self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        endpoint = f"http://{host}:{port}"
        self.invocation = GuardedInvocation(
            config(endpoint=endpoint),
            HttpEvaluationClient(endpoint, CREDENTIAL),
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_a_clean_prompt_reaches_the_model_and_returns_unchanged(self) -> None:
        result = self.invocation.run(CLEAN, lambda text: "a summary")
        self.assertTrue(result.allowed)
        self.assertEqual(result.text, "a summary")
        self.assertEqual(result.decisions["input"]["action"], "allow")

    def test_a_card_in_the_prompt_stops_before_the_model(self) -> None:
        calls: list[str] = []
        result = self.invocation.run(CARD, lambda text: calls.append(text) or "x")
        self.assertEqual(calls, [])
        self.assertFalse(result.allowed)
        self.assertEqual(result.blocked_at, "input")

    def test_an_email_in_the_prompt_is_redacted_before_inference(self) -> None:
        calls: list[str] = []

        def invoke(text: str) -> str:
            calls.append(text)
            return "acknowledged"

        result = self.invocation.run(EMAIL, invoke)
        self.assertTrue(result.allowed)
        self.assertNotIn("dana.reyes@example.com", calls[0])
        self.assertIn("[REDACTED]", calls[0])

    def test_a_card_in_the_response_is_caught_after_inference(self) -> None:
        result = self.invocation.run(CLEAN, lambda text: CARD)
        self.assertFalse(result.allowed)
        self.assertEqual(result.blocked_at, "output")
        self.assertEqual(result.text, config().safe_fallback_text)

    def test_both_stages_are_audited_under_one_correlation_id(self) -> None:
        result = self.invocation.run(CLEAN, lambda text: "fine")
        records = [
            r
            for r in self.runtime.audit.records
            if r["correlation_id"] == result.correlation_id
        ]
        self.assertEqual({r["stage"] for r in records}, {"input", "output"})


if __name__ == "__main__":
    unittest.main()
