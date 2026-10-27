"""Positive Disabled states on fresh governed tenants, actual runtime and PostgreSQL.

The classifier endpoint uses a protocol-only readiness response and rejects every
inference request. This proves zero dispatch, not deployed model readiness or
classifier quality. Existing team-a/team-b baselines are never modified.
"""
import asyncio
import json
from uuid import uuid4

import pytest
from evolution.evaluator_cases import ENGINES, content, control
from guardrails.evolution import models
from guardrails.evolution.classifier import ClassifierEndpoint, ClassifierEndpointConfig
from guardrails.evolution.classifier_contract import (
    MODEL_ID,
    WEIGHTS_SHA256,
)
from guardrails.evolution.plugins import EvaluatorRegistry
from guardrails.evolution.runtime_api import create_app
from https_support import serve_https
from test_evaluation_runtime import fixture_system, http, refresh, request, runtime

from mvp.tests.evolution.disabled_observation import ReadinessOnly


def create_resource(system, family, body, role="tenant_admin"):
    with system["repo"].transaction(system["tenant"]) as tx:
        value, status = system["service"].dispatch(tx, system["actors"][role], "POST", [family], body, {}, None)
    assert status == 201 and value["tenant_id"] == system["tenant"]
    return value


def prepare_disabled(system, engine, stage):
    payload = dict(system["payload"])
    readiness = ReadinessOnly()
    endpoints = {}
    list_id = None
    if engine in {"term", "topic"}:
        selected = create_resource(system, "lists", {"kind": "content", "allow_enabled": False,
            "allow": [], "deny": ["term:forbidden-fixture" if engine == "term" else "topic:finance"]}, "author")
        list_id = selected["list_id"]
        payload["list_refs"] = [*payload["list_refs"], {"id": list_id, "version": 1, "digest": selected["digest"]}]
    if engine == "injection":
        destination = create_resource(system, "destinations", {"name": "disabled-test", "host": "classifier.example.test",
            "port": 8443, "path_prefix": "/v1/classifier", "cidrs": ["10.96.0.35/32"],
            "zone": "local-shared", "purpose": "evaluation", "tls_ca_ref": "synthetic-ca"})
        provider = create_resource(system, "providers", {"kind": "evaluator", "vendor": "local_classifier",
            "model_id": MODEL_ID, "artifact_digest": WEIGHTS_SHA256, "destination_id": destination["destination_id"],
            "zone": "local-shared", "credential_ref": "synthetic-operator"})
        # Management safe_view deliberately omits this operator-only reference.
        # Reattach only our exact submitted value and authenticate the unchanged
        # immutable digest before constructing the private endpoint config.
        assert provider.get("credential_ref", "synthetic-operator") == "synthetic-operator"
        provider = {**provider, "credential_ref": "synthetic-operator"}
        provider_model = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(provider))
        assert provider_model.digest == models.resource_digest(provider_model)
        allowed = create_resource(system, "lists", {"kind": "provider", "allow_enabled": True,
            "allow": [system["provider"].provider_id, provider["provider_id"]], "deny": []}, "author")
        destinations = create_resource(system, "lists", {"kind": "destination", "allow_enabled": True,
            "allow": [destination["destination_id"]], "deny": []}, "author")
        residency = create_resource(system, "residency", {"provider_lists": [allowed["list_id"]],
            "destination_lists": [destinations["list_id"]]})
        payload["provider_refs"] = [*payload["provider_refs"], {"id": provider["provider_id"], "version": 1, "digest": provider["digest"]}]
        payload["destination_refs"] = [{"id": destination["destination_id"], "version": 1, "digest": destination["digest"]}]
        payload["list_refs"] = [{"id": entry["list_id"], "version": 1, "digest": entry["digest"]}
                                for entry in (allowed, destinations)]
        payload["residency_ref"] = {"id": residency["residency_id"], "version": 1, "digest": residency["digest"]}
        config = ClassifierEndpointConfig(provider=provider_model,
            destination=models.Destination.model_validate_json(models.canonical_json_bytes(destination)),
            runtime_zone="local-shared", language_profile="en", bearer_token="synthetic-readiness-token-0000000000")
        endpoints[system["tenant"]] = ClassifierEndpoint(config, readiness)
    payload["rules"] = [control(engine, stage, list_id=list_id)]
    registry = EvaluatorRegistry(tenant_ids=(system["tenant"],), workers=2, max_queued_jobs=4,
                                 classifier_endpoints=endpoints)
    system["service"].bindings.capability_check = registry.activation_check
    system["distributor"].capability_check = registry.validate_declared
    with system["repo"].transaction(system["tenant"]) as tx:
        candidate = system["service"].governance.submit(tx, system["actors"]["author"], "disabled-engine", payload)
        review = system["service"].governance.review(tx, system["actors"]["approver"], "disabled-engine", 1,
            outcome="approve", compiled_digest=candidate["compiled_digest"], reason="Independent isolated Disabled review")
        assert review["actor_id"] != candidate["author_id"]
        policy_ref = {"tenant_id": system["tenant"], "policy_id": "disabled-engine", "version": 1,
                      "digest": candidate["compiled_digest"]}
        publication = system["service"].governance.publish_baseline(tx, system["actors"]["tenant_admin"], [policy_ref], 0)
        assert publication["revision"] == 1
        baseline = tx.get("baseline", "current", required=True)
    # These are genuine signed-store applications, not two live runtime
    # processes. Live capability readiness is checked in the HTTPS child below.
    refresh(system, state="observe", policy_ref=policy_ref)
    refresh(system, state="disabled", policy_ref=policy_ref)
    with system["repo"].transaction(system["tenant"]) as tx:
        receipts = [row for row in tx.list("receipts") if row["binding_revision"] == system["revision"]]
        assert len(receipts) == 2 and {row["runtime_id"] for row in receipts} == set(system["runtime_ids"])
        assert all(row["digest"] == policy_ref["digest"] and row["health"] == "ready" for row in receipts)
    system["policy_ref"] = policy_ref
    return registry, readiness, baseline


