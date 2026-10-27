"""Active capabilities preserve exact stage/revision pairs and list constraints."""

from contextlib import contextmanager
from types import SimpleNamespace

from guardrails.evolution.models import Capability, ListSet, with_resource_digest
from guardrails.evolution.runtime_api import EvaluationRuntime
from test_compiler import policy, resource, rule, uid
from test_execution_profiles import injection


def selected(candidate, *, stages=("input", "output")):
    authorized = []
    identity = SimpleNamespace(tenant_id="team-a", workload_id="chat")

    class Tx:
        def lock(self, name):
            authorized.append(name)

        def get(self, kind, key, *, required):
            assert (kind, key, required) == ("workloads", "chat", True)
            return {"allowed_stages": stages}

    @contextmanager
    def transaction(tenant):
        assert tenant == "team-a"
        yield Tx()

    catalog = tuple(
        Capability(capability_id=revision, stage=stage, part_kinds=("text",),
                   actions=("allow", "block"), evaluator_revision=revision,
                   max_input_bytes=65536, timeout_ms=400, zone="local-shared", status="enabled")
        for stage in ("input", "output", "retrieval")
        for revision in ("pii-v2", "deberta-v2", "lexical-v1")
    )
    runtime = EvaluationRuntime.__new__(EvaluationRuntime)
    runtime.identity = SimpleNamespace(authorize=lambda *args: authorized.append("identity"))
    runtime.repository = SimpleNamespace(transaction=transaction)
    runtime._active_credential = lambda *args: authorized.append("credential")
    runtime._runtime_active = lambda *args: authorized.append("runtime")
    runtime.governance = SimpleNamespace(eligible=lambda *args: authorized.append("eligible"))
    runtime.slots = {"team-a": SimpleNamespace(store=SimpleNamespace(current=lambda workload: (None, None, candidate)))}
    runtime.registry = SimpleNamespace(capabilities=lambda tenant: catalog)
    result = runtime.capabilities_for(identity)
    assert authorized == ["identity", "governance-admission", "credential", "runtime", "eligible"]
    return {(value.stage, value.evaluator_revision) for value in result}


def test_revision_on_one_stage_is_not_advertised_for_another_stage():
    candidate = policy(rules=[injection(stages=["input"]), rule(stages=["output"])])
    assert selected(candidate) == {("input", "deberta-v2"), ("output", "pii-v2")}


def test_zero_rules_content_constraint_requires_lexical_capability_only_on_supported_workload_stages():
    lists = resource(ListSet, tenant_id="team-a", list_id=uid(889), kind="content", allow_enabled=True)
    candidate = policy(rules=[], resolved_lists=(lists,))
    candidate = candidate.model_copy(update={"residency": with_resource_digest(
        candidate.residency.model_copy(update={"content_lists": (lists.list_id,)}))})
    assert selected(candidate, stages=("output", "retrieval")) == {("output", "lexical-v1")}


def test_unreferenced_or_inactive_content_lists_do_not_advertise_a_detector():
    lists = resource(ListSet, tenant_id="team-a", list_id=uid(890), kind="content", deny=("term:denied",))
    assert selected(policy(rules=[], resolved_lists=(lists,))) == set()
    lists = with_resource_digest(lists.model_copy(update={"deny": ()}))
    candidate = policy(rules=[], resolved_lists=(lists,))
    candidate = candidate.model_copy(update={"residency": with_resource_digest(
        candidate.residency.model_copy(update={"content_lists": (lists.list_id,)}))})
    assert selected(candidate) == set()
