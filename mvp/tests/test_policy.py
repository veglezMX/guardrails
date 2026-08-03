"""Policy schema validation — ADR-0009's validation criteria, as tests."""

from __future__ import annotations

import unittest

from support import ALL_FAILURE_KEYS, make_policy, policy_doc

from guardrails.errors import PolicyValidationError
from guardrails.policy import FAILURE_MODES, parse_policy
from guardrails.store import PolicyCache


class PolicyValidation(unittest.TestCase):
    def test_every_missing_failure_key_is_named_in_the_rejection(self) -> None:
        for mode in FAILURE_MODES:
            behavior = {k: v for k, v in ALL_FAILURE_KEYS.items() if k != mode}
            with self.subTest(mode=mode):
                with self.assertRaises(PolicyValidationError) as caught:
                    parse_policy(policy_doc(failure_behavior=behavior))
                self.assertIn(mode, str(caught.exception))

    def test_no_failure_behavior_section_at_all_is_rejected(self) -> None:
        doc = policy_doc()
        del doc["failure_behavior"]
        with self.assertRaises(PolicyValidationError):
            parse_policy(doc)

    def test_undocumented_behavior_for_a_mode_is_rejected(self) -> None:
        behavior = dict(ALL_FAILURE_KEYS)
        behavior["policy_unresolved"] = "allow_and_log"
        with self.assertRaises(PolicyValidationError) as caught:
            parse_policy(policy_doc(failure_behavior=behavior))
        self.assertIn("policy_unresolved", str(caught.exception))

    def test_vendor_name_in_an_evaluator_selection_is_rejected(self) -> None:
        for field, value in (
            ("family", "bedrock_pii"),
            ("capability", "bedrock-email"),
        ):
            selection = {
                "family": "sensitive_data",
                "capability": "email",
                "action": "block",
            }
            selection[field] = value
            with self.subTest(field=field):
                with self.assertRaises(PolicyValidationError) as caught:
                    parse_policy(policy_doc(evaluators=[selection]))
                self.assertIn("vendor", str(caught.exception).lower())

    def test_vendor_name_in_params_is_rejected(self) -> None:
        with self.assertRaises(PolicyValidationError):
            parse_policy(
                policy_doc(
                    evaluators=[
                        {
                            "family": "organization_rules",
                            "capability": "blocked_terms",
                            "action": "block",
                            "params": {"engine": "aws bedrock guardrails"},
                        }
                    ]
                )
            )

    def test_action_outside_the_active_vocabulary_is_rejected(self) -> None:
        with self.assertRaises(PolicyValidationError) as caught:
            parse_policy(
                policy_doc(
                    evaluators=[
                        {
                            "family": "sensitive_data",
                            "capability": "email",
                            # Named in the documents as later-phase scope, so it
                            # must not be selectable today.
                            "action": "require_approval",
                        }
                    ]
                )
            )
        self.assertIn("require_approval", str(caught.exception))

    def test_stage_outside_the_reserved_vocabulary_is_rejected(self) -> None:
        with self.assertRaises(PolicyValidationError):
            parse_policy(policy_doc(stages=["input", "tool_call"]))

    def test_a_policy_that_omits_audit_behavior_retains_nothing(self) -> None:
        doc = policy_doc()
        del doc["audit_behavior"]
        self.assertFalse(parse_policy(doc).retain_content)

    def test_policy_carries_no_tenant_workload_or_enforcement_mode(self) -> None:
        policy = make_policy()
        for absent in ("tenant_id", "workload_id", "enforcement_state"):
            self.assertFalse(hasattr(policy, absent))

    def test_only_the_stage_configured_evaluators_run(self) -> None:
        policy = make_policy(
            evaluators=[
                {
                    "family": "sensitive_data",
                    "capability": "email",
                    "stages": ["output"],
                    "action": "redact",
                }
            ]
        )
        self.assertEqual(policy.selections_for("input"), ())
        self.assertEqual(len(policy.selections_for("output")), 1)


class PolicyImmutability(unittest.TestCase):
    def test_republishing_the_same_name_and_version_with_new_content_is_refused(
        self,
    ) -> None:
        cache = PolicyCache.empty()
        cache.put_policy(make_policy())
        changed = make_policy(
            evaluators=[
                {"family": "sensitive_data", "capability": "phone", "action": "block"}
            ]
        )
        with self.assertRaises(PolicyValidationError) as caught:
            cache.put_policy(changed)
        self.assertIn("new version", str(caught.exception))

    def test_republishing_identical_content_is_accepted(self) -> None:
        cache = PolicyCache.empty()
        cache.put_policy(make_policy())
        cache.put_policy(make_policy())  # idempotent redistribution

    def test_a_new_version_coexists_with_the_old_one(self) -> None:
        cache = PolicyCache.empty()
        cache.put_policy(make_policy())
        cache.put_policy(make_policy(version=2))
        self.assertEqual(len(cache.policies), 2)


if __name__ == "__main__":
    unittest.main()
