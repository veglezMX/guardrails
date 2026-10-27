"""Governance commands use real models/compiler and a closed fake HTTP boundary."""
import copy
from uuid import UUID, uuid4

import pytest
from evolution.evaluator_governance import GovernanceJourney, payload_from_approved
from guardrails.evolution import models
from guardrails.evolution.classifier_contract import MODEL_ID, WEIGHTS_SHA256
from guardrails.evolution.compiler import PolicyCompiler
from guardrails.evolution.errors import EvolutionError

STAMP = "2026-10-12T00:00:00Z"


def uid(number):
    return str(UUID(int=number))


def resource(cls, **data):
    return models.with_resource_digest(cls(tenant_id="team-a", created_at=STAMP, **data))


@pytest.fixture
def base():
    destinations, providers = [], []
    for index, name in enumerate(("mock", "ollama"), 1):
        destination = resource(models.Destination, destination_id=uid(index * 2),
            name=name, host=name + ".gr-model.svc.cluster.local", port=8443,
            path_prefix="/", cidrs=("10.96.0." + str(index) + "/32",), zone="local-shared",
            purpose="inference", tls_ca_ref="local-ca")
        provider = resource(models.ProviderProfile, provider_id=uid(index * 2 - 1),
            kind="model", vendor="ollama", model_id=name, destination_id=destination.destination_id,
            zone="local-shared")
        destinations.append(destination)
        providers.append(provider)
    lists = (resource(models.ListSet, list_id=uid(5), kind="provider", allow_enabled=True,
                      allow=tuple(value.provider_id for value in providers)),
             resource(models.ListSet, list_id=uid(6), kind="destination", allow_enabled=True,
                      allow=tuple(value.destination_id for value in destinations)))
    residency = resource(models.ResidencyProfile, residency_id=uid(7),
        provider_lists=(lists[0].list_id,), destination_lists=(lists[1].list_id,))
    quota = resource(models.Quota, quota_id=uid(8))
    compiled = PolicyCompiler().compile(models.PolicyVersion(tenant_id="team-a", policy_id="baseline",
        version=1, author_id=uid(11), created_at=STAMP, residency=residency, quota=quota,
        rules=(models.Rule(rule_id="pii", kind="pii", stages=("input", "output"), action="block",
            mandatory=True, minimum_state="observe", config={"entities": ["email"]}, evaluator_revision="pii-v2"),),
        resolved_lists=lists, providers=tuple(providers), resolved_destinations=tuple(destinations)))
    return compiled.model_copy(update={"status": "approved"}).model_dump(mode="json")


class Port:
    def __init__(self, base):
        self.calls, self.base, self.version = [], base, 2
        self.changed_v1 = False
        self.fail_provider = False

    def __call__(self, method, path, **options):
        self.calls.append((method, path, copy.deepcopy(options)))
        family, identity = path.strip("/").split("/")[:2]
        field, cls, id_field = (("providers", models.ProviderProfile, "provider_id") if family == "providers"
                               else ("resolved_destinations", models.Destination, "destination_id"))
        old = next(row for row in self.base[field] if row[id_field] == identity)
        if method == "GET":
            result = copy.deepcopy(old)
            if self.changed_v1:
                result["version"] = 17
            return result
        assert options["role"] == "tenant_admin" and options["expected"] == 201
        if family == "providers" and self.fail_provider:
            raise EvolutionError("revision_conflict")
        return resource(cls, **{id_field: identity}, version=self.version,
                        **options["body"]).model_dump(mode="json")


def journey(port, **options):
    return GovernanceJourney(tenant="team-a", operation=options.pop("operation", uid(99)),
        actors={name: uid(index) for index, name in enumerate(
            ("tenant_admin", "author", "approver", "operator", "auditor"), 10)}, call=port, **options)


def versions(j, base, **options):
    return j.classifier_versions(previous_provider=base["providers"][1],
        previous_destination=base["resolved_destinations"][1], address="10.96.0.35",
        model_id=MODEL_ID, artifact_digest=WEIGHTS_SHA256,
        credential_ref="classifier-shared-operator", **options)


