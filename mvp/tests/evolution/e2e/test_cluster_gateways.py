"""Two deployed LiteLLM gateways, actual shared runtime and volatile mock controls."""

from uuid import uuid4

import pytest
from cluster_support import Cluster
from guardrails.evolution import models


@pytest.mark.parametrize("state", ("observe", "warn", "redact", "block"))
@pytest.mark.parametrize("team", (0, 1), ids=("team-a", "team-b"))
def test_deployed_input_output_state_matrix_exact_dispatch_and_attribution(state, team):
    cluster = Cluster("deterministic_mock")
    try:
        tenant = cluster.tenants[team]
        revision = cluster.release(tenant, state)
        marker = "cluster-" + uuid4().hex
        email = marker + "@example.test"
        sensitive = "Contact " + email + " or +15551234567."
        expected = {
            "observe": "allow",
            "warn": "warn",
            "redact": "redact",
            "block": "block",
        }[state]
        cluster.scenario("clean")
        if state == "block":
            response, correlation = cluster.send(tenant, sensitive)
            assert response.status_code == 403
            assert (
                models.strict_json_loads(response.content)["error"]["code"]
                == "forbidden"
            )
            assert cluster.calls()["count"] == 0
            events = cluster.evaluation_events(tenant, correlation)
            assert len(events) == 1
            assert (
                events[0]["action"] == "block"
                and events[0]["metadata"]["stage"] == "input"
            )
            assert (
                events[0]["policy_digest"]
                == cluster.inventory["tenants"][tenant]["policy_ref"]["digest"]
            )
        else:
            result = cluster.sample_complete(tenant, sensitive)
            cluster.assert_attribution(
                tenant, result, revision, input_action=expected, output_action="allow"
            )
            calls = cluster.calls()
            assert calls["count"] == 1
            assert len(calls["calls"]) == 1
            inspected_input = calls["calls"][0]["messages"]
            matches = inspected_input == [
                {
                    "role": "user",
                    "content": "Contact [REDACTED] or [REDACTED]."
                    if state == "redact"
                    else sensitive,
                }
            ]
            assert matches, (
                "model received bytes different from the inspected transformation"
            )
            clean_output = result["content"] == "A clean mock response."
            assert clean_output
        cluster.scenario("sensitive")
        prompt = "Describe the sky in one sentence " + marker
        if state == "block":
            response, correlation = cluster.send(tenant, prompt)
            assert response.status_code == 403
            assert (
                models.strict_json_loads(response.content)["error"]["code"]
                == "forbidden"
            )
            assert cluster.calls()["count"] == 1
            events = cluster.evaluation_events(tenant, correlation)
            assert len(events) == 2
            assert {(row["metadata"]["stage"], row["action"]) for row in events} == {
                ("input", "allow"),
                ("output", "block"),
            }
            released = (
                b"proof@example.test" in response.content
                or b"+15551234567" in response.content
            )
            assert not released, "blocked model content escaped in the gateway error"
        else:
            result = cluster.sample_complete(tenant, prompt)
            cluster.assert_attribution(
                tenant, result, revision, input_action="allow", output_action=expected
            )
            matches = result["content"] == (
                "Contact [REDACTED] or [REDACTED]."
                if state == "redact"
                else "Contact proof@example.test or +15551234567."
            )
            assert matches, "gateway output differs from the inspected transformation"
            assert cluster.calls()["count"] == 1
        cluster.assert_private(
            (marker, email, prompt, "proof@example.test", "+15551234567")
        )
    finally:
        try:
            cluster.scenario("clean")
        finally:
            cluster.close()


