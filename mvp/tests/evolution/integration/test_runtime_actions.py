"""Every declared text rule action and approved failure mode on the real runtime."""

import copy
import json

import pytest
from guardrails.evolution import models
from guardrails.evolution.plugins import MAX_FINDINGS
from guardrails.evolution.runtime_api import create_app
from https_support import serve_https
from runtime_action_observation import RuntimeActionObservation
from test_evaluation_runtime import (
    fixture_system,
    http,
    refresh,
    request,
    runtime,
)


def action_system(tmp_path, action, *, mandatory=False, failure_behavior="block"):
    # Start with genuinely acknowledged Observe onboarding. With no baseline
    # yet published, replace it with an independently approved exact policy.
    system = fixture_system(tmp_path, baseline=False)
    payload = copy.deepcopy(system["payload"])
    payload["rules"][0].update(action=action, mandatory=mandatory)
    payload["failure_behavior"] = failure_behavior
    service = system["service"]
    with system["repo"].transaction(system["tenant"]) as tx:
        policy = service.governance.submit(
            tx, system["actors"]["author"], "actions", payload
        )
        service.governance.review(
            tx,
            system["actors"]["approver"],
            "actions",
            1,
            outcome="approve",
            compiled_digest=policy["compiled_digest"],
            reason="Reviewed action",
        )
        policy_ref = {
            "tenant_id": system["tenant"],
            "policy_id": "actions",
            "version": 1,
            "digest": policy["compiled_digest"],
        }
        service.governance.publish_baseline(
            tx, system["actors"]["tenant_admin"], [policy_ref], 0
        )
    system.update(policy=policy, policy_ref=policy_ref, payload=payload)
    refresh(system, state="observe", policy_ref=policy_ref)
    return system


def audit_rows(system):
    with system["repo"].transaction(system["tenant"]) as tx:
        return [row for row in tx.list("audit") if row["event_type"] == "evaluation"]


def close(system):
    system["service"].audit.close()
    system["repo"].close()


@pytest.mark.parametrize("stage", ["input", "output"])
@pytest.mark.parametrize("action", ["allow", "warn", "redact", "block"])
def test_declared_rule_actions_on_each_real_https_text_stage(tmp_path, action, stage):
    with RuntimeActionObservation(action, stage) as observation:
        system = action_system(tmp_path, action)
        # Explicit allow findings remain allow even under Block. Other actions use
        # their corresponding enforcing state and must preserve would_be_action.
        refresh(system, state="block" if action == "allow" else action)
        sensitive = "Contact matrix-private@example.test."
        try:
            with (
                serve_https(
                    lambda: create_app(service=runtime(system)), tmp_path / "tls"
                ) as server,
                http(server, system["tokens"]["workload"]) as client,
            ):
                assert client.get("/health/ready").status_code == 200
                context = None
                if stage == "output":
                    opened = observation.post(
                        client, "input", json=request(system, text="A clean input.")
                    )
                    assert opened.status_code == 200
                    initial = models.Decision.model_validate_json(opened.content)
                    assert initial.action == initial.would_be_action == "allow"
                    assert initial.findings == ()
                    context = initial.context_id
                payload = request(system, text=sensitive, stage=stage, context_id=context)
                response = observation.post(client, stage, json=payload)
                assert response.status_code == 200, (
                    response.json().get("error", {}).get("code")
                )
                decision = models.Decision.model_validate_json(response.content)
                assert decision.action == decision.would_be_action == action
                assert (
                    decision.stage == stage and decision.request_id == payload["request_id"]
                )
                assert decision.policy.digest == system["policy_ref"]["digest"]
                assert decision.binding_revision == system["revision"]
                assert decision.audit_status == "persisted" and decision.evaluation_ms < 500
                assert decision.evaluator_revisions == ("pii-v2",)
                assert decision.reason_codes == ("pii_detected",)
                assert len(decision.findings) == 1
                finding = decision.findings[0]
                assert (
                    finding.rule_id,
                    finding.category,
                    finding.part_id,
                    finding.start,
                    finding.end,
                ) == ("email", "email", "content", 8, 35)
                if action == "redact":
                    assert decision.transformed_parts == (
                        models.ContentPart(
                            part_id="content",
                            kind="text",
                            role="assistant" if stage == "output" else "user",
                            text="Contact [REDACTED:email].",
                        ),
                    )
                else:
                    assert decision.transformed_parts is None
                events = audit_rows(system)
                assert len(events) == (2 if stage == "output" else 1)
                event = next(
                    row for row in events if row["decision_id"] == decision.decision_id
                )
                assert event["action"] == action and event["metadata"] == {"stage": stage}
                assert event["actor_id"] == system["actors"]["workload"].principal_id
                assert (
                    event["tenant_id"] == system["tenant"]
                    and event["workload_id"] == "chat"
                )
                assert event["policy_digest"] == decision.policy.digest
                assert event["binding_revision"] == decision.binding_revision
                assert event["reason_codes"] == ["pii_detected"]
                with system["repo"].transaction(system["tenant"]) as tx:
                    persisted = [
                        row.data
                        for row in tx.execute(
                            "SELECT data FROM evolution.records WHERE tenant_id=:tenant",
                            {"tenant": system["tenant"]},
                        )
                    ]
                assert ("matrix-private@example.test" in json.dumps(persisted)) is False
        finally:
            close(system)