def extra_rule():
    return models.Rule(rule_id="extra-card", kind="pii", stages=("input",), action="block",
        config={"entities": ["payment_card"]}, evaluator_revision="pii-v2").model_dump(mode="json")


def test_existing_allowed_ids_get_new_exact_versions_without_baseline_or_v1_mutation(base):
    before = models.canonical_json_bytes(base)
    port = Port(base)
    result = versions(journey(port), base)
    assert result["status"] == "resource_candidates_not_approved"
    assert result["provider"]["provider_id"] == base["providers"][1]["provider_id"]
    assert result["destination"]["destination_id"] == base["resolved_destinations"][1]["destination_id"]
    assert result["provider"]["version"] == result["destination"]["version"] == 2
    assert [call[0] for call in port.calls] == ["GET", "GET", "POST", "POST"]
    assert models.canonical_json_bytes(base) == before
    assert not any("baseline" in call[1] or "release" in call[1] for call in port.calls)


def test_actual_compiler_accepts_exact_versioned_route_under_unchanged_id_floors(base):
    version = versions(journey(Port(base)), base)
    payload = payload_from_approved(base, extra_rule(), replacements=(
        ("providers", version["provider"]), ("resolved_destinations", version["destination"])))
    assert payload["parents"] == []
    assert payload["list_refs"] == [{"id": row["list_id"], "version": row["version"], "digest": row["digest"]}
                                   for row in base["resolved_lists"]]
    candidate = models.PolicyVersion.model_validate_json(models.canonical_json_bytes({**base,
        "policy_id": "versioned-classifier", "status": "candidate", "rules": payload["rules"],
        "providers": [base["providers"][0], version["provider"]],
        "resolved_destinations": [base["resolved_destinations"][0], version["destination"]],
        "compiled_digest": "0" * 64}))
    compiler = PolicyCompiler()
    compiled = compiler.compile(candidate)
    compiler.validate_current(compiled, models.PolicyVersion.model_validate_json(models.canonical_json_bytes(base)))
    assert compiled.rules[1].mandatory or compiled.rules[0].mandatory
    assert compiled.residency.model_dump(mode="json") == base["residency"]
    assert compiled.quota.model_dump(mode="json") == base["quota"]


def test_interrupted_versioning_has_stable_idempotency_but_never_retries_itself(base):
    port = Port(base)
    port.fail_provider = True
    with pytest.raises(EvolutionError):
        versions(journey(port), base)
    first = list(port.calls)
    assert len(first) == 4
    port.fail_provider = False
    versions(journey(port), base)
    assert [entry[2]["key"] for entry in port.calls[4:]] == [entry[2]["key"] for entry in first]
    assert [entry[2]["body"] for entry in port.calls[4:]] == [entry[2]["body"] for entry in first]
    other = Port(base)
    versions(journey(other, operation=str(uuid4())), base)
    assert other.calls[-1][2]["key"] != port.calls[-1][2]["key"]


def test_changed_original_version_refuses_before_any_post(base):
    port = Port(base)
    port.changed_v1 = True
    with pytest.raises(EvolutionError) as caught:
        versions(journey(port), base)
    assert caught.value.code == "revision_conflict"
    assert all(method == "GET" for method, _, _ in port.calls)


def test_unexpected_next_version_is_not_silently_adopted(base):
    port = Port(base)
    port.version = 3
    with pytest.raises(EvolutionError):
        versions(journey(port), base)
    assert len(port.calls) == 3


@pytest.mark.parametrize("fault", ["digest", "foreign", "old-version", "duplicate", "new-id"])
def test_payload_refuses_unauthenticated_or_ambiguous_resource_substitution(base, fault):
    version = versions(journey(Port(base)), base)["provider"]
    if fault == "digest":
        version["digest"] = "0" * 64
    elif fault == "foreign":
        value = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(version))
        version = models.with_resource_digest(value.model_copy(update={"tenant_id": "team-b"})).model_dump(mode="json")
    elif fault == "old-version":
        version = base["providers"][1]
    elif fault == "new-id":
        value = models.ProviderProfile.model_validate_json(models.canonical_json_bytes(version))
        version = models.with_resource_digest(value.model_copy(update={"provider_id": uid(333)})).model_dump(mode="json")
    replacements = [("providers", version)] * (2 if fault == "duplicate" else 1)
    with pytest.raises(EvolutionError):
        payload_from_approved(base, extra_rule(), replacements=replacements)


