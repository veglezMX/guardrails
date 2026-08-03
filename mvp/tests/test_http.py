"""The HTTP surface — ADR-0005's status-code rules."""

from __future__ import annotations

import json
import unittest

from support import CREDENTIAL, auth, evaluate_body, make_runtime

from guardrails.app import GuardrailsRuntime, RuntimeConfig
from guardrails.binding import WorkloadIdentity
from guardrails.evaluators import EvaluatorRegistry
from guardrails.store import PolicyCache

CARD = "card 4111 1111 1111 1111"


class StatusCodes(unittest.TestCase):
    def test_a_block_is_a_two_hundred(self) -> None:
        status, body = make_runtime().evaluate(auth(), evaluate_body(CARD))
        self.assertEqual(status, 200)
        self.assertEqual(body["action"], "block")
        self.assertFalse(body["allowed"])

    def test_a_failure_handler_substitute_is_a_two_hundred(self) -> None:
        # An evaluator outage is an evaluation outcome, not a transport error.
        runtime = make_runtime(registry=EvaluatorRegistry({}))
        status, body = runtime.evaluate(auth(), evaluate_body(CARD))
        self.assertEqual(status, 200)
        self.assertIn("GR-000:platform:detector_unavailable", body["reason_codes"])

    def test_malformed_body_is_four_hundred(self) -> None:
        status, body = make_runtime().evaluate(auth(), b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(body["status"], 400)
        self.assertIn("reason_code", body)

    def test_missing_credential_is_four_oh_one(self) -> None:
        status, _ = make_runtime().evaluate(None, evaluate_body("hi"))
        self.assertEqual(status, 401)

    def test_credential_for_another_workload_is_four_oh_three(self) -> None:
        status, body = make_runtime().evaluate(
            auth(), evaluate_body("hi", workload_id="someone-else")
        )
        self.assertEqual(status, 403)
        self.assertIn("credential", body["detail"])

    def test_oversize_content_is_four_thirteen(self) -> None:
        runtime = make_runtime()
        runtime.config.max_content_bytes = 32
        status, body = runtime.evaluate(auth(), evaluate_body("x" * 64))
        self.assertEqual(status, 413)
        self.assertIn("64", body["detail"])

    def test_unknown_stage_is_four_twenty_two(self) -> None:
        status, body = make_runtime().evaluate(
            auth(), evaluate_body("hi", stage="tool_call")
        )
        self.assertEqual(status, 422)
        self.assertIn("tool_call", body["detail"])

    def test_unknown_content_kind_is_four_twenty_two(self) -> None:
        status, _ = make_runtime().evaluate(
            auth(), evaluate_body("hi", content={"kind": "image", "text": "x"})
        )
        self.assertEqual(status, 422)

    def test_every_problem_carries_a_correlation_id(self) -> None:
        _, body = make_runtime().evaluate(
            auth(), evaluate_body("hi", stage="nope")
        )
        self.assertEqual(body["correlation_id"], "corr-1")


class PolicyResolution(unittest.TestCase):
    def test_an_unresolved_policy_is_a_two_hundred_and_is_audited(self) -> None:
        config = RuntimeConfig(
            unresolved_policy_behavior="block",
            identities={
                CREDENTIAL: WorkloadIdentity(CREDENTIAL, "acme", "support-summarizer")
            },
        )
        runtime = GuardrailsRuntime(PolicyCache.empty(), config)
        status, body = runtime.evaluate(auth(), evaluate_body("hi"))
        self.assertEqual(status, 200)
        self.assertEqual(body["action"], "block")
        self.assertIn("GR-000:platform:policy_unresolved", body["reason_codes"])
        self.assertEqual(len(runtime.audit.records), 1)

    def test_the_deployment_must_declare_its_unresolved_behavior(self) -> None:
        with self.assertRaises(ValueError):
            RuntimeConfig(unresolved_policy_behavior="allow_and_log")


class ContractShape(unittest.TestCase):
    def test_the_decision_carries_every_contract_field(self) -> None:
        _, body = make_runtime().evaluate(auth(), evaluate_body(CARD))
        for key in (
            "action",
            "allowed",
            "enforcement_state",
            "reason_codes",
            "guardrail_category",
            "policy_name",
            "policy_version",
            "evaluator_metadata",
            "correlation_id",
            "audit_id",
            "evaluation_latency_ms",
        ):
            self.assertIn(key, body)

    def test_the_caller_supplied_correlation_id_is_preserved(self) -> None:
        _, body = make_runtime().evaluate(auth(), evaluate_body("hello"))
        self.assertEqual(body["correlation_id"], "corr-1")

    def test_a_correlation_id_is_assigned_when_the_caller_omits_one(self) -> None:
        body = json.loads(evaluate_body("hello"))
        del body["correlation_id"]
        _, decision = make_runtime().evaluate(
            auth(), json.dumps(body).encode("utf-8")
        )
        self.assertTrue(decision["correlation_id"])

    def test_an_asserted_policy_ref_cannot_weaken_the_effective_policy(self) -> None:
        _, body = make_runtime().evaluate(
            auth(),
            evaluate_body(CARD, policy_ref={"name": "something-permissive", "version": 9}),
        )
        self.assertEqual(body["policy_name"], "test-policy")
        self.assertEqual(body["action"], "block")


class Capabilities(unittest.TestCase):
    def test_capabilities_lists_what_this_deployment_serves(self) -> None:
        status, body = make_runtime().capabilities()
        self.assertEqual(status, 200)
        self.assertEqual(body["contract_version"], "v1")
        self.assertIn("sensitive_data/email", body["capabilities"])


if __name__ == "__main__":
    unittest.main()