@pytest.mark.parametrize("scope", ("isolated-a", "isolated-b"))
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("stage", ("input", "output"))
def test_governed_disabled_has_zero_dispatch_and_exact_audit(tmp_path, scope, engine, stage):
    system = fixture_system(tmp_path, baseline=False)
    registry = None
    primary = None
    marker = scope + "-" + uuid4().hex
    try:
        # The existing fixture owns initial tenant/principal creation. Record the
        # completed fixture scope explicitly; later configuration, approval and
        # binding transitions use their production governed service methods.
        with system["repo"].transaction(system["tenant"]) as tx:
            assert tx.get("tenants", system["tenant"], required=True)["status"] == "active"
            system["service"].audit.append(tx, "tenant_created", metadata={
                "resource_type": "tenant", "resource_id": system["tenant"],
                "operation": "isolated-disabled-fixture", "status": "created"})
        registry, readiness, baseline = prepare_disabled(system, engine, stage)
        for name in ("evaluate", "evaluate_content_lists", "transform"):
            original = getattr(registry, name)
            async def observed(*args, _name=name, _original=original, **kwargs):
                readiness.counters.increment(_name)
                return await _original(*args, **kwargs)
            setattr(registry, name, observed)
        with serve_https(lambda: create_app(service=runtime(system, registry=registry)), tmp_path / "tls") as server, http(server, system["tokens"]["workload"]) as client:
            ready = client.get("/health/ready")
            assert ready.status_code == 200 and ready.json()["ready"] is True
            context = None
            if stage == "output":
                initial = client.post("/v2/evaluations", json=request(system, text="Clean input."))
                assert initial.status_code == 200 and initial.json()["reason_codes"] == ["disabled"]
                context = initial.json()["context_id"]
            value = request(system, stage=stage, context_id=context, parts=[content(engine, stage, marker)])
            response = client.post("/v2/evaluations", json=value)
            assert response.status_code == 200
            decision = models.Decision.model_validate_json(response.content)
            assert decision.action == decision.would_be_action == "allow"
            assert decision.reason_codes == ("disabled",)
            assert decision.findings == decision.evaluator_revisions == () and decision.transformed_parts is None
            assert decision.policy.model_dump(mode="json") == system["policy_ref"]
            assert decision.binding_revision == system["revision"] and decision.audit_status == "persisted"
            counts = readiness.counters.snapshot()
            assert all(counts[name] == 0 for name in ("evaluate", "evaluate_content_lists", "transform", "dispatch"))
            if engine == "injection":
                assert counts["readiness"] > 0
            with system["repo"].transaction(system["tenant"]) as tx:
                event = tx.get("audit", decision.decision_id, required=True)
                assert event["actor_id"] == system["actors"]["workload"].principal_id
                assert event["policy_digest"] == decision.policy.digest and event["binding_revision"] == system["revision"]
                assert event["action"] == "allow" and event["metadata"]["stage"] == stage
                assert event["raw_content_retained"] is False and event["correlation_id"] == value["correlation_id"]
                assert tx.get("baseline", "current", required=True) == baseline
                rows = list(tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant", {"tenant": system["tenant"]}))
                assert marker not in json.dumps([row.data for row in rows])
                approvals = tx.list("approvals")
                assert any(row["actor_id"] == system["actors"]["approver"].principal_id and
                           row["compiled_digest"] == decision.policy.digest for row in approvals)
            with system["repo"].transaction("team-a" if scope == "isolated-a" else "team-b") as tx:
                assert tx.get("audit", decision.decision_id) is None
    except BaseException as error:
        primary = error
        raise
    finally:
        # Keep the immutable governance/audit history, close access through the
        # public scoped management command, and revoke only this fresh scope.
        try:
            with system["repo"].transaction(system["tenant"]) as tx:
                workload = tx.get("workloads", "chat", required=True)
                system["service"].dispatch(tx, system["actors"]["tenant_admin"], "PATCH", ["workloads", "chat"],
                    {"status": "suspended"}, {}, workload["revision"])
                credentials = tx.list("credentials")
                for row in credentials:
                    system["service"].dispatch(tx, system["actors"]["tenant_admin"], "POST",
                        ["credentials", row["credential_id"], "revoke"], {"reason": "Isolated Disabled proof complete"}, {}, None)
                assert tx.get("workloads", "chat", required=True)["status"] == "suspended"
                assert all(row["revoked_at"] is not None for row in tx.list("credentials"))
                events = tx.list("audit")
                assert any(row["event_type"] == "tenant_created" and
                           row["metadata"].get("operation") == "isolated-disabled-fixture" for row in events)
                assert any(row["event_type"] == "workload_updated" for row in events)
                assert sum(row["event_type"] == "credential_revoke" for row in events) == len(credentials)
        except BaseException as cleanup_error:
            if primary is None:
                raise
            primary.add_note("Isolated Disabled scope cleanup also failed: " + type(cleanup_error).__name__)
        finally:
            if registry is not None and not registry._closed:
                asyncio.run(registry.close())
            system["service"].audit.close()
            system["repo"].close()