def test_mandatory_baseline_rule_cannot_be_replaced_via_payload_builder(base):
    before = models.canonical_json_bytes(base)
    rule = {**extra_rule(), "rule_id": "pii"}
    with pytest.raises(EvolutionError):
        payload_from_approved(base, rule)
    assert models.canonical_json_bytes(base) == before


def test_same_principal_roles_rejected_before_any_management_request(base):
    port = Port(base)
    with pytest.raises(EvolutionError) as caught:
        GovernanceJourney(tenant="team-a", operation=uid(99), call=port,
            actors={name: uid(1) for name in ("tenant_admin", "author", "approver", "operator", "auditor")})
    assert caught.value.code == "self_approval" and port.calls == []


def test_binding_cas_refuses_stale_pointer_before_release(base):
    calls = []
    def port(method, path, **options):
        calls.append((method, path))
        return {"tenant_id": "team-a", "workload_id": "chat", "desired_binding": {"binding_revision": 7}}
    with pytest.raises(EvolutionError) as caught:
        journey(port).release(policy=base, workload_id="chat", state="observe", expected_revision=6,
            runtime_ids=(uid(51), uid(52)), label="candidate")
    assert caught.value.code == "revision_conflict" and calls == [("GET", "/workloads/chat")]


class PolicyPort:
    def __init__(self, base, *, fault=None):
        self.base, self.fault, self.calls = base, fault, []

    def __call__(self, method, path, **options):
        self.calls.append((method, path, options))
        if method == "POST" and path.endswith("/versions"):
            data = {**copy.deepcopy(self.base), "policy_id": path.split("/")[2],
                    "rules": options["body"]["rules"], "status": "candidate"}
            if self.fault == "substituted-rule":
                data["rules"] = data["rules"][:1]
            if self.fault == "substituted-resource":
                quota = models.Quota.model_validate_json(models.canonical_json_bytes(data["quota"]))
                data["quota"] = models.with_resource_digest(quota.model_copy(update={"burst": 3})).model_dump(mode="json")
            self.candidate = PolicyCompiler().compile(models.PolicyVersion.model_validate_json(
                models.canonical_json_bytes(data))).model_dump(mode="json")
            return self.candidate
        if method == "POST":
            assert options["role"] == "approver" and options["body"]["compiled_digest"] == self.candidate["compiled_digest"]
            reference = {"tenant_id": "team-a", "policy_id": self.candidate["policy_id"],
                         "version": 1, "digest": self.candidate["compiled_digest"]}
            self.review = models.Approval(approval_id=uid(300), tenant_id="team-a", policy=reference,
                compiled_digest=reference["digest"], actor_id=uid(12 if self.fault != "wrong-reviewer" else 17),
                outcome="approve", reason="Reviewed", created_at=STAMP).model_dump(mode="json")
            return self.review
        return {"policy": {**self.candidate, "status": "approved"},
                "approvals": [] if self.fault == "missing-review" else [self.review]}


def test_author_then_independent_reviewer_approve_exact_requested_materialization(base):
    port = PolicyPort(base)
    approved = journey(port).approve(payload_from_approved(base, extra_rule()))
    assert approved["status"] == "approved"
    assert [row[2]["role"] for row in port.calls] == ["author", "approver", "operator"]
    assert port.calls[0][2]["key"] != port.calls[1][2]["key"]
    assert approved["rules"] == port.candidate["rules"]


@pytest.mark.parametrize("fault,call_count", [("substituted-rule", 1), ("substituted-resource", 1),
                                              ("wrong-reviewer", 2), ("missing-review", 3)])
