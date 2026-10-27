"""Six local engines through actual governed shared runtime; no model shortcuts."""
from pathlib import Path
from uuid import uuid4

import pytest
from cluster_support import ROOT
from evaluator_matrix_support import EvaluatorCluster
from evolution.evaluator_attempt import GovernanceAttempt, operation_root
from evolution.evaluator_cases import ENGINES, STATES, content, control, expected_action
from evolution.evaluator_governance import payload_from_approved, policy_reference


@pytest.mark.parametrize("tenant", ("team-a", "team-b"))
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("stage", ("input", "output"))
@pytest.mark.parametrize("state", STATES)
def test_deployed_governed_evaluator_matrix(tenant, engine, stage, state):
    operations = operation_root()
    cluster = EvaluatorCluster(engine=engine)
    original = approved = None
    changed_revision = None
    journey = attempt = None
    primary = None
    restoration = "not_needed"
    try:
        original, original_policy = cluster.binding(tenant)
        other = next(value for value in cluster.tenants if value != tenant)
        other_before = cluster.binding(other)[0]
        baseline_before, base = cluster.baseline(tenant)
        item = cluster.inventory["tenants"][tenant]
        actors = {name: item["roles"][name]["principal_id"] for name in
                  ("tenant_admin", "author", "approver", "operator", "auditor")}
        source_names = ("mvp/evolution/evaluator_attempt.py", "mvp/evolution/evaluator_governance.py",
                        "mvp/evolution/evaluator_cases.py", "mvp/tests/evolution/e2e/evaluator_matrix_support.py",
                        str(Path(__file__).relative_to(ROOT)))
        attempt = GovernanceAttempt(operations, tenant=tenant, original_binding=original,
            original_policy=original_policy, actors=actors, engine=engine, stage=stage, state=state,
            sources={name: ROOT / name for name in source_names})
        operation = attempt.operation
        journey = cluster.journey(tenant, operation, journal=attempt)
        target_ids = tuple(row["runtime_id"] for row in item["runtimes"].values())
        content_list = journey.content_list(engine) if engine in {"term", "topic"} else None
        rule = control(engine, stage, list_id=content_list["list_id"] if content_list else None)
        replacements = ()
        if engine == "injection":
            endpoint = cluster.endpoints[tenant]
            replacements = (("providers", endpoint["provider"]), ("resolved_destinations", endpoint["destination"]))
        approved = journey.approve(payload_from_approved(base, rule,
            replacements=replacements, lists=(content_list,) if content_list else ()))
        assert approved["author_id"] != journey.actors["approver"]
        assert all(value in approved["rules"] for value in base["rules"] if value["mandatory"])
        if state == "disabled":
            # Current team baselines mandate at least Observe. This is an
            # explicit impossible-combination case, not positive Disabled proof.
            refused = journey._request("POST", "/releases", role="operator", expected=409,
                label="disabled-floor-refusal", body={"policy": policy_reference(approved),
                    "workload_ids": [original["workload_id"]], "stage": "canary", "state": "disabled",
                    "expected_revisions": {original["workload_id"]: original["revision"]},
                    "reason": "Verify retained mandatory Observe floor"})
            assert refused["error"]["code"] == "floor_conflict"
            assert cluster.binding(tenant)[0] == original
            assert cluster.binding(other)[0] == other_before
            assert cluster.baseline(tenant)[0] == baseline_before
            cluster.assert_governance_audit(tenant, approved, applied=False)
            return
        release = journey.release(policy=approved, workload_id=original["workload_id"], state=state,
            expected_revision=original["revision"], runtime_ids=target_ids, label="matrix-release")
        changed_revision = release["binding_revision"]
        assert len(release["receipts"]) == 2
        cluster.assert_governance_audit(tenant, approved, applied=True)
        marker = "p3-canary-" + uuid4().hex
        context = None
        if stage == "output":
            initial, initial_request = cluster.evaluate(tenant, content(engine, "input", marker, clean=True), stage="input")
            assert initial.action == "allow" and not initial.findings
            assert initial.policy.model_dump(mode="json") == policy_reference(approved)
            context = initial.context_id
            cluster.assert_exact_audit(tenant, initial, initial_request)
        target, request = cluster.evaluate(tenant, content(engine, stage, marker), stage=stage, context_id=context)
        assert target.policy.model_dump(mode="json") == policy_reference(approved)
        assert target.binding_revision == changed_revision and target.snapshot_generation >= 1
        assert target.action == expected_action(engine, state)
        findings = [value for value in target.findings if value.rule_id == rule["rule_id"]]
        assert len(findings) >= 1 and len(findings) == len(target.findings)
        assert all(value.evaluator_revision == rule["evaluator_revision"] for value in findings)
        assert target.evaluator_revisions and rule["evaluator_revision"] in target.evaluator_revisions
        assert target.would_be_action == "block"
        if target.action == "redact":
            assert len(target.transformed_parts) == 1
            before = request["parts"][0]["text"]
            after = target.transformed_parts[0].text
            category = {"pii": "payment_card", "secret": "aws_access_key", "term": "term"}[engine]
            assert after != before and "[REDACTED:" + category + "]" in after and marker in after
            assert all(value not in after for value in ("4111111111111111", "AKIAABCDEFGHIJKLMNOP", "forbidden-fixture"))
        else:
            assert target.transformed_parts is None
        cluster.assert_exact_audit(tenant, target, request)
        assert cluster.binding(other)[0] == other_before
        assert cluster.baseline(tenant)[0] == baseline_before
        cluster.assert_private_metadata(marker)
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup_error = None
        try:
            handle = journey.last_release if journey is not None else None
            if handle is not None:
                assert handle["label"] == "matrix-release" and handle["operation"] == operation
                restoration = "failed"
                owned_revision = cluster.owned_binding_revision(tenant, handle)
                journey.release(policy=original_policy, workload_id=original["workload_id"],
                    state=original["state"], expected_revision=owned_revision, runtime_ids=target_ids,
                    label="restore-original-binding")
                restoration = "applied"
        except BaseException as error:  # noqa: BLE001 -- Preserve restoration failure while finishing owned cleanup.
            cleanup_error = error
        try:
            cluster.close()
        except BaseException as error:  # noqa: BLE001 -- Owned resources must close before journal success.
            cleanup_error = cleanup_error or error
        try:
            if attempt is not None:
                attempt.finish(passed=primary is None and cleanup_error is None, restoration=restoration)
        except BaseException as error:  # noqa: BLE001 -- The journal cannot replace the original test/cleanup failure.
            cleanup_error = cleanup_error or error
        finally:
            if attempt is not None:
                try:
                    attempt.close()
                except BaseException as error:  # noqa: BLE001 -- Attempt every cleanup without masking its predecessor.
                    cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            if primary is None:
                raise cleanup_error
            primary.add_note("Governed matrix cleanup also failed: " + type(cleanup_error).__name__)