def test_deployed_tenant_role_and_unsupported_surface_denials_have_zero_model_dispatch():
    cluster = Cluster("deterministic_mock")
    try:
        first, second = cluster.tenants
        revisions = {
            first: cluster.release(first, "block"),
            second: cluster.release(second, "observe"),
        }
        assert (
            cluster.access["tenants"][first]["gateway_url"]
            != cluster.access["tenants"][second]["gateway_url"]
        )
        assert (
            cluster.access["tenants"][first]["model_alias"]
            != cluster.access["tenants"][second]["model_alias"]
        )
        assert (
            cluster.inventory["tenants"][first]["policy_ref"]["tenant_id"]
            != cluster.inventory["tenants"][second]["policy_ref"]["tenant_id"]
        )
        marker = "isolated-" + uuid4().hex
        sensitive = marker + "@example.test"
        cluster.scenario("clean")
        response, _ = cluster.send(first, sensitive)
        assert response.status_code == 403
        assert cluster.calls()["count"] == 0
        result = cluster.sample_complete(second, sensitive)
        cluster.assert_attribution(
            second,
            result,
            revisions[second],
            input_action="allow",
            output_action="allow",
        )
        assert cluster.calls()["count"] == 1
        for tenant, foreign in ((first, second), (second, first)):
            cluster.scenario("clean")
            response, _ = cluster.send(
                tenant,
                marker,
                token_file=cluster.access["tenants"][foreign]["gateway_token_file"],
            )
            assert response.status_code == 401
            assert cluster.calls()["count"] == 0
            for extra, expected_status, expected_code in (
                ({"stream": True}, 400, "unsupported_surface"),
                ({"model": "unapproved-alias"}, 403, "provider_denied"),
                ({"tools": []}, 400, "unsupported_surface"),
                (
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": "https://denied.invalid/" + marker
                                        },
                                    }
                                ],
                            }
                        ]
                    },
                    400,
                    "unsupported_surface",
                ),
                ({"tenant_id": foreign}, 403, "forbidden"),
            ):
                response, _ = cluster.send(tenant, marker, extra=extra)
                assert response.status_code == expected_status
                code_matches = (
                    response.json().get("error", {}).get("code") == expected_code
                )
                assert code_matches, "gateway returned an unexpected finite error code"
                assert cluster.calls()["count"] == 0
            value = cluster.inventory["tenants"][tenant]
            body = {
                "policy": value["policy_ref"],
                "workload_ids": ["chat"],
                "stage": "canary",
                "state": "observe",
                "expected_revisions": {"chat": revisions[tenant]},
                "reason": "Role boundary attempt",
            }
            denied = cluster.management(
                tenant, "POST", "/releases", body=body, role="author", expected=403
            )
            assert denied["error"]["code"] == "forbidden"
            disabled = cluster.management(
                tenant,
                "POST",
                "/releases",
                body={
                    **body,
                    "state": "disabled",
                    "reason": "Minimum Observe floor probe",
                },
                role="operator",
                expected=409,
            )
            assert disabled["error"]["code"] == "floor_conflict"
            after = cluster.management(tenant, "GET", "/workloads/chat")
            assert after["desired_binding"]["binding_revision"] == revisions[tenant]
            assert after["applied_binding"]["binding_revision"] == revisions[tenant]
            assert cluster.calls()["count"] == 0
            response = cluster.http.get(
                cluster.access["management_url"]
                + "/v2/tenants/"
                + foreign
                + "/workloads/chat",
                headers=cluster.headers(value["roles"]["auditor"]["token_file"]),
            )
            assert response.status_code == 404
        cluster.assert_private((marker, sensitive))
    finally:
        try:
            cluster.scenario("clean")
        finally:
            cluster.close()


@pytest.mark.parametrize("scenario", ("tool_calls", "refusal", "multiple", "multipart"))
def test_deployed_unsupported_model_output_is_never_released(scenario):
    cluster = Cluster("deterministic_mock")
    try:
        marker = "unsupported-" + uuid4().hex
        for tenant in cluster.tenants:
            cluster.release(tenant, "observe")
            cluster.scenario(scenario)
            response, correlation = cluster.send(tenant, marker)
            assert response.status_code in {422, 503}
            assert cluster.calls()["count"] == 1
            value = models.strict_json_loads(response.content)
            assert set(value) == {"error"}
            assert value["error"]["code"] in {
                "unsupported_surface",
                "inspection_incomplete",
            }
            events = cluster.evaluation_events(tenant, correlation)
            assert len(events) == 1
            assert events[0]["metadata"]["stage"] == "input"
            assert events[0]["action"] == "allow"
        cluster.assert_private((marker, "Synthetic refusal", "Synthetic text"))
    finally:
        try:
            cluster.scenario("clean")
        finally:
            cluster.close()