def test_validly_digested_but_unrequested_policy_or_review_is_not_approved(base, fault, call_count):
    port = PolicyPort(base, fault=fault)
    with pytest.raises(EvolutionError):
        journey(port).approve(payload_from_approved(base, extra_rule()))
    assert len(port.calls) == call_count


class ReleasePort:
    def __init__(self, base, *, fault=None):
        self.calls, self.fault, self.base, self.ticks = [], fault, base, 0.0
        self.reference = {"tenant_id": "team-a", "policy_id": base["policy_id"], "version": 1,
                          "digest": base["compiled_digest"]}
        self.release = models.Release(release_id=uid(500), tenant_id="team-a", policy=self.reference,
            workload_ids=("chat",), stage="canary", state="observe", expected_revisions={"chat": 7},
            actor_id=uid(13), reason="Test", created_at=STAMP).model_dump(mode="json")

    def sleep(self, seconds):
        self.ticks += seconds

    def __call__(self, method, path, **options):
        self.calls.append((method, path, options))
        if path == "/workloads/chat":
            done = len(self.calls) > 1
            return {"tenant_id": "team-a", "workload_id": "chat",
                    "desired_binding": {"binding_revision": 8 if done else 7},
                    "applied_binding": {"binding_revision": 8 if done else 7,
                        "policy": self.reference if self.fault != "wrong-pointer" else {**self.reference, "version": 2}}}
        if method == "POST":
            assert options["body"]["expected_revisions"] == {"chat": 7}
            return self.release
        receipts = [models.RuntimeReceipt(runtime_id=uid(index), tenant_id="team-a", workload_id="chat",
            binding_revision=8, snapshot_generation=3, digest=self.reference["digest"],
            applied_at=STAMP, created_at=STAMP, health="ready").model_dump(mode="json") for index in (51, 52)]
        if self.fault == "one-receipt":
            receipts = receipts[:1]
        elif self.fault == "foreign-runtime":
            receipts[0]["runtime_id"] = uid(777)
        elif self.fault == "wrong-digest":
            receipts[0]["digest"] = "0" * 64
        elif self.fault == "newer-failed":
            receipts += [{**receipts[0], "snapshot_generation": 4, "health": "failed"}]
        elif self.fault == "old-revision":
            receipts[0]["binding_revision"] = 7
        release = {**self.release, "status": "failed" if self.fault == "failed-release" else "applied"}
        return {"release": release, "receipts": receipts}


def release_with(port):
    return journey(port, clock=lambda: port.ticks, sleep=port.sleep).release(policy=port.base,
        workload_id="chat", state="observe", expected_revision=7, runtime_ids=(uid(51), uid(52)),
        label="candidate", seconds=2)


def test_release_requires_two_actual_scoped_receipts_and_exact_applied_pointer(base):
    port = ReleasePort(base)
    result = release_with(port)
    assert result["binding_revision"] == 8 and len(result["receipts"]) == 2
    assert [row[0] for row in port.calls] == ["GET", "POST", "GET", "GET"]


@pytest.mark.parametrize("fault", ["one-receipt", "foreign-runtime", "wrong-digest", "newer-failed",
                                   "old-revision", "failed-release", "wrong-pointer"])
def test_release_never_synthesizes_missing_or_invalid_runtime_application(base, fault):
    port = ReleasePort(base, fault=fault)
    with pytest.raises(EvolutionError):
        release_with(port)
    assert sum(row[0] == "POST" for row in port.calls) == 1
    assert port.ticks <= 2


