"""Real two-gateway governed engine/state matrix with measured model dispatch."""
from pathlib import Path
from uuid import uuid4

import pytest
from cluster_support import ROOT
from evaluator_matrix_support import EvaluatorCluster
from evolution.evaluator_attempt import GovernanceAttempt, operation_root
from evolution.evaluator_cases import ENGINES, control
from evolution.evaluator_gateway_cases import assert_observation, sample
from evolution.evaluator_governance import payload_from_approved, policy_reference
from guardrails.evolution import models


def observed_sample(cluster, tenant, policy, revision, engine, stage, state, *, clean):
    marker = "phase3-gateway-" + uuid4().hex
    prompt, scenario, output = sample(engine, stage, marker, clean=clean)
    cluster.scenario(scenario)
    assert cluster.calls() == {"count": 0, "calls": []}
    item, access = cluster.inventory["tenants"][tenant], cluster.access["tenants"][tenant]
    provider_model = models.ProviderProfile.model_validate_json(
        models.canonical_json_bytes(item["profiles"]["mock"]["provider"]))
    assert provider_model.credential_ref is None
    assert provider_model.digest == models.resource_digest(provider_model)
    provider = provider_model.model_dump(mode="json")
    assert provider in policy["providers"]
    correlation = str(uuid4())
    response = cluster.http.post(access["gateway_url"] + "/v1/chat/completions",
        headers=cluster.headers(access["gateway_token_file"], **{"X-Request-ID": correlation}),
        json={"model": access["model_alias"], "messages": [{"role": "user", "content": prompt}],
              "stream": False, "temperature": 0, "max_tokens": 128}, timeout=4)
    assert len(response.content) <= 256 * 1024
    assert response.headers["x-request-id"] == correlation
    wire = models.strict_json_loads(response.content)
    # Capture counters after this exact turn before any reset or next request.
    calls = cluster.calls()
    with cluster.repo.transaction(tenant) as tx:
        rows = list(tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant AND kind='audit' "
            "AND data->>'event_type'='evaluation' AND data->>'correlation_id'=:correlation LIMIT 4",
            {"tenant": tenant, "correlation": correlation}))
    events = [row.data for row in rows]
    assert_observation(tenant=tenant, workload=item["workload"]["workload_id"],
        actor=item["roles"]["workload"]["principal_id"], policy=policy, revision=revision,
        engine=engine, stage=stage, state=state, clean=clean, prompt=prompt, model_output=output,
        provider_model=provider["model_id"], alias=access["model_alias"], correlation=correlation,
        http_status=response.status_code, response=wire, calls=calls, events=events)
    other = next(value for value in cluster.tenants if value != tenant)
    with cluster.repo.transaction(other) as tx:
        assert all(tx.get("audit", row["decision_id"]) is None for row in events)
    cluster.assert_private_metadata(marker)


