"""Two deployed LiteLLM gateways, actual shared runtime and volatile mock controls."""

import asyncio
import hashlib
import json
import os
import stat
from pathlib import Path
from uuid import uuid4

import pytest
from cluster_support import ROOT, Cluster, ReadinessObservation, readiness_snapshot
from evolution.diagnostics import SourceCatalog, read_catalog
from evolution.measurements import (
    MATRIX_CASES,
    MATRIX_SCHEMA_V2,
    MATRIX_SOURCES,
    validate_matrix_observation,
)
from evolution.registry import EvidenceError
from guardrails.evolution import models
from guardrails.evolution.errors import STATUS, EvolutionError


def _matrix_sources(case_id):
    if directory := os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR"):
        path = Path(directory)
        info = path.lstat()
        if (not path.is_absolute() or path.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
                or path.name != case_id):
            raise EvidenceError("measurement_path_invalid")
        sources = read_catalog(path.parents[1] / (case_id + ".sources.json"), ROOT)
    else:
        # Direct pytest also freezes these exact named sources before the call.
        sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in MATRIX_SOURCES}
    catalog = SourceCatalog(ROOT, sources)
    for name in MATRIX_SOURCES:
        catalog.path(name)
        catalog.load(name)
    return sources


def _matrix_sample(cluster, tenant, text, rows, side):
    row = {"call_ordinal": len(rows), "tenant_side": side, "http_status": 0,
           "wire_code": None, "elapsed_ms": 0.0, "sample_completed": False,
           "outcome": "other_error", "sample_error": None}
    rows.append(row)
    try:
        result = cluster.sample_complete(tenant, text, observation=row)
    except EvolutionError as error:
        row["outcome"] = "evolution_error"
        row["sample_error"] = error.code if type(error.code) is str and error.code in STATUS else None
        raise
    except asyncio.CancelledError:
        row["outcome"] = "cancelled"
        raise
    else:
        row["outcome"] = "returned"
        return result
    finally:
        # The existing hook proves a canonical correlated envelope. HTTP status
        # and finite code are distinct observations, not acceptance decisions.
        code = row["wire_code"]
        if type(code) is not str or code not in STATUS or row["http_status"] in (0, 200):
            row["wire_code"] = None


def _record_matrix(case_id, rows, sources, readiness):
    side, state = MATRIX_CASES[case_id]
    value = {"schema_version": MATRIX_SCHEMA_V2, "tenant_side": side, "state": state,
             "actual_attempts": len(rows), "completed": sum(row["sample_completed"] for row in rows),
             "rows": rows, "readiness": readiness_snapshot(readiness)}
    validate_matrix_observation(value, case_id, root=ROOT, sources=sources)
    if directory := os.environ.get("EVOLUTION_TEST_EVIDENCE_DIR"):
        path = Path(directory)
        info = path.lstat()
        if (not path.is_absolute() or path.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
                or path.name != case_id):
            raise EvidenceError("measurement_path_invalid")
        raw = json.dumps(value, sort_keys=True, allow_nan=False).encode() + b"\n"
        if len(raw) > 16384:
            raise EvidenceError("measurement_schema_invalid")
        descriptor = os.open(path / "cluster-matrix-observation.json",
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())


def _matrix_cleanup(cluster, case_id, rows, sources, primary_failure, readiness):
    failure = primary_failure
    try:
        _record_matrix(case_id, rows, sources, readiness)
    except BaseException as error:  # noqa: BLE001 -- Attempt all cleanup while preserving the first failure.
        if failure is None:
            failure = error
    if cluster is None:
        if primary_failure is None and failure is not None:
            raise failure
        return
    try:
        cluster.scenario("clean")
    except BaseException as error:  # noqa: BLE001 -- Attempt all cleanup while preserving the first failure.
        if failure is None:
            failure = error
    try:
        cluster.close()
    except BaseException as error:  # noqa: BLE001 -- Attempt all cleanup while preserving the first failure.
        if failure is None:
            failure = error
    if primary_failure is None and failure is not None:
        raise failure


@pytest.mark.parametrize("state", ("observe", "warn", "redact", "block"))
@pytest.mark.parametrize("team", (0, 1), ids=("team-a", "team-b"))
def test_deployed_input_output_state_matrix_exact_dispatch_and_attribution(state, team):
    case_id = next((key for key, value in MATRIX_CASES.items()
                    if value == ("A" if team == 0 else "B", state)), None)
    sources = _matrix_sources(case_id) if case_id is not None else None
    rows, primary_failure = [], None
    readiness = ReadinessObservation() if case_id is not None else None
    cluster = None if case_id is not None else Cluster("deterministic_mock")
    try:
        if case_id is not None:
            cluster = Cluster("deterministic_mock", readiness_observation=readiness)
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
            result = _matrix_sample(cluster, tenant, sensitive, rows, "A" if team == 0 else "B")
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
            result = _matrix_sample(cluster, tenant, prompt, rows, "A" if team == 0 else "B")
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
    except BaseException as error:
        primary_failure = error
        raise
    finally:
        if case_id is not None:
            _matrix_cleanup(cluster, case_id, rows, sources, primary_failure, readiness)
        else:
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