@pytest.mark.parametrize(
    "mandatory", [False, True], ids=["nonmandatory-observe", "mandatory-block"]
)
def test_approved_observe_failure_is_explicit_and_never_weakens_mandatory(
    tmp_path, mandatory
):
    system = action_system(
        tmp_path, "block", mandatory=mandatory, failure_behavior="observe"
    )
    # Real valid findings exceed the worker's bounded result contract. Its
    # unchanged deadline may win before the result cap; either explicit failure
    # must obey the same approved policy without returning partial findings.
    sensitive = "a@b.co " * (MAX_FINDINGS + 1)
    assert len(sensitive.encode("utf-8")) < 65536
    try:
        with (
            serve_https(
                lambda: create_app(service=runtime(system)), tmp_path / "tls"
            ) as server,
            http(server, system["tokens"]["workload"]) as client,
        ):
            assert client.get("/health/ready").status_code == 200
            payload = request(system, text=sensitive)
            response = client.post("/v2/evaluations", json=payload)
            assert response.status_code == 200, (
                response.json().get("error", {}).get("code")
            )
            decision = models.Decision.model_validate_json(response.content)
            expected = "block" if mandatory else "allow"
            assert decision.action == decision.would_be_action == expected
            assert decision.reason_codes in {
                ("inspection_incomplete",),
                ("evaluation_timeout",),
            }
            assert decision.findings == () and decision.transformed_parts is None
            assert decision.policy.digest == system["policy_ref"]["digest"]
            assert decision.audit_status == "persisted" and decision.evaluation_ms < 500
            event = next(
                row
                for row in audit_rows(system)
                if row["decision_id"] == decision.decision_id
            )
            assert event["action"] == expected
            assert event["reason_codes"] == list(decision.reason_codes)
            assert event["policy_digest"] == decision.policy.digest
            assert event["actor_id"] == system["actors"]["workload"].principal_id
            with system["repo"].transaction(system["tenant"]) as tx:
                context = tx.get(
                    "evaluation_contexts", decision.context_id, required=True
                )
                assert context["state"] == ("blocked" if mandatory else "open")
                persisted = [
                    row.data
                    for row in tx.execute(
                        "SELECT data FROM evolution.records WHERE tenant_id=:tenant",
                        {"tenant": system["tenant"]},
                    )
                ]
            assert ("a@b.co" in json.dumps(persisted)) is False
    finally:
        close(system)