@pytest.mark.parametrize("tenant", ("team-a", "team-b"))
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("stage", ("input", "output"))
@pytest.mark.parametrize("state", ("observe", "warn", "redact", "block"))
def test_deployed_gateway_engine_matrix_exact_dispatch_transformation_and_attribution(tenant, engine, stage, state):
    root = operation_root()
    cluster = EvaluatorCluster(engine=engine)
    attempt = journey = primary = route_stage = None
    restoration = "not_needed"
    restored_release = None
    try:
        original, original_policy = cluster.binding(tenant)
        baseline_before, baseline_policy = cluster.baseline(tenant)
        other = next(value for value in cluster.tenants if value != tenant)
        other_before = cluster.binding(other)[0]
        item = cluster.inventory["tenants"][tenant]
        actors = {name: item["roles"][name]["principal_id"] for name in
                  ("tenant_admin", "author", "approver", "operator", "auditor")}
        names = ("mvp/evolution/evaluator_attempt.py", "mvp/evolution/evaluator_governance.py",
                 "mvp/evolution/evaluator_cases.py", "mvp/evolution/evaluator_gateway_cases.py",
                 "mvp/proof/evolution_scenarios.py", "mvp/proof/mock_model.py",
                 "mvp/tests/evolution/e2e/evaluator_matrix_support.py", str(Path(__file__).relative_to(ROOT)))
        attempt = GovernanceAttempt(root, tenant=tenant, original_binding=original,
            original_policy=original_policy, actors=actors, engine=engine, stage=stage, state=state,
            sources={name: ROOT / name for name in names})
        cluster.register_route_governance(attempt)
        journey = cluster.journey(tenant, attempt.operation, journal=attempt)
        runtimes = tuple(row["runtime_id"] for row in item["runtimes"].values())
        selected = journey.content_list(engine) if engine in {"term", "topic"} else None
        rule = control(engine, stage, list_id=selected["list_id"] if selected else None)
        replacements = ()
        if engine == "injection":
            endpoint = cluster.endpoints[tenant]
            replacements = (("providers", endpoint["provider"]), ("resolved_destinations", endpoint["destination"]))
        policy = journey.approve(payload_from_approved(baseline_policy, rule,
            replacements=replacements, lists=(selected,) if selected else ()))
        assert policy_reference(policy) != policy_reference(original_policy)
        assert policy["author_id"] != actors["approver"]
        assert all(row in policy["rules"] for row in baseline_policy["rules"] if row["mandatory"])
        # The native connector caches the operator-installed manifest at startup.
        # Retain the old route and prove the newly approved digest is mounted in
        # an owned replacement Pod before governance can apply that policy.
        route_stage = cluster.gateway_routes()
        route_stage.stage(operation=attempt.operation, tenant=tenant, policy=policy,
            original_policy=original_policy, original_binding=original, inventory=item,
            access=cluster.access, operator_id=actors["operator"])
        cluster.reset_gateway_connections()
        released = journey.release(policy=policy, workload_id=original["workload_id"], state=state,
            expected_revision=original["revision"], runtime_ids=runtimes, label="matrix-release")
        assert len(released["receipts"]) == 2
        cluster.assert_governance_audit(tenant, policy, applied=True)
        # Serialize the shared mock selection and measured turn with all other
        # deployment operations. Credentials were resolved before this lock.
        with cluster.deployment.lock():
            route_stage.assert_active()
            for clean in (True, False):
                observed_sample(cluster, tenant, policy, released["binding_revision"], engine, stage, state, clean=clean)
        assert cluster.baseline(tenant)[0] == baseline_before
        assert cluster.binding(other)[0] == other_before
    except BaseException as error:
        primary = error
        raise
    finally:
        # Restore both the volatile mock selection and the exact owned binding;
        # attempt every cleanup while preserving the original test failure.
        cleanup_error = None
        try:
            with cluster.deployment.lock():
                cluster.scenario("clean")
        except BaseException as error:  # noqa: BLE001 -- Attempt restoration while preserving the primary failure.
            cleanup_error = error
        try:
            handle = journey.last_release if journey is not None else None
            if handle is not None:
                restoration = "failed"
                assert handle["operation"] == attempt.operation and handle["label"] == "matrix-release"
                revision = cluster.owned_binding_revision(tenant, handle)
                restored_release = journey.release(policy=original_policy, workload_id=original["workload_id"],
                    state=original["state"], expected_revision=revision, runtime_ids=runtimes,
                    label="restore-original-binding")
                restoration = "applied"
        except BaseException as error:  # noqa: BLE001 -- Retain unresolved restoration without hiding the primary failure.
            cleanup_error = cleanup_error or error
        try:
            if route_stage is not None:
                # Never remove a newly applied policy's route until the exact
                # original binding is acknowledged again. Unknown governance
                # outcomes leave the route intact for the parent finalizer.
                route_stage.restore(verify_binding=lambda intent: cluster.verify_original_route_binding(
                    intent, journey=journey, restored_release=restored_release))
        except BaseException as error:  # noqa: BLE001 -- Independent parent recovery retains this journal on interruption.
            cleanup_error = cleanup_error or error
        try:
            cluster.close()
        except BaseException as error:  # noqa: BLE001 -- Owned resources must close before journal success.
            cleanup_error = cleanup_error or error
        try:
            if attempt is not None:
                attempt.finish(passed=primary is None and cleanup_error is None, restoration=restoration)
        except BaseException as error:  # noqa: BLE001 -- A journal fault must not replace an earlier failure.
            cleanup_error = cleanup_error or error
        finally:
            if attempt is not None:
                try:
                    attempt.close()
                except BaseException as error:  # noqa: BLE001 -- Retain the first failure while releasing the journal.
                    cleanup_error = cleanup_error or error
        if cleanup_error is not None:
            if primary is None:
                raise cleanup_error
            primary.add_note("Gateway matrix cleanup also failed: " + type(cleanup_error).__name__)