@pytest.mark.parametrize("engine", ("pii", "secret", "term", "topic", "json_schema"))
@pytest.mark.parametrize("stage", ("input", "output"))
def test_matrix_payloads_reach_actual_deterministic_detector_and_clean_control(base, engine, stage):
    import time

    from evolution.evaluator_cases import content, control
    from guardrails.evolution.rules import (
        RulesBinding,
        evaluate_rule,
        transform_rule_findings,
    )
    selected = resource(models.ListSet, list_id=uid(61), kind="content", allow_enabled=False,
                        deny=("topic:finance" if engine == "topic" else "term:forbidden-fixture",))
    rule = models.Rule.model_validate_json(models.canonical_json_bytes(control(engine, stage, list_id=selected.list_id)))
    policy = models.PolicyVersion.model_validate_json(models.canonical_json_bytes({**base,
        "policy_id": "matrix-actual", "status": "candidate", "rules": [rule.model_dump(mode="json")],
        "resolved_lists": [*base["resolved_lists"], *([selected.model_dump(mode="json")] if engine in {"term", "topic"} else [])]}))
    policy = PolicyCompiler().compile(policy)
    binding = RulesBinding(policy, stage)
    def parts(clean):
        return (models.ContentPart.model_validate_json(models.canonical_json_bytes(content(engine, stage, "unit-canary", clean=clean))),)
    found = evaluate_rule(binding, rule, parts(False), time.monotonic() + 0.5)
    assert found
    if engine in {"pii", "secret", "term"}:
        transformed = transform_rule_findings(policy, stage, parts(False), found, time.monotonic() + 0.5)
        category = {"pii": "payment_card", "secret": "aws_access_key", "term": "term"}[engine]
        assert "[REDACTED:" + category + "]" in transformed[0].text
        assert "unit-canary" in transformed[0].text
    assert evaluate_rule(binding, rule, parts(True), time.monotonic() + 0.5) == ()


def test_injection_matrix_revision_is_actual_immutable_contract():
    from evolution.evaluator_cases import control
    from guardrails.evolution.classifier_contract import EVALUATOR_REVISION
    assert control("injection", "input")["evaluator_revision"] == EVALUATOR_REVISION


def test_acknowledged_release_handle_survives_poll_failure_for_owned_reconciliation(base):
    port = ReleasePort(base)
    def broken_poll(method, path, **options):
        if method == "GET" and path.startswith("/releases/"):
            raise EvolutionError("snapshot_stale")
        return port(method, path, **options)
    j = journey(broken_poll, clock=lambda: port.ticks, sleep=port.sleep)
    with pytest.raises(EvolutionError):
        j.release(policy=base, workload_id="chat", state="observe", expected_revision=7,
                  runtime_ids=(uid(51), uid(52)), label="matrix-release")
    assert j.last_release == {"operation": uid(99), "label": "matrix-release", "release_id": uid(500),
        "tenant_id": "team-a", "workload_id": "chat", "policy": port.reference,
        "binding_revision": 8, "previous_revision": 7, "state": "observe", "actor_id": uid(13)}
    assert sum(call[0] == "POST" for call in port.calls) == 1


@pytest.mark.parametrize("fault", (None, "pointer", "release_id", "policy", "activated_by", "previous_revision", "state", "workload_id", "tenant_id"))
def test_cleanup_adopts_only_exact_acknowledged_binding_without_foreign_drift(base, fault):
    from evolution.evaluator_governance import owned_revision
    port = ReleasePort(base)
    j = journey(port, clock=lambda: port.ticks, sleep=port.sleep)
    j.release(policy=base, workload_id="chat", state="observe", expected_revision=7,
              runtime_ids=(uid(51), uid(52)), label="matrix-release")
    pointer = {"binding_revision": 8}
    binding = {"tenant_id": "team-a", "workload_id": "chat", "revision": 8,
        "release_id": uid(500), "policy": port.reference, "state": "observe",
        "previous_revision": 7, "activated_by": uid(13)}
    if fault == "pointer":
        pointer["binding_revision"] = 9
    elif fault in {"release_id", "activated_by"}:
        binding[fault] = uid(999)
    elif fault == "policy":
        binding["policy"] = {**port.reference, "version": 2}
    elif fault == "previous_revision":
        binding["previous_revision"] = 6
    elif fault in {"state", "workload_id", "tenant_id"}:
        binding[fault] = {"state": "block", "workload_id": "foreign", "tenant_id": "team-b"}[fault]
    if fault is None:
        assert owned_revision(j.last_release, pointer, binding) == 8
    else:
        with pytest.raises(EvolutionError) as caught:
            owned_revision(j.last_release, pointer, binding)
        assert caught.value.code == "revision_conflict"
    assert sum(call[0] == "POST" for call in port.calls) == 1
