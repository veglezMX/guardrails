"""Actual Phase3 deployment access; never bootstraps or switches profiles."""
from __future__ import annotations

import os
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from cluster_support import ROOT, Cluster, load_module
from evolution.evaluator_cases import ENGINES
from evolution.evaluator_governance import (
    GovernanceJourney,
    checked_policy,
    owned_revision,
)
from evolution.seed import private_read
from guardrails.evolution import models
from guardrails.evolution.storage import TenantRepository


class EvaluatorCluster(Cluster):
    """Retain original credential/owner/TLS checks without the auto-platform path."""
    def __init__(self, *, engine="injection"):
        assert type(engine) is str and engine in ENGINES, "unknown governed evaluator engine"
        self._access_deadline = time.monotonic() + 30
        self.requires_classifier = engine == "injection"
        self.state = Path(os.environ["EVOLUTION_DEPLOYMENT_STATE"]).absolute()
        self.profile = "classifier"
        self.readiness_observation = None
        bootstrap = load_module("evaluator_cluster_bootstrap", ROOT / "deploy/evolution/bootstrap.py")
        self.deployment = bootstrap.LocalDeployment(ROOT, state=self.state)
        self.access = self._access()
        self.credentials = self._credentials()
        self.inventory = self.credentials["inventory"]
        self.tenants = tuple(sorted(self.inventory["tenants"]))
        assert self.tenants == ("team-a", "team-b")
        assert set(self.access["tenants"]) == set(self.tenants)
        for tenant in self.tenants:
            assert self.access["tenants"][tenant]["workload_token_file"] == self.inventory["tenants"][tenant]["roles"]["workload"]["token_file"]
        self.http = httpx.Client(verify=ssl.create_default_context(cafile=self.access["ca_file"]),
            trust_env=False, follow_redirects=False, timeout=15)
        try:
            self.assert_ready()
            self.repo = TenantRepository(private_read(self.access["database_url_file"], maximum=4096).decode().strip())
            self.endpoints = {}
            if self.profile == "classifier":
                path = Path(os.environ["EVOLUTION_EVALUATOR_CONFIG_FILE"])
                bundle, _, _, _ = self.deployment.support("assembly")._classifier_bundle(
                    self.deployment, self.state / "assembly", self.deployment.owner(), path)
                self.endpoints = {row["tenant_id"]: row for row in models.strict_json_loads(bundle)["classifier"]}
        except BaseException as primary:
            try:
                self.close()
            except BaseException:  # noqa: BLE001 - preserve the original constructor refusal after both cleanup attempts
                primary.add_note("Evaluator cluster cleanup also failed")
            raise

    def _access(self):
        """Observe exact existing state and forwards; never repair deployment access."""
        observed = self.deployment.access(read_only=True, deadline=self._access_deadline)
        assert observed["status"] == "available"
        assert Path(observed["access_file"]) == self.state / "assembly/access.json"
        value = observed["snapshot"]
        assert value["schema_version"] == "guardrails-evolution-local-access-v1"
        if getattr(self, "requires_classifier", True):
            assert value["profile"] == "classifier"
        else:
            assert value["profile"] in {"deterministic_mock", "classifier"}
        origins = [value[name] for name in ("management_url", "runtime_url", "mock_url", "ollama_url")]
        origins.extend(row["gateway_url"] for row in value["tenants"].values())
        for origin in origins:
            parsed = urlsplit(origin)
            assert parsed.scheme == "https" and parsed.hostname == "localhost"
            assert parsed.username is None and parsed.password is None
            assert parsed.port is not None and 0 < parsed.port < 65536
            assert not parsed.path and not parsed.query and not parsed.fragment
        assert time.monotonic() < self._access_deadline
        self.profile = value["profile"]
        return value

    def operator_providers(self, tenant):
        """Only the classifier profile has an external classifier credential ref."""
        if self.profile == "classifier":
            return (self.endpoints[tenant]["provider"],)
        assert self.profile == "deterministic_mock" and self.requires_classifier is False and not self.endpoints
        return ()

    def gateway_routes(self):
        """Allocate the handle before staging so finally owns partial mutations."""
        module = self.deployment.support("gateway_routes")
        return module.GatewayRouteStage(self.deployment,
            journal_root=Path(os.environ["EVOLUTION_GATEWAY_ROUTE_OPERATION_ROOT"]),
            deadline=time.monotonic() + module.STAGE_SECONDS)

    def register_route_governance(self, attempt):
        from evolution.gateway_route_case import register_governance_attempt

        register_governance_attempt(attempt)

    def verify_original_route_binding(self, intent, *, journey, restored_release):
        expected = intent["original_binding"]
        binding, policy = self.binding(intent["tenant"])
        keys = ("tenant_id", "workload_id", "policy", "state")
        assert all(binding[key] == expected[key] for key in keys), "route_binding_recovery_required"
        assert policy["compiled_digest"] == expected["policy"]["digest"]
        if restored_release is None:
            assert models.Binding.model_validate_json(models.canonical_json_bytes(binding)).model_dump(
                mode="json", exclude={"reason"}) == expected, "route_binding_recovery_required"
            verification = "unchanged_original"
        else:
            handle = journey.last_release
            assert handle["operation"] == intent["operation"] and handle["label"] == "restore-original-binding"
            assert handle["actor_id"] == intent["operator_id"]
            assert owned_revision(handle, {"binding_revision": binding["revision"]}, binding) == binding["revision"]
            release = restored_release["release"]
            assert release["release_id"] == binding["release_id"] and release["status"] == "applied"
            assert restored_release["binding_revision"] == binding["revision"]
            receipts = [models.RuntimeReceipt.model_validate_json(models.canonical_json_bytes(row))
                        for row in restored_release["receipts"]]
            assert len(receipts) == 2 and {row.runtime_id for row in receipts} == set(intent["runtime_ids"])
            assert all(row.tenant_id == binding["tenant_id"] and row.workload_id == binding["workload_id"]
                       and row.binding_revision == binding["revision"] and row.digest == binding["policy"]["digest"]
                       and row.health == "ready" for row in receipts)
            verification = "acknowledged_restoration"
        return {"verified_original_binding": True, "verification": verification,
                "release_id": binding["release_id"], **{key: binding[key] for key in (*keys, "revision")}}

    def reset_gateway_connections(self):
        """Discard connections to the replaced Pod; perform no readiness/model request."""
        self.http.close()
        self.http = httpx.Client(verify=ssl.create_default_context(cafile=self.access["ca_file"]),
            trust_env=False, follow_redirects=False, timeout=15)

    def close(self):
        """Attempt both owned resources once; cleanup never logs private errors."""
        failed = False
        for name in ("http", "repo"):
            resource = getattr(self, name, None)
            if resource is not None:
                setattr(self, name, None)
                try:
                    resource.close()
                except BaseException:  # noqa: BLE001 - close the other resource even during cancellation
                    failed = True
        if failed:
            raise RuntimeError("evaluator_cluster_cleanup_failed") from None

    def journey(self, tenant, operation, *, journal=None):
        roles = self.inventory["tenants"][tenant]["roles"]
        last = {}
        def call(method, suffix, *, role, body, key, expected):
            # Stay below the published per-principal management limit; do not
            # retry a failed mutation or turn 429 into a successful observation.
            wait = 1.05 - (time.monotonic() - last.get(role, 0))
            if wait > 0:
                time.sleep(wait)
            last[role] = time.monotonic()
            headers = self.headers(roles[role]["token_file"])
            if key is not None:
                headers["Idempotency-Key"] = key
            response = self.http.request(method, self.access["management_url"] +
                "/v2/tenants/" + tenant + suffix, headers=headers, json=body)
            assert response.status_code == expected, "scoped governance response status differs"
            assert len(response.content) <= 2 * 1024 * 1024
            return models.strict_json_loads(response.content)
        return GovernanceJourney(tenant=tenant, operation=operation,
            actors={role: roles[role]["principal_id"] for role in
                    ("tenant_admin", "author", "approver", "operator", "auditor")}, call=call, journal=journal,
            operator_providers=self.operator_providers(tenant))

    def binding(self, tenant):
        workload = self.inventory["tenants"][tenant]["workload"]["workload_id"]
        with self.repo.transaction(tenant) as tx:
            desired = tx.get("binding_pointers", workload, required=True)
            applied = tx.get("applied_bindings", workload, required=True)
            assert desired["binding_revision"] == applied["binding_revision"]
            binding = tx.get("bindings", workload + ":" + str(desired["binding_revision"]), required=True)
            policy = tx.get("policies", binding["policy"]["policy_id"] + ":" + str(binding["policy"]["version"]), required=True)
            status = tx.get("policy_status", binding["policy"]["policy_id"] + ":" + str(binding["policy"]["version"]), required=True)
        assert applied["policy"] == binding["policy"]
        return binding, checked_policy({**policy, "status": status["status"]}, tenant, status="approved",
            operator_providers=self.operator_providers(tenant))

    def owned_binding_revision(self, tenant, handle):
        """Read the acknowledged release identity before any restoration CAS."""
        assert handle["tenant_id"] == tenant
        workload = self.inventory["tenants"][tenant]["workload"]["workload_id"]
        assert handle["workload_id"] == workload
        with self.repo.transaction(tenant) as tx:
            pointer = tx.get("binding_pointers", workload, required=True)
            assert pointer["binding_revision"] == handle["binding_revision"], "owned release pointer changed"
            binding = tx.get("bindings", workload + ":" + str(pointer["binding_revision"]), required=True)
        return owned_revision(handle, pointer, binding)

    def baseline(self, tenant):
        with self.repo.transaction(tenant) as tx:
            baseline = tx.get("baseline", "current", required=True)
        assert len(baseline["policies"]) == 1
        reference = baseline["policies"][0]
        value = self.management(tenant, "GET", "/policies/" + reference["policy_id"] +
            "/versions/" + str(reference["version"]))["policy"]
        checked = checked_policy(value, tenant, status="approved",
            operator_providers=self.operator_providers(tenant))
        assert checked["compiled_digest"] == reference["digest"]
        return baseline, checked

    def evaluate(self, tenant, part, *, stage, context_id=None, correlation=None):
        item = self.inventory["tenants"][tenant]
        request = {"schema_version": "guardrails-v2", "request_id": str(uuid4()),
            "correlation_id": correlation or str(uuid4()), "tenant_id": tenant,
            "workload_id": item["workload"]["workload_id"], "stage": stage,
            "parts": [part], "provider_id": item["profiles"]["mock"]["provider"]["provider_id"],
            "context_id": context_id, "deadline_ms": 3000}
        response = self.http.post(self.access["runtime_url"] + "/v2/evaluations",
            headers=self.headers(item["roles"]["workload"]["token_file"]), json=request, timeout=4)
        assert response.status_code == 200, "actual runtime evaluation did not return a decision"
        assert len(response.content) <= 2 * 1024 * 1024
        decision = models.Decision.model_validate_json(response.content)
        assert decision.request_id == request["request_id"] and decision.tenant_id == tenant
        assert decision.workload_id == request["workload_id"] and decision.stage == stage
        assert decision.audit_status == "persisted"
        return decision, request

    def assert_governance_audit(self, tenant, policy, *, applied):
        roles = self.inventory["tenants"][tenant]["roles"]
        with self.repo.transaction(tenant) as tx:
            rows = list(tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant "
                "AND kind='audit' AND data->>'policy_digest'=:digest LIMIT 1001",
                {"tenant": tenant, "digest": policy["compiled_digest"]}))
        assert len(rows) <= 1000
        events = [row.data for row in rows]
        for event_type, role in (("policy_submitted", "author"), ("policy_approved", "approver")):
            selected = [row for row in events if row["event_type"] == event_type]
            assert len(selected) == 1 and selected[0]["actor_id"] == roles[role]["principal_id"]
            assert selected[0]["tenant_id"] == tenant and selected[0]["raw_content_retained"] is False
        pending = [row for row in events if row["event_type"] == "release_pending"]
        completed = [row for row in events if row["event_type"] == "release_applied"]
        if applied:
            assert len(pending) == len(completed) == 1
            assert pending[0]["actor_id"] == roles["operator"]["principal_id"]
            assert completed[0]["actor_id"] in {roles[name]["principal_id"] for name in ("runtime-1", "runtime-2")}
        else:
            assert not pending and not completed

    def assert_exact_audit(self, tenant, decision, request):
        with self.repo.transaction(tenant) as tx:
            event = tx.get("audit", decision.decision_id, required=True)
        assert event["event_type"] == "evaluation" and event["tenant_id"] == tenant
        assert event["workload_id"] == request["workload_id"]
        assert event["actor_id"] == self.inventory["tenants"][tenant]["roles"]["workload"]["principal_id"]
        assert event["correlation_id"] == request["correlation_id"]
        assert event["policy_digest"] == decision.policy.digest
        assert event["binding_revision"] == decision.binding_revision
        assert event["action"] == decision.action and event["metadata"]["stage"] == request["stage"]
        assert event["raw_content_retained"] is False
        other = next(value for value in self.tenants if value != tenant)
        with self.repo.transaction(other) as tx:
            assert tx.get("audit", decision.decision_id) is None
        return event

    def assert_private_metadata(self, marker):
        needle = marker.encode()
        for tenant in self.tenants:
            with self.repo.transaction(tenant) as tx:
                rows = list(tx.execute("SELECT data FROM evolution.records WHERE tenant_id=:tenant LIMIT 100001", {"tenant": tenant}))
            assert len(rows) <= 100000
            leaked = any(needle in models.canonical_json_bytes(row.data) for row in rows)
            assert not leaked, "evaluation input retained in database metadata"
